"""pgvector is enabled in the test DB and migration 0043 creates it (#621)."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

from pgvector.sqlalchemy import Vector
from sqlalchemy import text

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0043_pgvector.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0043", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vector_extension_is_installed(test_db):
    version = test_db.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    assert version is not None


def test_vector_insert_and_cosine_distance_query(test_db):
    # A TEMP table: no model, nothing for the per-test TRUNCATE to know about.
    test_db.execute(text("CREATE TEMP TABLE vec_smoke (id int PRIMARY KEY, v vector(3))"))
    test_db.execute(
        text(
            "INSERT INTO vec_smoke (id, v) VALUES "
            "(1, '[1,0,0]'), (2, '[0,1,0]'), (3, '[0.9,0.1,0]')"
        )
    )
    rows = test_db.execute(
        text("SELECT id, v <=> '[1,0,0]' AS dist FROM vec_smoke ORDER BY v <=> '[1,0,0]'")
    ).all()
    test_db.rollback()

    assert [r.id for r in rows] == [1, 3, 2]
    assert rows[0].dist == 0
    assert abs(rows[2].dist - 1.0) < 1e-6


def test_sqlalchemy_vector_type_compiles():
    assert Vector(1024).get_col_spec() == "VECTOR(1024)"


def test_migration_0043_creates_and_drops_the_extension():
    module = _load_migration()
    assert module.revision == "0043_pgvector"
    assert module.down_revision == "0042_article_audio_qa_es"

    with patch.object(module, "op") as op:
        module.upgrade()
    op.execute.assert_called_once_with("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")

    with patch.object(module, "op") as op:
        module.downgrade()
    op.execute.assert_called_once_with("DROP EXTENSION IF EXISTS vector")
