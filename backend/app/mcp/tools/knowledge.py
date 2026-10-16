"""MCP read tools over articles and the knowledge layer (#640).

``search_knowledge``, ``get_article``, ``find_related`` and ``check_claim``.
All need the ``read`` scope (``app.mcp.server`` registers them).

**Visibility.** The knowledge tools use the retrieval service's ``internal``
visibility (``app/services/knowledge/retrieval.py``): article chunks of
APPROVED articles, **scheduled ones included** (``published_at`` in the future
or unset) and **any taxonomy, taxonomy 5 included**, plus the verified-claims
KB. Unapproved drafts are never searched or related (the retrieval service has
no visibility that includes them). ``get_article`` is a direct lookup by id or
slug and returns an article of **any** status, drafts included, with its
``status`` / ``scheduled`` flags: pipeline diagnosis is mostly about articles
that are not approved yet.

**Embeddings.** Searches run in ``hybrid`` mode, which degrades to full-text
search without a ``VOYAGE_API_KEY`` (or when the query embedding fails) and
says so in ``degraded``; they never fail for that reason. No tool asks for a
rerank. ``find_related`` uses stored vectors only.

**Connection pool.** Each tool opens its own ``SessionLocal()``. The search
service embeds the query *before* its first statement (a session checks out a
connection only on its first statement), so no pooled connection is held
across the Voyage call; nothing else here does external I/O.

**Bounds.** Text fields are clipped per tool and every result goes through
:func:`app.mcp.tools.bounds.bound_result` (about 20k characters).

Errors an agent can act on (not found, bad input) raise ``ToolError`` with a
short message that never quotes the caller's text.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from sqlalchemy import or_, select

from app.core import database
from app.mcp.tools.bounds import bound_result, clip
from app.models.agent_task import AgentTask
from app.models.article import Article
from app.models.category import Category
from app.models.enums import (
    AgentTaskSubject,
    FactCheckSubject,
    FactCheckVerdict,
    KnowledgeSourceType,
)
from app.models.fact_check import FactCheckClaim
from app.models.verified_claim import VerifiedClaim
from app.services.fact_check import _normalize as normalize_claim_key
from app.services.knowledge import related as related_service
from app.services.knowledge import retrieval

LanguageCode = Literal["en", "es"]
SourceTypeName = Literal["ARTICLE", "ARTICLE_CLAIM", "VERIFIED_CLAIM"]
ArticlePart = Literal["content", "claims", "pipeline"]

MAX_QUERY_CHARS = 1000
MAX_CLAIM_CHARS = 2000
SNIPPET_CHARS = 600
CHUNKS_PER_GROUP = 3
# get_article(include=["content"]): the body is cut to this many characters.
CONTENT_CHARS = 12_000
CLAIMS_LISTED = 40
CLAIM_TEXT_CHARS = 300
TASK_ERROR_CHARS = 300

_DIGITS = re.compile(r"^\d+$")
# Claim verdicts that need attention come first in get_article's claim list.
_CLAIM_ORDER = {
    FactCheckVerdict.INACCURATE.value: 0,
    FactCheckVerdict.DISPUTED.value: 1,
    FactCheckVerdict.NEEDS_DEEP_RESEARCH.value: 2,
    FactCheckVerdict.PENDING.value: 3,
    FactCheckVerdict.UNVERIFIABLE.value: 4,
    FactCheckVerdict.CORRECTED.value: 5,
    FactCheckVerdict.VERIFIED.value: 6,
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _scheduled(published_at: datetime | None) -> bool:
    """True when the article is not live yet (no date, or a future one)."""
    return published_at is None or published_at > datetime.now(UTC)


def _search(db, query: str, **kwargs) -> retrieval.SearchResult:
    """``retrieval.search`` in hybrid mode, internal visibility, no rerank.

    Must be the session's first statement (see the module docstring).
    """
    return retrieval.search(db, query, mode="hybrid", visibility="internal", **kwargs)


# --- search_knowledge ---------------------------------------------------------

SEARCH_KNOWLEDGE_DESCRIPTION = """\
Search everything Panama In Context has written (blog articles in English and Spanish, \
their fact-checked claims, and the verified-claims knowledge base) and get the best \
matches grouped by article.

Use it to find which PIC articles cover a topic, person, place or event, or to collect \
passages before writing or checking something about Panama's history. Scheduled \
(approved, not yet published) articles are included; unapproved drafts are not. \
Do not use it to verify a single factual sentence (use check_claim) or to read a \
whole article (use get_article with the id from here).

