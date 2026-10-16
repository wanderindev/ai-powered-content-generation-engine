"""Seed set for the pipeline-memory eval (#632, epic #618): schema, sampling, Sonnet drafts.

Two suites, one file (``backend/eval/rag/memory_seed.json``):

* **contradictions** (B4, :mod:`app.services.consistency_check`): real
  published-article facts — ``ARTICLE_CLAIM`` chunks of **public** articles
  (APPROVED, ``published_at <= now()``, taxonomy != 5), whose text is the
  VERIFIED claim or the CORRECTED correction — turned by Sonnet into
  ``perturbed`` variants (one date / name / figure / place changed, expected
  ``CONTRADICTS``) and faithful ``paraphrase`` variants (expected
  ``CONSISTENT``), split between English and Spanish. Every item keeps its
  source chunk / claim / article id and the original fact text.
* **duplicates** (B3, :mod:`app.services.suggestion_dedup`): ``near_duplicate``
  suggestion topics (title + main topic) paraphrasing a public article
  (expected ``DUPLICATE``), and ``novel`` Panama topics none of the
  APPROVED articles covers (expected ``NOVEL``), split EN / ES.

**Novel topics are checked against the index**: each is embedded the way
#630 embeds a suggestion and compared with every article centroid; the
nearest article, its similarity and ``suspect`` (similarity at or above the
current ``suggestion_dedup_min_similarity``) are written on the item, which
gets ``review: "pending"`` for a human sanity pass. A reviewer sets
``review`` to ``"drop"`` to exclude an item from ``memory run`` (or
``"keep"``). Nothing is auto-dropped: dropping the near ones would remove
exactly the hard negatives the threshold has to separate.

Model output is parsed conservatively: a malformed item, an unknown or
repeated ``ref``, an unknown perturbation kind, or a "perturbed" text equal to
its original is dropped and counted, never repaired. This module reads the
database only; :mod:`scripts.eval_rag.cli` runs it on a read-only session.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.models.article import Article
from app.models.category import Category
from app.models.enums import ContentStatus, KnowledgeSourceType
from app.models.knowledge_chunk import KnowledgeChunk
from app.services import embeddings, llm
from app.services.knowledge import related
from app.services.knowledge.chunker import estimate_tokens
from app.services.knowledge.retrieval import public_article_criteria
from app.services.suggestion_dedup import suggestion_text
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .drafting import (
    EFFORT,
    MAX_TOKENS,
    MODEL,
    SONNET_INPUT_PRICE_PER_MTOK,
    SONNET_OUTPUT_PRICE_PER_MTOK,
)

SEED_VERSION = 1
DEFAULT_SEED_PATH = Path(__file__).resolve().parents[2] / "eval" / "rag" / "memory_seed.json"

FEATURES = {
    "perturbed": "rag_eval_memory_perturb",
    "paraphrase": "rag_eval_memory_paraphrase",
    "near_duplicate": "rag_eval_memory_near_dup",
    "novel": "rag_eval_memory_novel",
}
NOVELTY_FEATURE = "rag_eval_memory_novelty"

DEFAULT_COUNTS = {"perturbed": 20, "paraphrase": 10, "near_duplicate": 15, "novel": 15}
CONTRADICTION_TYPES = ("perturbed", "paraphrase")
DUPLICATE_TYPES = ("near_duplicate", "novel")
EXPECTED = {
    "perturbed": "CONTRADICTS",
    "paraphrase": "CONSISTENT",
    "near_duplicate": "DUPLICATE",
    "novel": "NOVEL",
}
PERTURBATIONS = ("date", "name", "figure", "place")
LANGUAGES = ("en", "es")
LANGUAGE_NAMES = {"en": "English", "es": "Spanish"}
REVIEW_VALUES = (None, "pending", "keep", "drop")

MIN_CLAIM_CHARS = 40
MAX_CLAIM_CHARS = 400
EXCERPT_CHARS = 500
MAX_TITLES_IN_PROMPT = 600

SYSTEM_PROMPT = (
    "You generate evaluation data for the editorial checks of a bilingual "
    "(English/Spanish) website about the history of Panama. Follow the "
    "instructions exactly. Respond with JSON only, no prose and no code fences."
)


class SeedError(ValueError):
    """The seed file is invalid; ``errors`` lists every problem."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class SourceClaim:
    chunk_id: int
    claim_id: int
    article_id: int
    text: str
    title: str | None


@dataclass(frozen=True)
class SourceArticle:
    article_id: int
    title: str
    title_es: str | None
    excerpt: str | None
    excerpt_es: str | None


