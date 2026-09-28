import json
from html import escape
from io import BytesIO
from uuid import UUID

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from armybot.domain.entities import Deployment, Project, User
from armybot.domain.enums import CredentialProvider, DeploymentTarget, UserStatus
from armybot.infrastructure.telegram.container import container_scope
from armybot.presentation.telegram.formatters import (
    deployment_error_html,
    deployment_report_html,
    user_line,
)
from armybot.presentation.telegram.keyboards import (
    access_decision_keyboard,
    admin_panel_keyboard,
    admin_project_keyboard,
    admin_user_projects_keyboard,
    admin_users_keyboard,
    cancel_deployment_keyboard,
    deploy_prompt_keyboard,
    deployment_target_keyboard,
    main_menu_keyboard,
    pending_users_keyboard,
    project_delete_confirm_keyboard,
    project_details_keyboard,
    projects_list_keyboard,
    provider_label,
    rejected_users_keyboard,
    setup_keyboard,
)
from armybot.shared.settings import settings

router = Router()
logger = structlog.get_logger()


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
        async with container_scope() as c:
            creds = await c.credentials_repo.list_for_user(user.id)
            has_server_cred = any(cr.provider == CredentialProvider.Server for cr in creds)
        try:
            rm = await message.answer("...", reply_markup=ReplyKeyboardRemove())
            await rm.delete()
        except Exception:  # noqa: BLE001
            logger.debug("telegram.noncritical_action_failed")
        await message.answer(
            "Welcome to Army Deploy.\nChoose what you want to do:",
            reply_markup=main_menu_keyboard(user.is_super_admin, has_server_cred=has_server_cred),
        )
        return

    if user.status == UserStatus.Pending:
        await message.answer(
            "⏳ طلبك قيد المراجعة من الأدمن. هتقدر تستخدم البوت فور الموافقة على حسابك.",
            reply_markup=ReplyKeyboardRemove(),
        )
    elif user.status == UserStatus.Rejected:
        await message.answer(
            "❌ تم رفض طلب انضمامك مسبقاً. تواصل مع الأدمن إذا كنت تعتقد أن هذا خطأ.",
            reply_markup=ReplyKeyboardRemove(),
        )
    elif user.status == UserStatus.Suspended:
        await message.answer(
            "⛔ تم إيقاف حسابك مؤقتاً بواسطة الأدمن.",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await message.answer(
            "طلبك اتبعت للادمن. هتقدر تستخدم البوت بعد الموافقة.",
            reply_markup=ReplyKeyboardRemove(),
        )

    if created:
        text = f"New access request:\n{user_line(user)}"
        for admin_id in settings.super_admin_ids:
            await message.bot.send_message(
                admin_id,
                text,
                reply_markup=access_decision_keyboard(user.telegram_id),
                parse_mode="HTML",
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
        saved = {
            credential.provider for credential in await c.credentials_repo.list_for_user(user.id)
        }
    await message.answer(_setup_text(saved), reply_markup=setup_keyboard(saved))


@router.message(F.text == "Setup credentials")
async def reply_setup(message: Message) -> None:
    await setup(message)


@router.message(F.text == "Deploy project")
async def reply_deploy(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        await c.access.require_active(message.from_user.id)
    await state.clear()
    await message.answer(
        "Where do you want to deploy this project?",
        reply_markup=deployment_target_keyboard(),
    )


@router.message(F.text == "Projects")
async def reply_projects(message: Message) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        rows = await c.projects.list_for_user(user.id)
    if not rows:
        await message.answer(
            "No projects yet. Use Deploy project from the menu.",
            reply_markup=main_menu_keyboard(user.is_super_admin),
        )
        return
    text = "📦 <b>Your Projects:</b>\n\n" + "\n".join(
        f"• <b>{project.name}</b> ({project.stack.value})\n  🔗 {project.live_url or 'No live URL'}"
        for project in rows
    )
    await message.answer(
        text,
        reply_markup=projects_list_keyboard(rows),
        parse_mode="HTML",
    )


@router.message(F.text == "Status")
async def reply_status(message: Message) -> None:
    if not message.from_user:
        return
    text = await _status_text(message.from_user.id)
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄 Refresh", callback_data="menu:status"),
                    InlineKeyboardButton(text="⬅️ Back to menu", callback_data="menu:home"),
                ]
            ]
        ),
        parse_mode="HTML",
    )


