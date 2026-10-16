"""Automated grammar check for blog articles (issue #409).

One strict, corrections-only Sonnet proofread per language replaces the manual
Grammarly session that used to block the autonomous weekly-article pipeline.
The model returns single-line ``FIND: … ||| REPLACE: …`` pairs — never rewrites
— and code applies them under hard safety guards:

* **Parse conservatively** — a malformed line is *dropped*, never guessed at.
  A FIND or REPLACE still carrying a protocol marker is parser fallout (a
  swallowed second pair), not content, so it is dropped too (the #433/#434
  lesson from the fact-check corrections parser, in this format).
* **Exactly once** — a pair applies only when its FIND occurs exactly once
  across all of the language's fields combined (title + excerpt + content), so
  the model can never touch prose it did not point at. Pairs apply in order
  against the already-edited values, so an earlier fix that invalidates a later
  FIND surfaces as a skip rather than a silent mismatch.
* **Audited** — every parsed pair writes one ``GrammarCheckFix`` row, applied or
  skipped-with-reason. Rows are append-only across re-runs.

Benchmarked 2026-07-21 on articles 213 (EN) / 211 (ES): exactly the real
mechanical defects, zero false positives. Haiku is *not* viable for this stage
(1 of 5 fixes); do not downgrade the model.

The service flushes but never commits — the endpoint owns the transaction.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.enums import Language
from app.models.grammar_check import GrammarCheckFix
from app.services.article_slugs import sync_article_slugs
from app.services.llm import SONNET_MODEL, create_message, first_text

logger = logging.getLogger(__name__)

# Benchmarked verbatim (2026-07-21) — the strictness of the wording is what
# keeps the output to real mechanical defects. Do not reword or reformat.
_GRAMMAR_PROMPT = """You are a meticulous copy editor for a history blog. You will receive an article in markdown.

Identify ONLY objective mechanical errors:
- misspellings and typos
- duplicated words
- missing hyphens in compound modifiers (e.g. "a 600 ton ship" -> "a 600-ton ship")
- subject-verb agreement errors
- unambiguous punctuation errors

Do NOT suggest style, tone, clarity, or wording changes. Do NOT rewrite sentences. Do NOT change historical names, places, titles, or foreign-language terms. Passive voice is acceptable. If a candidate fix could change meaning, skip it.

Output format — one line per fix, nothing else:
FIND: <exact text from the article, with enough surrounding words to be unique> ||| REPLACE: <corrected text>

If there are no fixes, output exactly: NONE"""  # noqa: E501

_FIND_PREFIX = "FIND: "
_SEPARATOR = " ||| REPLACE: "
_NO_FIXES = "NONE"

# Sonnet 5.5 always runs adaptive thinking and those tokens count against
# max_tokens (thinking alone ate 8000 in a prod incident), so carry headroom.
# This is also >= anthropic_stream_min_tokens, so the call streams — both
# intended. ``reject_on_truncation`` stays at its default True: a half-returned
# fix list must never be applied.
_MAX_TOKENS = 16000

# ``articles.title`` / ``title_es`` are String(255); a fix that overflows the
# column fails the pair instead of blowing up at flush time.
_TITLE_MAX = 255

# Ordered per language. The user message is these values joined by blank lines —
# plain values, no labels, so a verbatim FIND always matches inside one real
# field value rather than straddling a synthetic header.
_FIELDS: dict[str, tuple[str, ...]] = {
    Language.EN.value: ("title", "excerpt", "content"),
    Language.ES.value: ("title_es", "excerpt_es", "content_es"),
}
_TITLE_FIELDS = frozenset({"title", "title_es"})


@dataclass(frozen=True)
class GrammarFix:
    """One parsed FIND/REPLACE pair and what became of it."""

    find: str
    replace: str
    applied: bool
    skipped_reason: str | None = None


def _has_protocol_marker(text: str) -> bool:
    """True when *text* still carries raw fix-protocol syntax — the signature of
    a swallowed second pair rather than real article prose."""
    return text.startswith(_FIND_PREFIX) or _SEPARATOR in text


def parse_fix_lines(raw: str) -> list[tuple[str, str]]:
    """Parse the proofread output into ``(find, replace)`` pairs.

    One pair per line; ``NONE`` (alone) means no fixes. Malformed lines are
    dropped, never repaired: the format carries no redundancy, so a line we
    cannot read exactly is a line we cannot apply safely.

    FIND and REPLACE are taken verbatim between the delimiters — deliberately
    *not* stripped, because the whitespace can be the fix itself (``Europa.las``
    -> ``Europa. Las``).
    """
    if raw.strip() == _NO_FIXES:
        return []

    pairs: list[tuple[str, str]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith(_FIND_PREFIX):
            logger.debug("grammar check: dropping non-fix line %r", stripped[:120])
            continue
        body = stripped[len(_FIND_PREFIX) :]
        if _SEPARATOR not in body:
            logger.debug("grammar check: dropping line without separator %r", stripped[:120])
            continue
        find, _, replace = body.partition(_SEPARATOR)
        if not find or not replace or find == replace:
            logger.debug("grammar check: dropping degenerate pair %r", stripped[:120])
            continue
        if _has_protocol_marker(find) or _has_protocol_marker(replace):
            logger.debug("grammar check: dropping pair with protocol marker %r", stripped[:120])
            continue
        pairs.append((find, replace))
    return pairs


def _included_fields(article: Article, language: str) -> list[str]:
    """The language's non-empty field names, in canonical order."""
    return [name for name in _FIELDS[language] if getattr(article, name, None)]