@dataclass
class SeedBatch:
    """One Sonnet call: its prompt and the sources its ``ref``s point at."""

    type: str
    language: str
    prompt: str
    refs: dict[str, object] = field(default_factory=dict)
    count: int = 0  # items requested (novel batches have no refs)

    @property
    def input_tokens_est(self) -> int:
        return estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(self.prompt)


def _clip(text: str | None, limit: int = EXCERPT_CHARS) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _public_from():
    chunks = KnowledgeChunk.__table__
    return chunks.join(Article, Article.id == chunks.c.article_id).join(
        Category, Category.id == Article.category_id
    )


def load_public_claims(db: Session) -> list[SourceClaim]:
    """Fact chunks (``ARTICLE_CLAIM``) of public articles with a usable length, id order."""
    c = KnowledgeChunk.__table__.c
    stmt = (
        select(c.id, c.source_id, c.article_id, c.content, Article.title)
        .select_from(_public_from())
        .where(
            c.source_type == KnowledgeSourceType.ARTICLE_CLAIM.value,
            c.embedding.is_not(None),
            func.length(c.content).between(MIN_CLAIM_CHARS, MAX_CLAIM_CHARS),
            *public_article_criteria(),
        )
        .order_by(c.id)
    )
    return [
        SourceClaim(r.id, r.source_id, r.article_id, r.content, r.title) for r in db.execute(stmt)
    ]


def load_public_articles(db: Session) -> list[SourceArticle]:
    """Public articles that have an indexed ARTICLE chunk (so they have a centroid)."""
    c = KnowledgeChunk.__table__.c
    indexed = (
        select(c.article_id)
        .where(c.source_type == KnowledgeSourceType.ARTICLE.value, c.embedding.is_not(None))
        .distinct()
    )
    stmt = (
        select(Article.id, Article.title, Article.title_es, Article.excerpt, Article.excerpt_es)
        .join(Category, Category.id == Article.category_id)
        .where(Article.id.in_(indexed), Article.title.is_not(None), *public_article_criteria())
        .order_by(Article.id)
    )
    return [
        SourceArticle(r.id, r.title, r.title_es, r.excerpt, r.excerpt_es) for r in db.execute(stmt)
    ]


def covered_titles(db: Session) -> list[str]:
    """English titles of every APPROVED article (scheduled included: B3 compares with those too)."""
    stmt = (
        select(Article.title)
        .where(Article.status == ContentStatus.APPROVED.value, Article.title.is_not(None))
        .order_by(Article.title)
    )
    return [t for t in db.execute(stmt).scalars() if t]


