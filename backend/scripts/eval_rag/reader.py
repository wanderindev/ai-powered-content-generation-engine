"""Reader-style golden questions: drafted and verified by Opus (#693, epic #698).

The #627 types (:mod:`.drafting`) are drafted from ONE chunk in that chunk's
wording, so they share vocabulary with their answer. Real readers type short,
broad questions where many chunks compete. This module drafts the ``reader``
type the other way round and replaces operator curation with a second model
pass.

**Drafting** (``feature="rag_eval_reader_drafts"``, Opus): Opus reads whole
public articles (``ARTICLES_PER_CALL`` per call, plus the list of every
published title so it can see which subjects compete) and writes, per
article, one English and one Spanish question a visitor or student would
ask about its subject: who / when / where / first / how long, period terms
(South Sea, Tierra Firme, Castilla del Oro...) and distractor-heavy subjects
(a lesser-known event of a famous person). Each draft has a
``reference_answer`` and ``key_facts`` (short strings a correct answer must
contain). A second call drafts reader-style ``unanswerable`` questions about
periods no published title covers.

**Verification** (``feature="rag_eval_reader_verify"``, Opus): each draft is
shown next to excerpts of its source article and of the other public
articles a keyword search finds (every article whose text holds all its key
facts, ranked by question-word overlap), and Opus answers keep / fix / drop
with the complete ``expected_article_ids``. ``fix`` is derived, not trusted:
an item is fixed when the verified fields differ from the draft. Unanswerable
drafts are checked against the articles that best match their words.

Model output is parsed strictly: a malformed item is dropped, never
repaired, and counted (as :mod:`.drafting` does). Drafts whose key facts do
not occur in the source article (in the question's language) are dropped
mechanically too. The operator's own questions (:data:`OPERATOR_SEEDS`) go
through verification as well, but a ``drop`` verdict never removes one: it is
kept as typed and the verdict recorded in ``notes``.

Nothing here writes to the database; the CLI loads the articles on a
read-only session and closes it before the first model call.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

import anthropic
from app.models.article import Article
from app.models.category import EXCLUDED_TAXONOMY_ID, Category
from app.models.enums import ContentStatus
from app.services import llm
from app.services.knowledge.chunker import estimate_tokens
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .golden import READER_SUBTYPES, key_facts_found, normalize_fact_text

DRAFT_FEATURE = "rag_eval_reader_drafts"
VERIFY_FEATURE = "rag_eval_reader_verify"
MODEL = llm.OPUS_MODEL
# Streams (>= anthropic_stream_min_tokens): the 600 s timeout applies, and
# adaptive thinking has room before the JSON (thinking counts toward it).
MAX_TOKENS = 16000
EFFORT = "medium"  # Opus 5.5's default; set explicitly (CLAUDE.md LLM Access Layer)

ARTICLES_PER_CALL = 3
DRAFTS_PER_VERIFY_CALL = 8
DEFAULT_UNANSWERABLE = 16
MAX_CANDIDATES = 5
EXPECTED_SNIPPETS = 3
CANDIDATE_SNIPPETS = 2
SNIPPET_CHARS = 700
MAX_QUESTION_CHARS = 200
MAX_KEY_FACTS = 4
MAX_KEY_FACT_CHARS = 40
LANGUAGES = ("en", "es")
UNANSWERABLE_SUBTYPE = "uncovered_period"

# The operator's own failing questions (2026-10-12), verbatim, plus Spanish
# equivalents. Expected ids and facts were checked against the production
# articles (2026-10-12: "1510" + "Enciso" in 30/31/32/50; the 25 September 1513
# sighting in 29/31/32/36/39/50); verification may still correct them.
OPERATOR_SEEDS: tuple[dict, ...] = (
    {
        "id": "op01",
        "language": "en",
        "question": "When did Vasco Nunez de Balboa arrive to mainland Panama?",
        "subtype": "distractor",
        "expected_article_ids": [30, 31, 32, 50],
        "reference_answer": (
            "In 1510, with Martín Fernández de Enciso's relief expedition to the Gulf of "
            "Urabá (he had sailed the coast earlier, with Rodrigo de Bastidas in 1501)."
        ),
        "key_facts": ["1510", "Enciso"],
    },
    {
        "id": "op02",
        "language": "es",
        "question": "¿Cuándo llegó Vasco Núñez de Balboa a tierra firme en Panamá?",
        "subtype": "distractor",
        "expected_article_ids": [30, 31, 32, 50],
        "reference_answer": (
            "En 1510, con la expedición de socorro de Martín Fernández de Enciso al golfo "
            "de Urabá (antes había recorrido la costa con Rodrigo de Bastidas en 1501)."
        ),
        "key_facts": ["1510", "Enciso"],
    },
    {
        "id": "op03",
        "language": "en",
        "question": "When did Balboa discover the South Sea?",
        "subtype": "period_term",
        "expected_article_ids": [29, 31, 32, 36, 39, 50],
        "reference_answer": (
            "He sighted it from a peak in Darién on 25 September 1513 and claimed it for "
            "Spain on 29 September 1513."
        ),
        "key_facts": ["1513", "25"],
    },
    {
        "id": "op04",
        "language": "es",
        "question": "¿Cuándo descubrió Balboa el Mar del Sur?",
        "subtype": "period_term",
        "expected_article_ids": [29, 31, 32, 36, 39, 50],
        "reference_answer": (
            "Lo avistó desde una cumbre del Darién el 25 de septiembre de 1513 y tomó "
            "posesión de él el 29 de septiembre de 1513."
        ),
        "key_facts": ["1513", "25"],
    },
    {
        "id": "op05",
        "language": "en",
        "question": "When did Balboa discover the Pacific?",
        "subtype": "when",
        "expected_article_ids": [29, 31, 32, 36, 39, 50],
        "reference_answer": (
            "He sighted it from a peak in Darién on 25 September 1513 and claimed it for "
            "Spain on 29 September 1513."
        ),
        "key_facts": ["1513", "25"],
    },
    {
        "id": "op06",
        "language": "es",
        "question": "¿Cuándo descubrió Balboa el Pacífico?",
        "subtype": "when",
        "expected_article_ids": [29, 31, 32, 36, 39, 50],
        "reference_answer": (
            "Lo avistó desde una cumbre del Darién el 25 de septiembre de 1513 y tomó "
            "posesión de él el 29 de septiembre de 1513."
        ),
        "key_facts": ["1513", "25"],
    },
)

DRAFT_SYSTEM = (
    "You write evaluation questions for 'Ask PIC', a question box on a bilingual "
    "(English/Spanish) blog about the history of Panama. The questions must sound like "
    "what a curious visitor or a high-school student actually types: short, plain, "
    "sometimes vague, never copying the article's wording. A strict checker verifies "
    "every question against the articles afterwards. Respond with JSON only, no prose "
    "and no code fences."
)

VERIFY_SYSTEM = (
    "You verify evaluation questions for a retrieval-augmented Q&A system over a "
    "bilingual blog about the history of Panama. You are strict and literal: a fact "
    "counts only if an excerpt you are shown states it. Respond with JSON only, no "
    "prose and no code fences."
)

_STOPWORDS = frozenset(
    "what when where which who whom whose why how does did was were is are the and for "
    "with from that this into about first long many much have has had his her their them "
    "they there then than over under after before during between panama panamá "
    "cuando cuándo donde dónde quien quién quienes cual cuál cuales cómo como que qué "
    "para por con los las del una uno unos unas sobre entre desde hasta fue fueron era "
    "eran tiene tenía primer primero primera cuánto cuánta cuántos cuántas tiempo".split()
)


@dataclass
class PublicArticle:
    id: int
    title: str | None
    title_es: str | None
    content: str
    content_es: str

    def text(self, language: str) -> str:
        if language == "es":
            return self.content_es or self.content
        return self.content or self.content_es

    def title_for(self, language: str) -> str:
        return ((self.title_es or self.title) if language == "es" else self.title) or ""

    def normalized_all(self) -> str:
        return normalize_fact_text(f"{self.content}\n{self.content_es}")


@dataclass
class Batch:
    """One model call: its prompt and what its refs point at."""

    kind: str  # "draft" | "unanswerable" | "verify" | "verify_unanswerable"
    prompt: str
    refs: dict[str, object] = field(default_factory=dict)
    # The head of ``prompt`` every batch of this kind shares (the published-title
    # listing + drafting rules). ``call`` sends it as its own cache-marked block
    # so batches 2..N read it from the prompt cache (#716). Empty: no marker.
    prefix: str = ""

    @property
    def input_tokens_est(self) -> int:
        system = DRAFT_SYSTEM if self.kind in ("draft", "unanswerable") else VERIFY_SYSTEM
        return estimate_tokens(system) + estimate_tokens(self.prompt)


# --- Loading ----------------------------------------------------------------------


def load_public_articles(db: Session) -> list[PublicArticle]:
    """Every public article (APPROVED, published, not taxonomy 5), in id order."""
    stmt = (
        select(Article.id, Article.title, Article.title_es, Article.content, Article.content_es)
        .join(Category, Category.id == Article.category_id)
        .where(
            Article.status == ContentStatus.APPROVED.value,
            Article.published_at.is_not(None),
            Article.published_at <= func.now(),
            Category.taxonomy_id != EXCLUDED_TAXONOMY_ID,
        )
        .order_by(Article.id)
    )
    return [
        PublicArticle(aid, title, title_es, content or "", content_es or "")
        for aid, title, title_es, content, content_es in db.execute(stmt)
    ]


# --- Drafting ---------------------------------------------------------------------


def _titles_listing(articles: list[PublicArticle]) -> str:
    return "\n".join(f"- [{a.id}] {a.title}" for a in articles if a.title)


_DRAFT_RULES = """For EACH article below write exactly TWO questions about its subject: \
one in English ("language": "en") and one in Spanish ("language": "es"), about two \
different facts.

