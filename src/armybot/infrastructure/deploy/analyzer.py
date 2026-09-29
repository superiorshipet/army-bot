import asyncio
import shutil
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from armybot.domain.entities import DeploymentPlan
from armybot.domain.enums import ProjectStack
from armybot.shared.settings import settings


class FilesystemRepoAnalyzer:
    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or settings.workspace_root

    async def analyze(self, repo_url: str, branch: str) -> DeploymentPlan:
        repo_path, actual_branch = await self._prepare_repo(repo_url, branch)
        project_name = self._project_name(repo_url, None)
        try:
            stack, build_steps, runtime, app_path, app_entry = self._detect_stack(repo_path)
            commit_sha = await self._commit_sha(repo_path)
        finally:
            shutil.rmtree(repo_path, ignore_errors=True)

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
            commit_sha=commit_sha,
        )

    async def _prepare_repo(self, repo_url: str, branch: str) -> tuple[Path, str]:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        parsed = urlparse(repo_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Send a valid HTTP(S) Git repository URL.")

        name = self._project_name(repo_url, None)
        target = self.workspace_root / f"{name}-{uuid4().hex[:12]}"

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
        if (
            "Could not find remote branch" in err_text
            or "Remote branch" in err_text
            or "not found in upstream" in err_text
        ):
            if target.exists():
                shutil.rmtree(target)
            fallback_cmd = ["git", "clone", "--depth", "1", repo_url, str(target)]
            fallback_proc = await asyncio.create_subprocess_exec(
                *fallback_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, _fallback_err = await fallback_proc.communicate()
            if fallback_proc.returncode == 0:
                rev_proc = await asyncio.create_subprocess_exec(
                    "git",
                    "rev-parse",
                    "--abbrev-ref",
                    "HEAD",
                    cwd=str(target),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                rev_out, _ = await rev_proc.communicate()
                detected_branch = rev_out.decode().strip() or "master"
                return target, detected_branch

        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError(err_text or "git clone failed")

    @staticmethod
    async def _commit_sha(repo_path: Path) -> str | None:
        process = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "HEAD",
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return None

        commit_sha = stdout.decode(errors="replace").strip()
        return commit_sha or None

    @staticmethod
    def _project_name(repo_url: str, path: Path | None) -> str:
        if path:
            return path.name.replace(" ", "-").lower()
        parsed = urlparse(repo_url)
        name = Path(parsed.path).name or "project"
        return name.removesuffix(".git").replace(" ", "-").lower()

    @staticmethod
    def _detect_stack(repo_path: Path) -> tuple[ProjectStack, list[str], str, str, str]:
        detected: list[tuple[int, ProjectStack, list[str], str, str, str]] = []
        for app_path in FilesystemRepoAnalyzer._candidate_app_paths(repo_path):
            stack, build_steps, runtime, app_entry = FilesystemRepoAnalyzer._detect_stack_at(
                app_path
            )
            if stack == ProjectStack.Unknown:
                continue
            relative_path = app_path.relative_to(repo_path).as_posix()
            detected.append(
                (
                    FilesystemRepoAnalyzer._candidate_priority(repo_path, app_path, stack),
                    stack,
                    build_steps,
                    runtime,
                    "." if relative_path == "." else relative_path,
                    app_entry,
                )
            )

        if not detected:
            return (
                ProjectStack.Unknown,
                ["manual inspection required"],
                "unknown",
                ".",
                "index.html",
            )

        _, stack, build_steps, runtime, app_path, app_entry = max(
            detected, key=lambda item: item[0]
        )
        return stack, build_steps, runtime, app_path, app_entry

    @staticmethod
    def _candidate_priority(repo_path: Path, app_path: Path, stack: ProjectStack) -> int:
        relative = app_path.relative_to(repo_path)
        if relative == Path("."):
            package_json = repo_path / "package.json"
            if package_json.exists() and '"workspaces"' in package_json.read_text(errors="ignore"):
                return 20
            return 100

        first_part = relative.parts[0].lower()
        score = 40 - len(relative.parts)
        if first_part in {"client", "frontend", "front", "web", "ui", "apps"}:
            score += 50
        if first_part in {"server", "backend", "api"} and stack in {
            ProjectStack.DotNet,
            ProjectStack.SpringBoot,
            ProjectStack.Laravel,
            ProjectStack.Node,
            ProjectStack.Python,
        }:
            score += 40
        return score

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
        for java_marker in (
            *repo_path.glob("*/pom.xml"),
            *repo_path.glob("*/*/pom.xml"),
            *repo_path.glob("*/build.gradle"),
            *repo_path.glob("*/*/build.gradle"),
            *repo_path.glob("*/build.gradle.kts"),
            *repo_path.glob("*/*/build.gradle.kts"),
        ):
            candidates.append(java_marker.parent)
        for html_file in repo_path.rglob("*.html"):
            if any(
                part in {".git", "node_modules", "vendor", "dist", "build"}
                for part in html_file.parts
            ):
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
            return (
                ProjectStack.Laravel,
                ["composer install --no-dev", "php artisan migrate --force"],
                "php-fpm",
                "index.php",
            )
        if FilesystemRepoAnalyzer._is_spring_boot(repo_path):
            return (
                ProjectStack.SpringBoot,
                ["mvn package -DskipTests or gradle bootJar"],
                "systemd",
                "",
            )
        if any(path.suffix == ".csproj" for path in repo_path.rglob("*.csproj")):
            return (
                ProjectStack.DotNet,
                ["dotnet restore", "dotnet publish -c Release"],
                "systemd",
                "",
            )
        if "package.json" in files:
            package = (repo_path / "package.json").read_text(errors="ignore")
            if "vite" in package:
                return (
                    ProjectStack.Vite,
                    ["npm install", "npm run build"],
                    "nginx-static",
                    "index.html",
                )
            if "react-scripts" in package:
                return (
                    ProjectStack.React,
                    ["npm install", "npm run build"],
                    "nginx-static",
                    "index.html",
                )
            return ProjectStack.Node, ["npm install", "npm run build"], "node", ""
        if "requirements.txt" in files or "pyproject.toml" in files:
            return ProjectStack.Python, ["pip install -r requirements.txt"], "systemd", ""
        html_entry = FilesystemRepoAnalyzer._detect_html_entry(repo_path)
        if html_entry:
            return ProjectStack.Static, ["copy static files"], "nginx-static", html_entry
        return ProjectStack.Unknown, ["manual inspection required"], "unknown", "index.html"

    @staticmethod
    def _is_spring_boot(repo_path: Path) -> bool:
        markers = [
            repo_path / "pom.xml",
            repo_path / "build.gradle",
            repo_path / "build.gradle.kts",
        ]
        content = "\n".join(
            marker.read_text(errors="ignore").lower() for marker in markers if marker.exists()
        )
        if not content:
            return False
        return (
            "spring-boot" in content
            or "org.springframework.boot" in content
            or "springframework.boot" in content
        )

    @staticmethod
    def _detect_html_entry(repo_path: Path) -> str | None:
        html_files = sorted(
            (
                path
                for path in repo_path.iterdir()
                if path.is_file() and path.suffix.lower() in {".html", ".htm"}
            ),
            key=lambda path: path.name.lower(),
        )
        if not html_files:
            return None

        for html_file in html_files:
            if html_file.name.lower() == "index.html":
                return html_file.name

        return max(html_files, key=lambda path: path.stat().st_size).name
