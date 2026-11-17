# pyright: basic
"""Every Redis operation Stratal uses, behind typed methods (DATA_MODEL.md §7).

redis-py's own type hints are incomplete, so this one module is checked in
pyright's basic mode and the rest of the code stays strict.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import redis
import redis.asyncio as aioredis

EVENT_STREAM = "stratal:events"
COLLECTOR_GROUP = "collector"
LIVE_GLOBAL = "stratal:live:global"
ACTIVE_HASH = "stratal:active"
SYSTEM_KEY = "stratal:system"


def live_channel(job_id: str) -> str:
    return f"stratal:live:job:{job_id}"


def control_key(job_id: str) -> str:
    return f"stratal:job:{job_id}:control"


@dataclass(frozen=True)
class StreamEntry:
    entry_id: str
    kind: str
    """``event`` or ``process_sample``."""
    data: str
    """The JSON payload."""


class Bus:
    """A synchronous Redis client with only the calls Stratal needs."""

    def __init__(self, url: str, *, timeout_s: float = 5.0) -> None:
        self._client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=timeout_s,
            socket_timeout=timeout_s + 5.0,
        )

    def close(self) -> None:
        self._client.close()

    def ping(self) -> None:
        self._client.ping()

    # -- event stream: job child -> collector -------------------------------

    def append(self, kind: str, data: str, *, maxlen: int) -> None:
        self._client.xadd(
            EVENT_STREAM, {"kind": kind, "data": data}, maxlen=maxlen, approximate=True
        )

    def ensure_group(self) -> None:
        try:
            self._client.xgroup_create(EVENT_STREAM, COLLECTOR_GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def read_group(
        self, consumer: str, *, count: int, block_ms: int, pending: bool = False
    ) -> list[StreamEntry]:
        """Read new entries, or (``pending``) ones delivered before but never acknowledged."""
        reply: Any = self._client.xreadgroup(
            COLLECTOR_GROUP,
            consumer,
            {EVENT_STREAM: "0" if pending else ">"},
            count=count,
            block=None if pending else block_ms,
        )
        entries: list[StreamEntry] = []
        for _stream, items in reply or []:
            for entry_id, fields in items:
                if fields:
                    entries.append(
                        StreamEntry(entry_id, fields.get("kind", "event"), fields.get("data", ""))
                    )
        return entries

    def ack(self, entry_ids: list[str]) -> None:
        if entry_ids:
            self._client.xack(EVENT_STREAM, COLLECTOR_GROUP, *entry_ids)

    def stream_length(self) -> int:
        return int(self._client.xlen(EVENT_STREAM))

    # -- live fan-out: collector -> API -------------------------------------

    def publish(self, channel: str, message: dict[str, Any]) -> None:
        self._client.publish(channel, json.dumps(message, separators=(",", ":")))

    # -- control flags and the active-job registry --------------------------

    def set_control(self, job_id: str, value: str) -> None:
        self._client.set(control_key(job_id), value, ex=7 * 24 * 3600)

    def get_control(self, job_id: str) -> str | None:
        value: Any = self._client.get(control_key(job_id))
        return value

    def clear_control(self, job_id: str) -> None:
        self._client.delete(control_key(job_id))

    def set_active(self, job_id: str, *, stage: str, queue: str, since: str) -> None:
        payload = json.dumps({"stage": stage, "queue": queue, "since": since})
        self._client.hset(ACTIVE_HASH, job_id, payload)

    def clear_active(self, job_id: str) -> None:
        self._client.hdel(ACTIVE_HASH, job_id)

    def get_active(self) -> dict[str, dict[str, str]]:
        raw: Any = self._client.hgetall(ACTIVE_HASH)
        return {job_id: json.loads(value) for job_id, value in raw.items()}

    # -- machine description: monitor -> API (GET /system) ------------------

    def set_system(self, info: dict[str, Any]) -> None:
        self._client.set(SYSTEM_KEY, json.dumps(info, separators=(",", ":")))

    def get_system(self) -> dict[str, Any] | None:
        raw: Any = self._client.get(SYSTEM_KEY)
        return json.loads(raw) if raw else None

    def flush_all(self) -> None:
        """Empty this Redis database. For tests, which use their own database number."""
        self._client.flushdb()


async def subscribe(
    url: str, channels: list[str], *, idle_s: float
) -> AsyncIterator[dict[str, Any] | None]:
    """Yield each message published on ``channels``; ``None`` after ``idle_s`` of silence."""
    client = aioredis.Redis.from_url(url, decode_responses=True)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(*channels)
        yield None  # subscribed: the caller can now read its backlog without a gap
        while True:
            message: Any = await pubsub.get_message(ignore_subscribe_messages=True, timeout=idle_s)
            if message is None:
                yield None
                continue
            try:
                yield json.loads(message["data"])
            except (ValueError, TypeError):
                continue
    finally:
        await pubsub.aclose()
        await client.aclose()
