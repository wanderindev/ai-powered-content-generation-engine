"""Meaning check on an accepted Grammarly proofread (issue #518).

The first live Grammarly run (article 245 EN, 2026-09-20) accepted 25
suggestions; the Grammar ones were fine, the Clarity rewrites flattened voice
and in places changed meaning ("engineers would concentrate" -> "engineers
concentrated"). So before ``grammar-apply`` writes a corrected body, Sonnet
reads every *changed paragraph* side by side with the original and returns
one verdict per pair. It is a **reviewer, not an author** — the same
edits-not-rewrites discipline as ``fact_check_corrections.py``:

* ``KEEP`` — the change preserves meaning and voice; it is written.
* ``REVERT ||| FIND: <phrase in AFTER> ||| REPLACE: <phrase from BEFORE> |||
  REASON: …`` — a targeted undo. Code applies it only when FIND occurs
  **exactly once** in the corrected body (``grammar_revert.revert_snippet``,
  emphasis-tolerant); a revert that cannot be applied becomes a review row
  instead of a guess.
* ``REVIEW ||| REASON: …`` — the model is unsure. The change is still
  written (the operator accepted it) but a ``GrammarReviewDiff`` row records
  the before/after for a human, surfaced in the admin grid.

Malformed lines are dropped, never repaired, and a pair with no verdict at
all is treated as ``REVIEW`` ("no verdict returned") — the conservative
default, since silence must not read as approval. A failed model call (API
error, truncation, budget) never fails the write either: every changed pair
is filed for review with the error as its reason. ``dry_run`` and an empty
``accepted`` list never reach this module.

Gated by ``Settings.grammar_meaning_check_enabled`` (default True) and by an
Anthropic key being configured — empty key skips, never crashes.

Calibration (2026-09-27). The first prompt told the model to revert edits that
changed "emphasis", and once article bodies lost their bold/italics (#540) it
read that as rhetorical intensity: 27 of 186 accepted edits were reverted in one
day, about 10 of them for dropping a word like "verdadero" or "both". The rules
now name what to KEEP (intensifiers, splits, synonyms) and what to REVERT
(claims, terms, certainty, first-person opinion), plus three shifts the loosened
v1 missed: would/could -> did, forbidden -> unable, and idiom swaps. On a
48-pair labelled set plus 20 unverified KEEPs it caught 13-15 of 15 real
meaning changes (old: 14-15) while over-reverting 0-1 of 26 true KEEPs (old:
7-8). ``effort`` stays "low": "medium" removed the occasional self-contradicting
REVERT but missed 3-5 real changes per run.
``scripts/one_off/eval_grammar_verdicts.py`` reproduces the benchmark.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import sentry_sdk

from app.core.config import get_settings
from app.services.grammar_revert import revert_snippet
from app.services.llm import SONNET_MODEL, create_message, first_text

logger = logging.getLogger(__name__)

__all__ = [
    "ChangedPair",
    "MeaningCheckResult",
    "Verdict",
    "changed_pairs",
    "markdown_blocks",
    "parse_verdict_lines",
    "run_meaning_check",
]

KEEP = "KEEP"
REVERT = "REVERT"
REVIEW = "REVIEW"

NO_VERDICT_REASON = "no verdict returned"

_VERDICT_PROMPT = """You are reviewing a copy editor's changes to a history blog article, one paragraph at a time. For each numbered pair you get the paragraph BEFORE the edit and AFTER it. The copy editor is allowed to make the prose clearer, tighter and easier to read. What must survive is what the paragraph claims and the author's own opinions.

Judge each pair and answer with exactly one line per pair, nothing else:
[n] KEEP
[n] REVERT ||| FIND: <phrase copied verbatim from AFTER> ||| REPLACE: <the original phrase from BEFORE> ||| REASON: <short reason>
[n] REVIEW ||| REASON: <short reason>

