"""knowledge_chunks table (#623): migration 0044 vs the models, search columns."""

import importlib.util
import re
from pathlib import Path

import pytest
from app.models import search_sql
from app.models.enums import KnowledgeSourceType, Language
from app.models.knowledge_chunk import KnowledgeChunk
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError


def _load_migration():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0044_knowledge_chunks.py"
    spec = importlib.util.spec_from_file_location("migration_0044", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chunk(test_db, article_id=None, **overrides) -> KnowledgeChunk:
    defaults = {
        "source_type": KnowledgeSourceType.ARTICLE.value,
        "source_id": article_id or 1,
        "article_id": article_id,
        "language": "en",
        "chunk_index": 0,
        "heading_path": None,
        "content": "x",
        "content_hash": "0" * 64,
        "token_count": 1,
    }
    defaults.update(overrides)
    chunk = KnowledgeChunk(**defaults)
    test_db.add(chunk)
    test_db.commit()
    return chunk


class TestMigration:
    def test_migration_sql_matches_models(self):
        module = _load_migration()

        def norm(s: str) -> str:
            return " ".join(s.split())

        assert norm(module._SEARCH_VECTOR) == norm(search_sql.KNOWLEDGE_CHUNK_SEARCH_VECTOR_SQL)
        assert set(module._SOURCE_TYPES) == set(KnowledgeSourceType.values())
        assert set(module._LANGUAGES) == set(Language.values())
        assert module.revision == "0044_knowledge_chunks"
        assert module.down_revision == "0043_pgvector"

    def test_migration_is_additive(self):
        source = Path(_load_migration().__file__).read_text()
        upgrade = source.split("def upgrade")[1].split("def downgrade")[0]
        for forbidden in ("INSERT", "UPDATE ", "DELETE", "DROP", "ALTER"):
            assert forbidden not in upgrade.upper().replace("ONDELETE", "")


class TestTable:
    def test_search_columns_are_not_orm_attributes(self):
        mapped = set(KnowledgeChunk.__mapper__.columns.keys())
        assert "embedding" not in mapped
        assert "search_vector" not in mapped
        assert {"embedding", "search_vector"} <= set(KnowledgeChunk.__table__.c.keys())

    def test_orm_statements_never_name_search_columns(self, test_db):
        statements: list[str] = []

        def _capture(conn, cursor, statement, *args):
            statements.append(statement)

        bind = test_db.get_bind()
        event.listen(bind, "before_cursor_execute", _capture)
        try:
            chunk = _chunk(test_db, content="Guard")
            test_db.refresh(chunk)
            test_db.execute(select(KnowledgeChunk)).all()
        finally:
            event.remove(bind, "before_cursor_execute", _capture)
        assert statements
        assert not any(re.search(r"search_vector|\bembedding\b", s) for s in statements)

    def test_search_vector_uses_row_language(self, test_db):
        _chunk(test_db, language="en", source_id=1, content="The railroads were running")
        _chunk(test_db, language="es", source_id=2, content="Los ferrocarriles corrían en Panamá")
        sv = KnowledgeChunk.__table__.c.search_vector

        def ids_matching(config: str, query: str) -> set[int]:
            stmt = select(KnowledgeChunk.__table__.c.source_id).where(
                sv.op("@@")(
                    text(
                        f"websearch_to_tsquery('{config}'::regconfig, public.pic_unaccent(:q))"
                    ).bindparams(q=query)
                )
            )
            return set(test_db.execute(stmt).scalars())

        # English stemming: "railroad" matches "railroads" on the EN row.
        assert ids_matching("english", "railroad") == {1}
        # Spanish stemming + unaccent: "ferrocarril panama" matches the ES row.
        assert ids_matching("spanish", "ferrocarril panama") == {2}

    def test_embedding_round_trip_and_cosine(self, test_db):
        chunk = _chunk(test_db)
        vector = [0.0] * 1024
        vector[0] = 1.0
        literal = "[" + ",".join(str(v) for v in vector) + "]"
        test_db.execute(
            text("UPDATE knowledge_chunks SET embedding = CAST(:v AS vector) WHERE id = :id"),
            {"v": literal, "id": chunk.id},
        )
        distance = test_db.execute(
            text("SELECT embedding <=> CAST(:v AS vector) FROM knowledge_chunks WHERE id = :id"),
            {"v": literal, "id": chunk.id},
        ).scalar_one()
        assert distance == pytest.approx(0.0)

    def test_unique_source_chunk(self, test_db):
        _chunk(test_db, source_id=7, chunk_index=0)
        with pytest.raises(IntegrityError):
            _chunk(test_db, source_id=7, chunk_index=0)
        test_db.rollback()
        # Same position in the other language is a different chunk.
        _chunk(test_db, source_id=7, chunk_index=0, language="es")

    def test_article_delete_cascades(self, test_db, article_factory):
        article = article_factory()
        _chunk(test_db, article_id=article.id)
        _chunk(
            test_db,
            article_id=article.id,
            source_type=KnowledgeSourceType.ARTICLE_CLAIM.value,
            source_id=99,
        )
        _chunk(test_db, source_type=KnowledgeSourceType.VERIFIED_CLAIM.value, source_id=5)
        test_db.execute(text("DELETE FROM articles WHERE id = :id"), {"id": article.id})
        test_db.commit()
        remaining = test_db.execute(select(KnowledgeChunk.source_type)).scalars().all()
        assert remaining == ["VERIFIED_CLAIM"]

    def test_indexes_exist(self, test_db):
        rows = test_db.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'knowledge_chunks'")
        ).all()
        defs = {name: definition for name, definition in rows}
        assert "USING hnsw (embedding vector_cosine_ops)" in defs["ix_knowledge_chunks_embedding"]
        assert "USING gin (search_vector)" in defs["ix_knowledge_chunks_search_vector"]
        assert "uq_knowledge_chunks_source_chunk" in defs
