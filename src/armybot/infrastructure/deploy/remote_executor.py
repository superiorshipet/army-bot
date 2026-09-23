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
    """Deterministic remote deployment recipes for all web stacks."""

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

        live_url = self._live_url(plan, server)

        if plan.stack in {ProjectStack.Vite, ProjectStack.Static, ProjectStack.React}:
            script = self._static_site_script(plan, server, live_url)
        elif plan.stack == ProjectStack.Node:
            script = self._node_site_script(plan, server, live_url)
        elif plan.stack == ProjectStack.Python:
            script = self._python_site_script(plan, server, live_url)
        elif plan.stack == ProjectStack.DotNet:
            script = self._dotnet_site_script(plan, server, live_url)
        elif plan.stack == ProjectStack.Laravel:
            script = self._laravel_site_script(plan, server, live_url)
        else:
            script = self._universal_script(plan, server, live_url)

        command = self._remote_script_command(plan.project_name, script)

        ssh = SSHClient(command_timeout=self.command_timeout)
        try:
            await self._log(on_log, "🔌 Connecting to server...")
            await ssh.connect(
                host=server["host"],
                username=server["user"],
                key_path=server["ssh_key_path"],
            )
            await self._log(on_log, f"🚀 Deploying stack '{plan.stack.value}'...")
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

    async def cleanup_project(
        self,
        project_name: str,
        credentials: dict[str, dict],
        on_log: LogCallback = None,
    ) -> list[str]:
        server = credentials.get("server", {})
        if not server:
            raise ValueError("Server credentials are required to delete a project.")

        safe_name = self._safe_name(project_name)
        if not safe_name or safe_name in {"", "/", ".", "..", "var", "www", "etc", "home"}:
            raise ValueError(f"Invalid project name for deletion: {project_name!r}")

        base_path = server.get("base_path", "/var/www").rstrip("/")
        app_dir = f"{base_path}/{safe_name}"
        service_name = f"army-{safe_name}"
        nginx_name = f"army-{safe_name}"

        script = f"""#!/usr/bin/env bash
set -x

echo "[1/4] Stopping and removing systemd service..."
sudo systemctl stop "{service_name}.service" 2>/dev/null || true
sudo systemctl disable "{service_name}.service" 2>/dev/null || true
sudo rm -f "/etc/systemd/system/{service_name}.service"
sudo systemctl daemon-reload

echo "[2/4] Removing nginx configuration..."
sudo rm -f "/etc/nginx/army-locations/{nginx_name}.conf"
sudo nginx -t 2>/dev/null && sudo systemctl reload nginx 2>/dev/null || true

echo "[3/4] Removing project files..."
if [ -d "{app_dir}" ] && [ "{app_dir}" != "/var/www" ] && [ "{app_dir}" != "/" ]; then
  sudo rm -rf "{app_dir}"
fi
rm -f "/tmp/army-recipe-{safe_name}.sh" "/tmp/army-recipe-del-{safe_name}.sh"

echo "[4/4] Project {safe_name} completely removed from server."
"""
        command = self._remote_script_command(f"del-{safe_name}", script)
        ssh = SSHClient(command_timeout=120)
        try:
            await self._log(on_log, f"🔌 Connecting to server to remove {safe_name}...")
            await ssh.connect(
                host=server["host"],
                username=server["user"],
                key_path=server["ssh_key_path"],
            )
            await self._log(on_log, f"🗑️ Deleting files and services for {safe_name}...")
            stdout, stderr, exit_code = await ssh.run(command)
        finally:
            await ssh.close()

        output = self._summarise_output(stdout, stderr, exit_code)
        logs = [
            f"Cleanup requested for {safe_name}",
            output,
        ]
        if exit_code != 0:
            raise RuntimeError(f"Cleanup failed with exit code {exit_code}. {output}")
        return logs

    @staticmethod
    def _common_git_prep(project: str, repo_url: str, branch: str, base_path: str, app_path: str) -> str:
        return f"""
PROJECT='{project}'
REPO_URL='{repo_url}'
BRANCH='{branch}'
BASE_PATH='{base_path}'
APP_DIR="$BASE_PATH/$PROJECT"
APP_PATH='{app_path}'
BUILD_DIR="$APP_DIR/$APP_PATH"

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
"""

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

        prep = RemoteRecipeDeployExecutor._common_git_prep(project, repo_url, branch, base_path, app_path)

        return f"""#!/usr/bin/env bash
set -euo pipefail
{prep}
PUBLISH_DIR="$APP_DIR/_published"
NGINX_NAME='{nginx_name}'
ROUTE_PREFIX='{route_prefix}'
ROUTE_CLEAN='{route_clean}'
LIVE_URL='{live_url}'
APP_ENTRY='{app_entry}'

echo "[3/7] Detecting frontend output"
cd "$BUILD_DIR"
OUTPUT_DIR="$BUILD_DIR"
if [ -f package.json ]; then
  echo "[3/7] Installing Node dependencies"
  if [ -f package-lock.json ]; then
    npm ci || npm install
  else
    npm install
  fi

  echo "[4/7] Building frontend"
  npx vite build --base="$ROUTE_PREFIX" 2>/dev/null || npm run build -- --base="$ROUTE_PREFIX" 2>/dev/null || VITE_BASE="$ROUTE_PREFIX" PUBLIC_URL="$ROUTE_PREFIX" BASE_URL="$ROUTE_PREFIX" npm run build || true

  if [ -d "$BUILD_DIR/dist" ]; then
    OUTPUT_DIR="$BUILD_DIR/dist"
  elif [ -d "$BUILD_DIR/build" ]; then
    OUTPUT_DIR="$BUILD_DIR/build"
  elif [ -d "$BUILD_DIR/out" ]; then
    OUTPUT_DIR="$BUILD_DIR/out"
  fi
else
  echo "[4/7] Static site has no build step"
  rm -rf "$PUBLISH_DIR"
  mkdir -p "$PUBLISH_DIR"
  cp -a "$BUILD_DIR"/. "$PUBLISH_DIR"/
  if [ -f "$BUILD_DIR/$APP_ENTRY" ] && [ "$APP_ENTRY" != "index.html" ]; then
    cp "$BUILD_DIR/$APP_ENTRY" "$PUBLISH_DIR/index.html"
  fi
  OUTPUT_DIR="$PUBLISH_DIR"
fi

echo "[5/7] Writing nginx location include"
sudo mkdir -p /etc/nginx/army-locations
sudo tee "/etc/nginx/army-locations/$NGINX_NAME.conf" >/dev/null <<NGINX
location = $ROUTE_CLEAN {{
    return 301 $ROUTE_PREFIX;
}}

location ^~ $ROUTE_PREFIX {{
    alias $OUTPUT_DIR/;
    index index.html;
    try_files \$uri \$uri/ $ROUTE_PREFIX/index.html;
}}
NGINX

if ! sudo grep -R "include /etc/nginx/army-locations/\\*.conf;" /etc/nginx/sites-enabled /etc/nginx/conf.d >/dev/null 2>&1; then
  echo "Add: include /etc/nginx/army-locations/*.conf; inside the active server block." >&2
  exit 22
fi

echo "[6/7] Reloading nginx"
sudo nginx -t
sudo systemctl reload nginx

echo "[7/7] Verifying public URL"
curl -k -L --fail --max-time 30 "$LIVE_URL" >/dev/null || curl -k -L --max-time 10 "http://127.0.0.1$ROUTE_PREFIX" >/dev/null || true

echo "Deployment complete: $LIVE_URL"
"""

    _NEXT_BASEPATH_INJECTOR = r"""python3 - "$ROUTE_CLEAN" << 'PYEOF'
import sys, glob, re

route = sys.argv[1]
configs = glob.glob('next.config.*')
if not configs:
    with open('next.config.js', 'w') as f:
        f.write('/** @type {import("next").NextConfig} */\nconst nextConfig = { basePath: "' + route + '" };\nmodule.exports = nextConfig;\n')
    print('Created next.config.js with basePath: ' + route)
else:
    for cfg in configs:
        with open(cfg, 'r') as f:
            content = f.read()
        if re.search(r"basePath\s*:", content):
            content = re.sub(r"(basePath\s*:\s*)['\"][^'\"]*['\"]", r'\g<1>"' + route + '"', content)
            with open(cfg, 'w') as f:
                f.write(content)
            print('Updated basePath in ' + cfg)
            continue
        patterns = [
            (r'(const\s+\w+\s*:\s*\w+\s*=\s*\{)', r'\1\n  basePath: "' + route + '",'),
            (r'(const\s+\w+\s*=\s*\{)', r'\1\n  basePath: "' + route + '",'),
            (r'(let\s+\w+\s*=\s*\{)', r'\1\n  basePath: "' + route + '",'),
            (r'(module\.exports\s*=\s*\{)', r'\1\n  basePath: "' + route + '",'),
            (r'(export\s+default\s*\{)', r'\1\n  basePath: "' + route + '",'),
        ]
        modified = False
        for pat, repl in patterns:
            if re.search(pat, content):
                content = re.sub(pat, repl, content, count=1)
                modified = True
                break
        if modified:
            with open(cfg, 'w') as f:
                f.write(content)
            print('Injected basePath into ' + cfg)
        else:
            print('Warning: could not automatically inject basePath into ' + cfg)
PYEOF"""

    @staticmethod
    def _node_site_script(plan: DeploymentPlan, server: dict, live_url: str) -> str:
        base_path = server.get("base_path", "/var/www")
        project = RemoteRecipeDeployExecutor._safe_name(plan.project_name)
        route_prefix = f"/{project}/"
        route_clean = f"/{project}"
        repo_url = plan.repo_url.replace("'", "'\\''")
        branch = plan.branch.replace("'", "'\\''")
        app_path = plan.app_path.replace("'", "'\\''")
        nginx_name = f"army-{project}"
        service_name = f"army-{project}"

        prep = RemoteRecipeDeployExecutor._common_git_prep(project, repo_url, branch, base_path, app_path)

        return f"""#!/usr/bin/env bash
set -euo pipefail
{prep}
NGINX_NAME='{nginx_name}'
SERVICE_NAME='{service_name}'
ROUTE_PREFIX='{route_prefix}'
ROUTE_CLEAN='{route_clean}'
LIVE_URL='{live_url}'

cd "$BUILD_DIR"

# Allocate or reuse a persistent port for this project
if [ -f "$APP_DIR/.port" ]; then
  PORT=$(cat "$APP_DIR/.port")
else
  PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')
  echo "$PORT" > "$APP_DIR/.port"
fi

echo "[3/7] Installing Node dependencies"
if [ -f package-lock.json ]; then
  npm ci || npm install
elif [ -f yarn.lock ]; then
  yarn install 2>/dev/null || npm install
elif [ -f pnpm-lock.yaml ]; then
  pnpm install 2>/dev/null || npm install
else
  npm install
fi

# Detect Next.js and configure subpath
IS_NEXT=false
if grep -q '"next"' package.json 2>/dev/null; then
  IS_NEXT=true
  echo "Configuring Next.js subpath support: $ROUTE_CLEAN"
  {RemoteRecipeDeployExecutor._NEXT_BASEPATH_INJECTOR}
fi

echo "[4/7] Building application (if script exists)"
if grep -q '"build"' package.json 2>/dev/null; then
  npm run build || true
fi

# Detect entry/start command
START_CMD=""
if grep -q '"start"' package.json 2>/dev/null; then
  START_CMD="npm start"
elif [ -f server.js ]; then
  START_CMD="node server.js"
elif [ -f app.js ]; then
  START_CMD="node app.js"
elif [ -f index.js ]; then
  START_CMD="node index.js"
elif [ -f dist/index.js ]; then
  START_CMD="node dist/index.js"
elif [ -f build/index.js ]; then
  START_CMD="node build/index.js"
else
  START_CMD="npm start"
fi

echo "[5/7] Configuring systemd service"
sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null <<SERVICE
[Unit]
Description=Army Deploy - $PROJECT (Node.js)
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BUILD_DIR
EnvironmentFile=-$APP_DIR/.env
Environment=PORT=$PORT
Environment=NODE_ENV=production
Environment=PATH=/usr/local/bin:/usr/bin:/bin:$BUILD_DIR/node_modules/.bin
ExecStart=/usr/bin/env $START_CMD
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME.service"
sudo systemctl restart "$SERVICE_NAME.service"

echo "[6/7] Writing nginx proxy include"
sudo mkdir -p /etc/nginx/army-locations
if [ "$IS_NEXT" = "true" ]; then
sudo tee "/etc/nginx/army-locations/$NGINX_NAME.conf" >/dev/null <<NGINX
location = $ROUTE_CLEAN {{
    proxy_pass http://127.0.0.1:$PORT;
    proxy_http_version 1.1;
    proxy_set_header Host \\$http_host;
    proxy_set_header X-Real-IP \\$remote_addr;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \\$scheme;
    proxy_set_header Upgrade \\$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 90;
}}

location ^~ $ROUTE_PREFIX {{
    proxy_pass http://127.0.0.1:$PORT;
    proxy_http_version 1.1;
    proxy_set_header Host \\$http_host;
    proxy_set_header X-Real-IP \\$remote_addr;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \\$scheme;
    proxy_set_header Upgrade \\$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 90;
}}
NGINX
else
sudo tee "/etc/nginx/army-locations/$NGINX_NAME.conf" >/dev/null <<NGINX
location = $ROUTE_CLEAN {{
    return 301 $ROUTE_PREFIX;
}}

location ^~ $ROUTE_PREFIX {{
    proxy_pass http://127.0.0.1:$PORT/;
    proxy_http_version 1.1;
    proxy_set_header Host \\$http_host;
    proxy_set_header X-Real-IP \\$remote_addr;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \\$scheme;
    proxy_set_header Upgrade \\$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 90;
}}
NGINX
fi

sudo nginx -t
sudo systemctl reload nginx

echo "[7/7] Verifying service and public URL"
sleep 3
curl -k -L --fail --max-time 30 "$LIVE_URL" >/dev/null || curl -k -L --max-time 10 "http://127.0.0.1:$PORT$ROUTE_CLEAN" >/dev/null || curl -k -L --max-time 10 "http://127.0.0.1:$PORT/" >/dev/null || true

echo "Deployment complete: $LIVE_URL"
"""

    @staticmethod
    def _python_site_script(plan: DeploymentPlan, server: dict, live_url: str) -> str:
        base_path = server.get("base_path", "/var/www")
        project = RemoteRecipeDeployExecutor._safe_name(plan.project_name)
        route_prefix = f"/{project}/"
        route_clean = f"/{project}"
        repo_url = plan.repo_url.replace("'", "'\\''")
        branch = plan.branch.replace("'", "'\\''")
        app_path = plan.app_path.replace("'", "'\\''")
        nginx_name = f"army-{project}"
        service_name = f"army-{project}"

        prep = RemoteRecipeDeployExecutor._common_git_prep(project, repo_url, branch, base_path, app_path)

        return f"""#!/usr/bin/env bash
set -euo pipefail
{prep}
NGINX_NAME='{nginx_name}'
SERVICE_NAME='{service_name}'
ROUTE_PREFIX='{route_prefix}'
ROUTE_CLEAN='{route_clean}'
LIVE_URL='{live_url}'

cd "$BUILD_DIR"

if [ -f "$APP_DIR/.port" ]; then
  PORT=$(cat "$APP_DIR/.port")
else
  PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')
  echo "$PORT" > "$APP_DIR/.port"
fi

echo "[3/7] Setting up Python virtual environment"
python3 -m venv .venv
.venv/bin/pip install -U pip setuptools wheel

echo "[4/7] Installing Python dependencies"
if [ -f requirements.txt ]; then
  .venv/bin/pip install -r requirements.txt
elif [ -f pyproject.toml ]; then
  .venv/bin/pip install .
fi

START_CMD=""
if [ -f main.py ] && grep -qi "fastapi\\|uvicorn" main.py 2>/dev/null; then
  .venv/bin/pip install uvicorn
  START_CMD=".venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port $PORT"
elif [ -f app.py ] && grep -qi "fastapi\\|uvicorn" app.py 2>/dev/null; then
  .venv/bin/pip install uvicorn
  START_CMD=".venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port $PORT"
elif [ -f manage.py ]; then
  .venv/bin/pip install gunicorn
  .venv/bin/python manage.py migrate --noinput || true
  .venv/bin/python manage.py collectstatic --noinput || true
  WSGI_APP=$(grep -ro "WSGI_APPLICATION = '.*'" . 2>/dev/null | head -n 1 | cut -d"'" -f2 || echo "wsgi:application")
  START_CMD=".venv/bin/gunicorn $WSGI_APP --bind 127.0.0.1:$PORT"
elif [ -f main.py ]; then
  START_CMD=".venv/bin/python main.py"
elif [ -f app.py ]; then
  START_CMD=".venv/bin/python app.py"
else
  START_CMD=".venv/bin/python -m http.server $PORT"
fi

echo "[5/7] Configuring systemd service"
sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null <<SERVICE
[Unit]
Description=Army Deploy - $PROJECT (Python)
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BUILD_DIR
EnvironmentFile=-$APP_DIR/.env
Environment=PORT=$PORT
Environment=PYTHONUNBUFFERED=1
ExecStart=$BUILD_DIR/$START_CMD
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME.service"
sudo systemctl restart "$SERVICE_NAME.service"

echo "[6/7] Writing nginx proxy include"
sudo mkdir -p /etc/nginx/army-locations
sudo tee "/etc/nginx/army-locations/$NGINX_NAME.conf" >/dev/null <<NGINX
location = $ROUTE_CLEAN {{
    return 301 $ROUTE_PREFIX;
}}

location ^~ $ROUTE_PREFIX {{
    proxy_pass http://127.0.0.1:$PORT/;
    proxy_http_version 1.1;
    proxy_set_header Host \\$http_host;
    proxy_set_header X-Real-IP \\$remote_addr;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \\$scheme;
    proxy_set_header Upgrade \\$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 90;
}}
NGINX

sudo nginx -t
sudo systemctl reload nginx

echo "[7/7] Verifying public URL"
sleep 3
curl -k -L --fail --max-time 30 "$LIVE_URL" >/dev/null || curl -k -L --max-time 10 "http://127.0.0.1:$PORT/" >/dev/null || true

echo "Deployment complete: $LIVE_URL"
"""

    @staticmethod
    def _dotnet_site_script(plan: DeploymentPlan, server: dict, live_url: str) -> str:
        base_path = server.get("base_path", "/var/www")
        project = RemoteRecipeDeployExecutor._safe_name(plan.project_name)
        route_prefix = f"/{project}/"
        route_clean = f"/{project}"
        repo_url = plan.repo_url.replace("'", "'\\''")
        branch = plan.branch.replace("'", "'\\''")
        app_path = plan.app_path.replace("'", "'\\''")
        nginx_name = f"army-{project}"
        service_name = f"army-{project}"

        prep = RemoteRecipeDeployExecutor._common_git_prep(project, repo_url, branch, base_path, app_path)

        return f"""#!/usr/bin/env bash
set -euo pipefail
{prep}
NGINX_NAME='{nginx_name}'
SERVICE_NAME='{service_name}'
ROUTE_PREFIX='{route_prefix}'
ROUTE_CLEAN='{route_clean}'
LIVE_URL='{live_url}'

cd "$BUILD_DIR"

if [ -f "$APP_DIR/.port" ]; then
  PORT=$(cat "$APP_DIR/.port")
else
  PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')
  echo "$PORT" > "$APP_DIR/.port"
fi

echo "[3/7] Restoring .NET dependencies"
dotnet restore

echo "[4/7] Publishing .NET release"
PUBLISH_DIR="$BUILD_DIR/publish"
dotnet publish -c Release -o "$PUBLISH_DIR"

CLIENT_DIR=""
for candidate in "$APP_DIR/src/client" "$APP_DIR/client" "$APP_DIR/src/frontend" "$APP_DIR/frontend" "$APP_DIR/ui"; do
  if [ -f "$candidate/package.json" ]; then
    CLIENT_DIR="$candidate"
    break
  fi
done

if [ -n "$CLIENT_DIR" ]; then
  echo "Building frontend client in $CLIENT_DIR..."
  cd "$CLIENT_DIR"
  npm install
  python3 -c "
import glob
for f in glob.glob('$CLIENT_DIR/src/**/router*.*', recursive=True):
    try:
        content = open(f).read()
        if 'createBrowserRouter(' in content and 'basename' not in content:
            new_content = content.replace(']);', '], {{{{ basename: import.meta.env.BASE_URL }}}});')
            if new_content != content:
                open(f, 'w').write(new_content)
    except Exception:
        pass
" 2>/dev/null || true
  VITE_API_BASE_URL="$ROUTE_CLEAN" npm run build -- --base="$ROUTE_PREFIX" || npm run build || true
  cd "$BUILD_DIR"
fi

RUNTIMECFG=$(ls "$PUBLISH_DIR"/*.runtimeconfig.json 2>/dev/null | head -n 1 || true)
if [ -n "$RUNTIMECFG" ]; then
  APP_NAME=$(echo "$RUNTIMECFG" | sed 's/\\.runtimeconfig\\.json$//')
  DLL_FILE="${{APP_NAME}}.dll"
else
  DLL_FILE=$(find "$PUBLISH_DIR" -maxdepth 1 -name "*.dll" ! -name "Microsoft.*" ! -name "System.*" ! -name "Npgsql*" ! -name "OpenAI*" ! -name "StackExchange*" | head -n 1)
fi

echo "[5/7] Configuring systemd service"
sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null <<SERVICE
[Unit]
Description=Army Deploy - $PROJECT (.NET)
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PUBLISH_DIR
EnvironmentFile=-$APP_DIR/.env
Environment=ASPNETCORE_ENVIRONMENT=Development
Environment=ASPNETCORE_URLS=http://127.0.0.1:$PORT
ExecStart=/usr/bin/dotnet $DLL_FILE --urls "http://127.0.0.1:$PORT"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME.service"
sudo systemctl restart "$SERVICE_NAME.service"

echo "[6/7] Writing nginx proxy include"
sudo mkdir -p /etc/nginx/army-locations

CLIENT_DIST=""
if [ -n "$CLIENT_DIR" ]; then
  if [ -d "$CLIENT_DIR/dist" ]; then
    CLIENT_DIST="$CLIENT_DIR/dist"
  elif [ -d "$CLIENT_DIR/build" ]; then
    CLIENT_DIST="$CLIENT_DIR/build"
  fi
fi

if [ -n "$CLIENT_DIST" ]; then
  sudo tee "/etc/nginx/army-locations/$NGINX_NAME.conf" >/dev/null <<NGINX
location = $ROUTE_CLEAN {{
    return 301 $ROUTE_PREFIX;
}}

location ^~ {route_prefix}api/ {{
    proxy_pass http://127.0.0.1:$PORT/api/;
    proxy_http_version 1.1;
    proxy_set_header Host \\$http_host;
    proxy_set_header X-Real-IP \\$remote_addr;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \\$scheme;
    proxy_set_header Upgrade \\$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 90;
}}

location ^~ {route_prefix}scalar/ {{
    proxy_pass http://127.0.0.1:$PORT/scalar/;
    proxy_http_version 1.1;
    proxy_set_header Host \\$http_host;
    proxy_set_header X-Real-IP \\$remote_addr;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \\$scheme;
    proxy_set_header Upgrade \\$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 90;
}}

location ^~ {route_prefix}openapi/ {{
    proxy_pass http://127.0.0.1:$PORT/openapi/;
    proxy_http_version 1.1;
    proxy_set_header Host \\$http_host;
    proxy_set_header X-Real-IP \\$remote_addr;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \\$scheme;
}}

location ^~ {route_prefix}swagger/ {{
    proxy_pass http://127.0.0.1:$PORT/swagger/;
    proxy_http_version 1.1;
    proxy_set_header Host \\$http_host;
    proxy_set_header X-Real-IP \\$remote_addr;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \\$scheme;
}}

location ^~ $ROUTE_PREFIX {{
    alias $CLIENT_DIST/;
    index index.html;
    try_files \\$uri \\$uri/ $ROUTE_PREFIX/index.html;
}}
NGINX
else
  sudo tee "/etc/nginx/army-locations/$NGINX_NAME.conf" >/dev/null <<NGINX
location = $ROUTE_CLEAN {{
    return 301 $ROUTE_PREFIX;
}}

location ^~ $ROUTE_PREFIX {{
    proxy_pass http://127.0.0.1:$PORT/;
    proxy_redirect ~^http://[^/]+/(.*) $ROUTE_PREFIX\\$1;
    proxy_redirect / $ROUTE_PREFIX;
    proxy_http_version 1.1;
    proxy_set_header Host \\$http_host;
    proxy_set_header X-Real-IP \\$remote_addr;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \\$scheme;
    proxy_set_header Upgrade \\$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 90;
}}
NGINX
fi

sudo nginx -t
sudo systemctl reload nginx

echo "[7/7] Verifying public URL"
sleep 3
curl -k -L --fail --max-time 30 "$LIVE_URL" >/dev/null || curl -k -L --max-time 10 "http://127.0.0.1:$PORT/" >/dev/null || true

echo "Deployment complete: $LIVE_URL"
"""

    @staticmethod
    def _laravel_site_script(plan: DeploymentPlan, server: dict, live_url: str) -> str:
        base_path = server.get("base_path", "/var/www")
        project = RemoteRecipeDeployExecutor._safe_name(plan.project_name)
        route_prefix = f"/{project}/"
        route_clean = f"/{project}"
        repo_url = plan.repo_url.replace("'", "'\\''")
        branch = plan.branch.replace("'", "'\\''")
        app_path = plan.app_path.replace("'", "'\\''")
        nginx_name = f"army-{project}"

        prep = RemoteRecipeDeployExecutor._common_git_prep(project, repo_url, branch, base_path, app_path)

        return f"""#!/usr/bin/env bash
set -euo pipefail
{prep}
NGINX_NAME='{nginx_name}'
ROUTE_PREFIX='{route_prefix}'
ROUTE_CLEAN='{route_clean}'
LIVE_URL='{live_url}'

cd "$BUILD_DIR"

echo "[3/7] Installing PHP dependencies"
composer install --no-dev --optimize-autoloader --no-interaction

echo "[4/7] Configuring Laravel environment"
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  php artisan key:generate --force || true
fi

php artisan storage:link 2>/dev/null || true
php artisan migrate --force 2>/dev/null || true

sudo chown -R www-data:www-data storage bootstrap/cache 2>/dev/null || true
sudo chmod -R 775 storage bootstrap/cache 2>/dev/null || true

echo "[5/7] Detecting PHP-FPM socket"
PHP_SOCK=$(ls /run/php/php*-fpm.sock 2>/dev/null | head -n 1 || echo "/run/php/php8.3-fpm.sock")

echo "[6/7] Writing nginx configuration"
sudo mkdir -p /etc/nginx/army-locations
sudo tee "/etc/nginx/army-locations/$NGINX_NAME.conf" >/dev/null <<NGINX
location = $ROUTE_CLEAN {{
    return 301 $ROUTE_PREFIX;
}}

location ^~ $ROUTE_PREFIX {{
    alias $BUILD_DIR/public/;
    try_files \\$uri \\$uri/ @$NGINX_NAME;

    location ~ \\.php$ {{
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:$PHP_SOCK;
        fastcgi_param SCRIPT_FILENAME \\$request_filename;
    }}
}}

location @$NGINX_NAME {{
    rewrite ^$ROUTE_PREFIX(.*)$ $ROUTE_PREFIXindex.php?\\$1 last;
}}
NGINX

sudo nginx -t
sudo systemctl reload nginx

echo "[7/7] Verifying public URL"
curl -k -L --fail --max-time 30 "$LIVE_URL" >/dev/null || curl -k -L --max-time 10 "http://127.0.0.1/" >/dev/null || true

echo "Deployment complete: $LIVE_URL"
"""

    @staticmethod
    def _universal_script(plan: DeploymentPlan, server: dict, live_url: str) -> str:
        """Fallback script that auto-detects stack directly on the cloned files."""
        base_path = server.get("base_path", "/var/www")
        project = RemoteRecipeDeployExecutor._safe_name(plan.project_name)
        route_prefix = f"/{project}/"
        route_clean = f"/{project}"
        repo_url = plan.repo_url.replace("'", "'\\''")
        branch = plan.branch.replace("'", "'\\''")
        app_path = plan.app_path.replace("'", "'\\''")
        nginx_name = f"army-{project}"

        prep = RemoteRecipeDeployExecutor._common_git_prep(project, repo_url, branch, base_path, app_path)

        return f"""#!/usr/bin/env bash
set -euo pipefail
{prep}
NGINX_NAME='{nginx_name}'
ROUTE_PREFIX='{route_prefix}'
ROUTE_CLEAN='{route_clean}'
LIVE_URL='{live_url}'

cd "$BUILD_DIR"

if [ -f package.json ]; then
  echo "Auto-detected Node/JavaScript project"
  npm install
  if grep -q '"build"' package.json 2>/dev/null; then
    npm run build || true
  fi
fi

# If dist or build folder exists, publish statically
PUBLISH_DIR=""
if [ -d dist ]; then
  PUBLISH_DIR="$BUILD_DIR/dist"
elif [ -d build ]; then
  PUBLISH_DIR="$BUILD_DIR/build"
elif [ -d public ]; then
  PUBLISH_DIR="$BUILD_DIR/public"
else
  PUBLISH_DIR="$BUILD_DIR"
fi

sudo mkdir -p /etc/nginx/army-locations
sudo tee "/etc/nginx/army-locations/$NGINX_NAME.conf" >/dev/null <<NGINX
location = $ROUTE_CLEAN {{
    return 301 $ROUTE_PREFIX;
}}

location ^~ $ROUTE_PREFIX {{
    alias $PUBLISH_DIR/;
    index index.html index.php;
}}
NGINX

sudo nginx -t
sudo systemctl reload nginx

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
