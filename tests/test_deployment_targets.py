from datetime import UTC, datetime
from uuid import uuid4

import pytest

from armybot.application.use_cases.deploy import DeployProjectService
from armybot.domain.entities import DeploymentPlan, Project, User
from armybot.domain.enums import (
    DeploymentTarget,
    ProjectStack,
    UserRole,
    UserStatus,
)
from armybot.infrastructure.database.sqlite_store import (
    SqliteDeploymentRepository,
    SqliteProjectRepository,
    SqliteStore,
    SqliteUserRepository,
)
from armybot.infrastructure.deploy.coordinator import DeploymentCoordinator
from armybot.infrastructure.deploy.github_monitor import GitHubBranchClient


@pytest.mark.asyncio
async def test_sqlite_project_persists_auto_deploy_target_and_config(tmp_path) -> None:
    store = SqliteStore(f"sqlite:///{tmp_path / 'targets.db'}")
    await store.create_schema()
    repository = SqliteProjectRepository(store)
    project = Project(
        id=uuid4(),
        user_id=uuid4(),
        name="target-app",
        repo_url="https://github.com/example/target-app",
        branch="main",
        stack=ProjectStack.Vite,
        deployment_target=DeploymentTarget.Railway,
        auto_deploy_enabled=True,
        last_deployed_sha="abc123",
        last_triggered_sha="abc123",
        target_config={"project_id": "railway-id"},
    )

    await repository.add(project)
    restored = await repository.get_by_id(project.id)
    watched = await repository.list_auto_deploy()

    assert restored is not None
    assert restored.deployment_target == DeploymentTarget.Railway
    assert restored.target_config == {"project_id": "railway-id"}
    assert restored.last_deployed_sha == "abc123"
    assert [item.id for item in watched] == [project.id]


def test_github_monitor_parses_repository_urls() -> None:
    assert GitHubBranchClient._repository_parts(
        "https://github.com/superiorshipet/army-bot.git"
    ) == ("superiorshipet", "army-bot")

    with pytest.raises(ValueError, match="file or folder"):
        GitHubBranchClient._repository_parts("https://github.com/superiorshipet/army-bot/tree/main")


@pytest.mark.asyncio
async def test_deploy_service_routes_to_selected_platform_and_saves_result() -> None:
    user = User(
        id=uuid4(),
        telegram_id=100,
        full_name="Test User",
        username="tester",
        phone_number=None,
        role=UserRole.User,
        status=UserStatus.Active,
        created_at=datetime.now(UTC),
    )

    class Analyzer:
        async def analyze(self, repo_url, branch):
            return DeploymentPlan(
                project_name="demo",
                repo_url=repo_url,
                branch=branch,
                stack=ProjectStack.Vite,
                build_steps=[],
                runtime="nginx-static",
                commit_sha="new-sha",
            )

    class Executor:
        def __init__(self, name):
            self.name = name
            self.called = False

        async def deploy(self, plan, credentials, on_log=None):
            self.called = True
            return f"https://{self.name}.example", ["done"], {"project_id": self.name}

    class Projects:
        def __init__(self):
            self.project = None

        async def get_by_repo(self, user_id, repo_url, branch):
            return self.project

        async def add(self, project):
            self.project = project

        async def update(self, project):
            self.project = project

    class Deployments:
        async def add(self, deployment):
            pass

        async def update(self, deployment):
            pass

    class Credentials:
        async def load_all(self, current_user):
            return {"railway": {"token": "secret"}}

    server = Executor("server")
    railway = Executor("railway")
    projects = Projects()
    service = DeployProjectService(
        analyzer=Analyzer(),
        executor=server,
        projects=projects,
        deployments=Deployments(),
        credentials=Credentials(),
        coordinator=DeploymentCoordinator(),
        platform_executors={DeploymentTarget.Railway: railway},
    )

    deployment = await service.deploy(
        user,
        "https://github.com/example/demo",
        target=DeploymentTarget.Railway,
    )

    assert railway.called is True
    assert server.called is False
    assert deployment.live_url == "https://railway.example"
    assert projects.project.deployment_target == DeploymentTarget.Railway
    assert projects.project.last_deployed_sha == "new-sha"
    assert projects.project.target_config == {"project_id": "railway"}


@pytest.mark.asyncio
async def test_admin_redeploys_existing_project_as_owner(tmp_path) -> None:
    store = SqliteStore(f"sqlite:///{tmp_path / 'admin-deploy.db'}")
    await store.create_schema()
    users = SqliteUserRepository(store)
    projects = SqliteProjectRepository(store)
    deployments = SqliteDeploymentRepository(store)
    owner = User(
        id=uuid4(),
        telegram_id=101,
        full_name="Owner",
        username="owner",
        phone_number=None,
        role=UserRole.User,
        status=UserStatus.Active,
    )
    admin = User(
        id=uuid4(),
        telegram_id=102,
        full_name="Admin",
        username="admin",
        phone_number=None,
        role=UserRole.SuperAdmin,
        status=UserStatus.Active,
    )
    project = Project(
        id=uuid4(),
        user_id=owner.id,
        name="owned-app",
        repo_url="https://github.com/example/owned-app",
        branch="main",
        stack=ProjectStack.Static,
    )
    await users.add(owner)
    await users.add(admin)
    await projects.add(project)

    class Analyzer:
        async def analyze(self, repo_url, branch):
            return DeploymentPlan(
                project_name="owned-app",
                repo_url=repo_url,
                branch=branch,
                stack=ProjectStack.Static,
                build_steps=[],
                runtime="nginx-static",
                commit_sha="owner-sha",
            )

    class Executor:
        async def deploy(self, plan, credentials, on_log=None):
            assert credentials == {"server": {"owner": True}}
            return "https://example.test/owned-app", ["deployed"]

    class Credentials:
        async def load_all(self, user):
            assert user.id == owner.id
            return {"server": {"owner": True}}

    service = DeployProjectService(
        analyzer=Analyzer(),
        executor=Executor(),
        projects=projects,
        deployments=deployments,
        credentials=Credentials(),
        coordinator=DeploymentCoordinator(),
        users=users,
    )

    deployment = await service.deploy_existing(admin, str(project.id))
    restored = await projects.get_by_id(project.id)
    history = await deployments.latest_for_project(project.id, 1)

    assert deployment.user_id == owner.id
    assert restored is not None
    assert restored.user_id == owner.id
    assert restored.last_deployed_sha == "owner-sha"
    assert history[0].id == deployment.id
    assert history[0].metadata["triggered_by"] == "admin"