How the questions must read:
- Short and natural (usually 5-14 words), as a visitor or student would type them. \
Never copy a phrase from the article, never mention "the article" or "the blog".
- Each question has a "kind": who | when | where | first | how_long | period_term | \
distractor | what. Use a mix across the batch, leaning on who / when / where / first / \
how_long.
- period_term: use the name of the period, not the modern one, where the article uses \
it or it clearly applies (South Sea / Mar del Sur for the Pacific, Tierra Firme, \
Castilla del Oro, the isthmus / el istmo, New Granada / Nueva Granada, the Spanish Main).
- distractor: ask about a lesser-known fact of a famous person, place or event whose \
better-known story dominates the published titles (e.g. when Balboa first reached the \
mainland, not his 1513 crossing). Use the title list to see what competes.
- The answer must be stated explicitly in THIS article (not inferred, not background \
knowledge), and should be a short fact, not an essay.

For each question also give:
- "reference_answer": one or two sentences, in the question's language, from the article.
- "key_facts": 1-3 short strings a correct answer MUST contain, each one a year, a \
number or a proper name exactly as the article writes it (e.g. "1510", "Enciso"). \
Avoid month names, common nouns and full dates: they change between English and \
Spanish. A key fact must appear in the article's text.

Return {"questions": [{"article_id": <id>, "language": "en"|"es", "kind": "<kind>", \
"question": "...", "reference_answer": "...", "key_facts": ["..."]}]}."""


def build_draft_batches(
    articles: list[PublicArticle], all_articles: list[PublicArticle]
) -> list[Batch]:
    """One call per ``ARTICLES_PER_CALL`` articles (no I/O)."""
    listing = _titles_listing(all_articles)
    batches = []
    for start in range(0, len(articles), ARTICLES_PER_CALL):
        group = articles[start : start + ARTICLES_PER_CALL]
        body = "\n\n".join(
            f'<article id="{a.id}">\n# {a.title}\n\n{a.content.strip()}\n</article>' for a in group
        )
        prefix = (
            f"Every article the blog has published, with its id:\n{listing}\n\n{_DRAFT_RULES}\n\n"
        )
        batches.append(Batch("draft", prefix + body, {str(a.id): a for a in group}, prefix))
    return batches


def build_unanswerable_batch(all_articles: list[PublicArticle], n: int) -> Batch | None:
    if n <= 0:
        return None
    en = (n + 1) // 2
    prompt = (
        f"Every article the blog has published:\n{_titles_listing(all_articles)}\n\n"
        f"Write {n} questions ({en} in English, {n - en} in Spanish) a visitor might ask "
        "about Panama's history that NONE of these articles answers: periods, events or "
        "people of Panamanian history the titles do not reach (later or earlier eras, "
        "subjects nobody wrote about). Same style as a real reader: short, plain, one "
        "question each. Avoid near misses that an article above would partly answer, and "
        "do not ask about anything outside Panama.\n\n"
        'Return {"questions": [{"language": "en"|"es", "question": "...", '
        '"period": "<the period or subject it is about>"}]}.'
    )
    return Batch("unanswerable", prompt)


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _facts(value) -> list[str] | None:
    """A valid key-facts list, or None (malformed)."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_KEY_FACTS:
        return None
    facts = [_text(f) for f in value]
    if any(f is None or len(f) > MAX_KEY_FACT_CHARS for f in facts):
        return None
    if len({normalize_fact_text(f) for f in facts}) != len(facts):
        return None
    return facts


