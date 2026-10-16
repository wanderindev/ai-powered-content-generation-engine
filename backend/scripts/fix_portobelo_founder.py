"""Correct the spelling of Portobelo's founder: Velarde -> Valverde (#714, epic #715).

The corpus named the founder of San Felipe de Portobelo (20 March 1597) two
ways. Every cited source spells it **Valverde**: the KB's own deep-research
verdict (``verified_claims`` 1764, citing Castillero Calvo and the Boletín
Ravignani), ``verified_claims`` 5958 (Biblioteca Nacional de Panamá, *El
Panamá Hispano*) and research 32, fn. 5 (*Encyclopedia of Latin American
History and Culture*). The operator chose Valverde and corrected the article
bodies (``content`` / ``content_es`` of 205, 208 and 230) by hand to
"Francisco Valverde y Mercado", dropping "Governor" from 208 (he was *veedor y
factor* of the Real Hacienda, per 1764). This script conforms everything else
the indexer, regeneration or Ask PIC can read:

- ``articles.outline`` of 205 and 230;
- the ARTICLE grounding claims 5400, 5676 and 10471 (``claim_text``), and the
  deletion of 5674 ("... was Governor."): the article no longer says it and the
  KB contradicts it, so it is not an article claim any more (a re-grounding
  would not extract it either);
- the KB corrections of ``verified_claims`` 329, 891 and 2624 (``claim_key``
  is derived from ``claim_text``, which is untouched, so no key changes);
- ``research.content`` 27, 30, 31, 33, 34 and ``research.summary`` 30.

Every edit is an exact substring with an expected count, so it never matches a
bare "Velarde" (Óscar Vargas Velarde, a cited author) or Vicente de Valverde
(Pizarro's friar). An edit whose old text is gone and new text present is
``already applied``, so a re-run is a no-op; anything else is a ``mismatch``,
is skipped, and makes the run exit 1. Left alone on purpose: 1764's
``claim_text`` (the claim as it was checked; its correction already says
Valverde), citations quoting "Valverde/Velarde", and the RESEARCH /
SUGGESTION claim rows (audit records of what was checked, never indexed).

``--execute`` writes in one transaction, then re-indexes the touched articles
(body + ARTICLE_CLAIM chunks) and the VERIFIED_CLAIM chunks of the KB, unless
``--no-reindex``. Research is not indexed.

Usage (from ``backend/``, or inside the backend container with ``-w /app``):
    python -m scripts.fix_portobelo_founder                 # Preview (default)
    python -m scripts.fix_portobelo_founder --execute       # Write + reindex
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

from app.core.database import SessionLocal
from app.models.article import Article
from app.models.enums import FactCheckSubject
from app.models.fact_check import FactCheckClaim
from app.models.research import Research
from app.models.verified_claim import VerifiedClaim
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

OLD = "Francisco Velarde y Mercado"
NEW = "Francisco Valverde y Mercado"


@dataclass(frozen=True)
class Replace:
    """Replace ``old`` with ``new`` in one field; ``old`` must occur ``count`` times."""

    table: str
    row_id: int
    column: str
    old: str = OLD
    new: str = NEW
    count: int = 1


@dataclass(frozen=True)
class Delete:
    """Delete one row, only while ``column`` still equals ``expected`` exactly."""

    table: str
    row_id: int
    column: str
    expected: str


@dataclass
class Outcome:
    edit: Replace | Delete
    status: str  # "apply" | "already applied" | "mismatch"
    detail: str = ""
    article_id: int | None = None  # whose body / claim chunks this edit touches


EDITS: list[Replace | Delete] = [
    Replace("articles", 205, "outline"),
    Replace("articles", 230, "outline"),
    Replace("fact_check_claims", 5400, "claim_text"),
    Replace(
        "fact_check_claims",
        5676,
        "claim_text",
        old="founded by Governor Francisco Velarde y Mercado.",
        new="founded by Francisco Valverde y Mercado.",
    ),
    Delete(
        "fact_check_claims",
        5674,
        "claim_text",
        expected="Francisco Velarde y Mercado was Governor.",
    ),
    Replace("fact_check_claims", 10471, "claim_text"),
    Replace("verified_claims", 329, "correction"),
    Replace("verified_claims", 891, "correction"),
    Replace("verified_claims", 2624, "correction"),
    Replace("research", 27, "content"),
    Replace("research", 30, "content", count=2),
    Replace("research", 30, "summary"),
    Replace(
        "research",
        31,
        "content",
        old="Governor Francisco Velarde y Mercado",
        new=NEW,
    ),
    Replace("research", 33, "content"),
    Replace("research", 34, "content"),
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


def run(db: Session, edits: list[Replace | Delete] = EDITS, *, execute: bool = False) -> dict:
    """Plan (and with ``execute``, commit) every edit. Returns the outcomes."""
    outcomes: list[Outcome] = []
    for edit in edits:
        row = db.get(MODELS[edit.table], edit.row_id)
        if isinstance(edit, Delete):
            if row is None:
                outcomes.append(Outcome(edit, "already applied", "row is gone"))
            elif getattr(row, edit.column) != edit.expected:
                outcomes.append(
                    Outcome(edit, "mismatch", f"{edit.column} is {getattr(row, edit.column)!r}")
                )
            else:
                outcomes.append(
                    Outcome(edit, "apply", f"    delete: {edit.expected!r}", _article_of(row))
                )
                if execute:
                    db.delete(row)
            continue
        status, new_value, detail = plan_replace(getattr(row, edit.column) if row else None, edit)
        outcomes.append(Outcome(edit, status, detail, _article_of(row)))
        if execute and status == "apply":
            setattr(row, edit.column, new_value)
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
        verb = "DELETE" if isinstance(e, Delete) else "REPLACE"
        # One stream, so each header stays next to its snippets.
        print(f"{prefix}{verb} {e.table} id={e.row_id} {e.column}: {o.status}", file=sys.stderr)
        if o.detail:
            detail = o.detail if o.detail.startswith("    ") else f"    {o.detail}"
            print(detail, file=sys.stderr)
    for key, n in sorted(result["counts"].items()):
        logger.info("%s%s: %d", prefix, key, n)
    mismatches = sum(1 for o in result["outcomes"] if o.status == "mismatch")
    logger.info(
        "%sarticles to reindex: %s; KB reindex: %s; mismatches: %d",
        prefix,
        result["articles"],
        result["kb"],
        mismatches,
    )

    if args.execute and not args.no_reindex and (result["articles"] or result["kb"]):
        from scripts.reindex_knowledge import sweep

        for r in sweep(result["articles"], verified=result["kb"]):
            target = "KB" if r.article_id is None else f"article {r.article_id}"
            logger.info(
                "reindex %s %s: %s embedded=%d kept=%d deleted=%d",
                target,
                r.source_type,
                r.status,
                r.embedded,
                r.kept,
                r.deleted,
            )
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
