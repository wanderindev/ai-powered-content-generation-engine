"""Ask Panama in Context: the public answer flow (#635, epic #619).

``POST /api/v1/ask`` (``app/api/ask.py``) is a thin wrapper around
:func:`answer_question`, which runs one question through the #634 controls in
``ask_pic_controls`` (used, not reimplemented) and returns an
:class:`AskOutcome` the router maps to HTTP. Steps 4-7 are
:func:`run_pipeline`, which has no ledger or limits of its own (the controls
reach it through two hooks), so ``scripts.eval_rag answers`` (#637) measures
the same code the endpoint runs:

1. **Validate** — blank or longer than ``ask_pic_max_question_chars`` is
   recorded ``INVALID`` (it counts toward the limits, so it cannot be spammed
   for free).
2. **Language** — the UI hint (``en`` / ``es``) wins; otherwise
   :func:`detect_language` (accents / ``¿¡`` / stop words, ties → ``es``, the
   site's ``fallbackLng``).
3. **Admit** — ``admit_query`` with ``min_cost_usd`` = the planned call's
   worst case over the *largest* prompt it could send, so a query that could
   never afford its answer is refused before it pays for retrieval.
4. **Retrieve** — ``retrieval.search(mode="hybrid", rerank=True,
   visibility="public")`` over both languages, restricted to ``ARTICLE`` /
   ``ARTICLE_CLAIM`` chunks. Public visibility inner-joins ``articles``
   (APPROVED + ``published_at <= now()`` + taxonomy ≠ 5), so no
   ``VERIFIED_CLAIM`` KB chunk — which can be an INACCURATE falsehood — can
   become a passage, and any chunk without an article is dropped again here.
   The Voyage tokens of the call are read through ``embeddings.track_usage``
   for the ledger. With ``ask_pic_query_rewrite_enabled`` (#696, default
   off) one Haiku call first adds an other-language and a period-terms query
   (:func:`rewrite_queries`, falls back to the question alone on any
   failure); each query is ranked on its own, the rankings are fused by RRF
   and reranked against the **original** question. The rewrite's cost joins
   the rerank's in ``extra_cost_usd`` (budget reservation and ledger), its
   worst case joins the admission check, and its time is in
   ``retrieval_ms``.
5. **Abstain without the model** when the top-1 rerank score is under
   ``ask_pic_min_rerank_score`` (0.74, #653), or — rerank unavailable — the
   top-1 vector similarity is under ``ask_pic_min_vector_similarity`` (0.575).
   With neither signal (no Voyage key: FTS only) there is no calibrated floor,
   so it abstains too.
6. **Reserve, then call** ``create_message(feature="ask_pic")`` on
   ``ask_pic_model`` (Haiku 5.5: no server-side refusal fallback, so a decline
   is ``RefusalError`` → ``REFUSED``) with an explicit ``max_tokens`` and
   ``effort``.
7. **Validate the output** — the reply is schema-constrained
   (``ANSWER_SCHEMA`` as structured outputs, #694); :func:`_answer_json`
   parses it strictly and, failing that, takes the last top-level JSON object
   with a ``status`` from the text (prose before the JSON). Citations must be
   passage ids that were sent; anything else is dropped. ``answered`` with no valid citation becomes
   ``NOT_COVERED``. Malformed JSON is recorded ``ERROR`` but answered to the
   reader as not covered, so the response contract never changes. URL-like
   tokens are stripped from the answer (:func:`strip_urls`, #710; only the
   count is logged); an answer left empty by that is ``NOT_COVERED``.
8. **Finish** — ``finish_query`` with the real usage, the embed tokens and
   the rerank cost (``extra_cost_usd``, passed again: it replaces the row's
   cost), latency and the cited article ids.

**Connection Pool rule.** Every control call commits, but a commit *expires*
the row and the next attribute read reloads it, autobeginning a transaction
that pins a connection. So the flow reads the admitted row's id / status
once, commits again, and never reads the row's attributes afterwards (only
the controls touch it, each ending in a commit). The request session is
therefore out of a transaction during retrieval and during the model call
(guard-tested). Retrieval runs in its own short ``SessionLocal()`` session. Its query
embedding happens before that session's first statement, but the rerank runs
inside ``search()`` after the SQL, on the seam's short query budget (8 s, 2
attempts), so the retrieval session does hold its connection for that call.

**#9 guarantee.** No question text reaches Sentry or the logs: this module
never touches ``sentry_sdk`` (guard-tested), its log lines carry ids, counts
and scores only, and an unexpected failure is logged by exception *class*
only — a SQL error's text would include the search parameters, and
``logger.exception`` would hand frame locals (the question) to Sentry.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.ask_pic_query import AskPicQuery
from app.models.enums import AskPicQueryStatus, KnowledgeSourceType, Language
from app.prompts.ask_pic import (
    ANSWER_SCHEMA,
    REWRITE_SCHEMA,
    REWRITE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    PromptPassage,
    build_rewrite_prompt,
    build_user_prompt,
)
from app.services import ask_pic_controls as controls
from app.services import embeddings
from app.services.knowledge import retrieval
from app.services.knowledge.chunker import TWIN_KEY
from app.services.llm import (
    HAIKU_MODEL,
    BudgetExceededError,
    LLMUsage,
    RefusalError,
    create_message,
    estimate_cost_usd,
    first_text,
    parse_llm_json,
    usage_from_message,
    worst_case_cost_usd,
)

logger = logging.getLogger(__name__)

FEATURE = "ask_pic"
# Passages (chunks, best first after rerank) sent to the model; see
# :class:`PassageSelection` for how they are picked. 8 -> 15 with the #695
# selection rules (docs/portfolio/rag/results/2026-10-12-answers-695.md).
MAX_PASSAGES = 15
# At most this many passages from one article (#695); None = no cap.
MAX_PASSAGES_PER_ARTICLE = 2
# A chunk is ~300-600 tokens (chunker.MAX_TOKENS at 4 chars/token, plus the
# overlap); this cap bounds the worst-case prompt the admission check prices.
MAX_PASSAGE_CHARS = 3000
# Thinking counts toward max_tokens: an answer of <= 180 words plus the JSON is
# a few hundred tokens, the rest is headroom for adaptive thinking. Under the
# 8000 streaming threshold, so the call is non-streamed on the 120 s timeout.
MAX_TOKENS = 4000
EFFORT = "medium"
# Hard cap on the answer text returned to the reader.
MAX_ANSWER_CHARS = 2000

# Query rewriting (#696, setting ``ask_pic_query_rewrite_enabled``): one cheap
# call before retrieval adds an other-language and a period-terms query. Haiku
# at effort low: two short queries are a few dozen output tokens, the cap is
# headroom for thinking (non-streamed, 120 s; the call is typically < 2 s).
REWRITE_FEATURE = "ask_pic_rewrite"
REWRITE_MODEL = HAIKU_MODEL
REWRITE_EFFORT = "low"
REWRITE_MAX_TOKENS = 1500
# A rewritten query longer than this is cut (the prompt asks for <= 25 words).
MAX_REWRITE_QUERY_CHARS = 300

_PASSAGE_SOURCE_TYPES = (
    KnowledgeSourceType.ARTICLE.value,
    KnowledgeSourceType.ARTICLE_CLAIM.value,
)
# Every chunk a search can return (two stages x candidates, before fusion).
_ALL_GROUPS = 2 * retrieval.DEFAULT_CANDIDATES
_PASSAGE_ID_IN_TEXT = re.compile(r"\s*\[(?:P\d+(?:\s*,\s*)?)+\]")

# URLs in an answer (#710). The passages are article text and citations are
# passage ids the frontend renders, so a URL in the answer can only come from
# the question (an attacker's "cite this URL") or the model's own knowledge;
# either way it is stripped. Deliberately conservative, because Spanish and
# English history prose is full of dots ("1.513", "S.A.", "U.S.", "a.C.",
# "St. Lawrence", "Nombre de Dios.Es decir"):
# - anything after ``http://`` / ``https://`` or ``www.`` (any case);
# - a bare ``label.tld`` only when it is all lowercase, starts a word, and the
#   TLD is in a short list of common ones (plus the RFC 2606 reserved names an
#   attacker in our own injection suite uses). A capitalised word, a number
#   or a TLD that is also a common Spanish / English word (``de``, ``la``,
#   ``no``, ``us``, ``me`` ...) never matches.
# ``scripts.eval_rag.answers`` flags an answer with this same pattern, so the
# eval and the filter agree on what a URL is by construction.
_URL_TLDS = (
    "com", "org", "net", "edu", "gov", "mil", "info", "biz", "io", "ai", "app",
    "dev", "xyz", "online", "site", "pa", "es", "mx", "co", "uk",
    "example", "test", "invalid",
)  # fmt: skip
URL_PATTERN = re.compile(
    r"(?i:\bhttps?://|\bwww\.)[^\s<>\"']*"
    r"|(?<![\w.@-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*[a-z][a-z0-9-]*"
    rf"\.(?:{'|'.join(_URL_TLDS)})(?![\w-])(?:[/?#][^\s<>\"']*)?"
)
# Sentence punctuation a URL match swallowed from the text around it.
_URL_TRAILING = ".,;:!?)]}»”’\"'"


def strip_urls(text: str) -> tuple[str, int]:
    """``text`` without URL-like tokens (``URL_PATTERN``), and how many there were.

    Punctuation the match swallowed at its end is kept, empty brackets left
    behind are dropped and the spacing is tidied. Text with no URL comes back
    unchanged.
    """
    count = 0

    def _drop(match: re.Match) -> str:
        nonlocal count
        count += 1
        token = match.group()
        return token[len(token.rstrip(_URL_TRAILING)) :]

    stripped = URL_PATTERN.sub(_drop, text)
    if not count:
        return text, 0
    stripped = re.sub(r"\(\s*\)|\[\s*\]|«\s*»|“\s*”", "", stripped)
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)
    stripped = re.sub(r"[ \t]+([.,;:!?)])", r"\1", stripped)
    return stripped.strip(), count


class Outcome(StrEnum):
    """What the router answers; the ledger status is on the row."""

    ANSWERED = "answered"
    NOT_COVERED = "not_covered"
    REFUSED = "refused"
    INVALID = "invalid"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXCEEDED = "budget_exceeded"
    ERROR = "error"


@dataclass
class Citation:
    article_id: int
    title: str
    url: str


@dataclass
class AskOutcome:
    outcome: Outcome
    language: str
    answer: str | None = None
    citations: list[Citation] = field(default_factory=list)
    detail: str | None = None


# --- Language -------------------------------------------------------------------

_ES_CHARS = set("¿¡ñáéíóúü")
_ES_WORDS = frozenset(
    "el la los las del que qué cuál cuándo cuando quién quien quiénes dónde donde "
    "cómo por para fue fueron es son una unos unas con se su sus porque cuántos "
    "cuántas hay sobre entre durante después antes".split()
)
_EN_WORDS = frozenset(
    "the of what which when who whom where how why was were is are did does do an "
    "and with for from about during after before there their many much you your "
    "me my it this that can tell now use".split()
)


def detect_language(question: str, hint: str | None = None) -> str:
    """The reader's language: a valid UI hint wins, else a heuristic on the text.

    Spanish-only characters (``¿¡ñ`` and accented vowels) decide for Spanish;
    otherwise the stop-word count, with ties going to Spanish (the site's
    ``fallbackLng``).
    """
    if hint:
        hint = hint.strip().lower()[:2]
        if hint in Language.values():
            return hint
    text = question.lower()
    if any(ch in _ES_CHARS for ch in text):
        return Language.ES.value
    words = re.findall(r"[a-z]+", text)
    es = sum(w in _ES_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    return Language.EN.value if en > es else Language.ES.value


# --- Citations ------------------------------------------------------------------


def article_url(language: str, slug: str) -> str:
    """The public article path the frontend routes (``App.jsx``): EN
    ``/en/articles/<slug>``, ES ``/es/articulos/<slug>``."""
    if language == Language.EN.value:
        return f"/en/articles/{slug}"
    return f"/es/articulos/{slug}"


@dataclass
class _Passage:
    passage_id: str
    article_id: int
    content: str
    # The chunk behind the passage, for the eval (#695).
    chunk_id: int | None = None
    chunk_index: int | None = None
    source_type: str | None = None
    language: str | None = None
    twin_key: str | None = None


@dataclass(frozen=True)
class PassageSelection:
    """How passages are picked from the reranked chunk list (#695).

    The walk is in rank order and the passages stay best-first:

    - ``language_dedup``: an ``ARTICLE`` chunk whose other-language twin
      (same article, same ``chunk_index``) is already selected takes no slot
      of its own; when it is in the question's language and the twin is not,
      it replaces the twin in the twin's slot. ``ARTICLE_CLAIM`` chunks are
      never twins.
    - ``twin_by_index``: how twins are paired. ``True`` (the default) is the
      #695 rule above. EN and ES pack differently in about half the
      articles, so it also pairs chunks that are not translations (303 of
      1,291 index pairs on 2026-10-13). ``False`` pairs by
      ``meta["twin_key"]`` (#713), which names the source paragraphs a chunk
      packs, so it pairs only real translations; a chunk without a key is
      never deduplicated. Measured in #713 with no gain in the answers eval,
      so it stays an eval variant.
    - ``skip_covered_claims``: an ``ARTICLE_CLAIM`` chunk (a one-line fact
      restating the article) is skipped when a paragraph of its article is
      already selected.
    - ``paragraph_replaces_claims``: the same skip, and a paragraph also
      *drops* the claim chunks of its article selected before it (they no
      longer count toward the cap). Measured stronger than the skip alone
      (#695), so it is the default; the skip stays as an eval variant.
    - ``per_article_cap``: at most this many passages from one article
      (``None`` = no cap), so one article cannot fill every slot.

    The defaults are what the endpoint runs; the eval passes variants
    (``scripts.eval_rag selection`` / ``answers --per-article-cap ...``).
    """

    max_passages: int = MAX_PASSAGES
    per_article_cap: int | None = MAX_PASSAGES_PER_ARTICLE
    language_dedup: bool = True
    skip_covered_claims: bool = False
    paragraph_replaces_claims: bool = True
    twin_by_index: bool = True


DEFAULT_SELECTION = PassageSelection()


def _passages(
    result: retrieval.SearchResult,
    language: str = Language.EN.value,
    selection: PassageSelection = DEFAULT_SELECTION,
) -> list[_Passage]:
    claim = KnowledgeSourceType.ARTICLE_CLAIM.value
    chosen: list = []  # ChunkHit, rank order

    def is_claim(c) -> bool:
        return c.source_type == claim

    def twin_of(c):
        if is_claim(c):
            return None
        if selection.twin_by_index:
            return c.chunk_index
        return _twin_key(c)

    for chunk in result.chunks:
        if chunk.article_id is None or chunk.source_type not in _PASSAGE_SOURCE_TYPES:
            continue
        article = chunk.article_id
        key = twin_of(chunk) if selection.language_dedup else None
        if key is not None:
            twin = next(
                (
                    i
                    for i, c in enumerate(chosen)
                    if c.article_id == article
                    and c.language != chunk.language
                    and twin_of(c) == key
                ),
                None,
            )
            if twin is not None:
                if chunk.language == language and chosen[twin].language != language:
                    chosen[twin] = chunk
                continue
        has_paragraph = any(c.article_id == article and not is_claim(c) for c in chosen)
        if (
            is_claim(chunk)
            and has_paragraph
            and (selection.skip_covered_claims or selection.paragraph_replaces_claims)
        ):
            continue
        if not is_claim(chunk) and selection.paragraph_replaces_claims:
            chosen = [c for c in chosen if not (c.article_id == article and is_claim(c))]
        cap = selection.per_article_cap
        if cap is not None and sum(c.article_id == article for c in chosen) >= cap:
            continue
        chosen.append(chunk)
        if len(chosen) == selection.max_passages:
            break
    return [
        _Passage(
            passage_id=f"P{i}",
            article_id=chunk.article_id,
            content=chunk.content[:MAX_PASSAGE_CHARS],
            chunk_id=chunk.chunk_id,
            chunk_index=chunk.chunk_index,
            source_type=chunk.source_type,
            language=chunk.language,
            twin_key=_twin_key(chunk),
        )
        for i, chunk in enumerate(chosen, start=1)
    ]


def _twin_key(chunk) -> str | None:
    """The chunk's language-neutral twin key (#713), or None when it has none."""
    key = (chunk.meta or {}).get(TWIN_KEY)
    return key if isinstance(key, str) and key else None


def search_passages(
    db, question: str, extra_queries: list[str] | None = None
) -> retrieval.SearchResult:
    """The retrieval call behind every answer: hybrid + rerank over public
    ``ARTICLE`` / ``ARTICLE_CLAIM`` chunks, both languages. The passage
    selection eval (#695) calls it too, so it ranks exactly what the endpoint does.

    With ``extra_queries`` (the #696 rewrites) every query gets its own
    hybrid ranking, the rankings are fused by RRF and the fused candidates
    are reranked against ``question`` (``retrieval.search_multi``); without
    them it is the single-query search, unchanged."""
    if extra_queries:
        queries = [question, *extra_queries]
        return retrieval.search_multi(
            db,
            queries,
            rerank_query=question,
            rerank=True,
            k=_ALL_GROUPS * len(queries),
            visibility="public",
            source_types=_PASSAGE_SOURCE_TYPES,
        )
    return retrieval.search(
        db,
        question,
        mode="hybrid",
        rerank=True,
        # Groups carry the titles / slugs of the passages' articles, and a
        # selection rule can reach past the first few articles: group them all.
        k=_ALL_GROUPS,
        visibility="public",
        source_types=_PASSAGE_SOURCE_TYPES,
    )


@dataclass
class Rewrite:
    """What the #696 rewrite call produced.

    ``queries`` are the extra search queries only (the original question is
    always searched too). ``status``: ``ok``, ``empty`` (nothing usable that
    differs from the question) or the exception class of a failed call;
    every non-``ok`` status means original-only retrieval. ``cost_usd`` is
    the call's list price, or its worst case when the seam raised after the
    request may have been billed (refusal, truncation, transport error)."""

    queries: list[str]
    status: str
    usage: LLMUsage | None = None
    cost_usd: float = 0.0
    ms: int = 0


def worst_rewrite_cost_usd(question: str, language: str) -> float:
    """The rewrite call's worst case (all input at the cache-write rate, the
    full ``REWRITE_MAX_TOKENS`` of output), for admission and failed calls."""
    tokens = controls.estimate_input_tokens(
        REWRITE_SYSTEM_PROMPT, build_rewrite_prompt(question, language)
    )
    return worst_case_cost_usd(REWRITE_MODEL, input_tokens=tokens, max_tokens=REWRITE_MAX_TOKENS)


def _rewrite_queries_from(data, question: str) -> list[str]:
    if not isinstance(data, dict):
        return []
    seen = {" ".join(question.split()).casefold()}
    queries = []
    for key in ("other_language", "period_terms"):
        value = data.get(key)
        if not isinstance(value, str):
            continue
        query = " ".join(value.split())[:MAX_REWRITE_QUERY_CHARS].strip()
        if query and query.casefold() not in seen:
            seen.add(query.casefold())
            queries.append(query)
    return queries


def rewrite_queries(question: str, language: str, *, log_label: str = "-") -> Rewrite:
    """Turn the question into extra search queries (#696). Never raises.

    The question is data (``REWRITE_SYSTEM_PROMPT``); the queries steer
    retrieval only. Any failure, a malformed reply included, falls back to
    original-only retrieval. Logged by status and counts, never the text.
    """
    t = time.perf_counter()
    try:
        message = create_message(
            feature=REWRITE_FEATURE,
            model=REWRITE_MODEL,
            max_tokens=REWRITE_MAX_TOKENS,
            system=REWRITE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_rewrite_prompt(question, language)}],
            output_config={
                "effort": REWRITE_EFFORT,
                "format": {"type": "json_schema", "schema": REWRITE_SCHEMA},
            },
        )
    except Exception as exc:  # noqa: BLE001 — optional step: fall back, never fail the query
        # The in-process backstop raises before sending: nothing was billed.
        if isinstance(exc, BudgetExceededError):
            cost = 0.0
        else:
            cost = worst_rewrite_cost_usd(question, language)
        logger.warning(
            "ask_pic query %s rewrite failed: %s; original question only",
            log_label,
            type(exc).__name__,
        )
        return Rewrite([], type(exc).__name__, cost_usd=cost, ms=_ms(t))
    usage = usage_from_message(message)
    try:
        data = parse_llm_json(message)
    except ValueError:
        data = None
    queries = _rewrite_queries_from(data, question)
    return Rewrite(
        queries,
        "ok" if queries else "empty",
        usage=usage,
        cost_usd=estimate_cost_usd(REWRITE_MODEL, usage),
        ms=_ms(t),
    )


def _user_prompt(
    result: retrieval.SearchResult, passages: list[_Passage], question: str, language: str
) -> str:
    titles = {g.article_id: g.title or "" for g in result.results if g.article_id is not None}
    return build_user_prompt(
        [PromptPassage(p.passage_id, titles.get(p.article_id, ""), p.content) for p in passages],
        question,
        language,
    )


def _top_scores(result: retrieval.SearchResult) -> tuple[float | None, float | None]:
    """(top-1 rerank score, top-1 vector similarity), either None if absent."""
    rerank = result.chunks[0].rerank_score if result.reranked and result.chunks else None
    distances = [c.vector_distance for c in result.chunks if c.vector_distance is not None]
    similarity = 1.0 - min(distances) if distances else None
    return rerank, similarity


def _clears_floor(rerank: float | None, similarity: float | None) -> bool:
    settings = get_settings()
    if rerank is not None:
        return rerank >= settings.ask_pic_min_rerank_score
    if similarity is not None:
        return similarity >= settings.ask_pic_min_vector_similarity
    return False


_JSON_DECODER = json.JSONDecoder()


def _last_status_object(text: str) -> dict | None:
    """The last top-level JSON object in ``text`` that has a ``status`` key.

    Walks the text left to right, decoding a JSON value at each ``{``; a
    decoded object is skipped over whole, so an object nested in another is
    never a candidate on its own.
    """
    found = None
    i = text.find("{")
    while i != -1:
        try:
            value, end = _JSON_DECODER.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(value, dict) and "status" in value:
            found = value
        i = text.find("{", end)
    return found


def _answer_json(message) -> tuple[dict | None, str]:
    """(the reply's JSON object, parse path), the object None when malformed.

    The path is ``strict`` (the whole text block is the JSON, fences allowed:
    ``parse_llm_json``, whose strictness its other callers rely on),
    ``tolerant`` (the last top-level object with a ``status`` key, e.g. prose
    before the JSON) or ``malformed``. Structured outputs should make every
    reply ``strict``; a ``tolerant`` one means the constraint did not hold.
    """
    try:
        data = parse_llm_json(message, required_key="status")
        if isinstance(data, dict):
            return data, "strict"
    except ValueError:
        pass
    try:
        text = first_text(message)
    except ValueError:
        return None, "malformed"
    data = _last_status_object(text) if isinstance(text, str) else None
    return (data, "tolerant") if data is not None else (None, "malformed")


def _parse_answer(
    message, sent_ids: dict[str, int]
) -> tuple[str, str, list[int], list[str]] | None:
    """(status, answer, cited article ids, cited passage ids), or None when
    the reply is malformed.

    Only ids in ``sent_ids`` survive; both lists keep first-cited order.
    """
    data, _ = _answer_json(message)
    return _validate_answer(data, sent_ids)


def _validate_answer(
    data: dict | None, sent_ids: dict[str, int]
) -> tuple[str, str, list[int], list[str]] | None:
    if not isinstance(data, dict) or data.get("status") not in ("answered", "not_covered"):
        return None
    answer = data.get("answer")
    citations = data.get("citations")
    if not isinstance(answer, str) or not isinstance(citations, list):
        return None
    article_ids: list[int] = []
    passage_ids: list[str] = []
    for cid in citations:
        article_id = sent_ids.get(cid) if isinstance(cid, str) else None
        if article_id is None:
            continue
        if cid not in passage_ids:
            passage_ids.append(cid)
        if article_id not in article_ids:
            article_ids.append(article_id)
    answer = _PASSAGE_ID_IN_TEXT.sub("", answer).strip()[:MAX_ANSWER_CHARS]
    status = data["status"]
    if status == "answered" and (not article_ids or not answer):
        status = "not_covered"
    if status == "not_covered":
        return status, "", [], []
    return status, answer, article_ids, passage_ids


def _citations(
    result: retrieval.SearchResult, article_ids: list[int], language: str
) -> list[Citation]:
    groups = {g.article_id: g for g in result.results if g.article_id is not None}
    citations = []
    for article_id in article_ids:
        group = groups.get(article_id)
        if group is None or not group.slug:
            continue
        if language == Language.ES.value:
            title, slug = group.title_es or group.title, group.slug_es or group.slug
        else:
            title, slug = group.title, group.slug
        citations.append(
            Citation(article_id=article_id, title=title or "", url=article_url(language, slug))
        )
    return citations


# --- The flow -------------------------------------------------------------------


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _voyage_cost(scope: dict[tuple[str, str], int]) -> tuple[int | None, float]:
    """(query-embedding tokens, rerank USD) from a ``track_usage`` scope."""
    embed_tokens = sum(t for (endpoint, _), t in scope.items() if endpoint == "embeddings")
    rerank_cost = sum(
        embeddings.estimate_cost(model, t)
        for (endpoint, model), t in scope.items()
        if endpoint == "rerank"
    )
    has_embed = any(endpoint == "embeddings" for endpoint, _ in scope)
    return (embed_tokens if has_embed else None), rerank_cost


def _worst_prompt_tokens(question: str) -> int:
    return controls.estimate_input_tokens(
        SYSTEM_PROMPT, question, "x" * (MAX_PASSAGES * (MAX_PASSAGE_CHARS + 120))
    )


def safe_rollback(db) -> None:
    """Roll back, swallowing (and logging by class only) any failure."""
    try:
        db.rollback()
    except Exception as exc:  # noqa: BLE001 — never escape with user text in a frame
        logger.warning("ask_pic: rollback failed: %s", type(exc).__name__)


def answer_question(db, *, question: str, language_hint: str | None, ip_hash: str) -> AskOutcome:
    """Run one public question end to end; see the module docstring.

    Never raises: any exception (pool saturation at admission, an unpriced
    ``ask_pic_model``, ...) would surface as a 500 that sentry-sdk captures
    with this frame's locals, i.e. the question. It is logged by class only and
    answered ``ERROR``; a row admitted before the failure is closed ``ERROR``.
    """
    try:
        return _answer_question(db, question=question, language_hint=language_hint, ip_hash=ip_hash)
    except Exception as exc:  # noqa: BLE001 — see docstring
        safe_rollback(db)
        logger.warning(
            "ask_pic query failed before admission completed: %s (details withheld)",
            type(exc).__name__,
        )
        hint = (language_hint or "").strip().lower()[:2]
        return AskOutcome(Outcome.ERROR, hint if hint in Language.values() else Language.ES.value)


def _answer_question(db, *, question: str, language_hint: str | None, ip_hash: str) -> AskOutcome:
    settings = get_settings()
    model = settings.ask_pic_model
    question = (question or "").strip()
    language = detect_language(question, language_hint)

    if not question or len(question) > settings.ask_pic_max_question_chars:
        row = controls.admit_query(
            db,
            ip_hash=ip_hash,
            language=language,
            question=question,
            status=AskPicQueryStatus.INVALID,
        )
        if row.status == AskPicQueryStatus.RATE_LIMITED.value:
            return AskOutcome(Outcome.RATE_LIMITED, language)
        return AskOutcome(
            Outcome.INVALID,
            language,
            detail=f"question must be 1-{settings.ask_pic_max_question_chars} characters",
        )

    worst = worst_case_cost_usd(
        model, input_tokens=_worst_prompt_tokens(question), max_tokens=MAX_TOKENS
    )
    if settings.ask_pic_query_rewrite_enabled:
        # The rewrite call runs before retrieval: a query must afford both.
        worst += worst_rewrite_cost_usd(question, language)
    query = controls.admit_query(
        db, ip_hash=ip_hash, language=language, question=question, min_cost_usd=worst
    )
    # admit_query committed, which expired the row: reading it reloads it and
    # autobegins a transaction on the request session. Read the plain values
    # once, then commit again so the request session holds no connection
    # through retrieval (Connection Pool rule). Nothing below reads the row's
    # attributes; only the controls' own calls touch it, and each commits.
    query_id, admitted_status = query.id, query.status
    db.commit()
    if admitted_status is not None:
        return AskOutcome(Outcome(admitted_status.lower()), language)

    ctx: dict = {"closed": False, "embed_tokens": None, "extra_cost_usd": 0.0}
    try:
        return _answer_admitted(
            db, query, query_id, ctx, question=question, language=language, model=model
        )
    except Exception as exc:  # noqa: BLE001 — every admitted row must be finished
        logger.warning(
            "ask_pic query %d failed: %s (details withheld: may carry user text)",
            query_id,
            type(exc).__name__,
        )
        if not ctx["closed"]:
            safe_rollback(db)
            try:
                controls.finish_query(
                    db,
                    query,
                    status=AskPicQueryStatus.ERROR,
                    embed_tokens=ctx["embed_tokens"],
                    extra_cost_usd=ctx["extra_cost_usd"],
                    retrieval_ms=ctx.get("retrieval_ms"),
                )
            except Exception as finish_exc:  # noqa: BLE001 — never escape with user text in a frame
                safe_rollback(db)
                logger.warning(
                    "ask_pic query %d: recording ERROR failed: %s",
                    query_id,
                    type(finish_exc).__name__,
                )
        return AskOutcome(Outcome.ERROR, language)


@dataclass
class PipelineResult:
    """Everything one run of the answer pipeline produced, for the ledger or an eval.

    ``parse_path`` is how the reply parsed (``strict`` / ``tolerant`` /
    ``malformed``, see :func:`_answer_json`), None when no model reply.
    ``stage`` is where the run ended: ``abstained`` (no passage, or below the
    retrieval floor: no model call), ``budget_exceeded`` (``before_call``
    said no), ``refused`` (``RefusalError``; ``usage`` unknown), ``malformed``
    (the reply did not parse), ``not_covered`` (the model said so, or no valid
    citation survived) or ``answered``.
    """

    stage: str
    language: str
    model: str
    passages: list[_Passage] = field(default_factory=list)
    top_rerank: float | None = None
    top_similarity: float | None = None
    degraded: list[str] = field(default_factory=list)
    retrieval_ms: int = 0
    embed_tokens: int | None = None
    rerank_cost_usd: float = 0.0
    prompt_tokens: int | None = None
    llm_ms: int | None = None
    usage: LLMUsage | None = None
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    cited_passage_ids: list[str] = field(default_factory=list)
    parse_path: str | None = None
    # URL-like tokens :func:`strip_urls` removed from the model's answer (#710).
    urls_stripped: int = 0
    # Query rewriting (#696); None when it did not run.
    rewrite: Rewrite | None = None

    @property
    def rewrite_cost_usd(self) -> float:
        return self.rewrite.cost_usd if self.rewrite is not None else 0.0

    @property
    def extra_cost_usd(self) -> float:
        """Everything before the answer call except the query embedding: the
        rerank plus the rewrite call (the ledger's ``extra_cost_usd``)."""
        return self.rerank_cost_usd + self.rewrite_cost_usd

    @property
    def pre_answer_ms(self) -> int:
        """Rewrite + retrieval time: what the ledger records as ``retrieval_ms``."""
        return self.retrieval_ms + (self.rewrite.ms if self.rewrite is not None else 0)


def run_pipeline(
    question: str,
    language: str,
    *,
    model: str,
    search_session=None,
    on_retrieved=None,
    before_call=None,
    log_label: str = "-",
    selection: PassageSelection = DEFAULT_SELECTION,
    rewrite: bool | None = None,
    on_rewritten=None,
) -> PipelineResult:
    """Retrieve, abstain, prompt, call the model, validate: the whole answer path.

    No ledger and no limits: :func:`_answer_admitted` wraps it with the #634
    controls, and ``scripts.eval_rag answers`` (#637) calls it directly, so
    the eval measures exactly the code the endpoint runs.

    - ``search_session``: a zero-argument factory for the retrieval session
      (default ``SessionLocal``); the eval passes a read-only one.
    - ``on_retrieved(result)``: called once retrieval is paid for, before
      anything that can fail, so the caller can keep its Voyage cost.
    - ``before_call(prompt_tokens) -> bool``: called right before the model
      call; ``False`` ends the run ``budget_exceeded``.
    - ``selection``: how passages are picked (:class:`PassageSelection`); the
      endpoint always runs the default, the eval passes variants (#695).
    - ``rewrite``: run the #696 query rewrite before retrieval (default: the
      ``ask_pic_query_rewrite_enabled`` setting); ``on_rewritten(result)``
      is called once it is paid for, before retrieval.

    Exceptions other than ``RefusalError`` (``TruncationError``, a failed
    search, ...) propagate to the caller.
    """
    out = PipelineResult(stage="abstained", language=language, model=model)
    factory = search_session or SessionLocal
    if rewrite is None:
        rewrite = get_settings().ask_pic_query_rewrite_enabled
    if rewrite:
        out.rewrite = rewrite_queries(question, language, log_label=log_label)
        if on_rewritten is not None:
            on_rewritten(out)
    extra_queries = out.rewrite.queries if out.rewrite is not None else None
    t = time.perf_counter()
    with embeddings.track_usage() as scope, factory() as search_db:
        result = search_passages(search_db, question, extra_queries)
    out.retrieval_ms = _ms(t)
    out.embed_tokens, out.rerank_cost_usd = _voyage_cost(scope)
    out.passages = _passages(result, language, selection)
    out.top_rerank, out.top_similarity = _top_scores(result)
    out.degraded = list(result.degraded)
    if on_retrieved is not None:
        on_retrieved(out)
    logger.info(
        "ask_pic query %s retrieval: language=%s rewrite=%s queries=%d passages=%d "
        "top1_rerank=%s top1_vector_similarity=%s degraded=%s retrieval_ms=%d",
        log_label,
        language,
        out.rewrite.status if out.rewrite is not None else "off",
        1 + len(extra_queries or []),
        len(out.passages),
        f"{out.top_rerank:.3f}" if out.top_rerank is not None else "-",
        f"{out.top_similarity:.3f}" if out.top_similarity is not None else "-",
        ",".join(result.degraded) or "-",
        out.retrieval_ms,
    )
    if not out.passages or not _clears_floor(out.top_rerank, out.top_similarity):
        return out

    user_prompt = _user_prompt(result, out.passages, question, language)
    out.prompt_tokens = controls.estimate_input_tokens(SYSTEM_PROMPT, user_prompt)
    if before_call is not None and not before_call(out.prompt_tokens):
        out.stage = "budget_exceeded"
        return out

    t = time.perf_counter()
    try:
        message = create_message(
            feature=FEATURE,
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
            },
        )
    except RefusalError:
        out.llm_ms = _ms(t)
        out.stage = "refused"
        return out
    out.llm_ms = _ms(t)
    out.usage = usage_from_message(message)

    sent_ids = {p.passage_id: p.article_id for p in out.passages}
    data, out.parse_path = _answer_json(message)
    parsed = _validate_answer(data, sent_ids)
    if parsed is None:
        logger.warning(
            "ask_pic query %s: malformed model output (parse=%s); answered as not covered",
            log_label,
            out.parse_path,
        )
        out.stage = "malformed"
        return out
    if out.parse_path == "tolerant":
        logger.warning(
            "ask_pic query %s: reply parsed by the tolerant fallback (text around the JSON)",
            log_label,
        )

    status, answer, article_ids, passage_ids = parsed
    if status == "answered":
        # #710: returned stripped, not turned into not_covered (see strip_urls).
        answer, out.urls_stripped = strip_urls(answer)
        if out.urls_stripped:
            logger.warning(
                "ask_pic query %s: stripped %d URL(s) from the answer",
                log_label,
                out.urls_stripped,
            )
        if not answer:
            status = "not_covered"
    citations = _citations(result, article_ids, language) if status == "answered" else []
    if status == "not_covered" or not citations:
        out.stage = "not_covered"
        return out
    out.stage = "answered"
    out.answer = answer
    out.citations = citations
    out.cited_passage_ids = passage_ids
    return out


