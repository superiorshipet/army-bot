from uuid import uuid4

from armybot.application.ports.repositories import UserRepository
from armybot.domain.entities import PhoneInvite, User, UsernameInvite
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
            if username and existing.username != username:
                existing.username = username
                await self.users.update(existing)
            return existing, False

        is_super_admin = telegram_id in self.super_admin_ids
        invite = await self.users.get_username_invite(_normalize_username(username))
        is_invited = invite is not None
        user = User(
            id=uuid4(),
            telegram_id=telegram_id,
            full_name=invite.full_name or full_name if invite else full_name,
            username=username,
            phone_number=None,
            role=UserRole.SuperAdmin if is_super_admin else UserRole.User,
            status=UserStatus.Active if is_super_admin or is_invited else UserStatus.Pending,
        )
        await self.users.add(user)
        return user, True

    async def approve(self, telegram_id: int) -> User:
        user = await self._require_user(telegram_id)
        user.status = UserStatus.Active
        await self.users.update(user)
        return user

    async def add_active_user(
        self,
        telegram_id: int,
        full_name: str,
        username: str | None = None,
        phone_number: str | None = None,
    ) -> User:
        existing = await self.users.get_by_telegram_id(telegram_id)
        if existing:
            existing.full_name = full_name or existing.full_name
            existing.username = username or existing.username
            existing.phone_number = _normalize_phone(phone_number) or existing.phone_number
            existing.role = UserRole.SuperAdmin if telegram_id in self.super_admin_ids else UserRole.User
            existing.status = UserStatus.Active
            await self.users.update(existing)
            return existing

        user = User(
            id=uuid4(),
            telegram_id=telegram_id,
            full_name=full_name or str(telegram_id),
            username=username,
            phone_number=_normalize_phone(phone_number),
            role=UserRole.SuperAdmin if telegram_id in self.super_admin_ids else UserRole.User,
            status=UserStatus.Active,
        )
        await self.users.add(user)
        return user

    async def add_allowed_username(self, username: str, full_name: str | None = None) -> User | UsernameInvite:
        normalized = _normalize_username(username)
        if not normalized:
            raise ValueError("Username is required.")

        existing = await self.users.get_by_username(normalized)
        if existing:
            existing.full_name = full_name or existing.full_name
            existing.status = UserStatus.Active
            await self.users.update(existing)
            return existing

        invite = UsernameInvite(username=normalized, full_name=full_name)
        await self.users.add_username_invite(invite)
        return invite

    async def add_allowed_phone(self, phone_number: str, full_name: str | None = None) -> User | PhoneInvite:
        normalized = _normalize_phone(phone_number)
        if not normalized:
            raise ValueError("Phone number is required.")

        existing = await self.users.get_by_phone_number(normalized)
        if existing:
            existing.full_name = full_name or existing.full_name
            existing.status = UserStatus.Active
            await self.users.update(existing)
            return existing

        invite = PhoneInvite(phone_number=normalized, full_name=full_name)
        await self.users.add_phone_invite(invite)
        return invite

    async def activate_by_phone(
        self,
        telegram_id: int,
        full_name: str,
        username: str | None,
        phone_number: str,
    ) -> User | None:
        normalized = _normalize_phone(phone_number)
        invite = await self.users.get_phone_invite(normalized)
        if not invite:
            return None

        existing = await self.users.get_by_telegram_id(telegram_id)
        if existing:
            existing.full_name = invite.full_name or full_name or existing.full_name
            existing.username = username or existing.username
            existing.phone_number = normalized
            existing.status = UserStatus.Active
            await self.users.update(existing)
            return existing

        user = User(
            id=uuid4(),
            telegram_id=telegram_id,
            full_name=invite.full_name or full_name or str(telegram_id),
            username=username,
            phone_number=normalized,
            role=UserRole.SuperAdmin if telegram_id in self.super_admin_ids else UserRole.User,
            status=UserStatus.Active,
        )
        await self.users.add(user)
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

    async def all_users(self) -> list[User]:
        return await self.users.list_all()

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


def _normalize_username(username: str | None) -> str:
    return (username or "").strip().removeprefix("@").lower()


def _normalize_phone(phone_number: str | None) -> str:
    return "".join(ch for ch in (phone_number or "") if ch.isdigit() or ch == "+")
