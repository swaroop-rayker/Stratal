# pyright: basic, reportMissingImports=false
"""Spike S-11: which LLM, and what serving it costs (M12, docs/MILESTONES.md).

Runs beside an Ollama container that has no network (``--network none``), sharing
its network namespace, so ``127.0.0.1:11434`` is Ollama and nothing else is
reachable. ``scripts/spikes/s11_llm.ps1`` starts both; this file is the client.

Parts (``--part``):

* ``gpu``: for one model already created in Ollama, the cold load, 10 short chats,
  10 RAG-sized prompts (about 2,000 tokens of Stratal's docs), JSON refinement of
  50 COCO captions to LABEL-1.6's schema, 12 routing requests, then an unload and
  the card back at its baseline. VRAM from NVML (Ollama has no PyTorch figure).
* ``cpu``: the same model with ``num_gpu: 0``: a load, 3 short chats, 1 RAG prompt.
* ``embed``: ``inference``'s new CPU models, no Ollama: bge-small-en-v1.5 through
  sentence-transformers and CLIP's text tower; memory and time per question.

Results are merged into ``/out/S-11-results.json``.
"""

import argparse
import json
import random
import re
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

OLLAMA = "http://127.0.0.1:11434"
OUT = Path("/out/S-11-results.json")
DOCS = Path("/docs")
CAPTIONS = Path("/captions/captions.jsonl")
SEED = 11
SYSTEM = "You are Stratal's assistant. Answer briefly and plainly."

SHORT = [
    "What is a dataset version, in one sentence?",
    "Name three things a GPU job needs to watch for on a 6 GB card.",
    "Explain overfitting to a beginner in two sentences.",
    "What does hit@5 measure in retrieval?",
    "Give one reason to keep raw captions after refining them.",
    "Why might a training run resume from a checkpoint?",
    "What is the difference between precision and recall?",
    "Suggest a short name for a local ML platform.",
    "What does 4-bit quantisation trade away?",
    "In one sentence, what is retrieval-augmented generation?",
]

ROUTES = ["llm", "rag", "image_generation", "captioner", "translator"]
ROUTING = [
    ("Write a haiku about autumn.", "llm"),
    ("According to Stratal's docs, how are version IDs computed?", "rag"),
    ("Draw a watercolour of a lighthouse at dusk.", "image_generation"),
    ("Describe what is in this picture: [image attached]", "captioner"),
    ("Translate 'where is the station?' into French.", "translator"),
    ("What does ADR-0006 say about VRAM?", "rag"),
    ("Generate an image of a red bicycle on a beach.", "image_generation"),
    ("Caption the attached photo of a kitchen. [image attached]", "captioner"),
    ("How do I say 'thank you very much' in Japanese?", "translator"),
    ("Explain what a learning rate is.", "llm"),
    ("Which milestone added tabular transforms, according to the project docs?", "rag"),
    ("Tell me a fun fact about octopuses.", "llm"),
]

REFINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "attributes": {"type": "array", "items": {"type": "string"}},
        "setting": {"type": "string"},
        "style": {"type": "string"},
    },
    "required": ["subject", "attributes", "setting", "style"],
}
ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"route": {"type": "string", "enum": ROUTES}},
    "required": ["route"],
}


# -- Ollama --------------------------------------------------------------------------------