def _apply_pairs(
    values: dict[str, str], order: list[str], pairs: list[tuple[str, str]]
) -> list[GrammarFix]:
    """Apply pairs in order against *values* (mutated in place).

    Each FIND must occur exactly once across ALL included fields combined, on
    the already-edited text — matching the fact-check corrections semantics.
    """
    results: list[GrammarFix] = []
    for find, replace in pairs:
        total = sum(values[name].count(find) for name in order)
        if total == 0:
            results.append(
                GrammarFix(find, replace, False, "FIND snippet not found in the content")
            )
            continue
        if total > 1:
            results.append(
                GrammarFix(
                    find, replace, False, f"FIND snippet occurs {total} times in the content"
                )
            )
            continue
        name = next(key for key in order if find in values[key])
        new_value = values[name].replace(find, replace)
        if name in _TITLE_FIELDS and len(new_value) > _TITLE_MAX:
            results.append(
                GrammarFix(find, replace, False, f"corrected title exceeds {_TITLE_MAX} characters")
            )
            continue
        values[name] = new_value
        results.append(GrammarFix(find, replace, True, None))
    return results


def run_grammar_check(article: Article, language: str | Language, db: Session) -> list[GrammarFix]:
    """Proofread one language of *article*, apply the safe fixes, and audit them.

    Writes the edited field values back onto the article, adds one
    ``GrammarCheckFix`` row per parsed pair, and stamps the language's
    ``grammar_checked_*_at`` — always on a successful run, including when the
    model returned ``NONE`` (that the check ran is the fact being recorded).

    Flushes; the caller commits. Any exception (truncation, budget, API error)
    propagates with nothing stamped and nothing written.
    """
    lang = Language(language).value
    order = _included_fields(article, lang)
    values = {name: getattr(article, name) for name in order}
    target_text = "\n\n".join(values[name] for name in order)

    message = create_message(
        feature="grammar_check",
        model=SONNET_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_GRAMMAR_PROMPT,
        messages=[{"role": "user", "content": target_text}],
    )
    pairs = parse_fix_lines(first_text(message))
    fixes = _apply_pairs(values, order, pairs)

    for name in order:
        setattr(article, name, values[name])
    if any(name in ("title", "title_es") for name in order):
        # A proofread fix can rewrite a title. Let SlugCollisionError propagate:
        # a grammar fix that collides two unpublished slugs is a loud failure,
        # not something to swallow. Published articles are frozen anyway.
        sync_article_slugs(db, article)
    for fix in fixes:
        db.add(
            GrammarCheckFix(
                article_id=article.id,
                language=lang,
                find_text=fix.find,
                replace_text=fix.replace,
                applied=fix.applied,
                skipped_reason=fix.skipped_reason,
            )
        )

    checked_at = datetime.now(UTC)
    if lang == Language.EN.value:
        article.grammar_checked_en_at = checked_at
    else:
        article.grammar_checked_es_at = checked_at
    db.flush()

    applied_count = sum(1 for fix in fixes if fix.applied)
    logger.info(
        "Grammar check for article %d (%s): %d fixes parsed, %d applied, %d skipped",
        article.id,
        lang,
        len(fixes),
        applied_count,
        len(fixes) - applied_count,
    )
    return fixes
