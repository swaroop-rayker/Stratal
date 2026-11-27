# pyright: basic
"""Phase-3 (M12) acceptance on the running stack, with the real model: NFR-6 as decision 11
reads it, and LLM-2.1's citations on the frozen question set (decision 11).

    docker compose --profile gpu up -d
    docker compose run --rm dev pytest -q -s -m acceptance
        tests/acceptance/test_M12_llm_acceptance.py

Through the API, as SCR-12 does (``POST /chat``, server-sent events), on the index
``STRATAL_ACCEPTANCE_INDEX`` (default the 128-token index of Stratal's docs). The GPU must be
free: no job holds the lease.

- **NFR-6:** one warm-up query, reported apart (it may load the model); then 10 chats whose
  first token must come under 2 s and 10 RAG questions answered in full under 8 s; 19 of the
  20 must meet their target.
- **LLM-2.1, decision 11:** of the answerable questions whose retrieval gave the model a
  chunk of a labelled section, at least 90 % cite such a chunk; no shown citation refers to a
  source the model was not given.

Each prints an ``ACCEPTANCE`` line of figures for the README.
"""

import json
import os
import statistics
import urllib.request
from typing import Any

import pytest

from stratal.eval.retrieval import is_hit, load_question_set
from stratal.inference.citations import DONT_KNOW

pytestmark = pytest.mark.acceptance

API = os.environ.get("STRATAL_ACCEPTANCE_API", "http://api:8000/api/v1")
INDEX = os.environ.get("STRATAL_ACCEPTANCE_INDEX", "ds_710b60b40962")
QUESTION_SET = "stratal-docs-questions-v1"
FIRST_TOKEN_MS = 2000
RAG_TOTAL_MS = 8000
CITED_SHARE = 0.9

CHATS = [
    "In one sentence, what is a B-tree?",
    "Name three sorting algorithms.",
    "What does HTTP stand for?",
    "In one sentence, what is a GPU used for?",
    "What is the capital of France?",
    "Give one use of a hash function.",
    "In one sentence, what is overfitting?",
    "What is 17 times 3?",
    "Name two primary colours.",
    "In one sentence, what is a container image?",
]


def ask(mode: str, message: str, **extra: Any) -> dict[str, Any]:
    """One SCR-12 request; its events by name (``token`` joined into ``text``)."""
    body = json.dumps({"mode": mode, "message": message, **extra}).encode()
    request = urllib.request.Request(
        f"{API}/chat", data=body, method="POST", headers={"content-type": "application/json"}
    )
    events: dict[str, Any] = {"text": ""}
    with urllib.request.urlopen(request, timeout=180) as response:
        name = ""
        for raw in response:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if name == "token":
                    events["text"] += data["text"]
                else:
                    events[name] = data
    assert "error" not in events, events["error"]
    assert "done" in events, events
    return events


def test_NFR_6_twenty_queries_meet_their_targets() -> None:
    questions = load_question_set(QUESTION_SET).answerable[:10]
    warm = ask("llm", "Say OK.")
    print(
        f"warm-up: cold start {warm['meta'].get('cold_start')}, "
        f"load {warm['meta'].get('load_ms')} ms, "
        f"first token {warm['done'].get('ttft_ms')} ms",
        flush=True,
    )

    first_tokens = [float(ask("llm", q)["done"]["ttft_ms"]) for q in CHATS]
    totals = [
        float(ask("rag", q.question, index_version_id=INDEX)["done"]["total_ms"]) for q in questions
    ]
    met = sum(t < FIRST_TOKEN_MS for t in first_tokens) + sum(t < RAG_TOTAL_MS for t in totals)
    print(
        f"ACCEPTANCE NFR-6: {met} of 20 met; chat first token median "
        f"{statistics.median(first_tokens):.0f} ms, max {max(first_tokens):.0f} ms; "
        "RAG total median "
        f"{statistics.median(totals):.0f} ms, max {max(totals):.0f} ms",
        flush=True,
    )
    assert met >= 19, (first_tokens, totals)


def test_LLM_2_1_citations_meet_decision_11_on_the_question_set() -> None:
    found = cited = removed = uncited = declined = 0
    misses: list[str] = []
    for question in load_question_set(QUESTION_SET).answerable:
        answer = ask("rag", question.question, index_version_id=INDEX)
        details = answer["citations"]
        given = {s["n"]: s for s in details.get("sources") or []}
        # No shown citation refers to a source the model was not given (LLM-2.1).
        assert all(c["n"] in given for c in details.get("citations") or []), details
        removed += len(details.get("removed") or [])
        uncited += bool(details.get("uncited"))

        def hit(source: dict[str, Any], labels: Any = question.labels) -> bool:
            return any(is_hit(source["document"], source["section"], label) for label in labels)

        if not any(hit(s) for s in given.values()):
            continue  # retrieval missed: hit@k's business, not the citations'
        if DONT_KNOW.search(answer["text"]):
            declined += 1  # the model's own "I don't know" is counted apart (ADR-0022 rule 8)
            continue
        found += 1
        if any(hit(c) for c in details.get("citations") or []):
            cited += 1
        else:
            misses.append(question.id)
    share = cited / max(1, found)
    print(
        f"ACCEPTANCE LLM-2.1: {cited} of {found} answers whose sources held a labelled section "
        f"cite it ({share:.0%}); {declined} where the model said it did not know; {uncited} "
        f"uncited answers; {removed} citations removed as referring to no source given; "
        f"missed {misses}",
        flush=True,
    )
    assert share >= CITED_SHARE, misses
