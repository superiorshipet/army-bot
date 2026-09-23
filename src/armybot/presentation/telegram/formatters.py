from html import escape

from armybot.domain.entities import Deployment, User


def user_line(user: User) -> str:
    username = f"@{user.username}" if user.username else "no username"
    phone = f" - {user.phone_number}" if user.phone_number else ""
    return f"{user.full_name} ({username}) - `{user.telegram_id}`{phone} - {user.status.value}"


def deployment_report(deployment: Deployment) -> str:
    logs = "\n".join(f"- {line}" for line in deployment.logs[-8:])
    live = f"\nLive: {deployment.live_url}" if deployment.live_url else ""
    return (
        f"Deployment `{deployment.status.value}`\n"
        f"Branch: `{deployment.branch}`"
        f"{live}\n\n"
        f"Logs:\n{logs}"
    )


def deployment_report_html(deployment: Deployment) -> str:
    logs = "\n".join(f"- {escape(line)}" for line in deployment.logs[-8:])
    live = f"\nLive: {escape(deployment.live_url)}" if deployment.live_url else ""
    return (
        f"Deployment <code>{escape(deployment.status.value)}</code>\n"
        f"Branch: <code>{escape(deployment.branch)}</code>"
        f"{live}\n\n"
        f"Logs:\n{logs}"
    )


def deployment_error_html(error: Exception) -> str:
    return f"❌ Deployment failed with error:\n\n<code>{escape(str(error))}</code>"