def _answer_admitted(
    db,
    query: AskPicQuery,
    query_id: int,
    ctx: dict,
    *,
    question: str,
    language: str,
    model: str,
) -> AskOutcome:
    """Everything after admission: :func:`run_pipeline` plus the ledger.

    ``ctx`` carries what the caller's error path needs: the Voyage cost
    already paid and whether the row is closed."""

    def finish(**kwargs) -> None:
        controls.finish_query(db, query, **kwargs)
        ctx["closed"] = True

    def on_spent(run: PipelineResult) -> None:
        # After the rewrite and again after retrieval: what the error path
        # must keep if anything later fails.
        ctx.update(
            embed_tokens=run.embed_tokens,
            extra_cost_usd=run.extra_cost_usd,
            retrieval_ms=run.pre_answer_ms,
        )

    def before_call(prompt_tokens: int) -> bool:
        ok = controls.reserve_llm_call(
            db,
            query,
            model=model,
            input_tokens=prompt_tokens,
            max_tokens=MAX_TOKENS,
            embed_tokens=ctx["embed_tokens"],
            extra_cost_usd=ctx["extra_cost_usd"],
        )
        if not ok:
            ctx["closed"] = True  # reserve_llm_call closed the row BUDGET_EXCEEDED
        return ok

    run = run_pipeline(
        question,
        language,
        model=model,
        on_retrieved=on_spent,
        on_rewritten=on_spent,
        before_call=before_call,
        log_label=str(query_id),
    )
    costs = {
        "embed_tokens": run.embed_tokens,
        "extra_cost_usd": run.extra_cost_usd,
        "retrieval_ms": run.pre_answer_ms,
    }
    if run.stage == "abstained":
        finish(status=AskPicQueryStatus.NOT_COVERED, **costs)
        return AskOutcome(Outcome.NOT_COVERED, language)
    if run.stage == "budget_exceeded":
        return AskOutcome(Outcome.BUDGET_EXCEEDED, language)
    if run.stage == "refused":
        # The seam raises before handing back the message, so the call's usage
        # is unknown: keep the worst-case reservation as its cost.
        finish(
            status=AskPicQueryStatus.REFUSED,
            embed_tokens=run.embed_tokens,
            extra_cost_usd=run.extra_cost_usd
            + worst_case_cost_usd(model, input_tokens=run.prompt_tokens, max_tokens=MAX_TOKENS),
            retrieval_ms=run.pre_answer_ms,
            llm_ms=run.llm_ms,
        )
        return AskOutcome(Outcome.REFUSED, language)
    if run.stage == "malformed":
        finish(status=AskPicQueryStatus.ERROR, usage=run.usage, llm_ms=run.llm_ms, **costs)
        return AskOutcome(Outcome.NOT_COVERED, language)
    if run.stage == "not_covered":
        finish(status=AskPicQueryStatus.NOT_COVERED, usage=run.usage, llm_ms=run.llm_ms, **costs)
        return AskOutcome(Outcome.NOT_COVERED, language)
    finish(
        status=AskPicQueryStatus.ANSWERED,
        usage=run.usage,
        llm_ms=run.llm_ms,
        cited_article_ids=[c.article_id for c in run.citations],
        **costs,
    )
    return AskOutcome(Outcome.ANSWERED, language, answer=run.answer, citations=run.citations)


__all__ = [
    "AskOutcome",
    "Citation",
    "EFFORT",
    "FEATURE",
    "DEFAULT_SELECTION",
    "MAX_PASSAGES",
    "MAX_PASSAGES_PER_ARTICLE",
    "MAX_TOKENS",
    "Outcome",
    "PassageSelection",
    "PipelineResult",
    "REWRITE_FEATURE",
    "Rewrite",
    "answer_question",
    "article_url",
    "detect_language",
    "rewrite_queries",
    "run_pipeline",
    "search_passages",
    "worst_rewrite_cost_usd",
]
