"""Central configuration, loaded from environment / .env.

Secrets never live in the repo — only here, sourced from the environment.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Identity ---
    megan_name: str = "Megan"
    owner_telegram_id: int | None = None

    # --- Telegram ---
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_phone: str | None = None
    telegram_session_name: str = "megan"
    telegram_string_session: str | None = None

    # --- Anthropic ---
    anthropic_api_key: str | None = None
    megan_classify_model: str = "claude-haiku-4-5"
    megan_reasoning_model: str = "claude-opus-4-8"
    megan_monthly_cost_cap_usd: float = 50.0

    # --- Transcription ---
    transcribe_provider: str = "openai"  # openai | local | none
    openai_api_key: str | None = None
    openai_whisper_model: str = "whisper-1"
    whisper_cpp_bin: str | None = None
    whisper_cpp_model: str | None = None

    # --- Postgres ---
    database_url: str = "postgresql://megan:megan@localhost:5432/megan"

    # --- Linear ---
    linear_api_key: str | None = None
    linear_default_team: str | None = None

    # --- Obsidian ---
    obsidian_vault_path: str = "./vault"
    obsidian_git_autocommit: bool = True

    # --- Proactivity / scheduling ---
    max_open_asks: int = Field(default=4, ge=1, le=10)
    quiet_hours_start: int = 23
    quiet_hours_end: int = 8
    work_hours_start: int = 9
    work_hours_end: int = 19
    timezone: str = "America/New_York"
    backlog_drip_minutes: int = 150

    # --- Agent monitoring ---
    monitor_ssh_key_path: str | None = None
    monitor_ssh_known_hosts: str | None = None

    # --- Misc ---
    log_level: str = "INFO"
    download_dir: str = "./downloads"

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash)

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so every module shares one parsed config."""
    return Settings()
