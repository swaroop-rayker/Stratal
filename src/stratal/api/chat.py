"""Chat on SCR-12 (LLM-1.1; ADR-0021 rules 8 and 9; SCREENS.md v5).

``POST /chat`` stores the person's message, forwards the conversation to the
``inference`` service, and relays its answer as server-sent events:

* ``session`` first, with the conversation's ID (new or given) and the message's;
* then what ``inference`` streams: ``meta`` (route, model, device, cold start),
  ``token`` events, ``citations`` (RAG), ``done`` with the timings;
* or ``error``, a problem document (ADR-0011), when the request is refused or fails.

When the answer ends, stops (the client went away) or fails, the answer and one
``inference_requests`` row with its timings are stored (NFR-6). ``api`` keeps no ML
library and no HTTP client dependency: it uses the standard library's.

Context (open point 8b): *LLM* sends the conversation's last turns, up to about
1,000 tokens; *RAG* and *Images* send the message only.
"""

import json
import secrets
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stratal.api.streams import sse
from stratal.core.errors import DependencyUnavailable, NotFound, ValidationFailed
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.db.models import ChatMessage, ChatSession, InferenceRequest

router = APIRouter(tags=["chat"])
Json = dict[str, Any]

SYSTEM_PROMPT = (
    "You are Stratal's assistant, running locally. Answer briefly and plainly. "
    "If you do not know, say so."
)
HISTORY_CHARS = 4_000
"""About 1,000 tokens of earlier turns in *LLM* mode (open point 8b; ~4 characters a token)."""
REFUSALS = frozenset(
    {"gpu_busy", "llm_busy", "vram_insufficient", "cuda_unavailable", "no_image_embeddings"}
)


# ---------------------------------------------------------------------------- to inference


class Upstream(Protocol):
    """The answer coming back from ``inference``: a problem, or a stream of events."""

    problem: Json | None

    def lines(self) -> Iterator[Json]: ...
    def close(self) -> None: ...


ChatForward = Callable[[Json], Upstream]


class _HttpUpstream:
    def __init__(self, body: Json) -> None:
        settings = get_settings()
        self.problem: Json | None = None
        self._response: Any = None
        request = urllib.request.Request(
            f"{settings.inference_url}/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            self._response = urllib.request.urlopen(request, timeout=settings.inference_timeout_s)
        except urllib.error.HTTPError as exc:
            try:
                self.problem = dict(json.loads(exc.read()))
            except ValueError:
                self.problem = {"code": "internal_error", "detail": f"HTTP {exc.code}"}
            self.problem.setdefault("status", exc.code)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise DependencyUnavailable(
                f"The inference service at {settings.inference_url} did not answer ({exc}).",
                suggested_action="Start it with the GPU profile: docker compose --profile gpu "
                "up -d inference ollama",
            ) from None

    def lines(self) -> Iterator[Json]:
        if self._response is None:
            return
        for line in self._response:
            if line.strip():
                yield dict(json.loads(line))

    def close(self) -> None:
        if self._response is not None:
            self._response.close()


def get_chat_forward() -> ChatForward:
    """Dependency, so tests can call the inference app in process."""
    return _HttpUpstream


# ---------------------------------------------------------------------------- the request


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(
        default=None, pattern=r"^cs_[0-9a-f]{12}$", description="Continue this conversation."
    )
    mode: Literal["llm", "rag", "images"] = Field(
        description="LLM answers from the model alone; RAG from an index version, with "
        "citations; Images searches a labelled version (SCR-12, SCREENS.md v5)."
    )
    message: str = Field(min_length=1, max_length=8_000)
    index_version_id: str | None = Field(default=None, pattern=r"^ds_[0-9a-f]{12}$")
    labelled_version_id: str | None = Field(default=None, pattern=r"^ds_[0-9a-f]{12}$")


@dataclass
class _Turn:
    """What one answer produced, stored when it ends however it ends."""

    session_id: str
    mode: str
    meta: Json = field(default_factory=lambda: Json())
    done: Json | None = None
    error: Json | None = None
    text: list[str] = field(default_factory=lambda: list[str]())
    citations: list[Any] | None = None
    details: Json = field(default_factory=lambda: Json())

    def see(self, name: str, data: Json) -> None:
        if name == "meta":
            self.meta = data
        elif name == "token":
            self.text.append(str(data.get("text", "")))
        elif name == "citations":
            self.citations = list(data.get("citations") or [])
            self.details.update({k: v for k, v in data.items() if k != "citations"})
        elif name == "done":
            self.done = data
        elif name == "error":
            self.error = data

    @property
    def status(self) -> str:
        if self.error is not None:
            return "refused" if self.error.get("code") in REFUSALS else "failed"
        return "ok" if self.done is not None else "stopped"


def _new_session_id() -> str:
    return f"cs_{secrets.token_hex(6)}"


def _prepare(body: ChatRequest) -> tuple[str, int, list[Json]]:
    """Store the person's message; return the conversation, its ID and the messages
    ``inference`` gets."""
    with Session(get_engine()) as session, session.begin():
        if body.session_id is not None:
            chat = session.get(ChatSession, body.session_id)
            if chat is None:
                raise NotFound(
                    f"Conversation {body.session_id} was deleted or never existed.",
                    suggested_action="Start a new chat.",
                )
        else:
            chat = ChatSession(id=_new_session_id(), title=body.message.strip()[:80])
            session.add(chat)
            session.flush()
        history: list[Json] = []
        if body.mode == "llm":
            earlier = session.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == chat.id)
                .order_by(ChatMessage.id.desc())
                .limit(40)
            ).all()
            used = 0
            for message in earlier:
                if not message.content.strip() or message.mode != "llm":
                    continue
                used += len(message.content)
                if used > HISTORY_CHARS:
                    break
                history.insert(0, {"role": message.role, "content": message.content})
        message = ChatMessage(session_id=chat.id, role="user", mode=body.mode, content=body.message)
        session.add(message)
        chat.updated_at = func.now()  # type: ignore[assignment]
        session.flush()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": body.message},
        ]
        return chat.id, int(message.id), messages


