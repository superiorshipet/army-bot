from __future__ import annotations

from urllib.parse import quote, urlparse

import httpx


class GitHubBranchClient:
    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    async def latest_sha(self, repo_url: str, branch: str, token: str = "") -> str:
        owner, repository = self._repository_parts(repo_url)
        url = (
            f"https://api.github.com/repos/{quote(owner)}/{quote(repository)}"
            f"/commits/{quote(branch, safe='')}"
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        sha = response.json().get("sha")
        if not isinstance(sha, str) or not sha:
            raise RuntimeError("GitHub did not return a commit SHA.")
        return sha

    @staticmethod
    def _repository_parts(repo_url: str) -> tuple[str, str]:
        parsed = urlparse(repo_url)
        if parsed.hostname not in {"github.com", "www.github.com"}:
            raise ValueError("Auto deploy currently supports github.com repositories only.")
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) != 2:
            raise ValueError("Send a GitHub repository URL, not a file or folder URL.")
        return parts[0], parts[1].removesuffix(".git")
