"""MCP pipeline write tools (#641): get_deep_research_batch,
submit_fact_check_verdicts, submit_research, retry_agent_task, skip_agent_task.

Most tests call the tool functions directly (they open ``database.SessionLocal``,
patched to the test engine); the /mcp tests check registration, scopes, the
token name on the action row and the audit redaction. Each tool must call the
same service function as its HTTP twin, and every submit must converge on replay.
"""

import json
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx2
import pytest
from app.api import agent_tasks as agent_tasks_api
from app.api import dashboard as dashboard_api
from app.core import database
from app.main import app
from app.mcp.auth import REQUIRED_SCOPE_ATTR, generate_token, hash_token
from app.mcp.server import MCP_PATH, PIPELINE_TOOLS, mcp_http, mcp_server
from app.mcp.tools import writes as wt
from app.mcp.tools.bounds import MAX_RESULT_CHARS, result_size
from app.models.agent_task import AgentTask, AgentTaskActionLog
from app.models.enums import (
    AgentTaskAction,
    AgentTaskKind,
    AgentTaskStatus,
    FactCheckVerdict,
    McpScope,
)
from app.models.fact_check import FactCheckClaim
from app.models.mcp_token import McpToken
from app.models.mcp_tool_call import McpToolCall
from app.models.research import Research
from app.services import agent_task_driver, fact_check, research_intake
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.orm import sessionmaker

BASE = "http://localhost"
PIPELINE_TOOL_NAMES = {name for name, *_ in PIPELINE_TOOLS}
GOAL = "daily_series:2026-10-09"


@pytest.fixture
def mcp_db(test_engine, test_db):
    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with patch.object(database, "SessionLocal", factory):
        yield test_db


@pytest.fixture
def no_reindex(monkeypatch):
    """Record the off-request reindex instead of starting its thread (#689)."""
    calls = []
    monkeypatch.setattr(
        wt, "_reindex_off_request", lambda ids, verified=False: calls.append((ids, verified))
    )
    return calls


def _claim(db, subject_type="ARTICLE", subject_id=1, text="The canal opened in 1914.", **extra):
    row = FactCheckClaim(
        subject_type=subject_type,
        subject_id=subject_id,
        claim_text=text,
        claim_type="DATE",
        verdict=extra.pop("verdict", FactCheckVerdict.NEEDS_DEEP_RESEARCH.value),
        **extra,
    )
    db.add(row)
    db.commit()
    return row


def _task(db, status, *, kind=AgentTaskKind.GENERATE_FEATURE_IMAGE, position=0, depends_on=None,
          attempts=0):  # fmt: skip
    task = AgentTask(
        goal_key=GOAL,
        kind=kind,
        subject_type="ARTICLE",
        subject_id=position + 1,
        status=status,
        position=position,
        depends_on=depends_on,
        attempts=attempts,
        error="Gemini 429" if status == AgentTaskStatus.FAILED else None,
    )
    db.add(task)
    db.commit()
    return task


def _valid(content):
    return {"valid": True, "word_count": 4500, "content": content, "checks": {"word_count": True}}


# --- Registration ---------------------------------------------------------------------


def test_pipeline_tools_are_registered_with_scope_and_honest_hints():
    tools = {tool.name: tool for tool in mcp_server._tool_manager.list_tools()}
    assert PIPELINE_TOOL_NAMES == {
        "get_deep_research_batch",
        "submit_fact_check_verdicts",
        "submit_research",
        "retry_agent_task",
        "skip_agent_task",
    }
    for name in PIPELINE_TOOL_NAMES:
        tool = tools[name]
        assert getattr(tool.fn, REQUIRED_SCOPE_ATTR) is McpScope.PIPELINE, name
        assert "Example" in tool.description and "Do not use" in tool.description, name
        assert tool.annotations.idempotent_hint is True, name
    assert tools["get_deep_research_batch"].annotations.read_only_hint is True
    for name in PIPELINE_TOOL_NAMES - {"get_deep_research_batch"}:
        assert tools[name].annotations.read_only_hint is False, name
    destructive = {n for n in PIPELINE_TOOL_NAMES if tools[n].annotations.destructive_hint}
    assert destructive == {"submit_research", "skip_agent_task"}