def parse_draft_batch(batch: Batch, items: list) -> tuple[list[dict], Counter]:
    """Model items -> reader drafts; returns (drafts, counts of what was dropped and why)."""
    drafts: list[dict] = []
    dropped: Counter = Counter()
    for item in items:
        if not isinstance(item, dict):
            dropped["malformed"] += 1
            continue
        article = batch.refs.get(str(item.get("article_id")))
        question = _text(item.get("question"))
        answer = _text(item.get("reference_answer"))
        facts = _facts(item.get("key_facts"))
        language = item.get("language")
        kind = item.get("kind")
        if (
            article is None
            or language not in LANGUAGES
            or kind not in READER_SUBTYPES
            or question is None
            or len(question) > MAX_QUESTION_CHARS
            or answer is None
            or facts is None
        ):
            dropped["malformed"] += 1
            continue
        if len(key_facts_found(article.text(language), facts)) != len(facts):
            dropped["key_fact_not_in_article"] += 1
            continue
        drafts.append(
            {
                "language": language,
                "question": question,
                "type": "reader",
                "subtype": kind,
                "expected_article_ids": [article.id],
                "reference_answer": answer,
                "key_facts": facts,
                "source": "opus",
                "article_id": article.id,
            }
        )
    return drafts, dropped


def parse_unanswerable_batch(items: list) -> tuple[list[dict], Counter]:
    drafts: list[dict] = []
    dropped: Counter = Counter()
    for item in items:
        question = _text(item.get("question")) if isinstance(item, dict) else None
        if (
            question is None
            or len(question) > MAX_QUESTION_CHARS
            or item.get("language") not in LANGUAGES
        ):
            dropped["malformed"] += 1
            continue
        drafts.append(
            {
                "language": item["language"],
                "question": question,
                "type": "unanswerable",
                "subtype": UNANSWERABLE_SUBTYPE,
                "expected_article_ids": [],
                "source": "opus",
                "notes": _text(item.get("period")),
            }
        )
    return drafts, dropped


