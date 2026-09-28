from uuid import UUID

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from armybot.domain.entities import Project, User
from armybot.domain.enums import CredentialProvider

REQUIRED_CREDENTIALS = (
    CredentialProvider.GitHub,
    CredentialProvider.Server,
    CredentialProvider.Cloudflare,
    CredentialProvider.Railway,
    CredentialProvider.Firebase,
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


def main_menu_keyboard(
    is_admin: bool = False, has_server_cred: bool = False
) -> InlineKeyboardMarkup:
    top_row = [
        InlineKeyboardButton(text="Setup credentials", callback_data="menu:setup"),
        InlineKeyboardButton(text="Deploy project", callback_data="menu:deploy"),
    ]
    rows = [
        top_row,
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
            [
                InlineKeyboardButton(text="Add user", callback_data="admin:add_user"),
                InlineKeyboardButton(text="All users", callback_data="admin:users"),
            ],
            [
                InlineKeyboardButton(text="Pending users", callback_data="admin:pending"),
                InlineKeyboardButton(text="Rejected users", callback_data="admin:rejected"),
            ],
            [InlineKeyboardButton(text="Back to menu", callback_data="menu:home")],
        ]
    )


def pending_users_keyboard(users: list[User]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for user in users:
        label = _user_button_label(user)
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:user:{user.id}")])
        rows.append(
            [
                InlineKeyboardButton(
                    text="Approve", callback_data=f"access:approve:{user.telegram_id}"
                ),
                InlineKeyboardButton(
                    text="Reject", callback_data=f"access:reject:{user.telegram_id}"
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text="Back to Admin panel", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_users_keyboard(users: list[User]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{_user_button_label(user)} - {user.status.value}",
                callback_data=f"admin:user:{user.id}",
            )
        ]
        for user in users
    ]
    rows.append([InlineKeyboardButton(text="Back to Admin panel", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_projects_keyboard(projects: list[Project]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{project.name} - {project.deployment_target.value}",
                callback_data=f"admin:project:{project.id}",
            )
        ]
        for project in projects
    ]
    rows.extend(
        [
            [InlineKeyboardButton(text="Back to All users", callback_data="admin:users")],
            [InlineKeyboardButton(text="Admin panel", callback_data="menu:admin")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_project_keyboard(project: Project) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Deploy now", callback_data=f"admin:project:deploy:{project.id}"
            ),
            InlineKeyboardButton(
                text="View logs", callback_data=f"admin:project:logs:{project.id}"
            ),
        ],
        [
            InlineKeyboardButton(
                text="Delete project", callback_data=f"project:del_prompt:{project.id}"
            )
        ],
        [InlineKeyboardButton(text="Back to user", callback_data=f"admin:user:{project.user_id}")],
    ]
    if project.live_url:
        rows.insert(1, [InlineKeyboardButton(text="Open site", url=project.live_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _user_button_label(user: User) -> str:
    label = user.full_name or user.username or str(user.telegram_id)
    return label if len(label) <= 28 else label[:26] + ".."


def rejected_users_keyboard(users: list[User]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for user in users:
        label = user.full_name or user.username or str(user.telegram_id)
        if len(label) > 16:
            label = label[:14] + ".."
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔄 Un-reject {label}",
                    callback_data=f"admin:unreject:{user.telegram_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Back to Admin panel", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def setup_keyboard(saved_providers: set[CredentialProvider]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    missing = [provider for provider in REQUIRED_CREDENTIALS if provider not in saved_providers]
    for provider in missing:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Add {provider_label(provider)}",
                    callback_data=f"cred:add:{provider.value}",
                )
            ]
        )

    edit_buttons = [
        InlineKeyboardButton(
            text=f"Edit {provider_label(provider)}", callback_data=f"cred:edit:{provider.value}"
        )
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


def deployment_target_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="My Server", callback_data="deploy:target:server"),
                InlineKeyboardButton(text="Railway", callback_data="deploy:target:railway"),
            ],
            [InlineKeyboardButton(text="Firebase Hosting", callback_data="deploy:target:firebase")],
            [InlineKeyboardButton(text="Back to menu", callback_data="menu:home")],
        ]
    )


def provider_label(provider: CredentialProvider) -> str:
    labels = {
        CredentialProvider.GitHub: "GitHub",
        CredentialProvider.AWS: "AWS",
        CredentialProvider.Cloudflare: "Cloudflare",
        CredentialProvider.Server: "Server",
        CredentialProvider.Railway: "Railway",
        CredentialProvider.Firebase: "Firebase",
    }
    return labels[provider]


def projects_list_keyboard(projects: list[Project]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for project in projects:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📦 {project.name}", callback_data=f"project:view:{project.id}"
                ),
                InlineKeyboardButton(
                    text="🗑️ Delete", callback_data=f"project:del_prompt:{project.id}"
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text="Back to menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def project_details_keyboard(project: Project) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    auto_action = "off" if project.auto_deploy_enabled else "on"
    auto_label = "Disable Auto Deploy" if project.auto_deploy_enabled else "Enable Auto Deploy"
    rows.append(
        [
            InlineKeyboardButton(text="Deploy now", callback_data=f"project:deploy:{project.id}"),
            InlineKeyboardButton(
                text=auto_label,
                callback_data=f"project:auto:{project.id}:{auto_action}",
            ),
        ]
    )
    actions: list[InlineKeyboardButton] = []
    if project.live_url:
        actions.append(InlineKeyboardButton(text="🌐 Open Site", url=project.live_url))
    actions.append(
        InlineKeyboardButton(
            text="🗑️ Delete Project", callback_data=f"project:del_prompt:{project.id}"
        )
    )
    rows.append(actions)
    rows.append([InlineKeyboardButton(text="⬅️ Back to Projects", callback_data="menu:projects")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def project_delete_confirm_keyboard(project_id: UUID | str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Yes, Delete Project", callback_data=f"project:del_confirm:{project_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Cancel", callback_data=f"project:del_cancel:{project_id}"
                ),
            ]
        ]
    )


def cancel_deployment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Cancel deployment",
                    callback_data="deployment:cancel",
                )
            ]
        ]
    )
