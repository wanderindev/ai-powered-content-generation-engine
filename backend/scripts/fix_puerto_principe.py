"""Spell Morgan's 1668 target "Puerto Príncipe" everywhere (#730, epic #731).

Henry Morgan's first raid as commander (early 1668) hit Puerto Príncipe, today
Camagüey, Cuba. The corpus spelled it two ways: article 149's English body
said "Puerto del Príncipe" six times and its Spanish body mixed both forms,
while articles 148 and 232 (and research 33) say "Puerto Príncipe". The
operator chose **"Puerto Príncipe"** (2026-10-13). This script conforms
everything the public site, the indexer, regeneration or Ask PIC can read:

- article 149: ``content`` (6, including the "First Command" heading),
  ``content_es`` (2, each edited with its surrounding words so the Spanish
  stays grammatical: "como objetivo Puerto Príncipe", "ataque a Puerto
  Príncipe") and ``ai_summary`` (1, served by the article detail endpoint);
- the ARTICLE claims of 149 that name the town (``claim_text``): 5191-5195,
  5199, 5200 (CORRECTED: its indexed text is the correction, the claim text
  rides in the chunk's ``meta.original_claim``) and 5204;
- the KB rows whose ``claim_text`` names it (2376, 2377, 2378, 5126, 5128).
  ``claim_key`` is derived from ``claim_text`` (``fact_check._normalize``), so
  these edits re-key the row; a new key already used by another row is a
  ``mismatch``. The re-keyed rows match what a re-extraction of the corrected
  article and research would now produce;
- ``research.content`` and ``research.summary`` of 34;
- the situating sentences (``meta["context"]``, #697) of article 149's
  chunks that still say "Puerto del Príncipe". Chunks whose text changes get a
  new sentence anyway, but two Spanish chunks keep their text and would keep a
  stale sentence for good (sentences are keyed by content hash). Dropping the
  sentence makes the reindex ask for a new one.

Every edit is an exact substring with an expected count. An edit whose old
text is gone and new text present is ``already applied``, so a re-run is a
no-op; anything else is a ``mismatch``, is skipped, and makes the run exit 1.

Left alone on purpose: citations quoting the Wikipedia article title "Henry
Morgan's raid on Puerto del Príncipe" (a source's own title); the RESEARCH
claims of 34 (2623-2625, audit records of what was checked, never indexed);
"Puerto Príncipe" in articles 274-276, which is Port-au-Prince, Haiti;
Santiago del Príncipe (Panama) and Príncipe Pío (Madrid).

The Spanish body of 149 changes, so its narration (generated from
``content_es``) is stale until it is regenerated.

``--execute`` writes in one transaction, then re-indexes the touched articles
(body + ARTICLE_CLAIM chunks) and the VERIFIED_CLAIM chunks of the KB, unless
``--no-reindex``. Research is not indexed.

Usage (from ``backend/``, or inside the backend container with ``-w /app``):
    python -m scripts.fix_puerto_principe                 # Preview (default)
    python -m scripts.fix_puerto_principe --execute       # Write + reindex
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

from app.core.database import SessionLocal
from app.models.article import Article
from app.models.enums import FactCheckSubject, KnowledgeSourceType
from app.models.fact_check import FactCheckClaim
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.research import Research
from app.models.verified_claim import VerifiedClaim
from app.services.fact_check import _normalize
from app.services.knowledge.chunker import CONTEXT_KEY
from sqlalchemy.orm import Session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

MODELS = {
    "articles": Article,
    "fact_check_claims": FactCheckClaim,
    "verified_claims": VerifiedClaim,
    "research": Research,
}

OLD = "Puerto del Príncipe"
NEW = "Puerto Príncipe"


@dataclass(frozen=True)
class Replace:
    """Replace ``old`` with ``new`` in one field; ``old`` must occur ``count`` times.

    ``rekey`` (``verified_claims.claim_text`` only) also sets ``claim_key`` to
    the normalized new text.
    """

    table: str
    row_id: int
    column: str
    old: str = OLD
    new: str = NEW
    count: int = 1
    rekey: bool = False


@dataclass(frozen=True)
class ClearContext:
    """Drop the situating sentence of an article's chunks that still contain ``needle``."""

    article_id: int
    needle: str = OLD
    table: str = "knowledge_chunks"
    column: str = "meta.context"

    @property
    def row_id(self) -> int:
        return self.article_id


