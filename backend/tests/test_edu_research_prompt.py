"""Tests for the mode-aware educational research-prompt builder.

The pure builder ``build_edu_research_prompt`` toggles on two axes:
- excursion vs. standalone (``excursion_key``)
- grade-banded vs. single-level (``grade_bands``)

These are pure-string assertions — no LLM call. One wrapper test mocks
``create_message`` to confirm the tailored template is what gets sent.
"""

from unittest.mock import MagicMock, patch

from app.models.edu_suggestion import EduSuggestion
from app.services.edu_research import (
    build_edu_research_prompt,
    generate_edu_research_prompt,
)

SUB_TOPICS = ["Founding and Layout", "The Destruction of 1671"]


def _suggestion(*, excursion_key, grade_bands):
    return EduSuggestion(
        id=3,
        excursion_key=excursion_key,
        title="Panama Viejo Archeological Complex",
        description="The first European city on the Pacific coast.",
        grade_bands=grade_bands,
        sub_topics=SUB_TOPICS,
    )


# --- always-present elements, regardless of mode -------------------------------


def test_common_backbone_present_in_every_mode():
    for excursion_key in ("panama-viejo", None):
        for grade_bands in (["3rd-6th", "7th-8th"], None):
            prompt = build_edu_research_prompt(
                _suggestion(excursion_key=excursion_key, grade_bands=grade_bands)
            )
            assert "REF: edu_suggestion_3" in prompt
            assert "## References" in prompt
            assert "10-15 key vocabulary terms" in prompt
            assert "3-5 worthwhile primary-source quotations" in prompt
            assert "5000-8000 words" in prompt
            # sub-topics rendered as bullets
            assert "- Founding and Layout" in prompt
            assert "- The Destruction of 1671" in prompt


# --- axis 1: excursion vs. standalone ------------------------------------------


def test_excursion_mode_keeps_site_visit_framing():
    prompt = build_edu_research_prompt(
        _suggestion(excursion_key="panama-viejo", grade_bands=["3rd-6th"])
    )
    assert "pre-visit study guides, guided-tour" in prompt
    assert "observable evidence at the site" in prompt


def test_standalone_mode_drops_site_visit_framing():
    prompt = build_edu_research_prompt(_suggestion(excursion_key=None, grade_bands=["3rd-6th"]))
    assert "NOT tied to a site visit" in prompt
    assert "pre-visit" not in prompt
    assert "guided-tour" not in prompt
    assert "observable evidence at the site" not in prompt


# --- axis 2: grade-banded vs. single-level -------------------------------------


def test_banded_mode_targets_grade_bands():
    prompt = build_edu_research_prompt(
        _suggestion(excursion_key="panama-viejo", grade_bands=["3rd-6th", "7th-8th"])
    )
    assert "Target grade bands: 3rd-6th, 7th-8th" in prompt
    assert "informed general / educator level" not in prompt


def test_single_level_mode_omits_grade_bands():
    prompt = build_edu_research_prompt(_suggestion(excursion_key="panama-viejo", grade_bands=None))
    assert "Target grade bands" not in prompt
    assert "informed general / educator level" in prompt


# --- the future case: standalone + no grade adaptation -------------------------


def test_standalone_no_bands_is_clean():
    prompt = build_edu_research_prompt(_suggestion(excursion_key=None, grade_bands=None))
    assert "NOT tied to a site visit" in prompt
    assert "Target grade bands" not in prompt
    assert "informed general / educator level" in prompt
    # still a complete, processable research prompt
    assert "## References" in prompt
    assert "10-15 key vocabulary terms" in prompt


# --- wrapper: the tailored template reaches the LLM call -----------------------


def test_generate_passes_standalone_guard_to_llm():
    suggestion = _suggestion(excursion_key=None, grade_bands=None)
    with patch("app.services.edu_research.create_message") as mock_cm:
        mock_cm.return_value.content = [MagicMock(text="TAILORED PROMPT")]
        out = generate_edu_research_prompt(suggestion)

    assert out == "TAILORED PROMPT"
    sent = mock_cm.call_args.kwargs["messages"][0]["content"]
    assert "Panama topic" in sent  # standalone subject word
    assert "NOT a site visit" in sent  # standalone guard bullet
    assert "TEMPLATE:\nREF: edu_suggestion_3" in sent


def test_generate_uses_site_subject_for_excursion():
    suggestion = _suggestion(excursion_key="panama-viejo", grade_bands=["3rd-6th"])
    with patch("app.services.edu_research.create_message") as mock_cm:
        mock_cm.return_value.content = [MagicMock(text="X")]
        generate_edu_research_prompt(suggestion)

    sent = mock_cm.call_args.kwargs["messages"][0]["content"]
    assert "Panama site" in sent
    assert "NOT a site visit" not in sent