# --- Keyword search and excerpts ----------------------------------------------------


def question_words(question: str) -> list[str]:
    """Distinctive words of a question (normalized, >= 4 letters, no stopwords)."""
    words = re.findall(r"[a-z0-9]+", normalize_fact_text(question))
    stop = {normalize_fact_text(w) for w in _STOPWORDS}
    out: list[str] = []
    for w in words:
        if len(w) >= 4 and w not in stop and w not in out:
            out.append(w)
    return out


def keyword_candidates(
    draft: dict,
    articles: list[PublicArticle],
    *,
    exclude: set[int],
    limit: int = MAX_CANDIDATES,
) -> list[PublicArticle]:
    """Other public articles that may also answer ``draft``.

    Answerable: every article whose text (EN + ES) holds all its key facts,
    ranked by how many question words it also holds. Unanswerable: the
    articles holding the most question words.
    """
    words = question_words(draft["question"])
    facts = [normalize_fact_text(f) for f in draft.get("key_facts") or []]
    scored = []
    for article in articles:
        if article.id in exclude:
            continue
        haystack = article.normalized_all()
        if facts and not all(f in haystack for f in facts):
            continue
        overlap = sum(1 for w in words if w in haystack)
        if not facts and overlap == 0:
            continue
        scored.append((-overlap, article.id, article))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [a for _, _, a in scored[:limit]]


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def excerpts(article: PublicArticle, draft: dict, n: int) -> list[str]:
    """The ``n`` paragraphs (question's language) that best match the draft, in text order."""
    words = question_words(draft["question"])
    facts = [normalize_fact_text(f) for f in draft.get("key_facts") or []]
    paragraphs = _paragraphs(article.text(draft["language"]))
    scored = []
    for i, para in enumerate(paragraphs):
        norm = normalize_fact_text(para)
        score = 3 * sum(1 for f in facts if f in norm) + sum(1 for w in words if w in norm)
        if score:
            scored.append((-score, i))
    keep = sorted(i for _, i in sorted(scored)[:n])
    out = []
    for i in keep:
        para = " ".join(paragraphs[i].split())
        out.append(para if len(para) <= SNIPPET_CHARS else para[: SNIPPET_CHARS - 1] + "…")
    return out


def _article_block(article: PublicArticle, draft: dict, n: int) -> str:
    paras = excerpts(article, draft, n)
    body = "\n".join(f"  > {p}" for p in paras) if paras else "  (no matching paragraph)"
    return f'  <article id="{article.id}" title="{article.title_for(draft["language"])}">\n{body}'


# --- Verification -----------------------------------------------------------------

_VERIFY_RULES = """Each numbered item below is a draft question with its draft answer, \
draft key facts and draft expected articles, followed by excerpts of the draft's \
articles and of other articles a keyword search found. For EACH item decide:

- "keep": an excerpt of a draft expected article states the answer; the reference \
answer and key facts are right.
- "fix": the question is good and answerable from the excerpts, but the reference \
answer, the key facts or the expected articles are wrong or incomplete. Give the \
corrected values.
- "drop": the question is ambiguous (several defensible answers), not answerable from \
the excerpts, or not something a real reader would ask.

For keep and fix return the COMPLETE "expected_article_ids": every shown article whose \
excerpt states the answer (draft articles and keyword-search articles alike), and only \
those. "key_facts": 1-3 short strings that a correct answer must contain and that \
appear in the excerpts, in the question's language: each one a year, a number (a day \
of the month on its own, e.g. "25", is fine) or a proper name. Never a full date or a \
month name: "September 25" and "25 September" are both correct answers, so neither \
can be a key fact. Keep the draft's key facts when they follow these rules and are \
right. "reference_answer": one or two sentences in the question's language. With \
"keep", return the draft's values unchanged; rewording alone is not a fix.

Return {"verdicts": [{"ref": "<ref>", "verdict": "keep"|"fix"|"drop", \
"expected_article_ids": [<ids>], "reference_answer": "...", "key_facts": ["..."], \
"reason": "<one short sentence>"}], one per item."""

