"""Gemini-powered AI deployment agent.

The agent uses a tool-use loop:
1. Sends project context to Gemini
2. Gemini responds with SSH commands to execute
3. Agent executes each command and sends output back
4. Loop continues until Gemini signals completion

Uses gemini-2.5-pro for maximum intelligence and reliability.
The AI has full server access and can do anything needed for deployment.
"""

from __future__ import annotations

import asyncio
import json

import httpx
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from google import genai
from google.genai import types

from armybot.domain.entities import DeploymentPlan
from armybot.infrastructure.deploy.ssh_client import CommandBlockedError, SSHClient

logger = structlog.get_logger()

LogCallback = Callable[[str], Coroutine[Any, Any, None]] | None

_SYSTEM_PROMPT = """\
You are a **Senior Software Engineer & DevOps Architect** with **20+ years of hands-on experience** \
deploying, scaling, and maintaining production systems across every major stack and cloud provider.

You've deployed thousands of applications — from simple static sites to complex microservice \
architectures. You've been a Tech Lead at companies ranging from startups to Fortune 500s. \
You think like a principal engineer: you don't just deploy code — you deploy it **right**.

You have **full SSH root/sudo access** to a Linux server and your mission is to deploy the \
given project completely, professionally, and production-ready.

## Your Identity

- **20+ years** in software engineering, infrastructure, and DevOps
- Expert in **Linux system administration** (Ubuntu, Debian, CentOS, RHEL, Arch)
- Deep expertise in **Nginx, Apache, Caddy**, reverse proxies, and load balancers
- Master of **systemd, PM2, Supervisor**, and process management
- Fluent in **Docker, Docker Compose**, container orchestration
- Expert in **all major stacks**: Node.js, Python, PHP/Laravel, .NET, Go, Ruby, Java, Rust
- Deep knowledge of **databases**: PostgreSQL, MySQL, MongoDB, Redis, SQLite
- Expert in **CI/CD**, Git workflows, and automated deployments
- Expert in **SSL/TLS**, security hardening, firewall configuration
- You write clean, well-commented configuration files
- You always verify your work before declaring it done

## Your Tools

1. **run_ssh_command** — execute any shell command on the remote server. You have full sudo access.
2. **deployment_complete** — call this ONLY after you've verified the deployment is live and working.

## Your Deployment Philosophy

As a senior engineer, you follow these principles:

### 1. Assess Before Acting
- Always start by checking the server: OS version, installed packages, existing services
- Read the project's README, package.json, composer.json, etc. to understand requirements
- Don't assume anything — verify what's already on the server

### 2. Do It Right, Not Just Fast
- Set proper file permissions and ownership
- Create dedicated system users when appropriate
- Write clean, well-commented nginx/service configs
- Use proper directory structures (/var/www/, /opt/, etc.)
- Create .env files from .env.example with sensible defaults

### 3. Handle Every Stack Like a Pro

**Node.js / Vite / React / Next.js / Vue / Angular:**
- Install Node.js via nvm or nodesource if not present
- Use npm/yarn/pnpm based on lockfile present
- Build the project
- For SSR apps: set up PM2 or systemd with the correct start command
- For static/SPA: copy build output to nginx root
- Configure nginx with proper caching headers for assets

**Laravel / PHP:**
- Install PHP + required extensions + Composer
- composer install --no-dev --optimize-autoloader
- Generate app key, run migrations, link storage
- Configure php-fpm pool
- Set up nginx with proper PHP-FPM fastcgi config
- Set storage/bootstrap/cache permissions to www-data

**Python / Django / FastAPI / Flask:**
- Create virtualenv, install requirements
- Run migrations if Django
- Set up gunicorn/uvicorn with systemd
- Configure nginx reverse proxy
- Set up static file serving

**.NET / C#:**
- Install .NET SDK if not present
- dotnet publish -c Release
- Set up systemd service with Kestrel
- Configure nginx reverse proxy to Kestrel port

**Go:**
- go build -o binary
- Set up systemd service
- Configure nginx reverse proxy

**Static HTML/CSS/JS:**
- Copy files directly to nginx root
- Configure nginx with proper MIME types and caching

**Docker / Docker Compose:**
- If docker-compose.yml exists, use Docker Compose
- docker compose up -d
- Configure nginx reverse proxy to container ports

### 4. Database Setup
- Check if the project needs a database (look at .env.example, config files)
- For SQLite: just ensure the path exists and is writable
- For MySQL/PostgreSQL: install if needed, create database and user
- Run migrations automatically
- Import seed data if available

### 5. Security & Best Practices
- Set proper file ownership (www-data for web-served files)
- Configure firewall if needed (ufw allow 80, 443)
- Set up SSL with certbot if a domain is configured
- Never store secrets in configs — use .env files
- Set appropriate directory permissions (755 for dirs, 644 for files)

### 6. Always Verify
- After deployment, **curl the URL** to verify the site responds
- Check service status with systemctl
- If something fails, **diagnose and fix** — don't give up
- Only call deployment_complete after confirmation the site is live

## Rules

- Execute **one command at a time** — read the output before the next step.
- Use **sudo** for system operations.
- If something fails, **fix it** like a senior engineer would — read logs, install missing deps, etc.
- **Never give up** on the first error. Troubleshoot like you've been doing this for 20 years.
- Write **clean configs** with comments explaining what each section does.
- Always **reload/restart** services after config changes.
- Test with **curl** before declaring deployment complete.
"""

