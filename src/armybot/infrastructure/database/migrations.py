from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

Migration = tuple[int, Sequence[str]]

MIGRATIONS: tuple[Migration, ...] = (
    (
        1,
        (
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_credentials_provider "
                "ON user_credentials (user_id, provider)"
            ),
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_repo_branch "
                "ON projects (user_id, repo_url, branch)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_deployments_user_created "
                "ON deployments (user_id, created_at DESC)"
            ),
        ),
    ),
)


async def apply_migrations(connection: AsyncConnection) -> None:
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    result = await connection.execute(text("SELECT version FROM schema_migrations"))
    applied = set(result.scalars())

    for version, statements in MIGRATIONS:
        if version in applied:
            continue
        for statement in statements:
            await connection.execute(text(statement))
        await connection.execute(
            text("INSERT INTO schema_migrations (version) VALUES (:version)"),
            {"version": version},
        )