_VERIFY_UNANSWERABLE_RULES = """Each numbered item below is a question that should NOT be \
answerable from a Panama history blog, followed by excerpts of the blog articles that \
best match its words. For EACH item decide "keep" when no excerpt answers it, even \
partly, and it is a reasonable question about Panama's history; "drop" when an excerpt \
answers or partly answers it, or it is not about Panama.

Return {"verdicts": [{"ref": "<ref>", "verdict": "keep"|"drop", \
"reason": "<one short sentence>"}], one per item."""


def build_verify_batches(drafts: list[dict], articles: list[PublicArticle]) -> list[Batch]:
    """Verification calls for answerable drafts (no I/O). Each draft is ``R<n>``."""
    by_id = {a.id: a for a in articles}
    blocks: list[tuple[str, dict, list[int], str]] = []
    for i, draft in enumerate(drafts, start=1):
        ref = f"R{i}"
        expected = [by_id[a] for a in draft["expected_article_ids"] if a in by_id]
        others = keyword_candidates(draft, articles, exclude={a.id for a in expected})
        shown = [a.id for a in expected + others]
        text = (
            f"=== {ref} ({'English' if draft['language'] == 'en' else 'Spanish'}) ===\n"
            f"Question: {draft['question']}\n"
            f"Draft answer: {draft.get('reference_answer') or '-'}\n"
            f"Draft key facts: {json.dumps(draft.get('key_facts') or [], ensure_ascii=False)}\n"
            f"Draft expected articles: {[a.id for a in expected]}\n"
            "Draft articles:\n"
            + "\n".join(_article_block(a, draft, EXPECTED_SNIPPETS) for a in expected)
            + "\nKeyword-search articles:\n"
            + (
                "\n".join(_article_block(a, draft, CANDIDATE_SNIPPETS) for a in others)
                or "  (none)"
            )
        )
        blocks.append((ref, draft, shown, text))
    batches = []
    for start in range(0, len(blocks), DRAFTS_PER_VERIFY_CALL):
        group = blocks[start : start + DRAFTS_PER_VERIFY_CALL]
        prompt = _VERIFY_RULES + "\n\n" + "\n\n".join(text for *_, text in group)
        batches.append(Batch("verify", prompt, {ref: (d, shown) for ref, d, shown, _ in group}))
    return batches


def build_verify_unanswerable_batch(
    drafts: list[dict], articles: list[PublicArticle]
) -> Batch | None:
    if not drafts:
        return None
    blocks, refs = [], {}
    for i, draft in enumerate(drafts, start=1):
        ref = f"U{i}"
        found = keyword_candidates(draft, articles, exclude=set(), limit=4)
        blocks.append(
            f"=== {ref} ===\nQuestion: {draft['question']}\nBest-matching articles:\n"
            + ("\n".join(_article_block(a, draft, 2) for a in found) or "  (none)")
        )
        refs[ref] = (draft, [a.id for a in found])
    return Batch(
        "verify_unanswerable", _VERIFY_UNANSWERABLE_RULES + "\n\n" + "\n\n".join(blocks), refs
    )


def _same(a: dict, b: dict, compare_answer: bool = True) -> bool:
    """Same expected articles and key facts (and, by default, reference answer)."""
    return (
        sorted(a["expected_article_ids"]) == sorted(b["expected_article_ids"])
        and [normalize_fact_text(f) for f in a["key_facts"]]
        == [normalize_fact_text(f) for f in b["key_facts"]]
        and (
            not compare_answer
            or (a.get("reference_answer") or "") == (b.get("reference_answer") or "")
        )
    )


def apply_verdicts(
    batch: Batch, items: list, by_id: dict[int, PublicArticle]
) -> tuple[list[dict], Counter]:
    """Verifier items -> decided drafts (``verdict`` keep / fix / drop), plus counts.

    A malformed verdict, an id the item was not shown, or key facts absent
    from every verified article drop the item (``verify_malformed``); a ref
    the model skipped drops it as ``unverified``. Never repaired.
    """
    decided: dict[str, dict] = {}
    counts: Counter = Counter()
    for item in items:
        ref = item.get("ref") if isinstance(item, dict) else None
        if ref not in batch.refs or ref in decided:
            counts["verify_malformed"] += 1
            continue
        draft, shown = batch.refs[ref]
        verdict = item.get("verdict")
        reason = _text(item.get("reason"))
        if verdict == "drop":
            decided[ref] = {**draft, "verdict": "drop", "verify_reason": reason}
            continue
        ids = item.get("expected_article_ids")
        facts = _facts(item.get("key_facts"))
        answer = _text(item.get("reference_answer"))
        valid_ids = (
            isinstance(ids, list)
            and ids
            and all(isinstance(a, int) and not isinstance(a, bool) and a in shown for a in ids)
            and len(set(ids)) == len(ids)
        )
        if verdict not in ("keep", "fix") or not valid_ids or facts is None or answer is None:
            counts["verify_malformed"] += 1
            decided[ref] = {**draft, "verdict": "malformed", "verify_reason": reason}
            continue
        if not any(
            len(key_facts_found(by_id[a].text(draft["language"]), facts)) == len(facts) for a in ids
        ):
            counts["verify_malformed"] += 1
            decided[ref] = {
                **draft,
                "verdict": "malformed",
                "verify_reason": "key facts not in any expected article",
            }
            continue
        verified = {
            **draft,
            "expected_article_ids": sorted(ids),
            "key_facts": facts,
            "reference_answer": answer,
        }
        if verdict == "keep" and _same(draft, {**verified, "reference_answer": None}, False):
            verified = {**draft, "expected_article_ids": sorted(ids)}  # rewording is no fix
        final = "keep" if _same(draft, verified) else "fix"
        if final == "fix":
            verified["draft"] = {
                k: draft.get(k) for k in ("expected_article_ids", "key_facts", "reference_answer")
            }
        decided[ref] = {**verified, "verdict": final, "verify_reason": reason}
    for ref, (draft, _) in batch.refs.items():
        if ref not in decided:
            counts["unverified"] += 1
            decided[ref] = {**draft, "verdict": "unverified", "verify_reason": None}
    return [decided[ref] for ref in batch.refs], counts


