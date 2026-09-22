from armybot.domain.entities import Deployment, Project, User, UserCredential
from armybot.infrastructure.database.models import (
    DeploymentModel,
    ProjectModel,
    UserCredentialModel,
    UserModel,
)


def to_user(model: UserModel) -> User:
    return User(
        id=model.id,
        telegram_id=model.telegram_id,
        full_name=model.full_name,
        username=model.username,
        role=model.role,
        status=model.status,
        created_at=model.created_at,
    )


def to_credential(model: UserCredentialModel) -> UserCredential:
    return UserCredential(
        id=model.id,
        user_id=model.user_id,
        provider=model.provider,
        encrypted_payload=model.encrypted_payload,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def to_project(model: ProjectModel) -> Project:
    return Project(
        id=model.id,
        user_id=model.user_id,
        name=model.name,
        repo_url=model.repo_url,
        branch=model.branch,
        stack=model.stack,
        live_url=model.live_url,
        created_at=model.created_at,
    )


def to_deployment(model: DeploymentModel) -> Deployment:
    return Deployment(
        id=model.id,
        project_id=model.project_id,
        user_id=model.user_id,
        status=model.status,
        branch=model.branch,
        commit_sha=model.commit_sha,
        live_url=model.live_url,
        logs=model.logs or [],
        metadata=model.deployment_metadata or {},
        created_at=model.created_at,
        updated_at=model.updated_at,
    )
