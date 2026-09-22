import asyncio
import shutil
from pathlib import Path
from urllib.parse import urlparse

from armybot.domain.entities import DeploymentPlan
from armybot.domain.enums import ProjectStack
from armybot.shared.settings import settings


class FilesystemRepoAnalyzer:
    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or settings.workspace_root

    async def analyze(self, repo_url: str, branch: str) -> DeploymentPlan:
        repo_path = await self._prepare_repo(repo_url, branch)
        project_name = self._project_name(repo_url, repo_path)
        stack, build_steps, runtime = self._detect_stack(repo_path)

        return DeploymentPlan(
            project_name=project_name,
            repo_url=repo_url,
            branch=branch,
            stack=stack,
            build_steps=build_steps,
            runtime=runtime,
            notes=[f"Detected stack: {stack.value}", f"Runtime: {runtime}"],
        )

    async def _prepare_repo(self, repo_url: str, branch: str) -> Path:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        if repo_url.startswith("/") or repo_url.startswith("."):
            path = Path(repo_url).expanduser().resolve()
            if not path.exists():
                raise FileNotFoundError(f"Repository path not found: {path}")
            return path

        name = self._project_name(repo_url, None)
        target = self.workspace_root / name
        if target.exists():
            shutil.rmtree(target)

        command = ["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(target)]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode().strip() or "git clone failed")
        return target

    @staticmethod
    def _project_name(repo_url: str, path: Path | None) -> str:
        if path:
            return path.name.replace(" ", "-").lower()
        parsed = urlparse(repo_url)
        name = Path(parsed.path).name or "project"
        return name.removesuffix(".git").replace(" ", "-").lower()

    @staticmethod
    def _detect_stack(repo_path: Path) -> tuple[ProjectStack, list[str], str]:
        files = {path.name for path in repo_path.iterdir() if path.is_file()}
        if "composer.json" in files and "artisan" in files:
            return ProjectStack.Laravel, ["composer install --no-dev", "php artisan migrate --force"], "php-fpm"
        if any(path.suffix == ".csproj" for path in repo_path.rglob("*.csproj")):
            return ProjectStack.DotNet, ["dotnet restore", "dotnet publish -c Release"], "systemd"
        if "package.json" in files:
            package = (repo_path / "package.json").read_text(errors="ignore")
            if "vite" in package:
                return ProjectStack.Vite, ["npm install", "npm run build"], "nginx-static"
            return ProjectStack.Node, ["npm install", "npm run build"], "node"
        if "requirements.txt" in files or "pyproject.toml" in files:
            return ProjectStack.Python, ["pip install -r requirements.txt"], "systemd"
        if "index.html" in files:
            return ProjectStack.Static, ["copy static files"], "nginx-static"
        return ProjectStack.Unknown, ["manual inspection required"], "unknown"
