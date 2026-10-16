"""Add articles.audio_url_es — Diego-voice narration of the Spanish article.

Generated on demand from the admin drawer (ES tab) via ElevenLabs and stored
on DO Spaces; the public article page shows an audio player for it on the
Spanish surface only. Nullable: articles without generated audio simply have
no player.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0030_article_audio_url_es"
down_revision = "0029_article_slugs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("audio_url_es", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "audio_url_es")
