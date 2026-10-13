"""Baseline schema — exact reproduction of production at Alembic adoption.

Revision ID: 0000_baseline
Revises:
Create Date: 2026-05-30

The production database was originally bootstrapped via
``Base.metadata.create_all()`` (Flask era) and never had a materialising
migration — the pre-existing migrations only tracked deltas on top of that
implicit baseline, so ``alembic upgrade head`` against an empty database could
not reproduce the schema (closes #3).

This revision is that missing baseline. ``upgrade()`` replays the exact
production schema captured with ``pg_dump --schema-only`` (see the companion
``0000_baseline_schema.sql``), verified to reproduce prod's tables, columns,
enum types, constraints and indexes with zero drift. A fresh database therefore
reaches prod-equivalent schema through migrations alone.

The 21 pre-baseline migrations are archived under ``alembic/versions_archive/``
(out of Alembic's path, kept for history). Production and existing local
databases are *stamped* at this revision rather than re-created — see the PR
runbook. Improvements on top of prod's current state (server defaults,
``min_age`` NOT NULL) land in the following ``0001_align_prod_to_models``
revision; the Flask-era orphan tables/enums are dropped later by #4.
"""

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0000_baseline"
down_revision = None
branch_labels = None
depends_on = None

_SQL_FILE = os.path.join(os.path.dirname(__file__), "0000_baseline_schema.sql")

# Everything the baseline creates (drop targets for downgrade). alembic_version
# is intentionally excluded — Alembic owns it.
_TABLES = [
    "admin_emails", "agents", "ai_models", "api_usage", "approved_languages",
    "articles", "article_suggestions", "article_tags", "attractions", "bookings",
    "booking_status_log", "categories", "contact_submissions", "educators",
    "edu_material_files", "edu_materials", "edu_research", "edu_suggestions",
    "hashtag_groups", "hotels", "magic_links", "media", "media_candidates",
    "media_tags", "orders", "pricing_config", "research", "social_media_accounts",
    "social_media_post_media", "social_media_posts", "tags", "taxonomies",
    "tour_attractions", "tours", "translations", "users", "zone_attraction_rates",
    "zones", "zone_transport_rates",
]
_TYPES = [
    "agent_type", "content_status_type", "instagram_media_type",
    "media_source_type", "media_type_type", "platform_type", "post_type",
    "provider_type",
]


def upgrade() -> None:
    """Replay the captured production schema verbatim."""
    with open(_SQL_FILE, encoding="utf-8") as f:
        op.get_bind().exec_driver_sql(f.read())


def downgrade() -> None:
    """Tear the baseline back down, leaving alembic_version intact."""
    bind = op.get_bind()
    for table in _TABLES:
        bind.exec_driver_sql(f'DROP TABLE IF EXISTS public."{table}" CASCADE')
    for enum in _TYPES:
        bind.exec_driver_sql(f'DROP TYPE IF EXISTS public."{enum}" CASCADE')
