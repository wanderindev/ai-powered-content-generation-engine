"""Prompt-building functions for the article generation pipeline.

Pure string functions — no AI calls happen here.
"""


def build_focus_condense_prompt(
    research_content: str,
    outline: str,
    title: str,
    suggestion_title: str,
    suggestion_main_topic: str | None,
    suggestion_point_of_view: str,
) -> str:
    """Build a prompt to condense research into an outline-targeted briefing (#102).

    The article pipeline feeds the same 5000-8000-word research the edu pipeline
    condenses; at that length the writer drifts off its word-count and section
    constraints. But a flat single-article condensation discards facts that
    belong to *other* articles in a series and is blind to what *this* article
    actually covers. So the condensation is keyed to the article's outline: a
    short broad summary for context (the "periphery"), then detailed retention of
    every fact the outline's sections need (the "focus"). This runs on Sonnet,
    not Haiku — selective high-fidelity extraction keyed to an outline is a
    routing-plus-fidelity task Haiku is weaker at than flat summarization.
    """
    main_topic_line = f"\nMain topic: {suggestion_main_topic}" if suggestion_main_topic else ""
    return f"""You are a researcher preparing source material for a history writer.

The research document below is too long to use directly. Condense it into a \
focused briefing of approximately 1,800 words for the writer of ONE specific \
article. The article's outline (below) defines what this article covers — other \
material in the research may belong to sibling articles and is only context here.

ARTICLE TITLE: "{title}"
TOPIC: "{suggestion_title}"{main_topic_line}
Point of view to support: {suggestion_point_of_view}

ARTICLE OUTLINE (what THIS article must cover):
{outline}

Produce the briefing in two zones:

1. CONTEXT (approximately 300-400 words): a broad summary of the whole topic so \
the writer understands where this article sits — the surrounding events, periods, \
and people, even those this article will not cover in depth.

2. FOCUS (the remainder, ~1,400 words): the detailed source material for the \
outline's sections. For everything the outline covers, preserve in full:
- Every relevant historical fact, date, name, and figure (keep numbers and proper \
nouns EXACTLY — do not round, paraphrase, or drop them)
- The cause-and-effect narrative thread those sections will follow
- The best 2-3 primary-source quotes worth using, with attribution

EXCLUDE from both zones:
- Redundant information or repetitive examples
- Historiographic debates or academic methodological discussion

Label the two zones with "## Context" and "## Focus" headings. Stay under 1,800 \
words total. Preserve facts faithfully — when in doubt about a fact relevant to \
the outline, keep it.

RESEARCH DOCUMENT:
{research_content}"""


def build_outline_prompt(
    research_content: str,
    suggestion_title: str,
    suggestion_main_topic: str | None,
    suggestion_point_of_view: str,
    category_name: str,
    category_description: str | None,
    taxonomy_name: str,
) -> str:
    """Build a prompt that asks Claude to produce article outline(s) from research."""
    word_count = len(research_content.split())
    split_instruction = ""
    if word_count > 4000:
        split_instruction = (
            "\n\nIMPORTANT: This research document is substantial "
            f"({word_count} words). If the material covers multiple distinct "
            "themes or time periods, split it into 2-4 separate articles that "
            "form a series. Each article should be self-contained but reference "
            "the series. If the material is cohesive, a single article is fine."
        )

    return f"""You are an editor for a bilingual Panama history education platform.

CONTEXT:
- Taxonomy: "{taxonomy_name}"
- Category: "{category_name}"{f" — {category_description}" if category_description else ""}
- Original suggestion: "{suggestion_title}"
- Point of view: {suggestion_point_of_view}
- Main topic: {suggestion_main_topic or "See research below"}
{split_instruction}

Based on the research document below, produce an outline for one or more articles.
Each article should target approximately 2000 words of final prose.

Respond with ONLY valid JSON in this exact format:
{{
  "articles": [
    {{
      "title": "Article title (compelling, specific, not generic)",
      "summary": "2-3 sentence summary of what this article covers",
      "outline": "Detailed section-by-section outline with key points to cover under each section. Use markdown formatting with ## for sections and bullet points for key points.",
      "estimated_word_count": 2000
    }}
  ]
}}

If producing a series, order the articles chronologically or thematically.
The first article in a series should provide foundational context.

RESEARCH DOCUMENT:
{research_content}"""