def test_pipeline_tool_inputs_cannot_carry_a_body_as_a_list_of_short_strings():
    """redact_args stores lists of short strings verbatim (#639): the only list
    input is claim_ids, integers capped at one batch."""
    tools = {tool.name: tool for tool in mcp_server._tool_manager.list_tools()}
    for name in PIPELINE_TOOL_NAMES:
        for arg, schema in tools[name].parameters["properties"].items():
            for variant in schema.get("anyOf", [schema]):
                if variant.get("type") == "array":
                    assert variant["items"].get("type") == "integer", (name, arg)
                    assert variant.get("maxItems", 99) <= 25, (name, arg)


def test_each_tool_uses_the_http_twins_service_function():
    """Structural half of 'no duplicated logic': the routes import the very
    function objects the tools reach through their modules."""
    assert dashboard_api.deep_research_batch is fact_check.deep_research_batch
    assert dashboard_api.submit_deep_research_verdicts is fact_check.submit_deep_research_verdicts
    assert dashboard_api.submit_research is research_intake.submit_research
    assert agent_tasks_api.apply_task_action is agent_task_driver.apply_task_action
    assert wt.apply_task_action is agent_task_driver.apply_task_action


def test_tools_call_the_service_functions(mcp_db, no_reindex):
    """Behavioural half: patch each service and see the tool go through it."""
    batch = {"batch_id": "20261011_120000", "claim_ids": [1], "pending_total": 1,
             "prompt_text": "REF"}  # fmt: skip
    with patch.object(fact_check, "deep_research_batch", return_value=batch) as gen:
        assert wt.get_deep_research_batch()["claim_ids"] == [1]
    gen.assert_called_once()
    verdicts = {"written": 1, "skipped": [], "missing_claim_ids": [], "kb_upserts": 0,
                "article_ids": [], "replayed": False, "rejected_reason": None}  # fmt: skip
    with patch.object(fact_check, "submit_deep_research_verdicts", return_value=verdicts) as sub:
        assert wt.submit_fact_check_verdicts(claim_ids=[1], document="VERDICT: 1")["written"] == 1
    sub.assert_called_once()
    intake = research_intake.ResearchIntakeResult(
        accepted=True, action="inserted", suggestion_id=1, suggestion_title="t", word_count=1
    )
    with patch.object(research_intake, "submit_research", return_value=intake) as res:
        assert wt.submit_research(suggestion_id=1, markdown="x")["action"] == "inserted"
    res.assert_called_once()


# --- Through /mcp ---------------------------------------------------------------------


def _mint(db, *, name="supervisor", scopes=("pipeline",)):
    token = generate_token()
    db.add(McpToken(name=name, token_hash=hash_token(token), scopes=list(scopes)))
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


async def test_read_token_is_denied_every_pipeline_tool(mcp_db):
    token = _mint(mcp_db, name="reader", scopes=("read",))
    task = _task(mcp_db, AgentTaskStatus.FAILED)
    calls = {
        "get_deep_research_batch": {},
        "submit_fact_check_verdicts": {"claim_ids": [1], "document": "VERDICT: 1 | X | - | -"},
        "submit_research": {"suggestion_id": 1, "markdown": "# x"},
        "retry_agent_task": {"task_id": task.id, "reason": "r"},
        "skip_agent_task": {"task_id": task.id, "reason": "r"},
    }
    async with _client(token) as client:
        results = {name: await client.call_tool(name, args) for name, args in calls.items()}
    for name, result in results.items():
        assert result.is_error, name
        assert "insufficient_scope" in result.content[0].text, name
    mcp_db.expire_all()
    assert mcp_db.get(AgentTask, task.id).status == AgentTaskStatus.FAILED.value
    outcomes = {r.tool: r.outcome for r in mcp_db.query(McpToolCall).all()}
    assert outcomes == dict.fromkeys(calls, "DENIED")


