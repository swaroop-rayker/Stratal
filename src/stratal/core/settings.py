"""Process settings, read from ``STRATAL_*`` environment variables (SPEC §7.1)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Everything a Stratal process needs to know about its surroundings."""

    model_config = SettingsConfigDict(env_prefix="STRATAL_", extra="ignore")

    database_url: str = "postgresql+psycopg://stratal:stratal@postgres:5432/stratal"
    redis_url: str = "redis://redis:6379/0"
    data_dir: Path = Path("/data")
    log_level: LogLevel = "INFO"
    # Bound to all interfaces inside the container; Compose publishes it on 127.0.0.1 only.
    api_host: str = "0.0.0.0"
    api_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, read once."""
    return Settings()
