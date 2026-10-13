"""Align prod to the models: created/updated server defaults + min_age NOT NULL.

Revision ID: 0001_align_prod_to_models
Revises: 0000_baseline
Create Date: 2026-05-30

Two reconciliations deferred from the model-drift PR (#3 prep). The models
declare these; prod (captured in the 0000 baseline) does not, so they run as
forward changes that bring prod — and every fresh database — into line:

1. ``created_at`` / ``updated_at`` gain ``DEFAULT now()`` on the Flask-era
   tables that lacked a server default (the models carry ``server_default=now()``).
2. ``attractions.min_age`` becomes NOT NULL (the model declares it NOT NULL;
   prod had it nullable-with-default).

Orphan-table/enum drops are intentionally NOT here — they land in #4.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_align_prod_to_models"
down_revision = "0000_baseline"
branch_labels = None
depends_on = None

# Flask-era tables whose created_at/updated_at lack a server default in prod
# but carry server_default=now() in the models.
_TIMESTAMP_TABLES = [
    "articles",
    "article_suggestions",
    "categories",
    "media",
    "research",
    "tags",
    "taxonomies",
    "users",
]


def upgrade() -> None:
    for table in _TIMESTAMP_TABLES:
        op.execute(f'ALTER TABLE public."{table}" ALTER COLUMN created_at SET DEFAULT now()')
        op.execute(f'ALTER TABLE public."{table}" ALTER COLUMN updated_at SET DEFAULT now()')

    # min_age: backfill any nulls (defensive — prod default is 0), then enforce NOT NULL.
    op.execute("UPDATE public.attractions SET min_age = 0 WHERE min_age IS NULL")
    op.execute("ALTER TABLE public.attractions ALTER COLUMN min_age SET NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE public.attractions ALTER COLUMN min_age DROP NOT NULL")
    for table in _TIMESTAMP_TABLES:
        op.execute(f'ALTER TABLE public."{table}" ALTER COLUMN created_at DROP DEFAULT')
        op.execute(f'ALTER TABLE public."{table}" ALTER COLUMN updated_at DROP DEFAULT')
