from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from armybot.domain.enums import CredentialProvider
from armybot.infrastructure.telegram.container import container_scope
from armybot.presentation.telegram.formatters import (
    deployment_error_html,
    deployment_report,
    deployment_report_html,
    user_line,
)
from armybot.presentation.telegram.keyboards import (
    access_decision_keyboard,
    admin_panel_keyboard,
    deploy_prompt_keyboard,
    main_menu_keyboard,
    provider_label,
    setup_keyboard,
    start_reply_keyboard,
)
from armybot.shared.settings import settings

router = Router()


class SetupFlow(StatesGroup):
    waiting_credential = State()
    waiting_deploy_repo = State()
    waiting_user_to_add = State()


def _sender_name(message: Message) -> str:
    user = message.from_user
    if not user:
        return "Unknown"
    return user.full_name or user.username or str(user.id)


@router.message(Command("start"))
async def start(message: Message) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        user, created = await c.access.start_or_request_access(
            telegram_id=message.from_user.id,
            full_name=_sender_name(message),
            username=message.from_user.username,
        )

    if user.is_active:
        await message.answer(
            "Quick buttons are ready.",
            reply_markup=start_reply_keyboard(user.is_super_admin),
        )
        await message.answer(
            "Welcome to Army Deploy.\nChoose what you want to do:",
            reply_markup=main_menu_keyboard(user.is_super_admin),
        )
        return

    await message.answer("طلبك اتبعت للادمن. هتقدر تستخدم البوت بعد الموافقة.")
    if created:
        text = f"New access request:\n{user_line(user)}"
        for admin_id in settings.super_admin_ids:
            await message.bot.send_message(
                admin_id,
                text,
                reply_markup=access_decision_keyboard(user.telegram_id),
                parse_mode="Markdown",
            )


@router.message(F.text.in_({"Start", "Menu"}))
async def reply_start(message: Message) -> None:
    await start(message)


@router.message(Command("setup"))
async def setup(message: Message) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        saved = {credential.provider for credential in await c.credentials_repo.list_for_user(user.id)}
    await message.answer(_setup_text(saved), reply_markup=setup_keyboard(saved))


@router.message(F.text == "Setup credentials")
async def reply_setup(message: Message) -> None:
    await setup(message)


