from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar
from uuid import UUID

T = TypeVar("T")


class DeploymentAlreadyRunningError(RuntimeError):
    pass


class DeploymentCoordinator:
    """Coordinate deployments within the single bot process."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._target_tasks: dict[str, asyncio.Task[object]] = {}
        self._user_tasks: dict[UUID, asyncio.Task[object]] = {}

    async def run(self, target_key: str, user_id: UUID, operation: Awaitable[T]) -> T:
        async with self._guard:
            existing_user = self._user_tasks.get(user_id)
            if existing_user and not existing_user.done():
                if hasattr(operation, "close"):
                    operation.close()  # type: ignore[attr-defined]
                raise DeploymentAlreadyRunningError(
                    "You already have a deployment queued or running."
                )

            existing = self._target_tasks.get(target_key)
            if existing and not existing.done():
                if hasattr(operation, "close"):
                    operation.close()  # type: ignore[attr-defined]
                raise DeploymentAlreadyRunningError(
                    "A deployment for this project is already queued or running."
                )

            task = asyncio.create_task(operation)
            self._target_tasks[target_key] = task
            self._user_tasks[user_id] = task

        try:
            return await task
        finally:
            async with self._guard:
                if self._target_tasks.get(target_key) is task:
                    self._target_tasks.pop(target_key, None)
                if self._user_tasks.get(user_id) is task:
                    self._user_tasks.pop(user_id, None)

    async def cancel_for_user(self, user_id: UUID) -> bool:
        async with self._guard:
            task = self._user_tasks.get(user_id)
            if not task or task.done():
                return False
            task.cancel()
            return True


deployment_coordinator = DeploymentCoordinator()
