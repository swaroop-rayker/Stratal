"""NFR-7 for the LLM (ADR-0021 rule 1): ``ollama`` sits on the internal ``llm`` network only,
which has no gateway, and only ``inference`` joins it. Read from ``compose.yaml`` as text
(no YAML library in the dev image); checked live in M12 with ``ollama`` reaching neither
1.1.1.1, the Ollama registry nor ``api``."""

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "compose.yaml"


def _block(text: str, header: str, indent: int) -> str:
    """The lines under ``header`` at ``indent`` spaces, up to the next key at that indent."""
    lines = text.splitlines()
    start = lines.index(" " * indent + header)
    out: list[str] = []
    for line in lines[start + 1 :]:
        depth = len(line) - len(line.lstrip())
        if line.strip() and depth <= indent and not line.lstrip().startswith("#"):
            break
        out.append(line)
    return "\n".join(out)


def test_NFR_7_ollama_is_only_on_the_internal_llm_network() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    ollama = _block(text, "ollama:", 2)
    assert re.search(r"^\s+networks: \[llm\](\s*#.*)?$", ollama, re.M), "ollama joins only `llm`"
    assert 'OLLAMA_NO_CLOUD: "1"' in ollama
    assert "@sha256:" in ollama, "the image is pinned by digest"
    networks = _block(text, "networks:", 0)
    llm = _block(networks, "llm:", 2)
    assert re.search(r"^\s+internal: true", llm, re.M), "`llm` has no gateway"
    joined = [
        name
        for name in re.findall(r"^  ([a-z][a-z0-9-]*):$", text, re.M)
        if re.search(r"^\s+networks: \[[^\]]*\bllm\b", _block(text, f"{name}:", 2), re.M)
    ]
    assert sorted(set(joined)) == ["inference", "ollama"]