def split_languages(n: int) -> dict[str, int]:
    """EN gets the odd one: 15 -> {en: 8, es: 7}."""
    return {"en": n - n // 2, "es": n // 2}


def _distinct_articles_first(items: list, key, n: int, rng: random.Random) -> list:
    """Sample ``n`` items, one per article first, repeating articles only if needed."""
    pool = list(items)
    rng.shuffle(pool)
    first, rest, seen = [], [], set()
    for item in pool:
        (rest if key(item) in seen else first).append(item)
        seen.add(key(item))
    return (first + rest)[:n]


def sample(
    claims: list[SourceClaim], articles: list[SourceArticle], counts: dict[str, int], *, seed: int
) -> dict[str, list]:
    """Claims for the two contradiction types (all distinct), articles for near-duplicates."""
    rng = random.Random(seed)
    need = counts["perturbed"] + counts["paraphrase"]
    picked = _distinct_articles_first(claims, lambda c: c.article_id, need, rng)
    near = list(articles)
    rng.shuffle(near)
    return {
        "perturbed": picked[: counts["perturbed"]],
        "paraphrase": picked[counts["perturbed"] : need],
        "near_duplicate": near[: counts["near_duplicate"]],
    }


def _language_note(language: str) -> str:
    if language == "es":
        return " Write every output text in Spanish (translate faithfully whatever you keep)."
    return " Write every output text in English."


def _perturb_prompt(refs: dict[str, SourceClaim], language: str) -> str:
    lines = "\n".join(f"{ref}: {claim.text}" for ref, claim in refs.items())
    return (
        "Below are factual claims from published articles about the history of Panama, "
        "each with a ref. For each claim write ONE altered version that changes exactly one "
        "fact: a date, a person's name, a figure (a number, amount or quantity) or a place, "
        "so that the altered claim and the original cannot both be true. Keep everything "
        "else the same, make the change plausible, and do not hedge (no 'around', "
        "'possibly'). Vary the kind of change across the claims."
        + _language_note(language)
        + '\n\nReturn {"items": [{"ref": "c1", "perturbation": "date|name|figure|place", '
        '"text": "<altered claim>"}]} with exactly one item per claim.\n\nCLAIMS:\n' + lines
    )


def _paraphrase_prompt(refs: dict[str, SourceClaim], language: str) -> str:
    lines = "\n".join(f"{ref}: {claim.text}" for ref, claim in refs.items())
    return (
        "Below are factual claims from published articles about the history of Panama, "
        "each with a ref. For each claim write ONE faithful paraphrase: exactly the same "
        "facts (the same dates, names, figures and places), with different wording and "
        "sentence structure. Do not add, drop or round any fact."
        + _language_note(language)
        + '\n\nReturn {"items": [{"ref": "c1", "text": "<paraphrase>"}]} with exactly one '
        "item per claim.\n\nCLAIMS:\n" + lines
    )


def _near_dup_prompt(refs: dict[str, SourceArticle], language: str) -> str:
    blocks = []
    for ref, art in refs.items():
        title = (art.title_es or art.title) if language == "es" else art.title
        excerpt = (art.excerpt_es or art.excerpt) if language == "es" else art.excerpt
        blocks.append(f"{ref}: TITLE: {title}\n    SUMMARY: {_clip(excerpt) or '(none)'}")
    return (
        "Below are articles the site has already published, each with a ref. For each one, "
        "propose ONE new article suggestion that an editor might pitch without realising the "
        "article exists: the same core subject, but a new title that does not reuse the "
        "existing title's distinctive wording, and a one- or two-sentence main topic."
        + _language_note(language)
        + '\n\nReturn {"items": [{"ref": "a1", "title": "...", "main_topic": "..."}]} with '
        "exactly one item per article.\n\nARTICLES:\n" + "\n".join(blocks)
    )


def _novel_prompt(titles: list[str], count: int, language: str) -> str:
    listed = "\n".join(f"- {t}" for t in titles[:MAX_TITLES_IN_PROMPT])
    return (
        f"The site has already published or scheduled the articles titled below. Propose "
        f"{count} new article suggestions on Panama history topics that NONE of these "
        "articles covers, not even partly: different events, people, places or periods, not "
        "a new angle on a listed subject. Each needs a title and a one- or two-sentence main "
        "topic. Prefer concrete, real, documented subjects."
        + _language_note(language)
        + '\n\nReturn {"items": [{"title": "...", "main_topic": "..."}]}.\n\n'
        "EXISTING TITLES:\n" + listed
    )


def build_batches(
    sampled: dict[str, list], titles: list[str], counts: dict[str, int]
) -> list[SeedBatch]:
    """One batch per (type, language): the sampled sources split EN-first."""
    batches: list[SeedBatch] = []
    builders = {
        "perturbed": ("c", _perturb_prompt),
        "paraphrase": ("c", _paraphrase_prompt),
        "near_duplicate": ("a", _near_dup_prompt),
    }
    for type_, (prefix, build) in builders.items():
        sources = sampled[type_]
        split = split_languages(len(sources))
        offset = 0
        for language in LANGUAGES:
            chunk = sources[offset : offset + split[language]]
            offset += split[language]
            if not chunk:
                continue
            refs = {f"{prefix}{i}": s for i, s in enumerate(chunk, start=1)}
            batches.append(
                SeedBatch(type_, language, build(refs, language), refs=refs, count=len(refs))
            )
    for language, n in split_languages(counts["novel"]).items():
        if n:
            batches.append(
                SeedBatch("novel", language, _novel_prompt(titles, n, language), count=n)
            )
    return batches


def estimate_cost(batches: list[SeedBatch], novel_count: int) -> dict:
    """Sonnet input estimate + an output upper bound; the Voyage novelty-check estimate."""
    input_tokens = sum(b.input_tokens_est for b in batches)
    output_cap = MAX_TOKENS * len(batches)
    voyage_tokens = 60 * novel_count
    voyage_cost = embeddings.estimate_cost(embeddings.document_model(), voyage_tokens)
    return {
        "calls": len(batches),
        "input_tokens_est": input_tokens,
        "output_tokens_cap": output_cap,
        "cost_usd_min": round(input_tokens * SONNET_INPUT_PRICE_PER_MTOK / 1e6, 4),
        "cost_usd_max": round(
            input_tokens * SONNET_INPUT_PRICE_PER_MTOK / 1e6
            + output_cap * SONNET_OUTPUT_PRICE_PER_MTOK / 1e6,
            4,
        ),
        "voyage_tokens_est": voyage_tokens,
        "voyage_cost_usd_est": round(voyage_cost, 6),
    }


def _text(value) -> str | None:
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else None


def _norm(value: str) -> str:
    return " ".join(value.lower().split())


def parse_batch(batch: SeedBatch, items) -> tuple[list[dict], int]:
    """Model items -> seed items (without ids); returns (items, dropped count)."""
    out: list[dict] = []
    dropped = 0
    seen: set[str] = set()
    if not isinstance(items, list):
        return [], 0
    for item in items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        if batch.type in DUPLICATE_TYPES:
            title, topic = _text(item.get("title")), _text(item.get("main_topic"))
            if not title or not topic:
                dropped += 1
                continue
            if batch.type == "novel":
                if len(out) >= batch.count or _norm(title) in seen:
                    dropped += 1
                    continue
                seen.add(_norm(title))
                out.append(_duplicate_item("novel", batch.language, title, topic, None))
                continue
        ref = item.get("ref")
        if ref not in batch.refs or ref in seen:
            dropped += 1
            continue
        source = batch.refs[ref]
        if batch.type == "near_duplicate":
            seen.add(ref)
            out.append(_duplicate_item("near_duplicate", batch.language, title, topic, source))
            continue
        text = _text(item.get("text"))
        if not text:
            dropped += 1
            continue
        perturbation = None
        if batch.type == "perturbed":
            perturbation = item.get("perturbation")
            if perturbation not in PERTURBATIONS or _norm(text) == _norm(source.text):
                dropped += 1
                continue
        seen.add(ref)
        out.append(
            {
                "type": batch.type,
                "language": batch.language,
                "text": text,
                "perturbation": perturbation,
                "source_article_id": source.article_id,
                "source_article_title": source.title,
                "source_claim_id": source.claim_id,
                "source_chunk_id": source.chunk_id,
                "original_text": source.text,
                "expected_label": EXPECTED[batch.type],
                "review": None,
            }
        )
    return out, dropped


def _duplicate_item(type_, language, title, topic, source: SourceArticle | None) -> dict:
    return {
        "type": type_,
        "language": language,
        "title": title,
        "main_topic": topic,
        "text": suggestion_text(title, topic),
        "source_article_id": source.article_id if source else None,
        "original_text": source.title if source else None,
        "expected_label": EXPECTED[type_],
        "review": "pending" if type_ == "novel" else None,
    }


def _call(batch: SeedBatch) -> list:
    message = llm.create_message(
        feature=FEATURES[batch.type],
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": EFFORT},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": batch.prompt}],
    )
    parsed = llm.parse_llm_json(message, required_key="items")
    return parsed["items"] if isinstance(parsed["items"], list) else []