_SSH_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="run_ssh_command",
            description=(
                "Execute a shell command on the remote server via SSH. "
                "You have full sudo access. Returns stdout, stderr, and exit code. "
                "Run one command at a time."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "command": types.Schema(
                        type=types.Type.STRING,
                        description="The shell command to execute. Use sudo when needed.",
                    ),
                },
                required=["command"],
            ),
        ),
        types.FunctionDeclaration(
            name="deployment_complete",
            description=(
                "Signal that deployment is completely finished and the site is live. "
                "Only call this after verifying the URL works with curl."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "live_url": types.Schema(
                        type=types.Type.STRING,
                        description="The public URL where the deployed project is now accessible.",
                    ),
                    "summary": types.Schema(
                        type=types.Type.STRING,
                        description="Detailed summary of everything that was done during deployment.",
                    ),
                },
                required=["summary"],
            ),
        ),
    ]
)


class AIDeployAgent:
    """Gemini-powered deployment agent with full server capabilities."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-flash",
        max_commands: int = 50,
        command_timeout: int = 120,
    ) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_commands = max_commands
        self.command_timeout = command_timeout

    async def deploy(
        self,
        plan: DeploymentPlan,
        credentials: dict[str, dict],
        on_log: LogCallback = None,
    ) -> tuple[str | None, list[str]]:
        server = credentials.get("server", {})
        if not server:
            raise ValueError(
                "Server credentials are required for AI deployment. "
                "Use /set_server to configure them first."
            )

        ssh = SSHClient(command_timeout=self.command_timeout)
        try:
            await self._log(on_log, "🔌 Connecting to server...")
            await ssh.connect(
                host=server["host"],
                username=server["user"],
                key_path=server["ssh_key_path"],
            )
            await self._log(on_log, "✅ Connected! AI is now deploying your project...")
            return await self._agent_loop(ssh, plan, server, on_log)
        except Exception as exc:
            logger.exception("ai_deploy.failed")
            raise
        finally:
            await ssh.close()

    async def _agent_loop(
        self,
        ssh: SSHClient,
        plan: DeploymentPlan,
        server: dict,
        on_log: LogCallback,
    ) -> tuple[str | None, list[str]]:
        user_context = self._build_context(plan, server)
        logs: list[str] = []
        command_count = 0

        contents: list[types.Content] = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_context)],
            )
        ]

        config = types.GenerateContentConfig(
            tools=[_SSH_TOOL],
            system_instruction=_SYSTEM_PROMPT,
            temperature=0.1,
        )

        while command_count < self.max_commands:
            response = None
            for attempt in range(6):
                try:
                    response = await self.client.aio.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=config,
                    )
                    break
                except Exception as exc:
                    err_str = str(exc)
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                        wait_seconds = 8 + (attempt * 4)
                        logger.warning("ai_deploy.rate_limit_wait", attempt=attempt, wait_seconds=wait_seconds)
                        await self._log(on_log, f"⏳ Rate limit reached. Waiting {wait_seconds}s for quota cooldown...")
                        await asyncio.sleep(wait_seconds)
                    else:
                        raise

            if response is None or not response.candidates:
                logs.append("AI returned empty response or reached max retries.")
                break

            model_content = response.candidates[0].content
            contents.append(model_content)

            function_calls = [
                part for part in (model_content.parts or []) if part.function_call
            ]
            text_parts = [
                part.text
                for part in (model_content.parts or [])
                if part.text
            ]

            for text in text_parts:
                logs.append(text)

            if not function_calls:
                logs.append("AI finished without calling deployment_complete.")
                break

            response_parts: list[types.Part] = []
            for part in function_calls:
                fc = part.function_call
                if fc.name == "deployment_complete":
                    live_url = (fc.args or {}).get("live_url", "") or None
                    summary = (fc.args or {}).get("summary", "Deployment complete.")
                    logs.append(f"✅ {summary}")
                    await self._log(on_log, f"✅ Deployment complete!\n\n{summary}")
                    return live_url, logs

                if fc.name == "run_ssh_command":
                    command = (fc.args or {}).get("command", "")
                    command_count += 1
                    short_cmd = command[:80] + ("..." if len(command) > 80 else "")
                    await self._log(
                        on_log,
                        f"⚙️ [{command_count}/{self.max_commands}] `{short_cmd}`",
                    )
                    logs.append(f"$ {command}")

                    try:
                        stdout, stderr, exit_code = await ssh.run(command)
                    except CommandBlockedError as exc:
                        stdout, stderr, exit_code = "", str(exc), 1
                        logs.append(f"🚫 BLOCKED: {exc}")
                        await self._log(on_log, f"🚫 Blocked: {command[:60]}")

                    output_summary = self._summarise_output(stdout, stderr, exit_code)
                    logs.append(output_summary)

                    response_parts.append(
                        types.Part.from_function_response(
                            name="run_ssh_command",
                            response={
                                "stdout": stdout[-3000:],
                                "stderr": stderr[-1500:],
                                "exit_code": exit_code,
                            },
                        )
                    )
                else:
                    response_parts.append(
                        types.Part.from_function_response(
                            name=fc.name,
                            response={"error": f"Unknown function: {fc.name}"},
                        )
                    )

            contents.append(
                types.Content(role="user", parts=response_parts)
            )
            # Pacing delay to avoid exceeding free-tier requests-per-minute
            await asyncio.sleep(2.5)

        if command_count >= self.max_commands:
            logs.append(f"⚠️ Reached max command limit ({self.max_commands}).")
            await self._log(on_log, f"⚠️ Reached command limit ({self.max_commands}).")

        return None, logs

    @staticmethod
    def _build_context(plan: DeploymentPlan, server: dict) -> str:
        base_path = server.get("base_path", "/var/www")
        public_url = server.get("public_base_url", "")
        github = server.get("github", {})
        github_info = ""
        if github:
            github_info = f"\n**GitHub token available**: yes (use for private repos)"
        return (
            f"Deploy this project now. Do everything needed to make it live.\n\n"
            f"**Project**: {plan.project_name}\n"
            f"**Repository**: {plan.repo_url}\n"
            f"**Branch**: {plan.branch}\n"
            f"**Detected stack**: {plan.stack.value}\n"
            f"**Suggested build steps**: {', '.join(plan.build_steps)}\n"
            f"**Server base path**: {base_path}\n"
            f"**Public URL base**: {public_url}\n"
            f"**Target directory**: {base_path}/{plan.project_name}\n"
            f"{github_info}\n\n"
            f"Start by checking the server OS and what's already installed, "
            f"then proceed with the full deployment."
        )

    @staticmethod
    def _summarise_output(stdout: str, stderr: str, exit_code: int) -> str:
        parts = []
        if exit_code != 0:
            parts.append(f"exit_code={exit_code}")
        if stdout.strip():
            parts.append(f"stdout:\n{stdout.strip()[-500:]}")
        if stderr.strip():
            parts.append(f"stderr:\n{stderr.strip()[-300:]}")
        return " | ".join(parts) if parts else "(no output)"

    @staticmethod
    async def _log(on_log: LogCallback, message: str) -> None:
        if on_log is not None:
            try:
                await on_log(message)
            except Exception:
                pass


_GROQ_SYSTEM_PROMPT = """\
You are a senior DevOps deployment agent. Deploy the requested repository to the configured Linux server.