@dataclass
class Outcome:
    edit: Replace | ClearContext
    status: str  # "apply" | "already applied" | "mismatch"
    detail: str = ""
    article_id: int | None = None  # whose body / claim chunks this edit touches


def _claims(*ids: int) -> list[Replace]:
    return [Replace("fact_check_claims", i, "claim_text") for i in ids]


def _kb(*ids: int) -> list[Replace]:
    return [Replace("verified_claims", i, "claim_text", rekey=True) for i in ids]


EDITS: list[Replace | ClearContext] = [
    Replace("articles", 149, "content", count=6),
    Replace(
        "articles",
        149,
        "content_es",
        old="tuvo como objetivo Puerto del Príncipe (actual Camagüey)",
        new="tuvo como objetivo Puerto Príncipe (actual Camagüey)",
    ),
    Replace(
        "articles",
        149,
        "content_es",
        old="El exitoso ataque a Puerto del Príncipe demostró",
        new="El exitoso ataque a Puerto Príncipe demostró",
    ),
    Replace("articles", 149, "ai_summary"),
    *_claims(5191, 5192, 5193, 5194, 5195, 5199, 5200, 5204),
    *_kb(2376, 2377, 2378, 5126, 5128),
    Replace("research", 34, "content"),
    Replace("research", 34, "summary"),
    ClearContext(149),
]


def _snippet(text: str, at: int, length: int, width: int = 60) -> str:
    return repr(text[max(0, at - width) : at + length + width])


def plan_replace(value: str | None, edit: Replace) -> tuple[str, str | None, str]:
    """Return ``(status, new_value, detail)`` for one replacement. Pure."""
    if not value:
        return "mismatch", None, "field is empty or the row is missing"
    found = value.count(edit.old)
    if found == 0 and edit.new in value:
        return "already applied", None, _snippet(value, value.index(edit.new), len(edit.new))
    if found != edit.count:
        return (
            "mismatch",
            None,
            f"expected {edit.count} occurrence(s) of {edit.old!r}, found {found}",
        )
    new_value = value.replace(edit.old, edit.new)
    if not new_value.strip():
        return "mismatch", None, "replacement would blank the field"
    lines = []
    delta = len(edit.new) - len(edit.old)
    at = value.find(edit.old)
    n = 0
    while at != -1:
        lines.append(f"    before: {_snippet(value, at, len(edit.old))}")
        lines.append(f"    after:  {_snippet(new_value, at + n * delta, len(edit.new))}")
        n += 1
        at = value.find(edit.old, at + len(edit.old))
    return "apply", new_value, "\n".join(lines)


def _rekey(db: Session, row: VerifiedClaim, new_text: str) -> tuple[str | None, str]:
    """``(new_key, detail)``; ``new_key`` is None when another KB row already holds it."""
    key = _normalize(new_text)
    clash = (
        db.query(VerifiedClaim.id)
        .filter(VerifiedClaim.claim_key == key, VerifiedClaim.id != row.id)
        .first()
    )
    if clash is not None:
        return None, f"new claim_key {key!r} already belongs to verified_claims {clash[0]}"
    return key, f"    claim_key: {row.claim_key!r}\n         -> {key!r}"


def _clear_context(db: Session, edit: ClearContext, execute: bool) -> Outcome:
    rows = (
        db.query(KnowledgeChunk)
        .filter(
            KnowledgeChunk.article_id == edit.article_id,
            KnowledgeChunk.source_type == KnowledgeSourceType.ARTICLE.value,
        )
        .order_by(KnowledgeChunk.id)
        .all()
    )
    hits = [r for r in rows if edit.needle in ((r.meta or {}).get(CONTEXT_KEY) or "")]
    if not hits:
        return Outcome(edit, "already applied", "no situating sentence contains it")
    detail = "\n".join(
        f"    drop context of chunk {r.id} ({r.language} #{r.chunk_index}): {r.meta[CONTEXT_KEY]!r}"
        for r in hits
    )
    if execute:
        for r in hits:
            r.meta = {k: v for k, v in r.meta.items() if k != CONTEXT_KEY}
    return Outcome(edit, "apply", detail, edit.article_id)


