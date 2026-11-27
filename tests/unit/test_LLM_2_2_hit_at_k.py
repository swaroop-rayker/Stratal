"""LLM-2.2 (ADR-0022 rules 9 and 10): hit@k against hand-computed values on a planted set."""

from stratal.eval.retrieval import (
    Label,
    Question,
    QuestionSet,
    Retrieved,
    first_hit,
    is_hit,
    load_question_set,
    score,
)

T = "Stratal — Data Model"


def chunk(document: str, section: str, relevance: float, key: str = "k") -> Retrieved:
    return Retrieved(key, document, section, relevance)


def test_LLM_2_2_a_hit_is_the_labelled_section_or_under_it() -> None:
    label = Label(document="DATA_MODEL.md", section="4. PostgreSQL schema")
    assert is_hit("DATA_MODEL.md", f"{T} › 4. PostgreSQL schema", label)
    assert is_hit("DATA_MODEL.md", f"{T} › 4. PostgreSQL schema › 4.2 dataset_versions", label)
    assert not is_hit("DATA_MODEL.md", f"{T} › 4. PostgreSQL schemas", label)  # not a prefix match
    assert not is_hit("DATA_MODEL.md", f"{T} › 3. Identifiers", label)
    assert not is_hit("API.md", f"{T} › 4. PostgreSQL schema", label)
    assert is_hit("DATA_MODEL.md", T, Label(document="DATA_MODEL.md"))  # the whole document


def test_LLM_2_2_hit_at_k_matches_hand_computed_values() -> None:
    ident = Label(document="DATA_MODEL.md", section="3. Identifiers")
    sleep = Label(document="decisions/ADR-0008.md", section="Decision")
    questions = QuestionSet(
        "planted",
        "0" * 64,
        (
            Question(id="q1", question="IDs?", answerable=True, labels=(ident,)),
            Question(id="q2", question="Sleep?", answerable=True, labels=(sleep,)),
            Question(id="q3", question="Blobs?", answerable=True, labels=(ident,)),
            Question(id="q4", question="Capital of Mars?", answerable=False),
        ),
    )
    other = chunk("API.md", "Stratal — Interfaces › 3. Endpoints", 0.6)
    retrieved = {
        # q1: a hit at rank 1
        "q1": [chunk("DATA_MODEL.md", f"{T} › 3. Identifiers", 0.81), other],
        # q2: a hit at rank 3
        "q2": [other, other, chunk("decisions/ADR-0008.md", "ADR-0008 › Decision", 0.71)],
        # q3: no hit in the top 10
        "q3": [other] * 10,
        "q4": [chunk("API.md", "Stratal — Interfaces", 0.42)],
    }
    assert [first_hit(q, retrieved[q.id]) for q in questions.questions[:3]] == [1, 3, None]
    report = score(questions, retrieved, (1, 5, 10))
    # By hand: hit@1 = 1 of 3 (q1); hit@5 = 2 of 3 (q1, q2); hit@10 = 2 of 3.
    assert report["hits"]["hit@1"] == {"hits": 1, "questions": 3, "share": 0.3333}
    assert report["hits"]["hit@5"] == {"hits": 2, "questions": 3, "share": 0.6667}
    assert report["hits"]["hit@10"] == {"hits": 2, "questions": 3, "share": 0.6667}
    assert [m["id"] for m in report["missed"]] == ["q3"]
    assert report["relevance"]["answerable"] == [0.81, 0.6, 0.6]
    assert report["relevance"]["unanswerable"] == [0.42]
    at_050 = next(t for t in report["thresholds"] if t["threshold"] == 0.5)
    assert at_050 == {"threshold": 0.5, "answered": 3, "abstained": 1}
    at_065 = next(t for t in report["thresholds"] if t["threshold"] == 0.65)
    assert at_065 == {"threshold": 0.65, "answered": 1, "abstained": 1}
    assert sum(report["relevance"]["answerable_bins"]) == 3
    assert report["relevance"]["unanswerable_bins"][4] == 1  # 0.40–0.45


def test_LLM_2_2_the_question_set_is_well_formed() -> None:
    questions = load_question_set("stratal-docs-questions-v1")
    assert len(questions.sha256) == 64
    assert len(questions.answerable) >= 45
    assert sum(1 for q in questions.questions if not q.answerable) >= 12