def apply_unanswerable_verdicts(batch: Batch, items: list) -> tuple[list[dict], Counter]:
    decided: dict[str, dict] = {}
    counts: Counter = Counter()
    for item in items:
        ref = item.get("ref") if isinstance(item, dict) else None
        if ref not in batch.refs or ref in decided or item.get("verdict") not in ("keep", "drop"):
            counts["verify_malformed"] += 1
            continue
        draft, _ = batch.refs[ref]
        decided[ref] = {
            **draft,
            "verdict": item["verdict"],
            "verify_reason": _text(item.get("reason")),
        }
    for ref, (draft, _) in batch.refs.items():
        if ref not in decided:
            counts["unverified"] += 1
            decided[ref] = {**draft, "verdict": "unverified", "verify_reason": None}
    return [decided[ref] for ref in batch.refs], counts


# --- Calls ------------------------------------------------------------------------


@dataclass
class Spend:
    """Usage and list-price cost per ``feature=`` label."""

    usage: dict[str, llm.LLMUsage] = field(default_factory=dict)

    def add(self, feature: str, usage: llm.LLMUsage) -> None:
        prev = self.usage.get(feature, llm.LLMUsage())
        self.usage[feature] = llm.LLMUsage(*(a + b for a, b in zip(prev, usage, strict=True)))

    def as_dict(self) -> dict:
        out = {
            feature: {**u._asdict(), "cost_usd": round(llm.estimate_cost_usd(MODEL, u), 4)}
            for feature, u in self.usage.items()
        }
        out["total_cost_usd"] = round(sum(v["cost_usd"] for v in out.values()), 4)
        return out


# A failed batch is skipped, never the run (#708): a bad reply (ValueError,
# truncation and refusal included), the budget backstop, or an Anthropic API
# error after the SDK's own retries. Finished drafts are kept.
CALL_ERRORS = (ValueError, llm.BudgetExceededError, anthropic.APIError)


def _failure_kind(exc: Exception) -> dict[str, int]:
    """The extra counter for a failed call: ``api_errors`` for an API error."""
    return {"api_errors": 1} if isinstance(exc, anthropic.APIError) else {}


def _content(batch: Batch) -> str | list[dict]:
    """The user turn: the shared prefix as a cache-marked block, then the rest.

    Same text either way; splitting only moves where the cache breakpoint sits.
    """
    if not batch.prefix or not batch.prompt.startswith(batch.prefix):
        return batch.prompt
    rest = batch.prompt[len(batch.prefix) :]
    return [llm.cached_text(batch.prefix), {"type": "text", "text": rest}]


def call(batch: Batch, spend: Spend, required_key: str) -> list:
    """One Opus call; returns the list under ``required_key`` ([] when it is not a list)."""
    is_draft = batch.kind in ("draft", "unanswerable")
    feature = DRAFT_FEATURE if is_draft else VERIFY_FEATURE
    message = llm.create_message(
        feature=feature,
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": EFFORT},
        system=DRAFT_SYSTEM if is_draft else VERIFY_SYSTEM,
        messages=[{"role": "user", "content": _content(batch)}],
    )
    spend.add(feature, llm.usage_from_message(message))
    value = llm.parse_llm_json(message, required_key=required_key)[required_key]
    return value if isinstance(value, list) else []


def estimate_cost(batches: list[Batch]) -> dict:
    """Input estimate and the output cap, in USD at Opus list price."""
    p_in, p_out, *_ = llm.PRICE_PER_MILLION_TOKENS[MODEL]
    input_tokens = sum(b.input_tokens_est for b in batches)
    cap = MAX_TOKENS * len(batches)
    return {
        "calls": len(batches),
        "input_tokens_est": input_tokens,
        "output_tokens_cap": cap,
        "cost_usd_min": round(input_tokens * p_in / 1e6, 4),
        "cost_usd_max": round(input_tokens * p_in / 1e6 + cap * p_out / 1e6, 4),
    }


