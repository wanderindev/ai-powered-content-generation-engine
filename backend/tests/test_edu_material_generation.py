"""Unit tests for app.services.edu_material_generation.

Covers generate_edu_materials, generate_edu_slides, and _parse_outline_response.
Helpers for EduSuggestion / EduResearch / EduMaterial are defined locally —
no conftest.py changes.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_slide import EduSlide
from app.models.edu_suggestion import EduSuggestion
from app.services.edu_material_generation import (
    _normalize_slide,
    _parse_outline_response,
    apply_spanish_block_overrides,
    blocks_to_markdown,
    generate_edu_materials,
    generate_edu_narration,
    generate_edu_slides,
    normalize_guide_blocks,
    regenerate_slides_subset,
    validate_review_ladder,
)
from app.services.llm import OPUS_MODEL

# --- Helpers (kept inline per the brief) ---


def _mock_msg(text: str):
    return MagicMock(content=[MagicMock(text=text)])


def _make_client(*messages):
    """Mock Anthropic client whose calls consume `messages` in order across BOTH
    the create (short: condense, outline) and stream (long >= 8k: content,
    slides) seams that create_message uses. ``client.llm_calls`` records every
    call uniformly — assert on its call_count regardless of which seam was hit.
    """
    queue = list(messages)
    record = MagicMock(name="llm_call")

    def _create(**kwargs):
        record(**kwargs)
        return queue.pop(0)

    def _stream(**kwargs):
        record(**kwargs)
        cm = MagicMock()
        cm.__enter__.return_value.get_final_message.return_value = queue.pop(0)
        cm.__exit__.return_value = False
        return cm

    client = MagicMock()
    client.messages.create.side_effect = _create
    client.messages.stream.side_effect = _stream
    client.llm_calls = record
    return client


def _make_failing_client(*items):
    """Like ``_make_client`` but any item that is an ``Exception`` is raised when
    reached — used to simulate a mid-loop LLM failure on a specific band.
    """
    queue = list(items)
    record = MagicMock(name="llm_call")

    def _take():
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _create(**kwargs):
        record(**kwargs)
        return _take()

    def _stream(**kwargs):
        record(**kwargs)
        msg = _take()
        cm = MagicMock()
        cm.__enter__.return_value.get_final_message.return_value = msg
        cm.__exit__.return_value = False
        return cm

    client = MagicMock()
    client.messages.create.side_effect = _create
    client.messages.stream.side_effect = _stream
    client.llm_calls = record
    return client


def _create_suggestion(
    db,
    title="Test Edu Suggestion",
    description="A description",
    grade_bands=None,
    sub_topics=None,
    excursion_key=None,
):
    suggestion = EduSuggestion(
        title=title,
        description=description,
        grade_bands=grade_bands if grade_bands is not None else ["3rd-6th"],
        sub_topics=sub_topics if sub_topics is not None else ["Sub A", "Sub B"],
        excursion_key=excursion_key,
        status="APPROVED",
    )
    db.add(suggestion)
    db.flush()
    return suggestion


def _create_research(db, suggestion, content="Test research content " * 200):
    research = EduResearch(
        suggestion_id=suggestion.id,
        content=content,
        status="APPROVED",
    )
    db.add(research)
    db.flush()
    return research


def _short_research_content():
    # 100 words: well below CONDENSE_THRESHOLD (2500).
    return "word " * 100


def _long_research_content():
    # 2600 words: above CONDENSE_THRESHOLD.
    return "word " * 2600


CONDENSED_TEXT = "Condensed briefing"
OUTLINE_TEXT_WELL_FORMED = "TITLE: My Title\n---\nOutline body"


def _blocks_json(text: str) -> str:
    """A content-phase LLM response: a JSON array whose single paragraph block
    projects to exactly ``text`` (so ``material.content`` == text) (#340)."""
    return json.dumps([{"type": "paragraph", "text": text}])


# The content phase now returns structured blocks (#340), not markdown.
CONTENT_TEXT = _blocks_json("Guide body content.")


# --- Condense targets invariant (#338) ---


def test_condensed_targets_are_supersets_of_word_targets():
    """The per-band source briefing must be >= the final guide's upper word bound — a
    briefing shorter than the guide forces the writer to pad or invent (#338)."""
    from app.prompts.edu_material_generation import (
        GRADE_BAND_CONDENSED_TARGET,
        GRADE_BAND_WORD_TARGET,
    )

    for band, word_range in GRADE_BAND_WORD_TARGET.items():
        upper = int(word_range.split("-")[-1].replace(",", "").strip())
        assert GRADE_BAND_CONDENSED_TARGET[band] >= upper, (
            f"{band}: condensed {GRADE_BAND_CONDENSED_TARGET[band]} < word upper {upper}"
        )


# --- guide blocks: normalization + markdown projection (#340) ---


class TestGuideBlocks:
    def test_normalize_drops_unknown_and_empty(self):
        raw = [
            {"type": "cover", "title": "T", "subtitle": "S"},
            {"type": "section", "title": "Sec"},
            {"type": "section"},  # no title -> dropped
            {"type": "paragraph", "text": "p"},
            {"type": "paragraph", "text": "   "},  # blank -> dropped
            {"type": "bogus", "text": "x"},  # unknown type -> dropped
            {"type": "image", "caption": "c"},  # no image_query -> dropped
            {"type": "image", "image_query": "q", "caption": "c"},
        ]
        assert [b["type"] for b in normalize_guide_blocks(raw)] == [
            "cover",
            "section",
            "paragraph",
            "image",
        ]

    def test_normalize_non_list_returns_empty(self):
        assert normalize_guide_blocks({"type": "cover", "title": "x"}) == []

    def test_blocks_to_markdown_projection(self):
        md = blocks_to_markdown(
            [
                {"type": "cover", "title": "Guide", "subtitle": "Sub"},
                {"type": "section", "title": "Context"},
                {"type": "paragraph", "text": "Body."},
                {"type": "bullets", "items": ["a", "b"]},
                {"type": "ordered_list", "items": ["one"]},
                {"type": "quote", "text": "Q", "attribution": "Src"},
                {"type": "image", "image_query": "q", "caption": "Cap"},
            ]
        )
        assert "# Guide" in md and "## Context" in md
        assert "- a\n- b" in md and "1. one" in md
        assert "> Q" in md and "[Image: Cap]" in md

    def test_single_paragraph_projects_to_its_text(self):
        assert blocks_to_markdown([{"type": "paragraph", "text": "Hello"}]) == "Hello"


# --- _parse_outline_response (pure function) ---


class TestParseOutlineResponse:
    def test_well_formed_title_and_body(self):
        title, outline = _parse_outline_response("TITLE: My Title\n---\nOutline body")
        assert title == "My Title"
        assert outline == "Outline body"

    def test_title_with_extra_whitespace(self):
        title, outline = _parse_outline_response("TITLE:   Padded Title   \n---\nBody")
        assert title == "Padded Title"

    def test_title_only_no_body(self):
        # Per brief: when only TITLE: appears with no body, returns ("title", "").
        title, outline = _parse_outline_response("TITLE: Just A Title")
        assert title == "Just A Title"
        assert outline == ""

    def test_no_title_prefix_falls_back_to_empty_title(self):
        title, outline = _parse_outline_response("## Section\nBody")
        assert title == ""
        # The whole text becomes the outline.
        assert "## Section" in outline


# --- generate_edu_materials ---


class TestGenerateEduMaterials:
    @patch("app.services.llm.get_client")
    def test_loops_over_two_grade_bands_with_condensation(self, mock_get_client, test_db):
        # Long research triggers condensation -> 3 calls per band, 2 bands = 6 total.
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th", "7th-8th"])
        research = _create_research(test_db, suggestion, _long_research_content())

        mock_client = _make_client(
            _mock_msg(CONDENSED_TEXT),  # band 1 condense
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),  # band 1 outline
            _mock_msg(CONTENT_TEXT),  # band 1 content
            _mock_msg(CONDENSED_TEXT),  # band 2 condense
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),  # band 2 outline
            _mock_msg(CONTENT_TEXT),  # band 2 content
        )
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)

        assert mock_client.llm_calls.call_count == 6
        assert len(materials) == 2

    @patch("app.services.llm.get_client")
    def test_outline_call_uses_full_token_cap(self, mock_get_client, test_db):
        # #93: the outline cap (12k) leaves room for detailed outlines for the
        # larger grade bands plus Sonnet 5's default adaptive thinking, so
        # outlines do not truncate.
        suggestion = _create_suggestion(test_db, grade_bands=["9th-12th"])
        research = _create_research(test_db, suggestion, _short_research_content())

        mock_client = _make_client(
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),
            _mock_msg(CONTENT_TEXT),
        )
        mock_get_client.return_value = mock_client

        generate_edu_materials(research, suggestion, test_db)

        # Short research → first LLM call is the outline (feature label is
        # consumed by create_message and not forwarded to the client).
        outline_kwargs = mock_client.llm_calls.call_args_list[0].kwargs
        assert outline_kwargs["max_tokens"] == 32_000

    @patch("app.services.llm.get_client")
    def test_short_research_skips_condensation(self, mock_get_client, test_db):
        # Short research -> 2 calls per band (outline + content) only.
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())

        mock_client = _make_client(
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),
            _mock_msg(CONTENT_TEXT),
        )
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)

        assert mock_client.llm_calls.call_count == 2
        assert len(materials) == 1

    @patch("app.services.llm.get_client")
    def test_condense_uses_opus_with_thinking(self, mock_get_client, test_db):
        # #338: condensation runs with adaptive thinking at high effort; on Opus 5.5 since 2026-10-04.
        suggestion = _create_suggestion(test_db, grade_bands=["9th-12th"])
        research = _create_research(test_db, suggestion, _long_research_content())

        mock_client = _make_client(
            _mock_msg(CONDENSED_TEXT),  # condense (first call for long research)
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),
            _mock_msg(CONTENT_TEXT),
        )
        mock_get_client.return_value = mock_client

        generate_edu_materials(research, suggestion, test_db)

        condense_kwargs = mock_client.llm_calls.call_args_list[0].kwargs
        assert condense_kwargs["model"] == OPUS_MODEL
        assert condense_kwargs["thinking"] == {"type": "adaptive"}
        assert condense_kwargs["output_config"] == {"effort": "high"}
        # Outline and content follow it onto Opus, with effort pinned (Opus 5.5
        # would otherwise default to "medium").
        for call in mock_client.llm_calls.call_args_list:
            assert call.kwargs["model"] == OPUS_MODEL
            assert call.kwargs["output_config"] == {"effort": "high"}

    @patch("app.services.llm.get_client")
    def test_band_focus_threaded_into_all_prompts(self, mock_get_client, test_db):
        # #339: the per-band lens reaches condense, outline, AND content for an
        # excursion-keyed suggestion (looked up from core.excursions).
        suggestion = _create_suggestion(
            test_db, grade_bands=["7th-8th"], excursion_key="panama-viejo"
        )
        research = _create_research(test_db, suggestion, _long_research_content())
        mock_client = _make_client(
            _mock_msg(CONDENSED_TEXT),  # condense
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),  # outline
            _mock_msg(CONTENT_TEXT),  # content
        )
        mock_get_client.return_value = mock_client

        generate_edu_materials(research, suggestion, test_db)

        prompts_sent = [
            c.kwargs["messages"][0]["content"] for c in mock_client.llm_calls.call_args_list
        ]
        assert len(prompts_sent) == 3
        # panama-viejo / 7th-8th lens; the phrase only enters via the focus injection.
        assert all("trade networks and piracy" in p for p in prompts_sent)

    @patch("app.services.llm.get_client")
    def test_no_focus_block_for_suggestion_without_excursion(self, mock_get_client, test_db):
        # Standalone material (no excursion_key) → focus_for returns None → no block.
        suggestion = _create_suggestion(test_db, grade_bands=["7th-8th"], excursion_key=None)
        research = _create_research(test_db, suggestion, _long_research_content())
        mock_client = _make_client(
            _mock_msg(CONDENSED_TEXT),
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),
            _mock_msg(CONTENT_TEXT),
        )
        mock_get_client.return_value = mock_client

        generate_edu_materials(research, suggestion, test_db)

        prompts_sent = [
            c.kwargs["messages"][0]["content"] for c in mock_client.llm_calls.call_args_list
        ]
        assert all("GRADE-BAND FOCUS" not in p for p in prompts_sent)
        assert all("Grade-band focus" not in p for p in prompts_sent)

    @patch("app.services.llm.get_client")
    def test_well_formed_outline_yields_title_from_outline(self, mock_get_client, test_db):
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())

        mock_client = _make_client(
            _mock_msg("TITLE: My Title\n---\nOutline body"),
            _mock_msg(CONTENT_TEXT),
        )
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)

        assert materials[0].title == "My Title"

    @patch("app.services.llm.get_client")
    def test_no_title_prefix_falls_back_to_suggestion_title_dash_band(
        self, mock_get_client, test_db
    ):
        suggestion = _create_suggestion(test_db, title="My Suggestion", grade_bands=["7th-8th"])
        research = _create_research(test_db, suggestion, _short_research_content())

        mock_client = _make_client(
            _mock_msg("## Section\nBody"),  # no TITLE: prefix
            _mock_msg(CONTENT_TEXT),
        )
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)

        assert materials[0].title == "My Suggestion — 7th-8th"

    @patch("app.services.llm.get_client")
    def test_creates_new_material_with_pending_status_and_correct_fields(
        self, mock_get_client, test_db
    ):
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())

        mock_client = _make_client(
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),
            _mock_msg(CONTENT_TEXT),
        )
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)

        assert len(materials) == 1
        m = materials[0]
        assert m.material_type == "guide"
        assert m.status == "PENDING"
        assert m.research_id == research.id

    @patch("app.services.llm.get_client")
    def test_stores_blocks_and_markdown_projection(self, mock_get_client, test_db):
        # #340: the content phase yields structured blocks; content holds the projection.
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        blocks_json = json.dumps(
            [
                {"type": "cover", "title": "Cover"},
                {"type": "section", "title": "Context"},
                {"type": "paragraph", "text": "Body text."},
            ]
        )
        mock_client = _make_client(_mock_msg(OUTLINE_TEXT_WELL_FORMED), _mock_msg(blocks_json))
        mock_get_client.return_value = mock_client

        materials, _, _ = generate_edu_materials(research, suggestion, test_db)

        m = materials[0]
        assert [b["type"] for b in m.content_blocks] == ["cover", "section", "paragraph"]
        assert "## Context" in m.content and "Body text." in m.content  # markdown projection

    @patch("app.services.llm.get_client")
    def test_unparseable_blocks_fail_the_band(self, mock_get_client, test_db):
        # A content response that isn't a usable block array fails the band, not the run.
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        mock_client = _make_client(_mock_msg(OUTLINE_TEXT_WELL_FORMED), _mock_msg("not json"))
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)
        assert materials == [] and failed == ["3rd-6th"]

    @patch("app.services.llm.get_client")
    def test_skips_existing_approved_guide_by_default(self, mock_get_client, test_db):
        # #91: an APPROVED guide must be preserved (no overwrite, no LLM call).
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())

        existing = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Old Title",
            content="Old Content",
            status="APPROVED",
        )
        test_db.add(existing)
        test_db.flush()

        mock_client = _make_client()
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)

        # Band skipped: nothing acted upon, no LLM calls, content/status intact.
        assert materials == []
        assert skipped == ["3rd-6th"]
        mock_client.llm_calls.assert_not_called()
        assert existing.content == "Old Content"
        assert existing.status == "APPROVED"

    @patch("app.services.llm.get_client")
    def test_regenerates_existing_pending_guide_reusing_db_row(self, mock_get_client, test_db):
        # A non-APPROVED (PENDING) guide is regenerated in place.
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())

        existing = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Old Title",
            content="Old Content",
            status="PENDING",
        )
        test_db.add(existing)
        test_db.flush()
        existing_id = existing.id

        mock_client = _make_client(
            _mock_msg("TITLE: New Title\n---\nNew Outline"),
            _mock_msg(_blocks_json("New Content")),
        )
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)

        assert skipped == []
        assert len(materials) == 1
        # Same DB row reused, content overwritten, status stays PENDING.
        assert materials[0].id == existing_id
        assert materials[0].content == "New Content"
        assert materials[0].status == "PENDING"

    @patch("app.services.llm.get_client")
    def test_force_overwrites_existing_approved_guide(self, mock_get_client, test_db):
        # force=True deliberately regenerates an APPROVED guide and resets it.
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())

        existing = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Old Title",
            content="Old Content",
            status="APPROVED",
        )
        test_db.add(existing)
        test_db.flush()
        existing_id = existing.id

        mock_client = _make_client(
            _mock_msg("TITLE: New Title\n---\nNew Outline"),
            _mock_msg(_blocks_json("New Content")),
        )
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(
            research, suggestion, test_db, force=True
        )

        assert skipped == []
        assert len(materials) == 1
        assert materials[0].id == existing_id
        assert materials[0].content == "New Content"
        assert materials[0].status == "PENDING"

    @patch("app.services.llm.get_client")
    def test_mixed_bands_skip_approved_regenerate_others(self, mock_get_client, test_db):
        # APPROVED band preserved, PENDING band regenerated, missing band created.
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th", "7th-8th", "9th-12th"])
        research = _create_research(test_db, suggestion, _short_research_content())

        approved = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Approved",
            content="Keep me",
            status="APPROVED",
        )
        pending = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="7th-8th",
            title="Pending",
            content="Replace me",
            status="PENDING",
        )
        test_db.add_all([approved, pending])
        test_db.flush()
        approved_id, pending_id = approved.id, pending.id

        # Two acted bands (7th-8th regenerate, 9th-12th create), 2 calls each.
        mock_client = _make_client(
            _mock_msg("TITLE: Regen\n---\nOutline"),
            _mock_msg(_blocks_json("Regen Content")),
            _mock_msg("TITLE: Fresh\n---\nOutline"),
            _mock_msg(_blocks_json("Fresh Content")),
        )
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)

        assert skipped == ["3rd-6th"]
        assert {m.grade_band for m in materials} == {"7th-8th", "9th-12th"}
        # APPROVED band untouched, no extra LLM calls for it.
        assert mock_client.llm_calls.call_count == 4
        assert approved.content == "Keep me"
        assert approved.status == "APPROVED"
        # PENDING band reused its row.
        regen = next(m for m in materials if m.grade_band == "7th-8th")
        assert regen.id == pending_id
        assert regen.content == "Regen Content"
        assert approved_id not in {m.id for m in materials}

    @patch("app.services.llm.get_client")
    def test_failed_band_commits_good_bands_and_records_failure(self, mock_get_client, test_db):
        # #90: a mid-loop failure must not roll back the bands that already
        # succeeded; the bad band is recorded in failed_bands and the loop goes on.
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th", "7th-8th", "9th-12th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        # Production loads research already committed; commit the seed so the
        # service's per-band rollback can't discard it.
        test_db.commit()

        # Short research -> outline + content per band. Band 2's outline raises.
        mock_client = _make_failing_client(
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),  # band 1 outline
            _mock_msg(CONTENT_TEXT),  # band 1 content
            RuntimeError("boom on band 2"),  # band 2 outline -> fail
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),  # band 3 outline
            _mock_msg(CONTENT_TEXT),  # band 3 content
        )
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)

        assert failed == ["7th-8th"]
        assert skipped == []
        assert {m.grade_band for m in materials} == {"3rd-6th", "9th-12th"}
        # The two good bands are durably committed; the failed band left no row.
        persisted = {
            m.grade_band for m in test_db.query(EduMaterial).filter_by(material_type="guide").all()
        }
        assert persisted == {"3rd-6th", "9th-12th"}

    @patch("app.services.llm.get_client")
    def test_rerun_after_partial_failure_completes_without_duplicates(
        self, mock_get_client, test_db
    ):
        # #90: after a partial failure, a re-run completes the missing band and
        # regenerates the still-PENDING one in place — no duplicate rows, thanks
        # to the (research_id, material_type, grade_band) unique key (#97).
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th", "7th-8th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        test_db.commit()

        # First run: band 1 succeeds (committed), band 2 fails.
        mock_get_client.return_value = _make_failing_client(
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),
            _mock_msg(CONTENT_TEXT),
            RuntimeError("boom"),
        )
        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)
        assert failed == ["7th-8th"]
        assert len(materials) == 1

        # Second run: band 1 (PENDING) regenerates in place, band 2 is created.
        mock_get_client.return_value = _make_client(
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),
            _mock_msg(CONTENT_TEXT),
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),
            _mock_msg(CONTENT_TEXT),
        )
        materials2, skipped2, failed2 = generate_edu_materials(research, suggestion, test_db)
        assert failed2 == []
        assert {m.grade_band for m in materials2} == {"3rd-6th", "7th-8th"}
        # No duplicate rows: the unique (research_id, "guide", grade_band) key holds.
        rows = test_db.query(EduMaterial).filter_by(material_type="guide").all()
        assert len(rows) == 2

    @patch("app.services.llm.get_client")
    def test_oversized_grade_bands_raises_before_any_llm_call(self, mock_get_client, test_db):
        # #92: a suggestion carrying more than MAX_GRADE_BANDS bands is refused
        # before the loop spends, even if it bypassed the API schema cap.
        suggestion = _create_suggestion(test_db, grade_bands=[f"band-{i}" for i in range(7)])
        research = _create_research(test_db, suggestion, _short_research_content())

        mock_client = _make_client()
        mock_get_client.return_value = mock_client

        with pytest.raises(ValueError, match="grade_bands"):
            generate_edu_materials(research, suggestion, test_db)
        mock_client.llm_calls.assert_not_called()

    @patch("app.services.llm.get_client")
    def test_empty_grade_bands_returns_empty_no_llm_calls(self, mock_get_client, test_db):
        suggestion = _create_suggestion(test_db, grade_bands=[])
        research = _create_research(test_db, suggestion, _short_research_content())

        mock_client = _make_client()
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)

        assert materials == []
        mock_client.llm_calls.assert_not_called()

    @patch("app.services.llm.get_client")
    def test_none_grade_bands_returns_empty_no_llm_calls(self, mock_get_client, test_db):
        # Build directly so we get a true NULL grade_bands (the helper
        # substitutes a default for None).
        suggestion = EduSuggestion(
            title="Null bands",
            description="d",
            grade_bands=None,
            sub_topics=["a", "b"],
            status="APPROVED",
        )
        test_db.add(suggestion)
        test_db.flush()
        research = _create_research(test_db, suggestion, _short_research_content())

        mock_client = _make_client()
        mock_get_client.return_value = mock_client

        materials, skipped, failed = generate_edu_materials(research, suggestion, test_db)

        assert materials == []
        mock_client.llm_calls.assert_not_called()