Arguments: query (natural language, EN or ES, max 1000 chars); language ("en" / "es", \
optional: limits which chunks match); source_types (optional subset of ARTICLE, \
ARTICLE_CLAIM, VERIFIED_CLAIM); k (number of groups, 1-20, default 5).

Returns {"query_chars", "degraded": [str], "results": [{"rank", "article_id" (null for \
a knowledge-base claim), "title", "title_es", "slug", "slug_es", "status", \
"published_at", "scheduled", "chunks": [{"source_type", "language", "heading_path", \
"snippet", "verdict"?}]}], "truncated"}. "degraded" is non-empty when semantic search \
was unavailable and only keyword search ran.

Example: search_knowledge(query="construction of the Panama Railroad", k=3) -> \
{"results": [{"rank": 1, "article_id": 42, "title": "The Panama Railroad", \
"slug": "the-panama-railroad", "scheduled": false, "chunks": [{"source_type": "ARTICLE", \
"language": "en", "heading_path": "Building the line", "snippet": "Work began in 1850..."}]}], \
"degraded": [], "truncated": false}"""


def search_knowledge(
    query: Annotated[str, Field(min_length=1, max_length=MAX_QUERY_CHARS)],
    language: LanguageCode | None = None,
    source_types: Annotated[list[SourceTypeName] | None, Field(max_length=3)] = None,
    k: Annotated[int, Field(ge=1, le=20)] = 5,
) -> dict[str, Any]:
    db = database.SessionLocal()
    try:
        result = _search(
            db,
            query,
            k=k,
            languages=[language] if language else None,
            source_types=sorted(set(source_types)) if source_types else None,
        )
        article_ids = [hit.article_id for hit in result.results if hit.article_id is not None]
        meta = {}
        if article_ids:
            rows = db.execute(
                select(Article.id, Article.status, Article.published_at).where(
                    Article.id.in_(article_ids)
                )
            )
            meta = {row.id: row for row in rows}
    finally:
        db.close()

    groups = []
    for hit in result.results:
        art = meta.get(hit.article_id)
        chunks = []
        for chunk in hit.chunks[:CHUNKS_PER_GROUP]:
            entry: dict[str, Any] = {
                "source_type": chunk.source_type,
                "language": chunk.language,
                "heading_path": chunk.heading_path,
                "snippet": clip(chunk.content, SNIPPET_CHARS),
            }
            verdict = (chunk.meta or {}).get("verdict")
            if verdict:
                entry["verdict"] = verdict
            chunks.append(entry)
        groups.append(
            {
                "rank": hit.rank,
                "article_id": hit.article_id,
                "title": hit.title,
                "title_es": hit.title_es,
                "slug": hit.slug,
                "slug_es": hit.slug_es,
                "status": art.status if art else None,
                "published_at": _iso(art.published_at) if art else None,
                "scheduled": _scheduled(art.published_at) if art else None,
                "chunks": chunks,
            }
        )
    return bound_result(
        {"query_chars": len(query), "degraded": list(result.degraded), "results": groups}
    )


# --- get_article ----------------------------------------------------------------

GET_ARTICLE_DESCRIPTION = """\
Read one Panama In Context blog article: metadata, and optionally its body, its \
fact-check claims and its pipeline (agent task) rows.

Use it after search_knowledge / find_related to read an article, or to inspect an \
article a pipeline goal is stuck on. Any status is returned, drafts included \
("status", "scheduled"). Do not use it to search (use search_knowledge).

Arguments: id_or_slug (numeric id, or the English or Spanish slug); language ("en" \
default, or "es": which title/excerpt/content to return); include (any of "content", \
"claims", "pipeline"; default none). The body is markdown cut to 12,000 characters \
("content_truncated", "content_chars" give the full size).

Returns {"id", "language", "title", "slug", "excerpt", "title_en", "title_es", \
"slug_en", "slug_es", "status", "published_at", "scheduled", "category": {"id", "name", \
"taxonomy_id"}, "research_id", "series": {"parent_id", "order", "child_ids"}, "tags": \
[str], "has_spanish", "has_audio_es", "grammar_checked_en_at", "grammar_checked_es_at", \
"content"?, "content_chars"?, "content_truncated"?, "claims"?: {"counts": {verdict: n}, \
"items": [{"id", "verdict", "claim", "correction", "checked_via"}]}, "pipeline"?: \
[{"task_id", "goal_key", "kind", "status", "attempts", "waiting_for", "error"}], \
"truncated"}. Claims list problem verdicts first (INACCURATE, DISPUTED, \
NEEDS_DEEP_RESEARCH, ...), at most 40.

