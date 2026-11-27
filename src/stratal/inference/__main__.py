"""``python -m stratal.inference``: the inference service (REG-1.4, ADR-0016).

Starts the model holder, its background check (idle release, waiting jobs), and
the HTTP app on ``inference_port``. It holds no model until the first request.
"""

import functools
import signal
import sys
import threading
from types import FrameType
from typing import Any

import uvicorn

from stratal.core.bus import Bus
from stratal.core.logging import configure_logging, get_logger
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.inference.app import create_inference_app
from stratal.inference.holder import ModelHolder, decode_image
from stratal.inference.llm import LlmAdapter, NvmlCard, OllamaHttp
from stratal.inference.rag import RagService, Retriever
from stratal.mon.emitter import StreamEmitter
from stratal.mon.sink import StreamSink


def _prepare_llm(llm: LlmAdapter, log: Any) -> None:
    """Create Ollama's model from the verified file at start-up, so the first chat does not
    wait for the copy (ADR-0021 rule 2). A failure is retried by the first chat."""
    try:
        llm.ensure_model()
    except Exception as exc:
        log.warning("llm_prepare_failed", reason=str(exc))


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, sys.stderr)
    log = get_logger("stratal.inference")
    bus = Bus(settings.redis_url, timeout_s=2.0)
    # Its loads and unloads reach the event log with no job, for SCR-13 (MON-3.3).
    events = StreamEmitter(
        redis_url=settings.redis_url, spool_path=None, maxlen=settings.event_stream_maxlen
    )
    holder = ModelHolder(engine=get_engine(), settings=settings, bus=bus, sink=StreamSink(events))
    bus.set_inference_loaded(None)  # whatever a previous process held is gone with it
    llm = LlmAdapter(
        api=OllamaHttp(settings.ollama_url, settings.llm_timeout_s),
        owner=holder,
        settings=settings,
        card=NvmlCard(),
        sink=StreamSink(events),
    )
    holder.attach_llm(llm)
    rag = RagService(
        retriever=Retriever(engine=get_engine(), data_dir=settings.data_dir),
        llm=llm,
        engine=get_engine(),
        sink=StreamSink(events),
    )
    threading.Thread(target=_prepare_llm, args=(llm, log), daemon=True).start()
    stop = threading.Event()
    checks = threading.Thread(target=holder.run_checks, args=(stop,), daemon=True)
    checks.start()

    def _stop(signum: int, _frame: FrameType | None) -> None:
        log.info("inference_stopping", signal=signum)
        stop.set()
        holder.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    app = create_inference_app(
        holder, functools.partial(decode_image, data_dir=settings.data_dir), llm, rag
    )
    log.info("inference_started", port=settings.inference_port, status=holder.status())
    uvicorn.run(app, host="0.0.0.0", port=settings.inference_port, log_config=None)
    stop.set()
    holder.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
