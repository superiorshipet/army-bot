import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from armybot.application.ports.deploy import DeploymentExecutor, RepoAnalyzer
from armybot.application.ports.repositories import (
    DeploymentRepository,
    ProjectRepository,
    UserRepository,
)
from armybot.application.use_cases.credentials import CredentialService
from armybot.domain.entities import Deployment, Project, User, utcnow
from armybot.domain.enums import DeploymentStatus, DeploymentTarget
from armybot.infrastructure.deploy.coordinator import DeploymentCoordinator
from armybot.shared.redaction import redact_lines, redact_text

LogCallback = Callable[[str], Coroutine[Any, Any, None]] | None


class DeployProjectService:
    def __init__(
        self,
        analyzer: RepoAnalyzer,
        executor: DeploymentExecutor,
        projects: ProjectRepository,
        deployments: DeploymentRepository,
        credentials: CredentialService,
        coordinator: DeploymentCoordinator,
        platform_executors: dict[DeploymentTarget, DeploymentExecutor] | None = None,
        users: UserRepository | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.executor = executor
        self.projects = projects
        self.deployments = deployments
        self.credentials = credentials
        self.coordinator = coordinator
        self.platform_executors = platform_executors or {}
        self.users = users

    async def deploy(
        self,
        user: User,
        repo_url: str,
        branch: str = "main",
        on_log: LogCallback = None,
        target: DeploymentTarget = DeploymentTarget.Server,
        triggered_by: str = "manual",
    ) -> Deployment:
        plan = await self.analyzer.analyze(repo_url, branch)
        project = await self.projects.get_by_repo(user.id, repo_url, branch)
        if not project:
            project = Project(
                id=uuid4(),
                user_id=user.id,
                name=plan.project_name,
                repo_url=repo_url,
                branch=branch,
                stack=plan.stack,
                deployment_target=target,
            )
            await self.projects.add(project)
        elif project.deployment_target != target:
            project.deployment_target = target
            project.live_url = None
            project.target_config = {}
            await self.projects.update(project)

        plan = replace(
            plan,
            target_name=f"{plan.project_name}-{project.id.hex[:8]}",
            deployment_target=target,
            target_config=dict(project.target_config),
        )

        deployment = Deployment(
            id=uuid4(),
            project_id=project.id,
            user_id=user.id,
            status=DeploymentStatus.Queued,
            branch=branch,
            commit_sha=plan.commit_sha,
            logs=["Deployment started.", *plan.notes],
            metadata={
                "stack": plan.stack.value,
                "runtime": plan.runtime,
                "app_path": plan.app_path,
                "target_name": plan.deployment_name,
                "deployment_target": target.value,
                "triggered_by": triggered_by,
                "recipe_version": 2,
            },
        )
        await self.deployments.add(deployment)

        secrets = await self.credentials.load_all(user)
        target_credentials = secrets.get(target.value, {})
        target_key = ":".join(
            (
                target.value,
                str(target_credentials.get("host", target_credentials.get("project_id", ""))),
                str(target_credentials.get("base_path", "")),
                plan.deployment_name,
            )
        )
        executor = self.platform_executors.get(target, self.executor)

        async def execute():
            deployment.status = DeploymentStatus.Planning
            deployment.updated_at = utcnow()
            await self.deployments.update(deployment)
            deployment.status = DeploymentStatus.Running
            deployment.updated_at = utcnow()
            await self.deployments.update(deployment)
            return await executor.deploy(plan, secrets, on_log)

        try:
            output = await self.coordinator.run(target_key, user.id, execute())
            if len(output) == 3:
                live_url, logs, target_config = output
            else:
                live_url, logs = output
                target_config = project.target_config
            deployment.live_url = live_url
            deployment.logs.extend(redact_lines(logs, secrets))
            deployment.status = DeploymentStatus.Successful
            project.live_url = live_url
            project.stack = plan.stack
            project.deployment_target = target
            project.last_deployed_sha = plan.commit_sha
            project.last_triggered_sha = plan.commit_sha
            project.target_config = dict(target_config)
            await self.projects.update(project)
        except asyncio.CancelledError:
            deployment.status = DeploymentStatus.Cancelled
            deployment.logs.append("Deployment cancelled by the user.")
        except Exception as exc:  # noqa: BLE001
            deployment.status = DeploymentStatus.Failed
            deployment.logs.append(f"Deployment failed: {redact_text(str(exc), secrets)}")

        deployment.updated_at = utcnow()
        await self.deployments.update(deployment)
        return deployment

    async def set_auto_deploy(
        self,
        user: User,
        project_id: str,
        enabled: bool,
    ) -> Project:
        from uuid import UUID

        project = await self.projects.get_by_id(UUID(project_id))
        if not project:
            raise LookupError("Project not found.")
        if project.user_id != user.id and not user.is_super_admin:
            raise PermissionError("You do not have permission to update this project.")
        if enabled:
            hostname = (urlparse(project.repo_url).hostname or "").lower()
            if hostname not in {"github.com", "www.github.com"}:
                raise ValueError("Auto deploy currently supports GitHub repositories only.")
            secrets = await self.credentials.load_all(user)
            if not str(secrets.get("github", {}).get("token", "")).strip():
                raise ValueError("Add your GitHub token before enabling auto deploy.")
        project.auto_deploy_enabled = enabled
        if enabled and not project.last_triggered_sha:
            project.last_triggered_sha = project.last_deployed_sha
        await self.projects.update(project)
        return project

    async def deploy_existing(
        self,
        actor: User,
        project_id: str,
        on_log: LogCallback = None,
    ) -> Deployment:
        from uuid import UUID

        project = await self.projects.get_by_id(UUID(project_id))
        if not project:
            raise LookupError("Project not found.")
        if project.user_id != actor.id and not actor.is_super_admin:
            raise PermissionError("You do not have permission to deploy this project.")
        owner = actor
        if project.user_id != actor.id:
            if not self.users:
                raise RuntimeError("Project owner lookup is not configured.")
            owner = await self.users.get_by_id(project.user_id)
            if not owner:
                raise LookupError("Project owner not found.")
        return await self.deploy(
            owner,
            project.repo_url,
            project.branch,
            on_log=on_log,
            target=project.deployment_target,
            triggered_by="admin" if actor.id != owner.id else "manual",
        )

    async def cancel_current(self, user: User) -> bool:
        return await self.coordinator.cancel_for_user(user.id)

    async def delete_project(
        self,
        user: User,
        project_id_or_name: str,
        on_log: LogCallback = None,
    ) -> tuple[bool, str]:
        project: Project | None = None
        try:
            from uuid import UUID

            proj_uuid = UUID(project_id_or_name)
            project = await self.projects.get_by_id(proj_uuid)
        except (ValueError, TypeError):
            pass

        if not project:
            project = await self.projects.get_by_name(user.id, project_id_or_name)

        if not project:
            return False, f"Project '{project_id_or_name}' not found."

        if project.user_id != user.id and not user.is_super_admin:
            return False, "You do not have permission to delete this project."

        try:
            credential_owner = user
            if project.user_id != user.id and self.users:
                credential_owner = await self.users.get_by_id(project.user_id) or user
            secrets = await self.credentials.load_all(credential_owner)
            if project.deployment_target != DeploymentTarget.Server:
                if on_log:
                    await on_log("Removing the project from Army Deploy...")
            elif hasattr(self.executor, "cleanup_project"):
                await self.executor.cleanup_project(
                    f"{project.name}-{project.id.hex[:8]}", secrets, on_log
                )
            else:
                from armybot.infrastructure.deploy.remote_executor import RemoteRecipeDeployExecutor

                executor = RemoteRecipeDeployExecutor()
                await executor.cleanup_project(
                    f"{project.name}-{project.id.hex[:8]}", secrets, on_log
                )
        except Exception as exc:  # noqa: BLE001
            if on_log:
                await on_log(f"⚠️ Remote cleanup warning: {exc}")

        await self.projects.delete(project.id)
        return True, f"Project '{project.name}' deleted successfully."
