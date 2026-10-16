"""Add articles.audio_manifest_es — per-segment narration manifest.

Ordered list of {hash, key, is_heading, duration_ms} dicts, one per narration
segment (title / heading / paragraph). Written by the generate-audio endpoint;
lets a later regeneration re-synthesize only the segments whose text changed
(hash no longer in the manifest) and reuse the stored MP3s for the rest.
Nullable: audio generated before this column existed has no manifest, and the
first full regeneration backfills it.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0031_article_audio_manifest_es"
down_revision = "0030_article_audio_url_es"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("audio_manifest_es", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "audio_manifest_es")
