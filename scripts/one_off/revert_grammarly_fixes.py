#!/usr/bin/env python3
"""Revert a set of recorded grammar fixes on one article/language (#519).

Background. The first live Grammarly run (article 245 EN, 2026-09-20) accepted
25 suggestions and wrote them through ``grammar-apply``. The Grammar ones were
fine; the Clarity "Improve your text" rewrites flattened the prose and changed
meaning in places. Every accepted item is a ``grammar_check_fixes`` row
(``source='GRAMMARLY'``), so the audit table is the undo log.

Approach. For the given article/language and row ids, walk the rows in
**reverse id order** and put each row's ``find_text`` back where its
``replace_text`` now sits (``app.services.grammar_revert``): matching is
markdown-emphasis tolerant (the snippets are plain text, the body is markdown)
and a row reverts only when its snippet occurs exactly once. Reverse order is
what makes stacked fixes work — a later row's ``replace_text`` is what the
content currently says, and undoing it exposes the earlier row's. A row whose
snippet is absent or ambiguous is reported and skipped; the script aborts
before writing if *any* requested row cannot be reverted, unless
``--allow-partial`` is passed. Reverted rows are marked
``applied=False, skipped_reason='reverted manually'``; ``--clear-stamp`` nulls
``grammar_checked_<lang>_at`` so the article re-enters the proofread queue.

Safety. Dry-run by default (prints a unified diff of the body). ``--no-dry-run``
requires ``--confirm-prod`` when the DB host looks production-shaped. A
timestamped JSON backup of the pre-update content is written before the UPDATE.

Usage (inside the backend container, which already carries DATABASE_URL):
    docker cp scripts/one_off/revert_grammarly_fixes.py pic-backend:/tmp/
    docker exec -w /app pic-backend python /tmp/revert_grammarly_fixes.py \
        --article 245 --language en --ids 10-29 --clear-stamp            # dry run
    docker exec -w /app pic-backend python /tmp/revert_grammarly_fixes.py \
        --article 245 --language en --ids 10-29 --clear-stamp --no-dry-run --confirm-prod
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

# Make `app.*` importable from a checkout (scripts/one_off/ → repo root → backend/)
# as well as from the container, where /app is already the package root.
_PARENTS = Path(__file__).resolve().parents
_BACKEND_DIR = _PARENTS[2] / "backend" if len(_PARENTS) > 2 else Path("/nonexistent")
if (_BACKEND_DIR / "app").is_dir() and str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
elif (Path.cwd() / "app").is_dir():
    # `python /tmp/script.py` puts /tmp on sys.path, not the cwd (-w /app).
    sys.path.insert(0, str(Path.cwd()))

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.article import Article  # noqa: E402
from app.models.grammar_check import GrammarCheckFix  # noqa: E402
from app.services.grammar_revert import revert_snippet  # noqa: E402

REVERTED_REASON = "reverted manually"


def _is_prod_host(database_url: str) -> bool:
    parsed = urlparse(database_url)
    return "ondigitalocean.com" in (parsed.hostname or "") or parsed.port == 25060


def _safe_host_label(database_url: str) -> str:
    parsed = urlparse(database_url)
    return f"{parsed.hostname}:{parsed.port}"


def _parse_ids(spec: str) -> list[int]:
    """``"10-29"`` / ``"12,14,16"`` / a mix → sorted unique ids."""
    ids: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            ids.update(range(int(lo), int(hi) + 1))
        else:
            ids.add(int(part))
    if not ids:
        raise argparse.ArgumentTypeError("no row ids given")
    return sorted(ids)


def _column_for(language: str) -> str:
    return "content" if language == "en" else "content_es"


def _stamp_for(language: str) -> str:
    return "grammar_checked_en_at" if language == "en" else "grammar_checked_es_at"


def _write_backup(article_id: int, language: str, content: str, host: str) -> Path:
    # Repo checkout → scratch/ (gitignored); inside the container → /tmp.
    out_dir = _BACKEND_DIR.parent / "scratch" if _BACKEND_DIR.is_dir() else Path("/tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"revert_grammarly_fixes_{article_id}_{language}_{ts}.json"
    path.write_text(
        json.dumps(
            {
                "timestamp_utc": ts,
                "target_host": host,
                "issue": "https://github.com/wanderindev/panama-in-context/issues/519",
                "article_id": article_id,
                "language": language,
                "column": _column_for(language),
                "before": content,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--article", type=int, required=True, help="Article id")
    parser.add_argument("--language", choices=("en", "es"), required=True)
    parser.add_argument(
        "--ids", type=_parse_ids, required=True, help="grammar_check_fixes ids: '10-29', '12,14', or a mix"
    )
    parser.add_argument(
        "--clear-stamp",
        action="store_true",
        help="Also null grammar_checked_<lang>_at so the article re-enters the queue",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write even if some requested rows could not be reverted (default: abort)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", help="Default: report only")
    mode.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Write the changes")
    parser.set_defaults(dry_run=True)
    parser.add_argument("--confirm-prod", action="store_true", help="Required with --no-dry-run on prod")
    args = parser.parse_args()

    database_url = get_settings().database_url
    host = _safe_host_label(database_url)
    if not args.dry_run and _is_prod_host(database_url) and not args.confirm_prod:
        print(f"Refusing to write to production-shaped host {host} without --confirm-prod", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        article = db.get(Article, args.article)
        if article is None:
            print(f"Article {args.article} not found", file=sys.stderr)
            return 1
        column = _column_for(args.language)
        before: str = getattr(article, column) or ""
        if not before.strip():
            print(f"Article {args.article} has no {args.language} content", file=sys.stderr)
            return 1

        rows = (
            db.query(GrammarCheckFix)
            .filter(
                GrammarCheckFix.article_id == args.article,
                GrammarCheckFix.language == args.language,
                GrammarCheckFix.id.in_(args.ids),
            )
            .order_by(GrammarCheckFix.id.desc())
            .all()
        )
        found_ids = {r.id for r in rows}
        missing = [i for i in args.ids if i not in found_ids]
        if missing:
            print(
                f"Rows not found for article {args.article}/{args.language}: {missing}",
                file=sys.stderr,
            )
            return 1

        print(f"Target {host} — article {args.article} ({column}), {len(rows)} rows, newest first")
        content = before
        reverted: list[GrammarCheckFix] = []
        failed: list[tuple[GrammarCheckFix, str]] = []
        for row in rows:
            tag = "" if row.applied else " (already applied=False)"
            result = revert_snippet(content, row.replace_text, row.find_text)
            if result.applied:
                content = result.content
                reverted.append(row)
                print(f"  id={row.id:<5} REVERT{tag}")
            else:
                failed.append((row, result.reason or "unknown"))
                print(f"  id={row.id:<5} SKIP   {result.reason}{tag}")

        print()
        print(f"Reverted {len(reverted)} of {len(rows)} rows; {len(failed)} could not be reverted.")
        diff = difflib.unified_diff(
            before.splitlines(), content.splitlines(), "stored", "reverted", lineterm="", n=0
        )
        print("\n".join(diff) or "(no textual change)")

        if failed and not args.allow_partial:
            print("\nAborting: some rows could not be reverted (pass --allow-partial to write anyway).")
            return 1
        if args.dry_run:
            print("\nDry run — nothing written.")
            return 0
        if not reverted:
            print("\nNothing to write.")
            return 0

        backup = _write_backup(args.article, args.language, before, host)
        print(f"\nBackup written to {backup}")

        setattr(article, column, content)
        if args.clear_stamp:
            setattr(article, _stamp_for(args.language), None)
        now = datetime.now(UTC)
        for row in reverted:
            row.applied = False
            row.skipped_reason = REVERTED_REASON
        db.commit()

        db.refresh(article)
        assert getattr(article, column) == content, "post-commit content does not match"
        print(
            f"Committed at {now.isoformat()}: {len(reverted)} rows marked applied=False "
            f"('{REVERTED_REASON}'); stamp {'cleared' if args.clear_stamp else 'left as is'}."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