Rules:
- Use run_ssh_command for server work, one command at a time.
- Inspect the project before choosing the stack.
- Prefer existing server conventions and avoid breaking other apps.
- Use /var/www/<project_name> unless the project clearly needs another path.
- For static/SPAs, build and serve with nginx under the public base URL.
- For backend apps, create isolated env/service/database when needed.
- Verify with curl before calling deployment_complete.
- Keep commands efficient and do not dump large files or huge logs.
- If a command produces long output, ask only for the relevant tail or grep result next.
"""


_GROQ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_ssh_command",
            "description": (
                "Execute a shell command on the remote server via SSH. "
                "You have full sudo access. Returns stdout, stderr, and exit code. "
                "Run one command at a time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute. Use sudo when needed.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deployment_complete",
            "description": (
                "Signal that deployment is completely finished and the site is live. "
                "Only call this after verifying the URL works with curl."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "live_url": {
                        "type": "string",
                        "description": "The public URL where the deployed project is now accessible.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Detailed summary of everything that was done during deployment.",
                    },
                },
                "required": ["summary"],
            },
        },
    },
]


class GroqDeployAgent:
    """Groq-powered deployment agent using OpenAI-compatible tool calls."""

    endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        api_key: str,
        model: str = "openai/gpt-oss-120b",
        max_commands: int = 50,
        command_timeout: int = 120,
    ) -> None:
        if not api_key:
            raise ValueError("GROQ_API_KEY is required when AI_PROVIDER=groq.")

        self.api_key = api_key
        self.model = model
        self.max_commands = max_commands
        self.command_timeout = command_timeout

    async def deploy(
        self,
        plan: DeploymentPlan,
        credentials: dict[str, dict],
        on_log: LogCallback = None,
    ) -> tuple[str | None, list[str]]:
        server = credentials.get("server", {})
        if not server:
            raise ValueError(
                "Server credentials are required for AI deployment. "
                "Use /set_server to configure them first."
            )

        ssh = SSHClient(command_timeout=self.command_timeout)
        try:
            await AIDeployAgent._log(on_log, "🔌 Connecting to server...")
            await ssh.connect(
                host=server["host"],
                username=server["user"],
                key_path=server["ssh_key_path"],
            )
            await AIDeployAgent._log(on_log, "✅ Connected! AI is now deploying your project...")
            return await self._agent_loop(ssh, plan, server, on_log)
        finally:
            await ssh.close()

    async def _agent_loop(
        self,
        ssh: SSHClient,
        plan: DeploymentPlan,
        server: dict,
        on_log: LogCallback,
    ) -> tuple[str | None, list[str]]:
        logs: list[str] = []
        command_count = 0
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _GROQ_SYSTEM_PROMPT},
            {"role": "user", "content": AIDeployAgent._build_context(plan, server)},
        ]

        timeout = httpx.Timeout(max(60, self.command_timeout + 30))
        async with httpx.AsyncClient(timeout=timeout) as client:
            while command_count < self.max_commands:
                message = await self._complete(client, messages, on_log)
                content = message.get("content") or ""
                if content:
                    logs.append(content)

                tool_calls = message.get("tool_calls") or []
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": content,
                }
                if tool_calls:
                    assistant_message["tool_calls"] = tool_calls
                messages.append(assistant_message)

                if not tool_calls:
                    logs.append("AI finished without calling deployment_complete.")
                    break

                for tool_call in tool_calls:
                    function = tool_call.get("function") or {}
                    name = function.get("name", "")
                    args = self._decode_arguments(function.get("arguments"))

                    if name == "deployment_complete":
                        live_url = args.get("live_url") or None
                        summary = args.get("summary", "Deployment complete.")
                        logs.append(f"✅ {summary}")
                        await AIDeployAgent._log(on_log, f"✅ Deployment complete!\n\n{summary}")
                        return live_url, logs

                    if name == "run_ssh_command":
                        command_count += 1
                        command = str(args.get("command", ""))
                        short_cmd = command[:80] + ("..." if len(command) > 80 else "")
                        await AIDeployAgent._log(
                            on_log,
                            f"⚙️ [{command_count}/{self.max_commands}] `{short_cmd}`",
                        )
                        logs.append(f"$ {command}")

                        try:
                            stdout, stderr, exit_code = await ssh.run(command)
                        except CommandBlockedError as exc:
                            stdout, stderr, exit_code = "", str(exc), 1
                            logs.append(f"🚫 BLOCKED: {exc}")
                            await AIDeployAgent._log(on_log, f"🚫 Blocked: {command[:60]}")

                        logs.append(AIDeployAgent._summarise_output(stdout, stderr, exit_code))
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.get("id"),
                                "content": json.dumps(
                                    {
                                        "stdout": stdout[-1200:],
                                        "stderr": stderr[-600:],
                                        "exit_code": exit_code,
                                    }
                                ),
                            }
                        )
                        continue

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "content": json.dumps({"error": f"Unknown function: {name}"}),
                        }
                    )

                await asyncio.sleep(0.8)

        if command_count >= self.max_commands:
            logs.append(f"⚠️ Reached max command limit ({self.max_commands}).")
            await AIDeployAgent._log(on_log, f"⚠️ Reached command limit ({self.max_commands}).")

        return None, logs

    async def _complete(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
        on_log: LogCallback,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": self._compact_messages(messages),
            "tools": _GROQ_TOOLS,
            "tool_choice": "auto",
            "temperature": 0.1,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(5):
            try:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                if response.status_code in {429, 500, 502, 503, 504}:
                    wait_seconds = self._retry_wait(response, attempt)
                    await AIDeployAgent._log(
                        on_log,
                        f"⏳ AI provider is busy. Retrying in {wait_seconds}s...",
                    )
                    await asyncio.sleep(wait_seconds)
                    continue

                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                last_error = exc
                wait_seconds = min(2**attempt, 8)
                logger.warning("groq_deploy.retry", attempt=attempt, error=str(exc))
                await asyncio.sleep(wait_seconds)

        raise RuntimeError(f"Groq deployment request failed after retries: {last_error}")


    @classmethod
    def _compact_messages(cls, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fixed = messages[:2]
        tail = messages[2:]
        if len(tail) > 12:
            tail = tail[-12:]

        while tail and tail[0].get("role") == "tool":
            tail = tail[1:]

        return [cls._trim_message(message) for message in [*fixed, *tail]]

    @classmethod
    def _trim_message(cls, message: dict[str, Any]) -> dict[str, Any]:
        trimmed = dict(message)
        content = trimmed.get("content")
        role = trimmed.get("role")

        if isinstance(content, str):
            if role == "tool":
                trimmed["content"] = cls._trim_tool_content(content)
            else:
                trimmed["content"] = cls._trim_text(content, 1800)

        return trimmed

    @classmethod
    def _trim_tool_content(cls, content: str) -> str:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return cls._trim_text(content, 1200)

        if isinstance(payload, dict):
            if isinstance(payload.get("stdout"), str):
                payload["stdout"] = cls._trim_text(payload["stdout"], 900)
            if isinstance(payload.get("stderr"), str):
                payload["stderr"] = cls._trim_text(payload["stderr"], 450)
            return json.dumps(payload)

        return cls._trim_text(content, 1200)

    @staticmethod
    def _trim_text(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return f"...[trimmed {len(value) - limit} chars]\n{value[-limit:]}"

    @staticmethod
    def _decode_arguments(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @staticmethod
    def _retry_wait(response: httpx.Response, attempt: int) -> int:
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return min(int(retry_after), 30)
        return min(4 + (attempt * 3), 20)
