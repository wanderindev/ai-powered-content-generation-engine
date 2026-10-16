"""MCP approval gates (#642): request_publish / request_unpublish /
request_article_approval / get_approval_status never execute; an admin's
approve runs the action through the same ``article_publishing`` functions as
``PATCH .../publish``, ``.../unpublish`` and ``.../status``.
"""

import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx2
import pytest
from app.api import dashboard as dashboard_api
from app.core import database
from app.core.config import get_settings
from app.main import app
from app.mcp.auth import REQUIRED_SCOPE_ATTR, generate_token, hash_token
from app.mcp.server import MCP_PATH, PUBLISH_REQUEST_TOOLS, mcp_http, mcp_server
from app.mcp.tools import approvals as at
from app.models.article import Article
from app.models.enums import McpScope
from app.models.mcp_approval_request import McpApprovalRequest
from app.models.mcp_token import McpToken
from app.models.mcp_tool_call import McpToolCall
from app.models.media import Media
from app.services import article_publishing, mcp_approvals
from app.services.agent_task_driver import is_driver_call_allowed
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.orm import sessionmaker

BASE = "http://localhost"
APPROVALS_URL = "/api/v1/admin/dashboard/mcp/approvals"
TOOL_NAMES = {name for name, *_ in PUBLISH_REQUEST_TOOLS}


@pytest.fixture
def mcp_db(test_engine, test_db):
    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with patch.object(database, "SessionLocal", factory):
        yield test_db


def _media(db):
    now = datetime.now(UTC)
    media = Media(
        filename="f.jpg",
        original_filename="f.jpg",
        file_path="/static/images/articles/f.jpg",
        file_size=1,
        mime_type="image/jpeg",
        media_type="IMAGE",
        source="GENERATED",
        title="t",
        created_at=now,
        updated_at=now,
    )
    db.add(media)
    db.commit()
    return media


def _ready(db, article_factory, *, status="APPROVED", published_at="live", image=True):
    """An article that meets every publish precondition (unless told otherwise)."""
    _ready.n = getattr(_ready, "n", 0) + 1
    extra = {} if published_at == "live" else {"published_at": published_at}
    article = article_factory(title=f"Approval Article {_ready.n}", status=status, **extra)
    article.content_es = "Contenido"
    if image:
        article.feature_image_id = _media(db).id
    db.commit()
    return article


def _snapshot(db, article_id):
    db.expire_all()
    a = db.get(Article, article_id)
    return (a.status, a.published_at, a.approved_at)


def _request(action, article_id, rationale="ready", **params):
    db = database.SessionLocal()
    try:
        return mcp_approvals.create_request(
            db,
            action=action,
            article_id=article_id,
            params=params,
            rationale=rationale,
            token_id=None,
            token_name="supervisor",
        ).request.id
    finally:
        db.close()


# --- Registration ---------------------------------------------------------------------


def test_tools_are_registered_with_the_publish_request_scope_and_hints():
    tools = {tool.name: tool for tool in mcp_server._tool_manager.list_tools()}
    assert TOOL_NAMES == {
        "request_publish",
        "request_unpublish",
        "request_article_approval",
        "get_approval_status",
    }
    for name in TOOL_NAMES:
        tool = tools[name]
        assert getattr(tool.fn, REQUIRED_SCOPE_ATTR) is McpScope.PUBLISH_REQUEST, name
        assert "Example" in tool.description and "Do not use" in tool.description, name
        assert tool.annotations.destructive_hint is not True, name
        props = tool.parameters["properties"]
        if "rationale" in props:
            assert props["rationale"]["maxLength"] == mcp_approvals.MAX_RATIONALE_CHARS
    assert tools["get_approval_status"].annotations.read_only_hint is True


def test_routes_and_executor_share_one_implementation():
    """The admin routes and the approval executor reach the same functions."""
    assert dashboard_api.article_publishing is article_publishing
    assert mcp_approvals.article_publishing is article_publishing


# --- Requests never execute -------------------------------------------------------


