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


def _build_executor():
    """Return the appropriate executor based on execution_mode."""
    if settings.execution_mode == "ai":
        from armybot.infrastructure.deploy.ai_agent import AIDeployAgent, GroqDeployAgent

        provider = settings.ai_provider.strip().lower()
        strategy = settings.ai_deploy_strategy.strip().lower()
        if provider == "groq" and strategy == "recipe":
            from armybot.infrastructure.deploy.remote_executor import RemoteRecipeDeployExecutor

            return RemoteRecipeDeployExecutor(command_timeout=settings.ai_command_timeout)

        if provider == "groq":
            return GroqDeployAgent(
                api_key=settings.groq_api_key,
                model=settings.groq_model,
                max_commands=settings.ai_max_commands,
                command_timeout=settings.ai_command_timeout,
            )

        return AIDeployAgent(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            max_commands=settings.ai_max_commands,
            command_timeout=settings.ai_command_timeout,
        )
    return SafeDeploymentExecutor()


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
            executor=_build_executor(),
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
            executor=_build_executor(),
            projects=container.projects,
            deployments=container.deployments,
            credentials=container.credentials,
        )
        yield container
        return

    async for session in session_scope():
        yield RequestContainer(session)
