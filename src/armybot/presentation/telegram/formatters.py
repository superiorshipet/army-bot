from html import escape

from armybot.domain.entities import Deployment, User


def user_line(user: User) -> str:
    username = f"@{user.username}" if user.username else "no username"
    phone = f" - {user.phone_number}" if user.phone_number else ""
    return f"{escape(user.full_name)} ({escape(username)}) - <code>{user.telegram_id}</code>{escape(phone)} - {escape(user.status.value)}"


def deployment_report(deployment: Deployment) -> str:
    return deployment_report_html(deployment)


def deployment_report_html(deployment: Deployment) -> str:
    raw_logs = deployment.logs[-8:]
    safe_lines = []
    for line in raw_logs:
        if len(line) > 200:
            line = line[:197] + "..."
        safe_lines.append(f"- {escape(line)}")
    logs = "\n".join(safe_lines)
    live = f"\nLive: {escape(deployment.live_url)}" if deployment.live_url else ""
    return (
        f"Deployment <code>{escape(deployment.status.value)}</code>\n"
        f"Branch: <code>{escape(deployment.branch)}</code>"
        f"{live}\n\n"
        f"Logs:\n{logs}"
    )


def deployment_error_html(error: Exception) -> str:
    return f"❌ Deployment failed with error:\n\n<code>{escape(str(error))}</code>"