def test_request_publish_files_a_pending_request_and_changes_nothing(mcp_db, article_factory):
    article = _ready(mcp_db, article_factory)
    before = _snapshot(mcp_db, article.id)
    with patch.object(article_publishing, "publish_article") as publish:
        result = at.request_publish(article.id, "fact check resolved")
    publish.assert_not_called()
    assert result["status"] == "PENDING" and result["action"] == "PUBLISH"
    assert result["params"] == {"publish_at": None}
    assert result["publish_blockers"] == [] and result["replayed"] is False
    assert _snapshot(mcp_db, article.id) == before


def test_replay_converges_and_different_params_conflict(mcp_db, article_factory):
    article = _ready(mcp_db, article_factory)
    first = at.request_publish(article.id, "go")
    again = at.request_publish(article.id, "go, really")
    assert again["request_id"] == first["request_id"] and again["replayed"] is True
    when = datetime(2026, 11, 2, 12, tzinfo=UTC)
    with pytest.raises(ToolError, match="^conflict: approval request"):
        at.request_publish(article.id, "go", publish_at=when)
    assert mcp_db.query(McpApprovalRequest).count() == 1


def test_noop_requests_conflict_and_unknown_ids_are_not_found(mcp_db, article_factory):
    approved = _ready(mcp_db, article_factory, published_at=None)
    with pytest.raises(ToolError, match="^conflict: the article is already APPROVED"):
        at.request_article_approval(approved.id, "x")
    with pytest.raises(ToolError, match="^conflict: the article is not published"):
        at.request_unpublish(approved.id, "x")
    with pytest.raises(ToolError, match="^not_found"):
        at.request_publish(999999, "x")
    with pytest.raises(ToolError, match="^not_found"):
        at.get_approval_status(999999)


def test_blockers_are_reported_at_request_time(mcp_db, article_factory):
    draft = _ready(mcp_db, article_factory, status="PENDING", published_at=None, image=False)
    result = at.request_publish(draft.id, "x")
    assert result["publish_blockers"] == [
        "Article must be APPROVED before publishing",
        "Article must have a feature image before publishing",
    ]


# --- Admin decisions --------------------------------------------------------------


def test_approve_publish_executes_through_the_shared_service(
    mcp_db, article_factory, test_client, admin_token
):
    article = _ready(mcp_db, article_factory)
    when = datetime(2026, 11, 2, 12, tzinfo=UTC)
    rid = at.request_publish(article.id, "go", publish_at=when)["request_id"]
    real = article_publishing.publish_article
    with patch.object(article_publishing, "publish_article", side_effect=real) as spy:
        resp = test_client.post(f"{APPROVALS_URL}/{rid}/approve")
    assert resp.status_code == 200, resp.text
    spy.assert_called_once()
    body = resp.json()
    assert body["status"] == "EXECUTED"
    assert body["decided_by"] and body["decided_at"]
    assert body["result"]["published_at"].startswith("2026-11-02T12:00:00")
    mcp_db.expire_all()
    assert mcp_db.get(Article, article.id).published_at == when
    status = at.get_approval_status(rid)
    assert status["status"] == "EXECUTED" and status["decided_by"] == body["decided_by"]