async def test_over_mcp_actions_store_the_token_name_and_bodies_stay_hashed(mcp_db, no_reindex):
    claim = _claim(mcp_db)
    task = _task(mcp_db, AgentTaskStatus.FAILED, attempts=3)
    token = _mint(mcp_db)
    document = (
        "## Verdicts\n"
        f"VERDICT: {claim.id} | VERIFIED | - | Castillero Calvo, Historia General, 2004\n\n"
        + "## Notes\n"
        + "Long discussion. " * 40
    )
    async with _client(token) as client:
        nothing = await client.call_tool("get_deep_research_batch", {"subject_type": "RESEARCH"})
        retried = await client.call_tool(
            "retry_agent_task", {"task_id": task.id, "reason": "quota reset"}
        )
        submitted = await client.call_tool(
            "submit_fact_check_verdicts", {"claim_ids": [claim.id], "document": document}
        )
    assert not nothing.is_error and nothing.structured_content["nothing_pending"] is True
    assert retried.structured_content["task"]["status"] == AgentTaskStatus.READY.value
    assert retried.structured_content["last_action"]["actor"] == "mcp:supervisor"
    assert submitted.structured_content["written"] == 1

    mcp_db.expire_all()
    row = mcp_db.query(AgentTaskActionLog).one()
    assert (row.action, row.previous_status, row.reason, row.actor) == (
        "retry", "FAILED", "quota reset", "mcp:supervisor"
    )  # fmt: skip
    audit = mcp_db.query(McpToolCall).filter(McpToolCall.tool == "submit_fact_check_verdicts").one()
    assert audit.outcome == "OK"
    assert set(audit.args_redacted["document"]) == {"len", "sha256"}
    assert "Castillero" not in json.dumps(audit.args_redacted)


# --- get_deep_research_batch --------------------------------------------------------------


def test_batch_is_the_oldest_25_and_nothing_pending_is_structured(mcp_db):
    assert wt.get_deep_research_batch()["nothing_pending"] is True
    ids = [_claim(mcp_db, subject_id=7, text=f"Claim {i}").id for i in range(27)]
    _claim(mcp_db, subject_id=8, verdict=FactCheckVerdict.VERIFIED.value)

    batch = wt.get_deep_research_batch()
    assert batch["nothing_pending"] is False
    assert batch["pending_total"] == 27
    assert batch["claim_ids"] == ids[:25]
    assert batch["claim_count"] == 25
    assert f"REF: fact_check_{batch['batch_id']}" in batch["prompt_text"]
    scoped = wt.get_deep_research_batch(subject_type="ARTICLE", subject_id=8)
    assert scoped["nothing_pending"] is True
    # Stateless: asking again changes nothing and returns the same claims.
    assert wt.get_deep_research_batch()["claim_ids"] == ids[:25]


def test_batch_scope_errors_come_from_the_shared_service(mcp_db):
    with pytest.raises(ToolError, match="invalid_argument: subject_id requires subject_type"):
        wt.get_deep_research_batch(subject_id=3)


def test_batch_shrinks_instead_of_cutting_the_prompt(mcp_db):
    for i in range(25):
        _claim(mcp_db, text=f"{i} " + "a long claim sentence " * 60)
    batch = wt.get_deep_research_batch()
    assert batch["limited_by_size"] is True
    assert 1 <= batch["claim_count"] < 25
    assert batch["truncated"] is False
    assert result_size(batch) <= MAX_RESULT_CHARS
    for cid in batch["claim_ids"]:
        assert f"CLAIM {cid} " in batch["prompt_text"]


# --- submit_fact_check_verdicts -------------------------------------------------------------


def test_verdicts_write_reindex_and_replay_converges(mcp_db, no_reindex, article_factory):
    article = article_factory()
    a = _claim(mcp_db, subject_id=article.id)
    b = _claim(mcp_db, subject_id=article.id, text="Second")
    doc = (
        f"VERDICT: {a.id} | INACCURATE | It opened in 1914 | Smith, Canal, 2001\n"
        f"VERDICT: 999999 | VERIFIED | - | Out of batch, 2000\n"
    )
    first = wt.submit_fact_check_verdicts(
        claim_ids=[a.id, b.id], document=doc, batch_id="20261011_120000"
    )
    assert first["written"] == 1
    assert first["missing_claim_ids"] == [b.id]
    assert first["skipped"] == [{"claim_id": 999999, "reason": "not in this batch's claim_ids"}]
    assert first["replayed"] is False
    assert first["batch_id"] == "20261011_120000"
    assert no_reindex == [([article.id], True)]

    second = wt.submit_fact_check_verdicts(claim_ids=[a.id], document=doc.splitlines()[0])
    assert second["replayed"] is True
    assert second["written"] == 0
    assert second["skipped"][0]["reason"].startswith(
        "claim is no longer pending (verdict=INACCURATE"
    )
    mcp_db.expire_all()
    row = mcp_db.get(FactCheckClaim, a.id)
    assert (row.verdict, row.correction, row.checked_via) == (
        "INACCURATE",
        "It opened in 1914",
        "DEEP_RESEARCH",
    )
    assert mcp_db.get(FactCheckClaim, b.id).verdict == FactCheckVerdict.NEEDS_DEEP_RESEARCH.value