def _store(turn: _Turn) -> None:
    timings = turn.done or {}
    error_code = None if turn.error is None else str(turn.error.get("code"))
    with Session(get_engine()) as session, session.begin():
        row = InferenceRequest(
            kind=turn.mode,
            session_id=turn.session_id,
            route=str(turn.meta.get("route") or turn.mode),
            model=turn.meta.get("model"),
            revision=turn.meta.get("revision"),
            device=turn.meta.get("device"),
            index_version_id=turn.meta.get("index_version_id"),
            cold_start=bool(turn.meta.get("cold_start")),
            load_ms=turn.meta.get("load_ms"),
            ttft_ms=timings.get("ttft_ms"),
            total_ms=timings.get("total_ms"),
            prompt_tokens=timings.get("prompt_tokens"),
            output_tokens=timings.get("output_tokens"),
            tokens_per_s=timings.get("tokens_per_s"),
            status=turn.status,
            error_code=error_code,
        )
        session.add(row)
        session.flush()
        details = dict(turn.details)
        if turn.status == "stopped":
            details["stopped"] = True
        if turn.error is not None:
            details["error"] = turn.error
        session.add(
            ChatMessage(
                session_id=turn.session_id,
                role="assistant",
                mode=turn.mode,
                content="".join(turn.text),
                citations=turn.citations,
                details=details or None,
                request_id=row.id,
            )
        )
        chat = session.get(ChatSession, turn.session_id)
        if chat is not None:
            chat.updated_at = func.now()  # type: ignore[assignment]


def _payload(body: ChatRequest, messages: list[Json]) -> Json:
    """What ``inference`` is sent: the conversation for *LLM*, the question alone for *RAG*
    (open point 8b), so a RAG answer's sources are its own."""
    if body.mode == "rag":
        return {"mode": "rag", "question": body.message, "index_version_id": body.index_version_id}
    return {"mode": "llm", "messages": messages}


async def _relay(
    request: Request, forward: ChatForward, body: ChatRequest, prepared: tuple[str, int, list[Json]]
) -> AsyncIterator[str]:
    session_id, message_id, messages = prepared
    turn = _Turn(session_id=session_id, mode=body.mode)
    upstream: Upstream | None = None
    try:
        yield sse("session", {"session_id": session_id, "message_id": message_id})
        try:
            upstream = await run_in_threadpool(forward, _payload(body, messages))
        except DependencyUnavailable as exc:
            problem = {
                "code": exc.code,
                "detail": str(exc),
                "suggested_action": exc.suggested_action,
            }
            turn.see("error", problem)
            yield sse("error", problem)
            return
        if upstream.problem is not None:
            turn.see("error", upstream.problem)
            yield sse("error", upstream.problem)
            return
        events = upstream.lines()
        while True:
            if await request.is_disconnected():
                break
            event = await run_in_threadpool(next, events, None)
            if event is None:
                break
            name = str(event.pop("event", "token"))
            turn.see(name, event)
            yield sse(name, event)
    finally:
        if upstream is not None:
            upstream.close()
        _store(turn)  # the stream may have been cancelled: store without awaiting


