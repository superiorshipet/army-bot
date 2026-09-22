from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    log_level: str = "info"

    telegram_bot_token: str
    super_admin_telegram_ids: str = Field(default="")

    database_url: str
    encryption_key: str

    workspace_root: Path = Path("/tmp/army-bot-workspaces")
    public_base_url: str = ""
    execution_mode: str = "dry_run"

    @cached_property
    def super_admin_ids(self) -> set[int]:
        return {
            int(value.strip())
            for value in self.super_admin_telegram_ids.split(",")
            if value.strip()
        }


settings = Settings()