def _post(path: str, body: dict[str, Any], timeout: float = 600) -> Any:
    request = urllib.request.Request(
        OLLAMA + path, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    return urllib.request.urlopen(request, timeout=timeout)


THINK_FIELD = {"supported": True}


def chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    num_predict: int = 160,
    fmt: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One streamed chat: the wall time to the first token and to the end, the text, and
    Ollama's own counters. Thinking is switched off (``think: false``); a model whose
    template does not support the field gets ``/no_think`` in its system prompt instead."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": num_predict, **(options or {})},
    }
    if THINK_FIELD["supported"]:
        body["think"] = False
    else:
        body["messages"] = [
            {**m, "content": m["content"] + " /no_think"} if m["role"] == "system" else m
            for m in messages
        ]
    if fmt is not None:
        body["format"] = fmt
    started = time.monotonic()
    first: float | None = None
    text: list[str] = []
    thinking: list[str] = []
    final: dict[str, Any] = {}
    try:
        response = _post("/api/chat", body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        if THINK_FIELD["supported"] and "think" in detail:
            THINK_FIELD["supported"] = False
            return chat(model, messages, num_predict=num_predict, fmt=fmt, options=options)
        raise RuntimeError(f"Ollama {exc.code}: {detail}") from exc
    with response:
        for line in response:
            part = json.loads(line)
            message = part.get("message") or {}
            if message.get("thinking"):
                thinking.append(message["thinking"])
            if message.get("content"):
                if first is None:
                    first = time.monotonic() - started
                text.append(message["content"])
            if part.get("done"):
                final = part
    total = time.monotonic() - started
    eval_s = final.get("eval_duration", 0) / 1e9
    return {
        "ttft_s": first,
        "total_s": total,
        "text": "".join(text),
        "thinking_chars": len("".join(thinking)),
        "load_s": final.get("load_duration", 0) / 1e9,
        "prompt_tokens": final.get("prompt_eval_count"),
        "prompt_s": final.get("prompt_eval_duration", 0) / 1e9,
        "tokens": final.get("eval_count"),
        "tokens_per_s": (final.get("eval_count", 0) / eval_s) if eval_s else None,
    }


def unload(model: str) -> None:
    with _post("/api/generate", {"model": model, "keep_alive": 0}):
        pass


def loaded() -> list[dict[str, Any]]:
    with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=30) as response:
        return json.loads(response.read())["models"]


# -- the card ------------------------------------------------------------------------------


def vram_mib() -> float:
    import pynvml

    pynvml.nvmlInit()
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        return pynvml.nvmlDeviceGetMemoryInfo(handle).used / 2**20
    finally:
        pynvml.nvmlShutdown()


# -- prompts -------------------------------------------------------------------------------


def _sections() -> list[tuple[str, str]]:
    """Stratal's docs cut at second-level headings: (title, text)."""
    found: list[tuple[str, str]] = []
    for path in sorted([*DOCS.glob("*.md"), *DOCS.glob("decisions/*.md")]):
        if path.name in {"MILESTONES.md", "KICKOFF.md"}:
            continue
        parts = re.split(r"(?m)^## ", path.read_text(encoding="utf-8"))
        for part in parts[1:]:
            title, _, body = part.partition("\n")
            if len(body.split()) > 120:
                found.append((f"{path.name} § {title.strip()}", body.strip()))
    return found


def rag_prompts(count: int) -> list[list[dict[str, str]]]:
    """``count`` prompts of about 2,000 tokens: numbered sources from different sections,
    then a question about the first, as RAG will build them (LLM-2.1)."""
    sections = _sections()
    rng = random.Random(SEED)
    prompts = []
    for _ in range(count):
        chosen = rng.sample(sections, 12)
        sources, words = [], 0
        for i, (title, body) in enumerate(chosen, start=1):
            piece = " ".join(body.split()[:260])
            sources.append(f"[{i}] {title}\n{piece}")
            words += len(piece.split())
            if words > 1400:
                break
        question = f"Using the sources, summarise what {chosen[0][0]} says, citing [n]."
        prompts.append(
            [
                {
                    "role": "system",
                    "content": SYSTEM + " Answer only from the numbered sources and cite them "
                    "as [n]. If they do not answer the question, say you don't know.",
                },
                {"role": "user", "content": "\n\n".join(sources) + f"\n\nQuestion: {question}"},
            ]
        )
    return prompts


def short(question: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]


def _summary(runs: list[dict[str, Any]], key: str) -> dict[str, float]:
    values = sorted(r[key] for r in runs if r.get(key) is not None)
    if not values:
        return {}
    return {
        "median": statistics.median(values),
        "max": values[-1],
        "min": values[0],
        "p95": values[max(0, round(0.95 * len(values)) - 1)],
    }