@router.message(F.text == "Deploy project")
async def reply_deploy(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
    await state.set_state(SetupFlow.waiting_deploy_repo)
    await message.answer(
        "Send the repository URL or local path.\n"
        "Optional branch format:\n"
        "repo_url branch\n\n"
        "Example:\n"
        "https://github.com/user/project main",
        reply_markup=start_reply_keyboard(user.is_super_admin),
    )


@router.message(F.text == "Projects")
async def reply_projects(message: Message) -> None:
    if not message.from_user:
        return
    await message.answer(
        await _projects_text(message.from_user.id),
        reply_markup=start_reply_keyboard(await _is_admin(message.from_user.id)),
    )


@router.message(F.text == "Status")
async def reply_status(message: Message) -> None:
    if not message.from_user:
        return
    await message.answer(
        await _status_text(message.from_user.id),
        reply_markup=start_reply_keyboard(await _is_admin(message.from_user.id)),
        parse_mode="Markdown",
    )


@router.message(F.text == "Admin panel")
async def reply_admin(message: Message) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        await c.access.require_super_admin(message.from_user.id)
        users = await c.access.pending_users()
    text = "Admin panel\nNo pending users." if not users else "Pending users:\n" + "\n".join(user_line(user) for user in users)
    await message.answer(text, reply_markup=admin_panel_keyboard(), parse_mode="Markdown")


@router.message(F.contact)
async def receive_contact(message: Message) -> None:
    if not message.from_user or not message.contact:
        return
    if message.contact.user_id and message.contact.user_id != message.from_user.id:
        await message.answer("Please share your own phone using the Share phone button.")
        return

    async with container_scope() as c:
        user = await c.access.activate_by_phone(
            telegram_id=message.from_user.id,
            full_name=_sender_name(message),
            username=message.from_user.username,
            phone_number=message.contact.phone_number,
        )

    if user:
        await message.answer(
            "Phone matched. Your account is active now.",
            reply_markup=start_reply_keyboard(user.is_super_admin),
        )
        await message.answer("Choose what you want to do:", reply_markup=main_menu_keyboard(user.is_super_admin))
        return

    await message.answer("This phone number is not approved yet. Ask the admin to add it first.")


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not callback.from_user:
        return
    async with container_scope() as c:
        user = await c.access.require_active(callback.from_user.id)
    await callback.message.edit_text(
        "Army Deploy main menu:",
        reply_markup=main_menu_keyboard(user.is_super_admin),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:setup")
async def menu_setup(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not callback.from_user:
        return
    async with container_scope() as c:
        user = await c.access.require_active(callback.from_user.id)
        saved = {credential.provider for credential in await c.credentials_repo.list_for_user(user.id)}
    await callback.message.edit_text(_setup_text(saved), reply_markup=setup_keyboard(saved))
    await callback.answer()


@router.callback_query(F.data == "menu:deploy")
async def menu_deploy(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    async with container_scope() as c:
        await c.access.require_active(callback.from_user.id)
    await state.set_state(SetupFlow.waiting_deploy_repo)
    await callback.message.edit_text(
        "Send the repository URL or local path.\n"
        "Optional branch format:\n"
        "repo_url branch\n\n"
        "Example:\n"
        "https://github.com/user/project main",
        reply_markup=deploy_prompt_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:projects")
async def menu_projects(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    text = await _projects_text(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=await _main_menu_for(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "menu:status")
async def menu_status(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    text = await _status_text(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=await _main_menu_for(callback.from_user.id), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "menu:admin")
async def menu_admin(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    async with container_scope() as c:
        await c.access.require_super_admin(callback.from_user.id)
        users = await c.access.pending_users()
    if not users:
        text = "Admin panel\nNo pending users."
    else:
        text = "Pending users:\n" + "\n".join(user_line(user) for user in users)
    await callback.message.edit_text(text, reply_markup=admin_panel_keyboard(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "admin:pending")
async def admin_pending(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    async with container_scope() as c:
        await c.access.require_super_admin(callback.from_user.id)
        users = await c.access.pending_users()
    text = "No pending users." if not users else "Pending users:\n" + "\n".join(user_line(user) for user in users)
    await callback.message.edit_text(text, reply_markup=admin_panel_keyboard(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "admin:users")
async def admin_users(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    text = await _all_users_text(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=admin_panel_keyboard(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "admin:add_user")
async def admin_add_user(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    async with container_scope() as c:
        await c.access.require_super_admin(callback.from_user.id)
    await state.set_state(SetupFlow.waiting_user_to_add)
    await callback.message.edit_text(
        "Add user\n\n"
        "Send @username, phone number, or numeric Telegram ID.\n"
        "You can also forward a message from that user if Telegram exposes their ID.\n"
        "Phone users must press Share phone once when they open the bot.\n\n"
        "Example:\n"
        "@username Mohamed\n"
        "+201234567890 Mohamed\n"
        "123456789 Mohamed",
        reply_markup=deploy_prompt_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cred:"))
async def credential_button(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.data:
        return
    _, action, provider_value = callback.data.split(":", 2)
    provider = CredentialProvider(provider_value)
    async with container_scope() as c:
        await c.access.require_active(callback.from_user.id)
    await state.set_state(SetupFlow.waiting_credential)
    await state.update_data(provider=provider.value)
    await callback.message.edit_text(
        _credential_prompt(provider, action),
        reply_markup=deploy_prompt_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "flow:cancel")
async def cancel_flow(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not callback.from_user:
        return
    async with container_scope() as c:
        user = await c.access.require_active(callback.from_user.id)
        saved = {credential.provider for credential in await c.credentials_repo.list_for_user(user.id)}
    await callback.message.edit_text(_setup_text(saved), reply_markup=setup_keyboard(saved))
    await callback.answer("Cancelled")


@router.message(SetupFlow.waiting_credential)
async def receive_credential(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    provider = CredentialProvider(data["provider"])
    try:
        payload = _parse_credential_payload(provider, message.text or "")
    except ValueError as exc:
        await message.answer(str(exc), reply_markup=deploy_prompt_keyboard())
        return

    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        await c.credentials.save(user, provider, payload)
        saved = {credential.provider for credential in await c.credentials_repo.list_for_user(user.id)}

    await state.clear()
    await message.answer(
        f"{provider_label(provider)} saved.\n\n{_setup_text(saved)}",
        reply_markup=setup_keyboard(saved),
    )


@router.message(SetupFlow.waiting_deploy_repo)
async def receive_deploy_repo(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    text = (message.text or "").strip()
    args = text.split()
    if args and args[0].lower().startswith("/deploy"):
        args = args[1:]

    if not args:
        await message.answer(
            "Send the repository URL or local path first.",
            reply_markup=deploy_prompt_keyboard(),
        )
        return

    repo_url = args[0].strip()
    branch = args[1].strip() if len(args) > 1 else "main"
    await state.clear()
    notice = await message.answer("🚀 Deployment started...")

    async def _live_log(text: str) -> None:
        try:
            await notice.edit_text(f"🚀 Deploying...\n\n{text}")
        except Exception:
            pass

    try:
        async with container_scope() as c:
            user = await c.access.require_active(message.from_user.id)
            deployment = await c.deploy.deploy(user, repo_url, branch, on_log=_live_log)
        await notice.edit_text(deployment_report_html(deployment), parse_mode="HTML")
        await message.answer("What do you want to do next?", reply_markup=main_menu_keyboard(user.is_super_admin))
    except Exception as exc:
        await notice.edit_text(deployment_error_html(exc), parse_mode="HTML")



@router.message(SetupFlow.waiting_user_to_add)
async def receive_user_to_add(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    try:
        target = _parse_user_to_add(message)
    except ValueError as exc:
        await message.answer(str(exc), reply_markup=deploy_prompt_keyboard())
        return

    async with container_scope() as c:
        await c.access.require_super_admin(message.from_user.id)
        if isinstance(target[0], int):
            user_or_invite = await c.access.add_active_user(target[0], target[1], target[2])
        elif _looks_like_phone(target[0]):
            user_or_invite = await c.access.add_allowed_phone(target[0], target[1])
        else:
            user_or_invite = await c.access.add_allowed_username(target[0], target[1])

    await state.clear()
    await message.answer(
        _added_user_text(user_or_invite),
        reply_markup=admin_panel_keyboard(),
        parse_mode="Markdown",
    )
    if hasattr(user_or_invite, "telegram_id"):
        try:
            await message.bot.send_message(
                user_or_invite.telegram_id,
                "تم تفعيل حسابك على Army Deploy. ابعت /start عشان تبدأ.",
            )
        except Exception:
            pass


@router.message(Command("set_github"))
async def set_github(message: Message) -> None:
    if not message.from_user:
        return
    token = _command_args(message)
    if not token:
        await message.answer("Usage: /set_github <token>")
        return
    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        await c.credentials.save(user, CredentialProvider.GitHub, {"token": token})
        saved = {credential.provider for credential in await c.credentials_repo.list_for_user(user.id)}
    await message.answer(
        f"GitHub saved.\n\n{_setup_text(saved)}",
        reply_markup=setup_keyboard(saved),
    )


@router.message(Command("set_cloudflare"))
async def set_cloudflare(message: Message) -> None:
    if not message.from_user:
        return
    args = _command_args(message).split()
    if not args:
        await message.answer("Usage: /set_cloudflare <token> [account_id] [zone_id]")
        return
    payload = {
        "token": args[0],
        "account_id": args[1] if len(args) > 1 else None,
        "zone_id": args[2] if len(args) > 2 else None,
    }
    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        await c.credentials.save(user, CredentialProvider.Cloudflare, payload)
        saved = {credential.provider for credential in await c.credentials_repo.list_for_user(user.id)}
    await message.answer(
        f"Cloudflare saved.\n\n{_setup_text(saved)}",
        reply_markup=setup_keyboard(saved),
    )


@router.message(Command("set_server"))
async def set_server(message: Message) -> None:
    if not message.from_user:
        return
    args = _command_args(message).split()
    if len(args) < 4:
        await message.answer(
            "Usage: /set_server <host> <user> <ssh_key_path> <base_path> [public_base_url]"
        )
        return
    payload = {
        "host": args[0],
        "user": args[1],
        "ssh_key_path": args[2],
        "base_path": args[3],
        "public_base_url": args[4] if len(args) > 4 else settings.public_base_url,
    }
    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        await c.credentials.save(user, CredentialProvider.Server, payload)
        saved = {credential.provider for credential in await c.credentials_repo.list_for_user(user.id)}
    await message.answer(
        f"Server saved.\n\n{_setup_text(saved)}",
        reply_markup=setup_keyboard(saved),
    )


@router.message(Command("deploy"))
async def deploy(message: Message) -> None:
    if not message.from_user:
        return
    args = _command_args(message).split()
    if not args:
        await message.answer("Usage: /deploy <repo_url_or_path> [branch]")
        return

    repo_url = args[0]
    branch = args[1] if len(args) > 1 else "main"
    notice = await message.answer("🚀 Deployment started...")

    async def _live_log(text: str) -> None:
        try:
            await notice.edit_text(f"🚀 Deploying...\n\n{text}")
        except Exception:
            pass

    try:
        async with container_scope() as c:
            user = await c.access.require_active(message.from_user.id)
            deployment = await c.deploy.deploy(user, repo_url, branch, on_log=_live_log)
        await notice.edit_text(deployment_report_html(deployment), parse_mode="HTML")
    except Exception as exc:
        await notice.edit_text(deployment_error_html(exc), parse_mode="HTML")



@router.message(Command("projects"))
async def projects(message: Message) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        rows = await c.projects.list_for_user(user.id)
    if not rows:
        await message.answer("No projects yet. Use /deploy <repo_url_or_path>.")
        return
    await message.answer(
        "\n".join(
            f"- {project.name} | {project.stack.value} | {project.live_url or 'no live url yet'}"
            for project in rows
        )
    )


@router.message(Command("status"))
async def status(message: Message) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        deployments = await c.deployments.latest_for_user(user.id, limit=5)
    if not deployments:
        await message.answer("Bot is running. No deployments yet.")
        return
    await message.answer("\n\n".join(deployment_report(item) for item in deployments), parse_mode="Markdown")


@router.message(Command("pending"))
async def pending(message: Message) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        await c.access.require_super_admin(message.from_user.id)
        users = await c.access.pending_users()
    if not users:
        await message.answer("No pending users.")
        return
    await message.answer("\n".join(user_line(user) for user in users), parse_mode="Markdown")


@router.message(Command("users"))
async def users(message: Message) -> None:
    if not message.from_user:
        return
    await message.answer(await _all_users_text(message.from_user.id), parse_mode="Markdown")


@router.message(Command("add_user"))
async def add_user(message: Message) -> None:
    if not message.from_user:
        return
    try:
        target = _parse_user_to_add(message, command_mode=True)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    async with container_scope() as c:
        await c.access.require_super_admin(message.from_user.id)
        if isinstance(target[0], int):
            user_or_invite = await c.access.add_active_user(target[0], target[1], target[2])
        elif _looks_like_phone(target[0]):
            user_or_invite = await c.access.add_allowed_phone(target[0], target[1])
        else:
            user_or_invite = await c.access.add_allowed_username(target[0], target[1])
    await message.answer(
        _added_user_text(user_or_invite),
        reply_markup=admin_panel_keyboard(),
        parse_mode="Markdown",
    )


@router.message(Command("approve"))
async def approve(message: Message) -> None:
    await _admin_status_command(message, "approve")


@router.message(Command("reject"))
async def reject(message: Message) -> None:
    await _admin_status_command(message, "reject")


@router.message(Command("suspend"))
async def suspend(message: Message) -> None:
    await _admin_status_command(message, "suspend")


@router.callback_query(F.data.startswith("access:"))
async def access_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return
    _, action, raw_telegram_id = callback.data.split(":", 2)
    telegram_id = int(raw_telegram_id)
    async with container_scope() as c:
        await c.access.require_super_admin(callback.from_user.id)
        if action == "approve":
            user = await c.access.approve(telegram_id)
            await callback.bot.send_message(telegram_id, "تم تفعيل حسابك. اكتب /setup.")
        else:
            user = await c.access.reject(telegram_id)
            await callback.bot.send_message(telegram_id, "تم رفض طلب استخدام البوت.")
    await callback.message.edit_text(f"{action.title()}d:\n{user_line(user)}", parse_mode="Markdown")
    await callback.answer()


async def _admin_status_command(message: Message, action: str) -> None:
    if not message.from_user:
        return
    raw_id = _command_args(message).strip()
    if not raw_id.isdigit():
        await message.answer(f"Usage: /{action} <telegram_id>")
        return
    async with container_scope() as c:
        await c.access.require_super_admin(message.from_user.id)
        if action == "approve":
            user = await c.access.approve(int(raw_id))
        elif action == "reject":
            user = await c.access.reject(int(raw_id))
        else:
            user = await c.access.suspend(int(raw_id))
    await message.answer(f"Done:\n{user_line(user)}", parse_mode="Markdown")


async def _main_menu_for(telegram_id: int):
    async with container_scope() as c:
        user = await c.access.require_active(telegram_id)
    return main_menu_keyboard(user.is_super_admin)


async def _is_admin(telegram_id: int) -> bool:
    async with container_scope() as c:
        user = await c.access.require_active(telegram_id)
    return user.is_super_admin


async def _all_users_text(telegram_id: int) -> str:
    async with container_scope() as c:
        await c.access.require_super_admin(telegram_id)
        users = await c.access.all_users()
    if not users:
        return "No users yet."
    return "All users:\n" + "\n".join(user_line(user) for user in users)


def _command_args(message: Message) -> str:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _setup_text(saved: set[CredentialProvider]) -> str:
    missing = [
        provider_label(provider)
        for provider in (CredentialProvider.GitHub, CredentialProvider.Server, CredentialProvider.Cloudflare)
        if provider not in saved
    ]
    saved_names = [
        provider_label(provider)
        for provider in (CredentialProvider.GitHub, CredentialProvider.Server, CredentialProvider.Cloudflare)
        if provider in saved
    ]
    lines = ["Credential setup"]
    lines.append("")
    lines.append("Saved: " + (", ".join(saved_names) if saved_names else "none yet"))
    lines.append("Missing: " + (", ".join(missing) if missing else "all required credentials are ready"))
    lines.append("")
    if missing:
        lines.append("Choose the next missing credential, then paste it in the next message.")
    else:
        lines.append("You are ready to deploy. Use Deploy project from the menu.")
    return "\n".join(lines)


def _credential_prompt(provider: CredentialProvider, action: str) -> str:
    verb = "Update" if action == "edit" else "Add"
    if provider == CredentialProvider.GitHub:
        return (
            f"{verb} GitHub credentials\n\n"
            "Paste the GitHub token only.\n"
            "Required access: repo read access for private repositories."
        )
    if provider == CredentialProvider.Cloudflare:
        return (
            f"{verb} Cloudflare credentials\n\n"
            "Paste as:\n"
            "token account_id zone_id\n\n"
            "account_id and zone_id are optional if this deployment does not need them."
        )
    if provider == CredentialProvider.Server:
        return (
            f"{verb} server credentials\n\n"
            "Paste as:\n"
            "host user ssh_key_path base_path public_base_url\n\n"
            "public_base_url is optional."
        )
    return f"{verb} {provider_label(provider)} credentials."


def _parse_credential_payload(provider: CredentialProvider, text: str) -> dict:
    args = text.strip().split()
    if provider == CredentialProvider.GitHub:
        if not text.strip():
            raise ValueError("Paste the GitHub token.")
        return {"token": text.strip()}

    if provider == CredentialProvider.Cloudflare:
        if not args:
            raise ValueError("Paste: token account_id zone_id")
        return {
            "token": args[0],
            "account_id": args[1] if len(args) > 1 else None,
            "zone_id": args[2] if len(args) > 2 else None,
        }

    if provider == CredentialProvider.Server:
        if len(args) < 4:
            raise ValueError("Paste: host user ssh_key_path base_path public_base_url")
        return {
            "host": args[0],
            "user": args[1],
            "ssh_key_path": args[2],
            "base_path": args[3],
            "public_base_url": args[4] if len(args) > 4 else settings.public_base_url,
        }

    raise ValueError("Unsupported credential type.")


def _parse_user_to_add(message: Message, command_mode: bool = False) -> tuple[int, str, str | None] | tuple[str, str | None]:
    forwarded_user = _forwarded_user(message)
    if forwarded_user:
        telegram_id = int(forwarded_user.id)
        full_name = forwarded_user.full_name or forwarded_user.username or str(telegram_id)
        return telegram_id, full_name, forwarded_user.username

    text = _command_args(message) if command_mode else (message.text or "")
    parts = text.strip().split(maxsplit=1)
    if not parts:
        raise ValueError("Send @username, numeric Telegram ID, or forward a message from the user.")
    if parts[0].startswith("@"):
        username = parts[0].removeprefix("@").strip().lower()
        full_name = parts[1].strip() if len(parts) > 1 else None
        return username, full_name
    if _looks_like_phone(parts[0]):
        phone_number = parts[0].strip()
        full_name = parts[1].strip() if len(parts) > 1 else None
        return phone_number, full_name
    if not parts[0].isdigit():
        username = parts[0].strip().lower()
        full_name = parts[1].strip() if len(parts) > 1 else None
        return username, full_name

    telegram_id = int(parts[0])
    full_name = parts[1].strip() if len(parts) > 1 else str(telegram_id)
    return telegram_id, full_name, None


def _added_user_text(user_or_invite) -> str:
    if hasattr(user_or_invite, "telegram_id"):
        return f"User added and activated:\n{user_line(user_or_invite)}"
    if hasattr(user_or_invite, "phone_number"):
        return (
            "Phone approved.\n"
            f"{user_or_invite.phone_number} will be activated when the user presses Share phone."
        )
    return (
        "Username approved.\n"
        f"@{user_or_invite.username} will be activated automatically when they press Start."
    )


def _looks_like_phone(value: str) -> bool:
    cleaned = value.strip()
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    return cleaned.startswith("+") or (cleaned.startswith("0") and cleaned.isdigit() and len(digits) >= 8)


def _forwarded_user(message: Message):
    legacy_user = getattr(message, "forward_from", None)
    if legacy_user:
        return legacy_user
    origin = getattr(message, "forward_origin", None)
    return getattr(origin, "sender_user", None)


async def _projects_text(telegram_id: int) -> str:
    async with container_scope() as c:
        user = await c.access.require_active(telegram_id)
        rows = await c.projects.list_for_user(user.id)
    if not rows:
        return "No projects yet. Use Deploy project from the menu."
    return "\n".join(
        f"- {project.name} | {project.stack.value} | {project.live_url or 'no live url yet'}"
        for project in rows
    )


async def _status_text(telegram_id: int) -> str:
    async with container_scope() as c:
        user = await c.access.require_active(telegram_id)
        deployments = await c.deployments.latest_for_user(user.id, limit=5)
    if not deployments:
        return "Bot is running. No deployments yet."
    return "\n\n".join(deployment_report(item) for item in deployments)