def seed_drafts() -> list[dict]:
    """The operator's questions as drafts (``source: operator``)."""
    return [{**seed, "type": "reader", "source": "operator"} for seed in OPERATOR_SEEDS]


def pick_articles(
    articles: list[PublicArticle], limit: int | None, seed: int
) -> list[PublicArticle]:
    """All articles, or a seeded sample of ``limit`` of them (in id order)."""
    if not limit or limit >= len(articles):
        return list(articles)
    rng = random.Random(seed)
    return sorted(rng.sample(articles, limit), key=lambda a: a.id)


def run(
    articles: list[PublicArticle],
    *,
    drafted_from: list[PublicArticle],
    n_unanswerable: int,
    progress=lambda _line: None,
) -> tuple[list[dict], dict, Spend]:
    """Draft, then verify. Returns (decided items, stats, spend). Never raises on a bad call."""
    spend = Spend()
    stats: Counter = Counter()
    by_id = {a.id: a for a in articles}

    answerable: list[dict] = []
    for i, batch in enumerate(build_draft_batches(drafted_from, articles), start=1):
        stats["draft_calls"] += 1
        try:
            items = call(batch, spend, "questions")
        except CALL_ERRORS as exc:
            stats["failed_calls"] += 1
            stats.update(_failure_kind(exc))
            progress(f"draft call {i} failed ({type(exc).__name__}); skipped")
            continue
        drafts, dropped = parse_draft_batch(batch, items)
        stats.update(dropped)
        answerable += drafts
        progress(f"draft call {i}: {len(drafts)} drafts, dropped {dict(dropped)}")
    stats["drafted_reader"] = len(answerable) + sum(
        stats[k] for k in ("malformed", "key_fact_not_in_article")
    )

    unanswerable: list[dict] = []
    ubatch = build_unanswerable_batch(articles, n_unanswerable)
    if ubatch:
        stats["draft_calls"] += 1
        try:
            items = call(ubatch, spend, "questions")
            unanswerable, dropped = parse_unanswerable_batch(items)
            stats.update({f"unanswerable_{k}": v for k, v in dropped.items()})
        except CALL_ERRORS as exc:
            stats["failed_calls"] += 1
            stats.update(_failure_kind(exc))
            progress(f"unanswerable draft call failed ({type(exc).__name__}); skipped")

    to_verify = seed_drafts() + answerable
    decided: list[dict] = []
    for i, batch in enumerate(build_verify_batches(to_verify, articles), start=1):
        stats["verify_calls"] += 1
        try:
            items = call(batch, spend, "verdicts")
        except CALL_ERRORS as exc:
            stats["failed_calls"] += 1
            stats.update(_failure_kind(exc))
            progress(f"verify call {i} failed ({type(exc).__name__}); its items are unverified")
            items = []
        out, counts = apply_verdicts(batch, items, by_id)
        stats.update(counts)
        decided += out
        progress(f"verify call {i}: {Counter(d['verdict'] for d in out)}")

    vbatch = build_verify_unanswerable_batch(unanswerable, articles)
    if vbatch:
        stats["verify_calls"] += 1
        try:
            items = call(vbatch, spend, "verdicts")
        except CALL_ERRORS as exc:
            stats["failed_calls"] += 1
            stats.update(_failure_kind(exc))
            progress(f"unanswerable verify call failed ({type(exc).__name__})")
            items = []
        out, counts = apply_unanswerable_verdicts(vbatch, items)
        stats.update({f"unanswerable_{k}": v for k, v in counts.items()})
        decided += out
    return decided, dict(stats), spend


# --- Output -----------------------------------------------------------------------

_KEEP_FIELDS = (
    "language",
    "question",
    "type",
    "subtype",
    "expected_article_ids",
    "reference_answer",
    "key_facts",
    "source",
    "notes",
)


def _status(item: dict) -> str:
    """keep / edit / drop for :func:`golden.drafts_to_golden` (operator items never drop)."""
    if item["verdict"] == "keep":
        return "keep"
    if item["verdict"] == "fix":
        return "edit"
    return "keep" if item.get("source") == "operator" else "drop"


