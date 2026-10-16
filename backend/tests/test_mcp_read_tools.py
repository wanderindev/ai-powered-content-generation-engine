"""MCP read tools (#640): search_knowledge, get_article, find_related,
check_claim, pipeline_status, explain_stuck_goal.

Most tests call the tool functions directly (they open ``database.SessionLocal``,
patched to the test engine); a few go through the real ``/mcp`` endpoint with the
SDK client to check registration, structured output, errors, scopes and audit.
Voyage is never called for real: the ``voyage`` fixture fakes the query embedding,
and without it conftest's empty ``VOYAGE_API_KEY`` exercises the degraded path.
"""

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx2
import pytest
from app.core import database
from app.core.config import get_settings
from app.main import app
from app.mcp.auth import REQUIRED_SCOPE_ATTR, generate_token, hash_token
from app.mcp.server import MCP_PATH, READ_TOOLS, mcp_http, mcp_server
from app.mcp.tools import knowledge as kt
from app.mcp.tools import pipeline as pt
from app.mcp.tools.bounds import MAX_RESULT_CHARS, bound_result, result_size
from app.models.agent_task import AgentTask
from app.models.enums import McpScope
from app.models.fact_check import FactCheckClaim
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.mcp_token import McpToken
from app.models.mcp_tool_call import McpToolCall
from app.models.verified_claim import VerifiedClaim
from app.services import embeddings
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import insert
from sqlalchemy.orm import sessionmaker

DIMS = 1024
BASE = "http://localhost"
READ_TOOL_NAMES = {name for name, *_ in READ_TOOLS}


def _vec(*weights):
    v = [0.0] * DIMS
    for i, w in weights:
        v[i] = w
    return v


CANAL = _vec((0, 1.0))
RAILROAD = _vec((1, 1.0))
OTHER = _vec((2, 1.0))


def fake_embed_query(text, *, feature="unlabeled"):
    t = text.lower()
    if "canal" in t:
        return CANAL
    if "railroad" in t or "ferrocarril" in t:
        return RAILROAD
    return OTHER


@pytest.fixture
def mcp_db(test_engine, test_db):
    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with patch.object(database, "SessionLocal", factory):
        yield test_db


@pytest.fixture
def voyage(monkeypatch):
    monkeypatch.setattr(get_settings(), "voyage_api_key", "fake-key")
    monkeypatch.setattr(embeddings, "embed_query", fake_embed_query)

    def no_rerank(*args, **kwargs):
        raise AssertionError("MCP read tools never rerank")

    monkeypatch.setattr(embeddings, "rerank", no_rerank)


_counter = {"n": 0}


def _chunk(db, *, content, article_id=None, source_type="ARTICLE", source_id=None,
           language="en", embedding=None, meta=None, heading_path=None):  # fmt: skip
    _counter["n"] += 1
    db.execute(
        insert(KnowledgeChunk.__table__).values(
            source_type=source_type,
            source_id=source_id if source_id is not None else (article_id or _counter["n"]),
            article_id=article_id,
            language=language,
            chunk_index=_counter["n"],
            heading_path=heading_path,
            content=content,
            content_hash=hashlib.sha256(f"{content}{_counter['n']}".encode()).hexdigest(),
            token_count=len(content) // 4 + 1,
            embedding_model="voyage-4" if embedding is not None else None,
            embedding=embedding,
            meta=meta,
        )
    )
    db.commit()


def _task(db, goal, kind, status, *, position=0, depends_on=None, **extra):
    task = AgentTask(
        goal_key=goal,
        kind=kind,
        subject_type=extra.pop("subject_type", "ARTICLE"),
        subject_id=extra.pop("subject_id", 1),
        status=status,
        position=position,
        depends_on=depends_on,
        **extra,
    )
    db.add(task)
    db.commit()
    return task


# --- Registration -----------------------------------------------------------------


