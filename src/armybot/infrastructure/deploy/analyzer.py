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
        repo_path, actual_branch = await self._prepare_repo(repo_url, branch)
        project_name = self._project_name(repo_url, repo_path)
        stack, build_steps, runtime, app_path, app_entry = self._detect_stack(repo_path)

        return DeploymentPlan(
            project_name=project_name,
            repo_url=repo_url,
            branch=actual_branch,
            stack=stack,
            build_steps=build_steps,
            runtime=runtime,
            notes=[
                f"Detected stack: {stack.value}",
                f"Runtime: {runtime}",
                f"App path: {app_path}",
                f"App entry: {app_entry}",
            ],
            app_path=app_path,
            app_entry=app_entry,
        )

    async def _prepare_repo(self, repo_url: str, branch: str) -> tuple[Path, str]:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        if repo_url.startswith("/") or repo_url.startswith("."):
            path = Path(repo_url).expanduser().resolve()
            if not path.exists():
                raise FileNotFoundError(f"Repository path not found: {path}")
            return path, branch

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
        if process.returncode == 0:
            return target, branch

        err_text = stderr.decode(errors="replace").strip()
        if "Could not find remote branch" in err_text or "Remote branch" in err_text or "not found in upstream" in err_text:
            if target.exists():
                shutil.rmtree(target)
            fallback_cmd = ["git", "clone", "--depth", "1", repo_url, str(target)]
            fallback_proc = await asyncio.create_subprocess_exec(
                *fallback_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, fallback_err = await fallback_proc.communicate()
            if fallback_proc.returncode == 0:
                rev_proc = await asyncio.create_subprocess_exec(
                    "git", "rev-parse", "--abbrev-ref", "HEAD",
                    cwd=str(target),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                rev_out, _ = await rev_proc.communicate()
                detected_branch = rev_out.decode().strip() or "master"
                return target, detected_branch

        raise RuntimeError(err_text or "git clone failed")

    @staticmethod
    def _project_name(repo_url: str, path: Path | None) -> str:
        if path:
            return path.name.replace(" ", "-").lower()
        parsed = urlparse(repo_url)
        name = Path(parsed.path).name or "project"
        return name.removesuffix(".git").replace(" ", "-").lower()

    @staticmethod
    def _detect_stack(repo_path: Path) -> tuple[ProjectStack, list[str], str, str, str]:
        for app_path in FilesystemRepoAnalyzer._candidate_app_paths(repo_path):
            stack, build_steps, runtime, app_entry = FilesystemRepoAnalyzer._detect_stack_at(app_path)
            if stack != ProjectStack.Unknown:
                relative_path = app_path.relative_to(repo_path).as_posix()
                return (
                    stack,
                    build_steps,
                    runtime,
                    "." if relative_path == "." else relative_path,
                    app_entry,
                )

        return ProjectStack.Unknown, ["manual inspection required"], "unknown", ".", "index.html"

    @staticmethod
    def _candidate_app_paths(repo_path: Path) -> list[Path]:
        preferred_names = (
            ".",
            "client",
            "frontend",
            "front",
            "web",
            "app",
            "apps/web",
            "packages/web",
        )
        candidates = [repo_path / name for name in preferred_names]

        for package_json in repo_path.glob("*/*/package.json"):
            candidates.append(package_json.parent)
        for package_json in repo_path.glob("*/package.json"):
            candidates.append(package_json.parent)
        for html_file in repo_path.rglob("*.html"):
            if any(part in {".git", "node_modules", "vendor", "dist", "build"} for part in html_file.parts):
                continue
            candidates.append(html_file.parent)

        unique: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            if candidate.exists() and candidate.is_dir() and candidate not in seen:
                seen.add(candidate)
                unique.append(candidate)
        return unique

    @staticmethod
    def _detect_stack_at(repo_path: Path) -> tuple[ProjectStack, list[str], str, str]:
        files = {path.name for path in repo_path.iterdir() if path.is_file()}
        if "composer.json" in files and "artisan" in files:
            return ProjectStack.Laravel, ["composer install --no-dev", "php artisan migrate --force"], "php-fpm", "index.php"
        if any(path.suffix == ".csproj" for path in repo_path.rglob("*.csproj")):
            return ProjectStack.DotNet, ["dotnet restore", "dotnet publish -c Release"], "systemd", ""
        if "package.json" in files:
            package = (repo_path / "package.json").read_text(errors="ignore")
            if "vite" in package:
                return ProjectStack.Vite, ["npm install", "npm run build"], "nginx-static", "index.html"
            if "react-scripts" in package:
                return ProjectStack.React, ["npm install", "npm run build"], "nginx-static", "index.html"
            return ProjectStack.Node, ["npm install", "npm run build"], "node", ""
        if "requirements.txt" in files or "pyproject.toml" in files:
            return ProjectStack.Python, ["pip install -r requirements.txt"], "systemd", ""
        html_entry = FilesystemRepoAnalyzer._detect_html_entry(repo_path)
        if html_entry:
            return ProjectStack.Static, ["copy static files"], "nginx-static", html_entry
        return ProjectStack.Unknown, ["manual inspection required"], "unknown", "index.html"

    @staticmethod
    def _detect_html_entry(repo_path: Path) -> str | None:
        html_files = sorted(
            (path for path in repo_path.iterdir() if path.is_file() and path.suffix.lower() in {".html", ".htm"}),
            key=lambda path: path.name.lower(),
        )
        if not html_files:
            return None

        for html_file in html_files:
            if html_file.name.lower() == "index.html":
                return html_file.name

        return max(html_files, key=lambda path: path.stat().st_size).name
