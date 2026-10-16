"""Rewrite dot-form thousands (``1.500``) to the comma form (``1,500``) in stored Spanish text.

DeepL (ES-419) writes thousands inconsistently, so older Spanish content mixes
``1.500`` and ``1,500``, sometimes in one article (#531). New translations are
normalised in ``TranslationService``; this script applies the same
``normalize_es_thousands`` helper to what is already stored:

- ``articles.content_es`` / ``title_es`` / ``excerpt_es``
- ``edu_materials.content_es`` and the prose leaves of ``content_blocks_es``
  (machine keys such as ``image_query`` / ``media_path`` are skipped)

Every changed number is printed with its context. Rewriting ``content_es`` on a
**narrated** article changes the spoken text of the affected paragraphs, so the
next ``mode="changed"`` narration run re-synthesises them (the voice reads
``1.500`` and ``1,500`` the same way). The report counts those articles so the
operator can decide whether to re-run narration or leave it. The Grammarly
``fingerprint`` is computed at request time, not stored, so grammar-checked
articles are safe to rewrite.

Usage (from ``backend/``, or inside the backend container with ``-w /app``):
    python -m scripts.normalize_es_thousands                # Preview (default)
    python -m scripts.normalize_es_thousands --execute      # Write the changes
"""

import argparse
import logging
import re
from dataclasses import dataclass, field

from app.core.database import SessionLocal
from app.models.article import Article
from app.models.edu_material import EduMaterial
from app.services.translation import normalize_es_thousands
from app.utils.guide_blocks import map_prose
from sqlalchemy.orm import Session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_ARTICLE_COLUMNS = ("content_es", "title_es", "excerpt_es")
_DOT_NUMBER_RE = re.compile(r"[1-9]\d{0,2}(?:\.\d{3})+")


@dataclass
class Change:
    table: str
    row_id: int
    column: str
    numbers: list[str] = field(default_factory=list)  # "1.500 -> 1,500" with context


def _changed_numbers(before: str, after: str) -> list[str]:
    """The dot-form numbers that differ between *before* and *after*, with context."""
    out = []
    for m in _DOT_NUMBER_RE.finditer(before):
        if after[m.start() : m.end()] != m.group(0):
            ctx = before[max(0, m.start() - 30) : m.end() + 20].replace("\n", " ")
            out.append(f"{m.group(0)} -> {m.group(0).replace('.', ',')}  …{ctx}…")
    return out


def normalize_es_thousands_rows(db: Session, *, execute: bool = False) -> dict:
    """Normalise every stored Spanish column; write only when *execute*.

    Returns ``{"changes": [Change, ...], "narrated_articles": [ids]}``.
    """
    changes: list[Change] = []
    narrated: set[int] = set()

    for article in db.query(Article).order_by(Article.id):
        for column in _ARTICLE_COLUMNS:
            before = getattr(article, column)
            if not before:
                continue
            after = normalize_es_thousands(before)
            if after == before:
                continue
            changes.append(Change("articles", article.id, column, _changed_numbers(before, after)))
            if column == "content_es" and article.audio_url_es:
                narrated.add(article.id)
            if execute:
                setattr(article, column, after)

    for material in db.query(EduMaterial).order_by(EduMaterial.id):
        if material.content_es:
            after = normalize_es_thousands(material.content_es)
            if after != material.content_es:
                changes.append(
                    Change(
                        "edu_materials",
                        material.id,
                        "content_es",
                        _changed_numbers(material.content_es, after),
                    )
                )
                if execute:
                    material.content_es = after
        if material.content_blocks_es:
            numbers: list[str] = []

            def _fix(text: str, numbers: list[str] = numbers) -> str:
                fixed = normalize_es_thousands(text)
                if fixed != text:
                    numbers.extend(_changed_numbers(text, fixed))
                return fixed

            new_blocks = map_prose(material.content_blocks_es, _fix)
            if numbers:
                changes.append(Change("edu_materials", material.id, "content_blocks_es", numbers))
                if execute:
                    material.content_blocks_es = new_blocks

    if execute and changes:
        db.commit()
    else:
        db.rollback()
    return {"changes": changes, "narrated_articles": sorted(narrated)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--execute", action="store_true", help="Write the changes (default: preview only)"
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = normalize_es_thousands_rows(db, execute=args.execute)
    finally:
        db.close()

    changes = result["changes"]
    for change in changes:
        logger.info("%s %d %s: %d number(s)", change.table, change.row_id, change.column, len(change.numbers))
        for line in change.numbers:
            logger.info("    %s", line)
    rows = {(c.table, c.row_id) for c in changes}
    logger.info(
        "%s %d column(s) across %d row(s); %d narrated article(s) affected%s",
        "Rewrote" if args.execute else "DRY RUN: would rewrite",
        len(changes),
        len(rows),
        len(result["narrated_articles"]),
        f": {result['narrated_articles']}" if result["narrated_articles"] else "",
    )


if __name__ == "__main__":
    main()