def build_document(items: list[dict], *, stats: dict, spend: Spend, seed: int) -> dict:
    """The decided-drafts file: every item with its verdict, golden-ready."""
    order = {"operator": 0, "opus": 1}
    items = sorted(
        items,
        key=lambda d: (order.get(d.get("source"), 2), d["type"] == "unanswerable", d["language"]),
    )
    out, counters = [], {"reader": 0, "unanswerable": 0}
    for item in items:
        if item.get("source") == "operator":
            qid = item["id"]
        else:
            key = "unanswerable" if item["type"] == "unanswerable" else "reader"
            counters[key] += 1
            qid = f"{'ru' if key == 'unanswerable' else 'r'}{counters[key]:03d}"
        entry = {"id": qid, "status": _status(item), "verdict": item["verdict"]}
        entry.update({k: item[k] for k in _KEEP_FIELDS if item.get(k) is not None})
        if item.get("source") == "operator" and item["verdict"] not in ("keep", "fix"):
            entry["notes"] = (
                f"operator question kept as typed; verifier said {item['verdict']}: "
                f"{item.get('verify_reason') or '-'}"
            )
        for extra in ("article_id", "draft", "verify_reason"):
            if item.get(extra) is not None:
                entry[extra] = item[extra]
        out.append(entry)
    verdicts = Counter(e["verdict"] for e in out)
    summary = {
        "drafted": stats.get("drafted_reader", 0)
        + len(OPERATOR_SEEDS)
        + sum(1 for e in out if e["type"] == "unanswerable")
        + stats.get("unanswerable_malformed", 0),
        "kept": verdicts["keep"],
        "fixed": verdicts["fix"],
        "dropped": sum(1 for e in out if e["status"] == "drop"),
        "dropped_before_verification": stats.get("malformed", 0)
        + stats.get("key_fact_not_in_article", 0)
        + stats.get("unanswerable_malformed", 0),
        "verdicts": dict(verdicts),
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": MODEL,
        "effort": EFFORT,
        "seed": seed,
        "summary": summary,
        "stats": stats,
        "spend": spend.as_dict(),
        "instructions": (
            "Decided by the Opus verifier, not by hand: status keep / edit (= verdict fix) / "
            "drop. Merge with: python -m scripts.eval_rag golden --drafts <this file> --append"
        ),
        "drafts": out,
    }


def dumps(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def _cell(text) -> str:
    return " ".join(str(text or "").split()).replace("|", "/")


def render_curation_markdown(document: dict, drafts_file: str) -> str:
    """What the operator reviews instead of every item: the summary, then fixes and drops."""
    s, spend = document["summary"], document["spend"]
    drafts = document["drafts"]
    kept = [d for d in drafts if d["status"] != "drop"]
    out = [f"# Reader questions: Opus drafting and verification — {document['generated_at'][:10]}"]
    out += [
        "",
        f"Drafts file `{drafts_file}` (gitignored working file), model `{document['model']}` "
        f"(effort `{document['effort']}`). No per-item manual review: the verifier decided "
        "keep / fix / drop; this page lists every fix and every drop.",
        "",
        f"- Drafted {s['drafted']} (operator {len(OPERATOR_SEEDS)}); dropped before "
        f"verification as malformed or unsupported: {s['dropped_before_verification']}.",
        f"- Verdicts: {s['verdicts']}. Kept {s['kept']}, fixed {s['fixed']}, dropped "
        f"{s['dropped']}.",
        f"- In the golden set: {len(kept)} ({dict(Counter(d['type'] for d in kept))}; "
        f"{dict(Counter(d['language'] for d in kept))}; reader kinds "
        f"{dict(Counter(d.get('subtype') for d in kept if d['type'] == 'reader'))}).",
        f"- Stats: {document['stats']}.",
        "- Spend (list price, `feature=` labels): "
        + "; ".join(
            f"`{k}` {v['input_tokens']:,} in / {v['output_tokens']:,} out, ${v['cost_usd']:.2f}"
            for k, v in spend.items()
            if isinstance(v, dict)
        )
        + f"; total ${spend['total_cost_usd']:.2f}.",
        "",
        "## Fixed",
        "",
        "| id | question | expected ids (draft → verified) | key facts (draft → verified) | why |",
        "|---|---|---|---|---|",
    ]
    for d in drafts:
        if d["verdict"] != "fix":
            continue
        before = d.get("draft") or {}
        out.append(
            f"| {d['id']} | {_cell(d['question'])} | {before.get('expected_article_ids')} → "
            f"{d['expected_article_ids']} | {before.get('key_facts')} → {d['key_facts']} | "
            f"{_cell(d.get('verify_reason'))} |"
        )
    out += [
        "",
        "## Dropped",
        "",
        "| id | type | question | verdict | why |",
        "|---|---|---|---|---|",
    ]
    for d in drafts:
        if d["status"] == "drop":
            out.append(
                f"| {d['id']} | {d['type']} | {_cell(d['question'])} | {d['verdict']} | "
                f"{_cell(d.get('verify_reason'))} |"
            )
    operator = [d for d in drafts if d.get("source") == "operator"]
    out += [
        "",
        "## Operator questions",
        "",
        "| id | question | verdict | expected ids | key facts |",
    ]
    out += ["|---|---|---|---|---|"]
    for d in operator:
        out.append(
            f"| {d['id']} | {_cell(d['question'])} | {d['verdict']} | "
            f"{d['expected_article_ids']} | {d['key_facts']} |"
        )
    return "\n".join(out) + "\n"