# --- generate_edu_slides ---

# A minimal valid structured deck (2 slides) the LLM mock returns.
_SLIDES_JSON = (
    '[{"template": "title", "title": "Panama Viejo", "body": [], '
    '"image_query": "ruins of Panama Viejo", "narration": "Hola a todos, soy Diego."}, '
    '{"template": "bullets", "title": "Lo que veremos", "body": ["Punto uno", "Punto dos"], '
    '"image_query": null, "narration": "Hoy exploraremos esta ciudad."}]'
)


class TestGenerateEduSlides:
    @patch("app.services.llm.get_client")
    def test_single_llm_call(self, mock_get_client, test_db):
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Guide Title",
            content="## Guide content",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()

        mock_client = _make_client(_mock_msg(_SLIDES_JSON))
        mock_get_client.return_value = mock_client

        generate_edu_slides(guide, test_db)

        assert mock_client.llm_calls.call_count == 1

    @patch("app.services.llm.get_client")
    def test_deck_call_has_thinking_headroom(self, mock_get_client, test_db):
        # A 7th-8th deck truncated in production at a 12k cap: Sonnet 5's
        # adaptive thinking counts toward max_tokens, and a 16-22 slide JSON
        # array is ~4-5k tokens of output on top of it. The cap must leave room
        # for both, and stay above the streaming threshold so the call gets the
        # 600s stream timeout instead of the 120s one.
        suggestion = _create_suggestion(test_db, grade_bands=["7th-8th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="7th-8th",
            title="Guide Title",
            content="## Guide content",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()

        mock_client = _make_client(_mock_msg(_SLIDES_JSON))
        mock_get_client.return_value = mock_client

        generate_edu_slides(guide, test_db)

        kwargs = mock_client.llm_calls.call_args.kwargs
        assert kwargs["max_tokens"] >= 40_000
        assert kwargs["model"] == OPUS_MODEL
        assert mock_client.messages.stream.called
        assert not mock_client.messages.create.called

    @patch("app.services.llm.get_client")
    def test_slides_title_format(self, mock_get_client, test_db):
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Awesome Guide",
            content="## Guide",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()

        mock_client = _make_client(_mock_msg(_SLIDES_JSON))
        mock_get_client.return_value = mock_client

        slides, regenerated = generate_edu_slides(guide, test_db)

        assert slides.title == "Awesome Guide — Slides"
        assert regenerated is True

    @patch("app.services.llm.get_client")
    def test_skips_existing_approved_slides_by_default(self, mock_get_client, test_db):
        # #91: APPROVED slides preserved (returned untouched, no LLM call).
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Guide",
            content="## Guide",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()

        existing_slides = EduMaterial(
            research_id=research.id,
            material_type="slides",
            grade_band="3rd-6th",
            title="Old Slides",
            content="old slides",
            status="APPROVED",
        )
        test_db.add(existing_slides)
        test_db.flush()
        existing_id = existing_slides.id

        mock_client = _make_client()
        mock_get_client.return_value = mock_client

        slides, regenerated = generate_edu_slides(guide, test_db)

        assert regenerated is False
        assert slides.id == existing_id
        assert slides.content == "old slides"
        assert slides.status == "APPROVED"
        mock_client.llm_calls.assert_not_called()

    @patch("app.services.llm.get_client")
    def test_regenerates_existing_pending_slides_reusing_row(self, mock_get_client, test_db):
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Guide",
            content="## Guide",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()

        existing_slides = EduMaterial(
            research_id=research.id,
            material_type="slides",
            grade_band="3rd-6th",
            title="Old Slides",
            content="old slides",
            status="PENDING",
        )
        test_db.add(existing_slides)
        test_db.flush()
        existing_id = existing_slides.id

        mock_client = _make_client(_mock_msg(_SLIDES_JSON))
        mock_get_client.return_value = mock_client

        slides, regenerated = generate_edu_slides(guide, test_db)

        assert regenerated is True
        assert slides.id == existing_id
        assert len(slides.slides) == 2  # structured rows replaced, not appended
        assert slides.status == "PENDING"

    @patch("app.services.llm.get_client")
    def test_force_overwrites_existing_approved_slides(self, mock_get_client, test_db):
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Guide",
            content="## Guide",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()

        existing_slides = EduMaterial(
            research_id=research.id,
            material_type="slides",
            grade_band="3rd-6th",
            title="Old Slides",
            content="old slides",
            status="APPROVED",
        )
        test_db.add(existing_slides)
        test_db.flush()
        existing_id = existing_slides.id

        mock_client = _make_client(_mock_msg(_SLIDES_JSON))
        mock_get_client.return_value = mock_client

        slides, regenerated = generate_edu_slides(guide, test_db, force=True)

        assert regenerated is True
        assert slides.id == existing_id
        assert len(slides.slides) == 2  # structured rows replaced, not appended
        assert slides.status == "PENDING"

    @patch("app.services.llm.get_client")
    def test_creates_structured_edu_slides(self, mock_get_client, test_db):
        suggestion = _create_suggestion(
            test_db, grade_bands=["3rd-6th"], excursion_key="panama-viejo"
        )
        research = _create_research(test_db, suggestion, _short_research_content())
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Guide",
            content="## Guide",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()

        deck = (
            '[{"template": "title", "title": "Inicio", "body": [], '
            '"image_query": "x", "narration": "Hola."}, '
            '{"template": "bullets", "title": "Ideas", "body": ["a", "b"], '
            '"image_query": null}, '
            '{"template": "NOT_A_TEMPLATE", "title": "Z", "body": "texto", '
            '"image_query": null}]'
        )
        mock_get_client.return_value = _make_client(_mock_msg(deck))

        material, regenerated = generate_edu_slides(guide, test_db)

        assert material.material_type == "slides"
        assert regenerated is True
        rows = material.slides
        assert [s.position for s in rows] == [1, 2, 3]
        assert rows[0].template == "title"
        # Two-stage flow: content generation never sets narration — even if the
        # model volunteers one, it's discarded for the later narration pass.
        assert all(s.narration is None for s in rows)
        assert rows[1].body == "a\nb"  # list joined with newlines
        assert rows[2].template == "bullets"  # unknown template falls back
        assert rows[2].body == "texto"  # string body kept as-is

    @patch("app.services.llm.get_client")
    def test_long_research_condenses_first_and_persists_briefing(self, mock_get_client, test_db):
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _long_research_content())
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Guide",
            content="## Guide",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()

        mock_client = _make_client(_mock_msg("BRIEFING DE PRUEBA"), _mock_msg(_SLIDES_JSON))
        mock_get_client.return_value = mock_client

        material, _ = generate_edu_slides(guide, test_db)

        assert mock_client.llm_calls.call_count == 2  # condense, then content
        assert material.source_briefing == "BRIEFING DE PRUEBA"
        # The content call reads the briefing, not the raw research.
        content_prompt = mock_client.llm_calls.call_args.kwargs["messages"][0]["content"]
        assert "BRIEFING DE PRUEBA" in content_prompt

    @patch("app.services.llm.get_client")
    def test_prompt_uses_research_not_guide_text(self, mock_get_client, test_db):
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(
            test_db, suggestion, "Datos únicos de la investigación sobre Panamá Viejo."
        )
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Guide",
            content="## TEXTO_DEL_GUIDE_QUE_NO_DEBE_APARECER",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()

        mock_client = _make_client(_mock_msg(_SLIDES_JSON))
        mock_get_client.return_value = mock_client

        material, _ = generate_edu_slides(guide, test_db)

        prompt = mock_client.llm_calls.call_args.kwargs["messages"][0]["content"]
        assert "Datos únicos de la investigación" in prompt
        assert "TEXTO_DEL_GUIDE_QUE_NO_DEBE_APARECER" not in prompt
        # Short research skips the condense — the briefing is the research itself.
        assert material.source_briefing == "Datos únicos de la investigación sobre Panamá Viejo."

    @patch("app.services.llm.get_client")
    def test_raises_when_research_has_no_content(self, mock_get_client, test_db):
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Guide",
            content="## Guide",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()
        guide.research.content = "   "

        mock_get_client.return_value = MagicMock()

        with pytest.raises(ValueError, match="no content"):
            generate_edu_slides(guide, test_db)

    @patch("app.services.llm.get_client")
    def test_raises_when_research_has_no_suggestion(self, mock_get_client, test_db):
        # Build a guide whose research has no suggestion. To do this, we
        # create a research row pointing to a suggestion id then set the
        # relationship to None in-session.
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Guide",
            content="## Guide",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()

        # Force the relationship attribute to None.
        guide.research.suggestion = None

        mock_get_client.return_value = MagicMock()

        with pytest.raises(ValueError):
            generate_edu_slides(guide, test_db)

    @patch("app.services.llm.get_client")
    def test_raises_when_research_is_none(self, mock_get_client, test_db):
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        research = _create_research(test_db, suggestion, _short_research_content())
        guide = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band="3rd-6th",
            title="Guide",
            content="## Guide",
            status="APPROVED",
        )
        test_db.add(guide)
        test_db.flush()

        # Force research relationship to None entirely.
        guide.research = None

        mock_get_client.return_value = MagicMock()

        with pytest.raises(ValueError):
            generate_edu_slides(guide, test_db)