def _strip(runs: list[dict[str, Any]], keep_text: int = 0) -> list[dict[str, Any]]:
    return [
        {**r, "text": r["text"][:keep_text]} if keep_text else {**r, "text": None} for r in runs
    ]


# -- parts ---------------------------------------------------------------------------------


def part_gpu(model: str) -> dict[str, Any]:
    from pydantic import BaseModel, ValidationError

    class Refined(BaseModel):
        subject: str
        attributes: list[str]
        setting: str
        style: str

    for m in loaded():
        unload(m["name"])
    time.sleep(2)
    baseline = vram_mib()

    cold = chat(model, short("Say hello in five words."), num_predict=16)
    after_load = vram_mib()
    placement = loaded()

    chats = [chat(model, short(q)) for q in SHORT]
    rags = [chat(model, p, num_predict=256) for p in rag_prompts(10)]
    peak_vram = vram_mib()

    lines = CAPTIONS.read_text(encoding="utf-8").splitlines()
    captions = [json.loads(x)["text"] for x in random.Random(SEED).sample(lines, 50)]
    refined: list[dict[str, Any]] = []
    for caption in captions:
        run = chat(
            model,
            [
                {
                    "role": "system",
                    "content": "Rewrite the image caption as JSON with subject, attributes "
                    "(a list of short phrases), setting and style. Use only what the caption "
                    "says; use an empty string or list when it does not say.",
                },
                {"role": "user", "content": caption},
            ],
            num_predict=200,
            fmt=REFINE_SCHEMA,
        )
        try:
            Refined.model_validate_json(run["text"])
            run["valid"] = True
        except ValidationError:
            run["valid"] = False
        run["caption"] = caption
        refined.append(run)

    routed: list[dict[str, Any]] = []
    for request, expected in ROUTING:
        run = chat(
            model,
            [
                {
                    "role": "system",
                    "content": "Classify the request into one route: llm (general chat), rag "
                    "(questions about Stratal's own documents), image_generation (make a "
                    "picture), captioner (describe an attached image), translator. Answer "
                    "JSON.",
                },
                {"role": "user", "content": request},
            ],
            num_predict=20,
            fmt=ROUTE_SCHEMA,
        )
        try:
            got = json.loads(run["text"])["route"]
        except (ValueError, KeyError, TypeError):
            got = None
        routed.append({**run, "request": request, "expected": expected, "got": got})

    started = time.monotonic()
    unload(model)
    back: float | None = None
    while time.monotonic() - started < 60:
        if vram_mib() <= baseline + 64:
            back = time.monotonic() - started
            break
        time.sleep(0.25)

    return {
        "think_field_supported": THINK_FIELD["supported"],
        "vram_baseline_mib": baseline,
        "vram_loaded_mib": after_load,
        "vram_model_mib": after_load - baseline,
        "vram_peak_seen_mib": peak_vram,
        "ollama_ps": placement,
        "cold": {k: v for k, v in cold.items() if k != "text"},
        "chat": {
            "ttft_s": _summary(chats, "ttft_s"),
            "total_s": _summary(chats, "total_s"),
            "tokens_per_s": _summary(chats, "tokens_per_s"),
            "under_2s": sum(1 for r in chats if r["ttft_s"] is not None and r["ttft_s"] < 2),
            "thinking_chars": sum(r["thinking_chars"] for r in chats),
            "runs": _strip(chats, 300),
        },
        "rag": {
            "prompt_tokens": _summary(rags, "prompt_tokens"),
            "ttft_s": _summary(rags, "ttft_s"),
            "total_s": _summary(rags, "total_s"),
            "tokens_per_s": _summary(rags, "tokens_per_s"),
            "under_8s": sum(1 for r in rags if r["total_s"] < 8),
            "cites": sum(1 for r in rags if re.search(r"\[\d+\]", r["text"])),
            "runs": _strip(rags, 600),
        },
        "refine": {
            "valid": sum(1 for r in refined if r["valid"]),
            "count": len(refined),
            "total_s": _summary(refined, "total_s"),
            "examples": [
                {"caption": r["caption"], "output": r["text"], "valid": r["valid"]}
                for r in refined[:5]
            ],
            "invalid": [
                {"caption": r["caption"], "output": r["text"]} for r in refined if not r["valid"]
            ][:5],
        },
        "routing": {
            "correct": sum(1 for r in routed if r["got"] == r["expected"]),
            "count": len(routed),
            "total_s": _summary(routed, "total_s"),
            "runs": [
                {"request": r["request"], "expected": r["expected"], "got": r["got"]}
                for r in routed
            ],
        },
        "unload_back_to_baseline_s": back,
        "vram_after_unload_mib": vram_mib(),
    }