def test_every_registered_tool_has_a_description_and_annotations():
    tools = {tool.name: tool for tool in mcp_server._tool_manager.list_tools()}
    assert READ_TOOL_NAMES <= set(tools)
    for name, tool in tools.items():
        assert tool.description and len(tool.description) > 80, name
        assert "Example" in tool.description, name
        assert tool.annotations is not None, name
    for name in READ_TOOL_NAMES | {"ping"}:
        hints = tools[name].annotations
        assert hints.read_only_hint is True, name
        assert hints.destructive_hint is False, name
        assert getattr(tools[name].fn, REQUIRED_SCOPE_ATTR) is McpScope.READ, name


def test_tool_inputs_cannot_carry_a_body_as_a_list_of_short_strings():
    """redact_args stores lists of short strings verbatim (#639); every list
    input of a read tool is a closed vocabulary with a small max length."""
    tools = {tool.name: tool for tool in mcp_server._tool_manager.list_tools()}
    for name in READ_TOOL_NAMES:
        props = tools[name].parameters["properties"]
        for arg, schema in props.items():
            variants = schema.get("anyOf", [schema])
            for variant in variants:
                if variant.get("type") == "array":
                    assert "enum" in variant["items"], (name, arg)
                    assert variant.get("maxItems", 99) <= 3, (name, arg)
                if variant.get("type") == "string" and "enum" not in variant:
                    assert variant.get("maxLength", 10**9) <= 2000, (name, arg)


# --- Through /mcp -----------------------------------------------------------------


def _mint(db, *, scopes=("read",)):
    token = generate_token()
    db.add(McpToken(name="reader", token_hash=hash_token(token), scopes=list(scopes)))
    db.commit()
    return token


@asynccontextmanager
async def _client(token):
    async with mcp_http.run():
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=BASE,
            headers={"Authorization": f"Bearer {token}"},
        ) as http:
            async with Client(
                streamable_http_client(f"{BASE}{MCP_PATH}", http_client=http)
            ) as client:
                yield client


async def test_tools_answer_over_mcp_with_structured_content_and_audit(mcp_db, article_factory):
    article = article_factory(title="The Canal Treaty")
    token = _mint(mcp_db)
    async with _client(token) as client:
        listed = {tool.name for tool in (await client.list_tools()).tools}
        ok = await client.call_tool("get_article", {"id_or_slug": article.slug})
        missing = await client.call_tool("get_article", {"id_or_slug": 999999})
        status = await client.call_tool("pipeline_status", {})
    assert READ_TOOL_NAMES <= listed
    assert not ok.is_error
    assert ok.structured_content["id"] == article.id
    assert missing.is_error and "not_found:" in missing.content[0].text
    assert status.structured_content["goals"] == []

    mcp_db.expire_all()
    rows = mcp_db.query(McpToolCall).order_by(McpToolCall.id).all()
    assert [(r.tool, r.outcome) for r in rows] == [
        ("get_article", "OK"),
        ("get_article", "ERROR"),
        ("pipeline_status", "OK"),
    ]


async def test_read_tools_need_the_read_scope(mcp_db):
    token = _mint(mcp_db, scopes=("pipeline",))
    async with _client(token) as client:
        result = await client.call_tool("search_knowledge", {"query": "canal"})
    assert result.is_error
    assert "insufficient_scope" in result.content[0].text


async def test_embeddings_disabled_degrades_instead_of_failing(mcp_db, article_factory):
    """conftest forces VOYAGE_API_KEY empty: hybrid falls back to full-text."""
    article = article_factory(title="Canal")
    _chunk(mcp_db, article_id=article.id, content="The canal opened in 1914", embedding=CANAL)
    token = _mint(mcp_db)
    async with _client(token) as client:
        result = await client.call_tool("search_knowledge", {"query": "canal opened"})
    assert not result.is_error
    assert result.structured_content["degraded"] == ["embeddings_disabled"]
    assert [r["article_id"] for r in result.structured_content["results"]] == [article.id]


# --- search_knowledge ---------------------------------------------------------------


