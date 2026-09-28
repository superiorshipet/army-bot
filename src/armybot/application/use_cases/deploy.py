import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Any
from uuid import uuid4

from armybot.application.ports.deploy import DeploymentExecutor, RepoAnalyzer
from armybot.application.ports.repositories import DeploymentRepository, ProjectRepository
from armybot.application.use_cases.credentials import CredentialService
from armybot.domain.entities import Deployment, Project, User, utcnow
from armybot.domain.enums import DeploymentStatus
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
    ) -> None:
        self.analyzer = analyzer
        self.executor = executor
        self.projects = projects
        self.deployments = deployments
        self.credentials = credentials
        self.coordinator = coordinator

    async def deploy(
        self,
        user: User,
        repo_url: str,
        branch: str = "main",
        on_log: LogCallback = None,
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
            )
            await self.projects.add(project)

        plan = replace(plan, target_name=f"{plan.project_name}-{project.id.hex[:8]}")

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
                "recipe_version": 2,
            },
        )
        await self.deployments.add(deployment)

        secrets = await self.credentials.load_all(user)
        server = secrets.get("server", {})
        target_key = ":".join(
            (
                str(server.get("host", "")),
                str(server.get("base_path", "")),
                plan.deployment_name,
            )
        )

        async def execute() -> tuple[str | None, list[str]]:
            deployment.status = DeploymentStatus.Planning
            deployment.updated_at = utcnow()
            await self.deployments.update(deployment)
            deployment.status = DeploymentStatus.Running
            deployment.updated_at = utcnow()
            await self.deployments.update(deployment)
            return await self.executor.deploy(plan, secrets, on_log)

        try:
            live_url, logs = await self.coordinator.run(target_key, user.id, execute())
            deployment.live_url = live_url
            deployment.logs.extend(redact_lines(logs, secrets))
            deployment.status = DeploymentStatus.Successful
            project.live_url = live_url
            project.stack = plan.stack
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
            secrets = await self.credentials.load_all(user)
            if hasattr(self.executor, "cleanup_project"):
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