def test_verdicts_return_before_the_claims_reindex_finishes(mcp_db, monkeypatch, article_factory):
    """#689: the Voyage reindex runs off the call's path, not before it returns."""
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def slow_reindex(ids, verified=False):
        calls.append((ids, verified, threading.current_thread().name))
        started.set()
        release.wait(5)
        done.set()

    monkeypatch.setattr(wt, "reindex_claims", slow_reindex)
    article = article_factory()
    a = _claim(mcp_db, subject_id=article.id)
    doc = f"VERDICT: {a.id} | VERIFIED | - | Smith, Canal, 2001\n"
    try:
        result = wt.submit_fact_check_verdicts(claim_ids=[a.id], document=doc)
        assert result["written"] == 1
        assert started.wait(5)  # the reindex is running...
        assert not done.is_set()  # ...and the call returned without waiting for it
    finally:
        release.set()
    assert done.wait(5)
    assert calls == [([article.id], True, "mcp-claims-reindex")]


def test_failed_run_document_is_rejected_and_writes_nothing(mcp_db, no_reindex):
    a = _claim(mcp_db)
    b = _claim(mcp_db, text="Second")
    doc = f"VERDICT: {a.id} | UNVERIFIABLE | - | -\nVERDICT: {b.id} | UNVERIFIABLE | - | -\n"
    with pytest.raises(ToolError, match="rejected: Verdict document rejected: all 2 verdicts"):
        wt.submit_fact_check_verdicts(claim_ids=[a.id, b.id], document=doc)
    with pytest.raises(ToolError, match="rejected: No VERDICT lines"):
        wt.submit_fact_check_verdicts(claim_ids=[a.id], document="just prose")
    mcp_db.expire_all()
    assert mcp_db.get(FactCheckClaim, a.id).verdict == FactCheckVerdict.NEEDS_DEEP_RESEARCH.value
    assert no_reindex == []


def test_http_verdict_upload_still_400s_a_failed_run(test_client, admin_token, test_db):
    a = _claim(test_db)
    response = test_client.post(
        "/api/v1/admin/dashboard/fact-check/deep-research/verdicts",
        json={"content": f"VERDICT: {a.id} | UNVERIFIABLE | - | -", "claim_ids": [a.id]},
    )
    assert response.status_code == 400
    assert "uncited UNVERIFIABLE" in response.json()["detail"]
    bad = test_client.post(
        "/api/v1/admin/dashboard/fact-check/deep-research/generate-prompt",
        json={"subject_type": "EDU_MATERIAL"},
    )
    assert bad.status_code == 400


# --- submit_research ---------------------------------------------------------------------------


def test_submit_research_inserts_then_replay_converges(mcp_db, suggestion_factory):
    s = suggestion_factory(sub_topics=["A: a", "B: b"])
    body = "# Darien\n\n" + "word " * 4100 + "\n\n## Sources\n- X"
    with patch.object(
        research_intake, "validate_research_document", side_effect=lambda c, _s: _valid(c)
    ) as v:
        first = wt.submit_research(suggestion_id=s.id, markdown=body)
        second = wt.submit_research(suggestion_id=s.id, markdown=body + "\n")
    assert first["accepted"] is True and first["action"] == "inserted"
    assert first["status"] == "PENDING"
    assert second["action"] == "replayed" and second["replayed"] is True
    assert second["research_id"] == first["research_id"]
    assert v.call_count == 1  # a replay spends no validation tokens
    assert v.call_args.args[1].sub_topics == ["A: a", "B: b"]
    mcp_db.expire_all()
    assert mcp_db.query(Research).filter(Research.suggestion_id == s.id).count() == 1


def test_replay_recognises_the_auto_generated_references(mcp_db, suggestion_factory):
    s = suggestion_factory()
    body = "# Darien\n\nInline (Smith 2001)."

    def with_refs(content, _snapshot):
        return _valid(content.rstrip() + "\n\n---\n\n## References\n- Smith 2001")

    with patch.object(research_intake, "validate_research_document", side_effect=with_refs):
        first = wt.submit_research(suggestion_id=s.id, markdown=body)
        second = wt.submit_research(suggestion_id=s.id, markdown=body)
    assert first["references_generated"] is True
    assert second["action"] == "replayed"


