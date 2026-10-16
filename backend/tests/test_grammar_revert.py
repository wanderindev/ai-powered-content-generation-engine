"""Tests for the emphasis-tolerant, exactly-once grammar revert helper (#519)."""

from app.services.grammar_revert import count_snippet, revert_snippet

BODY = (
    "The defensive economics decided the matter. On the peninsula, engineers "
    "concentrated their heaviest works on a single front: the *frente de tierra*, "
    "the narrow landward approach.\n\n"
    "In practice, the *situado* arrived one to three years late."
)


def test_plain_revert_puts_find_text_back():
    out = revert_snippet("a raiding party; it was", "party; it was", "party, it was")
    assert out.applied
    assert out.content == "a raiding party, it was"


def test_revert_keeps_markdown_emphasis_on_unchanged_tokens():
    replace = "engineers concentrated their heaviest works on a single front: the frente de tierra, the narrow"
    find = "engineers would concentrate their heaviest works on a single front, the frente de tierra, the narrow"
    out = revert_snippet(BODY, replace, find)
    assert out.applied
    assert (
        "engineers would concentrate their heaviest works on a single front, the *frente de tierra*, the narrow"
        in out.content
    )
    # Nothing outside the matched span moved.
    assert out.content.endswith("In practice, the *situado* arrived one to three years late.")


def test_count_is_emphasis_tolerant():
    assert count_snippet(BODY, "the situado arrived") == 1
    assert count_snippet(BODY, "the *situado* arrived") == 1
    assert "the situado arrived" not in BODY


def test_partial_words_at_the_edges_match_inside_words():
    # Grammarly cuts its context window mid-word ("tude holds:").
    body = "the order of magnitude holds: this was not a raiding party; it was"
    out = revert_snippet(
        body,
        "tude holds: this was not a raiding party; it was",
        "tude holds: this was not a raiding party, it was",
    )
    assert out.applied
    assert out.content == "the order of magnitude holds: this was not a raiding party, it was"


def test_absent_snippet_is_refused():
    out = revert_snippet(BODY, "nowhere in the body", "anything")
    assert not out.applied
    assert out.occurrences == 0
    assert out.reason == "snippet not found in content"


def test_ambiguous_snippet_is_refused():
    body = "the narrow approach and the narrow approach again"
    out = revert_snippet(body, "the narrow approach", "the wide approach")
    assert not out.applied
    assert out.occurrences == 2
    assert "2 times" in out.reason


def test_stacked_fixes_revert_newest_first():
    # Row A: "party, it" -> "party; it"; row B then stacked "party; it was a small army, hungry and experienced"
    # -> "party;; it was a small army, hungry, experienced,"; row C fixed ";;" -> ";".
    original = "not a raiding party, it was a small army, hungry and experienced and moving fast"
    after_a = "not a raiding party; it was a small army, hungry and experienced and moving fast"
    after_b = "not a raiding party;; it was a small army, hungry, experienced, and moving fast"
    after_c = "not a raiding party; it was a small army, hungry, experienced, and moving fast"
    content = after_c
    for replace, find in (
        (after_c, after_b),  # row C
        (after_b, after_a),  # row B
    ):
        out = revert_snippet(content, replace, find)
        assert out.applied
        content = out.content
    assert content == after_a  # row A (the good semicolon fix) kept
    assert revert_snippet(content, after_a, original).content == original