@router.message(F.text == "Admin panel")
async def reply_admin(message: Message) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        await c.access.require_super_admin(message.from_user.id)
        users = await c.access.pending_users()
    text = (
        "Admin panel\nNo pending users."
        if not users
        else "Pending users:\n" + "\n".join(user_line(user) for user in users)
    )
    await message.answer(text, reply_markup=admin_panel_keyboard(), parse_mode="HTML")


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
        try:
            rm = await message.answer(
                "Phone matched. Your account is active now.", reply_markup=ReplyKeyboardRemove()
            )
            await rm.delete()
        except Exception:  # noqa: BLE001
            logger.debug("telegram.noncritical_action_failed")
        await message.answer(
            "Welcome to Army Deploy. Choose what you want to do:",
            reply_markup=await _main_menu_for(user.telegram_id),
        )
        return

    await message.answer("This phone number is not approved yet. Ask the admin to add it first.")


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not callback.from_user:
        return
    await callback.message.edit_text(
        "Army Deploy main menu:",
        reply_markup=await _main_menu_for(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:setup")
async def menu_setup(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not callback.from_user:
        return
    async with container_scope() as c:
        user = await c.access.require_active(callback.from_user.id)
        saved = {
            credential.provider for credential in await c.credentials_repo.list_for_user(user.id)
        }
    await callback.message.edit_text(_setup_text(saved), reply_markup=setup_keyboard(saved))
    await callback.answer()


@router.callback_query(F.data == "menu:deploy")
async def menu_deploy(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    async with container_scope() as c:
        await c.access.require_active(callback.from_user.id)
    await state.clear()
    await callback.message.edit_text(
        "Where do you want to deploy this project?",
        reply_markup=deployment_target_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deploy:target:"))
async def choose_deployment_target(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.data:
        return
    target = DeploymentTarget(callback.data.rsplit(":", 1)[1])
    provider = _target_provider(target)
    async with container_scope() as c:
        user = await c.access.require_active(callback.from_user.id)
        saved = {
            credential.provider for credential in await c.credentials_repo.list_for_user(user.id)
        }
    if provider not in saved:
        await callback.message.edit_text(
            f"Add your {provider_label(provider)} credentials first.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"Add {provider_label(provider)}",
                            callback_data=f"cred:add:{provider.value}",
                        )
                    ],
                    [InlineKeyboardButton(text="Back", callback_data="menu:deploy")],
                ]
            ),
        )
        await callback.answer()
        return
    await state.set_state(SetupFlow.waiting_deploy_repo)
    await state.update_data(deployment_target=target.value)
    await callback.message.edit_text(
        "Send the HTTP(S) Git repository URL.\n"
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
    async with container_scope() as c:
        user = await c.access.require_active(callback.from_user.id)
        rows = await c.projects.list_for_user(user.id)
    if not rows:
        await callback.message.edit_text(
            "No projects yet. Use Deploy project from the menu.",
            reply_markup=await _main_menu_for(callback.from_user.id),
        )
    else:
        text = "📦 <b>Your Projects:</b>\n\n" + "\n".join(
            f"• <b>{project.name}</b> ({project.stack.value})\n  🔗 {project.live_url or 'No live URL'}"
            for project in rows
        )
        await callback.message.edit_text(
            text,
            reply_markup=projects_list_keyboard(rows),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "menu:status")
async def menu_status(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    text = await _status_text(callback.from_user.id)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🔄 Refresh", callback_data="menu:status"),
                        InlineKeyboardButton(text="⬅️ Back to menu", callback_data="menu:home"),
                    ]
                ]
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as err:
        if "message is not modified" not in str(err).lower():
            raise
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
    try:
        await callback.message.edit_text(
            text, reply_markup=admin_panel_keyboard(), parse_mode="HTML"
        )
    except TelegramBadRequest as err:
        if "message is not modified" not in str(err).lower():
            raise
    await callback.answer()


@router.callback_query(F.data == "admin:pending")
async def admin_pending(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    async with container_scope() as c:
        await c.access.require_super_admin(callback.from_user.id)
        users = await c.access.pending_users()
    text = (
        "No pending users."
        if not users
        else "Pending users:\n" + "\n".join(user_line(user) for user in users)
    )
    markup = pending_users_keyboard(users) if users else admin_panel_keyboard()
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as err:
        if "message is not modified" not in str(err).lower():
            raise
    await callback.answer()


@router.callback_query(F.data == "admin:users")
async def admin_users(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    async with container_scope() as c:
        await c.access.require_super_admin(callback.from_user.id)
        users = await c.access.all_users()
    text = (
        "No users yet."
        if not users
        else f"<b>All users ({len(users)}):</b>\n\nChoose a user to view their projects."
    )
    markup = admin_users_keyboard(users) if users else admin_panel_keyboard()
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as err:
        if "message is not modified" not in str(err).lower():
            raise
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user:"))
async def admin_user_details(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return
    try:
        user_id = UUID(callback.data.split(":", 2)[2])
    except ValueError:
        await callback.answer("Invalid user.", show_alert=True)
        return
    async with container_scope() as c:
        await c.access.require_super_admin(callback.from_user.id)
        target_user = await c.users.get_by_id(user_id)
        projects = await c.projects.list_for_user(user_id) if target_user else []
        project_rows = []
        for project in projects:
            deployments = await c.deployments.latest_for_project(project.id, 1)
            project_rows.append((project, deployments[0] if deployments else None))
    if not target_user:
        await callback.answer("User not found.", show_alert=True)
        return
    project_lines = [
        (
            f"• <b>{escape(project.name)}</b> - "
            f"<code>{escape(deployment.status.value if deployment else 'not_deployed')}</code>"
        )
        for project, deployment in project_rows
    ]
    text = (
        f"<b>User details</b>\n\n"
        f"{user_line(target_user)}\n"
        f"Projects: <b>{len(projects)}</b>\n\n"
        + ("\n".join(project_lines) if project_lines else "No projects yet.")
    )
    await callback.message.edit_text(
        text,
        reply_markup=admin_user_projects_keyboard(projects),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:project:[0-9a-f-]{36}$"))
async def admin_project_details(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return
    project_id = UUID(callback.data.rsplit(":", 1)[1])
    async with container_scope() as c:
        await c.access.require_super_admin(callback.from_user.id)
        project = await c.projects.get_by_id(project_id)
        owner = await c.users.get_by_id(project.user_id) if project else None
        deployments = await c.deployments.latest_for_project(project_id, 1) if project else []
    if not project:
        await callback.answer("Project not found.", show_alert=True)
        return
    latest = deployments[0] if deployments else None
    await callback.message.edit_text(
        _admin_project_text(project, owner, latest),
        reply_markup=admin_project_keyboard(project),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:project:logs:"))
async def admin_project_logs(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return
    project_id = UUID(callback.data.rsplit(":", 1)[1])
    async with container_scope() as c:
        await c.access.require_super_admin(callback.from_user.id)
        project = await c.projects.get_by_id(project_id)
        deployments = await c.deployments.latest_for_project(project_id, 1) if project else []
    if not project:
        await callback.answer("Project not found.", show_alert=True)
        return
    if not deployments:
        text = f"<b>{escape(project.name)}</b>\n\nNo deployment logs yet."
    else:
        deployment = deployments[0]
        logs = "\n".join(escape(line[:180]) for line in deployment.logs[-20:])
        text = (
            f"<b>{escape(project.name)} - latest deployment</b>\n\n"
            f"Status: <code>{escape(deployment.status.value)}</code>\n"
            f"Branch: <code>{escape(deployment.branch)}</code>\n"
            f"Commit: <code>{escape(deployment.commit_sha or 'unknown')}</code>\n\n"
            f"<b>Logs</b>\n<pre>{logs or 'No logs.'}</pre>"
        )
    await callback.message.edit_text(
        text,
        reply_markup=admin_project_keyboard(project),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:project:deploy:"))
async def admin_project_deploy(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return
    project_id = callback.data.rsplit(":", 1)[1]
    await callback.message.edit_text(
        "Deployment started...", reply_markup=cancel_deployment_keyboard()
    )

    async def _live_log(text: str) -> None:
        try:
            await callback.message.edit_text(
                f"Deploying...\n\n{escape(text)}",
                reply_markup=cancel_deployment_keyboard(),
            )
        except TelegramBadRequest:
            logger.debug("telegram.noncritical_action_failed")

    try:
        async with container_scope() as c:
            admin = await c.access.require_super_admin(callback.from_user.id)
            deployment = await c.deploy.deploy_existing(admin, project_id, on_log=_live_log)
        await callback.message.edit_text(
            deployment_report_html(deployment),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Project details", callback_data=f"admin:project:{project_id}"
                        )
                    ]
                ]
            ),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.exception("telegram.admin_deployment_failed", project_id=project_id)
        await callback.message.edit_text(deployment_error_html(exc), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:rejected")
async def admin_rejected(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    async with container_scope() as c:
        await c.access.require_super_admin(callback.from_user.id)
        users = await c.access.rejected_users()
    if not users:
        text = "Admin panel\nNo rejected users."
        markup = admin_panel_keyboard()
    else:
        text = "🚫 <b>Rejected users:</b>\n\n" + "\n".join(user_line(user) for user in users)
        markup = rejected_users_keyboard(users)
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as err:
        if "message is not modified" not in str(err).lower():
            raise
    await callback.answer()


@router.callback_query(F.data.startswith("admin:unreject:"))
async def admin_unreject_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return
    raw_telegram_id = callback.data.split(":", 2)[2]
    telegram_id = int(raw_telegram_id)
    async with container_scope() as c:
        await c.access.require_super_admin(callback.from_user.id)
        await c.access.unreject(telegram_id)
        users = await c.access.rejected_users()

    await callback.answer("✅ Removed from rejected list.")
    if not users:
        text = "Admin panel\nNo rejected users remaining."
        markup = admin_panel_keyboard()
    else:
        text = "🚫 <b>Rejected users:</b>\n\n" + "\n".join(user_line(user) for user in users)
        markup = rejected_users_keyboard(users)
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as err:
        if "message is not modified" not in str(err).lower():
            raise


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
        saved = {
            credential.provider for credential in await c.credentials_repo.list_for_user(user.id)
        }
    await callback.message.edit_text(_setup_text(saved), reply_markup=setup_keyboard(saved))
    await callback.answer("Cancelled")


@router.message(SetupFlow.waiting_credential)
async def receive_credential(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    provider = CredentialProvider(data["provider"])
    try:
        credential_text = message.text or ""
        if message.document:
            if provider != CredentialProvider.Firebase:
                raise ValueError("This credential must be sent as text.")
            if message.document.file_size and message.document.file_size > 64 * 1024:
                raise ValueError("Firebase credential JSON must be smaller than 64 KB.")
            buffer = BytesIO()
            await message.bot.download(message.document, destination=buffer)
            credential_text = buffer.getvalue().decode("utf-8")
        payload = _parse_credential_payload(provider, credential_text)
    except ValueError as exc:
        await message.answer(str(exc), reply_markup=deploy_prompt_keyboard())
        return

    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        await c.credentials.save(user, provider, payload)
        saved = {
            credential.provider for credential in await c.credentials_repo.list_for_user(user.id)
        }

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

    data = await state.get_data()
    target = DeploymentTarget(data.get("deployment_target", DeploymentTarget.Server.value))
    repo_url = args[0].strip()
    branch = args[1].strip() if len(args) > 1 else "main"
    await state.clear()
    notice = await message.answer(
        "🚀 Deployment started...", reply_markup=cancel_deployment_keyboard()
    )

    async def _live_log(text: str) -> None:
        try:
            await notice.edit_text(
                f"🚀 Deploying...\n\n{text}", reply_markup=cancel_deployment_keyboard()
            )
        except Exception:  # noqa: BLE001
            logger.debug("telegram.noncritical_action_failed")

    try:
        async with container_scope() as c:
            user = await c.access.require_active(message.from_user.id)
            creds = await c.credentials_repo.list_for_user(user.id)
            provider = _target_provider(target)
            if not any(cr.provider == provider for cr in creds):
                await notice.edit_text(
                    f"Add your <b>{provider_label(provider)}</b> credentials first.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=f"Add {provider_label(provider)}",
                                    callback_data=f"cred:add:{provider.value}",
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="⬅️ Back to menu", callback_data="menu:home"
                                )
                            ],
                        ]
                    ),
                    parse_mode="HTML",
                )
                return
            deployment = await c.deploy.deploy(
                user,
                repo_url,
                branch,
                on_log=_live_log,
                target=target,
            )
        await notice.edit_text(deployment_report_html(deployment), parse_mode="HTML")
        await message.answer(
            "What do you want to do next?", reply_markup=await _main_menu_for(message.from_user.id)
        )
    except Exception as exc:
        logger.exception("telegram.deployment_failed")
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
        parse_mode="HTML",
    )
    if hasattr(user_or_invite, "telegram_id"):
        try:
            await message.bot.send_message(
                user_or_invite.telegram_id,
                "تم تفعيل حسابك على Army Deploy. ابعت /start عشان تبدأ.",
            )
        except Exception:  # noqa: BLE001
            logger.debug("telegram.noncritical_action_failed")


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
        saved = {
            credential.provider for credential in await c.credentials_repo.list_for_user(user.id)
        }
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
        saved = {
            credential.provider for credential in await c.credentials_repo.list_for_user(user.id)
        }
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
        saved = {
            credential.provider for credential in await c.credentials_repo.list_for_user(user.id)
        }
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
        await message.answer("Usage: /deploy <https_repo_url> [branch]")
        return

    repo_url = args[0]
    branch = args[1] if len(args) > 1 else "main"
    notice = await message.answer(
        "🚀 Deployment started...", reply_markup=cancel_deployment_keyboard()
    )

    async def _live_log(text: str) -> None:
        try:
            await notice.edit_text(
                f"🚀 Deploying...\n\n{text}", reply_markup=cancel_deployment_keyboard()
            )
        except Exception:  # noqa: BLE001
            logger.debug("telegram.noncritical_action_failed")

    try:
        async with container_scope() as c:
            user = await c.access.require_active(message.from_user.id)
            deployment = await c.deploy.deploy(user, repo_url, branch, on_log=_live_log)
        await notice.edit_text(deployment_report_html(deployment), parse_mode="HTML")
    except Exception as exc:
        logger.exception("telegram.deployment_failed")
        await notice.edit_text(deployment_error_html(exc), parse_mode="HTML")


@router.message(Command("cancel"))
async def cancel_deployment(message: Message) -> None:
    if not message.from_user:
        return

    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        cancelled = await c.deploy.cancel_current(user)

    if cancelled:
        await message.answer("Cancelling the current deployment...")
        return

    await message.answer("No deployment is currently queued or running.")


@router.callback_query(F.data == "deployment:cancel")
async def cancel_deployment_callback(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return

    async with container_scope() as c:
        user = await c.access.require_active(callback.from_user.id)
        cancelled = await c.deploy.cancel_current(user)

    if cancelled:
        await callback.answer("Cancelling deployment...", show_alert=True)
        return

    await callback.answer("No deployment is currently running.", show_alert=True)


@router.message(Command("projects"))
async def projects(message: Message) -> None:
    await reply_projects(message)


@router.message(Command("delete", "delete_project"))
async def command_delete_project(message: Message) -> None:
    if not message.from_user:
        return
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        rows = await c.projects.list_for_user(user.id)

    if not rows:
        await message.answer("No projects available to delete.")
        return

    if len(parts) > 1:
        target_name = parts[1].strip()
        matched = next((p for p in rows if p.name.lower() == target_name.lower()), None)
        if matched:
            confirm_text = (
                f"⚠️ <b>Delete Project Confirmation</b>\n\n"
                f"Are you sure you want to permanently delete <b>{matched.name}</b>?\n\n"
                f"This will:\n"
                f"• Stop and disable its systemd service (<code>army-{matched.name}</code>)\n"
                f"• Remove its Nginx configuration\n"
                f"• Delete its files from the server\n"
                f"• Delete all its deployment records\n\n"
                f"<i>This action cannot be undone!</i>"
            )
            await message.answer(
                confirm_text,
                reply_markup=project_delete_confirm_keyboard(matched.id),
                parse_mode="HTML",
            )
            return
        await message.answer(
            f"Project '{target_name}' not found. Please choose from your projects below:"
        )

    await message.answer(
        "Select a project to delete:",
        reply_markup=projects_list_keyboard(rows),
    )


@router.callback_query(F.data.startswith("project:view:"))
async def project_view(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    proj_id_str = callback.data.split(":", 2)[2]
    async with container_scope() as c:
        user = await c.access.require_active(callback.from_user.id)
        try:
            from uuid import UUID

            proj_id = UUID(proj_id_str)
            project = await c.projects.get_by_id(proj_id)
        except ValueError:
            project = None

    if project and project.user_id != user.id and not user.is_super_admin:
        await callback.answer("You do not have permission to view this project.", show_alert=True)
        return

    if not project:
        await callback.answer("Project not found.", show_alert=True)
        return

    text = (
        f"📦 <b>Project Details:</b>\n\n"
        f"<b>Name:</b> {project.name}\n"
        f"<b>Stack:</b> {project.stack.value}\n"
        f"<b>Branch:</b> {project.branch}\n"
        f"<b>Target:</b> {project.deployment_target.value}\n"
        f"<b>Auto deploy:</b> {'On' if project.auto_deploy_enabled else 'Off'}\n"
        f"<b>Repo:</b> {project.repo_url}\n"
        f"<b>Live URL:</b> {project.live_url or 'None'}\n"
    )
    await callback.message.edit_text(
        text,
        reply_markup=project_details_keyboard(project),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("project:auto:"))
async def project_auto_deploy(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return
    _, _, project_id, action = callback.data.split(":", 3)
    enabled = action == "on"
    try:
        async with container_scope() as c:
            user = await c.access.require_active(callback.from_user.id)
            project = await c.deploy.set_auto_deploy(user, project_id, enabled)
    except (LookupError, PermissionError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    text = (
        f"📦 <b>Project Details:</b>\n\n"
        f"<b>Name:</b> {project.name}\n"
        f"<b>Stack:</b> {project.stack.value}\n"
        f"<b>Branch:</b> {project.branch}\n"
        f"<b>Target:</b> {project.deployment_target.value}\n"
        f"<b>Auto deploy:</b> {'On' if project.auto_deploy_enabled else 'Off'}\n"
        f"<b>Repo:</b> {project.repo_url}\n"
        f"<b>Live URL:</b> {project.live_url or 'None'}\n"
    )
    await callback.message.edit_text(
        text,
        reply_markup=project_details_keyboard(project),
        parse_mode="HTML",
    )
    await callback.answer("Auto deploy enabled" if enabled else "Auto deploy disabled")


@router.callback_query(F.data.startswith("project:deploy:"))
async def project_deploy_now(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return
    project_id = callback.data.rsplit(":", 1)[1]
    try:
        from uuid import UUID

        async with container_scope() as c:
            user = await c.access.require_active(callback.from_user.id)
            project = await c.projects.get_by_id(UUID(project_id))
            if not project:
                raise LookupError("Project not found.")
            if project.user_id != user.id and not user.is_super_admin:
                raise PermissionError("You do not have permission to deploy this project.")
            await callback.message.edit_text(
                f"Deploying {project.name} to {project.deployment_target.value}..."
            )
            deployment = await c.deploy.deploy_existing(user, project_id)
    except (LookupError, PermissionError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.message.edit_text(
        deployment_report_html(deployment),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Back to Projects", callback_data="menu:projects")]
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("project:del_prompt:"))
async def project_delete_prompt(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    proj_id_str = callback.data.split(":", 2)[2]
    async with container_scope() as c:
        user = await c.access.require_active(callback.from_user.id)
        try:
            from uuid import UUID

            proj_id = UUID(proj_id_str)
            project = await c.projects.get_by_id(proj_id)
        except ValueError:
            project = None

    if project and project.user_id != user.id and not user.is_super_admin:
        await callback.answer("You do not have permission to delete this project.", show_alert=True)
        return

    if not project:
        await callback.answer("Project not found.", show_alert=True)
        return

    text = (
        f"⚠️ <b>Delete Project Confirmation</b>\n\n"
        f"Are you sure you want to permanently delete <b>{project.name}</b>?\n\n"
        f"This will:\n"
        f"• Stop and disable systemd service (<code>army-{project.name}</code>)\n"
        f"• Remove Nginx configuration\n"
        f"• Delete project files from <code>/var/www/{project.name}</code>\n"
        f"• Remove all deployment records\n\n"
        f"<i>This action cannot be undone!</i>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=project_delete_confirm_keyboard(project.id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("project:del_cancel:"))
async def project_delete_cancel(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    proj_id_str = callback.data.split(":", 2)[2]
    async with container_scope() as c:
        user = await c.access.require_active(callback.from_user.id)
        try:
            from uuid import UUID

            proj_id = UUID(proj_id_str)
            project = await c.projects.get_by_id(proj_id)
        except ValueError:
            project = None

    if project and project.user_id != user.id and not user.is_super_admin:
        await callback.answer("You do not have permission to view this project.", show_alert=True)
        return

    if project:
        text = (
            f"📦 <b>Project Details:</b>\n\n"
            f"<b>Name:</b> {project.name}\n"
            f"<b>Stack:</b> {project.stack.value}\n"
            f"<b>Branch:</b> {project.branch}\n"
            f"<b>Target:</b> {project.deployment_target.value}\n"
            f"<b>Auto deploy:</b> {'On' if project.auto_deploy_enabled else 'Off'}\n"
            f"<b>Repo:</b> {project.repo_url}\n"
            f"<b>Live URL:</b> {project.live_url or 'None'}\n"
        )
        await callback.message.edit_text(
            text,
            reply_markup=project_details_keyboard(project),
            parse_mode="HTML",
        )
    else:
        await menu_projects(callback)
    await callback.answer("Deletion cancelled.")


@router.callback_query(F.data.startswith("project:del_confirm:"))
async def project_delete_confirm(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    proj_id_str = callback.data.split(":", 2)[2]
    await callback.message.edit_text(
        "⏳ <b>Deleting project...</b>\nStopping services, removing Nginx config, and cleaning up server files...",
        parse_mode="HTML",
    )

    async def _cleanup_log(text: str) -> None:
        try:
            await callback.message.edit_text(
                f"⏳ <b>Deleting project...</b>\n\n{text}", parse_mode="HTML"
            )
        except Exception:  # noqa: BLE001
            logger.debug("telegram.noncritical_action_failed")

    async with container_scope() as c:
        user = await c.access.require_active(callback.from_user.id)
        success, msg = await c.deploy.delete_project(user, proj_id_str, on_log=_cleanup_log)

    back_callback = "admin:users" if user.is_super_admin else "menu:projects"
    back_label = "Back to All users" if user.is_super_admin else "Back to Projects"

    if success:
        await callback.message.edit_text(
            f"✅ <b>{msg}</b>\n\nAll server resources and database records have been completely removed.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=back_label, callback_data=back_callback)]
                ]
            ),
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            f"❌ <b>Deletion failed:</b>\n{msg}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=back_label, callback_data=back_callback)]
                ]
            ),
            parse_mode="HTML",
        )
    await callback.answer()


@router.message(Command("status"))
async def status(message: Message) -> None:
    if not message.from_user:
        return
    text = await _status_text(message.from_user.id)
    await message.answer(text, parse_mode="HTML")


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
    await message.answer(
        "Pending users:\n" + "\n".join(user_line(user) for user in users),
        reply_markup=pending_users_keyboard(users),
        parse_mode="HTML",
    )


@router.message(Command("users"))
async def users(message: Message) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        await c.access.require_super_admin(message.from_user.id)
        rows = await c.access.all_users()
    await message.answer(
        f"<b>All users ({len(rows)}):</b>\n\nChoose a user to view their projects.",
        reply_markup=admin_users_keyboard(rows),
        parse_mode="HTML",
    )


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
        parse_mode="HTML",
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


@router.message(Command("rejected"))
async def rejected_command(message: Message) -> None:
    if not message.from_user:
        return
    async with container_scope() as c:
        await c.access.require_super_admin(message.from_user.id)
        users = await c.access.rejected_users()
    if not users:
        await message.answer("No rejected users.")
        return
    await message.answer(
        "🚫 <b>Rejected users:</b>\n\n" + "\n".join(user_line(user) for user in users),
        reply_markup=rejected_users_keyboard(users),
        parse_mode="HTML",
    )


@router.message(Command("unreject"))
async def unreject_command(message: Message) -> None:
    if not message.from_user:
        return
    raw_id = _command_args(message).strip()
    if not raw_id.isdigit():
        await message.answer("Usage: /unreject <telegram_id>")
        return
    telegram_id = int(raw_id)
    async with container_scope() as c:
        await c.access.require_super_admin(message.from_user.id)
        deleted = await c.access.unreject(telegram_id)
    if deleted:
        await message.answer(
            f"✅ User <code>{telegram_id}</code> removed from rejected list. They can now send /start again.",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"User <code>{telegram_id}</code> was not found in database.", parse_mode="HTML"
        )


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
        else:
            user = await c.access.reject(telegram_id)
        remaining = await c.access.pending_users()
    try:
        notification = (
            "تم تفعيل حسابك. اكتب /setup أو /start."
            if action == "approve"
            else "تم رفض طلب استخدام البوت."
        )
        await callback.bot.send_message(telegram_id, notification)
    except TelegramAPIError:
        logger.warning("telegram.user_status_notification_failed", telegram_id=telegram_id)
    text = (
        f"{action.title()}d {escape(user.full_name)}.\n\nNo pending users remaining."
        if not remaining
        else "Pending users:\n" + "\n".join(user_line(item) for item in remaining)
    )
    await callback.message.edit_text(
        text,
        reply_markup=(pending_users_keyboard(remaining) if remaining else admin_panel_keyboard()),
        parse_mode="HTML",
    )
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
    await message.answer(f"Done:\n{user_line(user)}", parse_mode="HTML")


def _admin_project_text(
    project: Project,
    owner: User | None,
    deployment: Deployment | None,
) -> str:
    owner_name = owner.full_name if owner else "Unknown user"
    status = deployment.status.value if deployment else "not_deployed"
    commit = deployment.commit_sha if deployment and deployment.commit_sha else "unknown"
    updated = deployment.updated_at.strftime("%Y-%m-%d %H:%M UTC") if deployment else "Never"
    return (
        f"<b>Admin project details</b>\n\n"
        f"Name: <b>{escape(project.name)}</b>\n"
        f"Owner: {escape(owner_name)}\n"
        f"Stack: <code>{escape(project.stack.value)}</code>\n"
        f"Target: <code>{escape(project.deployment_target.value)}</code>\n"
        f"Branch: <code>{escape(project.branch)}</code>\n"
        f"Status: <code>{escape(status)}</code>\n"
        f"Commit: <code>{escape(commit)}</code>\n"
        f"Last update: {escape(updated)}\n"
        f"Auto deploy: {'On' if project.auto_deploy_enabled else 'Off'}\n"
        f"Live URL: {escape(project.live_url or 'None')}"
    )


async def _main_menu_for(telegram_id: int):
    async with container_scope() as c:
        user = await c.access.require_active(telegram_id)
        creds = await c.credentials_repo.list_for_user(user.id)
        has_server_cred = any(cr.provider == CredentialProvider.Server for cr in creds)
    return main_menu_keyboard(user.is_super_admin, has_server_cred=has_server_cred)


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
        for provider in (
            CredentialProvider.GitHub,
            CredentialProvider.Server,
            CredentialProvider.Cloudflare,
            CredentialProvider.Railway,
            CredentialProvider.Firebase,
        )
        if provider not in saved
    ]
    saved_names = [
        provider_label(provider)
        for provider in (
            CredentialProvider.GitHub,
            CredentialProvider.Server,
            CredentialProvider.Cloudflare,
            CredentialProvider.Railway,
            CredentialProvider.Firebase,
        )
        if provider in saved
    ]
    lines = ["Credential setup"]
    lines.append("")
    lines.append("Saved: " + (", ".join(saved_names) if saved_names else "none yet"))
    lines.append("Not added: " + (", ".join(missing) if missing else "none"))
    lines.append("")
    if missing:
        lines.append("Add only the destinations you want to use.")
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
    if provider == CredentialProvider.Railway:
        return (
            f"{verb} Railway credentials\n\n"
            "Paste your Railway account token.\n"
            "Optional format when you use multiple workspaces:\n"
            "token workspace_id"
        )
    if provider == CredentialProvider.Firebase:
        return (
            f"{verb} Firebase credentials\n\n"
            "Upload the service-account JSON file, or paste its full JSON content here."
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

    if provider == CredentialProvider.Railway:
        if not args:
            raise ValueError("Paste your Railway account token.")
        return {
            "token": args[0],
            "workspace": args[1] if len(args) > 1 else None,
        }

    if provider == CredentialProvider.Firebase:
        try:
            service_account = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Send a valid Firebase service-account JSON file.") from exc
        if not isinstance(service_account, dict):
            raise ValueError("Firebase service-account content must be a JSON object.")
        required = {"project_id", "client_email", "private_key"}
        if not required.issubset(service_account):
            raise ValueError(
                "Firebase JSON must include project_id, client_email, and private_key."
            )
        return {
            "project_id": service_account["project_id"],
            "service_account": service_account,
        }

    raise ValueError("Unsupported credential type.")


def _target_provider(target: DeploymentTarget) -> CredentialProvider:
    return {
        DeploymentTarget.Server: CredentialProvider.Server,
        DeploymentTarget.Railway: CredentialProvider.Railway,
        DeploymentTarget.Firebase: CredentialProvider.Firebase,
    }[target]


def _parse_user_to_add(
    message: Message, command_mode: bool = False
) -> tuple[int, str, str | None] | tuple[str, str | None]:
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
    return cleaned.startswith("+") or (
        cleaned.startswith("0") and cleaned.isdigit() and len(digits) >= 8
    )


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
        deployments = await c.deployments.latest_for_user(user.id, limit=3)
    if not deployments:
        return "Bot is running. No deployments yet."
    reports = [deployment_report_html(item) for item in deployments]
    text = "\n\n──────────────\n\n".join(reports)
    if len(text) > 3800:
        text = text[:3800] + "\n...[truncated]"
    return text
