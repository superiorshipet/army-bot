from uuid import uuid4

from armybot.domain.entities import Deployment, User
from armybot.domain.enums import DeploymentStatus, UserRole, UserStatus
from armybot.presentation.telegram.formatters import (
    deployment_error_html,
    deployment_report,
    deployment_report_html,
    user_line,
)


def test_user_line_html_escaped():
    user = User(
        id=uuid4(),
        telegram_id=123456,
        full_name="<Admin_User & Co>",
        username="admin_user_name",
        phone_number="+1234567890",
        role=UserRole.User,
        status=UserStatus.Active,
    )
    line = user_line(user)
    assert "&lt;Admin_User &amp; Co&gt;" in line
    assert "<code>123456</code>" in line
    assert "<" not in line.replace("<code>", "").replace("</code>", "")


def test_deployment_report_html_safety():
    dep = Deployment(
        id=uuid4(),
        project_id=uuid4(),
        user_id=uuid4(),
        status=DeploymentStatus.Failed,
        branch="feature/test_underscore",
        logs=[
            "Starting build...",
            ".svelte-kit/output/server/_app/immutable/assets/_page.css",
            "<div>HTML tag & unescaped entities</div>",
            "x" * 300,
        ],
        live_url="https://example.com/test_app/",
    )
    report = deployment_report_html(dep)
    assert "<code>failed</code>" in report
    assert "<code>feature/test_underscore</code>" in report
    assert "&lt;div&gt;HTML tag &amp; unescaped entities&lt;/div&gt;" in report
    assert len(report) < 2000
    assert deployment_report(dep) == report


def test_deployment_error_html():
    err = ValueError("Invalid <syntax> & failure_code")
    err_text = deployment_error_html(err)
    assert "&lt;syntax&gt; &amp; failure_code" in err_text
