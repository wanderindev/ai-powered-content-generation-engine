"""Spanish accuracy check against the English, after translation (issue #557).

The English article is fact-checked and corrected before ``TRANSLATE`` runs, so
the remaining accuracy risk in the Spanish is the translation itself: a DeepL
number, date, name or negation that no longer says what the English says, or a
claim dropped or added. This pass compares the two languages paragraph by
paragraph with Sonnet and fixes **the Spanish only**.

It mirrors the grammar check (#409, ``grammar_check.py``) and reuses its parser
and applier, so the same safety guards hold:

* **Corrections only** — the model returns ``FIND: … ||| REPLACE: …`` lines
  quoting the Spanish; a malformed line is dropped, never guessed at.
* **Exactly once** — a pair applies only when its FIND occurs exactly once
  across the Spanish title + excerpt + body combined.
* **Audited** — every parsed pair writes one ``GrammarCheckFix`` row
  (``language='es'``, ``source='ES_ACCURACY'``), applied or skipped-with-reason.

Scope choices:

* **Meaning, not style.** The prompt forbids wording, register and grammar
  edits — Grammarly (the ``WAIT_GRAMMAR_CHECKED_ES`` step that follows) owns
  those. Number *formatting* is not a difference: ``TranslationService``
  normalises thousands separators to the comma (#531), and every REPLACE goes
  through ``normalize_es_thousands`` too, so a fix can never reintroduce the
  dot form.
* **Body only.** The About / Continue Reading series sections are generated
  per language, not translated, so both bodies are cut out with
  ``split_series_sections`` and the sections are re-attached byte-for-byte —
  the same contract as the Grammarly flow.
* **No stamp.** Nothing records that the check ran besides the audit rows, and
  zero fixes is a success, so a replay after a lost response simply runs again
  and converges (#495): an already-applied fix's FIND is gone (a skipped row),
  and accurate Spanish returns ``NONE``.

The service flushes but never commits — the endpoint owns the transaction.
"""

import logging
import re

from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.enums import GrammarFixSource, Language
from app.models.grammar_check import GrammarCheckFix
from app.services.article_slugs import sync_article_slugs
from app.services.grammar_check import GrammarFix, _apply_pairs, parse_fix_lines
from app.services.llm import SONNET_MODEL, create_message, first_text
from app.services.series_sections import split_series_sections
from app.services.translation import normalize_es_thousands

logger = logging.getLogger(__name__)

_ACCURACY_PROMPT = """You check a Spanish translation of a history blog article against its English original. The English has already been fact-checked: treat it as correct. You will receive the article as numbered segments; each segment shows the English text and then the Spanish text that should say the same thing.

Compare each segment's Spanish with its English and report ONLY differences in meaning:
- numbers, quantities, measurements, percentages that differ
- dates, years, centuries, durations that differ
- names of people, places, ships, institutions, titles that are wrong or swapped
- negations that were dropped, added or inverted
- a claim, qualifier or detail in the English that is missing from the Spanish
- a claim or detail in the Spanish that is not in the English
- a mistranslation that changes who did what, when, or why

Do NOT change style, tone, word choice, register, sentence structure or grammar when the meaning is the same. Do NOT change number formatting: Spanish uses its own separators (1,500 and 1.500 are the same value here) — only a different value is an error. Do NOT translate names that are customarily kept or adapted in Spanish. If you are not sure the meaning differs, skip it.

Each fix must edit the SPANISH only. Output format — one line per fix, nothing else:
FIND: <exact text from the Spanish, with enough surrounding words to be unique> ||| REPLACE: <corrected Spanish text>

Never include the segment labels in FIND. If the Spanish says what the English says, output exactly: NONE"""  # noqa: E501

# Same budget as the grammar check: Sonnet 5.5 always thinks, the thinking
# counts against max_tokens, and >= anthropic_stream_min_tokens keeps the call
# streamed on the 600s timeout. ``reject_on_truncation`` stays True — a
# half-returned fix list must never be applied. effort "low": this is a
# side-by-side comparison, not open-ended reasoning (the grammar_verdicts
# precedent).
_MAX_TOKENS = 16000

