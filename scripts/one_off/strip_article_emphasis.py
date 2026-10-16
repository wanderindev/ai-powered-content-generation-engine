#!/usr/bin/env python3
"""Strip bold/italic markdown from article bodies still awaiting the Grammarly pass (#540).

Background. Grammarly drops a ``**bold**`` / ``*italic*`` run whenever it
rewords the text inside it, and the grammar-apply meaning check compares stored
markdown, so a lost marker reads as a changed paragraph and sound suggestions
get reverted or filed for review. New articles are generated without emphasis
(prompt rule + ``strip_inline_emphasis`` in ``generate_article_content``); this
script does the same for articles that have not been proofread yet.

Approach. An article's EN body is in scope when ``grammar_checked_en_at IS
NULL``, its ES body when ``grammar_checked_es_at IS NULL`` — each language on
its own, so a proofread language is never touched. Each column is split with
``split_series_sections`` and only the body is stripped: the About this Article
and Continue Reading sections are re-attached byte-for-byte (``about + body +
cont == content`` is the splitter's guarantee). Titles and excerpts are not
touched.

Stray markers. Earlier proofread passes left broken emphasis behind in some of
these bodies — a marker inside a word (``junto co*n``), an opener with no closer
(``*muralla de mar,``), escaped ``\\*`` that renders as a literal asterisk,
``***Title***opened`` with the space lost. ``strip_inline_emphasis`` only removes
*paired* runs and leaves those alone, so this script adds a second, prose-only
pass: any remaining ``*`` / ``\\*`` run that touches text is removed, and a
run of two or more between two letters becomes a space (it was a closer that
swallowed one). A spaced lone ``*``, list markers and ``* * *`` rules are kept.
Every stray fix is printed with its context, dry run included.

Narration. ``segment_article`` strips ``**x**`` / ``*x*`` before hashing the
spoken text, so removing asterisk emphasis leaves every segment hash alone and a
``mode="changed"`` narration run finds nothing to re-pay. Underscore italics are
*not* stripped there, so a narrated ES body that loses ``_x_`` runs re-flags
those paragraphs; the report marks such rows ``narration re-flags``.

Safety. Dry-run by default (per-row summary; ``--verbose`` adds a unified diff).
``--no-dry-run`` requires ``--confirm-prod`` when the DB host looks
production-shaped. A timestamped JSON backup of every column about to change is
written before the UPDATE, and one commit covers the whole run.

Usage (inside the backend container, which already carries DATABASE_URL; the
image must include ``app.utils.markdown.strip_inline_emphasis``, i.e. #540 is
deployed):
    docker cp scripts/one_off/strip_article_emphasis.py pic-backend:/tmp/
    docker exec -w /app pic-backend python /tmp/strip_article_emphasis.py              # dry run
    docker exec -w /app pic-backend python /tmp/strip_article_emphasis.py --verbose    # + diffs
    docker exec -w /app pic-backend python /tmp/strip_article_emphasis.py \\
        --no-dry-run --confirm-prod
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
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
from app.services.series_sections import split_series_sections  # noqa: E402
from app.utils.markdown import strip_inline_emphasis  # noqa: E402

_COLUMNS = {"en": ("content", "grammar_checked_en_at"), "es": ("content_es", "grammar_checked_es_at")}

# Emphasis-looking leftovers the helper deliberately leaves alone (an opener it
# cannot pair, e.g. ``**Note:**text``) — reported so a human can eyeball them.
_RESIDUAL_RE = re.compile(r"\*\*|(?<![\w*\\])\*(?=[^\s*])")
_STRAY_RE = re.compile(r"\\?\*+")
_LIST_MARKER_RE = re.compile(r"^(\s*(?:[*+-]|\d+[.)])\s+)")
_RULE_RE = re.compile(r"^\s*([*_-])(?:\s*\1){2,}\s*$")


def clean_stray_markers(body: str) -> tuple[str, list[str]]:
    """Remove unpaired ``*`` / ``\\*`` runs left in prose; return (body, contexts)."""
    fixes: list[str] = []
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if _RULE_RE.match(line):
            continue
        marker = _LIST_MARKER_RE.match(line)
        head = marker.group(1) if marker else ""
        rest = line[len(head) :]

        def _fix(m: re.Match, rest: str = rest) -> str:
            before = rest[m.start() - 1] if m.start() else " "
            after = rest[m.end()] if m.end() < len(rest) else " "
            if before.isspace() and after.isspace():
                return m.group(0)  # a spaced lone star is not emphasis
            stars = m.group(0).count("*")
            replacement = " " if stars >= 2 and before.isalpha() and after.isalpha() else ""
            fixes.append(rest[max(0, m.start() - 40) : m.end() + 40])
            return replacement

        lines[i] = head + _STRAY_RE.sub(_fix, rest)
    return "\n".join(lines), fixes


def _is_prod_host(database_url: str) -> bool:
    parsed = urlparse(database_url)
    return "ondigitalocean.com" in (parsed.hostname or "") or parsed.port == 25060


def _safe_host_label(database_url: str) -> str:
    parsed = urlparse(database_url)
    return f"{parsed.hostname}:{parsed.port}"


def _write_backup(changes: list[dict], host: str) -> Path:
    # Repo checkout → scratch/ (gitignored); inside the container → /tmp.
    out_dir = _BACKEND_DIR.parent / "scratch" if _BACKEND_DIR.is_dir() else Path("/tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"strip_article_emphasis_{ts}.json"
    path.write_text(
        json.dumps(
            {
                "timestamp_utc": ts,
                "target_host": host,
                "issue": "https://github.com/wanderindev/panama-in-context/issues/540",
                "rows": [
                    {"article_id": c["id"], "column": c["column"], "before": c["before"]}
                    for c in changes
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return path


def plan_changes(articles: list[Article], languages: tuple[str, ...]) -> list[dict]:
    """Compute the per-(article, language) rewrites without touching the rows."""
    changes: list[dict] = []
    for article in articles:
        for language in languages:
            column, stamp = _COLUMNS[language]
            if getattr(article, stamp) is not None:
                continue  # already proofread in this language
            before = getattr(article, column) or ""
            if not before.strip():
                continue
            about, body, cont = split_series_sections(before)
            new_body, stray_fixes = clean_stray_markers(strip_inline_emphasis(body))
            if new_body == body:
                continue
            after = about + new_body + cont
            changes.append(
                {
                    "id": article.id,
                    "language": language,
                    "column": column,
                    "status": article.status,
                    "published": article.published_at is not None,
                    "before": before,
                    "after": after,
                    "chars_removed": len(body) - len(new_body),
                    "stray_fixes": stray_fixes,
                    "residual": len(_RESIDUAL_RE.findall(new_body)),
                    "narration_reflags": (
                        language == "es"
                        and bool(article.audio_url_es)
                        and body.count("_") != new_body.count("_")
                    ),
                }
            )
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--article", type=int, help="Limit to one article id")
    parser.add_argument("--language", choices=("en", "es", "both"), default="both")
    parser.add_argument("--verbose", action="store_true", help="Print a unified diff per row")
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

    languages = ("en", "es") if args.language == "both" else (args.language,)
    db = SessionLocal()
    try:
        query = db.query(Article).order_by(Article.id)
        if args.article is not None:
            query = query.filter(Article.id == args.article)
        articles = query.all()
        changes = plan_changes(articles, languages)

        print(f"Target {host} — {len(articles)} articles scanned, {len(changes)} bodies to strip")
        for c in changes:
            flags = []
            if c["stray_fixes"]:
                flags.append(f"{len(c['stray_fixes'])} stray marker(s)")
            if c["residual"]:
                flags.append(f"{c['residual']} residual marker(s)")
            if c["narration_reflags"]:
                flags.append("narration re-flags")
            state = f"{c['status']}{'/published' if c['published'] else ''}"
            print(
                f"  article {c['id']:<5} {c['language']}  {state:<20} "
                f"-{c['chars_removed']} chars{'  [' + ', '.join(flags) + ']' if flags else ''}"
            )
            for context in c["stray_fixes"]:
                print(f"      stray: {context!r}")
            if args.verbose:
                diff = difflib.unified_diff(
                    c["before"].splitlines(), c["after"].splitlines(), "stored", "stripped", lineterm="", n=0
                )
                print("\n".join(diff))

        narrated = sum(1 for c in changes if c["narration_reflags"])
        if narrated:
            print(f"\n{narrated} narrated ES bodies lose _underscore_ italics (a 'changed' narration run re-pays those).")
        if args.dry_run:
            print("\nDry run — nothing written.")
            return 0
        if not changes:
            print("\nNothing to write.")
            return 0

        backup = _write_backup(changes, host)
        print(f"\nBackup written to {backup}")

        by_id = {a.id: a for a in articles}
        for c in changes:
            setattr(by_id[c["id"]], c["column"], c["after"])
        db.commit()

        for c in changes:
            db.refresh(by_id[c["id"]])
            assert getattr(by_id[c["id"]], c["column"]) == c["after"], f"article {c['id']} did not persist"
        print(f"Committed {len(changes)} bodies at {datetime.now(UTC).isoformat()}.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