def test_failed_precondition_records_failed_and_leaves_the_article_untouched(
    mcp_db, article_factory, test_client, admin_token
):
    article = _ready(mcp_db, article_factory)
    rid = at.request_publish(article.id, "go")["request_id"]
    # The article loses its Spanish body between request and decision.
    article.content_es = ""
    mcp_db.commit()
    before = _snapshot(mcp_db, article.id)
    resp = test_client.post(f"{APPROVALS_URL}/{rid}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "FAILED"
    assert resp.json()["result"] == {"error": "Article must have Spanish content before publishing"}
    assert _snapshot(mcp_db, article.id) == before


def test_a_failure_after_a_partial_write_rolls_the_article_back(
    mcp_db, article_factory, test_client, admin_token
):
    """Nothing half-applies: a write made before the failure is undone."""
    article = _ready(mcp_db, article_factory)
    rid = at.request_publish(article.id, "go")["request_id"]
    before = _snapshot(mcp_db, article.id)

    def half_then_fail(db, article_id, published_at=None):
        db.get(Article, article_id).published_at = datetime(2030, 1, 1, tzinfo=UTC)
        db.flush()
        raise article_publishing.PublishPreconditionError("late failure")

    with patch.object(article_publishing, "publish_article", side_effect=half_then_fail):
        resp = test_client.post(f"{APPROVALS_URL}/{rid}/approve")
    assert resp.json()["status"] == "FAILED"
    assert resp.json()["result"] == {"error": "late failure"}
    assert _snapshot(mcp_db, article.id) == before


def test_approve_article_and_unpublish_execute(mcp_db, article_factory, test_client, admin_token):
    draft = _ready(mcp_db, article_factory, status="PENDING", published_at=None)
    live = _ready(mcp_db, article_factory)
    approve_id = at.request_article_approval(draft.id, "checks done")["request_id"]
    unpublish_id = at.request_unpublish(live.id, "error found")["request_id"]
    assert test_client.post(f"{APPROVALS_URL}/{approve_id}/approve").json()["status"] == (
        "EXECUTED"
    )
    assert test_client.post(f"{APPROVALS_URL}/{unpublish_id}/approve").json()["status"] == (
        "EXECUTED"
    )
    mcp_db.expire_all()
    assert mcp_db.get(Article, draft.id).status == "APPROVED"
    assert mcp_db.get(Article, draft.id).approved_at is not None
    assert mcp_db.get(Article, live.id).published_at is None


def test_reject_runs_nothing_and_decisions_converge(
    mcp_db, article_factory, test_client, admin_token
):
    article = _ready(mcp_db, article_factory)
    rid = at.request_publish(article.id, "go")["request_id"]
    before = _snapshot(mcp_db, article.id)
    rejected = test_client.post(f"{APPROVALS_URL}/{rid}/reject")
    assert rejected.json()["status"] == "REJECTED"
    assert test_client.post(f"{APPROVALS_URL}/{rid}/reject").json()["replayed"] is True
    assert test_client.post(f"{APPROVALS_URL}/{rid}/approve").status_code == 409
    assert _snapshot(mcp_db, article.id) == before
    # A rejected request no longer blocks a new one.
    assert at.request_publish(article.id, "go")["request_id"] != rid

    listing = test_client.get(APPROVALS_URL, params={"status": "PENDING"}).json()
    assert listing["total"] == 1 and listing["items"][0]["article_title"] == article.title
    assert test_client.post(f"{APPROVALS_URL}/999999/approve").status_code == 404


def test_double_approve_executes_once(mcp_db, article_factory, test_engine):
    """Two admins approving at once: the row lock lets one execute."""
    article = _ready(mcp_db, article_factory)
    rid = _request(mcp_approvals.McpApprovalAction.PUBLISH, article.id, publish_at=None)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    real = article_publishing.publish_article
    calls = []
    barrier = threading.Barrier(2)

    def slow_publish(db, article_id, published_at=None):
        calls.append(article_id)
        threading.Event().wait(0.3)
        return real(db, article_id, published_at)

    outcomes = []

    def approve(email):
        db = factory()
        try:
            barrier.wait()
            outcomes.append(mcp_approvals.decide(db, rid, approve=True, admin_email=email))
        finally:
            db.close()

    with patch.object(article_publishing, "publish_article", side_effect=slow_publish):
        threads = [threading.Thread(target=approve, args=(e,)) for e in ("a@x.com", "b@x.com")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    assert len(calls) == 1
    assert sorted(o.replayed for o in outcomes) == [False, True]


def test_admin_approval_routes_are_not_on_the_driver_surface(test_client, monkeypatch):
    assert not is_driver_call_allowed("GET", APPROVALS_URL)
    assert not is_driver_call_allowed("POST", f"{APPROVALS_URL}/1/approve")
    monkeypatch.setattr(get_settings(), "agent_driver_token", "driver-secret")
    test_client.headers["Authorization"] = "Bearer driver-secret"
    assert test_client.get(APPROVALS_URL).status_code == 401
    assert test_client.post(f"{APPROVALS_URL}/1/approve").status_code == 401


# --- Over /mcp ------------------------------------------------------------------------


def _mint(db, *, name="supervisor", scopes=("publish_request",)):
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


async def test_read_and_pipeline_tokens_are_denied(mcp_db, article_factory):
    article = _ready(mcp_db, article_factory)
    token = _mint(mcp_db, name="worker", scopes=("read", "pipeline"))
    calls = {
        "request_publish": {"article_id": article.id, "rationale": "r"},
        "request_unpublish": {"article_id": article.id, "rationale": "r"},
        "request_article_approval": {"article_id": article.id, "rationale": "r"},
        "get_approval_status": {"request_id": 1},
    }
    async with _client(token) as client:
        results = {name: await client.call_tool(name, args) for name, args in calls.items()}
    for name, result in results.items():
        assert result.is_error and "insufficient_scope" in result.content[0].text, name
    assert mcp_db.query(McpApprovalRequest).count() == 0


async def test_over_mcp_the_request_names_the_token_and_replays(mcp_db, article_factory):
    article = _ready(mcp_db, article_factory)
    token = _mint(mcp_db)
    before = _snapshot(mcp_db, article.id)
    async with _client(token) as client:
        first = await client.call_tool(
            "request_unpublish", {"article_id": article.id, "rationale": "error found"}
        )
        again = await client.call_tool(
            "request_unpublish", {"article_id": article.id, "rationale": "error found"}
        )
        status = await client.call_tool(
            "get_approval_status", {"request_id": first.structured_content["request_id"]}
        )
    assert first.structured_content["requested_by"] == "supervisor"
    assert again.structured_content["replayed"] is True
    assert status.structured_content["status"] == "PENDING"
    assert _snapshot(mcp_db, article.id) == before
    row = mcp_db.query(McpApprovalRequest).one()
    assert row.requested_by_token_id == mcp_db.query(McpToken).one().id
    assert {r.outcome for r in mcp_db.query(McpToolCall).all()} == {"OK"}


def _args_for(schema, article_id):
    """Plausible arguments for any tool, aimed at ``article_id``."""
    args = {}
    for name in schema.get("required", []):
        prop = schema["properties"][name]
        variant = next((v for v in prop.get("anyOf", [prop]) if v.get("type") != "null"), prop)
        kind = variant.get("type")
        if "enum" in variant:
            args[name] = variant["enum"][0]
        elif kind == "integer":
            args[name] = article_id
        elif kind == "array":
            args[name] = [article_id]
        elif kind == "boolean":
            args[name] = True
        else:
            args[name] = "VERDICT: 1 | VERIFIED | - | src"
    return args


async def test_no_mcp_path_can_publish_unpublish_or_approve(mcp_db, article_factory):
    """Every registered tool, called by a token holding every scope against
    articles that a publish / unpublish / approve would change, leaves them
    untouched and executes no request."""
    ready = _ready(mcp_db, article_factory, published_at=None)
    live = _ready(mcp_db, article_factory)
    draft = _ready(mcp_db, article_factory, status="PENDING", published_at=None)
    targets = (ready.id, live.id, draft.id)
    before = {aid: _snapshot(mcp_db, aid) for aid in targets}
    token = _mint(mcp_db, name="all", scopes=("read", "pipeline", "publish_request"))
    tools = mcp_server._tool_manager.list_tools()
    assert TOOL_NAMES <= {t.name for t in tools}
    with (
        patch("app.services.research_common.validate_research_document",
              return_value={"valid": False, "word_count": 1, "content": "x",
                            "checks": {"word_count": False}}),
        patch("app.mcp.tools.writes.reindex_claims", lambda *a, **k: None),
    ):  # fmt: skip
        async with _client(token) as client:
            for tool in tools:
                for aid in targets:
                    await client.call_tool(tool.name, _args_for(tool.parameters, aid))
    for aid in targets:
        assert _snapshot(mcp_db, aid) == before[aid], aid
    statuses = {r.status for r in mcp_db.query(McpApprovalRequest).all()}
    assert statuses <= {"PENDING"}
