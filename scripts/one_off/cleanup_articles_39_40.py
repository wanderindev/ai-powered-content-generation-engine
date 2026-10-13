#!/usr/bin/env python3
"""One-off cleanup for the doubled series-section blocks on articles 39 and 40.

Issue #99 background. The series-sections endpoint had no regeneration guard
and built navigation by raw-SQL string concatenation (`content = :about ||
content || :continue`). A re-run on the 2-part series (articles 39 and 40)
produced `about + about + body + continue + continue` in both content and
content_es. This script removes the duplicate blocks deterministically.

Approach. Re-derive the canonical about/continue sections via
SeriesSectionGenerator from the current article + suggestion metadata (so the
script is robust to title changes). Assert each affected column contains the
canonical duplicate exactly twice (or zero, for article 40's continue). Replace
the leading duplicate about-section with `replace(canonical, "", 1)` and trim
the trailing duplicate continue-section by `endswith`-check. Re-assert that
each column now contains the canonical exactly once.

Safety. Dry-run by default. `--no-dry-run` requires `--confirm-prod` if the
target host looks production-shaped (DigitalOcean managed DB host or port
25060). A timestamped JSON backup of the pre-update columns is written to
scratch/ before any UPDATE. Asserts fail loud — if pre-state does not match
expectations, the script aborts before writing.

Usage.
    DATABASE_URL=postgresql://... python scripts/one_off/cleanup_articles_39_40.py --dry-run
    DATABASE_URL=postgresql://... python scripts/one_off/cleanup_articles_39_40.py --no-dry-run
    DATABASE_URL=postgresql://... python scripts/one_off/cleanup_articles_39_40.py --no-dry-run --confirm-prod
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Make `app.*` importable when the script runs from the backend container or
# from a checkout where backend/ is the package root.
_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if (_BACKEND_DIR / "app").is_dir() and str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.models.article import Article  # noqa: E402
from app.services.series_sections import (  # noqa: E402
    ABOUT_MARKER_EN,
    ABOUT_MARKER_ES,
    SeriesSectionGenerator,
)

SERIES_PARENT_ID = 39
AFFECTED_IDS = (39, 40)
SCRATCH_DIR = Path(__file__).resolve().parents[2] / "scratch"


@dataclass
class CleanupPlan:
    """Per-(article, lang) cleanup plan: canonical strings + pre-state + cleaned column."""

    article_id: int
    lang: str  # "en" or "es"
    column: str  # "content" or "content_es"
    canonical_about: str
    canonical_continue: str  # "" for article 40
    before: str
    cleaned: str
    pre_about_count: int
    pre_continue_count: int
    post_about_count: int
    post_continue_count: int


def _is_prod_host(database_url: str) -> bool:
    """Return True if the DATABASE_URL looks like a managed-prod host."""
    parsed = urlparse(database_url)
    host = (parsed.hostname or "").lower()
    port = parsed.port or 0
    return "ondigitalocean.com" in host or port == 25060


def _safe_host_label(database_url: str) -> str:
    """Return host:port from DATABASE_URL without leaking credentials."""
    parsed = urlparse(database_url)
    host = parsed.hostname or "(unknown)"
    port = parsed.port or "(no-port)"
    return f"{host}:{port}"


def _build_plans(session: Session) -> list[CleanupPlan]:
    """For each (article_id, lang) build the canonical-derived cleanup plan."""
    generator = SeriesSectionGenerator(session)
    suggestion, articles = generator.get_series_info(SERIES_PARENT_ID)
    if not suggestion or not articles:
        raise RuntimeError(
            f"Series parent {SERIES_PARENT_ID} not found or has no suggestion/articles."
        )

    article_by_id = {a.id: a for a in articles}
    missing = [aid for aid in AFFECTED_IDS if aid not in article_by_id]
    if missing:
        raise RuntimeError(
            f"Expected articles {AFFECTED_IDS} in series {SERIES_PARENT_ID}; "
            f"missing {missing}. Refusing to proceed."
        )

    plans: list[CleanupPlan] = []
    for aid in AFFECTED_IDS:
        article_info = article_by_id[aid]
        # next_article is None for the last article in the series.
        idx = articles.index(article_info)
        next_article = articles[idx + 1] if idx + 1 < len(articles) else None

        article_row = session.query(Article).filter(Article.id == aid).first()
        if article_row is None:
            raise RuntimeError(f"Article {aid} row not found.")

        for lang in ("en", "es"):
            canonical_about = generator.generate_about_section(
                article_info, articles, suggestion, lang
            )
            canonical_continue = generator.generate_continue_reading_section(
                article_info, next_article, lang
            )
            column = "content" if lang == "en" else "content_es"
            before = getattr(article_row, column) or ""

            pre_about_count = before.count(canonical_about)

            # Continue-section match. The current generator emits two leading
            # newlines ("\n\n---\n\n**Continue Reading**..."). The corruption
            # stored on articles 39/40 has only ONE leading newline before the
            # rule ("\n---\n\n**Continue Reading**...") — identical from "---"
            # onward, one "\n" short at the boundary. Most plausible cause:
            # a trailing newline on the original body got absorbed by a later
            # admin edit, or an early markdown-normalization step collapsed
            # adjacent blanks. Match the canonical first; fall back to the
            # one-newline-shorter form if and only if it appears exactly
            # twice. Anything else aborts.
            if canonical_continue:
                pre_continue_count = before.count(canonical_continue)
                if pre_continue_count != 2:
                    fallback_continue = canonical_continue.lstrip("\n")
                    if fallback_continue:
                        fallback_continue = "\n" + fallback_continue
                    fallback_count = (
                        before.count(fallback_continue) if fallback_continue else 0
                    )
                    if fallback_count == 2:
                        canonical_continue = fallback_continue
                        pre_continue_count = fallback_count
            else:
                pre_continue_count = 0

            # Pre-condition asserts — loud failure if the corruption is not
            # exactly what the issue body describes.
            if pre_about_count != 2:
                raise AssertionError(
                    f"Article {aid} {column}: expected canonical about-section "
                    f"to appear exactly 2 times, found {pre_about_count}. "
                    f"Aborting. The corruption shape does not match #99."
                )
            if canonical_continue and pre_continue_count != 2:
                raise AssertionError(
                    f"Article {aid} {column}: expected canonical "
                    f"continue-section to appear exactly 2 times (canonical "
                    f"or one-leading-newline-shorter fallback), "
                    f"found {pre_continue_count}. Aborting."
                )

            # Remove the leading duplicate about-section.
            cleaned = before.replace(canonical_about, "", 1)

            # Trim the trailing duplicate continue-section if present.
            if canonical_continue and cleaned.endswith(
                canonical_continue + canonical_continue
            ):
                cleaned = cleaned[: -len(canonical_continue)]

            post_about_count = cleaned.count(canonical_about)
            post_continue_count = (
                cleaned.count(canonical_continue) if canonical_continue else 0
            )

            if post_about_count != 1:
                raise AssertionError(
                    f"Article {aid} {column}: post-cleanup about-section "
                    f"count is {post_about_count}, expected 1. Aborting."
                )
            if canonical_continue and post_continue_count != 1:
                raise AssertionError(
                    f"Article {aid} {column}: post-cleanup continue-section "
                    f"count is {post_continue_count}, expected 1. Aborting."
                )

            plans.append(
                CleanupPlan(
                    article_id=aid,
                    lang=lang,
                    column=column,
                    canonical_about=canonical_about,
                    canonical_continue=canonical_continue,
                    before=before,
                    cleaned=cleaned,
                    pre_about_count=pre_about_count,
                    pre_continue_count=pre_continue_count,
                    post_about_count=post_about_count,
                    post_continue_count=post_continue_count,
                )
            )

    return plans


def _print_dry_run(plans: list[CleanupPlan]) -> None:
    print()
    print("== Canonical regenerated sections ==")
    seen: set[str] = set()
    for p in plans:
        key = f"about::{p.article_id}::{p.lang}"
        if key not in seen:
            print(f"\n-- about (article {p.article_id}, {p.lang}) --")
            print(p.canonical_about)
            seen.add(key)
    for p in plans:
        if not p.canonical_continue:
            continue
        key = f"continue::{p.article_id}::{p.lang}"
        if key not in seen:
            print(f"\n-- continue (article {p.article_id}, {p.lang}) --")
            print(p.canonical_continue)
            seen.add(key)

    print()
    print("== Per-column diff summary (first 300 + last 300 chars) ==")
    for p in plans:
        print(
            f"\n-- article {p.article_id} / {p.column} "
            f"(pre about={p.pre_about_count}, continue={p.pre_continue_count}; "
            f"post about={p.post_about_count}, continue={p.post_continue_count}) --"
        )
        print(f"  len(before)={len(p.before)}, len(cleaned)={len(p.cleaned)}, "
              f"removed={len(p.before) - len(p.cleaned)} chars")
        print("  before[:300] =", repr(p.before[:300]))
        print("  before[-300:] =", repr(p.before[-300:]))
        print("  cleaned[:300] =", repr(p.cleaned[:300]))
        print("  cleaned[-300:] =", repr(p.cleaned[-300:]))


def _write_backup(plans: list[CleanupPlan], target_host: str) -> Path:
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = SCRATCH_DIR / f"cleanup_articles_39_40_{ts}.json"
    by_article: dict[int, dict[str, str]] = {}
    for p in plans:
        by_article.setdefault(p.article_id, {})[p.column] = p.before
    payload = {
        "timestamp_utc": ts,
        "target_host": target_host,
        "issue": "https://github.com/wanderindev/panama-in-context/issues/99",
        "articles": [
            {"id": aid, **cols} for aid, cols in sorted(by_article.items())
        ],
    }
    backup_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return backup_path


def _apply(session: Session, plans: list[CleanupPlan]) -> None:
    """Apply the cleaned columns via the ORM, one transaction per article."""
    by_article: dict[int, dict[str, str]] = {}
    for p in plans:
        by_article.setdefault(p.article_id, {})[p.column] = p.cleaned

    for aid, cols in sorted(by_article.items()):
        article = session.query(Article).filter(Article.id == aid).first()
        if article is None:
            raise RuntimeError(f"Article {aid} disappeared between plan and apply.")
        if "content" in cols:
            article.content = cols["content"]
        if "content_es" in cols:
            article.content_es = cols["content_es"]
        session.add(article)
        session.commit()

        # Re-read to verify post-state at the row level.
        session.refresh(article)
        if (article.content or "").count(ABOUT_MARKER_EN) != 1:
            raise AssertionError(
                f"Article {aid} content post-commit has "
                f"{(article.content or '').count(ABOUT_MARKER_EN)} EN about-markers, "
                f"expected 1."
            )
        if (article.content_es or "").count(ABOUT_MARKER_ES) != 1:
            raise AssertionError(
                f"Article {aid} content_es post-commit has "
                f"{(article.content_es or '').count(ABOUT_MARKER_ES)} ES about-markers, "
                f"expected 1."
            )
        print(f"  article {aid}: committed; post-counts verified.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "One-off cleanup for doubled series-section blocks on articles 39 "
            "and 40 (#99)."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Default. Prints planned changes; does not write.",
    )
    mode.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Apply the cleanup. Requires --confirm-prod when DATABASE_URL is prod-shaped.",
    )
    parser.set_defaults(dry_run=True)
    parser.add_argument(
        "--confirm-prod",
        action="store_true",
        help="Required acknowledgement when --no-dry-run targets a prod-shaped DATABASE_URL.",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("ERROR: DATABASE_URL env var is not set.", file=sys.stderr)
        return 2

    host_label = _safe_host_label(database_url)
    is_prod = _is_prod_host(database_url)
    mode_label = "DRY-RUN" if args.dry_run else "WRITE"

    print(f"== cleanup_articles_39_40 ({mode_label}) ==")
    print(f"  target host: {host_label}")
    print(f"  prod-shaped: {is_prod}")
    print(f"  scratch dir: {SCRATCH_DIR}")
    print()

    if not args.dry_run and is_prod and not args.confirm_prod:
        print(
            "ERROR: --no-dry-run against a prod-shaped DATABASE_URL requires "
            "--confirm-prod.",
            file=sys.stderr,
        )
        return 2

    engine = create_engine(database_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = SessionLocal()
    try:
        plans = _build_plans(session)
        _print_dry_run(plans)

        if args.dry_run:
            print()
            print("DRY-RUN complete. No writes performed.")
            return 0

        # Write the backup before any UPDATE.
        backup_path = _write_backup(plans, host_label)
        print()
        print(f"Backup written: {backup_path}")
        print("Applying cleanup via ORM...")
        _apply(session, plans)
        print()
        print("Cleanup complete.")
        return 0
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
