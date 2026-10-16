"""Production refusal + eval-scoped chunk tables for the RAG eval (#627).

**Never against production.** :func:`assert_not_production` refuses when
``settings.environment == "production"`` (the ``Settings`` default, so a shell
with no ``ENVIRONMENT`` is refused too; the dev container sets
``development``) or when the database host is a DigitalOcean managed-Postgres
host. The dev backend can be pointed at prod (``source .env.prod-readonly``,
see CLAUDE.md), so the URL check matters as much as the environment one.

**Eval tables.** One table per embedding model,
``eval_knowledge_chunks_<model_slug>``, created at runtime with
``CREATE TABLE ... (LIKE knowledge_chunks INCLUDING GENERATED INCLUDING
CONSTRAINTS INCLUDING INDEXES)``: the same columns, the generated bilingual
``search_vector``, the CHECKs and the HNSW / GIN indexes, but **no foreign key
and no defaults** (so it never depends on ``knowledge_chunks``' id sequence).
It is not an Alembic migration and not in ``Base.metadata``. Rows are copied
from the local ``knowledge_chunks`` **with the same ids** (so chunk ids line up
across models) and re-embedded with the eval model; a row whose source
``embedding_model`` already is that model reuses its vector (``reuse=True``).
A re-run only embeds rows that are new or whose ``content_hash`` changed, and
deletes rows gone from the source. ``knowledge_chunks`` itself is only read.

The embedding text is rebuilt exactly as the indexers build it: article
chunks are ``build_embedding_text(title, heading_path, content)`` with the
article's title in the chunk's language (EN title as the ES fallback), claim
chunks are their ``content``. Each rebuilt text is checked against the stored
``content_hash``; mismatches are counted (and still embedded) so a drifted
chunker is visible in the output instead of silently skewing the comparison.
An article chunk with a situating sentence (``meta["context"]``, #697) is
embedded with it (:func:`~app.services.knowledge.chunker.contextual_text`),
as the indexer does; the hash is over the text without it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.models.article import Article
from app.models.enums import KnowledgeSourceType, Language
from app.models.knowledge_chunk import KnowledgeChunk
from app.services import embeddings
from app.services.knowledge.chunker import (
    CONTEXT_KEY,
    build_embedding_text,
    content_hash,
    contextual_text,
    embedding_prefix,
    estimate_tokens,
)
from app.services.knowledge.retrieval import chunk_table_like
from sqlalchemy import Table, delete, insert, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

FEATURE = "rag_eval_index"
SOURCE_TABLE = KnowledgeChunk.__table__
TABLE_PREFIX = "eval_knowledge_chunks_"
# Host substrings of the production managed Postgres (CLAUDE.md "Database").
PROD_HOST_MARKERS = ("ondigitalocean.com",)
# Embedding batches are flushed to the DB in groups this size.
EMBED_GROUP = 500

# Every stored column except the generated ``search_vector``.
_COPY_COLUMNS = [c.name for c in SOURCE_TABLE.columns if c.name != "search_vector"]


class ProductionRefusedError(RuntimeError):
    """The eval was pointed at production."""


def assert_not_production(environment: str | None, database_url: str | None) -> None:
    """Raise :class:`ProductionRefusedError` for a production environment or DB host."""
    env = (environment or "").strip().lower()
    if env == "production":
        raise ProductionRefusedError(
            "Refusing to run the RAG eval: settings.environment is 'production' "
            "(run it in the dev backend container, which sets ENVIRONMENT=development)"
        )
    host = ""
    if database_url:
        try:
            host = (make_url(database_url).host or "").lower()
        except Exception:  # noqa: BLE001 — an unparseable URL is checked as a string
            host = database_url.lower()
    if any(marker in host for marker in PROD_HOST_MARKERS):
        raise ProductionRefusedError(
            f"Refusing to run the RAG eval: the database host {host!r} is the production "
            "managed Postgres. Unset DATABASE_URL (or stop sourcing .env.prod-readonly)."
        )


def assert_session_not_production(db: Session, environment: str | None) -> None:
    """:func:`assert_not_production` against the URL ``db`` is actually bound to."""
    url = db.get_bind().engine.url.render_as_string(hide_password=True)
    assert_not_production(environment, url)


def model_slug(model: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    if not slug:
        raise ValueError(f"cannot derive a table name from model {model!r}")
    return slug


def eval_table_name(model: str) -> str:
    return TABLE_PREFIX + model_slug(model)


def eval_table(model: str) -> Table:
    """The Core table for ``model``'s eval copy (pass it to ``retrieval.search``)."""
    return chunk_table_like(eval_table_name(model))


