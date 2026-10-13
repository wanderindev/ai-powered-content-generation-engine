"""Prompt-building functions for the edu material generation pipeline.

Pure string functions — no AI calls happen here.
"""

import json

GRADE_BAND_VOICE = {
    "3rd-6th": (
        "Write at a 4th-5th grade reading level. Use short, clear sentences and "
        "a storytelling approach. Relate historical events to things students can "
        "picture or experience. Define any vocabulary word inline the first time "
        "it appears. Keep paragraphs to 3-4 sentences."
    ),
    "4th-6th": (
        "Write at a 4th-5th grade reading level. Use short, clear sentences and "
        "a storytelling approach. Relate historical events to things students can "
        "picture or experience. Define any vocabulary word inline the first time "
        "it appears. Keep paragraphs to 3-4 sentences."
    ),
    "7th-8th": (
        "Write at a middle-school reading level. Encourage analytical thinking — "
        "ask students to consider cause and effect, compare perspectives, and "
        "identify patterns. Use moderately complex sentence structures. Introduce "
        "discipline-specific vocabulary with brief context."
    ),
    "9th-12th": (
        "Write at a high-school reading level. Reference primary sources when "
        "possible and encourage critical thinking. Students should evaluate "
        "evidence, analyze competing interpretations, and connect historical "
        "events to broader themes. Use sophisticated but accessible prose."
    ),
}

GRADE_BAND_WORD_TARGET = {
    "3rd-6th": "1,200-1,800",
    "4th-6th": "1,200-1,800",
    "7th-8th": "1,500-2,500",
    "9th-12th": "2,000-3,000",
}


# Source-briefing length per band. The briefing is the guide writer's ONLY source,
# so it must be a SUPERSET of the final guide (GRADE_BAND_WORD_TARGET), not a subset —
# ~1.4-1.5x the final upper bound gives the writer material to select from rather than
# pad or invent. Treated as a SOFT CEILING in build_edu_condense_prompt, never a target
# to shrink toward (#338). Invariant guarded by test_condensed_targets_are_supersets.
GRADE_BAND_CONDENSED_TARGET = {
    "3rd-6th": 2500,
    "4th-6th": 2500,
    "7th-8th": 3500,
    "9th-12th": 4500,
}

# Briefing size for SLIDE decks (slides redesign): the deck + its narration pass
# both read this briefing, and a 16-22 slide / 15-17 minute presentation needs a
# richer fact pool than the youngest guides get — but still bounded (the known
# failure mode: 6,000+-word inputs make the model ignore shape constraints).
SLIDES_CONDENSED_TARGET = {
    "3rd-6th": 3000,
    "4th-6th": 3000,
    "7th-8th": 3500,
    "9th-12th": 4000,
}


# Post-visit review ladder: per band, (multiple-choice questions, choices per
# question, reflection prompts). Enforced by exact counts in the prompts and
# checked warn-only by validate_review_ladder() — a bad count never fails a
# multi-minute generation run; the admin edit UI is the correction path.
GRADE_BAND_REVIEW_LADDER = {
    "3rd-6th": (4, 2, 1),
    "4th-6th": (4, 2, 1),
    "7th-8th": (6, 3, 2),
    "9th-12th": (8, 4, 3),
}

# Canonical subtitles for the two post-visit review sub-sections. Shared by the
# prompts, the ES translation override, and tests — never LLM-invented, so the
# rendered guides use identical wording across excursions.
REVIEW_SECTION_TITLES = {
    "en": ("Test Your Knowledge", "For Reflection"),
    "es": ("Pon a prueba tus conocimientos", "Para reflexionar"),
}


def _voice_for_band(grade_band: str) -> str:
    return GRADE_BAND_VOICE.get(grade_band, GRADE_BAND_VOICE["7th-8th"])


def _review_ladder_for_band(grade_band: str) -> tuple[int, int, int]:
    return GRADE_BAND_REVIEW_LADDER.get(grade_band, GRADE_BAND_REVIEW_LADDER["7th-8th"])


def _word_target_for_band(grade_band: str) -> str:
    return GRADE_BAND_WORD_TARGET.get(grade_band, "1,500-2,500")