def test_search_internal_visibility_and_grouping(mcp_db, voyage, article_factory):
    published = article_factory(title="Published Canal")
    scheduled = article_factory(
        title="Scheduled Canal", published_at=datetime.now(UTC) + timedelta(days=3)
    )
    draft = article_factory(title="Draft Canal", status="PENDING", published_at=None)
    for a in (published, scheduled, draft):
        _chunk(mcp_db, article_id=a.id, content=f"{a.title}: the canal", embedding=CANAL)
    _chunk(mcp_db, article_id=published.id, content="second canal chunk", embedding=CANAL,
           heading_path="Opening")  # fmt: skip

    out = kt.search_knowledge("canal", k=5)

    by_id = {r["article_id"]: r for r in out["results"]}
    assert set(by_id) == {published.id, scheduled.id}  # draft excluded
    assert by_id[scheduled.id]["scheduled"] is True
    assert by_id[published.id]["scheduled"] is False
    assert len(by_id[published.id]["chunks"]) == 2
    assert out["degraded"] == [] and out["truncated"] is False


def test_search_filters_and_kb_groups(mcp_db, voyage, article_factory):
    article = article_factory(title="Railroad")
    _chunk(mcp_db, article_id=article.id, content="railroad en", embedding=RAILROAD)
    _chunk(mcp_db, article_id=article.id, content="ferrocarril es", embedding=RAILROAD,
           language="es")  # fmt: skip
    _chunk(mcp_db, source_type="VERIFIED_CLAIM", source_id=7, content="railroad claim",
           embedding=RAILROAD, meta={"verdict": "VERIFIED"})  # fmt: skip

    es_only = kt.search_knowledge("railroad", language="es")
    assert [c["language"] for r in es_only["results"] for c in r["chunks"]] == ["es"]

    kb_only = kt.search_knowledge("railroad", source_types=["VERIFIED_CLAIM"])
    assert len(kb_only["results"]) == 1
    group = kb_only["results"][0]
    assert group["article_id"] is None
    assert group["chunks"][0]["verdict"] == "VERIFIED"


def test_search_output_is_bounded(mcp_db, voyage, article_factory):
    for i in range(20):
        a = article_factory(title=f"Canal {i}")
        for _ in range(3):
            _chunk(mcp_db, article_id=a.id, content="canal " + "x" * 3000, embedding=CANAL)
    out = kt.search_knowledge("canal", k=20)
    assert result_size(out) <= MAX_RESULT_CHARS
    assert out["results"]


# --- get_article -----------------------------------------------------------------


def test_get_article_by_id_and_spanish_slug_with_parts(mcp_db, article_factory):
    article = article_factory(title="The Railroad", title_es="El Ferrocarril")
    article.content_es = "Contenido en español"
    mcp_db.add_all(
        [
            FactCheckClaim(subject_type="ARTICLE", subject_id=article.id,
                           claim_text="Built in 1855", verdict="VERIFIED"),
            FactCheckClaim(subject_type="ARTICLE", subject_id=article.id,
                           claim_text="Built by the French", verdict="INACCURATE",
                           correction="Built by an American company"),
        ]
    )  # fmt: skip
    mcp_db.commit()
    _task(mcp_db, "daily_series:2026-10-01", "TRANSLATE", "FAILED", subject_id=article.id,
          error="DeepL 503", attempts=3)  # fmt: skip

    out = kt.get_article(str(article.id), include=["content", "claims", "pipeline"])
    assert out["title"] == "The Railroad"
    assert out["content"] == "Test content" and out["content_truncated"] is False
    assert out["claims"]["counts"] == {"VERIFIED": 1, "INACCURATE": 1}
    assert out["claims"]["items"][0]["verdict"] == "INACCURATE"  # problems first
    assert out["pipeline"][0]["kind"] == "TRANSLATE"
    assert out["pipeline"][0]["status"] == "FAILED"

    es = kt.get_article(article.slug_es, language="es", include=["content"])
    assert es["id"] == article.id
    assert es["title"] == "El Ferrocarril"
    assert es["content"] == "Contenido en español"
    assert "claims" not in es and "pipeline" not in es


def test_get_article_returns_drafts_and_raises_not_found(mcp_db, article_factory):
    draft = article_factory(title="Draft", status="PENDING", published_at=None)
    out = kt.get_article(draft.id)
    assert out["status"] == "PENDING" and out["scheduled"] is True
    with pytest.raises(ToolError, match="not_found"):
        kt.get_article("no-such-slug")


