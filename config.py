"""
Application configuration loaded from environment variables.
"""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Clinical Data Reconciliation Engine"
    app_version: str = "1.0.0"
    debug: bool = False

    # API authentication
    api_key: str = os.getenv("API_KEY", "dev-key-change-in-production")

    # Anthropic Claude configuration
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = "claude-sonnet-4-20250514"
    anthropic_max_tokens: int = 2048
    anthropic_timeout: int = 30

    # Cache settings
    cache_ttl_seconds: int = 3600  # 1 hour
    cache_max_entries: int = 500

    # Rate limiting
    llm_requests_per_minute: int = 30

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
