import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

from armybot.domain.entities import Deployment, Project, User, UserCredential, UsernameInvite
from armybot.domain.enums import (
    CredentialProvider,
    DeploymentStatus,
    ProjectStack,
    UserRole,
    UserStatus,
)


def sqlite_path_from_url(database_url: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    if database_url.startswith(prefix):
        raw = database_url.removeprefix(prefix)
        return Path("/" + raw.lstrip("/")) if raw.startswith("/") else Path(raw)
    if database_url.startswith("sqlite:///"):
        raw = database_url.removeprefix("sqlite:///")
        return Path("/" + raw.lstrip("/")) if raw.startswith("/") else Path(raw)
    raise ValueError("Not a sqlite URL.")


class SqliteStore:
    def __init__(self, database_url: str) -> None:
        self.path = sqlite_path_from_url(database_url)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    async def create_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL UNIQUE,
                    full_name TEXT NOT NULL,
                    username TEXT,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_credentials (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    encrypted_payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, provider)
                );

                CREATE TABLE IF NOT EXISTS username_invites (
                    username TEXT PRIMARY KEY,
                    full_name TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    repo_url TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    stack TEXT NOT NULL,
                    live_url TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, repo_url, branch)
                );

                CREATE TABLE IF NOT EXISTS deployments (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    commit_sha TEXT,
                    live_url TEXT,
                    logs TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )


class SqliteUserRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def any_exists(self) -> bool:
        with self.store._connect() as conn:
            row = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return row is not None

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        with self.store._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return _row_to_user(row) if row else None

    async def get_by_username(self, username: str) -> User | None:
        with self.store._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE lower(username) = ?", (username.lower(),)).fetchone()
        return _row_to_user(row) if row else None

    async def add(self, user: User) -> None:
        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (id, telegram_id, full_name, username, role, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(user.id),
                    user.telegram_id,
                    user.full_name,
                    user.username,
                    user.role.value,
                    user.status.value,
                    user.created_at.isoformat(),
                ),
            )

    async def update(self, user: User) -> None:
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE users SET full_name=?, username=?, role=?, status=? WHERE id=?",
                (user.full_name, user.username, user.role.value, user.status.value, str(user.id)),
            )

    async def list_by_status(self, status: UserStatus) -> list[User]:
        with self.store._connect() as conn:
            rows = conn.execute("SELECT * FROM users WHERE status = ?", (status.value,)).fetchall()
        return [_row_to_user(row) for row in rows]

    async def add_username_invite(self, invite: UsernameInvite) -> None:
        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO username_invites (username, full_name, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    full_name=excluded.full_name
                """,
                (invite.username, invite.full_name, invite.created_at.isoformat()),
            )

    async def get_username_invite(self, username: str) -> UsernameInvite | None:
        if not username:
            return None
        with self.store._connect() as conn:
            row = conn.execute("SELECT * FROM username_invites WHERE username = ?", (username,)).fetchone()
        return _row_to_username_invite(row) if row else None


class SqliteCredentialRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def upsert(self, credential: UserCredential) -> None:
        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_credentials
                    (id, user_id, provider, encrypted_payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    encrypted_payload=excluded.encrypted_payload,
                    updated_at=excluded.updated_at
                """,
                (
                    str(credential.id),
                    str(credential.user_id),
                    credential.provider.value,
                    credential.encrypted_payload,
                    credential.created_at.isoformat(),
                    credential.updated_at.isoformat(),
                ),
            )

    async def get(self, user_id: UUID, provider: CredentialProvider) -> UserCredential | None:
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_credentials WHERE user_id=? AND provider=?",
                (str(user_id), provider.value),
            ).fetchone()
        return _row_to_credential(row) if row else None

    async def list_for_user(self, user_id: UUID) -> list[UserCredential]:
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM user_credentials WHERE user_id=?",
                (str(user_id),),
            ).fetchall()
        return [_row_to_credential(row) for row in rows]


class SqliteProjectRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def get_by_repo(self, user_id: UUID, repo_url: str, branch: str) -> Project | None:
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE user_id=? AND repo_url=? AND branch=?",
                (str(user_id), repo_url, branch),
            ).fetchone()
        return _row_to_project(row) if row else None

    async def add(self, project: Project) -> None:
        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects
                    (id, user_id, name, repo_url, branch, stack, live_url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(project.id),
                    str(project.user_id),
                    project.name,
                    project.repo_url,
                    project.branch,
                    project.stack.value,
                    project.live_url,
                    project.created_at.isoformat(),
                ),
            )

    async def update(self, project: Project) -> None:
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE projects SET name=?, stack=?, live_url=? WHERE id=?",
                (project.name, project.stack.value, project.live_url, str(project.id)),
            )

    async def list_for_user(self, user_id: UUID) -> list[Project]:
        with self.store._connect() as conn:
            rows = conn.execute("SELECT * FROM projects WHERE user_id=?", (str(user_id),)).fetchall()
        return [_row_to_project(row) for row in rows]


class SqliteDeploymentRepository:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    async def add(self, deployment: Deployment) -> None:
        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO deployments
                    (id, project_id, user_id, status, branch, commit_sha, live_url, logs,
                     metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _deployment_values(deployment),
            )

    async def update(self, deployment: Deployment) -> None:
        with self.store._connect() as conn:
            conn.execute(
                """
                UPDATE deployments
                SET status=?, commit_sha=?, live_url=?, logs=?, metadata=?, updated_at=?
                WHERE id=?
                """,
                (
                    deployment.status.value,
                    deployment.commit_sha,
                    deployment.live_url,
                    json.dumps(deployment.logs),
                    json.dumps(deployment.metadata),
                    deployment.updated_at.isoformat(),
                    str(deployment.id),
                ),
            )

    async def latest_for_user(self, user_id: UUID, limit: int = 10) -> list[Deployment]:
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM deployments WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (str(user_id), limit),
            ).fetchall()
        return [_row_to_deployment(row) for row in rows]


def _row_to_user(row: sqlite3.Row) -> User:
    from datetime import datetime

    return User(
        id=UUID(row["id"]),
        telegram_id=row["telegram_id"],
        full_name=row["full_name"],
        username=row["username"],
        role=UserRole(row["role"]),
        status=UserStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_credential(row: sqlite3.Row) -> UserCredential:
    from datetime import datetime

    return UserCredential(
        id=UUID(row["id"]),
        user_id=UUID(row["user_id"]),
        provider=CredentialProvider(row["provider"]),
        encrypted_payload=row["encrypted_payload"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_username_invite(row: sqlite3.Row) -> UsernameInvite:
    return UsernameInvite(
        username=row["username"],
        full_name=row["full_name"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_project(row: sqlite3.Row) -> Project:
    from datetime import datetime

    return Project(
        id=UUID(row["id"]),
        user_id=UUID(row["user_id"]),
        name=row["name"],
        repo_url=row["repo_url"],
        branch=row["branch"],
        stack=ProjectStack(row["stack"]),
        live_url=row["live_url"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_deployment(row: sqlite3.Row) -> Deployment:
    from datetime import datetime

    return Deployment(
        id=UUID(row["id"]),
        project_id=UUID(row["project_id"]),
        user_id=UUID(row["user_id"]),
        status=DeploymentStatus(row["status"]),
        branch=row["branch"],
        commit_sha=row["commit_sha"],
        live_url=row["live_url"],
        logs=json.loads(row["logs"]),
        metadata=json.loads(row["metadata"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _deployment_values(deployment: Deployment) -> tuple:
    return (
        str(deployment.id),
        str(deployment.project_id),
        str(deployment.user_id),
        deployment.status.value,
        deployment.branch,
        deployment.commit_sha,
        deployment.live_url,
        json.dumps(deployment.logs),
        json.dumps(deployment.metadata),
        deployment.created_at.isoformat(),
        deployment.updated_at.isoformat(),
    )