def test_a_different_body_conflicts_unless_replace_and_never_over_approved(
    mcp_db, suggestion_factory, research_factory
):
    s = suggestion_factory()
    original = research_factory(suggestion=s, content="OLD BODY", status="PENDING")
    with patch.object(
        research_intake, "validate_research_document", side_effect=lambda c, _s: _valid(c)
    ) as v:
        with pytest.raises(ToolError, match=f"conflict: Research {original.id} already exists"):
            wt.submit_research(suggestion_id=s.id, markdown="NEW BODY")
        assert v.call_count == 0
        replaced = wt.submit_research(suggestion_id=s.id, markdown="NEW BODY", replace=True)
        assert replaced["action"] == "updated" and replaced["research_id"] == original.id

        mcp_db.expire_all()
        row = mcp_db.get(Research, original.id)
        assert row.content == "NEW BODY"
        row.status = "APPROVED"
        mcp_db.commit()
        with pytest.raises(ToolError, match="conflict: .*already APPROVED") as approved:
            wt.submit_research(suggestion_id=s.id, markdown="THIRD BODY", replace=True)
        assert "force" not in str(approved.value)  # the tool has no force argument
        # The identical body over APPROVED still converges on the MCP path.
        assert wt.submit_research(suggestion_id=s.id, markdown="NEW BODY")["replayed"] is True
    mcp_db.expire_all()
    assert mcp_db.get(Research, original.id).content == "NEW BODY"


def test_submit_research_rejects_failed_checks_and_unknown_suggestion(mcp_db, suggestion_factory):
    s = suggestion_factory()
    failed = {"valid": False, "word_count": 12, "content": "x",
              "checks": {"word_count": False, "references": True, "subtopic_coverage": True}}  # fmt: skip
    with patch.object(research_intake, "validate_research_document", return_value=failed):
        result = wt.submit_research(suggestion_id=s.id, markdown="too short")
    assert result["accepted"] is False and result["action"] == "rejected"
    assert result["checks"]["word_count"] is False
    assert result["research_id"] is None
    mcp_db.expire_all()
    assert mcp_db.query(Research).filter(Research.suggestion_id == s.id).count() == 0
    with pytest.raises(ToolError, match="not_found:"):
        wt.submit_research(suggestion_id=999999, markdown="x")


UPLOAD_URL = "/api/v1/admin/dashboard/research/upload"
SAME_FILE = b"# Body\n\ntext"


def _upload(client, suggestion_id, *, force=False):
    url = UPLOAD_URL + ("?force=true" if force else "")
    files = {"file": ("research.md", SAME_FILE, "text/markdown")}
    return client.post(url, data={"suggestion_id": suggestion_id}, files=files)


def _patched_validator():
    return patch.object(
        research_intake, "validate_research_document", side_effect=lambda c, _s: _valid(c)
    )


def test_http_identical_reupload_over_rejected_goes_back_to_pending(
    test_client, admin_token, test_db, suggestion_factory, research_factory
):
    """The admin route keeps its pre-#641 behaviour: no replay short-circuit;
    any accepted re-upload is re-validated and reset to PENDING."""
    s = suggestion_factory()
    original = research_factory(suggestion=s, content=SAME_FILE.decode(), status="REJECTED")
    with _patched_validator() as v:
        response = _upload(test_client, s.id)
    assert response.status_code == 200
    assert response.json()["research_id"] == original.id
    assert response.json()["status"] == "PENDING"
    assert v.call_count == 1
    test_db.refresh(original)
    assert original.status == "PENDING"


def test_http_identical_reupload_over_approved_needs_force(
    test_client, admin_token, test_db, suggestion_factory, research_factory
):
    s = suggestion_factory()
    original = research_factory(suggestion=s, content=SAME_FILE.decode(), status="APPROVED")
    with _patched_validator():
        blocked = _upload(test_client, s.id)
        assert blocked.status_code == 409
        assert "force=true" in blocked.json()["detail"]
        test_db.refresh(original)
        assert original.status == "APPROVED"
        forced = _upload(test_client, s.id, force=True)
    assert forced.status_code == 200
    assert forced.json()["status"] == "PENDING"
    test_db.refresh(original)
    assert original.status == "PENDING"


def test_same_research_body_only_accepts_the_generated_references_suffix():
    body = "# Darien\n\nText."
    assert research_intake.same_research_body(body + "\n", body)
    assert research_intake.same_research_body(body + "\n\n---\n\n## References\n- X", body)
    assert research_intake.same_research_body(body + "\n\n---\n\n## Sources\n- X", body)
    assert not research_intake.same_research_body(body + "\n\n---\n\n## Epilogue\n", body)
    assert not research_intake.same_research_body(body + " more text", body)
    assert not research_intake.same_research_body(body, "")


