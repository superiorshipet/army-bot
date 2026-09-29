from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
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
from armybot.infrastructure.deploy.platform_executors import (
    GitHubPagesDeployExecutor,
    _github_pages_base_path,
    _github_pages_url,
)


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


def test_github_pages_uses_repository_base_path() -> None:
    assert _github_pages_base_path("Example", "portfolio") == "/portfolio/"
    assert _github_pages_url("Example", "portfolio") == ("https://Example.github.io/portfolio/")
    assert _github_pages_base_path("Example", "example.github.io") == "/"
    assert _github_pages_url("Example", "example.github.io") == ("https://Example.github.io/")


@pytest.mark.asyncio
async def test_github_pages_executor_publishes_without_token_in_command(
    tmp_path, monkeypatch
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "index.html").write_text("<h1>Pages</h1>")
    executor = GitHubPagesDeployExecutor()
    executor.workspace_root = tmp_path
    calls: list[tuple[list[str], dict[str, str], bool]] = []

    async def clone(_plan):
        return repo_path

    async def run(command, _cwd, env=None, inherit_env=True):
        calls.append((command, env or {}, inherit_env))
        return "ok"

    async def configure_pages(owner, repository, token):
        assert (owner, repository, token) == ("example", "demo", "github-secret")
        return "https://example.github.io/demo/"

    monkeypatch.setattr(executor, "_clone", clone)
    monkeypatch.setattr(executor, "_run", run)
    monkeypatch.setattr(executor, "_configure_pages", configure_pages)
    plan = DeploymentPlan(
        project_name="demo",
        repo_url="https://github.com/example/demo",
        branch="main",
        stack=ProjectStack.Static,
        build_steps=[],
        runtime="nginx-static",
        app_entry="index.html",
        commit_sha="abc123",
    )

    live_url, logs, config = await executor.deploy(plan, {"github": {"token": "github-secret"}})

    push_call = next(call for call in calls if call[0][:2] == ["git", "push"])
    assert "github-secret" not in " ".join(push_call[0])
    assert push_call[1]["ARMY_GITHUB_TOKEN"] == "github-secret"
    assert not Path(push_call[1]["GIT_ASKPASS"]).exists()
    assert push_call[2] is False
    assert live_url == "https://example.github.io/demo/"
    assert logs == [
        "GitHub Pages branch published.",
        "GitHub Pages deployment configured.",
    ]
    assert config == {
        "owner": "example",
        "repository": "demo",
        "pages_branch": "gh-pages",
    }


@pytest.mark.asyncio
async def test_github_pages_executor_enables_branch_with_github_api() -> None:
    requests: list[httpx.Request] = []
    responses = iter(
        [
            httpx.Response(404),
            httpx.Response(201),
            httpx.Response(200, json={"html_url": "https://example.github.io/demo"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return next(responses)

    executor = GitHubPagesDeployExecutor(http_transport=httpx.MockTransport(handler))

    live_url = await executor._configure_pages("example", "demo", "github-secret")

    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    assert requests[1].headers["authorization"] == "Bearer github-secret"
    assert live_url == "https://example.github.io/demo/"


@pytest.mark.asyncio
async def test_github_pages_executor_rejects_backend_stacks() -> None:
    executor = GitHubPagesDeployExecutor()
    plan = DeploymentPlan(
        project_name="api",
        repo_url="https://github.com/example/api",
        branch="main",
        stack=ProjectStack.DotNet,
        build_steps=[],
        runtime="systemd",
    )

    with pytest.raises(ValueError, match="Vite, React, and plain static"):
        await executor.deploy(plan, {"github": {"token": "github-secret"}})
