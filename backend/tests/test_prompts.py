"""Unit tests for pure-string prompt builders.

These are deterministic string assemblers — no LLM calls, no DB.
Tests follow the oracle rule: assertions derived from the spec/contract,
not from reading the implementation.
"""

from app.prompts.edu_material_generation import (
    GRADE_BAND_CONDENSED_TARGET,
    GRADE_BAND_REVIEW_LADDER,
    GRADE_BAND_VOICE,
    GRADE_BAND_WORD_TARGET,
    REVIEW_SECTION_TITLES,
    _condensed_target_for_band,
    _review_ladder_for_band,
    _voice_for_band,
    _word_target_for_band,
    build_edu_blocks_prompt,
    build_edu_condense_prompt,
    build_edu_outline_prompt,
    build_edu_narration_prompt,
    build_edu_slides_prompt,
)
from app.prompts.image_generation import build_image_prompt
from app.prompts.suggestion_generation import build_historical_suggestion_prompt

# --- build_historical_suggestion_prompt ---


class TestBuildHistoricalSuggestionPrompt:
    """Tests for app.prompts.suggestion_generation.build_historical_suggestion_prompt."""

    def test_includes_taxonomy_category_and_count(self):
        prompt = build_historical_suggestion_prompt(
            taxonomy_name="Colonial Era",
            taxonomy_description="The Spanish colonial period in Panama",
            category_name="Early Settlements",
            category_description="Founding of Spanish outposts",
            num_suggestions=3,
            existing_titles=[],
        )
        assert "Colonial Era" in prompt
        assert "Early Settlements" in prompt
        assert "3" in prompt

    def test_empty_existing_titles_renders_none_yet(self):
        prompt = build_historical_suggestion_prompt(
            taxonomy_name="T",
            taxonomy_description="td",
            category_name="C",
            category_description="cd",
            num_suggestions=1,
            existing_titles=[],
        )
        assert "(none yet)" in prompt

    def test_existing_titles_each_on_its_own_dash_line(self):
        prompt = build_historical_suggestion_prompt(
            taxonomy_name="T",
            taxonomy_description="td",
            category_name="C",
            category_description="cd",
            num_suggestions=1,
            existing_titles=["Title One", "Title Two", "Title Three"],
        )
        assert "- Title One" in prompt
        assert "- Title Two" in prompt
        assert "- Title Three" in prompt
        # "(none yet)" must not be present when titles exist
        assert "(none yet)" not in prompt

    def test_none_taxonomy_description_renders_na(self):
        prompt = build_historical_suggestion_prompt(
            taxonomy_name="T",
            taxonomy_description=None,
            category_name="C",
            category_description="cd",
            num_suggestions=1,
            existing_titles=[],
        )
        assert "Taxonomy Description: N/A" in prompt

    def test_none_category_description_renders_na(self):
        prompt = build_historical_suggestion_prompt(
            taxonomy_name="T",
            taxonomy_description="td",
            category_name="C",
            category_description=None,
            num_suggestions=1,
            existing_titles=[],
        )
        assert "Category Description: N/A" in prompt


# --- build_image_prompt ---


class TestBuildImagePrompt:
    """Tests for app.prompts.image_generation.build_image_prompt."""

    def test_includes_title_verbatim(self):
        prompt = build_image_prompt(
            title="The Panama Canal: A Historical Overview",
            content="Some article content.",
        )
        assert "The Panama Canal: A Historical Overview" in prompt

    def test_short_content_appears_in_full(self):
        content = "Short article body about colonial history."
        prompt = build_image_prompt(title="Title", content=content)
        assert content in prompt

    def test_long_content_truncated_at_12000_chars(self):
        # 12001 chars: char at index 12000 should be cut.
        # Use a recognizable distinguishing character at the boundary.
        body = "a" * 11999 + "B" + "C"  # len = 12001; index 11999='B', 12000='C'
        prompt = build_image_prompt(title="Title", content=body)
        # The 'B' at index 11999 must remain
        assert "B" in prompt
        # The 'C' at index 12000 must be cut off
        # The truncation: only first 12000 chars kept.
        # Verify the substring "BC" (positions 11999-12000) is NOT present.
        assert "BC" not in prompt

    def test_contains_aspect_ratio_style_anchor(self):
        prompt = build_image_prompt(title="T", content="c")
        assert "Aspect ratio: 16:9 (landscape)" in prompt


# --- edu_material_generation helpers ---


class TestVoiceForBand:
    """Tests for _voice_for_band."""

    def test_known_band_returns_its_voice(self):
        assert _voice_for_band("3rd-6th") == GRADE_BAND_VOICE["3rd-6th"]

    def test_unknown_band_falls_back_to_7th_8th(self):
        assert _voice_for_band("unknown-band") == GRADE_BAND_VOICE["7th-8th"]