def test_get_article_content_is_bounded(mcp_db, article_factory):
    article = article_factory(title="Long", content="word " * 30_000)
    out = kt.get_article(article.id, include=["content"])
    assert out["content_truncated"] is True
    assert out["content_chars"] == 150_000
    assert result_size(out) <= MAX_RESULT_CHARS


# --- find_related ----------------------------------------------------------------


def test_find_related_internal_and_errors(mcp_db, article_factory, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("find_related must not call Voyage")

    monkeypatch.setattr(embeddings, "embed_query", boom)
    source = article_factory(title="Source")
    near = article_factory(title="Near", published_at=datetime.now(UTC) + timedelta(days=2))
    far = article_factory(title="Far")
    draft = article_factory(title="Draft", status="PENDING", published_at=None)
    _chunk(mcp_db, article_id=source.id, content="s", embedding=_vec((0, 1.0)))
    _chunk(mcp_db, article_id=near.id, content="n", embedding=_vec((0, 1.0), (1, 0.2)))
    _chunk(mcp_db, article_id=far.id, content="f", embedding=_vec((1, 1.0)))
    _chunk(mcp_db, article_id=draft.id, content="d", embedding=_vec((0, 1.0)))

    out = kt.find_related(source.id, k=4)
    assert [r["article_id"] for r in out["results"]] == [near.id, far.id]
    assert out["results"][0]["scheduled"] is True
    assert out["results"][0]["similarity"] > out["results"][1]["similarity"]

    with pytest.raises(ToolError, match="not_found"):
        kt.find_related(draft.id)


# --- check_claim -----------------------------------------------------------------


def test_check_claim_exact_match_without_embeddings(mcp_db):
    row = VerifiedClaim(
        claim_key="the panama canal opened in 1914",
        claim_text="The Panama Canal opened in 1914.",
        verdict="VERIFIED",
        citation="https://example.org/canal",
        source_method="DEEP_RESEARCH",
    )
    mcp_db.add(row)
    mcp_db.commit()
    out = kt.check_claim("The Panama Canal opened in 1914!")
    assert out["exact_match"]["id"] == row.id
    assert out["exact_match"]["citation"] == "https://example.org/canal"
    assert out["degraded"] == ["embeddings_disabled"]


def test_check_claim_kb_and_article_claim_matches(mcp_db, voyage, article_factory):
    kb = VerifiedClaim(
        claim_key="the canal was built by the french",
        claim_text="The canal was built by the French.",
        verdict="INACCURATE",
        correction="The US completed it in 1914; the French effort failed.",
        citation="https://example.org/kb",
    )
    mcp_db.add(kb)
    article = article_factory(title="Canal")
    claim = FactCheckClaim(
        subject_type="ARTICLE",
        subject_id=article.id,
        claim_text="The canal opened in 1915",
        verdict="CORRECTED",
        correction="The canal opened in 1914",
        citation="https://example.org/art",
    )
    mcp_db.add(claim)
    mcp_db.commit()
    _chunk(mcp_db, source_type="VERIFIED_CLAIM", source_id=kb.id, content=kb.claim_text,
           embedding=CANAL, meta={"verdict": "INACCURATE"})  # fmt: skip
    _chunk(mcp_db, source_type="ARTICLE_CLAIM", source_id=claim.id, article_id=article.id,
           content=claim.correction, embedding=CANAL, meta={"verdict": "CORRECTED"})  # fmt: skip
    # An article body chunk is not a claim and is not returned.
    _chunk(mcp_db, article_id=article.id, content="canal body text", embedding=CANAL)

    out = kt.check_claim("When did the canal open?", k=5)
    sources = {(m["source"], m["id"]) for m in out["matches"]}
    assert sources == {("knowledge_base", kb.id), ("article_claim", claim.id)}
    by_source = {m["source"]: m for m in out["matches"]}
    assert by_source["knowledge_base"]["verdict"] == "INACCURATE"
    assert by_source["knowledge_base"]["correction"].startswith("The US")
    assert by_source["article_claim"]["claim"] == "The canal opened in 1915"
    assert by_source["article_claim"]["correction"] == "The canal opened in 1914"
    assert by_source["article_claim"]["article"]["id"] == article.id
    assert by_source["knowledge_base"]["similarity"] == pytest.approx(1.0)
    assert out["exact_match"] is None


# --- pipeline_status ------------------------------------------------------------


def test_pipeline_status_in_flight_goals_and_capacity(mcp_db, monkeypatch):
    monkeypatch.setattr(get_settings(), "weekly_plan_max_in_flight", 2)
    g1, g2, done = "daily_series:2026-10-01", "daily_series:2026-10-02", "daily_series:2026-09-01"
    _task(mcp_db, done, "WRITE_ARTICLE", "DONE")
    _task(mcp_db, g1, "WRITE_ARTICLE", "DONE")
    _task(mcp_db, g1, "TRANSLATE", "FAILED", position=1, attempts=3, error="boom")
    _task(mcp_db, g2, "WAIT_GRAMMAR_CHECKED_EN", "WAITING", waiting_for="Grammarly EN",
          next_check_at=datetime.now(UTC) + timedelta(hours=5))  # fmt: skip
    _task(mcp_db, g2, "TRANSLATE", "READY", position=1)

    out = pt.pipeline_status()
    cap = out["capacity"]
    assert cap["in_flight_goals"] == [g1, g2]
    assert cap["at_capacity"] is True and cap["max_in_flight"] == 2
    goals = {g["goal_key"]: g for g in out["goals"]}
    assert set(goals) == {g1, g2}
    assert goals[g1]["failed"][0]["error"] == "boom"
    assert goals[g1]["by_status"] == {"DONE": 1, "FAILED": 1}
    assert goals[g2]["waiting"][0]["waiting_for"] == "Grammarly EN"
    assert goals[g2]["ready"][0]["kind"] == "TRANSLATE"

    one = pt.pipeline_status(goal_key=done)
    assert one["goals"][0]["finished"] is True

    with pytest.raises(ToolError, match="not_found"):
        pt.pipeline_status(goal_key="daily_series:2020-01-01")
    with pytest.raises(ToolError, match="invalid_goal_key"):
        pt.pipeline_status(goal_key="DROP TABLE")


def test_pipeline_status_is_bounded(mcp_db):
    for g in range(12):
        for i in range(30):
            _task(mcp_db, f"daily_series:2026-01-{g + 1:02d}", "TRANSLATE", "FAILED",
                  subject_id=i, error="e" * 2000)  # fmt: skip
    out = pt.pipeline_status()
    assert len(out["goals"]) == 10 and out["goals_omitted"] == 2
    assert result_size(out) <= MAX_RESULT_CHARS
    goal = out["goals"][0]
    assert goal["by_status"] == {"FAILED": 30}
    assert len(goal["failed"]) == pt.ROWS_PER_STATUS
    assert goal["failed"][0]["error"].endswith("[truncated]")

    explained = pt.explain_stuck_goal("daily_series:2026-01-01")
    assert result_size(explained) <= MAX_RESULT_CHARS
    assert explained["truncated"] is True


# --- explain_stuck_goal ------------------------------------------------------------


def test_explain_failed_rows_suggest_retry_or_skip(mcp_db):
    goal = "daily_series:2026-10-03"
    root = _task(mcp_db, goal, "WRITE_ARTICLE", "DONE")
    img = _task(mcp_db, goal, "GENERATE_FEATURE_IMAGE", "FAILED", position=1,
                depends_on=[root.id], attempts=3, error="Invalid prompt payload")  # fmt: skip
    tr = _task(mcp_db, goal, "TRANSLATE", "FAILED", position=2, depends_on=[root.id],
               attempts=3, error="DeepL 503 Service Unavailable")  # fmt: skip
    gram = _task(mcp_db, goal, "GRAMMAR_CHECK_ES", "FAILED", position=3, depends_on=[root.id],
                 attempts=3, error="ValueError: bad FIND line")  # fmt: skip
    after = _task(mcp_db, goal, "APPROVE_ARTICLE", "PENDING", position=4,
                  depends_on=[img.id, tr.id])  # fmt: skip
    last = _task(mcp_db, goal, "SET_PUBLISH_DATE", "PENDING", position=5, depends_on=[after.id])

    out = pt.explain_stuck_goal(goal)
    assert out["verdict"] == "blocked_on_failure"
    assert out["holds_capacity_slot"] is True
    advice = {b["task_id"]: b for b in out["blockers"]}
    assert advice[img.id]["suggested_action"] == "skip"
    assert advice[tr.id]["suggested_action"] == "retry"
    assert advice[gram.id]["suggested_action"] == "investigate"
    assert advice[tr.id]["blocked_dependents"] == [after.id, last.id]
    assert advice[tr.id]["allowed_actions"] == ["retry", "skip"]
    assert "3 failed task(s)" in out["summary"]
    assert "Only the operator" not in advice[tr.id]["rationale"]


def test_explain_says_the_operator_acts_on_a_publish_path_step(mcp_db):
    goal = "daily_series:2026-10-08"
    ig = _task(mcp_db, goal, "SCHEDULE_IG_POST", "FAILED", attempts=3,
               error="Instagram publishing is off")  # fmt: skip
    advice = pt.explain_stuck_goal(goal)["blockers"][0]
    assert advice["task_id"] == ig.id and advice["suggested_action"] == "skip"
    assert "Only the operator can retry / skip a SCHEDULE_IG_POST task" in advice["rationale"]


def test_explain_waiting_finished_and_stalled(mcp_db):
    waiting = "daily_series:2026-10-04"
    _task(mcp_db, waiting, "WAIT_ARTICLE_CLAIMS_RESOLVED", "WAITING",
          waiting_for="2 claims need deep research", attempts=0,
          next_check_at=datetime.now(UTC) - timedelta(hours=1))  # fmt: skip
    out = pt.explain_stuck_goal(waiting)
    assert out["verdict"] == "waiting_on_operator"
    blocker = out["blockers"][0]
    assert blocker["suggested_action"] == "wait"
    assert blocker["waiting_for"] == "2 claims need deep research"
    assert "recheck is due" in blocker["rationale"]
    # #686: the MCP skip tool refuses a wait gate, so the advice says who acts.
    assert "Only the operator can skip a WAIT_ARTICLE_CLAIMS_RESOLVED task" in (
        blocker["rationale"]
    )

    finished = "daily_series:2026-10-05"
    _task(mcp_db, finished, "WRITE_ARTICLE", "DONE")
    _task(mcp_db, finished, "GENERATE_FEATURE_IMAGE", "SKIPPED", position=1)
    out = pt.explain_stuck_goal(finished)
    assert out["verdict"] == "finished" and out["blockers"] == []
    assert out["holds_capacity_slot"] is False

    stalled = "daily_series:2026-10-06"
    done = _task(mcp_db, stalled, "WRITE_ARTICLE", "DONE")
    stuck = _task(mcp_db, stalled, "TRANSLATE", "PENDING", position=1, depends_on=[done.id])
    out = pt.explain_stuck_goal(stalled)
    assert out["verdict"] == "stalled"
    assert out["blockers"][0]["task_id"] == stuck.id
    assert out["blockers"][0]["reason"] == "not_promoted"

    leased = "daily_series:2026-10-07"
    _task(mcp_db, leased, "TRANSLATE", "LEASED", leased_by="driver",
          lease_expires_at=datetime.now(UTC) - timedelta(minutes=5))  # fmt: skip
    out = pt.explain_stuck_goal(leased)
    assert out["verdict"] == "in_progress"
    assert out["blockers"][0]["reason"] == "lease_expired"
    assert out["blockers"][0]["allowed_actions"] == []

    with pytest.raises(ToolError, match="not_found"):
        pt.explain_stuck_goal("daily_series:1999-01-01")


# --- bounds -----------------------------------------------------------------------


def test_bound_result_caps_any_payload():
    payload = {
        "items": [{"text": "y" * 5000, "n": i} for i in range(50)],
        "small": ["a"] * 5000,
    }
    out = bound_result(payload)
    assert result_size(out) <= MAX_RESULT_CHARS
    assert out["truncated"] is True
    json.dumps(out)

    small = bound_result({"a": 1})
    assert small == {"a": 1, "truncated": False}
