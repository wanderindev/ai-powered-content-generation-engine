"""Add articles.audio_qa_es — the narration voice-check result (#601).

A JSONB summary written by every narration run: the article's pitch baseline,
the threshold, measured / skipped counts, the high-pitch segments that were
re-taken from ElevenLabs and the ones still to listen to, plus an
OK / FIXED / NEEDS_REVIEW status. Overwritten on each run. Nullable: every
narration made before this column has no voice check until it is regenerated.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0042_article_audio_qa_es"
down_revision = "0041_media_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("audio_qa_es", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "audio_qa_es")
