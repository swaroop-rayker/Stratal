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
    # NFR-11 benchmark only: false runs a job with no event emitter and no memory reporter.
    monitoring_enabled: bool = True
    # NFR-12: raw telemetry is kept this many days, then averaged per minute by `beat`.
    telemetry_keep_days: float = 7.0
    downsample_every_s: float = 3600.0
    # RUN-1.6 (OC-3): pause GPU jobs at this temperature, resume those below the second.
    thermal_pause_c: float = 83.0
    thermal_resume_c: float = 75.0
    # RUN-1.7 (OC-3): a running stage with no progress or activity for this long is stalled.
    stall_timeout_s: float = 300.0
    # MON-2.6 (OC-7): alert above these shares of the card's VRAM and the VM's RAM, and
    # MON-3.2: disk reads at least this fast count as the bottleneck (ADR-0020). Until the
    # monitoring section is saved on SCR-14.
    vram_alert_percent: float = 90.0
    ram_alert_percent: float = 85.0
    disk_bottleneck_mb_s: float = 50.0
    # Time a cancelled child gets to stop by itself before its process group is killed.
    cancel_grace_s: float = 10.0
    # The job's own reporter thread (process RSS, PyTorch allocator).
    process_sample_interval_s: float = 1.0
    # Approximate cap on the Redis event stream.
    event_stream_maxlen: int = 100_000
    # Where source folders are mounted in the api and worker containers.
    sources_dir: Path = Path("/sources")
    # REG-1.4, ADR-0016: the inference service, reached by the API on the Compose network.
    inference_url: str = "http://inference:8100"
    inference_port: int = 8100
    # Decision 2: below this many parameters a model runs on the CPU while a job holds the
    # GPU; at or above it the request is refused with gpu_busy. 50 M is about 200 MB of fp32.
    inference_cpu_max_parameters: int = 50_000_000
    # Rule 5: the lease goes back after this long without a GPU request.
    inference_idle_release_s: float = 60.0
    # How often the service checks for idleness and for a waiting job.
    inference_check_interval_s: float = 1.0
    # How long the API waits for a prediction, including a model load on the CPU.
    inference_timeout_s: float = 120.0
    # LLM serving (LLM-1.1, ADR-0021). Reached only from `inference`, on the `llm` network.
    ollama_url: str = "http://ollama:11434"
    llm_model: str = "qwen3-1.7b-q4km"
    """The allow-list name of the LLM's GGUF file (M12 decision 1)."""
    llm_timeout_s: float = 30.0
    """How long a single read from Ollama may take before the answer is llm_unavailable."""
    llm_vram_estimate_mb: float = 1900.0
    """Free VRAM needed before loading: the model and a 4,096-token cache (S-11: 1,657 MiB)."""
    llm_unload_wait_s: float = 10.0
    llm_baseline_margin_mb: float = 64.0
    llm_max_waiting: int = 4
    llm_num_ctx: int = 4096
    llm_max_tokens: int = 512
    llm_temperature: float = 0.3


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, read once."""
    return Settings()