def _slides_material_with_rows(
    db,
    narrations=(None, None),
    briefing="BRIEFING GUARDADO",
    status="APPROVED",
    research_content=None,
):
    """A slides material with len(narrations) bullet rows (narration per row)."""
    suggestion = _create_suggestion(db, grade_bands=["3rd-6th"])
    research = _create_research(db, suggestion, research_content or _short_research_content())
    material = EduMaterial(
        research_id=research.id,
        material_type="slides",
        grade_band="3rd-6th",
        title="Deck",
        content="preview",
        source_briefing=briefing,
        status=status,
    )
    db.add(material)
    db.flush()
    for i, narration in enumerate(narrations, start=1):
        material.slides.append(
            EduSlide(
                position=i,
                template="bullets",
                title=f"Slide {i}",
                body="Uno\nDos",
                narration=narration,
                audio_url=f"https://cdn.example/audio/{i}.mp3" if narration else None,
                duration_ms=4000 if narration else None,
            )
        )
    db.flush()
    return material


class TestGenerateEduNarration:
    NARRATION_JSON = (
        '[{"position": 1, "narration": "Bienvenidos al recorrido."}, '
        '{"position": 2, "narration": "Uno es la plaza. Dos es la catedral."}]'
    )

    @patch("app.services.llm.get_client")
    def test_happy_path_assigns_narration_and_clears_stale_audio(self, mock_get_client, test_db):
        material = _slides_material_with_rows(test_db, narrations=("Vieja.", "Vieja."))
        mock_client = _make_client(_mock_msg(self.NARRATION_JSON))
        mock_get_client.return_value = mock_client

        result = generate_edu_narration(material, test_db, force=True)

        assert mock_client.llm_calls.call_count == 1  # briefing persisted → no condense
        rows = material.slides
        assert rows[0].narration == "Bienvenidos al recorrido."
        assert rows[1].narration == "Uno es la plaza. Dos es la catedral."
        # Old audio no longer matches the new script.
        assert all(s.audio_url is None and s.duration_ms is None for s in rows)
        assert "_Narración:_" in material.content
        assert result["slides_narrated"] == 2
        assert result["total_words"] > 0
        # The prompt carried the persisted briefing.
        prompt = mock_client.llm_calls.call_args.kwargs["messages"][0]["content"]
        assert "BRIEFING GUARDADO" in prompt
        assert mock_client.llm_calls.call_args.kwargs["model"] == OPUS_MODEL

    @patch("app.services.llm.get_client")
    def test_position_mismatch_raises_and_persists_nothing(self, mock_get_client, test_db):
        material = _slides_material_with_rows(test_db, narrations=(None, None))
        bad = '[{"position": 1, "narration": "Solo una."}]'
        mock_get_client.return_value = _make_client(_mock_msg(bad))

        with pytest.raises(ValueError, match="positions mismatch"):
            generate_edu_narration(material, test_db)

        assert all(s.narration is None for s in material.slides)

    @patch("app.services.llm.get_client")
    def test_existing_narration_requires_force(self, mock_get_client, test_db):
        material = _slides_material_with_rows(test_db, narrations=("Ya narrado.", None))
        mock_client = _make_client()
        mock_get_client.return_value = mock_client

        with pytest.raises(ValueError, match="force"):
            generate_edu_narration(material, test_db)
        mock_client.llm_calls.assert_not_called()

    @patch("app.services.llm.get_client")
    def test_legacy_deck_without_briefing_recondenses(self, mock_get_client, test_db):
        material = _slides_material_with_rows(
            test_db,
            narrations=(None, None),
            briefing=None,
            research_content=_long_research_content(),
        )
        mock_client = _make_client(
            _mock_msg("BRIEFING RECONSTRUIDO"), _mock_msg(self.NARRATION_JSON)
        )
        mock_get_client.return_value = mock_client

        generate_edu_narration(material, test_db)

        assert mock_client.llm_calls.call_count == 2  # condense, then narration
        assert material.source_briefing == "BRIEFING RECONSTRUIDO"
        assert material.slides[0].narration == "Bienvenidos al recorrido."

    @patch("app.services.llm.get_client")
    def test_no_slides_raises(self, mock_get_client, test_db):
        material = _slides_material_with_rows(test_db, narrations=())
        mock_get_client.return_value = MagicMock()

        with pytest.raises(ValueError, match="no slides"):
            generate_edu_narration(material, test_db)

    @patch("app.services.llm.get_client")
    def test_subset_rewrites_only_targeted_leaves_others(self, mock_get_client, test_db):
        # Slides 1, 2, 3 all narrated. A subset run for {2} must rewrite ONLY
        # slide 2 (clearing its audio) and leave slides 1 & 3 — narration AND
        # audio — untouched. No force needed for a subset.
        material = _slides_material_with_rows(
            test_db, narrations=("Uno viejo.", "Dos viejo.", "Tres viejo.")
        )
        only_two = '[{"position": 2, "narration": "Dos nuevo y mejor."}]'
        mock_get_client.return_value = _make_client(_mock_msg(only_two))

        result = generate_edu_narration(material, test_db, positions={2})

        rows = material.slides
        assert rows[0].narration == "Uno viejo."  # untouched
        assert rows[0].audio_url == "https://cdn.example/audio/1.mp3"
        assert rows[1].narration == "Dos nuevo y mejor."  # rewritten
        assert rows[1].audio_url is None and rows[1].duration_ms is None  # audio cleared
        assert rows[2].narration == "Tres viejo."  # untouched
        assert rows[2].audio_url == "https://cdn.example/audio/3.mp3"
        assert result["slides_narrated"] == 1
        # The prompt carried each slide's existing narration for continuity.
        prompt = mock_get_client.return_value.llm_calls.call_args.kwargs["messages"][0]["content"]
        assert "Tres viejo." in prompt

    @patch("app.services.llm.get_client")
    def test_subset_ignores_force_guard(self, mock_get_client, test_db):
        # An already-narrated deck: a subset rewrite is intentional, so it does
        # NOT require force (unlike a whole-deck rewrite).
        material = _slides_material_with_rows(test_db, narrations=("Uno.", "Dos."))
        mock_get_client.return_value = _make_client(
            _mock_msg('[{"position": 1, "narration": "Uno rehecho."}]')
        )
        result = generate_edu_narration(material, test_db, positions={1})
        assert result["slides_narrated"] == 1
        assert material.slides[0].narration == "Uno rehecho."

    @patch("app.services.llm.get_client")
    def test_subset_position_mismatch_raises_and_persists_nothing(self, mock_get_client, test_db):
        material = _slides_material_with_rows(test_db, narrations=("Uno.", "Dos.", "Tres."))
        # Model returns an extra slide the subset never asked for.
        bad = '[{"position": 2, "narration": "ok"}, {"position": 3, "narration": "extra"}]'
        mock_get_client.return_value = _make_client(_mock_msg(bad))

        with pytest.raises(ValueError, match="positions mismatch"):
            generate_edu_narration(material, test_db, positions={2})

        # Nothing changed.
        assert material.slides[1].narration == "Dos."
        assert material.slides[1].audio_url == "https://cdn.example/audio/2.mp3"


