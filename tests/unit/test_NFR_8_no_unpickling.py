"""NFR-8 for training and the registry: nothing in the source unpickles.

Weights, checkpoints and the registry are safetensors and JSON (ADR-0014 rule 5,
ADR-0015 rule 3). This scan keeps it so: no call in ``src/`` or ``scripts/`` can
unpickle, and none of the pickle-based loaders is imported. The torch tests in
``tests/integration/test_M8_nfr.py`` check the files on disk and the loads at run time.
"""

import ast
import io
import pickle
from pathlib import Path

import pytest

from stratal.testing.no_pickle import Unpickled, forbid_unpickling, plant_pickle

ROOT = Path(__file__).resolve().parents[2]
SCANNED = (ROOT / "src", ROOT / "scripts")

# module -> functions that unpickle (or load something that can run code)
UNPICKLING_CALLS = {
    "pickle": {"load", "loads", "Unpickler"},
    "_pickle": {"load", "loads", "Unpickler"},
    "torch": {"load"},
    "torch.serialization": {"load"},
    "torch.jit": {"load"},
    "pandas": {"read_pickle"},
    "marshal": {"load", "loads"},
}
UNPICKLING_MODULES = {"dill", "cloudpickle", "joblib", "shelve", "jsonpickle"}


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Local names bound to modules or functions: ``import torch as t`` gives t -> torch."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _dotted(node: ast.expr, names: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, names)
        return f"{base}.{node.attr}" if base else None
    return None


def _offences(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = _aliases(tree)
    where = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            modules = (
                [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            )
            for module in modules:
                if module and module.split(".")[0] in UNPICKLING_MODULES:
                    found.append(f"{where}:{node.lineno} imports {module}")
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func, names)
        if dotted is None or "." not in dotted:
            continue
        module, function = dotted.rsplit(".", 1)
        if function in UNPICKLING_CALLS.get(module, set()):
            found.append(f"{where}:{node.lineno} calls {dotted}")
        if function == "load" and module in ("numpy", "np"):
            for keyword in node.keywords:
                if keyword.arg == "allow_pickle" and not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                ):
                    found.append(f"{where}:{node.lineno} numpy allow_pickle")
    return found


def test_NFR_8_nothing_in_the_source_unpickles() -> None:
    files = [path for root in SCANNED for path in sorted(root.rglob("*.py"))]
    offences = [offence for path in files for offence in _offences(path)]

    assert offences == []


@pytest.mark.parametrize(
    "source",
    [
        "import torch\ntorch.load('w.pt')",
        "import torch as t\nt.load('w.pt', weights_only=True)",
        "from torch import load\nload('w.pt')",
        "import pickle\npickle.loads(b'')",
        "from pickle import Unpickler\nUnpickler(f).load()",
        "import numpy as np\nnp.load('a.npy', allow_pickle=True)",
        "import joblib",
        "from dill import loads",
    ],
)
def test_NFR_8_the_scan_finds_each_way_to_unpickle(tmp_path: Path, source: str) -> None:
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")

    assert _offences(path), source


def test_NFR_8_the_tripwire_stops_a_planted_pickle_before_its_payload_runs(
    tmp_path: Path,
) -> None:
    planted, marker = tmp_path / "model.safetensors", tmp_path / "PWNED"
    plant_pickle(planted, marker)
    data = planted.read_bytes()

    with forbid_unpickling() as attempts, pytest.raises(Unpickled):
        pickle.loads(data)
    with forbid_unpickling() as found, pytest.raises(Unpickled):
        pickle.Unpickler(planted.open("rb")).load()

    with forbid_unpickling() as audited, pytest.raises(Unpickled):
        pickle._Unpickler(io.BytesIO(data)).load()  # pyright: ignore[reportPrivateUsage]

    assert attempts == ["pickle.loads"] and found == ["pickle.Unpickler"]
    assert audited == ["pickle.find_class(_io.open)"], "the audit hook, without the patches"
    assert not marker.exists(), "the payload never ran"
    assert pickle.loads is not None and pickle.loads(pickle.dumps(1)) == 1, "restored after"