def run(db: Session, edits: list[Replace | ClearContext] = EDITS, *, execute: bool = False) -> dict:
    """Plan (and with ``execute``, commit) every edit. Returns the outcomes."""
    outcomes: list[Outcome] = []
    # Edits to the same field chain: each sees the previous one's result.
    pending: dict[tuple[str, int, str], str] = {}
    for edit in edits:
        if isinstance(edit, ClearContext):
            outcomes.append(_clear_context(db, edit, execute))
            continue
        row = db.get(MODELS[edit.table], edit.row_id)
        field = (edit.table, edit.row_id, edit.column)
        current = pending.get(field, getattr(row, edit.column) if row else None)
        status, new_value, detail = plan_replace(current, edit)
        new_key = None
        if status == "apply" and edit.rekey:
            new_key, key_detail = _rekey(db, row, new_value)
            if new_key is None:
                status, new_value, detail = "mismatch", None, key_detail
            else:
                detail = f"{detail}\n{key_detail}"
        elif status == "already applied" and edit.rekey and row.claim_key != _normalize(current):
            status, detail = "mismatch", f"claim_key {row.claim_key!r} does not match the text"
        outcomes.append(Outcome(edit, status, detail, _article_of(row)))
        if status == "apply":
            pending[field] = new_value
            if execute:
                setattr(row, edit.column, new_value)
                if new_key is not None:
                    row.claim_key = new_key
    if execute:
        db.commit()
    else:
        db.rollback()
    return {
        "outcomes": outcomes,
        "counts": _counts(outcomes),
        "articles": sorted(
            {o.article_id for o in outcomes if o.status == "apply" and o.article_id}
        ),
        "kb": any(o.status == "apply" and o.edit.table == "verified_claims" for o in outcomes),
        "narration": sorted(
            {
                o.edit.row_id
                for o in outcomes
                if o.status == "apply"
                and o.edit.table == "articles"
                and o.edit.column == "content_es"
            }
        ),
    }


def _counts(outcomes: list[Outcome]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for o in outcomes:
        key = f"{o.edit.table}.{o.edit.column} {o.status}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _article_of(row) -> int | None:
    """The article an edited row is indexed under (body or ARTICLE_CLAIM), if any."""
    if isinstance(row, Article):
        return row.id
    if isinstance(row, FactCheckClaim) and row.subject_type == FactCheckSubject.ARTICLE.value:
        return row.subject_id
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--execute", action="store_true", help="Write the changes (default: preview)"
    )
    parser.add_argument(
        "--no-reindex", action="store_true", help="With --execute, skip the knowledge reindex"
    )
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        result = run(db, execute=args.execute)

    prefix = "" if args.execute else "DRY RUN: "
    for o in result["outcomes"]:
        e = o.edit
        verb = "CLEAR" if isinstance(e, ClearContext) else "REPLACE"
        # One stream, so each header stays next to its snippets.
        print(f"{prefix}{verb} {e.table} id={e.row_id} {e.column}: {o.status}", file=sys.stderr)
        if o.detail:
            detail = o.detail if o.detail.startswith("    ") else f"    {o.detail}"
            print(detail, file=sys.stderr)
    for key, n in sorted(result["counts"].items()):
        logger.info("%s%s: %d", prefix, key, n)
    mismatches = sum(1 for o in result["outcomes"] if o.status == "mismatch")
    logger.info(
        "%sarticles to reindex: %s; KB reindex: %s; narration to regenerate: %s; mismatches: %d",
        prefix,
        result["articles"],
        result["kb"],
        result["narration"],
        mismatches,
    )

    if args.execute and not args.no_reindex and (result["articles"] or result["kb"]):
        from scripts.reindex_knowledge import sweep

        for r in sweep(result["articles"], verified=result["kb"]):
            target = "KB" if r.article_id is None else f"article {r.article_id}"
            logger.info(
                "reindex %s %s: %s embedded=%d kept=%d updated=%d deleted=%d",
                target,
                r.source_type,
                r.status,
                r.embedded,
                r.kept,
                r.updated,
                r.deleted,
            )
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