def _condensed_target_for_band(grade_band: str) -> int:
    return GRADE_BAND_CONDENSED_TARGET.get(grade_band, 3500)


def build_edu_condense_prompt(
    research_content: str,
    suggestion_title: str,
    sub_topics: list[str] | None,
    grade_band: str,
    excursion_key: str | None,
    band_focus: str | None = None,
    target_words: int | None = None,
) -> str:
    """Build a prompt to condense research into key facts for a study guide.

    ``target_words`` overrides the per-band guide briefing size — the slides
    path reuses this prompt with its own (larger) ``SLIDES_CONDENSED_TARGET``.
    """
    target = target_words or _condensed_target_for_band(grade_band)

    sub_topics_text = ""
    if sub_topics:
        sub_topics_text = "\nKey sub-topics to prioritize: " + ", ".join(sub_topics)

    excursion_text = ""
    if excursion_key:
        excursion_text = f"\nExcursion site: {excursion_key}"

    # The per-band lens (#339): each band emphasizes a different slice of the same
    # research, so the briefing — and everything downstream — diverges by band.
    focus_text = ""
    if band_focus:
        focus_text = (
            f'\n\nGRADE-BAND FOCUS — this briefing is for a "{grade_band}" audience. '
            "Give extra weight to the research that serves this lens and frame the "
            "briefing around it, WITHOUT dropping the other substantive facts:\n"
            f"{band_focus}"
        )

    return f"""You are a curriculum researcher preparing a source briefing for a study guide writer.

The research document below is the source material for a "{grade_band}" study guide. \
Reorganize and reframe it into a focused briefing for that audience — but keep it \
NEARLY COMPLETE: preserve essentially all of the substantive facts, dates, names, \
primary-source quotes, and vocabulary. This briefing is the writer's ONLY source, so \
it must contain MORE than the final guide will, never less. Cut only genuine \
redundancy and meta-academic discussion — never drop substance to hit a length.

TOPIC: "{suggestion_title}"{excursion_text}{sub_topics_text}{focus_text}

KEEP (do not lose any of these):
- Every specific historical fact, date, and name (keep numbers and proper nouns)
- All primary-source quotes worth using, with attribution
- Vocabulary terms students should learn
- What is historically or culturally significant at the excursion site(s)
- Substantive historical debate or competing interpretations where they bear on the \
topic (older students are expected to analyze these)
- Concrete, vivid details that would engage {grade_band} students

CUT (only these):
- Verbatim repetition and redundant restatement
- Purely methodological / meta-academic discussion (how historians know, as opposed \
to what they know)
- Logistical details (addresses, hours, phone numbers, how to get there)
- Background that is common knowledge for Panama

Write the briefing as clean prose with section breaks; preserve narrative flow where \
it helps. Aim for roughly {target} words as a SOFT CEILING — go shorter only if the \
source genuinely holds less. Never pad, and never cut substance just to shrink.

RESEARCH DOCUMENT:
{research_content}"""


