"""LLM-1.1, NFR-6 (ADR-0021 rules 8 and 9): ``POST /chat`` through the API, with the real
database and the inference app in process (the real adapter, Ollama faked).

The live model is checked in ``tests/gpu/``; here the stream, what is stored, the
context each mode sends, refusals and a stopped answer.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stratal.api import chat as chat_api
from stratal.api.app import create_app
from stratal.core.bus import Bus
from stratal.core.errors import GpuBusy
from stratal.core.settings import Settings
from stratal.db.engine import get_engine
from stratal.db.models import ChatMessage, InferenceRequest
from stratal.inference.app import create_inference_app
from stratal.inference.llm import LlmAdapter
from stratal.pretrained.catalog import STAMP, get_model, model_dir
from stratal.testing.fake_llm import FakeCard, FakeOllama

pytestmark = [pytest.mark.integration]


class Owner:
    def __init__(self) -> None:
        self.busy_with: str | None = None

    def claim_gpu_for_llm(self) -> None:
        if self.busy_with:
            raise GpuBusy(f"The GPU is in use by {self.busy_with}.")

    def note_gpu_use(self) -> None:
        pass


class NoPredictor:
    def status(self) -> dict[str, Any]:
        return {}


class Chat:
    def __init__(self, tmp_path: Path) -> None:
        entry = get_model("qwen3-1.7b-q4km")
        folder = model_dir(tmp_path / "models", entry)
        folder.mkdir(parents=True)
        (folder / entry.files[0].path).write_bytes(b"gguf")
        (folder / STAMP).write_text("{}", encoding="utf-8")
        self.ollama, self.card, self.owner = FakeOllama(), FakeCard(), Owner()
        self.ollama.card = self.card
        settings = Settings(data_dir=tmp_path, llm_unload_wait_s=0.2, inference_timeout_s=2.0)
        self.llm = LlmAdapter(api=self.ollama, owner=self.owner, settings=settings, card=self.card)
        inference = TestClient(
            create_inference_app(NoPredictor(), lambda **_: None, self.llm),  # type: ignore[arg-type]
            raise_server_exceptions=False,
        )

        class InProcess:
            def __init__(self, body: dict[str, Any]) -> None:
                self._response = inference.post("/chat", json=body)
                self.problem = None if self._response.status_code == 200 else self._response.json()

            def lines(self) -> Iterator[dict[str, Any]]:
                for line in self._response.text.splitlines():
                    if line.strip():
                        yield json.loads(line)

            def close(self) -> None:
                pass

        app = create_app()
        app.dependency_overrides[chat_api.get_chat_forward] = lambda: InProcess
        self.client = TestClient(app)

    def ask(self, message: str, **body: Any) -> list[tuple[str, dict[str, Any]]]:
        response = self.client.post(
            "/api/v1/chat", json={"mode": "llm", "message": message, **body}
        )
        assert response.status_code == 200, response.text
        events: list[tuple[str, dict[str, Any]]] = []
        name = ""
        for line in response.text.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
        return events


@pytest.fixture
def chat(bus: Bus, tmp_path: Path) -> Chat:
    return Chat(tmp_path)


def test_LLM_1_1_chat_streams_and_stores_the_answer(chat: Chat) -> None:
    events = chat.ask("What is a learning rate?")
    assert [n for n, _ in events] == ["session", "meta", "token", "token", "done"]
    session_id = events[0][1]["session_id"]
    assert session_id.startswith("cs_")
    view = chat.client.get(f"/api/v1/chat/sessions/{session_id}").json()
    assert view["title"] == "What is a learning rate?"
    user, answer = view["messages"]
    assert (user["role"], answer["role"]) == ("user", "assistant")
    assert answer["content"] == "Hello there."
    timings = answer["request"]
    assert timings["status"] == "ok" and timings["cold_start"] is True
    assert timings["model"] == "qwen3-1.7b-q4km" and timings["device"] == "gpu"
    assert timings["output_tokens"] == 3 and timings["tokens_per_s"] == 100.0
    listed = chat.client.get("/api/v1/chat/sessions").json()["items"]
    assert [(s["id"], s["mode"]) for s in listed] == [(session_id, "llm")]


def test_LLM_1_1_llm_mode_sends_the_earlier_turns(chat: Chat) -> None:
    session_id = chat.ask("First question")[0][1]["session_id"]
    chat.ask("Second question", session_id=session_id)
    sent = [c[1] for c in chat.ollama.calls if c[0] == "chat"][-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "First question" and sent[2]["content"] == "Hello there."
    assert sent[-1]["content"] == "Second question"


def test_LLM_1_1_gpu_busy_is_an_error_event_and_stored_as_refused(chat: Chat) -> None:
    chat.owner.busy_with = "job_0131"
    events = chat.ask("Hello")
    assert [n for n, _ in events] == ["session", "error"]
    problem = events[1][1]
    assert problem["code"] == "gpu_busy" and "job_0131" in problem["detail"]
    with Session(get_engine()) as session:
        row = session.scalars(select(InferenceRequest)).one()
        assert (row.status, row.error_code) == ("refused", "gpu_busy")
        answer = session.scalars(select(ChatMessage).where(ChatMessage.role == "assistant")).one()
        assert answer.details is not None and answer.details["error"]["code"] == "gpu_busy"


def test_LLM_1_1_other_modes_wait_for_their_build(chat: Chat) -> None:
    response = chat.client.post("/api/v1/chat", json={"mode": "rag", "message": "Hi"})
    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["body", "mode"]


def test_NFR_6_deleting_a_conversation_keeps_its_timings(chat: Chat) -> None:
    session_id = chat.ask("Hello")[0][1]["session_id"]
    assert chat.client.delete(f"/api/v1/chat/sessions/{session_id}").status_code == 204
    assert chat.client.get(f"/api/v1/chat/sessions/{session_id}").status_code == 404
    with Session(get_engine()) as session:
        assert session.scalar(select(func.count()).select_from(ChatMessage)) == 0
        row = session.scalars(select(InferenceRequest)).one()
        assert row.session_id is None and row.status == "ok"
