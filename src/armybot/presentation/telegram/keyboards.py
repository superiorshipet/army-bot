from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from armybot.domain.entities import Project
from armybot.domain.enums import CredentialProvider


REQUIRED_CREDENTIALS = (
    CredentialProvider.GitHub,
    CredentialProvider.Server,
    CredentialProvider.Cloudflare,
)


def access_decision_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Approve", callback_data=f"access:approve:{telegram_id}"),
                InlineKeyboardButton(text="Reject", callback_data=f"access:reject:{telegram_id}"),
            ]
        ]
    )


def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Setup credentials", callback_data="menu:setup"),
            InlineKeyboardButton(text="Deploy project", callback_data="menu:deploy"),
        ],
        [
            InlineKeyboardButton(text="Projects", callback_data="menu:projects"),
            InlineKeyboardButton(text="Status", callback_data="menu:status"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="Admin panel", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def start_reply_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Start"), KeyboardButton(text="Menu")],
        [KeyboardButton(text="Setup credentials"), KeyboardButton(text="Deploy project")],
        [KeyboardButton(text="Projects"), KeyboardButton(text="Status")],
        [KeyboardButton(text="Share phone", request_contact=True)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="Admin panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Add user", callback_data="admin:add_user")],
            [InlineKeyboardButton(text="All users", callback_data="admin:users")],
            [InlineKeyboardButton(text="Pending users", callback_data="admin:pending")],
            [InlineKeyboardButton(text="Back to menu", callback_data="menu:home")],
        ]
    )


def setup_keyboard(saved_providers: set[CredentialProvider]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    missing = [provider for provider in REQUIRED_CREDENTIALS if provider not in saved_providers]
    for provider in missing:
        rows.append([InlineKeyboardButton(text=f"Add {provider_label(provider)}", callback_data=f"cred:add:{provider.value}")])

    edit_buttons = [
        InlineKeyboardButton(text=f"Edit {provider_label(provider)}", callback_data=f"cred:edit:{provider.value}")
        for provider in sorted(saved_providers, key=lambda item: item.value)
    ]
    for button in edit_buttons:
        rows.append([button])

    rows.append([InlineKeyboardButton(text="Back to menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deploy_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Cancel", callback_data="flow:cancel")],
            [InlineKeyboardButton(text="Back to menu", callback_data="menu:home")],
        ]
    )


def provider_label(provider: CredentialProvider) -> str:
    labels = {
        CredentialProvider.GitHub: "GitHub",
        CredentialProvider.AWS: "AWS",
        CredentialProvider.Cloudflare: "Cloudflare",
        CredentialProvider.Server: "Server",
    }
    return labels[provider]


def projects_list_keyboard(projects: list[Project]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for project in projects:
        rows.append([
            InlineKeyboardButton(text=f"📦 {project.name}", callback_data=f"project:view:{project.id}"),
            InlineKeyboardButton(text="🗑️ Delete", callback_data=f"project:del_prompt:{project.id}"),
        ])
    rows.append([InlineKeyboardButton(text="Back to menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def project_details_keyboard(project: Project) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    actions: list[InlineKeyboardButton] = []
    if project.live_url:
        actions.append(InlineKeyboardButton(text="🌐 Open Site", url=project.live_url))
    actions.append(InlineKeyboardButton(text="🗑️ Delete Project", callback_data=f"project:del_prompt:{project.id}"))
    rows.append(actions)
    rows.append([InlineKeyboardButton(text="⬅️ Back to Projects", callback_data="menu:projects")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def project_delete_confirm_keyboard(project_id: UUID | str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Yes, Delete Project", callback_data=f"project:del_confirm:{project_id}"),
                InlineKeyboardButton(text="❌ Cancel", callback_data=f"project:del_cancel:{project_id}"),
            ]
        ]
    )

