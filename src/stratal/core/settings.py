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

    # MON-2.2: sampling interval while a job is active, and while idle.
    sample_interval_active_s: float = 1.0
    sample_interval_idle_s: float = 10.0
    # Supervisor heartbeat, and how stale it may get before the watchdog acts.
    heartbeat_interval_s: float = 5.0
    heartbeat_stale_after_s: float = 30.0
    watchdog_interval_s: float = 5.0
    # How often a job may be requeued after its worker was lost before it is failed.
    max_attempts: int = 3
    # Time a cancelled child gets to stop by itself before its process group is killed.
    cancel_grace_s: float = 10.0
    # The job's own reporter thread (process RSS, PyTorch allocator).
    process_sample_interval_s: float = 1.0
    # Approximate cap on the Redis event stream.
    event_stream_maxlen: int = 100_000
    # Where source folders are mounted in the api and worker containers.
    sources_dir: Path = Path("/sources")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, read once."""
    return Settings()
