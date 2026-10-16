"""Draft candidate golden questions with Sonnet, for the operator to curate (#627).

Samples public ARTICLE chunks from the local ``knowledge_chunks`` (APPROVED,
``published_at <= now()``, taxonomy != ``EXCLUDED_TAXONOMY_ID``: the same
corpus Ask PIC answers from), asks Sonnet through ``llm.create_message`` for
questions in four batches, and writes a draft file. **It never writes to the
database**; the session is closed before the first model call.

* ``factual`` — one chunk, a question that chunk answers, in the chunk's language.
* ``cross-lingual`` — one chunk, the question in the *other* language; the
  draft's ``search_languages`` is the chunk's language, so retrieval must cross.
* ``multi-article`` — two chunks from two articles of one category; a question
  that needs both.
* ``unanswerable`` — the list of published titles goes in, and Sonnet proposes
  Panama topics none of them covers plus off-topic questions.

The fifth type, ``reader`` (#693), is drafted per article by Opus and verified
by a second Opus pass instead of a curator; it lives in :mod:`.reader`
(``draft-reader`` command). These four types are unchanged.

Every draft gets ``status: null``; the curator sets ``keep`` / ``edit`` /
``drop`` (editing fields in place for ``edit``), then ``golden`` converts the
file. Malformed model output is dropped, never repaired, and counted.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models.article import Article
from app.models.category import EXCLUDED_TAXONOMY_ID, Category
from app.models.enums import ContentStatus, KnowledgeSourceType
from app.models.knowledge_chunk import KnowledgeChunk
from app.services import llm
from app.services.knowledge.chunker import estimate_tokens
from sqlalchemy import func, select
from sqlalchemy.orm import Session

FEATURE = "rag_eval_drafts"
MODEL = llm.SONNET_MODEL
# Streams (>= anthropic_stream_min_tokens), so the 600 s timeout applies, and
# leaves adaptive thinking ample headroom (CLAUDE.md LLM Access Layer).
MAX_TOKENS = 16000
EFFORT = "medium"
# TODO(#653): confirm Sonnet 5.5 list prices; nothing in the repo records them.
# These are Sonnet 4.x's ($3 / $15 per 1M), used only for the printed estimate.
SONNET_INPUT_PRICE_PER_MTOK = 3.0
SONNET_OUTPUT_PRICE_PER_MTOK = 15.0

MIN_CHUNK_CHARS = 400
EXCERPT_CHARS = 600
OTHER = {"en": "es", "es": "en"}
LANGUAGE_NAMES = {"en": "English", "es": "Spanish"}

# ~60 drafts: factual 24, cross-lingual 10, multi-article 10, unanswerable 16.
DEFAULT_COUNTS = {"factual": 24, "cross-lingual": 10, "multi-article": 10, "unanswerable": 16}

SYSTEM_PROMPT = (
    "You draft evaluation questions for a retrieval system over a bilingual "
    "(English/Spanish) blog about the history of Panama. A human curator reviews "
    "every draft. Write questions the way a curious reader or a teacher would type "
    "them into a search box: natural, specific, one question each, never quoting "
    "the passage verbatim and never mentioning 'the passage' or 'the article'. "
    "Respond with JSON only, no prose and no code fences."
)


@dataclass
class SampledChunk:
    chunk_id: int
    article_id: int
    category_id: int | None
    language: str
    heading_path: str | None
    content: str
    title: str | None
    title_es: str | None

    def title_for(self, language: str) -> str | None:
        return (self.title_es or self.title) if language == "es" else self.title

    def source(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "article_id": self.article_id,
            "article_title": self.title_for(self.language),
            "language": self.language,
            "heading_path": self.heading_path,
            "excerpt": _excerpt(self.content),
        }


@dataclass
class DraftBatch:
    """One model call: its prompt and the passages its ``ref``s point at."""

    type: str
    prompt: str
    refs: dict[str, object] = field(default_factory=dict)  # ref -> SampledChunk | tuple

    @property
    def input_tokens_est(self) -> int:
        return estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(self.prompt)


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1].rstrip() + "…"


def load_public_chunks(db: Session) -> list[SampledChunk]:
    """Public ARTICLE chunks with enough text to ask about, in id order."""
    c = KnowledgeChunk.__table__.c
    articles = Article.__table__
    categories = Category.__table__
    stmt = (
        select(
            c.id,
            c.article_id,
            articles.c.category_id,
            c.language,
            c.heading_path,
            c.content,
            articles.c.title,
            articles.c.title_es,
        )
        .select_from(
            KnowledgeChunk.__table__.join(articles, articles.c.id == c.article_id).join(
                categories, categories.c.id == articles.c.category_id
            )
        )
        .where(
            c.source_type == KnowledgeSourceType.ARTICLE.value,
            articles.c.status == ContentStatus.APPROVED.value,
            articles.c.published_at.is_not(None),
            articles.c.published_at <= func.now(),
            categories.c.taxonomy_id != EXCLUDED_TAXONOMY_ID,
            func.length(c.content) >= MIN_CHUNK_CHARS,
        )
        .order_by(c.id)
    )
    return [SampledChunk(*row) for row in db.execute(stmt)]


def _pick_distinct_articles(
    rng: random.Random, chunks: list[SampledChunk], n: int, language: str, used: set[int]
) -> list[SampledChunk]:
    by_article: dict[int, list[SampledChunk]] = defaultdict(list)
    for chunk in chunks:
        if chunk.language == language and chunk.article_id not in used:
            by_article[chunk.article_id].append(chunk)
    article_ids = sorted(by_article)
    rng.shuffle(article_ids)
    picked = []
    for article_id in article_ids[:n]:
        picked.append(rng.choice(by_article[article_id]))
        used.add(article_id)
    return picked


def _split(n: int) -> tuple[int, int]:
    """(EN, ES) halves of ``n``, EN taking the odd one."""
    return (n + 1) // 2, n // 2


def sample(chunks: list[SampledChunk], counts: dict[str, int], *, seed: int) -> dict[str, list]:
    """Deterministic (by ``seed``) sample per type; articles do not repeat across types."""
    rng = random.Random(seed)
    used: set[int] = set()
    out: dict[str, list] = {}
    for qtype in ("factual", "cross-lingual"):
        en, es = _split(counts.get(qtype, 0))
        out[qtype] = _pick_distinct_articles(rng, chunks, en, "en", used)
        out[qtype] += _pick_distinct_articles(rng, chunks, es, "es", used)
    # Multi-article: two articles of one category, same language.
    pairs = []
    en, es = _split(counts.get("multi-article", 0))
    for language, n in (("en", en), ("es", es)):
        by_category: dict[int, dict[int, list[SampledChunk]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for chunk in chunks:
            if chunk.language == language and chunk.category_id is not None:
                by_category[chunk.category_id][chunk.article_id].append(chunk)
        categories = sorted(cid for cid, arts in by_category.items() if len(arts) >= 2)
        rng.shuffle(categories)
        for category_id in categories:
            if len([p for p in pairs if p[0].language == language]) >= n:
                break
            first, second = rng.sample(sorted(by_category[category_id]), 2)
            pairs.append(
                (
                    rng.choice(by_category[category_id][first]),
                    rng.choice(by_category[category_id][second]),
                )
            )
    out["multi-article"] = pairs
    return out


def _passage(ref: str, chunk: SampledChunk) -> str:
    heading = f" > {chunk.heading_path}" if chunk.heading_path else ""
    return (
        f"[{ref}] ({LANGUAGE_NAMES[chunk.language]}) "
        f"{chunk.title_for(chunk.language)}{heading}\n{chunk.content.strip()}"
    )


_SHAPE = (
    'Return {"drafts": [{"ref": "<ref>", "question": "<question>", '
    '"answer": "<one-sentence answer from the passage(s)>"}]}, one entry per ref.'
)


def build_batches(
    sampled: dict[str, list], titles: list[str], counts: dict[str, int]
) -> list[DraftBatch]:
    """The model calls for a sample (no I/O)."""
    batches: list[DraftBatch] = []
    factual = sampled.get("factual") or []
    if factual:
        refs = {f"P{i}": c for i, c in enumerate(factual, start=1)}
        body = "\n\n".join(_passage(ref, c) for ref, c in refs.items())
        batches.append(
            DraftBatch(
                "factual",
                "For each passage below, write ONE factual question that the passage "
                "answers on its own, in the SAME language as the passage. Prefer "
                "specific facts (who, when, where, how many, why) over vague themes.\n\n"
                f"{_SHAPE}\n\n{body}",
                refs,
            )
        )
    cross = sampled.get("cross-lingual") or []
    if cross:
        refs = {f"P{i}": c for i, c in enumerate(cross, start=1)}
        body = "\n\n".join(
            _passage(ref, c) + f"\n(write the question in {LANGUAGE_NAMES[OTHER[c.language]]})"
            for ref, c in refs.items()
        )
        batches.append(
            DraftBatch(
                "cross-lingual",
                "For each passage below, write ONE factual question that the passage "
                "answers, written in the language named after the passage (the OTHER "
                "language from the passage's own). Use the names a reader of that "
                "language would use.\n\n"
                f"{_SHAPE}\n\n{body}",
                refs,
            )
        )
    pairs = sampled.get("multi-article") or []
    if pairs:
        refs = {f"M{i}": pair for i, pair in enumerate(pairs, start=1)}
        body = "\n\n".join(
            f"=== {ref} ===\n{_passage(ref + 'a', a)}\n\n{_passage(ref + 'b', b)}"
            for ref, (a, b) in refs.items()
        )
        batches.append(
            DraftBatch(
                "multi-article",
                "Each numbered group below holds two passages from two different "
                "articles. For each group write ONE question, in the passages' "
                "language, whose full answer needs facts from BOTH passages (a "
                "comparison, a cause and its consequence, a before/after). Use the "
                'group ref (e.g. "M1") as the ref.\n\n'
                f"{_SHAPE}\n\n{body}",
                refs,
            )
        )
    n_unanswerable = counts.get("unanswerable", 0)
    if n_unanswerable:
        en, es = _split(n_unanswerable)
        panama_en, panama_es = (en + 1) // 2, (es + 1) // 2
        listing = "\n".join(f"- {t}" for t in titles)
        batches.append(
            DraftBatch(
                "unanswerable",
                "The blog has published exactly these articles:\n"
                f"{listing}\n\n"
                "Write questions this blog CANNOT answer:\n"
                f"- {panama_en} in English and {panama_es} in Spanish about Panama "
                "(history, geography, culture, current affairs) on topics NONE of the "
                'articles above covers ("subtype": "uncovered_panama");\n'
                f"- {en - panama_en} in English and {es - panama_es} in Spanish that are "
                'off-topic, unrelated to Panama ("subtype": "off_topic").\n'
                "Avoid near-misses an article above would partly answer.\n\n"
                'Return {"drafts": [{"language": "en"|"es", "subtype": '
                '"uncovered_panama"|"off_topic", "question": "<question>"}]}.',
            )
        )
    return batches


def estimate_cost(batches: list[DraftBatch]) -> dict:
    """Input estimate and an output upper bound (``MAX_TOKENS`` per call), in USD."""
    input_tokens = sum(b.input_tokens_est for b in batches)
    output_cap = MAX_TOKENS * len(batches)
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
    }


def _call(batch: DraftBatch) -> list:
    message = llm.create_message(
        feature=FEATURE,
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": EFFORT},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": batch.prompt}],
    )
    parsed = llm.parse_llm_json(message, required_key="drafts")
    drafts = parsed["drafts"]
    return drafts if isinstance(drafts, list) else []


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_batch(batch: DraftBatch, items: list) -> tuple[list[dict], int]:
    """Model items -> draft dicts (without ids); returns (drafts, dropped count)."""
    drafts: list[dict] = []
    dropped = 0
    seen_refs: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not _text(item.get("question")):
            dropped += 1
            continue
        question = _text(item["question"])
        if batch.type == "unanswerable":
            language = item.get("language")
            subtype = item.get("subtype")
            if language not in OTHER or subtype not in ("uncovered_panama", "off_topic"):
                dropped += 1
                continue
            drafts.append(
                {
                    "language": language,
                    "question": question,
                    "type": "unanswerable",
                    "subtype": subtype,
                    "expected_article_ids": [],
                    "source": None,
                }
            )
            continue
        ref = item.get("ref")
        if ref not in batch.refs or ref in seen_refs:
            dropped += 1
            continue
        seen_refs.add(ref)
        target = batch.refs[ref]
        if batch.type == "multi-article":
            a, b = target
            drafts.append(
                {
                    "language": a.language,
                    "question": question,
                    "type": "multi-article",
                    "expected_article_ids": [a.article_id, b.article_id],
                    "reference_answer": _text(item.get("answer")),
                    "source": [a.source(), b.source()],
                }
            )
            continue
        chunk = target
        language = chunk.language if batch.type == "factual" else OTHER[chunk.language]
        draft = {
            "language": language,
            "question": question,
            "type": batch.type,
            "expected_article_ids": [chunk.article_id],
            "expected_section": chunk.heading_path,
            "reference_answer": _text(item.get("answer")),
            "source": chunk.source(),
        }
        if batch.type == "cross-lingual":
            draft["search_languages"] = [chunk.language]
        drafts.append(draft)
    return drafts, dropped


def run_batches(batches: list[DraftBatch]) -> tuple[list[dict], dict]:
    """Call the model per batch; a failed or malformed batch is skipped and counted."""
    drafts: list[dict] = []
    stats = {"calls": 0, "failed_calls": 0, "dropped_items": 0}
    for batch in batches:
        stats["calls"] += 1
        try:
            items = _call(batch)
        except (ValueError, llm.BudgetExceededError) as exc:
            stats["failed_calls"] += 1
            print(f"  {batch.type}: model call failed ({type(exc).__name__}: {exc}); skipped")
            continue
        parsed, dropped = parse_batch(batch, items)
        stats["dropped_items"] += dropped
        drafts += parsed
    return drafts, stats


def build_document(
    drafts: list[dict], *, seed: int, sampled_chunks: int, stats: dict, usage: dict
) -> dict:
    """The draft file: numbered drafts with an empty ``status`` for the curator."""
    order = {t: i for i, t in enumerate(DEFAULT_COUNTS)}
    drafts = sorted(drafts, key=lambda d: (order[d["type"]], d["language"]))
    numbered = []
    for i, draft in enumerate(drafts, start=1):
        numbered.append(
            {
                "id": f"d{i:03d}",
                "status": None,
                **{k: v for k, v in draft.items() if v is not None or k == "source"},
                "notes": None,
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": MODEL,
        "seed": seed,
        "sampled_chunks": sampled_chunks,
        "stats": stats,
        "usage": usage,
        "instructions": (
            "For every draft set status to keep / edit / drop. For edit, change the "
            "fields in place (question, type, expected_article_ids, expected_section, "
            "search_languages, reference_answer). Check each answerable draft against "
            "source.excerpt, and add any other article that also answers it to "
            "expected_article_ids. Then run: python -m scripts.eval_rag golden "
            "--drafts <this file>"
        ),
        "drafts": numbered,
    }


def public_titles(db: Session) -> list[str]:
    """English titles of the public corpus, for the unanswerable prompt."""
    stmt = (
        select(Article.title)
        .join(Category, Category.id == Article.category_id)
        .where(
            Article.status == ContentStatus.APPROVED.value,
            Article.published_at.is_not(None),
            Article.published_at <= func.now(),
            Category.taxonomy_id != EXCLUDED_TAXONOMY_ID,
        )
        .order_by(Article.title)
    )
    return [t for t in db.execute(stmt).scalars() if t]


def dumps(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"
