from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from armybot.domain.enums import (
    CredentialProvider,
    DeploymentStatus,
    ProjectStack,
    UserRole,
    UserStatus,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class User:
    id: UUID
    telegram_id: int
    full_name: str
    username: str | None
    role: UserRole
    status: UserStatus
    created_at: datetime = field(default_factory=utcnow)

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.Active

    @property
    def is_super_admin(self) -> bool:
        return self.role == UserRole.SuperAdmin


@dataclass(slots=True)
class UserCredential:
    id: UUID
    user_id: UUID
    provider: CredentialProvider
    encrypted_payload: str
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(slots=True)
class Project:
    id: UUID
    user_id: UUID
    name: str
    repo_url: str
    branch: str
    stack: ProjectStack = ProjectStack.Unknown
    live_url: str | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass(slots=True)
class Deployment:
    id: UUID
    project_id: UUID
    user_id: UUID
    status: DeploymentStatus
    branch: str
    commit_sha: str | None = None
    live_url: str | None = None
    logs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class DeploymentPlan:
    project_name: str
    repo_url: str
    branch: str
    stack: ProjectStack
    build_steps: list[str]
    runtime: str
    notes: list[str] = field(default_factory=list)