def build_edu_outline_prompt(
    research_content: str,
    suggestion_title: str,
    description: str | None,
    sub_topics: list[str] | None,
    grade_band: str,
    excursion_key: str | None,
    band_focus: str | None = None,
    fixed_title: str | None = None,
) -> str:
    """Build a prompt asking for a study-guide outline in plain-text format.

    ``fixed_title`` (excursion-linked materials) pins the guide title to the
    excursion's real name — the LLM echoes it instead of inventing one.
    """
    sub_topics_text = ""
    if sub_topics:
        sub_topics_text = "\n".join(f"- {t}" for t in sub_topics)
        sub_topics_text = f"\n\nSUB-TOPICS TO COVER:\n{sub_topics_text}"

    excursion_text = ""
    if excursion_key:
        excursion_text = f"\n- Excursion site: {excursion_key}"

    focus_text = ""
    if band_focus:
        focus_text = f"\n- Grade-band focus (emphasize this lens): {band_focus}"

    mc_count, choice_count, reflection_count = _review_ladder_for_band(grade_band)
    reflection_plural = "s" if reflection_count != 1 else ""
    review_title, reflection_title = REVIEW_SECTION_TITLES["en"]

    title_instruction = (
        f"{fixed_title} (echo this fixed title EXACTLY — do not invent a different one)"
        if fixed_title
        else f"Study guide title appropriate for {grade_band} students"
    )

    return f"""You are a curriculum designer for Panama In Context, creating study guides \
that accompany educational excursions to historical sites in Panama.

CONTEXT:
- Topic: "{suggestion_title}"
- Grade band: {grade_band}{excursion_text}{focus_text}
{f"- Description: {description}" if description else ""}{sub_topics_text}

VOICE AND DEPTH:
{_voice_for_band(grade_band)}

TARGET LENGTH: {_word_target_for_band(grade_band)} words total for the finished guide. \
Keep the outline proportional to this target — do not plan more content than fits.

Based on the research document below, produce a study-guide outline for \
a "{grade_band}" audience. The outline must include EXACTLY these 6 sections and \
NO additional sections:

1. Learning Objectives (3-5 measurable objectives)
2. Pre-Visit Preparation (activities and readings before the excursion — \
reference books, articles, and documents by name only)
3. Key Vocabulary (8-12 terms with brief definitions)
4. Historical Context (narrative background — the longest section)
5. What to Look For (observation prompts for the site visit — focus on \
what students should observe and WHY it matters historically or culturally; \
do NOT include directions, visiting hours, contact info, or logistics — \
this is a school excursion where all logistics are handled by the organizer)
6. Post-Visit Review — plan EXACTLY two sub-sections with these exact titles: \
"{review_title}" ({mc_count} multiple-choice questions drawing on recall and \
analysis, {choice_count} answer choices each) and "{reflection_title}" \
({reflection_count} open reflection prompt{reflection_plural})

IMPORTANT CONSTRAINTS:
- Produce EXACTLY 6 sections. Do not add an introduction, conclusion, or any extra sections.
- This guide accompanies a SCHOOL EXCURSION. All transportation, scheduling, and logistics \
are handled by Panama In Context staff. Never include how to get there, opening hours, \
phone numbers, ticket prices, or practical visitor tips.
- Section 5 should read like a historian's observation guide, not a tourist brochure.
- Never reference URLs, websites, or external links anywhere in the outline.

Use the following format — a TITLE: line, then a separator, then the full outline in markdown:

TITLE: {title_instruction}
---
Detailed section-by-section outline with key points under each section.
Use markdown formatting with ## for sections and bullet points for key points.

SOURCE BRIEFING:
{research_content}"""