class TestRegenerateSlidesSubset:
    """Content re-roll of a subset of an existing deck's slides (in place)."""

    def _deck(self, db, n=3):
        suggestion = _create_suggestion(db, grade_bands=["3rd-6th"])
        research = _create_research(db, suggestion, _short_research_content())
        material = EduMaterial(
            research_id=research.id,
            material_type="slides",
            grade_band="3rd-6th",
            title="Deck",
            content="preview",
            source_briefing="BRIEFING GUARDADO",
            status="APPROVED",
        )
        db.add(material)
        db.flush()
        for i in range(1, n + 1):
            material.slides.append(
                EduSlide(
                    position=i,
                    template="bullets",
                    title=f"Viejo {i}",
                    body="Uno\nDos",
                    image_query=f"query {i}",
                    media_path=f"https://cdn.example/photo/{i}.jpg",
                    image_url=f"https://cdn.example/frame/{i}.png",
                    narration=f"Narración {i}.",
                    audio_url=f"https://cdn.example/audio/{i}.mp3",
                    duration_ms=4000,
                )
            )
        db.flush()
        return material

    @patch("app.services.llm.get_client")
    def test_reroll_targeted_in_place_preserves_media_narration_audio(
        self, mock_get_client, test_db
    ):
        material = self._deck(test_db, n=3)
        new = (
            '[{"position": 2, "template": "bullets", "title": "Nuevo 2", '
            '"body": ["A", "B"], "image_query": "nueva query 2"}]'
        )
        mock_get_client.return_value = _make_client(_mock_msg(new))

        result = regenerate_slides_subset(material, {2}, test_db)

        rows = material.slides
        # Targeted slide re-rolled in place; frame cleared but media/narration/audio kept.
        assert rows[1].title == "Nuevo 2"
        assert rows[1].body == "A\nB"
        assert rows[1].image_query == "nueva query 2"
        assert rows[1].image_url is None  # stale frame cleared
        assert rows[1].media_path == "https://cdn.example/photo/2.jpg"  # photo pick kept
        assert rows[1].narration == "Narración 2."  # narration kept
        assert rows[1].audio_url == "https://cdn.example/audio/2.mp3"  # audio kept
        # Untouched slides fully intact.
        assert rows[0].title == "Viejo 1" and rows[0].image_url == "https://cdn.example/frame/1.png"
        assert rows[2].title == "Viejo 3" and rows[2].image_url == "https://cdn.example/frame/3.png"
        assert result == {"positions": [2], "slides_regenerated": 1}
        # Deck slide count unchanged.
        assert len(material.slides) == 3

    @patch("app.services.llm.get_client")
    def test_position_mismatch_raises_and_persists_nothing(self, mock_get_client, test_db):
        material = self._deck(test_db, n=3)
        # Model returns a position outside the requested subset → nothing saved.
        bad = (
            '[{"position": 1, "template": "bullets", "title": "X", '
            '"body": [], "image_query": null}]'
        )
        mock_get_client.return_value = _make_client(_mock_msg(bad))

        with pytest.raises(ValueError, match="positions mismatch"):
            regenerate_slides_subset(material, {2}, test_db)

        assert material.slides[1].title == "Viejo 2"
        assert material.slides[1].image_url == "https://cdn.example/frame/2.png"

    @patch("app.services.llm.get_client")
    def test_no_matching_positions_raises(self, mock_get_client, test_db):
        material = self._deck(test_db, n=2)
        mock_get_client.return_value = MagicMock()
        with pytest.raises(ValueError, match="match this deck"):
            regenerate_slides_subset(material, {9}, test_db)