def run_batches(batches: list[SeedBatch]) -> tuple[list[dict], dict]:
    """Call Sonnet per batch; a failed batch is skipped and counted."""
    items: list[dict] = []
    stats = {"calls": 0, "failed_calls": 0, "dropped_items": 0}
    for batch in batches:
        stats["calls"] += 1
        try:
            raw = _call(batch)
        except (ValueError, llm.BudgetExceededError) as exc:
            stats["failed_calls"] += 1
            print(
                f"  {batch.type}/{batch.language}: model call failed ({type(exc).__name__}); skipped"
            )
            continue
        parsed, dropped = parse_batch(batch, raw)
        stats["dropped_items"] += dropped
        items += parsed
    return items, stats


def novelty_texts(items: list[dict]) -> list[str]:
    return [i["text"] for i in items if i["type"] == "novel"]


def annotate_novelty(
    db: Session, items: list[dict], vectors: list[list[float]], floor: float
) -> None:
    """Write the nearest article centroid onto each novel item (``vectors`` in novel order)."""
    novel = [i for i in items if i["type"] == "novel"]
    hits = []
    for item, vector in zip(novel, vectors, strict=True):
        found = related.nearest_articles(db, vector, k=1, visibility="internal")
        hits.append(found[0] if found else None)
    ids = {h.article_id for h in hits if h}
    titles = (
        dict(db.execute(select(Article.id, Article.title).where(Article.id.in_(ids))).all())
        if ids
        else {}
    )
    for item, hit in zip(novel, hits, strict=True):
        item["nearest_article_id"] = hit.article_id if hit else None
        item["nearest_article_title"] = titles.get(hit.article_id) if hit else None
        item["nearest_similarity"] = round(hit.similarity, 4) if hit else None
        item["suspect"] = bool(hit and hit.similarity >= floor)


