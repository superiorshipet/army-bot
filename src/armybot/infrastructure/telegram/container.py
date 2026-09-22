from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from armybot.application.use_cases.access import AccessService
from armybot.application.use_cases.credentials import CredentialService
from armybot.application.use_cases.deploy import DeployProjectService
from armybot.infrastructure.database.session import session_scope
from armybot.infrastructure.database.sqlite_store import (
    SqliteCredentialRepository,
    SqliteDeploymentRepository,
    SqliteProjectRepository,
    SqliteStore,
    SqliteUserRepository,
)
from armybot.infrastructure.deploy.analyzer import FilesystemRepoAnalyzer
from armybot.infrastructure.deploy.executor import SafeDeploymentExecutor
from armybot.infrastructure.repositories.sqlalchemy_repositories import (
    SqlCredentialRepository,
    SqlDeploymentRepository,
    SqlProjectRepository,
    SqlUserRepository,
)
from armybot.infrastructure.security.fernet_box import FernetSecretBox
from armybot.shared.settings import settings


class RequestContainer:
    def __init__(self, session: AsyncSession) -> None:
        self.users = SqlUserRepository(session)
        self.credentials_repo = SqlCredentialRepository(session)
        self.projects = SqlProjectRepository(session)
        self.deployments = SqlDeploymentRepository(session)
        self.secret_box = FernetSecretBox(settings.encryption_key)

        self.access = AccessService(self.users, settings.super_admin_ids)
        self.credentials = CredentialService(self.credentials_repo, self.secret_box)
        self.deploy = DeployProjectService(
            analyzer=FilesystemRepoAnalyzer(),
            executor=SafeDeploymentExecutor(),
            projects=self.projects,
            deployments=self.deployments,
            credentials=self.credentials,
        )


@asynccontextmanager
async def container_scope() -> AsyncIterator[RequestContainer]:
    if settings.database_url.startswith("sqlite"):
        store = SqliteStore(settings.database_url)
        await store.create_schema()
        container = RequestContainer.__new__(RequestContainer)
        container.users = SqliteUserRepository(store)
        container.credentials_repo = SqliteCredentialRepository(store)
        container.projects = SqliteProjectRepository(store)
        container.deployments = SqliteDeploymentRepository(store)
        container.secret_box = FernetSecretBox(settings.encryption_key)
        container.access = AccessService(container.users, settings.super_admin_ids)
        container.credentials = CredentialService(container.credentials_repo, container.secret_box)
        container.deploy = DeployProjectService(
            analyzer=FilesystemRepoAnalyzer(),
            executor=SafeDeploymentExecutor(),
            projects=container.projects,
            deployments=container.deployments,
            credentials=container.credentials,
        )
        yield container
        return

    async for session in session_scope():
        yield RequestContainer(session)