def build_edu_blocks_prompt(
    research_content: str,
    outline: str,
    title: str,
    grade_band: str,
    suggestion_title: str,
    band_focus: str | None = None,
) -> str:
    """Build a prompt asking for the study guide as a JSON array of typed blocks (#340).

    The blocks (cover / section / paragraph / bullets / ordered_list / quote / map /
    timeline / image / callout / mc_question) are rendered into a branded PDF; image
    and map blocks carry an English ``image_query`` shown to the reviewing editor,
    who picks the actual photo from the media library.
    """
    focus_line = (
        f"\n- GRADE-BAND FOCUS — emphasize this lens throughout: {band_focus}" if band_focus else ""
    )
    mc_count, choice_count, reflection_count = _review_ladder_for_band(grade_band)
    review_title, reflection_title = REVIEW_SECTION_TITLES["en"]
    return f"""You are a curriculum writer for Panama In Context, creating study guides \
that accompany educational excursions to historical sites in Panama.

Produce a complete, publication-ready study guide as a STRUCTURED DOCUMENT — a JSON \
array of typed blocks that will be rendered into a branded PDF.

GUIDELINES:
- Grade band: {grade_band}
- Topic: "{suggestion_title}"{focus_line}
- {_voice_for_band(grade_band)}
- TARGET LENGTH: {_word_target_for_band(grade_band)} words of body text total. Be \
concise and purposeful — every block should earn its place. Do not pad.
- Follow the outline's sections EXACTLY — one "section" block per outline section, in \
order, and ONLY those sections (the final Post-Visit section becomes the two review \
sub-sections described below). Do not add an introduction or conclusion section.
- Write in English.

OUTPUT: Respond with ONLY a JSON array of block objects — no markdown fences, no \
commentary. The FIRST block MUST be a "cover"; then, for each outline section, a \
"section" block followed by the blocks that make up that section.

BLOCK TYPES (each object has a "type" plus the fields shown):
- "cover": {{"type": "cover", "title": guide title, "subtitle": one-line description}}
- "section": {{"type": "section", "title": the section heading}}
- "paragraph": {{"type": "paragraph", "text": a paragraph of prose}}
- "bullets": {{"type": "bullets", "items": ["short point", ...]}}
- "ordered_list": {{"type": "ordered_list", "items": ["step or item", ...]}}
- "quote": {{"type": "quote", "text": a primary-source quotation, "attribution": source}}
- "timeline": {{"type": "timeline", "events": [{{"label": "1519", "text": "what happened"}}, ...]}}
- "map": {{"type": "map", "image_query": English description of the area, \
"caption": short caption, "locations": ["place to point out", ...]}}
- "image": {{"type": "image", "image_query": English description of the ideal photo, \
"caption": short caption}}
- "callout": {{"type": "callout", "label": short box label, "text": short aside text \
(optional), "terms": [{{"term": "...", "definition": "..."}}, ...] (optional)}}
- "mc_question": {{"type": "mc_question", "question": the question, \
"choices": ["answer choice", ...]}}

STRUCTURE RULES (strict — these make the guide readable for {grade_band} students):
- Paragraphs are 3-4 sentences. NEVER place more than 2 "paragraph" blocks in a row — \
break prose up with "bullets", a "timeline", a "quote", a "callout", or an "image".
- Prefer structured blocks over prose: people, events, dates, and vocabulary belong in \
bullets, timelines, and callouts — not buried in long paragraphs.
- The cover block's "title" must be EXACTLY the TITLE given below — do not rephrase it.

BLOCK GUIDANCE:
- Use an "image" where a photo helps (e.g. in Historical Context or What to Look For). \
"image_query" is an English phrase describing the ideal photo — an editor uses it to \
pick the real photo later.
- Use "timeline" for a chronology and "map" where geography matters — at most one of \
each, and only if the content genuinely calls for it.
- Key Vocabulary is a single "callout" with "label": "Key Terms" and every term in \
"terms" — not a bullets block.
- Use a "callout" with a short "text" for a genuinely interesting aside \
("Did you know?"-style) — at most 1-2 per guide.
- Do NOT put a "title" on any block except the cover — section headings live in \
"section" blocks.

POST-VISIT REVIEW (strict): the outline's final section becomes exactly this sequence — \
a "section" block titled "{review_title}", then EXACTLY {mc_count} "mc_question" blocks \
with EXACTLY {choice_count} "choices" each (drawing on recall and analysis; one clearly \
best answer per question), then a "section" block titled "{reflection_title}", then \
EXACTLY {reflection_count} "callout" block(s), each with "label": "{reflection_title}" \
and "text" holding one open reflection prompt. Do NOT use "ordered_list" for review \
questions.

LINKS: Never include URLs, web addresses, hyperlinks, or [LINK NEEDED] placeholders \
anywhere. Reference books, articles, and documents by name only.

EXCURSION CONTEXT:
- This guide accompanies a SCHOOL EXCURSION organized by Panama In Context. All \
logistics are handled by staff. Do NOT include directions, hours, contact numbers, \
ticket prices, or visitor tips.
- The "What to Look For" section is an observation guide — what to notice and why it \
matters historically/culturally — NOT a tourist description.

TITLE: {title}

OUTLINE:
{outline}

SOURCE BRIEFING:
{research_content}"""


_LANGUAGE_NAMES = {"es": "Spanish", "en": "English"}