def build_document(items: list[dict], *, seed: int, stats: dict, usage: dict, counts: dict) -> dict:
    contradictions = [i for i in items if i["type"] in CONTRADICTION_TYPES]
    duplicates = [i for i in items if i["type"] in DUPLICATE_TYPES]
    order = {t: n for n, t in enumerate(DEFAULT_COUNTS)}

    def number(rows: list[dict], prefix: str) -> list[dict]:
        rows = sorted(rows, key=lambda r: (order[r["type"]], r["language"]))
        return [{"id": f"{prefix}{n:03d}", **r} for n, r in enumerate(rows, start=1)]

    return {
        "version": SEED_VERSION,
        "kind": "pipeline-memory-seed",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": MODEL,
        "seed": seed,
        "requested": counts,
        "stats": stats,
        "usage": usage,
        "instructions": (
            "Sanity-review the novel duplicates (review: pending): if a topic is in fact "
            "covered by an existing article (see nearest_article_title), set review to "
            '"drop"; otherwise "keep". Any item with review "drop" is left out of '
            "`python -m scripts.eval_rag memory run`."
        ),
        "contradictions": number(contradictions, "c"),
        "duplicates": number(duplicates, "d"),
    }


def dumps(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


# --- Schema ---------------------------------------------------------------------------


def _check_item(item, suite: str, errors: list[str], ids: set[str]) -> None:
    if not isinstance(item, dict):
        errors.append(f"{suite}: an item is not an object")
        return
    iid = item.get("id")
    where = f"{suite}[{iid}]"
    if not isinstance(iid, str) or not iid:
        errors.append(f"{suite}: an item has no id")
    elif iid in ids:
        errors.append(f"{where}: duplicate id")
    else:
        ids.add(iid)
    types = CONTRADICTION_TYPES if suite == "contradictions" else DUPLICATE_TYPES
    type_ = item.get("type")
    if type_ not in types:
        errors.append(f"{where}: type must be one of {types}")
        return
    if item.get("language") not in LANGUAGES:
        errors.append(f"{where}: language must be en or es")
    if not _text(item.get("text")):
        errors.append(f"{where}: text is empty")
    if item.get("expected_label") != EXPECTED[type_]:
        errors.append(f"{where}: expected_label must be {EXPECTED[type_]} for {type_}")
    if item.get("review") not in REVIEW_VALUES:
        errors.append(f"{where}: review must be null, pending, keep or drop")
    if type_ == "novel":
        if item.get("source_article_id") is not None:
            errors.append(f"{where}: a novel topic has no source article")
    else:
        if not isinstance(item.get("source_article_id"), int):
            errors.append(f"{where}: source_article_id must be an integer")
        if not _text(item.get("original_text")):
            errors.append(f"{where}: original_text is empty")
    if suite == "contradictions":
        for key in ("source_claim_id", "source_chunk_id"):
            if not isinstance(item.get(key), int):
                errors.append(f"{where}: {key} must be an integer")
        if type_ == "perturbed" and item.get("perturbation") not in PERTURBATIONS:
            errors.append(f"{where}: perturbation must be one of {PERTURBATIONS}")
    else:
        for key in ("title", "main_topic"):
            if not _text(item.get(key)):
                errors.append(f"{where}: {key} is empty")


def validate_seed(doc) -> list[str]:
    """Every schema problem in a seed document (empty list = valid)."""
    if not isinstance(doc, dict):
        return ["the seed file is not a JSON object"]
    errors: list[str] = []
    if doc.get("version") != SEED_VERSION:
        errors.append(f"version must be {SEED_VERSION}")
    ids: set[str] = set()
    for suite in ("contradictions", "duplicates"):
        rows = doc.get(suite)
        if not isinstance(rows, list):
            errors.append(f"{suite} must be a list")
            continue
        for item in rows:
            _check_item(item, suite, errors, ids)
    return errors


def load_seed(path: Path) -> dict:
    """Parse + validate; raises :class:`SeedError` (or ``FileNotFoundError``)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SeedError([f"not valid JSON: {exc}"]) from None
    errors = validate_seed(doc)
    if errors:
        raise SeedError(errors)
    return doc


def active(rows: list[dict]) -> list[dict]:
    """Items a reviewer has not dropped."""
    return [r for r in rows if r.get("review") != "drop"]