Example: get_article(id_or_slug="the-panama-railroad", include=["claims"]) -> \
{"id": 42, "title": "The Panama Railroad", "status": "APPROVED", "scheduled": false, \
"claims": {"counts": {"VERIFIED": 18, "CORRECTED": 2}, "items": [...]}, ...}"""


def _find_article(db, id_or_slug: int | str) -> Article | None:
    key = str(id_or_slug).strip()
    if _DIGITS.match(key):
        found = db.get(Article, int(key))
        if found is not None:
            return found
    return db.query(Article).filter(or_(Article.slug == key, Article.slug_es == key)).first()


def _claims_summary(db, article_id: int) -> dict[str, Any]:
    claims = (
        db.query(FactCheckClaim)
        .filter(
            FactCheckClaim.subject_type == FactCheckSubject.ARTICLE.value,
            FactCheckClaim.subject_id == article_id,
        )
        .all()
    )
    counts: dict[str, int] = {}
    for claim in claims:
        counts[claim.verdict] = counts.get(claim.verdict, 0) + 1
    claims.sort(key=lambda c: (_CLAIM_ORDER.get(c.verdict, 9), c.id))
    items = [
        {
            "id": claim.id,
            "verdict": claim.verdict,
            "claim": clip(claim.claim_text, CLAIM_TEXT_CHARS),
            "correction": clip(claim.correction, CLAIM_TEXT_CHARS),
            "checked_via": claim.checked_via,
        }
        for claim in claims[:CLAIMS_LISTED]
    ]
    return {"total": len(claims), "counts": counts, "items": items}


def _pipeline_rows(db, article_id: int) -> list[dict[str, Any]]:
    tasks = (
        db.query(AgentTask)
        .filter(
            AgentTask.subject_type == AgentTaskSubject.ARTICLE.value,
            AgentTask.subject_id == article_id,
        )
        .order_by(AgentTask.goal_key, AgentTask.position, AgentTask.id)
        .all()
    )
    return [
        {
            "task_id": task.id,
            "goal_key": task.goal_key,
            "kind": task.kind,
            "status": task.status,
            "attempts": task.attempts,
            "waiting_for": task.waiting_for,
            "error": clip(task.error, TASK_ERROR_CHARS),
        }
        for task in tasks
    ]


def get_article(
    id_or_slug: Annotated[
        int | Annotated[str, Field(min_length=1, max_length=255)],
        Field(description="Numeric id or EN/ES slug"),
    ],
    language: LanguageCode = "en",
    include: Annotated[list[ArticlePart] | None, Field(max_length=3)] = None,
) -> dict[str, Any]:
    parts = set(include or ())
    db = database.SessionLocal()
    try:
        article = _find_article(db, id_or_slug)
        if article is None:
            raise ToolError("not_found: no article with that id or slug.")
        es = language == "es"
        category = article.category
        child_ids = sorted(child.id for child in article.series_children)
        out: dict[str, Any] = {
            "id": article.id,
            "language": language,
            "title": (article.title_es if es else article.title),
            "slug": (article.slug_es if es else article.slug),
            "excerpt": clip(article.excerpt_es if es else article.excerpt, 1000),
            "title_en": article.title,
            "title_es": article.title_es,
            "slug_en": article.slug,
            "slug_es": article.slug_es,
            "status": article.status,
            "published_at": _iso(article.published_at),
            "scheduled": _scheduled(article.published_at),
            "category": {
                "id": category.id,
                "name": category.name,
                "taxonomy_id": category.taxonomy_id,
            }
            if category
            else None,
            "research_id": article.research_id,
            "series": {
                "parent_id": article.series_parent_id,
                "order": article.series_order,
                "child_ids": child_ids,
            },
            "tags": sorted(tag.name for tag in article.tags),
            "has_spanish": bool(article.content_es),
            "has_audio_es": bool(article.audio_url_es),
            "grammar_checked_en_at": _iso(article.grammar_checked_en_at),
            "grammar_checked_es_at": _iso(article.grammar_checked_es_at),
        }
        if "content" in parts:
            body = (article.content_es if es else article.content) or ""
            out["content_chars"] = len(body)
            out["content_truncated"] = len(body) > CONTENT_CHARS
            out["content"] = clip(body, CONTENT_CHARS)
        if "claims" in parts:
            out["claims"] = _claims_summary(db, article.id)
        if "pipeline" in parts:
            out["pipeline"] = _pipeline_rows(db, article.id)
    finally:
        db.close()
    return bound_result(out)


# --- find_related -----------------------------------------------------------------

FIND_RELATED_DESCRIPTION = """\
List the Panama In Context articles most similar to a given article, by meaning \
(stored embeddings; no keyword match needed).