# --- retry_agent_task / skip_agent_task -------------------------------------------------


def test_retry_resets_and_a_replay_converges(mcp_db):
    task = _task(mcp_db, AgentTaskStatus.FAILED, attempts=3)
    first = wt.retry_agent_task(task_id=task.id, reason="transient 429")
    assert first["replayed"] is False
    assert (first["task"]["status"], first["task"]["attempts"]) == ("READY", 0)
    assert first["last_action"]["reason"] == "transient 429"

    again = wt.retry_agent_task(task_id=task.id, reason="transient 429")
    assert again["replayed"] is True and again["last_action"]["previous_status"] == "FAILED"

    # Only READY converges: once the driver claimed it, an earlier retry on
    # record does not turn a repeat into a replay; the 409 names the status.
    mcp_db.expire_all()
    mcp_db.get(AgentTask, task.id).status = AgentTaskStatus.LEASED.value
    mcp_db.commit()
    with pytest.raises(ToolError, match="conflict: Task .* is LEASED"):
        wt.retry_agent_task(task_id=task.id, reason="dup")
    assert mcp_db.query(AgentTaskActionLog).count() == 1


def test_skip_promotes_dependents_and_a_replay_converges(mcp_db):
    first = _task(mcp_db, AgentTaskStatus.FAILED, position=0)
    second = _task(mcp_db, AgentTaskStatus.PENDING, kind=AgentTaskKind.RENDER_IG_POST,
                   position=1, depends_on=[first.id])  # fmt: skip
    result = wt.skip_agent_task(task_id=first.id, reason="optional step")
    assert result["task"]["status"] == "SKIPPED"
    assert result["promoted"] == [second.id]
    replay = wt.skip_agent_task(task_id=first.id, reason="optional step")
    assert replay["replayed"] is True and replay["promoted"] == []
    assert mcp_db.query(AgentTaskActionLog).count() == 1


def test_skip_refuses_a_pending_row_ahead_of_an_unfinished_fact_check(mcp_db):
    # #690: the chain is linear and a skip promotes what it unblocks, so
    # skipping GENERATE_SERIES_SECTIONS while ARTICLE_FACT_CHECK is FAILED
    # would promote APPROVE_ARTICLE past the fact-check.
    check = _task(mcp_db, AgentTaskStatus.FAILED, kind=AgentTaskKind.ARTICLE_FACT_CHECK)
    sections = _task(mcp_db, AgentTaskStatus.PENDING, position=1, depends_on=[check.id],
                     kind=AgentTaskKind.GENERATE_SERIES_SECTIONS)  # fmt: skip
    approve = _task(mcp_db, AgentTaskStatus.PENDING, position=2, depends_on=[sections.id],
                    kind=AgentTaskKind.APPROVE_ARTICLE)  # fmt: skip
    with pytest.raises(ToolError, match=r"^refused: skip on a PENDING GENERATE_SERIES_SECTIONS"):
        wt.skip_agent_task(task_id=sections.id, reason="r")
    mcp_db.expire_all()
    assert mcp_db.get(AgentTask, sections.id).status == AgentTaskStatus.PENDING.value
    assert mcp_db.get(AgentTask, approve.id).status == AgentTaskStatus.PENDING.value
    assert mcp_db.query(AgentTaskActionLog).count() == 0


def test_skip_allows_a_pending_row_whose_dependencies_are_satisfied(mcp_db):
    # The "not promoted" row explain_stuck_goal reports stays skippable.
    done = _task(mcp_db, AgentTaskStatus.DONE, kind=AgentTaskKind.WRITE_ARTICLE)
    stuck = _task(mcp_db, AgentTaskStatus.PENDING, position=1, depends_on=[done.id])
    result = wt.skip_agent_task(task_id=stuck.id, reason="not needed")
    assert result["task"]["status"] == AgentTaskStatus.SKIPPED.value


def test_admin_queue_still_skips_a_pending_row_ahead_of_a_failure(
    test_client, admin_token, test_db
):
    failed = _task(test_db, AgentTaskStatus.FAILED, kind=AgentTaskKind.ARTICLE_FACT_CHECK)
    pending = _task(test_db, AgentTaskStatus.PENDING, position=1, depends_on=[failed.id])
    url = f"/api/v1/admin/dashboard/agent-tasks/{pending.id}/action"
    assert test_client.post(url, json={"action": "skip"}).status_code == 200
    test_db.expire_all()
    assert test_db.get(AgentTask, pending.id).status == AgentTaskStatus.SKIPPED.value