def part_cpu(model: str) -> dict[str, Any]:
    cpu = {"num_gpu": 0}
    for m in loaded():
        unload(m["name"])
    time.sleep(2)
    cold = chat(model, short("Say hello in five words."), num_predict=16, options=cpu)
    chats = [chat(model, short(q), options=cpu) for q in SHORT[:3]]
    rag = chat(model, rag_prompts(1)[0], num_predict=256, options=cpu)
    placement = loaded()
    unload(model)
    return {
        "cold_load_s": cold["load_s"],
        "cold_total_s": cold["total_s"],
        "ollama_ps": placement,
        "chat_ttft_s": _summary(chats, "ttft_s"),
        "chat_tokens_per_s": _summary(chats, "tokens_per_s"),
        "rag": {k: v for k, v in rag.items() if k != "text"},
    }


def _cgroup(name: str) -> int | None:
    path = Path("/sys/fs/cgroup") / name
    return int(path.read_text().split()[0]) if path.is_file() else None


def part_embed() -> dict[str, Any]:
    import resource

    import torch
    from sentence_transformers import SentenceTransformer
    from transformers import CLIPTextModelWithProjection, CLIPTokenizer

    from stratal.core.settings import get_settings
    from stratal.pretrained.catalog import local_path

    torch.set_num_threads(4)
    models = get_settings().data_dir / "models"
    start_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    started = time.monotonic()
    bge = SentenceTransformer(str(local_path(models, "bge-small-en-v1.5")), device="cpu")
    bge_load = time.monotonic() - started
    after_bge = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    started = time.monotonic()
    clip_dir = local_path(models, "clip-vit-b-32-laion2b")
    clip = CLIPTextModelWithProjection.from_pretrained(str(clip_dir)).eval()
    tokenizer = CLIPTokenizer.from_pretrained(str(clip_dir))
    clip_load = time.monotonic() - started
    after_clip = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    questions = SHORT + [q for q, _ in ROUTING]
    times = []
    for q in questions:
        t = time.monotonic()
        vector = bge.encode([q], normalize_embeddings=True)
        times.append(time.monotonic() - t)
    clip_times = []
    with torch.no_grad():
        for q in ["dogs at night", "a red bicycle on a beach", "a kitchen with a window"] * 4:
            t = time.monotonic()
            _ = clip(**tokenizer([q], return_tensors="pt", padding=True)).text_embeds
            clip_times.append(time.monotonic() - t)
    return {
        "bge_dim": int(vector.shape[1]),
        "bge_load_s": bge_load,
        "clip_text_load_s": clip_load,
        "rss_start_mib": start_rss,
        "rss_after_bge_mib": after_bge,
        "rss_after_clip_text_mib": after_clip,
        "cgroup_memory_peak_mib": (_cgroup("memory.peak") or 0) / 2**20,
        "bge_per_question_s": _summary([{"t": t} for t in times[1:]], "t"),
        "clip_text_per_query_s": _summary([{"t": t} for t in clip_times[1:]], "t"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", choices=["gpu", "cpu", "embed"], required=True)
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    results = json.loads(OUT.read_text()) if OUT.is_file() else {}
    if args.part == "embed":
        results["embed"] = part_embed()
    else:
        entry = results.setdefault(args.model, {})
        entry[args.part] = part_gpu(args.model) if args.part == "gpu" else part_cpu(args.model)
    OUT.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in results.items()}, indent=1)[:3000])


if __name__ == "__main__":
    main()
