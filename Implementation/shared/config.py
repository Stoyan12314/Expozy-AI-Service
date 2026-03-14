"""
Configuration bridge — shared/config.py

WHY THIS FILE EXISTS
--------------------
All configuration values live in the .env file. This file does not define any
values — it only declares what variables are expected and what types they should be.

WHY NOT JUST USE .env DIRECTLY
-------------------------------
Python code cannot read a .env file natively. The two common alternatives are:

1. os.environ["KEY"] — reads raw strings from the environment, no type conversion,
   no validation, no IDE autocomplete. A missing key raises a cryptic KeyError at
   the exact moment the code runs, not at startup. A typo in a key name goes
   unnoticed until that code path is hit in production.

2. This file — pydantic-settings reads the .env file once at startup, converts each
   value to the declared type (str, int, float, PostgresDsn, etc.), and raises a
   clear validation error immediately if anything is missing or malformed. Every
   setting is then accessible as a typed attribute (settings.telegram_bot_token)
   with full IDE support and no risk of KeyError at runtime.

HOW IT WORKS
------------
- Settings() loads values from .env automatically via pydantic-settings.
- get_settings() is cached with @lru_cache so the .env file is only read once.
- Any file in the codebase that needs a config value imports get_settings()
  and accesses it as settings.some_value.
- No defaults are defined here — if a value is missing from .env, the app
  refuses to start with a clear error listing exactly which variables are absent.

"""

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, AmqpDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: PostgresDsn

    # RabbitMQ
    rabbitmq_url: AmqpDsn
    job_queue_name: str

    # Telegram
    telegram_bot_token: str
    telegram_secret_token: str
    telegram_send_message_url: str

    # AI Provider (DashScope / Alibaba Cloud)
    ai_provider: str
    ai_model: str
    ai_timeout: float

    # DashScope config
    dashscope_api_key: str
    dashscope_api_url: str

    # Worker
    max_retries: int
    retry_base_delay: float
    retry_max_delay: float

    # Preview
    previews_path: str
    preview_base_url: str

    # Expozy
    core_saas_telegram_url: str
    core_login_telegram_url: str
    expozy_admin_login_url: str
    expozy_store_domain: str

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    log_format: Literal["json", "console"]


@lru_cache
def get_settings() -> Settings:
    return Settings()