# --- study-guide redesign: new block types, link strip, media pass-through ---


class TestNormalizeSlideCaps:
    """Defensive length caps behind the prompt's HARD LIMITS (fixed 1280x720 canvas)."""

    def test_bullets_truncated_to_five(self):
        slide = _normalize_slide(
            {"template": "bullets", "title": "T", "body": [f"Punto {i}" for i in range(1, 9)]}
        )
        lines = slide["body"].split("\n")
        assert len(lines) == 5
        assert lines == ["Punto 1", "Punto 2", "Punto 3", "Punto 4", "Punto 5"]

    def test_long_title_truncated_on_word_boundary_with_ellipsis(self):
        long_title = "Una historia larguísima de la ciudad colonial " * 5
        slide = _normalize_slide({"template": "bullets", "title": long_title, "body": []})
        assert len(slide["title"]) <= 80
        assert slide["title"].endswith("…")
        assert not slide["title"][:-1].endswith(" ")

    def test_short_title_untouched(self):
        slide = _normalize_slide({"template": "bullets", "title": "Corto", "body": []})
        assert slide["title"] == "Corto"

    def test_long_body_line_truncated(self):
        long_line = "palabra " * 40
        slide = _normalize_slide({"template": "bullets", "title": "T", "body": [long_line]})
        assert len(slide["body"]) <= 140
        assert slide["body"].endswith("…")

    def test_per_template_caps(self):
        many = [f"Línea {i}" for i in range(1, 12)]
        for template, cap in (
            ("map", 6),
            ("timeline", 5),
            ("two-col-image-left", 4),
            ("two-col-image-right", 4),
            ("section", 2),
            ("closing", 2),
        ):
            slide = _normalize_slide({"template": template, "title": "T", "body": many})
            assert len(slide["body"].split("\n")) == cap, template

    def test_newline_string_body_also_capped(self):
        body = "\n".join(f"Punto {i}" for i in range(1, 9))
        slide = _normalize_slide({"template": "bullets", "title": "T", "body": body})
        assert len(slide["body"].split("\n")) == 5


