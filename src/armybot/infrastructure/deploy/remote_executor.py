from __future__ import annotations

import base64
import re
from collections.abc import Callable, Coroutine
from typing import Any

from armybot.domain.entities import DeploymentPlan
from armybot.domain.enums import ProjectStack
from armybot.infrastructure.deploy.ssh_client import SSHClient

LogCallback = Callable[[str], Coroutine[Any, Any, None]] | None


class RemoteRecipeDeployExecutor:
    """Deterministic remote deployment recipes for common web stacks."""

    def __init__(self, command_timeout: int = 900) -> None:
        self.command_timeout = max(command_timeout, 900)

    async def deploy(
        self,
        plan: DeploymentPlan,
        credentials: dict[str, dict],
        on_log: LogCallback = None,
    ) -> tuple[str | None, list[str]]:
        server = credentials.get("server", {})
        if not server:
            raise ValueError("Server credentials are required. Use /set_server first.")

        if plan.stack not in {ProjectStack.Vite, ProjectStack.Static, ProjectStack.React}:
            raise ValueError(
                f"Recipe deployment does not support stack '{plan.stack.value}' yet. "
                "Supported now: Vite/React static builds and plain static sites."
            )

        live_url = self._live_url(plan, server)
        script = self._static_site_script(plan, server, live_url)
        command = self._remote_script_command(plan.project_name, script)

        ssh = SSHClient(command_timeout=self.command_timeout)
        try:
            await self._log(on_log, "🔌 Connecting to server...")
            await ssh.connect(
                host=server["host"],
                username=server["user"],
                key_path=server["ssh_key_path"],
            )
            await self._log(on_log, "🚀 Running deterministic deployment recipe...")
            stdout, stderr, exit_code = await ssh.run(command)
        finally:
            await ssh.close()

        output = self._summarise_output(stdout, stderr, exit_code)
        logs = [
            "Deployment strategy: deterministic recipe.",
            f"Stack: {plan.stack.value}",
            f"Live URL: {live_url}",
            output,
        ]

        if exit_code != 0:
            raise RuntimeError(f"Recipe deployment failed with exit code {exit_code}. {output}")

        await self._log(on_log, "✅ Deployment recipe finished.")
        return live_url, logs

    @staticmethod
    def _static_site_script(plan: DeploymentPlan, server: dict, live_url: str) -> str:
        base_path = server.get("base_path", "/var/www")
        project = RemoteRecipeDeployExecutor._safe_name(plan.project_name)
        route_prefix = f"/{project}/"
        route_clean = f"/{project}"
        repo_url = plan.repo_url.replace("'", "'\\''")
        branch = plan.branch.replace("'", "'\\''")
        app_path = plan.app_path.replace("'", "'\\''")
        app_entry = plan.app_entry.replace("'", "'\\''") or "index.html"
        nginx_name = f"army-{project}"

        return f"""#!/usr/bin/env bash
set -euo pipefail

PROJECT='{project}'
REPO_URL='{repo_url}'
BRANCH='{branch}'
BASE_PATH='{base_path}'
APP_DIR="$BASE_PATH/$PROJECT"
APP_PATH='{app_path}'
APP_ENTRY='{app_entry}'
BUILD_DIR="$APP_DIR/$APP_PATH"
PUBLISH_DIR="$APP_DIR/_published"
NGINX_NAME='{nginx_name}'
ROUTE_PREFIX='{route_prefix}'
LIVE_URL='{live_url}'

echo "[1/7] Preparing directories"
sudo mkdir -p "$BASE_PATH"
sudo chown "$USER":"$USER" "$BASE_PATH"
git config --global --add safe.directory "*" 2>/dev/null || true
sudo git config --global --add safe.directory "*" 2>/dev/null || true

if [ -d "$APP_DIR" ]; then
  sudo chown -R "$USER":"$USER" "$APP_DIR" 2>/dev/null || true
fi

if [ -d "$APP_DIR/.git" ]; then
  echo "[2/7] Updating repository"
  cd "$APP_DIR"
  if git fetch origin "$BRANCH"; then
    git reset --hard "origin/$BRANCH"
  else
    git fetch origin || true
    DEFAULT_BRANCH=$(git remote show origin 2>/dev/null | sed -n '/HEAD branch/s/.*: //p' || echo "master")
    git reset --hard "origin/$DEFAULT_BRANCH" 2>/dev/null || git pull || true
  fi
else
  echo "[2/7] Cloning repository"
  rm -rf "$APP_DIR"
  if ! git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"; then
    echo "Branch $BRANCH not found, cloning default branch..."
    git clone --depth 1 "$REPO_URL" "$APP_DIR"
  fi
  cd "$APP_DIR"
fi

if [ ! -d "$BUILD_DIR" ]; then
  echo "Configured app path does not exist: $APP_PATH" >&2
  exit 19
fi

echo "[3/7] Detecting frontend output"
cd "$BUILD_DIR"
OUTPUT_DIR="$BUILD_DIR"
if [ -f package.json ]; then
  echo "[3/7] Installing Node dependencies"
  if [ -f package-lock.json ]; then
    npm ci
  else
    npm install
  fi

  echo "[4/7] Building frontend"
  VITE_BASE="$ROUTE_PREFIX" PUBLIC_URL="$ROUTE_PREFIX" BASE_URL="$ROUTE_PREFIX" npm run build

  if [ -d "$BUILD_DIR/dist" ]; then
    OUTPUT_DIR="$BUILD_DIR/dist"
  elif [ -d "$BUILD_DIR/build" ]; then
    OUTPUT_DIR="$BUILD_DIR/build"
  else
    echo "No dist or build directory was produced." >&2
    exit 20
  fi
else
  echo "[4/7] Static site has no build step"
  if [ ! -f "$BUILD_DIR/$APP_ENTRY" ]; then
    echo "Static recipe needs $APP_ENTRY at app path." >&2
    exit 21
  fi
  rm -rf "$PUBLISH_DIR"
  mkdir -p "$PUBLISH_DIR"
  cp -a "$BUILD_DIR"/. "$PUBLISH_DIR"/
  if [ "$APP_ENTRY" != "index.html" ]; then
    cp "$BUILD_DIR/$APP_ENTRY" "$PUBLISH_DIR/index.html"
  fi
  OUTPUT_DIR="$PUBLISH_DIR"
fi

echo "[5/7] Writing nginx location include"
sudo mkdir -p /etc/nginx/army-locations
sudo tee "/etc/nginx/army-locations/$NGINX_NAME.conf" >/dev/null <<NGINX
location = {route_clean} {{
    return 301 {route_prefix};
}}

location ^~ {route_prefix} {{
    alias $OUTPUT_DIR/;
    index index.html;
}}
NGINX

if ! sudo grep -R "include /etc/nginx/army-locations/\\*.conf;" /etc/nginx/sites-enabled /etc/nginx/conf.d >/dev/null 2>&1; then
  echo "Nginx is not configured to include /etc/nginx/army-locations/*.conf" >&2
  echo "Add: include /etc/nginx/army-locations/*.conf; inside the active server block." >&2
  exit 22
fi

echo "[6/7] Reloading nginx"
sudo nginx -t
sudo systemctl reload nginx

echo "[7/7] Verifying public URL"
curl -k -L --fail --max-time 30 "$LIVE_URL" >/dev/null

echo "Deployment complete: $LIVE_URL"
"""

    @staticmethod
    def _remote_script_command(project_name: str, script: str) -> str:
        safe_name = RemoteRecipeDeployExecutor._safe_name(project_name)
        encoded = base64.b64encode(script.encode()).decode()
        path = f"/tmp/army-recipe-{safe_name}.sh"
        return (
            f"printf '%s' '{encoded}' | base64 -d > {path} && "
            f"chmod +x {path} && "
            f"bash {path}"
        )

    @staticmethod
    def _live_url(plan: DeploymentPlan, server: dict) -> str:
        base_url = server.get("public_base_url")
        if not base_url:
            raise ValueError("Server public_base_url is required for recipe deployments.")
        return f"{base_url.rstrip('/')}/{RemoteRecipeDeployExecutor._safe_name(plan.project_name)}/"

    @staticmethod
    def _safe_name(name: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-").lower() or "project"

    @staticmethod
    def _summarise_output(stdout: str, stderr: str, exit_code: int) -> str:
        parts = [f"exit_code={exit_code}"]
        if stdout.strip():
            parts.append(f"stdout:\n{stdout.strip()[-1600:]}")
        if stderr.strip():
            parts.append(f"stderr:\n{stderr.strip()[-900:]}")
        return " | ".join(parts)

    @staticmethod
    async def _log(on_log: LogCallback, message: str) -> None:
        if on_log is None:
            return
        try:
            await on_log(message)
        except Exception:
            pass