class TestWordTargetForBand:
    """Tests for _word_target_for_band."""

    def test_known_band_returns_its_value(self):
        assert _word_target_for_band("3rd-6th") == GRADE_BAND_WORD_TARGET["3rd-6th"]
        assert _word_target_for_band("9th-12th") == GRADE_BAND_WORD_TARGET["9th-12th"]

    def test_unknown_band_falls_back_to_default(self):
        assert _word_target_for_band("unknown") == "1,500-2,500"


class TestCondensedTargetForBand:
    """Tests for _condensed_target_for_band."""

    def test_known_band_returns_its_value(self):
        assert _condensed_target_for_band("3rd-6th") == GRADE_BAND_CONDENSED_TARGET["3rd-6th"]
        assert _condensed_target_for_band("9th-12th") == GRADE_BAND_CONDENSED_TARGET["9th-12th"]

    def test_unknown_band_falls_back_to_default(self):
        # Default mirrors the 7th-8th band (also the default voice/word band) (#338).
        assert _condensed_target_for_band("unknown") == 3500


# --- build_edu_condense_prompt ---


class TestBuildEduCondensePrompt:
    """Tests for build_edu_condense_prompt."""

    def test_includes_title_and_word_target(self):
        prompt = build_edu_condense_prompt(
            research_content="rc",
            suggestion_title="My Suggestion",
            sub_topics=None,
            grade_band="7th-8th",
            excursion_key=None,
        )
        assert "My Suggestion" in prompt
        # The band's condensed target (from the module-level dict) is injected.
        assert str(GRADE_BAND_CONDENSED_TARGET["7th-8th"]) in prompt

    def test_sub_topics_section_included_when_non_empty(self):
        prompt = build_edu_condense_prompt(
            research_content="rc",
            suggestion_title="S",
            sub_topics=["Alpha", "Beta"],
            grade_band="7th-8th",
            excursion_key=None,
        )
        assert "Key sub-topics to prioritize: Alpha, Beta" in prompt

    def test_sub_topics_section_omitted_when_none(self):
        prompt = build_edu_condense_prompt(
            research_content="rc",
            suggestion_title="S",
            sub_topics=None,
            grade_band="7th-8th",
            excursion_key=None,
        )
        assert "Key sub-topics to prioritize" not in prompt

    def test_sub_topics_section_omitted_when_empty_list(self):
        prompt = build_edu_condense_prompt(
            research_content="rc",
            suggestion_title="S",
            sub_topics=[],
            grade_band="7th-8th",
            excursion_key=None,
        )
        assert "Key sub-topics to prioritize" not in prompt

    def test_excursion_key_included_when_set(self):
        prompt = build_edu_condense_prompt(
            research_content="rc",
            suggestion_title="S",
            sub_topics=None,
            grade_band="7th-8th",
            excursion_key="panama_viejo",
        )
        assert "Excursion site: panama_viejo" in prompt

    def test_excursion_key_omitted_when_none(self):
        prompt = build_edu_condense_prompt(
            research_content="rc",
            suggestion_title="S",
            sub_topics=None,
            grade_band="7th-8th",
            excursion_key=None,
        )
        assert "Excursion site:" not in prompt

    def test_band_focus_included_when_set(self):
        prompt = build_edu_condense_prompt(
            research_content="rc",
            suggestion_title="S",
            sub_topics=None,
            grade_band="7th-8th",
            excursion_key="panama-viejo",
            band_focus="Examine trade networks and piracy.",
        )
        assert "GRADE-BAND FOCUS" in prompt
        assert "Examine trade networks and piracy." in prompt

    def test_band_focus_omitted_when_none(self):
        prompt = build_edu_condense_prompt(
            research_content="rc",
            suggestion_title="S",
            sub_topics=None,
            grade_band="7th-8th",
            excursion_key="panama-viejo",
            band_focus=None,
        )
        assert "GRADE-BAND FOCUS" not in prompt


# --- build_edu_outline_prompt ---