@router.post(
    "/chat",
    responses={200: {"content": {"text/event-stream": {}}, "description": "The answer as SSE"}},
)
async def chat(
    body: ChatRequest,
    request: Request,
    forward: Annotated[ChatForward, Depends(get_chat_forward)],
) -> StreamingResponse:
    """Ask the LLM (LLM-1.1). The answer streams as server-sent events: ``session``,
    ``meta``, ``token``…, ``done``; or ``error`` with a problem document."""
    if body.mode == "images":
        raise ValidationFailed(
            "Image search is built later in M12 (ADR-0022 rule 12).",
            errors=[{"loc": ["body", "mode"], "msg": "not built yet", "code": "not_built"}],
        )
    if body.mode == "rag" and body.index_version_id is None:
        raise ValidationFailed(
            "RAG answers from an index version; none was given.",
            suggested_action="Pick the corpus's index version.",
            errors=[
                {"loc": ["body", "index_version_id"], "msg": "required for rag", "code": "missing"}
            ],
        )
    prepared = await run_in_threadpool(_prepare, body)
    return StreamingResponse(
        _relay(request, forward, body, prepared),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------- conversations


class SessionSummary(BaseModel):
    id: str
    title: str
    mode: str | None
    created_at: datetime
    updated_at: datetime


class SessionList(BaseModel):
    items: list[SessionSummary]


class RequestTimings(BaseModel):
    route: str
    model: str | None
    revision: str | None
    device: str | None
    index_version_id: str | None
    cold_start: bool
    load_ms: float | None
    ttft_ms: float | None
    total_ms: float | None
    prompt_tokens: int | None
    output_tokens: int | None
    tokens_per_s: float | None
    status: str
    error_code: str | None


class MessageOut(BaseModel):
    id: int
    role: str
    mode: str
    content: str
    citations: list[Any] | None
    details: dict[str, Any] | None
    request: RequestTimings | None
    created_at: datetime


class SessionOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut]


@router.get("/chat/sessions", response_model=SessionList)
def list_sessions(limit: int = 100) -> Json:
    """Conversations, newest first, with the mode of their last message (SCR-12)."""
    last_mode = (
        select(ChatMessage.mode)
        .where(ChatMessage.session_id == ChatSession.id)
        .order_by(ChatMessage.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    with Session(get_engine()) as session:
        rows = session.execute(
            select(ChatSession, last_mode)
            .order_by(ChatSession.updated_at.desc())
            .limit(max(1, min(limit, 500)))
        ).all()
        items = [
            {
                "id": chat.id,
                "title": chat.title,
                "mode": mode,
                "created_at": chat.created_at,
                "updated_at": chat.updated_at,
            }
            for chat, mode in rows
        ]
    return {"items": items}


def _timings(row: InferenceRequest | None) -> Json | None:
    if row is None:
        return None
    names = RequestTimings.model_fields
    return {name: getattr(row, name) for name in names}


@router.get("/chat/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: str) -> Json:
    with Session(get_engine()) as session:
        chat = session.get(ChatSession, session_id)
        if chat is None:
            raise NotFound(f"Conversation {session_id} was deleted or never existed.")
        rows = session.execute(
            select(ChatMessage, InferenceRequest)
            .outerjoin(InferenceRequest, InferenceRequest.id == ChatMessage.request_id)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id)
        ).all()
        return {
            "id": chat.id,
            "title": chat.title,
            "created_at": chat.created_at,
            "updated_at": chat.updated_at,
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "mode": message.mode,
                    "content": message.content,
                    "citations": message.citations,
                    "details": message.details,
                    "request": _timings(timing),
                    "created_at": message.created_at,
                }
                for message, timing in rows
            ],
        }


@router.delete("/chat/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> Response:
    """Delete a conversation and its messages; its requests' timings stay (NFR-6)."""
    with Session(get_engine()) as session, session.begin():
        chat = session.get(ChatSession, session_id)
        if chat is None:
            raise NotFound(f"Conversation {session_id} was deleted or never existed.")
        session.delete(chat)
    return Response(status_code=204)
