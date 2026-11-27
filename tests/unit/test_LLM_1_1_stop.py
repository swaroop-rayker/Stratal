"""LLM-1.1, Stop: when the client goes away mid-answer, inference closes the generation.

Found in the M12 live check (2026-11-27): Starlette cancelled the stream without closing a
plain generator, so ``Generation.events()``'s ``finally`` never ran, Ollama generated all
512 tokens and the LLM stayed claimed until the next request timed out.
"""

from collections.abc import Iterator
from typing import Any

import anyio

from stratal.inference.app import closing_stream, ndjson


class _Source:
    """A generation that records whether it was closed."""

    def __init__(self) -> None:
        self.closed = False
        self.sent = 0

    def events(self) -> Iterator[dict[str, Any]]:
        try:
            for i in range(1000):
                self.sent += 1
                yield {"event": "token", "text": str(i)}
        finally:
            self.closed = True


def test_LLM_1_1_stop_closes_the_generation_on_disconnect() -> None:
    source = _Source()

    async def consume_two_then_disconnect() -> list[str]:
        got: list[str] = []
        stream = closing_stream(ndjson(source.events()))
        async for line in stream:
            got.append(line)
            if len(got) == 2:
                break
        await stream.aclose()  # what Starlette's cancellation amounts to
        return got

    got = anyio.run(consume_two_then_disconnect)
    assert len(got) == 2
    assert source.closed, "the generation's finally (close Ollama, free the LLM) must run"
    assert source.sent < 1000


def test_LLM_1_1_stop_closes_the_generation_on_cancel() -> None:
    source = _Source()

    async def cancel_mid_stream() -> None:
        async def consume() -> None:
            async for _ in closing_stream(ndjson(source.events())):
                await anyio.sleep(0.01)

        with anyio.move_on_after(0.05):
            await consume()

    anyio.run(cancel_mid_stream)
    assert source.closed
    assert source.sent < 1000


def test_LLM_1_1_ndjson_closes_its_events() -> None:
    source = _Source()
    lines = ndjson(source.events())
    next(lines)
    lines.close()
    assert source.closed
