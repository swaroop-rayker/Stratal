"""NFR-8 test helpers: a tripwire on unpickling, and a pickle planted as weights.

``forbid_unpickling`` fails anything that unpickles while it is active, in two ways
that do not depend on each other. Python raises the ``pickle.find_class`` audit event
whenever an unpickler resolves a class or function, which is the step a malicious pickle
needs to run code. And ``pickle.load``, ``pickle.loads``, ``pickle.Unpickler`` and
``torch.load`` are replaced by functions that refuse to run. Each attempt is recorded,
so a library that catches the error still fails the test.

``plant_pickle`` writes a pickle that would create a marker file if it were ever
unpickled. The marker's absence shows that its payload never ran.
"""

import contextlib
import pickle
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

_collectors: list[list[str]] = []
_reports: list[Path | None] = []
_hook_installed = False


class Unpickled(RuntimeError):
    """Something unpickled while that was forbidden (NFR-8)."""


def _record(what: str) -> None:
    _collectors[-1].append(what)
    report = _reports[-1]
    if report is not None:  # at once: the process may be killed before the block ends
        with report.open("a", encoding="utf-8") as handle:
            handle.write(what + "\n")


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if _collectors and event == "pickle.find_class":
        what = f"pickle.find_class({args[0]}.{args[1]})"
        _record(what)
        raise Unpickled(f"NFR-8: {what} while unpickling was forbidden")


def _refuse(name: str) -> Callable[..., Any]:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        if _collectors:
            _record(name)
        raise Unpickled(f"NFR-8: {name} was called while unpickling was forbidden")

    return refuse


def _refusing_unpickler(original: type[pickle.Unpickler]) -> type[pickle.Unpickler]:
    """A subclass, not a function: libraries subclass ``pickle.Unpickler`` when imported."""

    class RefusingUnpickler(original):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _refuse("pickle.Unpickler")()

        def load(self) -> Any:
            return _refuse("pickle.Unpickler")()

    return RefusingUnpickler


@contextlib.contextmanager
def forbid_unpickling(report: Path | None = None) -> Generator[list[str]]:
    """Refuse every unpickling inside the block. Yields the attempts that were made, and
    appends each to ``report`` as it happens, if given."""
    global _hook_installed
    if not _hook_installed:  # an audit hook cannot be removed, so it is armed per block
        sys.addaudithook(_audit)
        _hook_installed = True
    patched: list[tuple[Any, str, Any, Any]] = [
        (pickle, name, getattr(pickle, name), _refuse(f"pickle.{name}"))
        for name in ("load", "loads")
    ]
    patched.append((pickle, "Unpickler", pickle.Unpickler, _refusing_unpickler(pickle.Unpickler)))
    torch = sys.modules.get("torch")  # patched only if imported: import it first to cover it
    if torch is not None:
        patched.append((torch, "load", torch.load, _refuse("torch.load")))
        patched.append(
            (torch.serialization, "load", torch.serialization.load, _refuse("torch.load"))
        )
    attempts: list[str] = []
    for owner, name, _, replacement in patched:
        setattr(owner, name, replacement)
    _collectors.append(attempts)
    _reports.append(report)
    try:
        yield attempts
    finally:
        _collectors.pop()
        _reports.pop()
        for owner, name, original, _ in patched:
            setattr(owner, name, original)


class _Payload:
    """Unpickling this calls ``open(marker, "w")``, which creates the marker file."""

    def __init__(self, marker: Path) -> None:
        self.marker = str(marker)

    def __reduce__(self) -> tuple[Any, ...]:
        return (open, (self.marker, "w"))


def plant_pickle(path: Path, marker: Path) -> None:
    """Write a plain pickle at ``path`` that would create ``marker`` if unpickled."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump({"weight": _Payload(marker)}, handle)


def payload(marker: Path) -> object:
    """The object :func:`plant_pickle` writes, for planting with ``torch.save`` instead."""
    return {"weight": _Payload(marker)}