def build_edu_slides_prompt(
    research_content: str,
    suggestion_title: str,
    description: str | None,
    sub_topics: list[str] | None,
    grade_band: str,
    excursion_key: str | None,
    guide_title: str,
    band_focus: str | None = None,
    language: str = "es",
) -> str:
    """Build a prompt asking Sonnet for a slide deck's CONTENT (JSON, no narration).

    Stage 1 of the two-stage deck flow (slides redesign): on-screen content is
    generated from the condensed RESEARCH briefing — not the study guide, which
    is itself a summary and starved the deck down to ~7 minutes. A human edits
    and approves the slides in the admin (picking real photos for the
    image_query placeholders), then the narration pass
    (:func:`build_edu_narration_prompt`) writes Diego's script against the
    final slides.
    """
    language_name = _LANGUAGE_NAMES.get(language, "Spanish")
    excursion_text = f"\n- Excursion site: {excursion_key}" if excursion_key else ""
    description_text = f"\n- About the topic: {description}" if description else ""
    sub_topics_text = ""
    if sub_topics:
        sub_topics_text = "\n- Key sub-topics to cover: " + ", ".join(sub_topics)
    focus_text = (
        f"\n- GRADE-BAND FOCUS — emphasize this lens throughout: {band_focus}" if band_focus else ""
    )

    return f"""You are a presentation designer for Panama In Context, creating the slides of \
a pre-visit video that introduces an educational excursion to a historical site in Panama.

The finished deck becomes a ~15-minute video narrated by Diego, the excursion guide, that \
teachers play in class a few days before the field trip. It should feel like Diego personally \
introducing the topic — warm, vivid, and engaging — NOT like a textbook. The students also \
receive a study guide titled "{guide_title}"; the deck is its visual companion.

CONTEXT:
- Topic: "{suggestion_title}"
- Grade band: {grade_band}{excursion_text}{description_text}{sub_topics_text}{focus_text}
- Source: the research briefing below (use it as your ONLY source of facts)

READING LEVEL / STANCE:
{_voice_for_band(grade_band)}

LANGUAGE: Write EVERY title and body line in {language_name}. Image descriptions may be in \
English (they are used to search an image library).

OUTPUT: Respond with ONLY a JSON array of slide objects — no markdown fences, no commentary. \
Each object has these fields:
- "template": one of "title", "section", "bullets", "two-col-image-left", \
"two-col-image-right", "full-image", "timeline", "map", "closing"
- "title": the on-screen slide title (short)
- "body": an array of short on-screen lines/bullets (may be empty, e.g. for title/section slides)
- "image_query": a brief English description of the ideal image, or null
Do NOT write narration — the spoken script is authored in a later pass, after an editor \
reviews these slides.

TEMPLATE GUIDE:
- "title": opening slide (topic + grade band). Use once, first.
- "section": a divider between major parts of the deck.
- "bullets": a title with 3-5 short bullets.
- "two-col-image-left" / "two-col-image-right": a short paragraph or bullets beside one photo.
- "full-image": a full-bleed photo with a one-line caption in "body".
- "timeline": "body" is a list of "year — milestone" lines.
- "map": "body" is a list of locations to point out.
- "closing": a closing / thank-you slide. Use once, last.

HARD LIMITS (the canvas is a fixed 1280x720 slide — text that exceeds these gets cut off):
- "title": at most 60 characters.
- "bullets" body: 3-5 bullets, each at most 90 characters.
- "two-col-*" body: one short lead line (at most 160 characters) plus at most 3 bullets of \
at most 80 characters each.
- "timeline" body: 4-5 "year — milestone" lines, milestone text at most 45 characters.
- "map" body: at most 6 locations, each at most 50 characters.
- "title" / "section" / "full-image" / "closing" body: at most 2 short lines.

DECK SHAPE: 16-22 slides — enough substance for 15+ minutes of narration. A natural arc: \
title → what we'll explore → 2-4 major parts separated by "section" dividers (historical \
context; key people and events; vocabulary for the visit; what to look for at the site and \
why it matters) → recap → closing. Include at least one "timeline" and one "map" slide when \
the content supports them, and vary "bullets" with "two-col-*" and "full-image" slides so \
the deck breathes visually.

INCLUDE: historical context, key facts/dates/figures, vocabulary for the visit, and \
observation prompts (what to look for at the site and why it matters).
EXCLUDE: student activities, worksheets, post-visit questions, homework.

SOURCE BRIEFING:
{research_content}"""