def test_task_action_errors(mcp_db):
    leased = _task(mcp_db, AgentTaskStatus.LEASED)
    done = _task(mcp_db, AgentTaskStatus.DONE, position=2)
    with pytest.raises(ToolError, match="conflict: Task .* is LEASED"):
        wt.skip_agent_task(task_id=leased.id, reason="r")
    with pytest.raises(ToolError, match="conflict: Task .* is DONE"):
        wt.retry_agent_task(task_id=done.id, reason="r")
    with pytest.raises(ToolError, match="not_found:"):
        wt.retry_agent_task(task_id=999999, reason="r")
    assert mcp_db.query(AgentTaskActionLog).count() == 0


# --- Refused kinds (#686) ---------------------------------------------------------------

WAIT_GATES = {
    AgentTaskKind.WAIT_RESEARCH_READY,
    AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED,
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN,
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES,
}
# The steps that feed the gates (#690): skipping ARTICLE_FACT_CHECK leaves the
# article with no claims, which WAIT_ARTICLE_CLAIMS_RESOLVED counts as resolved.
CHECK_STEPS = {
    AgentTaskKind.RESEARCH_FACT_CHECK,
    AgentTaskKind.APPLY_RESEARCH_CORRECTIONS,
    AgentTaskKind.ARTICLE_FACT_CHECK,
    AgentTaskKind.APPLY_ARTICLE_CORRECTIONS,
    AgentTaskKind.GRAMMAR_CHECK_EN,
    AgentTaskKind.GRAMMAR_CHECK_ES,
}
PUBLISH_PATH = {
    AgentTaskKind.APPROVE_ARTICLE,
    AgentTaskKind.SET_PUBLISH_DATE,
    AgentTaskKind.APPROVE_IG_POST,
    AgentTaskKind.SCHEDULE_IG_POST,
}
REFUSED = [(kind, "skip") for kind in sorted(WAIT_GATES | CHECK_STEPS)] + [
    (kind, action) for kind in sorted(PUBLISH_PATH) for action in ("retry", "skip")
]


def test_refused_set_is_every_wait_gate_skip_and_every_publish_path_action():
    refused = wt.MCP_REFUSED_TASK_ACTIONS
    assert set(refused) == {k.value for k in WAIT_GATES | CHECK_STEPS | PUBLISH_PATH}
    assert set(refused) <= set(agent_task_driver.KIND_CALLS)
    # Every wait kind the driver knows is a gate the MCP tools cannot skip.
    assert set(agent_task_driver.WAIT_CONDITIONS) == {k.value for k in WAIT_GATES}
    for kind in WAIT_GATES | CHECK_STEPS:
        assert refused[kind.value] == {AgentTaskAction.SKIP}
    for kind in PUBLISH_PATH:
        assert refused[kind.value] == {AgentTaskAction.RETRY, AgentTaskAction.SKIP}


@pytest.mark.parametrize(("kind", "action"), REFUSED)
def test_refused_kinds_error_and_change_nothing(mcp_db, kind, action):
    task = _task(mcp_db, AgentTaskStatus.FAILED, kind=kind, attempts=3)
    tool = wt.retry_agent_task if action == "retry" else wt.skip_agent_task
    with pytest.raises(ToolError, match=rf"^refused: {action} on a {kind.value} task .*admin"):
        tool(task_id=task.id, reason="r")
    mcp_db.expire_all()
    row = mcp_db.get(AgentTask, task.id)
    assert (row.status, row.attempts) == (AgentTaskStatus.FAILED.value, 3)
    assert mcp_db.query(AgentTaskActionLog).count() == 0


def test_refusal_message_names_what_the_token_cannot_do(mcp_db):
    expected = {
        AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED: "skip an operator wait gate",
        AgentTaskKind.ARTICLE_FACT_CHECK: "skip a fact-check, corrections or grammar step",
        AgentTaskKind.APPROVE_ARTICLE: "retry or skip a step that approves",
    }
    for position, (kind, why) in enumerate(expected.items()):
        task = _task(mcp_db, AgentTaskStatus.FAILED, kind=kind, position=position)
        with pytest.raises(ToolError, match=why):
            wt.skip_agent_task(task_id=task.id, reason="r")


