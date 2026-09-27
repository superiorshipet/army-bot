import pytest
from uuid import uuid4

from armybot.domain.entities import User
from armybot.domain.enums import UserRole, UserStatus
from armybot.infrastructure.database.sqlite_store import SqliteStore, SqliteUserRepository
from armybot.application.use_cases.access import AccessService
from armybot.presentation.telegram.keyboards import (
    admin_panel_keyboard,
    main_menu_keyboard,
    rejected_users_keyboard,
)


def test_main_menu_keyboard_visibility():
    # Regular user, no server credentials
    kb1 = main_menu_keyboard(is_admin=False, has_server_cred=False)
    texts1 = [btn.text for row in kb1.inline_keyboard for btn in row]
    assert "Admin panel" not in texts1
    assert "Deploy project" not in texts1
    assert "Setup credentials" in texts1
    assert "Projects" in texts1
    assert "Status" in texts1

    # Regular user, has server credentials
    kb2 = main_menu_keyboard(is_admin=False, has_server_cred=True)
    texts2 = [btn.text for row in kb2.inline_keyboard for btn in row]
    assert "Admin panel" not in texts2
    assert "Deploy project" in texts2

    # Admin user, has server credentials
    kb3 = main_menu_keyboard(is_admin=True, has_server_cred=True)
    texts3 = [btn.text for row in kb3.inline_keyboard for btn in row]
    assert "Admin panel" in texts3
    assert "Deploy project" in texts3


def test_admin_and_rejected_keyboards():
    admin_kb = admin_panel_keyboard()
    admin_callbacks = [btn.callback_data for row in admin_kb.inline_keyboard for btn in row]
    assert "admin:rejected" in admin_callbacks

    user = User(
        id=uuid4(),
        telegram_id=987654,
        full_name="Rejected User",
        username="rejected_one",
        phone_number=None,
        role=UserRole.User,
        status=UserStatus.Rejected,
    )
    rej_kb = rejected_users_keyboard([user])
    rej_callbacks = [btn.callback_data for row in rej_kb.inline_keyboard for btn in row]
    assert "admin:unreject:987654" in rej_callbacks


@pytest.mark.asyncio
async def test_access_service_rejected_and_unreject(tmp_path):
    db_path = tmp_path / "test_access.db"
    store = SqliteStore(f"sqlite:///{db_path}")
    await store.create_schema()
    user_repo = SqliteUserRepository(store)
    access = AccessService(user_repo, super_admin_ids={111})

    # Start user
    user, created = await access.start_or_request_access(222, "Test Person", "testperson")
    assert created is True
    assert user.status == UserStatus.Pending

    # Reject user
    rej_user = await access.reject(222)
    assert rej_user.status == UserStatus.Rejected

    # Check rejected_users
    rejected_list = await access.rejected_users()
    assert len(rejected_list) == 1
    assert rejected_list[0].telegram_id == 222

    # Unreject user
    unrejected = await access.unreject(222)
    assert unrejected is True

    # User is no longer in rejected
    assert len(await access.rejected_users()) == 0
    assert await user_repo.get_by_telegram_id(222) is None

    # Now user can send /start again cleanly
    user_again, created_again = await access.start_or_request_access(222, "Test Person", "testperson")
    assert created_again is True
    assert user_again.status == UserStatus.Pending
