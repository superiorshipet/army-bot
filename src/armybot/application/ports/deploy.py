from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from armybot.domain.entities import DeploymentPlan

LogCallback = Callable[[str], Coroutine[Any, Any, None]] | None


class RepoAnalyzer(Protocol):
    async def analyze(self, repo_url: str, branch: str) -> DeploymentPlan: ...


class DeploymentExecutor(Protocol):
    async def deploy(
        self,
        plan: DeploymentPlan,
        credentials: dict[str, dict],
        on_log: LogCallback = None,
    ) -> tuple[str | None, list[str]]: ...
