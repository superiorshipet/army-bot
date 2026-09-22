from uuid import uuid4

from armybot.application.ports.deploy import DeploymentExecutor, RepoAnalyzer
from armybot.application.ports.repositories import DeploymentRepository, ProjectRepository
from armybot.application.use_cases.credentials import CredentialService
from armybot.domain.entities import Deployment, Project, User
from armybot.domain.enums import DeploymentStatus


class DeployProjectService:
    def __init__(
        self,
        analyzer: RepoAnalyzer,
        executor: DeploymentExecutor,
        projects: ProjectRepository,
        deployments: DeploymentRepository,
        credentials: CredentialService,
    ) -> None:
        self.analyzer = analyzer
        self.executor = executor
        self.projects = projects
        self.deployments = deployments
        self.credentials = credentials

    async def deploy(self, user: User, repo_url: str, branch: str = "main") -> Deployment:
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

        deployment = Deployment(
            id=uuid4(),
            project_id=project.id,
            user_id=user.id,
            status=DeploymentStatus.Running,
            branch=branch,
            logs=["Deployment started.", *plan.notes],
            metadata={"stack": plan.stack.value, "runtime": plan.runtime},
        )
        await self.deployments.add(deployment)

        try:
            secrets = await self.credentials.load_all(user)
            live_url, logs = await self.executor.deploy(plan, secrets)
            deployment.live_url = live_url
            deployment.logs.extend(logs)
            deployment.status = DeploymentStatus.Successful
            project.live_url = live_url
            project.stack = plan.stack
            await self.projects.update(project)
        except Exception as exc:
            deployment.status = DeploymentStatus.Failed
            deployment.logs.append(f"Deployment failed: {exc}")

        await self.deployments.update(deployment)
        return deployment