def build_article_prompt(
    research_content: str,
    outline: str,
    title: str,
    suggestion_point_of_view: str,
    category_name: str,
    taxonomy_name: str,
    series_context: str | None = None,
) -> str:
    """Build a prompt that asks Claude to write a complete article from an outline."""
    series_instruction = ""
    if series_context:
        series_instruction = f"\n\nSERIES CONTEXT:\n{series_context}\n"

    return f"""You are a writer for Panama In Context, a blog dedicated to exploring how historical events and cultural elements have shaped Panama's national identity. You have a B.S. in Historic Tourism with emphasis in Cultural Promotion, and you're also an expert in world history. You're 30 years old and deeply passionate about sharing Panama's story.

Write a complete, publication-ready article based on the outline and research below.

GUIDELINES:
- Write approximately 2000 words of engaging, narrative prose
- Write at a college level but keep it accessible, knowledgeable but casual
- Maintain direct and personal engagement with the reader
- Prefer active voice: lead with who or what performs the action. Reserve passive voice for when the actor is unknown or genuinely unimportant (e.g., "The fortifications were built over decades").
- Point of view: {suggestion_point_of_view}
- Category: {category_name} (Taxonomy: {taxonomy_name})
- Use specific dates, names, and figures from the research
- Include section headings (## Markdown format)
- Do NOT include a title heading (the title is stored separately)
- Do NOT include a references section (sources are tracked separately)
- Do NOT use em dashes (—). Rephrase with commas, parentheses, a colon, or two separate sentences instead. (En dashes in number ranges like 1903–1914 are fine.) A rare em dash that is genuinely the clearest choice is tolerable, but avoid them by default.
- Write in English
{series_instruction}
TITLE: {title}

OUTLINE:
{outline}

RESEARCH DOCUMENT:
{research_content}"""


def build_excerpt_prompt(title: str, content_preview: str) -> str:
    """Build a prompt for generating a 2-sentence excerpt (for Haiku)."""
    return f"""Write a compelling 2-sentence excerpt for the following article.
The excerpt should hook readers and summarize the key theme.
Respond with ONLY the 2 sentences, no quotes or labels.

TITLE: {title}

ARTICLE (first 1500 chars):
{content_preview[:1500]}"""


def build_summary_prompt(title: str, content_preview: str) -> str:
    """Build a prompt for generating a 3-sentence AI summary (for Haiku)."""
    return f"""Write a concise 3-sentence summary of the following article.
Focus on the main argument, key evidence, and significance.
Respond with ONLY the 3 sentences, no quotes or labels.

TITLE: {title}

ARTICLE (first 2000 chars):
{content_preview[:2000]}"""


def build_tagging_prompt(
    title: str,
    content_preview: str,
    existing_tags: list[str],
) -> str:
    """Build a prompt for article tagging (for Haiku)."""
    tags_list = "\n".join(f"- {tag}" for tag in existing_tags) if existing_tags else "(none)"

    return f"""You are a content tagger for a Panama history education platform.

Analyze the article below and:
1. Select relevant tags from the existing list (if any match)
2. Suggest 1-3 new tags if the article covers topics not in the existing list

Respond with ONLY valid JSON:
{{
  "assign": ["tag1", "tag2"],
  "suggest": ["new tag 1"]
}}

Rules:
- Only assign tags that genuinely apply to this article
- Suggested new tags should be specific (e.g., "Panama Canal Treaties" not "History")
- Tag names should be Title Case, 1-4 words

EXISTING TAGS:
{tags_list}

TITLE: {title}

ARTICLE (first 2000 chars):
{content_preview[:2000]}"""
