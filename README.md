# Army Deploy Bot

Army Deploy is a Telegram-first deployment assistant for users who may only have a phone and limited internet. An approved user configures a target server once, sends a Git repository URL, and follows deployment status from Telegram.

The current release is an MVP for trusted users and controlled servers.

## Current Capabilities

- Admin-controlled user access and a button-first Telegram interface.
- Encrypted per-user credentials.
- Automatic stack selection for static, Vite/React, Node.js, Python, Laravel, .NET, and Spring Boot.
- Isolated release directories with shared environment/runtime state.
- Namespaced project targets that prevent same-name collisions.
- Queued, planning, running, successful, failed, and cancelled states.
- Duplicate target protection and a /cancel command.
- Sanitized deployment logs.
- SQLite locally and PostgreSQL in production.
- Versioned database migrations and schema constraints.

## Simple Mobile Flow

1. Open the bot and press Start.
2. Wait for admin approval.
3. Open Setup credentials and save the required values.
4. Press Deploy project.
5. Paste an HTTP(S) Git repository URL and optional branch.
6. Follow deployment progress in the same Telegram message.
7. Press Cancel deployment in the progress message, or send /cancel.
8. Open Projects or Status to review results.

No terminal is required for the normal user flow.

## Architecture

~~~text
presentation/telegram    Telegram routers, keyboards, and messages
application/use_cases    Access, credentials, and deployment lifecycle
application/ports        Repository and executor contracts
domain                   Entities, statuses, and stack definitions
infrastructure/database  SQLite/PostgreSQL persistence and migrations
infrastructure/deploy    Analyzer, coordinator, AI, recipes, and SSH
shared                   Settings, logging, and secret redaction
~~~

The deterministic recipe executor is the default for Groq recipe deployments. The AI executor remains available for the MVP and keeps its current SSH/sudo behavior.

## Repository Analysis

The bot accepts HTTP or HTTPS Git repository URLs. Local filesystem paths are rejected. For monorepos, the analyzer scores the root and common application folders such as client, frontend, web, backend, and api. Every analysis uses a unique temporary workspace.

## Release Model

Deployments no longer update a live Git checkout with git reset --hard. Each deployment is cloned into:

~~~text
<base_path>/.army-projects/<project-target>/releases/<release-id>
~~~

Environment and runtime state live under the shared directory. A successful release becomes the current symlink, and the latest three releases are retained. Targets include a project ID suffix so similar repository names cannot overwrite each other.

## Credentials

Credentials remain optimized for the phone-first MVP:

- GitHub token
- server host, user, SSH key path, base path, and public base URL
- optional Cloudflare token/account/zone

Payloads are encrypted with Fernet. Sensitive values are redacted from stored deployment logs. The bot's own Groq/OpenAI keys are never copied into deployed application environments.

## Setup

Requirements: Python 3.12, PostgreSQL for production or SQLite locally, Telegram token, Fernet key, and SSH connectivity from the bot host.

~~~bash
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
python -m armybot
~~~

Generate an encryption key:

~~~bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
~~~

Important configuration:

~~~env
TELEGRAM_BOT_TOKEN=
SUPER_ADMIN_TELEGRAM_IDS=
DATABASE_URL=
ENCRYPTION_KEY=
WORKSPACE_ROOT=/tmp/army-bot-workspaces
PUBLIC_BASE_URL=
EXECUTION_MODE=dry_run
AI_PROVIDER=groq
AI_DEPLOY_STRATEGY=recipe
AI_COMMAND_TIMEOUT=900
~~~

Execution modes are dry_run, local, and ai. With Groq plus recipe strategy, deployments use deterministic recipes.

## Telegram Commands

User:

~~~text
/start
/setup
/deploy <repo_url> [branch]
/cancel
/projects
/status
~~~

Credential fallbacks retained for the MVP:

~~~text
/set_github <token>
/set_cloudflare <token> [account_id] [zone_id]
/set_server <host> <user> <ssh_key_path> <base_path> [public_base_url]
~~~

Admin:

~~~text
/admin
/users
/pending
/approve <telegram_id>
/reject <telegram_id>
/suspend <telegram_id>
~~~

## Quality Checks

~~~bash
pytest -q
ruff check src tests
python -m compileall -q src tests
~~~

Exact dependency versions are stored in requirements.lock.

## Production Notes

- Run one bot process while the deployment coordinator is in memory.
- Use PostgreSQL and back up the database with the encryption key.
- Restrict the MVP to trusted users.
- Monitor the bot service, Nginx, deployment services, and disk usage.
- Database deployment records remain after restart, but active in-memory tasks do not resume automatically.

## Intentional MVP Decisions

To keep onboarding usable from a phone, this release keeps the current credential entry flow. The AI adapter also keeps its current SSH/sudo capability. These are explicit MVP tradeoffs and must be revisited before allowing untrusted public users.