class TestBuildEduOutlinePrompt:
    """Tests for build_edu_outline_prompt."""

    def test_includes_6_sections_constraint(self):
        prompt = build_edu_outline_prompt(
            research_content="rc",
            suggestion_title="S",
            description=None,
            sub_topics=None,
            grade_band="7th-8th",
            excursion_key=None,
        )
        assert "EXACTLY 6 sections" in prompt

    def test_includes_format_anchors_title_and_separator(self):
        prompt = build_edu_outline_prompt(
            research_content="rc",
            suggestion_title="S",
            description=None,
            sub_topics=None,
            grade_band="7th-8th",
            excursion_key=None,
        )
        assert "TITLE:" in prompt
        assert "---" in prompt

    def test_sub_topics_section_included_when_non_empty(self):
        prompt = build_edu_outline_prompt(
            research_content="rc",
            suggestion_title="S",
            description=None,
            sub_topics=["Topic A", "Topic B"],
            grade_band="7th-8th",
            excursion_key=None,
        )
        assert "SUB-TOPICS TO COVER:" in prompt
        assert "- Topic A" in prompt
        assert "- Topic B" in prompt

    def test_sub_topics_section_omitted_when_none(self):
        prompt = build_edu_outline_prompt(
            research_content="rc",
            suggestion_title="S",
            description=None,
            sub_topics=None,
            grade_band="7th-8th",
            excursion_key=None,
        )
        assert "SUB-TOPICS TO COVER:" not in prompt

    def test_sub_topics_section_omitted_when_empty_list(self):
        prompt = build_edu_outline_prompt(
            research_content="rc",
            suggestion_title="S",
            description=None,
            sub_topics=[],
            grade_band="7th-8th",
            excursion_key=None,
        )
        assert "SUB-TOPICS TO COVER:" not in prompt


# --- build_edu_blocks_prompt (#340) ---


class TestBuildEduBlocksPrompt:
    """Tests for build_edu_blocks_prompt."""

    def test_includes_title_grade_band_voice_and_word_target(self):
        prompt = build_edu_blocks_prompt(
            research_content="rc",
            outline="ol",
            title="My Guide Title",
            grade_band="3rd-6th",
            suggestion_title="S",
        )
        assert "My Guide Title" in prompt
        assert "3rd-6th" in prompt
        assert GRADE_BAND_VOICE["3rd-6th"] in prompt
        assert GRADE_BAND_WORD_TARGET["3rd-6th"] in prompt

    def test_asks_for_json_blocks_with_image_queries(self):
        prompt = build_edu_blocks_prompt(
            research_content="rc",
            outline="ol",
            title="T",
            grade_band="7th-8th",
            suggestion_title="S",
        )
        assert "JSON array" in prompt
        assert '"cover"' in prompt
        assert "image_query" in prompt

    def test_band_focus_included_when_set(self):
        prompt = build_edu_blocks_prompt(
            research_content="rc",
            outline="ol",
            title="T",
            grade_band="7th-8th",
            suggestion_title="S",
            band_focus="Examine trade networks.",
        )
        assert "GRADE-BAND FOCUS" in prompt
        assert "Examine trade networks." in prompt


# --- build_edu_slides_prompt ---


class TestBuildEduSlidesPrompt:
    """Tests for build_edu_slides_prompt (stage 1: content only, research-based)."""

    def _prompt(self, **overrides):
        kwargs = dict(
            research_content="datos de investigacion",
            suggestion_title="S",
            description="Sobre el tema",
            sub_topics=["A", "B"],
            grade_band="7th-8th",
            excursion_key=None,
            guide_title="gt",
        )
        kwargs.update(overrides)
        return build_edu_slides_prompt(**kwargs)

    def test_targets_16_to_22_slides(self):
        assert "16-22 slides" in self._prompt()

    def test_requests_json_without_narration(self):
        prompt = self._prompt()
        assert "JSON array of slide objects" in prompt
        assert "Do NOT write narration" in prompt
        assert "Spanish" in prompt  # default language

    def test_sources_from_research_briefing(self):
        prompt = self._prompt()
        assert "SOURCE BRIEFING:" in prompt
        assert "datos de investigacion" in prompt
        assert "research briefing below" in prompt

    def test_hard_limits_present(self):
        prompt = self._prompt()
        assert "HARD LIMITS" in prompt
        assert "at most 60 characters" in prompt

    def test_excursion_key_included_when_set(self):
        assert "Excursion site: panama_viejo" in self._prompt(excursion_key="panama_viejo")

    def test_excursion_key_omitted_when_none(self):
        assert "Excursion site:" not in self._prompt()

    def test_band_focus_injected_when_set(self):
        assert "GRADE-BAND FOCUS" in self._prompt(band_focus="enfoque")
        assert "GRADE-BAND FOCUS" not in self._prompt()