@pytest.mark.parametrize("kind", sorted(CHECK_STEPS))
def test_retry_on_a_check_step_still_reruns_it(mcp_db, kind):
    task = _task(mcp_db, AgentTaskStatus.FAILED, kind=kind, attempts=3)
    result = wt.retry_agent_task(task_id=task.id, reason="transient 529")
    assert (result["task"]["status"], result["task"]["attempts"]) == (
        AgentTaskStatus.READY.value, 0
    )  # fmt: skip
    assert mcp_db.query(AgentTaskActionLog).count() == 1


@pytest.mark.parametrize("kind", sorted(WAIT_GATES))
def test_retry_on_a_wait_gate_still_means_check_again(mcp_db, kind):
    task = _task(mcp_db, AgentTaskStatus.WAITING, kind=kind)
    result = wt.retry_agent_task(task_id=task.id, reason="operator finished the pass")
    assert result["task"]["status"] == AgentTaskStatus.READY.value
    assert mcp_db.query(AgentTaskActionLog).count() == 1


async def test_over_mcp_a_refused_kind_is_audited_and_writes_nothing(mcp_db):
    gate = _task(mcp_db, AgentTaskStatus.WAITING, kind=AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN)
    approve = _task(mcp_db, AgentTaskStatus.FAILED, kind=AgentTaskKind.APPROVE_ARTICLE,
                    position=1)  # fmt: skip
    token = _mint(mcp_db, name="everything", scopes=[s.value for s in McpScope])
    async with _client(token) as client:
        skipped = await client.call_tool("skip_agent_task", {"task_id": gate.id, "reason": "r"})
        retried = await client.call_tool(
            "retry_agent_task", {"task_id": approve.id, "reason": "r"}
        )
    for result, kind in ((skipped, "WAIT_GRAMMAR_CHECKED_EN"), (retried, "APPROVE_ARTICLE")):
        assert result.is_error
        assert "refused:" in result.content[0].text and kind in result.content[0].text
    mcp_db.expire_all()
    assert mcp_db.get(AgentTask, gate.id).status == AgentTaskStatus.WAITING.value
    assert mcp_db.get(AgentTask, approve.id).status == AgentTaskStatus.FAILED.value
    assert mcp_db.query(AgentTaskActionLog).count() == 0
    audit = {r.tool: (r.outcome, r.error_class) for r in mcp_db.query(McpToolCall).all()}
    assert audit == {
        "skip_agent_task": ("ERROR", "ToolError"),
        "retry_agent_task": ("ERROR", "ToolError"),
    }


def test_admin_queue_still_skips_a_wait_gate_and_retries_a_publish_step(
    test_client, admin_token, test_db
):
    gate = _task(test_db, AgentTaskStatus.WAITING, kind=AgentTaskKind.WAIT_RESEARCH_READY)
    approve = _task(test_db, AgentTaskStatus.FAILED, kind=AgentTaskKind.APPROVE_ARTICLE,
                    position=1)  # fmt: skip
    check = _task(test_db, AgentTaskStatus.FAILED, kind=AgentTaskKind.ARTICLE_FACT_CHECK,
                  position=2)  # fmt: skip
    base = "/api/v1/admin/dashboard/agent-tasks"
    skip = test_client.post(f"{base}/{gate.id}/action", json={"action": "skip"})
    retry = test_client.post(f"{base}/{approve.id}/action", json={"action": "retry"})
    skip_check = test_client.post(f"{base}/{check.id}/action", json={"action": "skip"})
    assert skip.status_code == retry.status_code == skip_check.status_code == 200
    test_db.expire_all()
    assert test_db.get(AgentTask, gate.id).status == AgentTaskStatus.SKIPPED.value
    assert test_db.get(AgentTask, check.id).status == AgentTaskStatus.SKIPPED.value
    assert test_db.get(AgentTask, approve.id).status == AgentTaskStatus.READY.value


def test_http_action_stores_reason_and_admin_email(test_client, admin_token, test_db):
    task = _task(test_db, AgentTaskStatus.WAITING)
    url = f"/api/v1/admin/dashboard/agent-tasks/{task.id}/action"
    first = test_client.post(url, json={"action": "skip", "reason": "done by hand"})
    second = test_client.post(url, json={"action": "skip"})
    assert first.status_code == second.status_code == 200
    assert first.json()["replayed"] is False and second.json()["replayed"] is True
    row = test_db.query(AgentTaskActionLog).one()
    assert (row.action, row.previous_status, row.reason, row.actor) == (
        "skip", "WAITING", "done by hand", "test-admin@example.com"
    )  # fmt: skip
    assert row.created_at <= datetime.now(UTC) + timedelta(seconds=5)