def ensure_eval_table(db: Session, model: str) -> Table:
    """Create ``model``'s eval table if it does not exist; return its Core table."""
    name = eval_table_name(model)  # [a-z0-9_] only, so safe to interpolate
    db.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {name} (LIKE {SOURCE_TABLE.name} "
            "INCLUDING GENERATED INCLUDING CONSTRAINTS INCLUDING INDEXES)"
        )
    )
    db.commit()
    return eval_table(model)


def drop_eval_table(db: Session, model: str) -> None:
    db.execute(text(f"DROP TABLE IF EXISTS {eval_table_name(model)}"))
    db.commit()


@dataclass
class SourceChunk:
    id: int
    content_hash: str
    embedding_model: str | None
    has_embedding: bool
    embedding_text: str
    hash_matches: bool


@dataclass
class SyncPlan:
    model: str
    table: str
    source_rows: int = 0
    keep: int = 0
    stale_ids: list[int] = field(default_factory=list)
    copy_ids: list[int] = field(default_factory=list)  # reuse the source vector
    embed: list[SourceChunk] = field(default_factory=list)
    hash_mismatches: int = 0
    avg_chunk_tokens: float = 0.0

    @property
    def embed_tokens(self) -> int:
        return sum(estimate_tokens(c.embedding_text) for c in self.embed)

    @property
    def estimated_cost(self) -> float:
        return embeddings.estimate_cost(self.model, self.embed_tokens)

    def summary(self) -> dict:
        return {
            "model": self.model,
            "table": self.table,
            "source_rows": self.source_rows,
            "kept": self.keep,
            "deleted": len(self.stale_ids),
            "copied_vectors": len(self.copy_ids),
            "to_embed": len(self.embed),
            "embed_tokens_est": self.embed_tokens,
            "embed_cost_usd_est": round(self.estimated_cost, 6),
            "hash_mismatches": self.hash_mismatches,
        }


def _source_chunks(db: Session) -> list[SourceChunk]:
    c = SOURCE_TABLE.c
    articles = Article.__table__
    stmt = (
        select(
            c.id,
            c.source_type,
            c.language,
            c.heading_path,
            c.content,
            c.content_hash,
            c.embedding_model,
            c.embedding.is_not(None).label("has_embedding"),
            c.meta,
            articles.c.title,
            articles.c.title_es,
        )
        .select_from(SOURCE_TABLE.outerjoin(articles, articles.c.id == c.article_id))
        .order_by(c.id)
    )
    out = []
    for row in db.execute(stmt):
        if row.source_type == KnowledgeSourceType.ARTICLE.value:
            title = (row.title_es or row.title) if row.language == Language.ES.value else row.title
            title = (title or "").strip()
            hashed_text = build_embedding_text(title, row.heading_path, row.content)
            embedding_text = contextual_text(
                embedding_prefix(title, row.heading_path),
                (row.meta or {}).get(CONTEXT_KEY),
                row.content,
            )
        else:
            hashed_text = embedding_text = row.content
        out.append(
            SourceChunk(
                id=row.id,
                content_hash=row.content_hash,
                embedding_model=row.embedding_model,
                has_embedding=bool(row.has_embedding),
                embedding_text=embedding_text,
                hash_matches=content_hash(hashed_text) == row.content_hash,
            )
        )
    return out