Rules:
- KEEP when AFTER makes the same claims as BEFORE, with the same certainty, even if it words them differently. In particular, KEEP: spelling, punctuation, agreement and grammar fixes; synonym swaps; sentences split, merged or reordered; filler and intensifiers dropped or softened (really, truly, both, verdadero, ligeramente, catastrophically) when the underlying claim is unchanged; tense made consistent without moving an event in time.
- REVERT only when the edit changes what the paragraph claims: a fact, number, date, name, place, or the direction or order of events; a technical or period term replaced by a generic word or dropped; who did what, or why; the certainty of a historical statement (a hedge such as "in effect", "almost", "possibly" or "roughly" added or removed); or the author's first-person opinion or reflection. Watch in particular for these shifts, which read naturally but change the claim: a plan, intention or possibility turned into a fact or back ("would build" / "could build" -> "built"); a rule or prohibition turned into mere inability or back ("was not allowed to" -> "could not"); an idiom or figure of speech replaced by one that means something else.
- Also REVERT when AFTER is ungrammatical or has broken punctuation, even if the meaning is close: read AFTER as a native speaker would before keeping it.
- For a REVERT, FIND must be a contiguous phrase that appears exactly once in AFTER; REPLACE is what BEFORE said there. Keep FIND as short as possible while still unique, and revert only the phrase that changed the claim, not the rest of the edit. If several phrases in one paragraph need reverting, output several REVERT lines with the same [n].
- REVIEW when you cannot tell, or when a revert cannot be expressed as one FIND/REPLACE.
- Never rewrite. Never introduce wording that appears in neither BEFORE nor AFTER.
- Do not comment on the pairs; output only verdict lines."""  # noqa: E501

# Sonnet 5.5 thinking counts against max_tokens; effort is low because this is a
# judgment over a handful of short pairs, not generation. 8000 puts the call on
# the streamed/600s budget rather than the 120s one (see llm.py).
_MAX_TOKENS = 8000

_LINE_RE = re.compile(r"^\[(\d+)\]\s*(KEEP|REVERT|REVIEW)\b\s*(.*)$")
_FIND_PREFIX = "FIND: "
_REPLACE_PREFIX = "REPLACE: "
_REASON_PREFIX = "REASON: "
_SEPARATOR = "|||"


@dataclass(frozen=True)
class ChangedPair:
    index: int  # 0-based block index in the body
    before: str
    after: str


@dataclass(frozen=True)
class Verdict:
    position: int  # 0-based position in the *pairs* list (the model's [n] - 1)
    kind: str  # KEEP / REVERT / REVIEW
    reason: str = ""
    find: str = ""
    replace: str = ""


@dataclass
class MeaningCheckResult:
    body: str
    called: bool = False
    # (pair, reason) for every paragraph the check reverted, fully or partly.
    reverted: list[tuple[ChangedPair, str]] = field(default_factory=list)
    # (pair, reason) for every paragraph a human should look at.
    review: list[tuple[ChangedPair, str]] = field(default_factory=list)


def markdown_blocks(markdown_text: str) -> list[str]:
    """Split a markdown body into its blank-line-separated blocks."""
    return [b.strip() for b in re.split(r"\n\s*\n", markdown_text.strip()) if b.strip()]


def changed_pairs(old_body: str, new_body: str) -> list[ChangedPair]:
    """The blocks that differ between the stored and corrected bodies.

    The caller has already refused a block-count mismatch, so blocks align by
    position; any residual mismatch is zipped to the shorter list.
    """
    old_blocks, new_blocks = markdown_blocks(old_body), markdown_blocks(new_body)
    return [
        ChangedPair(i, before, after)
        for i, (before, after) in enumerate(zip(old_blocks, new_blocks, strict=False))
        if before != after
    ]


def _fields(rest: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in rest.split(_SEPARATOR):
        part = part.strip()
        for prefix in (_FIND_PREFIX, _REPLACE_PREFIX, _REASON_PREFIX):
            if part.startswith(prefix):
                out[prefix.rstrip(": ").lower()] = part[len(prefix) :].strip()
    return out


def parse_verdict_lines(raw: str, pair_count: int) -> list[Verdict]:
    """Parse the model output into verdicts; malformed lines are dropped."""
    verdicts: list[Verdict] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _LINE_RE.match(stripped)
        if not match:
            logger.debug("grammar verdicts: dropping unparseable line %r", stripped[:120])
            continue
        n, kind, rest = int(match.group(1)), match.group(2), match.group(3)
        if not 1 <= n <= pair_count:
            logger.debug("grammar verdicts: dropping out-of-range index %r", stripped[:120])
            continue
        fields = _fields(rest)
        reason = fields.get("reason", "")
        if kind == REVERT:
            find, replace = fields.get("find", ""), fields.get("replace", "")
            if not find or not replace or find == replace:
                logger.debug("grammar verdicts: dropping degenerate REVERT %r", stripped[:120])
                continue
            verdicts.append(Verdict(n - 1, REVERT, reason, find, replace))
        else:
            verdicts.append(Verdict(n - 1, kind, reason))
    return verdicts


def _apply_verdicts(
    new_body: str, pairs: list[ChangedPair], verdicts: list[Verdict]
) -> MeaningCheckResult:
    result = MeaningCheckResult(body=new_body, called=True)
    by_index: dict[int, list[Verdict]] = {}
    for verdict in verdicts:
        by_index.setdefault(verdict.position, []).append(verdict)

    body = new_body
    for position, pair in enumerate(pairs):
        own = by_index.get(position, [])
        if not own:
            result.review.append((pair, NO_VERDICT_REASON))
            continue
        reverts = [v for v in own if v.kind == REVERT]
        if not reverts:
            # KEEP wins over REVIEW only when nothing asked for review.
            reviews = [v for v in own if v.kind == REVIEW]
            if reviews:
                result.review.append((pair, reviews[0].reason or "flagged for review"))
            continue
        applied_reasons: list[str] = []
        for verdict in reverts:
            outcome = revert_snippet(body, verdict.find, verdict.replace)
            if outcome.applied:
                body = outcome.content
                applied_reasons.append(verdict.reason or "reverted by meaning check")
            else:
                result.review.append(
                    (pair, f"revert could not be applied ({outcome.reason}): {verdict.reason}")
                )
        if applied_reasons:
            result.reverted.append((pair, "; ".join(applied_reasons)))
    result.body = body
    return result


def _render_pairs(pairs: list[ChangedPair]) -> str:
    chunks = []
    for n, pair in enumerate(pairs, start=1):
        chunks.append(f"[{n}] BEFORE:\n{pair.before}\n[{n}] AFTER:\n{pair.after}")
    return "\n\n".join(chunks)


def run_meaning_check(old_body: str, new_body: str) -> MeaningCheckResult:
    """Review the changed paragraphs and return the body with reverts applied.

    Returns ``called=False`` (body untouched, nothing filed) when the check is
    disabled, no key is configured, or nothing changed.
    """
    pairs = changed_pairs(old_body, new_body)
    settings = get_settings()
    if not pairs or not settings.grammar_meaning_check_enabled or not settings.anthropic_api_key:
        return MeaningCheckResult(body=new_body, called=False)

    try:
        message = create_message(
            feature="grammar_verdicts",
            model=SONNET_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_VERDICT_PROMPT,
            messages=[{"role": "user", "content": _render_pairs(pairs)}],
            output_config={"effort": "low"},
        )
        verdicts = parse_verdict_lines(first_text(message), len(pairs))
    except Exception as exc:  # noqa: BLE001 — the write must not fail on the reviewer
        logger.exception(
            "grammar verdicts: model call failed; filing %d pairs for review", len(pairs)
        )
        sentry_sdk.capture_exception(exc)
        result = MeaningCheckResult(body=new_body, called=True)
        result.review = [(pair, f"meaning check failed: {exc}") for pair in pairs]
        return result

    result = _apply_verdicts(new_body, pairs, verdicts)
    logger.info(
        "grammar verdicts: %d changed paragraphs, %d reverted, %d for review",
        len(pairs),
        len(result.reverted),
        len(result.review),
    )
    return result
