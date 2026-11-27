"""The inference service's HTTP side (REG-1.4, ADR-0016 rule 1).

Reachable only on the Compose network; clients go through the API's
``POST /api/v1/models/{version_id}/predict``, which forwards here. Errors are
the same RFC 9457 problem documents as the API's (ADR-0011), so the API can pass
them through unchanged.
"""

import json
from collections.abc import Iterator
from typing import Any, Literal, Protocol

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stratal.api.problem import install_error_handlers
from stratal.core.errors import LlmUnavailable, StratalError


class Predictor(Protocol):
    """What the app needs from :class:`stratal.inference.holder.ModelHolder`."""

    def predict(self, version_id: str, image: Image.Image) -> dict[str, Any]: ...
    def check_input(self, version_id: str, given: str) -> None: ...
    def predict_values(
        self, version_id: str, *, record: dict[str, Any] | None = None, text: str | None = None
    ) -> dict[str, Any]: ...
    def status(self) -> dict[str, Any]: ...


class Chatter(Protocol):
    """What the app needs from :class:`stratal.inference.llm.LlmAdapter` (ADR-0021)."""

    def start(
        self,
        messages: list[dict[str, Any]],
        *,
        route: str = "llm",
        extra: dict[str, Any] | None = None,
    ) -> Any: ...
    def status(self) -> dict[str, Any]: ...


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=60_000)


class ChatBody(BaseModel):
    """A conversation for the LLM, or a question for RAG, as the API builds it (ADR-0021
    rule 8, ADR-0022 rules 5 to 8)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["llm", "rag", "images"] = "llm"
    messages: list[ChatMessage] = Field(default_factory=lambda: list[ChatMessage](), max_length=64)
    question: str | None = Field(default=None, max_length=8_000)
    index_version_id: str | None = Field(default=None, pattern=r"^ds_[0-9a-f]{12}$")
    labelled_version_id: str | None = Field(default=None, pattern=r"^ds_[0-9a-f]{12}$")
    k: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def _complete(self) -> "ChatBody":
        if self.mode == "llm" and not self.messages:
            raise ValueError("llm mode needs messages")
        if self.mode == "rag" and (not self.question or not self.index_version_id):
            raise ValueError("rag mode needs a question and an index_version_id")
        if self.mode == "images" and (not self.question or not self.labelled_version_id):
            raise ValueError("images mode needs a question and a labelled_version_id")
        return self


class Images(Protocol):
    """What the app needs from :class:`stratal.inference.image_search.ImageSearch`."""

    def search(self, text: str, version_id: str, k: int = 10) -> Iterator[dict[str, Any]]: ...


class Rag(Protocol):
    """What the app needs from :class:`stratal.inference.rag.RagService`."""

    def start(
        self, question: str, index_version_id: str, k: int | None = None
    ) -> Iterator[dict[str, Any]]: ...


def _problem_event(exc: Exception) -> dict[str, Any]:
    error = exc if isinstance(exc, StratalError) else LlmUnavailable(f"The answer stopped: {exc}")
    return {
        "event": "error",
        "code": error.code,
        "title": error.title,
        "status": error.http_status,
        "detail": str(error),
        "suggested_action": error.suggested_action,
    }


def ndjson(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    """One JSON object per line; a failure after the first line becomes an ``error`` event,
    since the status line has been sent by then."""
    try:
        for event in events:
            yield json.dumps(event) + "\n"
    except Exception as exc:
        yield json.dumps(_problem_event(exc)) + "\n"


ImageDecoder = Any
"""``(image_b64=, blob=) -> PIL image``; :func:`stratal.inference.holder.decode_image`."""


class PredictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(pattern=r"^mv_[0-9a-z]{12}$")
    image_b64: str | None = Field(default=None, description="An image file, base64-encoded.")
    blob: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="A file of the data folder's blob store, by its SHA-256.",
    )
    record: dict[str, Any] | None = Field(
        default=None,
        description="A table model's input: one value per feature column, by name; null to "
        "impute (ADR-0019 rule 21).",
    )
    text: str | None = Field(default=None, description="A text model's input.")

    @model_validator(mode="after")
    def _one_input(self) -> "PredictBody":
        given = [n for n in ("image", "record", "text") if self._given(n)]
        if len(given) != 1:
            raise ValueError("send exactly one input: image_b64 or blob, record, or text")
        return self

    def _given(self, name: str) -> bool:
        if name == "image":
            return self.image_b64 is not None or self.blob is not None
        return getattr(self, name) is not None


def create_inference_app(
    holder: Predictor,
    decode: ImageDecoder,
    llm: Chatter | None = None,
    rag: Rag | None = None,
    images: Images | None = None,
) -> FastAPI:
    app = FastAPI(title="Stratal inference", docs_url=None, redoc_url=None)
    install_error_handlers(app)

    @app.post("/predict")
    def predict(body: PredictBody) -> dict[str, Any]:
        if body.record is not None or body.text is not None:
            return holder.predict_values(body.version_id, record=body.record, text=body.text)
        holder.check_input(body.version_id, "image")  # before reading an image it would refuse
        image = decode(image_b64=body.image_b64, blob=body.blob)
        return holder.predict(body.version_id, image)

    @app.get("/status")
    def status() -> dict[str, Any]:
        result = holder.status()
        if llm is not None:
            result["llm"] = llm.status()
        return result

    @app.post("/chat")
    def chat(body: ChatBody) -> StreamingResponse:
        """Stream one answer from the LLM (LLM-1.1). Queueing, the lease and the load happen
        before the response starts, so a refusal is an ordinary problem document."""
        if body.mode == "images":
            if images is None:
                raise LlmUnavailable("This inference service runs without image search.")
            assert body.question is not None and body.labelled_version_id is not None
            found = images.search(body.question, body.labelled_version_id, body.k or 10)
            return StreamingResponse(ndjson(found), media_type="application/x-ndjson")
        if body.mode == "rag":
            if rag is None:
                raise LlmUnavailable("This inference service runs without retrieval.")
            assert body.question is not None and body.index_version_id is not None
            events = rag.start(body.question, body.index_version_id, body.k)
            return StreamingResponse(ndjson(events), media_type="application/x-ndjson")
        if llm is None:
            raise LlmUnavailable("This inference service runs without the LLM.")
        generation = llm.start([m.model_dump() for m in body.messages])
        return StreamingResponse(ndjson(generation.events()), media_type="application/x-ndjson")

    return app