def eval_table_exists(db: Session, model: str) -> bool:
    return inspect(db.get_bind()).has_table(eval_table_name(model))


def plan_sync(db: Session, model: str, *, reuse: bool = True) -> SyncPlan:
    """Compare ``knowledge_chunks`` with ``model``'s eval table. Read-only."""
    table = eval_table(model)
    plan = SyncPlan(model=model, table=table.name)
    source = _source_chunks(db)
    plan.source_rows = len(source)
    plan.hash_mismatches = sum(not s.hash_matches for s in source)
    if source:
        plan.avg_chunk_tokens = sum(estimate_tokens(s.embedding_text) for s in source) / len(source)
    existing = {}
    if eval_table_exists(db, model):
        existing = {
            row.id: (row.content_hash, row.embedded)
            for row in db.execute(
                select(
                    table.c.id,
                    table.c.content_hash,
                    table.c.embedding.is_not(None).label("embedded"),
                )
            )
        }
    source_ids = {s.id for s in source}
    for chunk in source:
        current = existing.get(chunk.id)
        if current and current[0] == chunk.content_hash and current[1]:
            plan.keep += 1
            continue
        if current:
            plan.stale_ids.append(chunk.id)
        if reuse and chunk.has_embedding and chunk.embedding_model == model:
            plan.copy_ids.append(chunk.id)
        else:
            plan.embed.append(chunk)
    plan.stale_ids += [rid for rid in existing if rid not in source_ids]
    return plan


def apply_sync(db: Session, plan: SyncPlan) -> SyncPlan:
    """Execute a :func:`plan_sync` plan: create the table, delete stale rows, copy, embed."""
    table = ensure_eval_table(db, plan.model)
    cols = ", ".join(_COPY_COLUMNS)
    if plan.stale_ids:
        db.execute(delete(table).where(table.c.id.in_(plan.stale_ids)))
    if plan.copy_ids:
        db.execute(
            text(
                f"INSERT INTO {table.name} ({cols}) SELECT {cols} FROM {SOURCE_TABLE.name} "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": plan.copy_ids},
        )
    # Release the connection before the Voyage calls (CLAUDE.md pool rule).
    db.commit()
    for start in range(0, len(plan.embed), EMBED_GROUP):
        group = plan.embed[start : start + EMBED_GROUP]
        vectors = embeddings.embed_documents(
            [c.embedding_text for c in group], feature=FEATURE, model=plan.model
        )
        by_id = dict(zip((c.id for c in group), vectors, strict=True))
        source_cols = [SOURCE_TABLE.c[name] for name in _COPY_COLUMNS if name != "embedding"]
        rows = db.execute(select(*source_cols).where(SOURCE_TABLE.c.id.in_(list(by_id))))
        values = []
        for row in rows:
            record = dict(row._mapping)
            record["embedding"] = by_id[record["id"]]
            record["embedding_model"] = plan.model
            values.append(record)
        if values:
            db.execute(insert(table), values)
        db.commit()
        logger.info(
            "eval index %s: embedded %d/%d", table.name, start + len(group), len(plan.embed)
        )
    return plan


def corpus_counts(db: Session, table: Table) -> dict[str, int]:
    """Rows per source type and how many carry a vector, for the results header."""
    rows = db.execute(
        select(table.c.source_type, table.c.embedding.is_not(None), text("count(*)"))
        .group_by(table.c.source_type, table.c.embedding.is_not(None))
        .order_by(table.c.source_type)
    )
    counts: dict[str, int] = {"total": 0}
    for source_type, embedded, n in rows:
        counts["total"] += n
        counts[source_type] = counts.get(source_type, 0) + n
        if embedded:
            counts["embedded"] = counts.get("embedded", 0) + n
    return counts
