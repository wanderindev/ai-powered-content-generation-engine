"""Drop Flask-era orphan tables, enums, and media residue columns (closes #4).

Revision ID: 0002_drop_flask_orphans
Revises: 0001_align_prod_to_models
Create Date: 2026-05-30

The production database was carried over from the original Flask app. These
objects belong to functionality that was dropped or never reimplemented in the
FastAPI rewrite and are unused by the current app:

- Tables (9): agents, ai_models, api_usage, approved_languages, hashtag_groups,
  social_media_accounts, social_media_posts, social_media_post_media,
  translations. (Some hold stale Flask-era rows; the operator confirmed they
  are dead and takes a backup before applying — the data is intentionally
  discarded.)
- Enum types (5): agent_type, platform_type, post_type, provider_type,
  instagram_media_type. (content_status_type, media_type_type and
  media_source_type are KEPT — still used by live tables.)
- Media residue columns (2): media.external_url, media.instagram_media_type.

No kept table references any orphan (verified against the baseline), so the
drops cannot cascade into live tables. downgrade() recreates the structure from
0002_restore_flask_orphans.sql (extracted from the baseline); the original rows
are not recoverable, which is inherent to dropping.
"""

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_drop_flask_orphans"
down_revision = "0001_align_prod_to_models"
branch_labels = None
depends_on = None

_RESTORE_SQL = os.path.join(os.path.dirname(__file__), "0002_restore_flask_orphans.sql")

_ORPHAN_TABLES = [
    "social_media_post_media", "social_media_posts", "social_media_accounts",
    "agents", "translations", "ai_models", "api_usage", "approved_languages",
    "hashtag_groups",
]
# Orphan enums only. content_status_type / media_type_type / media_source_type stay.
_ORPHAN_ENUMS = [
    "agent_type", "platform_type", "post_type", "provider_type",
    "instagram_media_type",
]


def upgrade() -> None:
    bind = op.get_bind()
    # 1. Media residue columns (instagram_media_type column must go before its enum).
    bind.exec_driver_sql("ALTER TABLE public.media DROP COLUMN IF EXISTS external_url")
    bind.exec_driver_sql("ALTER TABLE public.media DROP COLUMN IF EXISTS instagram_media_type")
    # 2. Orphan tables (CASCADE clears the inter-orphan FK constraints; no kept
    #    table references these, verified against the baseline).
    for table in _ORPHAN_TABLES:
        bind.exec_driver_sql(f'DROP TABLE IF EXISTS public."{table}" CASCADE')
    # 3. Orphan enum types (now that every column using them is gone).
    for enum in _ORPHAN_ENUMS:
        bind.exec_driver_sql(f'DROP TYPE IF EXISTS public."{enum}"')


def downgrade() -> None:
    """Recreate the orphan structure (data is not restored)."""
    with open(_RESTORE_SQL, encoding="utf-8") as f:
        op.get_bind().exec_driver_sql(f.read())
