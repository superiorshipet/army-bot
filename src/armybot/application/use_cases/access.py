from uuid import uuid4

from armybot.application.ports.repositories import UserRepository
from armybot.domain.entities import User
from armybot.domain.enums import UserRole, UserStatus


class AccessService:
    def __init__(self, users: UserRepository, super_admin_ids: set[int]) -> None:
        self.users = users
        self.super_admin_ids = super_admin_ids

    async def start_or_request_access(
        self,
        telegram_id: int,
        full_name: str,
        username: str | None,
    ) -> tuple[User, bool]:
        existing = await self.users.get_by_telegram_id(telegram_id)
        if existing:
            return existing, False

        is_super_admin = telegram_id in self.super_admin_ids
        user = User(
            id=uuid4(),
            telegram_id=telegram_id,
            full_name=full_name,
            username=username,
            role=UserRole.SuperAdmin if is_super_admin else UserRole.User,
            status=UserStatus.Active if is_super_admin else UserStatus.Pending,
        )
        await self.users.add(user)
        return user, True

    async def approve(self, telegram_id: int) -> User:
        user = await self._require_user(telegram_id)
        user.status = UserStatus.Active
        await self.users.update(user)
        return user

    async def reject(self, telegram_id: int) -> User:
        user = await self._require_user(telegram_id)
        user.status = UserStatus.Rejected
        await self.users.update(user)
        return user

    async def suspend(self, telegram_id: int) -> User:
        user = await self._require_user(telegram_id)
        user.status = UserStatus.Suspended
        await self.users.update(user)
        return user

    async def pending_users(self) -> list[User]:
        return await self.users.list_by_status(UserStatus.Pending)

    async def require_active(self, telegram_id: int) -> User:
        user = await self._require_user(telegram_id)
        if not user.is_active:
            raise PermissionError(f"User status is {user.status}.")
        return user

    async def require_super_admin(self, telegram_id: int) -> User:
        user = await self.require_active(telegram_id)
        if not user.is_super_admin:
            raise PermissionError("Only super admins can do that.")
        return user

    async def _require_user(self, telegram_id: int) -> User:
        user = await self.users.get_by_telegram_id(telegram_id)
        if not user:
            raise LookupError("User not found.")
        return user
