"""Checking an answer's citations as it streams (LLM-2.1, ADR-0022 rule 7).

The answer's text passes straight through, except a ``[`` that could still become a
citation marker, which is held until it closes. A marker naming no source the model
was given is removed (and reported); one naming several keeps only the given ones.
"""

import re

DONT_KNOW = re.compile(r"\bI (?:do not|don['’]t) know\b", re.IGNORECASE)
_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_OPEN = re.compile(r"\[[\d,\s]*$")
_HOLD_CHARS = 24
"""How long a held ``[…`` may grow before it is let through as plain text."""


class CitationFilter:
    """Passes an answer's text through as it streams, holding back only a ``[`` that could
    still become a citation marker; a marker naming no given source is removed."""

    def __init__(self, given: set[int]) -> None:
        self._given = given
        self._buffer = ""
        self.used: list[int] = []
        self.removed: list[int] = []

    def feed(self, text: str) -> str:
        self._buffer += text
        held = _OPEN.search(self._buffer)
        if held is not None and len(self._buffer) - held.start() < _HOLD_CHARS:
            ready, self._buffer = self._buffer[: held.start()], self._buffer[held.start() :]
        else:
            ready, self._buffer = self._buffer, ""
        return self._clean(ready)

    def flush(self) -> str:
        ready, self._buffer = self._buffer, ""
        return self._clean(ready)

    def _clean(self, text: str) -> str:
        def keep(match: re.Match[str]) -> str:
            numbers = [int(x) for x in re.split(r"\s*,\s*", match.group(1))]
            valid = [n for n in numbers if n in self._given]
            for n in numbers:
                target = self.used if n in self._given else self.removed
                if n not in target:
                    target.append(n)
            return f"[{', '.join(str(n) for n in valid)}]" if valid else ""

        return _MARKER.sub(keep, text)