class TestNewBlockTypes:
    def test_callout_with_terms_normalizes(self):
        blocks = normalize_guide_blocks(
            [
                {
                    "type": "callout",
                    "label": "Key Terms",
                    "terms": [
                        {"term": "isthmus", "definition": "a narrow land bridge"},
                        {"term": "", "definition": ""},  # empty pair dropped
                    ],
                }
            ]
        )
        assert blocks == [
            {
                "type": "callout",
                "label": "Key Terms",
                "text": None,
                "terms": [{"term": "isthmus", "definition": "a narrow land bridge"}],
            }
        ]

    def test_callout_with_text_only_normalizes(self):
        blocks = normalize_guide_blocks(
            [{"type": "callout", "label": "Did you know?", "text": "A fun fact."}]
        )
        assert blocks[0]["text"] == "A fun fact."
        assert blocks[0]["terms"] == []

    def test_empty_callout_dropped(self):
        assert normalize_guide_blocks([{"type": "callout", "label": "Empty"}]) == []

    def test_mc_question_normalizes(self):
        blocks = normalize_guide_blocks(
            [{"type": "mc_question", "question": "When?", "choices": ["1519", "1671"]}]
        )
        assert blocks == [{"type": "mc_question", "question": "When?", "choices": ["1519", "1671"]}]

    def test_mc_question_needs_two_choices(self):
        assert (
            normalize_guide_blocks(
                [{"type": "mc_question", "question": "When?", "choices": ["1519"]}]
            )
            == []
        )
        assert normalize_guide_blocks([{"type": "mc_question", "choices": ["a", "b"]}]) == []

    def test_markdown_projection_of_new_types(self):
        md = blocks_to_markdown(
            [
                {
                    "type": "callout",
                    "label": "Key Terms",
                    "text": None,
                    "terms": [{"term": "isthmus", "definition": "a land bridge"}],
                },
                {"type": "mc_question", "question": "When?", "choices": ["1519", "1671"]},
            ]
        )
        assert "**Key Terms**" in md
        assert "- **isthmus** — a land bridge" in md
        assert "When?" in md
        assert "a) 1519" in md and "b) 1671" in md


