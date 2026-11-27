"""LLM-2.1 (ADR-0022 rule 7): citations are checked as the answer streams."""

from stratal.inference.citations import DONT_KNOW, CitationFilter


def _stream(check: CitationFilter, pieces: list[str]) -> str:
    return "".join(check.feed(p) for p in pieces) + check.flush()


def test_LLM_2_1_given_citations_pass_through() -> None:
    check = CitationFilter({1, 2, 3})
    text = _stream(check, ["IDs hash the rows ", "[", "1", "]. Blobs ", "are stored once [2]."])
    assert text == "IDs hash the rows [1]. Blobs are stored once [2]."
    assert check.used == [1, 2] and check.removed == []


def test_LLM_2_1_a_citation_of_no_given_source_is_removed() -> None:
    check = CitationFilter({1, 2})
    text = _stream(check, ["It sleeps", " [", "7", "]", " and wakes [1, 9]."])
    assert text == "It sleeps  and wakes [1]."
    assert check.used == [1] and check.removed == [7, 9]


def test_LLM_2_1_text_is_not_held_back_without_a_marker() -> None:
    check = CitationFilter({1})
    assert check.feed("A version ID is ") == "A version ID is "
    assert check.feed("ds_ plus [") == "ds_ plus "  # held: it may become a marker
    assert check.feed("a list]") == "[a list]"  # it did not
    assert check.flush() == ""


def test_LLM_2_1_an_unclosed_bracket_is_let_through_at_the_end() -> None:
    check = CitationFilter({1})
    assert _stream(check, ["Ends with [12"]) == "Ends with [12"


def test_LLM_2_3_the_model_saying_it_does_not_know_is_recognised() -> None:
    assert DONT_KNOW.search("I don't know, the sources do not say.")
    assert DONT_KNOW.search("I do not know.")
    assert not DONT_KNOW.search("I know this one [1].")
