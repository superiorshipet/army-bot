import asyncio
from pathlib import Path

from armybot.domain.entities import DeploymentPlan
from armybot.shared.settings import settings


class SafeDeploymentExecutor:
    """First execution adapter.

    It deliberately starts conservative. In dry_run it only reports what would happen.
    In local mode it can run build commands locally later; remote SSH/AWS adapters can
    implement the same DeploymentExecutor port without changing application code.
    """

    async def deploy(self, plan: DeploymentPlan, credentials: dict[str, dict]) -> tuple[str | None, list[str]]:
        logs = [
            f"Project: {plan.project_name}",
            f"Branch: {plan.branch}",
            f"Stack: {plan.stack.value}",
            f"Build steps: {', '.join(plan.build_steps)}",
        ]

        if settings.execution_mode == "dry_run":
            logs.append("Dry run mode: no server changes were made.")
            return self._guess_url(plan, credentials), logs

        if settings.execution_mode == "local":
            logs.extend(await self._run_local_steps(plan))
            return self._guess_url(plan, credentials), logs

        raise ValueError(f"Unsupported execution mode: {settings.execution_mode}")

    async def _run_local_steps(self, plan: DeploymentPlan) -> list[str]:
        logs: list[str] = []
        workdir = settings.workspace_root / plan.project_name
        for step in plan.build_steps:
            command = step.split()
            logs.append(f"Running: {step}")
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workdir if workdir.exists() else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await process.communicate()
            logs.append(output.decode(errors="replace")[-2000:])
            if process.returncode != 0:
                raise RuntimeError(f"Command failed: {step}")
        return logs

    @staticmethod
    def _guess_url(plan: DeploymentPlan, credentials: dict[str, dict]) -> str | None:
        server = credentials.get("server", {})
        base_url = server.get("public_base_url") or settings.public_base_url
        if not base_url:
            return None
        return f"{base_url.rstrip('/')}/{plan.project_name}/"
