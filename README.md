# Army Deploy Bot

Telegram AI DevOps bot foundation.

The bot is designed as a multi-user deployment assistant:

- users request access through Telegram
- super admin approves or rejects users
- each approved user stores encrypted credentials
- users can ask the bot to deploy repositories
- deployments are versioned in the database
- dangerous operations stay outside the default MVP path

## Architecture

```text
presentation/telegram  -> Telegram commands and callback buttons
application/use_cases  -> business workflows
application/ports      -> repository and service contracts
domain                 -> entities and enums
infrastructure         -> database, encryption, deployment adapters
```

## Setup

```bash
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

Generate encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Run:

```bash
python -m armybot
```

## Telegram Commands

User:

```text
/start
/setup
/set_github <token>
/set_cloudflare <token>
/set_server <host> <user> <ssh_key_path> <base_path>
/deploy <repo_url> [branch]
/projects
/status
```

Super admin:

```text
/admin
/pending
/approve <telegram_id>
/reject <telegram_id>
/suspend <telegram_id>
```

## MVP Notes

The first version intentionally avoids destructive operations. Deploy execution starts in
`dry_run` mode until real deploy adapters are enabled.
