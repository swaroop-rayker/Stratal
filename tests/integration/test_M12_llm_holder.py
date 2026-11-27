# pyright: basic, reportMissingImports=false
"""ADR-0021 rules 3 to 5: the LLM shares the inference service's one GPU slot and lease.

The real model holder and the real GPU lease in PostgreSQL, with Ollama and the card
faked (``stratal.testing.fake_llm``). Needs PyTorch (the holder imports it), so it runs
in the GPU worker's image like the M8 inference tests.
"""

import threading
from pathlib import Path
from typing import Any

import pytest

torch = pytest.importorskip("torch")

from stratal.core.bus import Bus  # noqa: E402
from stratal.core.errors import GpuBusy  # noqa: E402
from stratal.core.settings import get_settings  # noqa: E402
from stratal.db.engine import get_engine  # noqa: E402
from stratal.inference.holder import ModelHolder  # noqa: E402
from stratal.inference.llm import LlmAdapter  # noqa: E402
from stratal.pretrained.catalog import STAMP, get_model, model_dir  # noqa: E402
from stratal.runtime.gpu_lease import HeldLease  # noqa: E402
from stratal.testing.fake_llm import FakeCard, FakeOllama  # noqa: E402
from stratal.testing.fakes import ListSink  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.torch]


class AvailableGpu:
    """A card the holder believes in; nothing is placed on it in these tests."""

    tensor_device = "cpu"

    def available(self) -> bool:
        return True


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    entry = get_model("qwen3-1.7b-q4km")
    folder = model_dir(tmp_path / "models", entry)
    folder.mkdir(parents=True)
    (folder / entry.files[0].path).write_bytes(b"gguf")
    (folder / STAMP).write_text("{}", encoding="utf-8")
    settings = get_settings().model_copy(
        update={"data_dir": tmp_path, "inference_idle_release_s": 0.0, "llm_unload_wait_s": 0.3}
    )
    bus = Bus(settings.redis_url, timeout_s=2.0)
    holder = ModelHolder(engine=get_engine(), settings=settings, bus=bus, gpu=AvailableGpu())  # type: ignore[arg-type]
    api, card = FakeOllama(), FakeCard()
    api.card = card
    llm = LlmAdapter(api=api, owner=holder, settings=settings, card=card, sink=ListSink())
    holder.attach_llm(llm)
    yield holder, llm, api, card
    holder.shutdown()


def _answer(llm: LlmAdapter) -> list[dict[str, Any]]:
    return list(llm.start([{"role": "user", "content": "Hi"}]).events())


def test_LLM_1_1_the_llm_takes_the_lease_and_a_job_cannot(service: Any) -> None:
    holder, llm, *_ = service
    assert _answer(llm)[-1]["event"] == "done"
    assert holder.status()["lease_held"] is True
    job = HeldLease(get_engine(), holder="job_9001")
    assert job.try_acquire() is False
    job.release()


def test_BUG6_idle_release_unloads_the_llm_and_frees_the_lease(service: Any) -> None:
    holder, llm, api, card = service
    _answer(llm)
    assert holder.tick() == "idle"
    assert llm.loaded is False and card.used == card.base
    assert ("unload", llm.name) in api.calls
    job = HeldLease(get_engine(), holder="job_9002")
    assert job.try_acquire() is True  # a queued job gets the GPU
    job.release()


def test_BUG6_the_answer_in_progress_finishes_before_the_release(service: Any) -> None:
    holder, llm, api, _ = service
    api.gate = threading.Event()
    events = llm.start([{"role": "user", "content": "Hi"}]).events()
    next(events)
    assert holder.tick() is None  # busy: the lease stays
    assert holder.status()["lease_held"] is True
    api.gate.set()
    list(events)
    assert holder.tick() == "idle"


def test_BUG6_vram_not_given_back_keeps_the_lease(service: Any) -> None:
    holder, llm, _, card = service
    _answer(llm)
    card.stuck = True
    assert holder.tick() is None
    assert holder.status()["lease_held"] is True  # no job loads onto a full card
    card.stuck = False
    card.used = card.base
    assert holder.tick() == "idle"


def test_LLM_1_1_gpu_busy_names_the_job(service: Any) -> None:
    _, llm, api, _ = service
    job = HeldLease(get_engine(), holder="job_0131")
    assert job.try_acquire()
    try:
        with pytest.raises(GpuBusy, match="job_0131"):
            llm.start([{"role": "user", "content": "Hi"}])
        assert not any(c[0] == "load" for c in api.calls)
    finally:
        job.release()
