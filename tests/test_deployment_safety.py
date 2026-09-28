import asyncio
from uuid import uuid4

import pytest

from armybot.infrastructure.deploy.analyzer import FilesystemRepoAnalyzer
from armybot.infrastructure.deploy.coordinator import (
    DeploymentAlreadyRunningError,
    DeploymentCoordinator,
)
from armybot.shared.redaction import redact_lines, redact_text


@pytest.mark.asyncio
async def test_coordinator_rejects_duplicate_target_and_can_cancel() -> None:
    coordinator = DeploymentCoordinator()
    user_id = uuid4()
    started = asyncio.Event()

    async def slow_operation() -> str:
        started.set()
        await asyncio.sleep(30)
        return "done"

    task = asyncio.create_task(coordinator.run("server:project", user_id, slow_operation()))
    await started.wait()

    with pytest.raises(DeploymentAlreadyRunningError):
        await coordinator.run("server:project", uuid4(), asyncio.sleep(0))

    assert await coordinator.cancel_for_user(user_id) is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await coordinator.cancel_for_user(user_id) is False


def test_redaction_removes_nested_provider_secrets() -> None:
    credentials = {
        "github": {"token": "github-secret-token"},
        "server": {"host": "example.com", "password": "server-secret-password"},
    }

    value = "github-secret-token server-secret-password example.com"
    assert redact_text(value, credentials) == "[REDACTED] [REDACTED] example.com"
    assert redact_lines([value], credentials) == ["[REDACTED] [REDACTED] example.com"]


@pytest.mark.asyncio
async def test_analyzer_rejects_local_paths(tmp_path) -> None:
    analyzer = FilesystemRepoAnalyzer(tmp_path)

    with pytest.raises(ValueError, match="HTTP"):
        await analyzer.analyze("/etc", "main")


@pytest.mark.asyncio
async def test_coordinator_limits_user_to_one_active_deployment() -> None:
    coordinator = DeploymentCoordinator()
    user_id = uuid4()
    started = asyncio.Event()

    async def slow_operation() -> None:
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(coordinator.run("server:first", user_id, slow_operation()))
    await started.wait()

    with pytest.raises(DeploymentAlreadyRunningError, match="already have"):
        await coordinator.run("server:second", user_id, asyncio.sleep(0))

    assert await coordinator.cancel_for_user(user_id) is True
    with pytest.raises(asyncio.CancelledError):
        await task


def test_analyzer_prefers_frontend_application_in_workspace_monorepo(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"workspaces": ["apps/*"]}')
    frontend = tmp_path / "apps" / "web"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text(
        '{"scripts": {"build": "vite build"}, "devDependencies": {"vite": "1"}}'
    )

    stack, _, _, app_path, _ = FilesystemRepoAnalyzer._detect_stack(tmp_path)

    assert stack.value == "vite"
    assert app_path == "apps/web"