Use it to suggest internal links, to see what PIC already covers near a topic, or to \
check a new article does not duplicate an existing one. The article itself and its own \
series (parts of the same research) are excluded. Only approved articles take part, \
scheduled ones included. Do not use it for free-text questions (use search_knowledge).

Arguments: article_id (int, an approved article); k (1-10, default 4).

Returns {"article_id", "results": [{"article_id", "title", "title_es", "slug", \
"slug_es", "category", "status", "published_at", "scheduled", "similarity" (0-1)}], \
"truncated"}. An empty "results" means the article is not indexed yet.

Example: find_related(article_id=42, k=2) -> {"article_id": 42, "results": \
[{"article_id": 57, "title": "The Gold Rush Crossing", "similarity": 0.83, ...}, \
{"article_id": 12, "title": "Colón in the 1850s", "similarity": 0.79, ...}], \
"truncated": false}"""


def find_related(
    article_id: int,
    k: Annotated[int, Field(ge=1, le=10)] = 4,
) -> dict[str, Any]:
    db = database.SessionLocal()
    try:
        hits = related_service.find_related(db, article_id, k=k, visibility="internal")
        if hits is None:
            raise ToolError(
                f"not_found: article {article_id} does not exist or is not approved "
                "(only approved articles have related articles)."
            )
        ids = [hit.article_id for hit in hits]
        rows = {}
        if ids:
            query = (
                select(
                    Article.id,
                    Article.title,
                    Article.title_es,
                    Article.slug,
                    Article.slug_es,
                    Article.status,
                    Article.published_at,
                    Category.name.label("category"),
                )
                .join(Category, Category.id == Article.category_id)
                .where(Article.id.in_(ids))
            )
            rows = {row.id: row for row in db.execute(query)}
    finally:
        db.close()

    results = []
    for hit in hits:
        row = rows.get(hit.article_id)
        if row is None:
            continue
        results.append(
            {
                "article_id": hit.article_id,
                "title": row.title,
                "title_es": row.title_es,
                "slug": row.slug,
                "slug_es": row.slug_es,
                "category": row.category,
                "status": row.status,
                "published_at": _iso(row.published_at),
                "scheduled": _scheduled(row.published_at),
                "similarity": round(hit.similarity, 4),
            }
        )
    return bound_result({"article_id": article_id, "results": results})


# --- check_claim ------------------------------------------------------------------

CHECK_CLAIM_DESCRIPTION = """\
Has Panama In Context already established this? Look a factual statement up in PIC's \
fact-check memory: the verified-claims knowledge base (claims resolved by deep research \
or, historically, web search, with a verdict, a correction or note, and a citation) and the verified or \
corrected claims of published and scheduled articles.

Use it before asserting or researching a single fact about Panama's history, or to \
see how PIC resolved a disputed point. Pass ONE claim per call, as a plain sentence. \
A match is evidence only when its verdict is VERIFIED or CORRECTED (then the \
"correction" is the established fact); INACCURATE means PIC found the statement false \
(its "correction" says what is true); DISPUTED means sources disagree. No match means PIC \
has not checked it, not that it is false. Do not use it for topic searches (use \
search_knowledge).

Arguments: text (the claim, max 2000 chars); k (matches, 1-10, default 5).

Returns {"exact_match": {...} | null (same claim text, normalized, in the knowledge \
base), "matches": [{"source": "knowledge_base" | "article_claim", "id", "claim", \
"verdict", "correction", "citation", "similarity" (0-1, null when only keyword search \
ran), "article"?: {"id", "title", "slug"}}], "degraded": [str], "truncated"}.