class TestBuildEduNarrationPrompt:
    """Tests for build_edu_narration_prompt (stage 2: script over final slides)."""

    SLIDES = [
        {"position": 1, "template": "title", "title": "Inicio", "body": None},
        {"position": 2, "template": "bullets", "title": "Ideas", "body": "a\nb"},
    ]

    def _prompt(self, **overrides):
        kwargs = dict(
            slides=self.SLIDES,
            briefing="briefing de hechos",
            suggestion_title="S",
            grade_band="7th-8th",
            excursion_key="panama-viejo",
        )
        kwargs.update(overrides)
        return build_edu_narration_prompt(**kwargs)

    def test_covers_every_position_and_budget(self):
        prompt = self._prompt()
        assert "from 1 to 2" in prompt
        assert "2,300-2,600 words" in prompt
        assert "15-17 minutes" in prompt

    def test_walkthrough_rule_present(self):
        prompt = self._prompt()
        assert "WALK THROUGH EVERY ON-SCREEN LINE" in prompt
        assert "EVERY term" in prompt

    def test_embeds_slides_and_briefing(self):
        prompt = self._prompt()
        assert '"position": 1' in prompt
        assert "briefing de hechos" in prompt

    def test_no_stage_directions_rule_kept(self):
        assert "NO stage directions" in self._prompt()


# --- study-guide redesign: review ladder, fixed title, no links ---


class TestReviewLadderForBand:
    def test_known_bands(self):
        assert _review_ladder_for_band("3rd-6th") == (4, 2, 1)
        assert _review_ladder_for_band("4th-6th") == (4, 2, 1)
        assert _review_ladder_for_band("7th-8th") == (6, 3, 2)
        assert _review_ladder_for_band("9th-12th") == (8, 4, 3)

    def test_unknown_band_falls_back_to_7th_8th(self):
        assert _review_ladder_for_band("nope") == GRADE_BAND_REVIEW_LADDER["7th-8th"]

    def test_canonical_review_titles_paired(self):
        assert len(REVIEW_SECTION_TITLES["en"]) == len(REVIEW_SECTION_TITLES["es"]) == 2


def _outline_prompt(**overrides):
    kwargs = dict(
        research_content="rc",
        suggestion_title="S",
        description=None,
        sub_topics=None,
        grade_band="7th-8th",
        excursion_key=None,
    )
    kwargs.update(overrides)
    return build_edu_outline_prompt(**kwargs)


class TestOutlinePromptRedesign:
    def test_never_asks_for_links(self):
        prompt = _outline_prompt()
        assert "[LINK NEEDED]" not in prompt
        assert "direct URL" not in prompt
        assert "Google Maps" not in prompt
        assert "Never reference URLs" in prompt

    def test_review_ladder_counts_per_band(self):
        prompt = _outline_prompt(grade_band="9th-12th")
        assert '"Test Your Knowledge" (8 multiple-choice questions' in prompt
        assert "4 answer choices each" in prompt
        assert '"For Reflection" (3 open reflection prompts)' in prompt

    def test_review_ladder_singular_reflection(self):
        prompt = _outline_prompt(grade_band="3rd-6th")
        assert "(1 open reflection prompt)" in prompt

    def test_fixed_title_is_echoed_verbatim(self):
        prompt = _outline_prompt(fixed_title="Panama Viejo Archeological Complex")
        assert "TITLE: Panama Viejo Archeological Complex" in prompt
        assert "title appropriate for" not in prompt

    def test_without_fixed_title_llm_invents_one(self):
        prompt = _outline_prompt()
        assert "Study guide title appropriate for 7th-8th students" in prompt


def _blocks_prompt(**overrides):
    kwargs = dict(
        research_content="rc",
        outline="ol",
        title="T",
        grade_band="3rd-6th",
        suggestion_title="S",
    )
    kwargs.update(overrides)
    return build_edu_blocks_prompt(**kwargs)


class TestBlocksPromptRedesign:
    def test_includes_new_block_types(self):
        prompt = _blocks_prompt()
        assert '"callout"' in prompt
        assert '"mc_question"' in prompt

    def test_structure_rules_limit_consecutive_paragraphs(self):
        prompt = _blocks_prompt()
        assert 'NEVER place more than 2 "paragraph" blocks in a row' in prompt
        assert "Paragraphs are 3-4 sentences" in prompt

    def test_key_vocabulary_is_a_terms_callout(self):
        prompt = _blocks_prompt()
        assert 'Key Vocabulary is a single "callout"' in prompt
        assert "not a bullets block" in prompt

    def test_review_ladder_counts_per_band(self):
        prompt = _blocks_prompt(grade_band="7th-8th")
        assert 'EXACTLY 6 "mc_question" blocks' in prompt
        assert 'EXACTLY 3 "choices" each' in prompt
        assert 'EXACTLY 2 "callout" block(s)' in prompt
        assert '"Test Your Knowledge"' in prompt
        assert '"For Reflection"' in prompt

    def test_never_asks_for_links(self):
        prompt = _blocks_prompt()
        assert "RESOURCE LINKS" not in prompt
        assert "include a direct URL" not in prompt
        assert "Never include URLs" in prompt

    def test_cover_title_must_match_exactly(self):
        prompt = _blocks_prompt(title="Fixed Name")
        assert "must be EXACTLY the TITLE" in prompt
        assert "TITLE: Fixed Name" in prompt