def build_edu_slides_subset_prompt(
    deck: list[dict],
    target_positions: list[int],
    research_content: str,
    suggestion_title: str,
    grade_band: str,
    excursion_key: str | None,
    band_focus: str | None = None,
    language: str = "es",
) -> str:
    """Build a prompt to re-roll the CONTENT of a subset of an existing deck.

    ``deck`` is the full current deck (position / template / title / body) shown
    for continuity; the model rewrites only ``target_positions`` and returns one
    object per targeted position (``position`` + template/title/body/image_query),
    keeping the deck's slide count and overall arc intact. Same canvas HARD LIMITS
    as :func:`build_edu_slides_prompt`.
    """
    language_name = _LANGUAGE_NAMES.get(language, "Spanish")
    excursion_text = f"\n- Excursion site: {excursion_key}" if excursion_key else ""
    focus_text = (
        f"\n- GRADE-BAND FOCUS — emphasize this lens throughout: {band_focus}" if band_focus else ""
    )
    targets = sorted(set(target_positions))
    deck_json = json.dumps(deck, ensure_ascii=False, indent=1)

    return f"""You are a presentation designer for Panama In Context revising the slides of a \
pre-visit video that introduces an educational excursion to a historical site in Panama.

Below is the FULL current deck. Rewrite the on-screen content for ONLY these slide positions: \
{targets}. Keep every other slide as-is (shown for continuity), keep each targeted slide in \
its place in the deck's arc, and do NOT add or remove slides. Improve the targeted slides — \
clearer, more vivid, better-structured content grounded in the briefing — while staying \
consistent with their neighbors.

CONTEXT:
- Topic: "{suggestion_title}"
- Grade band: {grade_band}{excursion_text}{focus_text}
- Source: the research briefing below (use it as your ONLY source of facts)

READING LEVEL / STANCE:
{_voice_for_band(grade_band)}

LANGUAGE: Write EVERY title and body line in {language_name}. Image descriptions may be in \
English (they are used to search an image library).

OUTPUT: Respond with ONLY a JSON array — no markdown fences, no commentary — with EXACTLY one \
object for EACH targeted position {targets} and NO others. Each object has these fields:
- "position": the targeted slide position (integer)
- "template": one of "title", "section", "bullets", "two-col-image-left", \
"two-col-image-right", "full-image", "timeline", "map", "closing"
- "title": the on-screen slide title (short)
- "body": an array of short on-screen lines/bullets (may be empty, e.g. for title/section slides)
- "image_query": a brief English description of the ideal image, or null

TEMPLATE GUIDE:
- "title": opening slide. "section": a divider. "bullets": a title with 3-5 short bullets.
- "two-col-image-left" / "two-col-image-right": a short paragraph or bullets beside one photo.
- "full-image": a full-bleed photo with a one-line caption in "body".
- "timeline": "body" is a list of "year — milestone" lines. "map": "body" is a list of locations.
- "closing": a closing / thank-you slide.
Prefer keeping a targeted slide's existing template unless a different one clearly fits better.

HARD LIMITS (the canvas is a fixed 1280x720 slide — text that exceeds these gets cut off):
- "title": at most 60 characters.
- "bullets" body: 3-5 bullets, each at most 90 characters.
- "two-col-*" body: one short lead line (at most 160 characters) plus at most 3 bullets of \
at most 80 characters each.
- "timeline" body: 4-5 "year — milestone" lines, milestone text at most 45 characters.
- "map" body: at most 6 locations, each at most 50 characters.
- "title" / "section" / "full-image" / "closing" body: at most 2 short lines.

EXCLUDE: student activities, worksheets, post-visit questions, homework.

THE FULL DECK (rewrite only positions {targets}):
{deck_json}

SOURCE BRIEFING:
{research_content}"""


