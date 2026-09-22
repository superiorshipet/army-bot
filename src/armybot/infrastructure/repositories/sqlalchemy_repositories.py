from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from armybot.domain.entities import Deployment, PhoneInvite, Project, User, UserCredential, UsernameInvite
from armybot.domain.enums import CredentialProvider, UserStatus
from armybot.infrastructure.database.models import (
    DeploymentModel,
    PhoneInviteModel,
    ProjectModel,
    UserCredentialModel,
    UserModel,
    UsernameInviteModel,
)
from armybot.infrastructure.repositories.mappers import (
    to_credential,
    to_deployment,
    to_project,
    to_user,
    to_phone_invite,
    to_username_invite,
)


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        model = await self.session.scalar(select(UserModel).where(UserModel.telegram_id == telegram_id))
        return to_user(model) if model else None

    async def get_by_username(self, username: str) -> User | None:
        model = await self.session.scalar(select(UserModel).where(UserModel.username.ilike(username)))
        return to_user(model) if model else None

    async def get_by_phone_number(self, phone_number: str) -> User | None:
        model = await self.session.scalar(select(UserModel).where(UserModel.phone_number == phone_number))
        return to_user(model) if model else None

    async def add(self, user: User) -> None:
        self.session.add(
            UserModel(
                id=user.id,
                telegram_id=user.telegram_id,
                full_name=user.full_name,
                username=user.username,
                phone_number=user.phone_number,
                role=user.role,
                status=user.status,
                created_at=user.created_at,
            )
        )
        await self.session.flush()

    async def update(self, user: User) -> None:
        model = await self.session.get(UserModel, user.id)
        if not model:
            raise LookupError("User not found.")
        model.full_name = user.full_name
        model.username = user.username
        model.phone_number = user.phone_number
        model.role = user.role
        model.status = user.status
        await self.session.flush()

    async def list_by_status(self, status: UserStatus) -> list[User]:
        rows = await self.session.scalars(select(UserModel).where(UserModel.status == status))
        return [to_user(row) for row in rows]

    async def list_all(self) -> list[User]:
        rows = await self.session.scalars(select(UserModel).order_by(UserModel.created_at.desc()))
        return [to_user(row) for row in rows]

    async def add_username_invite(self, invite: UsernameInvite) -> None:
        model = await self.session.get(UsernameInviteModel, invite.username)
        if model:
            model.full_name = invite.full_name
            await self.session.flush()
            return
        self.session.add(
            UsernameInviteModel(
                username=invite.username,
                full_name=invite.full_name,
                created_at=invite.created_at,
            )
        )
        await self.session.flush()

    async def get_username_invite(self, username: str) -> UsernameInvite | None:
        if not username:
            return None
        model = await self.session.get(UsernameInviteModel, username)
        return to_username_invite(model) if model else None

    async def add_phone_invite(self, invite: PhoneInvite) -> None:
        model = await self.session.get(PhoneInviteModel, invite.phone_number)
        if model:
            model.full_name = invite.full_name
            await self.session.flush()
            return
        self.session.add(
            PhoneInviteModel(
                phone_number=invite.phone_number,
                full_name=invite.full_name,
                created_at=invite.created_at,
            )
        )
        await self.session.flush()

    async def get_phone_invite(self, phone_number: str) -> PhoneInvite | None:
        if not phone_number:
            return None
        model = await self.session.get(PhoneInviteModel, phone_number)
        return to_phone_invite(model) if model else None


class SqlCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, credential: UserCredential) -> None:
        model = await self.session.scalar(
            select(UserCredentialModel).where(
                UserCredentialModel.user_id == credential.user_id,
                UserCredentialModel.provider == credential.provider,
            )
        )
        if model:
            model.encrypted_payload = credential.encrypted_payload
            await self.session.flush()
            return
        self.session.add(
            UserCredentialModel(
                id=credential.id,
                user_id=credential.user_id,
                provider=credential.provider,
                encrypted_payload=credential.encrypted_payload,
            )
        )
        await self.session.flush()

    async def get(self, user_id: UUID, provider: CredentialProvider) -> UserCredential | None:
        model = await self.session.scalar(
            select(UserCredentialModel).where(
                UserCredentialModel.user_id == user_id,
                UserCredentialModel.provider == provider,
            )
        )
        return to_credential(model) if model else None

    async def list_for_user(self, user_id: UUID) -> list[UserCredential]:
        rows = await self.session.scalars(
            select(UserCredentialModel).where(UserCredentialModel.user_id == user_id)
        )
        return [to_credential(row) for row in rows]


class SqlProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_repo(self, user_id: UUID, repo_url: str, branch: str) -> Project | None:
        model = await self.session.scalar(
            select(ProjectModel).where(
                ProjectModel.user_id == user_id,
                ProjectModel.repo_url == repo_url,
                ProjectModel.branch == branch,
            )
        )
        return to_project(model) if model else None

    async def add(self, project: Project) -> None:
        self.session.add(
            ProjectModel(
                id=project.id,
                user_id=project.user_id,
                name=project.name,
                repo_url=project.repo_url,
                branch=project.branch,
                stack=project.stack,
                live_url=project.live_url,
            )
        )
        await self.session.flush()

    async def update(self, project: Project) -> None:
        model = await self.session.get(ProjectModel, project.id)
        if not model:
            raise LookupError("Project not found.")
        model.name = project.name
        model.stack = project.stack
        model.live_url = project.live_url
        await self.session.flush()

    async def list_for_user(self, user_id: UUID) -> list[Project]:
        rows = await self.session.scalars(select(ProjectModel).where(ProjectModel.user_id == user_id))
        return [to_project(row) for row in rows]


class SqlDeploymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, deployment: Deployment) -> None:
        self.session.add(
            DeploymentModel(
                id=deployment.id,
                project_id=deployment.project_id,
                user_id=deployment.user_id,
                status=deployment.status,
                branch=deployment.branch,
                commit_sha=deployment.commit_sha,
                live_url=deployment.live_url,
                logs=deployment.logs,
                deployment_metadata=deployment.metadata,
            )
        )
        await self.session.flush()

    async def update(self, deployment: Deployment) -> None:
        model = await self.session.get(DeploymentModel, deployment.id)
        if not model:
            raise LookupError("Deployment not found.")
        model.status = deployment.status
        model.commit_sha = deployment.commit_sha
        model.live_url = deployment.live_url
        model.logs = deployment.logs
        model.deployment_metadata = deployment.metadata
        await self.session.flush()

    async def latest_for_user(self, user_id: UUID, limit: int = 10) -> list[Deployment]:
        rows = await self.session.scalars(
            select(DeploymentModel)
            .where(DeploymentModel.user_id == user_id)
            .order_by(DeploymentModel.created_at.desc())
            .limit(limit)
        )
        return [to_deployment(row) for row in rows]
