"""article_suggestions: duplicate-topic flag + stored vector (#630, epic #618).

Purely additive: four nullable columns, two FK indexes and one CHECK, no data
writes. Nothing writes them while ``SUGGESTION_DEDUP_ENABLED`` is off.

* ``embedding vector(1024)`` (voyage-4, document input type) of the
  suggestion's title + main topic. A cache for the suggestion-to-suggestion
  comparison; not an ORM attribute (``exclude_properties``, like
  ``knowledge_chunks.embedding``). No HNSW index: the table holds hundreds of
  rows and is scanned once per generated suggestion.
* ``duplicate_article_id`` / ``duplicate_suggestion_id``: the advisory
  "possible duplicate of" match, both ``ON DELETE SET NULL``. At most one is set
  (``ck_article_suggestions_single_duplicate``).
* ``duplicate_similarity``: cosine similarity of that match.

Needs the ``vector`` extension from 0043.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = "0045_suggestion_dedup"
down_revision = "0044_knowledge_chunks"
branch_labels = None
depends_on = None

_TABLE = "article_suggestions"
_CHECK = "ck_article_suggestions_single_duplicate"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("embedding", Vector(1024), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column(
            "duplicate_article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "duplicate_suggestion_id",
            sa.Integer(),
            sa.ForeignKey("article_suggestions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(_TABLE, sa.Column("duplicate_similarity", sa.Float(), nullable=True))
    op.create_index("ix_article_suggestions_duplicate_article_id", _TABLE, ["duplicate_article_id"])
    op.create_index(
        "ix_article_suggestions_duplicate_suggestion_id", _TABLE, ["duplicate_suggestion_id"]
    )
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "num_nonnulls(duplicate_article_id, duplicate_suggestion_id) <= 1",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_index("ix_article_suggestions_duplicate_suggestion_id", table_name=_TABLE)
    op.drop_index("ix_article_suggestions_duplicate_article_id", table_name=_TABLE)
    op.drop_column(_TABLE, "duplicate_similarity")
    op.drop_column(_TABLE, "duplicate_suggestion_id")
    op.drop_column(_TABLE, "duplicate_article_id")
    op.drop_column(_TABLE, "embedding")