Example: check_claim(text="The Panama Canal opened on 15 August 1914.") -> \
{"exact_match": null, "matches": [{"source": "knowledge_base", "id": 311, \
"claim": "The Panama Canal opened to traffic on August 15, 1914.", "verdict": \
"VERIFIED", "correction": null, "citation": "https://...", "similarity": 0.94}], \
"degraded": [], "truncated": false}"""

_CLAIM_SOURCES = [
    KnowledgeSourceType.VERIFIED_CLAIM.value,
    KnowledgeSourceType.ARTICLE_CLAIM.value,
]


def _kb_entry(row: VerifiedClaim, similarity: float | None) -> dict[str, Any]:
    return {
        "source": "knowledge_base",
        "id": row.id,
        "claim": clip(row.claim_text, CLAIM_TEXT_CHARS * 2),
        "verdict": row.verdict,
        "correction": clip(row.correction, CLAIM_TEXT_CHARS * 2),
        "citation": clip(row.citation, CLAIM_TEXT_CHARS),
        "source_method": row.source_method,
        "similarity": similarity,
    }


def check_claim(
    text: Annotated[str, Field(min_length=3, max_length=MAX_CLAIM_CHARS)],
    k: Annotated[int, Field(ge=1, le=10)] = 5,
) -> dict[str, Any]:
    db = database.SessionLocal()
    try:
        # First statement of the session: the embedding call happens before it.
        result = _search(db, text, k=k, source_types=_CLAIM_SOURCES)
        # The ranked chunk list, not the article groups: several claims of one
        # article are separate matches here.
        hits = []
        seen: set[tuple[str, int]] = set()
        for chunk in result.chunks:
            if (chunk.source_type, chunk.source_id) in seen:
                continue
            seen.add((chunk.source_type, chunk.source_id))
            similarity = (
                round(1.0 - chunk.vector_distance, 4) if chunk.vector_distance is not None else None
            )
            hits.append((chunk.source_type, chunk.source_id, similarity))
            if len(hits) >= k:
                break

        kb_ids = [sid for st, sid, _ in hits if st == KnowledgeSourceType.VERIFIED_CLAIM.value]
        claim_ids = [sid for st, sid, _ in hits if st == KnowledgeSourceType.ARTICLE_CLAIM.value]
        kb_rows = (
            {row.id: row for row in db.query(VerifiedClaim).filter(VerifiedClaim.id.in_(kb_ids))}
            if kb_ids
            else {}
        )
        claim_rows = (
            {
                row.id: row
                for row in db.query(FactCheckClaim).filter(FactCheckClaim.id.in_(claim_ids))
            }
            if claim_ids
            else {}
        )
        article_ids = {row.subject_id for row in claim_rows.values()}
        articles = (
            {
                row.id: row
                for row in db.execute(
                    select(Article.id, Article.title, Article.slug).where(
                        Article.id.in_(article_ids)
                    )
                )
            }
            if article_ids
            else {}
        )
        key = normalize_claim_key(text)
        exact = (
            db.query(VerifiedClaim).filter(VerifiedClaim.claim_key == key).first() if key else None
        )

        matches = []
        for source_type, source_id, similarity in hits:
            if source_type == KnowledgeSourceType.VERIFIED_CLAIM.value:
                row = kb_rows.get(source_id)
                if row is not None:
                    matches.append(_kb_entry(row, similarity))
                continue
            claim = claim_rows.get(source_id)
            if claim is None:
                continue
            art = articles.get(claim.subject_id)
            matches.append(
                {
                    "source": "article_claim",
                    "id": claim.id,
                    "claim": clip(claim.claim_text, CLAIM_TEXT_CHARS * 2),
                    "verdict": claim.verdict,
                    "correction": clip(claim.correction, CLAIM_TEXT_CHARS * 2),
                    "citation": clip(claim.citation, CLAIM_TEXT_CHARS),
                    "checked_via": claim.checked_via,
                    "similarity": similarity,
                    "article": {"id": art.id, "title": art.title, "slug": art.slug}
                    if art
                    else None,
                }
            )
        exact_entry = _kb_entry(exact, None) if exact is not None else None
    finally:
        db.close()
    return bound_result(
        {
            "exact_match": exact_entry,
            "matches": matches,
            "degraded": list(result.degraded),
        }
    )


__all__ = [
    "CHECK_CLAIM_DESCRIPTION",
    "FIND_RELATED_DESCRIPTION",
    "GET_ARTICLE_DESCRIPTION",
    "SEARCH_KNOWLEDGE_DESCRIPTION",
    "check_claim",
    "find_related",
    "get_article",
    "search_knowledge",
]