# The key the Spanish body is applied under. ``_apply_pairs`` treats only
# title / title_es as length-capped, so any other name works.
_BODY = "body"

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_SPLIT.split(text.strip()) if p.strip()]


def render_segments(article: Article, en_body: str, es_body: str) -> str:
    """Build the model's user message: numbered EN/ES segment pairs.

    Title and excerpt are a segment each when both languages have one. The
    bodies pair paragraph by paragraph when the paragraph counts agree (the
    markdown round trip in ``translate_markdown`` normally preserves them);
    otherwise the two whole bodies go in as one segment rather than pairing
    paragraphs that do not correspond. Labels sit on their own lines so a FIND
    copied from the Spanish never straddles one.
    """
    pairs: list[tuple[str, str]] = []
    if article.title and article.title_es:
        pairs.append((article.title, article.title_es))
    if article.excerpt and article.excerpt_es:
        pairs.append((article.excerpt, article.excerpt_es))
    en_paras, es_paras = _paragraphs(en_body), _paragraphs(es_body)
    if len(en_paras) == len(es_paras):
        pairs.extend(zip(en_paras, es_paras))
    else:
        pairs.append((en_body.strip(), es_body.strip()))

    blocks = [
        f"[{number}] ENGLISH\n{english}\n\n[{number}] SPANISH\n{spanish}"
        for number, (english, spanish) in enumerate(pairs, start=1)
    ]
    return "\n\n".join(blocks)


def _normalise_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Run each REPLACE through the #531 normaliser; drop pairs it makes no-ops."""
    out: list[tuple[str, str]] = []
    for find, replace in pairs:
        replace = normalize_es_thousands(replace)
        if replace != find:
            out.append((find, replace))
    return out


def run_es_accuracy_check(article: Article, db: Session) -> list[GrammarFix]:
    """Compare the Spanish with the English, apply the safe fixes, and audit them.

    Requires both ``content`` and ``content_es`` (the endpoint 400s otherwise).
    Writes the edited Spanish title / excerpt / body back onto the article and
    adds one ``GrammarCheckFix`` row per parsed pair. Flushes; the caller
    commits. Any exception (truncation, budget, refusal, API error) propagates
    with nothing written.
    """
    _, en_body, _ = split_series_sections(article.content)
    es_about, es_body, es_continue = split_series_sections(article.content_es)

    message = create_message(
        feature="es_accuracy",
        model=SONNET_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_ACCURACY_PROMPT,
        messages=[{"role": "user", "content": render_segments(article, en_body, es_body)}],
        output_config={"effort": "low"},
    )
    pairs = _normalise_pairs(parse_fix_lines(first_text(message)))

    values: dict[str, str] = {}
    if article.title_es:
        values["title_es"] = article.title_es
    if article.excerpt_es:
        values["excerpt_es"] = article.excerpt_es
    values[_BODY] = es_body
    order = list(values)
    fixes = _apply_pairs(values, order, pairs)

    if "title_es" in values and values["title_es"] != article.title_es:
        article.title_es = values["title_es"]
        # A meaning fix can rewrite the Spanish title. As in the grammar check,
        # SlugCollisionError propagates; a live article's slug is frozen anyway.
        sync_article_slugs(db, article)
    if "excerpt_es" in values:
        article.excerpt_es = values["excerpt_es"]
    if values[_BODY] != es_body:
        article.content_es = es_about + values[_BODY] + es_continue

    for fix in fixes:
        db.add(
            GrammarCheckFix(
                article_id=article.id,
                language=Language.ES.value,
                find_text=fix.find,
                replace_text=fix.replace,
                applied=fix.applied,
                skipped_reason=fix.skipped_reason,
                source=GrammarFixSource.ES_ACCURACY.value,
            )
        )
    db.flush()

    applied_count = sum(1 for fix in fixes if fix.applied)
    logger.info(
        "ES accuracy check for article %d: %d fixes parsed, %d applied, %d skipped",
        article.id,
        len(fixes),
        applied_count,
        len(fixes) - applied_count,
    )
    return fixes
