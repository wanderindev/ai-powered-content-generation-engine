"""Prompt templates for the Instagram social pipeline.

All copy is Spanish (the account posts in Spanish). Output contracts are plain
``FIELD:`` line formats with strict parsers — never JSON containing prose
(house rule: unescaped quotes/newlines make that shape fragile).
"""

SABIAS_QUE_COUNT = 3

# Keep facts short enough for the 540px card column; the renderer also has a
# shrink-to-fit pass, but a fact that needs it is already too long to punch.
FACT_MAX_CHARS = 320


def build_ig_condense_prompt(research_content: str, suggestion_title: str) -> str:
    """Condense a long research doc into a briefing for sabías-que extraction."""
    return f"""You are a researcher preparing a briefing for a social-media copywriter.

The research document below is about "{suggestion_title}". Condense it to roughly \
1,200 words, PRESERVING the most surprising, concrete, and visual facts — exact dates, \
names, numbers, and little-known details. Those "wow" facts are the whole point: the \
copywriter will turn them into "¿Sabías que…?" Instagram cards. Keep every fact \
accurate to the document; do not add outside knowledge.

Respond with the condensed briefing only — no preamble.

RESEARCH DOCUMENT:
{research_content}"""


def build_sabias_que_prompt(briefing: str, suggestion_title: str) -> str:
    """Build the prompt that turns a research briefing into 3 fact-card drafts."""
    return f"""You are the social-media copywriter for Panama In Context, a Panama history \
brand run by tour guide Diego Raúl Feliu. His Instagram (@panamaincontextbydiego) posts \
"¿Sabías que…?" cards: a branded image with one surprising historical fact, in Spanish.

From the briefing below (about "{suggestion_title}"), write {SABIAS_QUE_COUNT} distinct \
fact cards. Pick the {SABIAS_QUE_COUNT} most surprising, concrete facts — the ones a \
scroller stops for. Every fact must come from the briefing; invent nothing.

Rules for each card:
- FACT continues the heading "¿Sabías que" as one sentence: it starts in lowercase, \
reads naturally after the heading, and ends with "?". Maximum {FACT_MAX_CHARS} characters. \
Wrap the 1-3 most striking words or phrases in <b>…</b> (bold). No other markup, no emoji.
- POLAROID is a short handwritten-style caption for the card's photo (place or subject \
+ optional era), e.g. "El Arco Chato, Casco Antiguo". Maximum 60 characters.
- IMAGE_QUERY is a 2-5 word ENGLISH search hint for our photo library (the photo is \
picked by a human later), e.g. "Arco Chato ruins".
- CAPTION is the full Instagram caption in Spanish: an engaging 2-4 sentence expansion \
of the fact, then a line inviting readers to learn more on our tours or the blog \
(enlace en la bio), then 5-8 relevant hashtags on the final line (always include \
#PanamaInContext #HistoriaDePanama). No emoji. Under 1,500 characters.
- TITLE is a short internal label for the admin grid (not shown publicly).

Respond in EXACTLY this format, one block per card, separated by a line with only "---":

POST 1
TITLE: <short internal label>
FACT: <the fact sentence>
POLAROID: <photo caption>
IMAGE_QUERY: <english photo hint>
CAPTION:
<caption line 1>
<caption line 2>
<hashtags>
---
POST 2
...

BRIEFING:
{briefing}"""


def build_article_promo_prompt(
    series_title: str,
    article_count: int,
    article_titles: list[str],
    summary: str | None,
) -> str:
    """Build the prompt for a "Nuevo en el blog" announcement caption."""
    titles_text = "\n".join(f"- {t}" for t in article_titles)
    summary_text = f"\nSeries summary: {summary}" if summary else ""
    return f"""You are the social-media copywriter for Panama In Context, a Panama history \
brand run by tour guide Diego Raúl Feliu. His Instagram (@panamaincontextbydiego) \
announces new blog publications with a branded "Nuevo en el blog" card.

We just published a series of {article_count} articles titled "{series_title}" on \
panamaincontext.com. The articles:
{titles_text}{summary_text}

Write the Instagram caption in Spanish: 2-4 engaging sentences that tease what the \
series covers and why it matters, a line pointing readers to the blog (enlace en la \
bio), then 5-8 relevant hashtags on the final line (always include #PanamaInContext \
#HistoriaDePanama). No emoji. Under 1,500 characters.

Respond in EXACTLY this format:

CAPTION:
<caption line 1>
<caption line 2>
<hashtags>"""