class TestLinkStripAndMedia:
    def test_urls_and_placeholders_stripped_from_text_fields(self):
        blocks = normalize_guide_blocks(
            [
                {"type": "paragraph", "text": "Read this https://example.com/x today."},
                {"type": "bullets", "items": ["See [LINK NEEDED: maps] for the route."]},
                {"type": "paragraph", "text": "Visit www.example.com now."},
            ]
        )
        assert blocks[0]["text"] == "Read this today."
        assert blocks[1]["items"] == ["See for the route."]
        assert blocks[2]["text"] == "Visit now."

    def test_block_of_only_a_link_is_dropped(self):
        assert normalize_guide_blocks([{"type": "paragraph", "text": "https://x.com"}]) == []

    def test_media_selection_passes_through_untouched(self):
        raw = {
            "type": "image",
            "image_query": "colonial tower",
            "caption": "The tower",
            "media_id": 42,
            "media_path": "media/wikimedia/tower.jpg",
        }
        blocks = normalize_guide_blocks([raw])
        assert blocks[0]["media_id"] == 42
        assert blocks[0]["media_path"] == "media/wikimedia/tower.jpg"

    def test_media_fields_absent_when_not_set(self):
        blocks = normalize_guide_blocks([{"type": "image", "image_query": "tower"}])
        assert "media_id" not in blocks[0] and "media_path" not in blocks[0]

    def test_invalid_media_id_type_dropped(self):
        blocks = normalize_guide_blocks(
            [{"type": "image", "image_query": "tower", "media_id": "42"}]
        )
        assert "media_id" not in blocks[0]

    def test_photo_aspect_round_trips_on_image_and_map(self):
        blocks = normalize_guide_blocks(
            [
                {"type": "image", "image_query": "tower", "aspect": "portrait"},
                {"type": "map", "image_query": "map", "locations": ["Catedral"], "aspect": "wide"},
            ]
        )
        assert blocks[0]["aspect"] == "portrait"
        assert blocks[1]["aspect"] == "wide"

    def test_photo_aspect_default_and_unknown_normalize_to_absent(self):
        blocks = normalize_guide_blocks(
            [
                {"type": "image", "image_query": "a", "aspect": "landscape"},
                {"type": "image", "image_query": "b", "aspect": "circle"},
                {"type": "image", "image_query": "c"},
            ]
        )
        assert all("aspect" not in b for b in blocks)


