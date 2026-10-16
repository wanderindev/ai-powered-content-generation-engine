"""Seed the local dev database with content from production.

Reads from prod (DATABASE_URL env var — `source .env.prod-readonly` first),
writes to local (hard-coded localhost:5433). Copies only the column intersection
between local and prod schemas, so it tolerates the known drift where prod has
extra columns local's models don't define.

Usage:
    set -a; source .env.prod-readonly; set +a
    python backend/scripts/seed_local_db.py

Tables seeded: content/reference (articles, categories, taxonomies, tours, etc.),
plus `knowledge_chunks` with its vectors (the RAG eval's corpus, #627 — about
14k rows and a few hundred MB from prod, so expect it to be the slow one).
Generated columns (the `search_vector` tsvectors) are skipped on the read side
and recomputed by local Postgres.
Tables NOT seeded (PII / operational): users, admin_emails, magic_links,
contact_submissions, bookings, booking_status_log, orders, educators.
"""

from __future__ import annotations

import os
import sys

from psycopg2.extensions import register_adapter
from psycopg2.extras import Json
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Auto-adapt dict → jsonb so columns like `fixed_price_config` round-trip cleanly.
# We DON'T adapt list — psycopg2's default converts Python lists to Postgres arrays
# (needed for columns like media_candidates.tags which are TEXT[]).
register_adapter(dict, Json)

LOCAL_URL = "postgresql://panama:localdev@localhost:5433/panama_in_context"

# Order matters: parents before children for foreign keys.
SEED_TABLES = [
    "taxonomies",
    "tags",
    "attractions",
    "zones",
    "hotels",
    "pricing_config",
    "categories",
    "tours",
    "tour_attractions",
    "zone_transport_rates",
    "zone_attraction_rates",
    "media",
    "media_candidates",
    "media_tags",
    "articles",
    "article_tags",
    "knowledge_chunks",
    "article_suggestions",
    "research",
    "edu_suggestions",
    "edu_research",
    "edu_materials",
    "edu_material_files",
]


def ordered_columns(engine: Engine, table: str) -> list[str]:
    # Generated columns (e.g. `search_vector`) cannot be inserted into.
    sql = text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND is_generated = 'NEVER' "
        "ORDER BY ordinal_position"
    )
    with engine.connect() as c:
        return [r[0] for r in c.execute(sql, {"t": table})]


def json_columns(engine: Engine, table: str) -> set[str]:
    sql = text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t "
        "AND data_type IN ('json', 'jsonb')"
    )
    with engine.connect() as c:
        return {r[0] for r in c.execute(sql, {"t": table})}


def row_params(row, common: list[str], json_cols: set[str]) -> dict:
    """Bind one row, wrapping every json/jsonb value in `Json`.

    The dict adapter alone is not enough: a jsonb column holding a list
    (`articles.audio_manifest_es`) would otherwise bind as a Postgres array
    and fail with DatatypeMismatch.
    """
    return {
        f"p{i}": (Json(row[c]) if c in json_cols and row[c] is not None else row[c])
        for i, c in enumerate(common)
    }


def reset_sequence(engine: Engine, table: str) -> None:
    """Reset the table's PK sequence so future inserts don't collide.

    Skips tables that don't have a serial `id` column (junction tables, etc.).
    """
    with engine.begin() as c:
        has_id = c.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t AND column_name='id'"
            ),
            {"t": table},
        ).scalar()
        if not has_id:
            return
        seq = c.execute(
            text("SELECT pg_get_serial_sequence(:t, 'id')"),
            {"t": table},
        ).scalar()
        if not seq:
            return
        max_id = c.execute(text(f"SELECT MAX(id) FROM {table}")).scalar()
        if max_id is None:
            return
        c.execute(text(f"SELECT setval('{seq}', {max_id})"))


def main() -> int:
    prod_url = os.environ.get("DATABASE_URL")
    if not prod_url:
        sys.stderr.write("DATABASE_URL is not set. Did you `source .env.prod-readonly`?\n")
        return 2
    if "localhost" in prod_url or "127.0.0.1" in prod_url or "@db:" in prod_url:
        sys.stderr.write(
            f"Refusing to run: DATABASE_URL looks local, not prod. ({prod_url[:60]}…)\n"
        )
        return 2

    prod = create_engine(prod_url, pool_pre_ping=True)
    local = create_engine(LOCAL_URL, pool_pre_ping=True)

    # Truncate every seeded table in ONE statement before loading any. A
    # per-table `TRUNCATE ... CASCADE` empties every table that references it,
    # so truncating `research` after loading `articles` wiped articles (and,
    # through them, knowledge_chunks) right after they were seeded.
    present = [t for t in SEED_TABLES if ordered_columns(local, t)]
    with local.begin() as lc:
        lc.execute(text(f"TRUNCATE {', '.join(present)} RESTART IDENTITY CASCADE"))

    total_loaded = 0
    for table in SEED_TABLES:
        pcols = ordered_columns(prod, table)
        lcols = ordered_columns(local, table)
        if not pcols or not lcols:
            print(f"SKIP {table}: missing in prod or local")
            continue
        common = [c for c in lcols if c in pcols]
        prod_only = set(pcols) - set(lcols)
        json_cols = json_columns(local, table)

        col_list = ", ".join(f'"{c}"' for c in common)
        placeholders = ", ".join(f":p{i}" for i in range(len(common)))

        with prod.connect() as pc:
            rows = list(pc.execute(text(f"SELECT {col_list} FROM {table}")).mappings())

        with local.begin() as lc:
            # session_replication_role='replica' bypasses FK and other triggers.
            # Safer than ALTER TABLE DISABLE TRIGGER ALL since it's session-scoped.
            lc.execute(text("SET session_replication_role = 'replica'"))
            if rows:
                lc.execute(
                    text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"),
                    [row_params(row, common, json_cols) for row in rows],
                )
            lc.execute(text("SET session_replication_role = 'origin'"))

        reset_sequence(local, table)

        total_loaded += len(rows)
        drift_note = f" (dropped {sorted(prod_only)})" if prod_only else ""
        print(f"  {table}: loaded {len(rows)} rows{drift_note}")

    print(f"\nDone. {total_loaded} rows loaded across {len(SEED_TABLES)} tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
