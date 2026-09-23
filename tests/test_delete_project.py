import pytest
from datetime import datetime, timezone
from uuid import uuid4

from armybot.domain.entities import Project, User
from armybot.domain.enums import ProjectStack, UserRole, UserStatus
from armybot.infrastructure.database.sqlite_store import SqliteProjectRepository, SqliteStore
from armybot.infrastructure.deploy.remote_executor import RemoteRecipeDeployExecutor
from armybot.application.use_cases.deploy import DeployProjectService


@pytest.mark.asyncio
async def test_sqlite_project_repository_delete(tmp_path):
    db_path = tmp_path / "test.db"
    store = SqliteStore(f"sqlite:///{db_path}")
    await store.create_schema()
    repo = SqliteProjectRepository(store)

    user_id = uuid4()
    project = Project(
        id=uuid4(),
        user_id=user_id,
        name="test-project",
        repo_url="https://github.com/example/test",
        branch="main",
        stack=ProjectStack.Node,
        live_url="https://example.com/test-project/",
    )

    await repo.add(project)

    # Test get_by_id
    fetched_by_id = await repo.get_by_id(project.id)
    assert fetched_by_id is not None
    assert fetched_by_id.name == "test-project"

    # Test get_by_name
    fetched_by_name = await repo.get_by_name(user_id, "test-project")
    assert fetched_by_name is not None
    assert fetched_by_name.id == project.id

    # Test delete
    await repo.delete(project.id)
    assert await repo.get_by_id(project.id) is None
    assert await repo.get_by_name(user_id, "test-project") is None


@pytest.mark.asyncio
async def test_deploy_service_delete_project_permissions(tmp_path):
    db_path = tmp_path / "test2.db"
    store = SqliteStore(f"sqlite:///{db_path}")
    await store.create_schema()
    repo = SqliteProjectRepository(store)

    owner_user = User(
        id=uuid4(),
        telegram_id=111,
        full_name="Owner",
        username="owner",
        phone_number=None,
        role=UserRole.User,
        status=UserStatus.Active,
        created_at=datetime.now(timezone.utc),
    )
    other_user = User(
        id=uuid4(),
        telegram_id=222,
        full_name="Other",
        username="other",
        phone_number=None,
        role=UserRole.User,
        status=UserStatus.Active,
        created_at=datetime.now(timezone.utc),
    )

    project = Project(
        id=uuid4(),
        user_id=owner_user.id,
        name="my-app",
        repo_url="https://github.com/example/my-app",
        branch="main",
        stack=ProjectStack.Node,
    )
    await repo.add(project)

    class MockAnalyzer:
        pass

    class MockExecutor:
        async def cleanup_project(self, name, secrets, on_log=None):
            return ["cleaned"]

    class MockDeployments:
        pass

    class MockCredentials:
        async def load_all(self, user):
            return {"server": {"host": "localhost", "user": "ubuntu", "ssh_key_path": "/tmp/key"}}

    service = DeployProjectService(
        analyzer=MockAnalyzer(),
        executor=MockExecutor(),
        projects=repo,
        deployments=MockDeployments(),
        credentials=MockCredentials(),
    )

    # Unauthorized delete attempt
    success, msg = await service.delete_project(other_user, str(project.id))
    assert success is False
    assert "permission" in msg.lower()

    # Authorized delete attempt
    success, msg = await service.delete_project(owner_user, str(project.id))
    assert success is True
    assert "deleted successfully" in msg
    assert await repo.get_by_id(project.id) is None


def test_remote_executor_safe_name_and_cleanup_validation():
    executor = RemoteRecipeDeployExecutor()
    assert executor._safe_name("my Project 123!") == "my-project-123"
    assert executor._safe_name("test_app") == "test_app"