class TestStrictNormalization:
    def test_strict_raises_naming_rejected_blocks(self):
        raw = [
            {"type": "paragraph", "text": "fine"},
            {"type": "not_a_type", "text": "x"},
            {"type": "section"},  # no title
            "not an object",
        ]
        with pytest.raises(ValueError) as exc:
            normalize_guide_blocks(raw, strict=True)
        msg = str(exc.value)
        assert "block 1" in msg and "not_a_type" in msg
        assert "block 2" in msg and "'section'" in msg
        assert "block 3" in msg and "not an object" in msg

    def test_strict_raises_on_non_list(self):
        with pytest.raises(ValueError):
            normalize_guide_blocks({"type": "paragraph"}, strict=True)

    def test_lenient_still_drops_silently(self):
        assert normalize_guide_blocks([{"type": "not_a_type"}]) == []


# --- study-guide redesign: review ladder validation ---


def _compliant_review_blocks(mc, choices, reflections):
    blocks = [{"type": "section", "title": "Test Your Knowledge"}]
    blocks += [
        {"type": "mc_question", "question": f"Q{i}?", "choices": [f"c{j}" for j in range(choices)]}
        for i in range(mc)
    ]
    blocks.append({"type": "section", "title": "For Reflection"})
    blocks += [
        {"type": "callout", "label": "For Reflection", "text": f"Think about {i}.", "terms": []}
        for i in range(reflections)
    ]
    return blocks


class TestValidateReviewLadder:
    def test_compliant_blocks_pass(self):
        assert validate_review_ladder(_compliant_review_blocks(4, 2, 1), "3rd-6th") == []
        assert validate_review_ladder(_compliant_review_blocks(8, 4, 3), "9th-12th") == []

    def test_wrong_question_count_reported(self):
        problems = validate_review_ladder(_compliant_review_blocks(3, 2, 1), "3rd-6th")
        assert any("expected 4 mc_question" in p for p in problems)

    def test_wrong_choice_count_reported(self):
        problems = validate_review_ladder(_compliant_review_blocks(6, 4, 2), "7th-8th")
        assert any("expected 3" in p for p in problems)

    def test_missing_sections_reported(self):
        problems = validate_review_ladder([{"type": "paragraph", "text": "x"}], "3rd-6th")
        assert any("missing section 'Test Your Knowledge'" in p for p in problems)
        assert any("missing section 'For Reflection'" in p for p in problems)


# --- study-guide redesign: excursion title + Spanish overrides ---


class TestExcursionTitle:
    @patch("app.services.llm.get_client")
    def test_excursion_guide_uses_display_name_not_llm_title(self, mock_get_client, test_db):
        suggestion = _create_suggestion(
            test_db, grade_bands=["3rd-6th"], excursion_key="panama-viejo"
        )
        research = _create_research(test_db, suggestion, _short_research_content())
        content = json.dumps(
            [
                {"type": "cover", "title": "Detective en las ruinas", "subtitle": "s"},
                {"type": "paragraph", "text": "Body."},
            ]
        )
        mock_get_client.return_value = _make_client(
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),  # LLM still emits an invented TITLE:
            _mock_msg(content),
        )

        materials, _, _ = generate_edu_materials(research, suggestion, test_db)

        assert materials[0].title == "Panama Viejo Archeological Complex"
        assert materials[0].content_blocks[0]["title"] == "Panama Viejo Archeological Complex"

    @patch("app.services.llm.get_client")
    def test_standalone_guide_keeps_llm_title(self, mock_get_client, test_db):
        suggestion = _create_suggestion(test_db, grade_bands=["3rd-6th"], excursion_key=None)
        research = _create_research(test_db, suggestion, _short_research_content())
        mock_get_client.return_value = _make_client(
            _mock_msg(OUTLINE_TEXT_WELL_FORMED),
            _mock_msg(CONTENT_TEXT),
        )

        materials, _, _ = generate_edu_materials(research, suggestion, test_db)

        assert materials[0].title == "My Title"  # from OUTLINE_TEXT_WELL_FORMED


class TestSpanishBlockOverrides:
    def test_cover_and_review_titles_overridden(self):
        en = [
            {"type": "cover", "title": "Panama Viejo Archeological Complex", "subtitle": "s"},
            {"type": "section", "title": "Test Your Knowledge"},
            {"type": "section", "title": "For Reflection"},
        ]
        es = [
            {"type": "cover", "title": "Complejo Arqueologico DeepL", "subtitle": "s2"},
            {"type": "section", "title": "Prueba tu conocimiento (DeepL)"},
            {"type": "section", "title": "Reflexiona (DeepL)"},
        ]
        apply_spanish_block_overrides(en, es, "panama-viejo")
        assert es[0]["title"] == "Complejo Arqueológico de Panamá Viejo"
        assert es[1]["title"] == "Pon a prueba tus conocimientos"
        assert es[2]["title"] == "Para reflexionar"

    def test_standalone_keeps_translated_cover_but_fixes_review_titles(self):
        en = [
            {"type": "cover", "title": "Some Standalone Guide"},
            {"type": "section", "title": "Test Your Knowledge"},
        ]
        es = [
            {"type": "cover", "title": "Una guía independiente"},
            {"type": "section", "title": "Prueba (DeepL)"},
        ]
        apply_spanish_block_overrides(en, es, None)
        assert es[0]["title"] == "Una guía independiente"
        assert es[1]["title"] == "Pon a prueba tus conocimientos"

    def test_non_review_sections_untouched(self):
        en = [{"type": "section", "title": "Historical Context"}]
        es = [{"type": "section", "title": "Contexto histórico"}]
        apply_spanish_block_overrides(en, es, "panama-viejo")
        assert es[0]["title"] == "Contexto histórico"
