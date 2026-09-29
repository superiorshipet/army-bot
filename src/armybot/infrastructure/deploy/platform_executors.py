from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, ClassVar
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx

from armybot.application.ports.deploy import DeploymentOutput, LogCallback
from armybot.domain.entities import DeploymentPlan
from armybot.domain.enums import ProjectStack
from armybot.shared.settings import settings


class PlatformCommandError(RuntimeError):
    pass


class _PlatformExecutor:
    def __init__(self, command_timeout: int | None = None) -> None:
        self.command_timeout = command_timeout or settings.platform_command_timeout
        self.workspace_root = settings.workspace_root

    async def _clone(self, plan: DeploymentPlan) -> Path:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        target = self.workspace_root / f"platform-{uuid4().hex[:12]}"
        await self._run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                plan.branch,
                plan.repo_url,
                str(target),
            ],
            cwd=self.workspace_root,
        )
        return target

    async def _run(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        inherit_env: bool = True,
    ) -> str:
        executable = command[0]
        if shutil.which(executable) is None:
            raise PlatformCommandError(
                f"{executable} CLI is not installed on the Army Deploy server."
            )
        if inherit_env:
            process_env = dict(os.environ)
        else:
            allowed_keys = ("HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "SHELL", "TMPDIR", "USER")
            process_env = {key: os.environ[key] for key in allowed_keys if key in os.environ}
        process_env.update(env or {})
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=process_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.command_timeout
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise PlatformCommandError(
                f"{executable} timed out after {self.command_timeout} seconds."
            ) from exc
        output = stdout.decode(errors="replace").strip()
        error = stderr.decode(errors="replace").strip()
        if process.returncode != 0:
            raise PlatformCommandError(error or output or f"{executable} failed.")
        return output or error

    @staticmethod
    async def _log(callback: LogCallback, text: str) -> None:
        if callback:
            await callback(text)


class _StaticHostingExecutor(_PlatformExecutor):
    supported_stacks: ClassVar[set[ProjectStack]] = {
        ProjectStack.Vite,
        ProjectStack.React,
        ProjectStack.Static,
    }

    async def _prepare_public_dir(
        self,
        plan: DeploymentPlan,
        app_path: Path,
        env: dict[str, str],
        on_log: LogCallback,
        base_path: str | None = None,
    ) -> Path:
        if plan.stack == ProjectStack.Static:
            entry = app_path / plan.app_entry
            if not entry.exists():
                raise PlatformCommandError(f"Static entry file '{plan.app_entry}' was not found.")
            if entry.name != "index.html":
                shutil.copy2(entry, app_path / "index.html")
            return app_path

        await self._log(on_log, "Installing frontend dependencies...")
        install_command = (
            ["npm", "ci"] if (app_path / "package-lock.json").exists() else ["npm", "install"]
        )
        await self._run(install_command, app_path, env, inherit_env=False)
        await self._log(on_log, "Building the frontend...")
        build_command = ["npm", "run", "build"]
        build_env = dict(env)
        if base_path and plan.stack == ProjectStack.Vite:
            build_command.extend(["--", "--base", base_path])
        elif base_path and plan.stack == ProjectStack.React:
            build_env["PUBLIC_URL"] = base_path.rstrip("/") or "/"
        await self._run(build_command, app_path, build_env, inherit_env=False)
        for name in ("dist", "build"):
            output = app_path / name
            if output.is_dir():
                return output
        raise PlatformCommandError("The frontend build finished without a dist or build folder.")


class RailwayDeployExecutor(_PlatformExecutor):
    async def deploy(
        self,
        plan: DeploymentPlan,
        credentials: dict[str, dict],
        on_log: LogCallback = None,
    ) -> DeploymentOutput:
        railway = credentials.get("railway", {})
        token = str(railway.get("token", "")).strip()
        if not token:
            raise ValueError("Add your Railway account token before deploying to Railway.")

        repo_path = await self._clone(plan)
        app_path = (repo_path / plan.app_path).resolve()
        env = {"RAILWAY_API_TOKEN": token, "RAILWAY_TOKEN": token}
        config = dict(plan.target_config)
        logs: list[str] = []
        try:
            project_id = str(config.get("project_id", "")).strip()
            if not project_id:
                await self._log(on_log, "Creating the Railway project...")
                command = ["railway", "init", "--name", plan.deployment_name, "--json"]
                workspace = str(railway.get("workspace", "")).strip()
                if workspace:
                    command.extend(["--workspace", workspace])
                response = await self._run(command, app_path, env)
                project_id = _find_json_value(response, "projectId", "project_id", "id")
                if not project_id:
                    raise PlatformCommandError(
                        "Railway created the project but did not return its project ID."
                    )
                logs.append("Railway project created.")

            await self._log(on_log, "Uploading the application to Railway...")
            await self._run(
                [
                    "railway",
                    "up",
                    "--ci",
                    "--project",
                    project_id,
                    "--environment",
                    "production",
                ],
                app_path,
                env,
            )
            logs.append("Railway deployment completed.")

            await self._log(on_log, "Preparing the public Railway URL...")
            domain_output = await self._railway_domain(project_id, app_path, env)
            live_url = _find_url(domain_output, ("railway.app", "up.railway.app"))
            if not live_url:
                raise PlatformCommandError(
                    "Deployment succeeded, but Railway did not return a public domain."
                )
            config.update({"project_id": project_id, "environment": "production"})
            return live_url, logs, config
        finally:
            shutil.rmtree(repo_path, ignore_errors=True)

    async def _railway_domain(
        self,
        project_id: str,
        app_path: Path,
        env: dict[str, str],
    ) -> str:
        command = [
            "railway",
            "domain",
            "--project",
            project_id,
            "--environment",
            "production",
            "--json",
        ]
        try:
            return await self._run(command, app_path, env)
        except PlatformCommandError:
            return await self._run(
                [
                    "railway",
                    "domain",
                    "list",
                    "--project",
                    project_id,
                    "--environment",
                    "production",
                    "--json",
                ],
                app_path,
                env,
            )


class FirebaseDeployExecutor(_StaticHostingExecutor):
    async def deploy(
        self,
        plan: DeploymentPlan,
        credentials: dict[str, dict],
        on_log: LogCallback = None,
    ) -> DeploymentOutput:
        if plan.stack not in self.supported_stacks:
            raise ValueError(
                "Firebase Hosting currently supports Vite, React, and plain static sites only."
            )
        firebase = credentials.get("firebase", {})
        service_account = firebase.get("service_account")
        project_id = str(firebase.get("project_id", "")).strip()
        if not isinstance(service_account, dict) or not project_id:
            raise ValueError("Add a Firebase service-account JSON before deploying.")

        repo_path = await self._clone(plan)
        app_path = (repo_path / plan.app_path).resolve()
        logs: list[str] = []
        service_account_file: str | None = None
        try:
            with NamedTemporaryFile(
                mode="w", prefix="army-firebase-", suffix=".json", delete=False
            ) as handle:
                json.dump(service_account, handle)
                service_account_file = handle.name
            os.chmod(service_account_file, 0o600)
            env = {"GOOGLE_APPLICATION_CREDENTIALS": service_account_file}

            public_dir = await self._prepare_public_dir(plan, app_path, env, on_log)
            site_id = str(plan.target_config.get("site_id", "")).strip()
            if not site_id:
                site_id = _firebase_site_id(plan.deployment_name)

            await self._ensure_firebase_site(site_id, project_id, app_path, env)
            firebase_config = {
                "hosting": {
                    "site": site_id,
                    "public": os.path.relpath(public_dir, app_path),
                    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
                    "rewrites": [{"source": "**", "destination": "/index.html"}],
                }
            }
            (app_path / "firebase.json").write_text(
                json.dumps(firebase_config, indent=2), encoding="utf-8"
            )
            await self._log(on_log, "Uploading the site to Firebase Hosting...")
            await self._run(
                [
                    "firebase",
                    "deploy",
                    "--only",
                    "hosting",
                    "--project",
                    project_id,
                    "--non-interactive",
                ],
                app_path,
                env,
            )
            logs.append("Firebase Hosting deployment completed.")
            return (
                f"https://{site_id}.web.app",
                logs,
                {"project_id": project_id, "site_id": site_id},
            )
        finally:
            if service_account_file:
                Path(service_account_file).unlink(missing_ok=True)
            shutil.rmtree(repo_path, ignore_errors=True)

    async def _ensure_firebase_site(
        self,
        site_id: str,
        project_id: str,
        app_path: Path,
        env: dict[str, str],
    ) -> None:
        sites = await self._run(
            [
                "firebase",
                "hosting:sites:list",
                "--project",
                project_id,
                "--json",
            ],
            app_path,
            env,
        )
        if site_id in sites:
            return
        await self._run(
            [
                "firebase",
                "hosting:sites:create",
                site_id,
                "--project",
                project_id,
                "--non-interactive",
            ],
            app_path,
            env,
        )


class GitHubPagesDeployExecutor(_StaticHostingExecutor):
    def __init__(
        self,
        command_timeout: int | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(command_timeout)
        self.http_transport = http_transport

    async def deploy(
        self,
        plan: DeploymentPlan,
        credentials: dict[str, dict],
        on_log: LogCallback = None,
    ) -> DeploymentOutput:
        if plan.stack not in self.supported_stacks:
            raise ValueError("GitHub Pages supports Vite, React, and plain static sites only.")
        github = credentials.get("github", {})
        token = str(github.get("token", "")).strip()
        if not token:
            raise ValueError("Add your GitHub token before deploying to GitHub Pages.")

        owner, repository = _github_repository_parts(plan.repo_url)
        repo_path = await self._clone(plan)
        publish_path = self.workspace_root / f"pages-{uuid4().hex[:12]}"
        askpass_file: str | None = None
        logs: list[str] = []
        try:
            app_path = (repo_path / plan.app_path).resolve()
            base_path = _github_pages_base_path(owner, repository)
            public_dir = await self._prepare_public_dir(
                plan,
                app_path,
                {},
                on_log,
                base_path=base_path,
            )

            shutil.copytree(
                public_dir,
                publish_path,
                symlinks=True,
                ignore=shutil.ignore_patterns(".git", ".github", "node_modules"),
            )
            nojekyll_file = publish_path / ".nojekyll"
            if nojekyll_file.is_symlink():
                nojekyll_file.unlink()
            nojekyll_file.touch()
            index_file = publish_path / "index.html"
            not_found_file = publish_path / "404.html"
            if index_file.is_file() and not index_file.is_symlink() and not not_found_file.exists():
                shutil.copy2(index_file, not_found_file)

            await self._log(on_log, "Publishing the build to the gh-pages branch...")
            await self._run(
                ["git", "init", "--initial-branch=gh-pages"],
                publish_path,
                inherit_env=False,
            )
            await self._run(
                ["git", "config", "user.name", "Army Deploy Bot"],
                publish_path,
                inherit_env=False,
            )
            await self._run(
                ["git", "config", "user.email", "army-deploy@users.noreply.github.com"],
                publish_path,
                inherit_env=False,
            )
            await self._run(["git", "add", "--all"], publish_path, inherit_env=False)
            await self._run(
                ["git", "commit", "-m", f"Deploy {plan.commit_sha or 'latest'}"],
                publish_path,
                inherit_env=False,
            )

            with NamedTemporaryFile(
                mode="w", prefix="army-github-askpass-", delete=False
            ) as handle:
                handle.write(
                    "#!/bin/sh\n"
                    'case "$1" in\n'
                    "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
                    "  *) printf '%s\\n' \"$ARMY_GITHUB_TOKEN\" ;;\n"
                    "esac\n"
                )
                askpass_file = handle.name
            os.chmod(askpass_file, 0o700)
            push_env = {
                "ARMY_GITHUB_TOKEN": token,
                "GIT_ASKPASS": askpass_file,
                "GIT_TERMINAL_PROMPT": "0",
            }
            remote_url = f"https://github.com/{owner}/{repository}.git"
            await self._run(
                ["git", "push", "--force", remote_url, "HEAD:gh-pages"],
                publish_path,
                push_env,
                inherit_env=False,
            )
            logs.append("GitHub Pages branch published.")

            await self._log(on_log, "Enabling GitHub Pages...")
            live_url = await self._configure_pages(owner, repository, token)
            logs.append("GitHub Pages deployment configured.")
            return (
                live_url,
                logs,
                {"owner": owner, "repository": repository, "pages_branch": "gh-pages"},
            )
        finally:
            if askpass_file:
                Path(askpass_file).unlink(missing_ok=True)
            shutil.rmtree(repo_path, ignore_errors=True)
            shutil.rmtree(publish_path, ignore_errors=True)

    async def _configure_pages(self, owner: str, repository: str, token: str) -> str:
        api_url = f"https://api.github.com/repos/{quote(owner)}/{quote(repository)}/pages"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        payload = {"source": {"branch": "gh-pages", "path": "/"}}
        async with httpx.AsyncClient(
            timeout=self.command_timeout,
            transport=self.http_transport,
        ) as client:
            current = await client.get(api_url, headers=headers)
            if current.status_code == 200:
                method = client.put
                expected_status = 204
            elif current.status_code == 404:
                method = client.post
                expected_status = 201
            else:
                raise PlatformCommandError(_github_pages_api_error(current))

            response: httpx.Response | None = None
            for attempt in range(3):
                response = await method(api_url, headers=headers, json=payload)
                if response.status_code == expected_status:
                    break
                if response.status_code not in {409, 422} or attempt == 2:
                    raise PlatformCommandError(_github_pages_api_error(response))
                await asyncio.sleep(2 * (attempt + 1))

            refreshed = await client.get(api_url, headers=headers)
            if refreshed.status_code == 200:
                html_url = refreshed.json().get("html_url")
                if isinstance(html_url, str) and html_url:
                    return html_url.rstrip("/") + "/"

        return _github_pages_url(owner, repository)


def _find_json_value(raw: str, *keys: str) -> str | None:
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        return None

    def search(value: Any) -> str | None:
        if isinstance(value, dict):
            for key in keys:
                item = value.get(key)
                if isinstance(item, str) and item:
                    return item
            for item in value.values():
                found = search(item)
                if found:
                    return found
        if isinstance(value, list):
            for item in value:
                found = search(item)
                if found:
                    return found
        return None

    return search(payload)


def _find_url(raw: str, allowed_suffixes: tuple[str, ...]) -> str | None:
    for match in re.findall(r"(?:https?://)?[a-zA-Z0-9.-]+", raw):
        hostname = match.removeprefix("https://").removeprefix("http://").rstrip(".")
        if any(hostname.endswith(suffix) for suffix in allowed_suffixes):
            return f"https://{hostname}"
    return None


def _firebase_site_id(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]", "-", value.lower()).strip("-")
    cleaned = re.sub(r"-+", "-", cleaned)
    return (cleaned or f"army-{uuid4().hex[:12]}")[:30].rstrip("-")


def _github_repository_parts(repo_url: str) -> tuple[str, str]:
    parsed = urlparse(repo_url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        raise ValueError("GitHub Pages requires a github.com repository URL.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError("Send a GitHub repository URL, not a file or folder URL.")
    return parts[0], parts[1].removesuffix(".git")


def _github_pages_base_path(owner: str, repository: str) -> str:
    if repository.lower() == f"{owner.lower()}.github.io":
        return "/"
    return f"/{repository}/"


def _github_pages_url(owner: str, repository: str) -> str:
    return f"https://{owner}.github.io{_github_pages_base_path(owner, repository)}"


def _github_pages_api_error(response: httpx.Response) -> str:
    try:
        message = response.json().get("message", response.text)
    except (ValueError, AttributeError):
        message = response.text
    detail = str(message).strip()[:400] or "Unknown GitHub API error"
    return (
        f"GitHub Pages API returned {response.status_code}: {detail}. "
        "Check that the token can write Contents and Pages and administer this repository."
    )
