from typing import Protocol

from armybot.domain.entities import DeploymentPlan


class RepoAnalyzer(Protocol):
    async def analyze(self, repo_url: str, branch: str) -> DeploymentPlan: ...


class DeploymentExecutor(Protocol):
    async def deploy(self, plan: DeploymentPlan, credentials: dict[str, dict]) -> tuple[str | None, list[str]]: ...
