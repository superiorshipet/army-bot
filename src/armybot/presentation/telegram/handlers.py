from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from armybot.domain.enums import CredentialProvider
from armybot.infrastructure.telegram.container import container_scope
from armybot.presentation.telegram.formatters import deployment_report, user_line
from armybot.presentation.telegram.keyboards import access_decision_keyboard
from armybot.shared.settings import settings

router = Router()


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
            "اهلا. انت متفعل وتقدر تستخدم البوت.\n"
            "اكتب /setup عشان تضيف الكريدنشالز، او /deploy <repo>."
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


@router.message(Command("setup"))
async def setup(message: Message) -> None:
    await message.answer(
        "Setup commands:\n"
        "/set_github <token>\n"
        "/set_cloudflare <token> [account_id] [zone_id]\n"
        "/set_server <host> <user> <ssh_key_path> <base_path> [public_base_url]\n"
        "/deploy <repo_url_or_path> [branch]"
    )


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
    await message.answer("GitHub credentials saved encrypted.")


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
    await message.answer("Cloudflare credentials saved encrypted.")


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
    await message.answer("Server credentials saved encrypted.")


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
    notice = await message.answer("Deployment started. I will report back here.")
    async with container_scope() as c:
        user = await c.access.require_active(message.from_user.id)
        deployment = await c.deploy.deploy(user, repo_url, branch)
    await notice.edit_text(deployment_report(deployment), parse_mode="Markdown")


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


def _command_args(message: Message) -> str:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""
