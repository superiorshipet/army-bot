from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot

from armybot.domain.enums import DeploymentStatus
from armybot.infrastructure.deploy.github_monitor import GitHubBranchClient
from armybot.infrastructure.telegram.container import container_scope
from armybot.presentation.telegram.formatters import deployment_report_html
from armybot.shared.settings import settings

logger = structlog.get_logger()


class AutoDeployWorker:
    def __init__(self, bot: Bot, github: GitHubBranchClient | None = None) -> None:
        self.bot = bot
        self.github = github or GitHubBranchClient()

    async def run(self) -> None:
        if not settings.auto_deploy_enabled:
            logger.info("auto_deploy.disabled")
            return
        logger.info("auto_deploy.started", interval_seconds=settings.auto_deploy_interval_seconds)
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("auto_deploy.cycle_failed")
            await asyncio.sleep(max(settings.auto_deploy_interval_seconds, 15))

    async def check_once(self) -> None:
        async with container_scope() as container:
            projects = await container.projects.list_auto_deploy()
        for snapshot in projects:
            try:
                await self._check_project(snapshot.id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("auto_deploy.project_failed", project_id=str(snapshot.id))

    async def _check_project(self, project_id) -> None:
        async with container_scope() as container:
            project = await container.projects.get_by_id(project_id)
            if not project or not project.auto_deploy_enabled:
                return
            user = await container.users.get_by_id(project.user_id)
            if not user or not user.is_active:
                return
            credentials = await container.credentials.load_all(user)
            github_token = str(credentials.get("github", {}).get("token", ""))
            sha = await self.github.latest_sha(project.repo_url, project.branch, github_token)
            if sha == project.last_triggered_sha:
                return
            project.last_triggered_sha = sha
            await container.projects.update(project)
            target = project.deployment_target
            repo_url = project.repo_url
            branch = project.branch
            telegram_id = user.telegram_id

        await self.bot.send_message(
            telegram_id,
            f"New push detected on {branch}. Deploying {project.name} to {target.value}...",
        )
        async with container_scope() as container:
            current_user = await container.users.get_by_id(project.user_id)
            if not current_user:
                return
            deployment = await container.deploy.deploy(
                current_user,
                repo_url,
                branch,
                target=target,
                triggered_by="github_push",
            )
        await self.bot.send_message(
            telegram_id,
            deployment_report_html(deployment),
            parse_mode="HTML",
        )
        if deployment.status == DeploymentStatus.Failed:
            logger.warning("auto_deploy.deployment_failed", project_id=str(project_id))
