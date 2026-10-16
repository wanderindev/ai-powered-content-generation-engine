"""knowledge_chunks: the RAG retrieval table (#623, epic #617).

Purely additive: one CREATE TABLE plus its indexes, no data writes. Nothing
reads or writes the table until the indexer (#624) ships.

* ``source_type`` is ``String(20)`` + the named CHECK
  ``ck_knowledge_chunks_source_type`` (``KnowledgeSourceType``), ``language``
  likewise (``ck_knowledge_chunks_language``, ``Language``).
* ``embedding vector(1024)`` (voyage-4) with an HNSW ``vector_cosine_ops``
  index; needs the ``vector`` extension from 0043.
* ``search_vector`` is a STORED generated ``tsvector`` that picks the
  ``english`` / ``spanish`` config by the row's language, over
  ``public.pic_unaccent`` (created by 0041). Must match
  ``app/models/search_sql.py`` (tests/test_knowledge_chunks.py asserts it).
* ``article_id`` cascades on article delete. No visibility column: published
  state is decided at query time.

Indexes are not CONCURRENTLY (cannot run inside Alembic's transaction) and the
table is empty when they are built.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

# revision identifiers, used by Alembic.
revision = "0044_knowledge_chunks"
down_revision = "0043_pgvector"
branch_labels = None
depends_on = None

_SOURCE_TYPES = ("ARTICLE", "ARTICLE_CLAIM", "VERIFIED_CLAIM")
_LANGUAGES = ("en", "es")

# Must match app/models/search_sql.py KNOWLEDGE_CHUNK_SEARCH_VECTOR_SQL.
_SEARCH_VECTOR = (
    "setweight(to_tsvector("
    "CASE WHEN language = 'es' THEN 'spanish'::regconfig ELSE 'english'::regconfig END, "
    "public.pic_unaccent(coalesce(heading_path, ''))), 'A') || "
    "setweight(to_tsvector("
    "CASE WHEN language = 'es' THEN 'spanish'::regconfig ELSE 'english'::regconfig END, "
    "public.pic_unaccent(coalesce(content, ''))), 'B')"
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(50), nullable=True),
        sa.Column("meta", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("search_vector", TSVECTOR(), sa.Computed(_SEARCH_VECTOR, persisted=True)),
        sa.CheckConstraint(
            _in("source_type", _SOURCE_TYPES), name="ck_knowledge_chunks_source_type"
        ),
        sa.CheckConstraint(_in("language", _LANGUAGES), name="ck_knowledge_chunks_language"),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "language",
            "chunk_index",
            name="uq_knowledge_chunks_source_chunk",
        ),
    )
    op.create_index("ix_knowledge_chunks_article_id", "knowledge_chunks", ["article_id"])
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_search_vector "
        "ON knowledge_chunks USING gin (search_vector)"
    )


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