def build_edu_narration_prompt(
    slides: list[dict],
    briefing: str,
    suggestion_title: str,
    grade_band: str,
    excursion_key: str | None,
    band_focus: str | None = None,
    language: str = "es",
    target_positions: list[int] | None = None,
) -> str:
    """Build a prompt asking Sonnet for Diego's narration over an approved deck.

    Stage 2 of the two-stage deck flow: ``slides`` is the human-approved deck
    (position / template / title / body), ``briefing`` the same condensed
    research the content was written from. The script must walk through every
    on-screen line — the old single-pass flow skipped bullets ("these terms are
    important…") — and hit a total word budget that lands the video at 15-17
    minutes.

    ``target_positions`` (1-based, a subset) switches to a **targeted rewrite**:
    the whole deck (including each slide's existing narration) is shown for tone
    and continuity, but the model rewrites and returns narration for ONLY those
    positions. Used to re-roll a few slides without paying for the whole deck.
    """
    language_name = _LANGUAGE_NAMES.get(language, "Spanish")
    excursion_text = f"\n- Excursion site: {excursion_key}" if excursion_key else ""
    focus_text = (
        f"\n- GRADE-BAND FOCUS — emphasize this lens throughout: {band_focus}" if band_focus else ""
    )
    slides_json = json.dumps(slides, ensure_ascii=False, indent=1)

    if target_positions:
        targets = sorted(set(target_positions))
        return f"""You are revising the narration script for a Panama In Context pre-visit \
video. Diego, the excursion guide, narrates a slide deck that teachers play in class a few \
days before a field trip to a historical site in Panama.

The slides below are FINAL (already reviewed by an editor — do not propose changes). Each \
slide includes the narration Diego currently says over it. Rewrite the spoken narration for \
ONLY these slide positions: {targets}. Leave every other slide's narration exactly as-is \
(it is shown so your rewrite matches the surrounding tone, pacing, and transitions).

CONTEXT:
- Topic: "{suggestion_title}"
- Grade band: {grade_band}{excursion_text}{focus_text}

READING LEVEL / STANCE:
{_voice_for_band(grade_band)}

OUTPUT: Respond with ONLY a JSON array — no markdown fences, no commentary — with EXACTLY \
one object for EACH targeted position {targets} and NO others:
[{{"position": <targeted position>, "narration": "<what Diego says>"}}]

NARRATION RULES (the spoken script for Diego's cloned voice):
- Natural, spoken {language_name} — how Diego would actually talk to students: warm and clear.
- NO stage directions, parentheticals, or pronunciation guides — only the words Diego speaks.
- WALK THROUGH EVERY ON-SCREEN LINE of the targeted slide: expand each bullet, timeline \
milestone, or map location into 1-3 spoken sentences with facts from the briefing. On a \
vocabulary slide, say and define EVERY term shown. Never wave at a list.
- Keep the same natural transitions into the neighboring slides so the rewrite is seamless.

PER-SLIDE LENGTH (match the deck's existing pace):
- "title" / "section" / "closing" slides: about 60-90 words each.
- Content slides (bullets, two-col, full-image, timeline, map): about 120-170 words each.

THE FULL DECK (each slide shows its current narration; rewrite only {targets}):
{slides_json}

SOURCE BRIEFING (the facts to teach from — same source the slides were written from):
{briefing}"""

    return f"""You are writing the narration script for a Panama In Context pre-visit video. \
Diego, the excursion guide, narrates a slide deck that teachers play in class a few days \
before a field trip to a historical site in Panama.

The slides below are FINAL (already reviewed by an editor — do not propose changes). Write \
what Diego SAYS over each slide.

CONTEXT:
- Topic: "{suggestion_title}"
- Grade band: {grade_band}{excursion_text}{focus_text}

READING LEVEL / STANCE:
{_voice_for_band(grade_band)}

OUTPUT: Respond with ONLY a JSON array — no markdown fences, no commentary — with EXACTLY \
one object per slide, covering every position from 1 to {len(slides)}:
[{{"position": <slide position>, "narration": "<what Diego says>"}}]

NARRATION RULES (the spoken script for Diego's cloned voice):
- Natural, spoken {language_name} — how Diego would actually talk to students: warm and clear.
- NO stage directions, parentheticals, or pronunciation guides — only the words Diego speaks.
- WALK THROUGH EVERY ON-SCREEN LINE: expand each bullet, timeline milestone, or map location \
into 1-3 spoken sentences with facts from the briefing. On a vocabulary slide, say and define \
EVERY term shown. Never wave at a list ("these terms are important") without covering it.
- Each slide's narration is a complete thought and ends with a natural transition into the \
next slide.

LENGTH BUDGET (the video should run 15-17 minutes at a natural pace):
- Total: 2,300-2,600 words of {language_name} across all slides.
- "title" / "section" / "closing" slides: about 60-90 words each.
- Content slides (bullets, two-col, full-image, timeline, map): about 120-170 words each.

THE FINAL SLIDES:
{slides_json}

SOURCE BRIEFING (the facts to teach from — same source the slides were written from):
{briefing}"""
