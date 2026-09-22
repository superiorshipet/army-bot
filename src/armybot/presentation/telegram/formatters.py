from armybot.domain.entities import Deployment, User


def user_line(user: User) -> str:
    username = f"@{user.username}" if user.username else "no username"
    return f"{user.full_name} ({username}) - `{user.telegram_id}` - {user.status.value}"


def deployment_report(deployment: Deployment) -> str:
    logs = "\n".join(f"- {line}" for line in deployment.logs[-8:])
    live = f"\nLive: {deployment.live_url}" if deployment.live_url else ""
    return (
        f"Deployment `{deployment.status.value}`\n"
        f"Branch: `{deployment.branch}`"
        f"{live}\n\n"
        f"Logs:\n{logs}"
    )
