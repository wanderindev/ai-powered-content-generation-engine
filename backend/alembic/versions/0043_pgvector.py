"""Enable the pgvector extension (#621, epic #617).

First step of the RAG knowledge layer: only the ``vector`` extension, so the
later tickets can declare ``vector(1024)`` columns and HNSW indexes. No table
or column is created here.

``vector`` 0.8.6 is available on the managed DO cluster (PG 17) and is created
``IF NOT EXISTS`` in ``public``, the same way 0041 created ``unaccent`` /
``pg_trgm``. Purely additive: nothing reads it until a later migration adds a
vector column. The test and dev databases run the ``pgvector/pgvector:pg17``
image, which ships the extension (``postgres:17-alpine`` does not).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0043_pgvector"
down_revision = "0042_article_audio_qa_es"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
