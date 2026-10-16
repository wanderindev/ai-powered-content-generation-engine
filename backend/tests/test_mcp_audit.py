"""MCP tool-call audit log (#639): the wrapper, the redaction, the admin read endpoint.

End-to-end tests drive the real ``app`` with the SDK ``Client`` (as in
``test_mcp_server.py``). Probe tools are registered on the live ``mcp_server``
through ``pic_tool`` -- the production registration path -- and removed again
after each test, so the audit wrapper is exercised exactly as deployed.
"""

import functools
import json
import math
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import anyio
import anyio.from_thread
import anyio.to_thread
import httpx2
import pytest
from app.core import database
from app.core.observability import scrub_pii_event
from app.main import app
from app.mcp import audit
from app.mcp.audit import AUDITED_ATTR, MAX_INLINE_STRING, is_credential_key, redact_args
from app.mcp.auth import (
    REQUIRED_SCOPE_ATTR,
    ScopeDeniedError,
    generate_token,
    hash_token,
    require_scope,
)
from app.mcp.server import MCP_PATH, mcp_http, mcp_server, pic_tool
from app.mcp.server import _new_server as new_mcp_server
from app.models.enums import McpCallOutcome, McpScope
from app.models.mcp_token import McpToken
from app.models.mcp_tool_call import McpToolCall
from app.services.agent_task_driver import is_driver_call_allowed
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel
from sentry_sdk.consts import DEFAULT_OPTIONS
from sentry_sdk.utils import event_from_exception
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

BASE = "http://localhost"
CALLS_URL = "/api/v1/admin/dashboard/mcp/calls"

# A realistic article body: far longer than MAX_INLINE_STRING, with a
# distinctive sentence to search for in the stored row.
MARKER = "The Spanish crown fortified Portobelo after Drake's raid of 1596"
ARTICLE_BODY = ("## Portobelo\n\n" + MARKER + ". " + "Lorem ipsum dolor sit amet. " * 400).strip()


@pytest.fixture
def mcp_db(test_engine, test_db):
    """Point every ``SessionLocal()`` (verifier, audit writer) at the test DB."""
    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with patch.object(database, "SessionLocal", factory):
        yield test_db


@pytest.fixture
def probe_tools():
    """Register probe tools on the live server through ``pic_tool``; remove them after."""

    @pic_tool(mcp_server, McpScope.READ, name="audit_probe_echo", description="probe")
    async def audit_probe_echo(body: str, slug: str = "", password: str = "") -> dict:
        return {"chars": len(body)}

    @pic_tool(mcp_server, McpScope.PIPELINE, name="audit_probe_crash", description="probe")
    def audit_probe_crash(research_id: int) -> dict:
        raise ValueError("boom with secret details")

    @pic_tool(mcp_server, McpScope.READ, name="audit_probe_refuse", description="probe")
    def audit_probe_refuse() -> dict:
        raise ToolError("not found")

    @pic_tool(mcp_server, McpScope.READ, name="audit_probe_sync_echo", description="probe")
    def audit_probe_sync_echo(body: str) -> dict:
        return {"chars": len(body)}

    @pic_tool(mcp_server, McpScope.READ, name="audit_probe_sync_body_crash", description="probe")
    def audit_probe_sync_body_crash(body: str) -> dict:
        raise ValueError("crash")

    @pic_tool(mcp_server, McpScope.READ, name="audit_probe_async_body_crash", description="probe")
    async def audit_probe_async_body_crash(body: str) -> dict:
        raise ValueError("crash")

    names = (
        "audit_probe_echo",
        "audit_probe_crash",
        "audit_probe_refuse",
        "audit_probe_sync_echo",
        "audit_probe_sync_body_crash",
        "audit_probe_async_body_crash",
    )
    try:
        yield names
    finally:
        for name in names:
            mcp_server._tool_manager.remove_tool(name)


def _mint(db, *, name="agent", scopes=("read",)) -> tuple[str, int]:
    token = generate_token()
    row = McpToken(name=name, token_hash=hash_token(token), scopes=list(scopes))
    db.add(row)
    db.commit()
    return token, row.id


@asynccontextmanager
async def _mcp_client(token: str):
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


def _rows(db) -> list[McpToolCall]:
    db.expire_all()
    return db.query(McpToolCall).order_by(McpToolCall.id).all()


def _raw_table_text(db) -> str:
    """Every column of every audit row, as Postgres renders it."""
    return "\n".join(db.execute(text("SELECT t::text FROM mcp_tool_calls t")).scalars())


# --- The wrapper, end to end ---------------------------------------------------


async def test_ok_call_is_logged(mcp_db):
    token, token_id = _mint(mcp_db, name="operator-laptop")
    async with _mcp_client(token) as client:
        result = await client.call_tool("ping", {})
    assert not result.is_error

    [row] = _rows(mcp_db)
    assert row.tool == "ping"
    assert row.token_id == token_id
    assert row.token_name == "operator-laptop"
    assert row.required_scope == McpScope.READ.value
    assert row.outcome == McpCallOutcome.OK.value
    assert row.error_class is None
    assert row.args_redacted == {}
    assert row.duration_ms >= 0
    assert row.created_at is not None


async def test_denied_call_is_logged(mcp_db):
    token, token_id = _mint(mcp_db, scopes=("pipeline",))
    async with _mcp_client(token) as client:
        result = await client.call_tool("ping", {})
    assert result.is_error
    assert "insufficient_scope" in result.content[0].text

    [row] = _rows(mcp_db)
    assert row.outcome == McpCallOutcome.DENIED.value
    assert row.error_class == "ScopeDeniedError"
    assert row.token_id == token_id
    assert row.required_scope == McpScope.READ.value


async def test_crashing_tool_is_logged_as_error_with_its_class(mcp_db, probe_tools):
    token, _ = _mint(mcp_db, scopes=("pipeline",))
    async with _mcp_client(token) as client:
        result = await client.call_tool("audit_probe_crash", {"research_id": 7})
    assert result.is_error

    [row] = _rows(mcp_db)
    assert row.tool == "audit_probe_crash"
    assert row.required_scope == McpScope.PIPELINE.value
    assert row.outcome == McpCallOutcome.ERROR.value
    assert row.error_class == "ValueError"
    assert row.args_redacted == {"research_id": 7}
    # The exception message is never stored.
    assert "secret details" not in _raw_table_text(mcp_db)


async def test_tool_error_is_logged_as_error(mcp_db, probe_tools):
    token, _ = _mint(mcp_db)
    async with _mcp_client(token) as client:
        result = await client.call_tool("audit_probe_refuse", {})
    assert result.is_error

    [row] = _rows(mcp_db)
    assert row.outcome == McpCallOutcome.ERROR.value
    assert row.error_class == "ToolError"


async def test_article_body_argument_is_never_stored(mcp_db, probe_tools):
    """Acceptance: a research/article body passed to a tool is stored as len + hash only."""
    token, _ = _mint(mcp_db)
    async with _mcp_client(token) as client:
        result = await client.call_tool(
            "audit_probe_echo",
            {"body": ARTICLE_BODY, "slug": "portobelo-1596", "password": "hunter2-hunter2"},
        )
    assert not result.is_error

    [row] = _rows(mcp_db)
    assert row.outcome == McpCallOutcome.OK.value
    assert row.args_redacted["slug"] == "portobelo-1596"
    assert set(row.args_redacted["body"]) == {"len", "sha256"}
    assert row.args_redacted["body"]["len"] == len(ARTICLE_BODY)
    assert "password" not in row.args_redacted

    stored = _raw_table_text(mcp_db)
    assert MARKER not in stored
    assert "Lorem ipsum" not in stored
    assert "hunter2" not in stored


async def test_denied_call_does_not_store_the_body_either(mcp_db, probe_tools):
    token, _ = _mint(mcp_db, scopes=("pipeline",))
    async with _mcp_client(token) as client:
        result = await client.call_tool("audit_probe_echo", {"body": ARTICLE_BODY})
    assert result.is_error

    [row] = _rows(mcp_db)
    assert row.outcome == McpCallOutcome.DENIED.value
    assert row.args_redacted["body"]["len"] == len(ARTICLE_BODY)
    assert MARKER not in _raw_table_text(mcp_db)


async def test_a_failed_audit_write_does_not_fail_the_call(mcp_db):
    token, _ = _mint(mcp_db)

    # Break only the audit insert (the token verifier shares SessionLocal).
    with (
        patch.object(audit, "McpToolCall", side_effect=RuntimeError("pool exhausted")),
        patch.object(audit.sentry_sdk, "capture_exception") as capture,
    ):
        async with _mcp_client(token) as client:
            result = await client.call_tool("ping", {})

    assert not result.is_error
    assert result.structured_content["ok"] is True
    capture.assert_called_once()
    assert _rows(mcp_db) == []


def test_a_redaction_failure_still_writes_a_row(mcp_db):
    class Unprintable:
        def __str__(self):
            raise RuntimeError("no")

    row = audit.prepare_entry(
        tool="ping",
        scope=McpScope.READ,
        token_id=None,
        token_name=None,
        arguments={"weird": Unprintable()},
        exc=None,
        duration_ms=1,
    )
    audit.record_call(**row)
    [stored] = _rows(mcp_db)
    assert stored.args_redacted == audit.REDACTION_FAILED
    assert stored.token_id is None


def _sentry_render(exc: BaseException) -> str:
    """Everything Sentry could serialize for *exc*: the locals of every frame of
    every exception in its chain (``__cause__`` and ``__context__``), plus the
    event the SDK itself builds with local variables on, after our scrubber."""
    parts, seen, stack = [], set(), [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        tb = current.__traceback__
        while tb is not None:
            parts.append(repr(tb.tb_frame.f_locals))
            tb = tb.tb_next
        stack.extend([current.__cause__, current.__context__])
    options = {**DEFAULT_OPTIONS, "include_local_variables": True}
    event, _hint = event_from_exception(exc, client_options=options)
    parts.append(json.dumps(scrub_pii_event(event, {}), default=str))
    return "\n".join(parts)


def _assert_no_secret(rendered: str, token: str) -> None:
    assert token not in rendered
    assert "pic_mcp_" not in rendered
    assert MARKER not in rendered
    assert "Lorem ipsum" not in rendered


@pytest.mark.parametrize(
    ("tool", "scopes", "outcome"),
    [
        ("audit_probe_echo", ("read",), "OK"),  # async
        ("audit_probe_echo", ("pipeline",), "DENIED"),
        ("audit_probe_async_body_crash", ("read",), "ERROR"),
        ("audit_probe_sync_echo", ("read",), "OK"),  # sync, like `ping`
        ("audit_probe_sync_echo", ("pipeline",), "DENIED"),
        ("audit_probe_sync_body_crash", ("read",), "ERROR"),
    ],
)
async def test_a_failed_audit_write_sends_no_token_or_body_to_sentry(
    mcp_db, probe_tools, tool, scopes, outcome
):
    """Sentry serializes frame locals across the whole exception chain: a failed
    audit write must carry neither the raw bearer token nor the raw arguments,
    for sync and async tools and every outcome (review rounds 1 and 2 of #639)."""
    token, _ = _mint(mcp_db, scopes=scopes)
    captured, logged = [], []
    real_exception = audit.logger.exception

    def log_exception(*args, **kwargs):
        import sys

        logged.append(sys.exc_info()[1])
        real_exception(*args, **kwargs)

    with (
        patch.object(audit, "McpToolCall", side_effect=RuntimeError("pool exhausted")),
        patch.object(audit.sentry_sdk, "capture_exception", side_effect=captured.append),
        patch.object(audit.logger, "exception", side_effect=log_exception),
    ):
        async with _mcp_client(token) as client:
            result = await client.call_tool(tool, {"body": ARTICLE_BODY})
    assert result.is_error is (outcome != "OK")
    [exc] = captured
    assert logged == [exc]  # the logging integration would report the same object
    assert exc.__context__ is None

    rendered = _sentry_render(exc)
    # The locals really were rendered (record_call's redacted row is there)...
    assert "args_redacted" in rendered and "sha256" in rendered
    assert f"'{outcome}'" in rendered
    # ...but no credential or body.
    _assert_no_secret(rendered, token)


def test_a_scope_denial_traceback_holds_no_access_token():
    """``require_scope``'s check frame is on every denial's traceback."""
    token = generate_token()
    access = AccessToken(token=token, client_id="t", scopes=["pipeline"], claims={})

    @require_scope(McpScope.READ)
    def tool(body: str) -> None:
        return None

    def call() -> BaseException | None:
        # Caught here, so the traceback starts in this frame (no token local),
        # not in the test's (which holds `access` by construction).
        try:
            tool(body="short")
        except ScopeDeniedError as exc:
            return exc
        return None

    reset = auth_context_var.set(AuthenticatedUser(access))
    try:
        denied = call()
    finally:
        auth_context_var.reset(reset)
    assert isinstance(denied, ScopeDeniedError)
    rendered = _sentry_render(denied)
    tb, functions = denied.__traceback__, []
    while tb is not None:
        functions.append(tb.tb_frame.f_code.co_name)
        tb = tb.tb_next
    assert "check" in functions  # the frame under test really is on the traceback
    assert token not in rendered and "pic_mcp_" not in rendered


async def test_a_failed_token_lookup_logs_no_token(mcp_db, caplog):
    """The verifier logs a DB failure with its traceback; the bearer must not be in it."""
    token, _ = _mint(mcp_db)
    with patch("app.mcp.auth._lookup_token", side_effect=RuntimeError("pool exhausted")):
        async with mcp_http.run():
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url=BASE
            ) as http:
                response = await http.post(
                    MCP_PATH,
                    json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                )
    assert response.status_code == 401
    [record] = [r for r in caplog.records if r.getMessage() == "MCP token lookup failed"]
    _assert_no_secret(_sentry_render(record.exc_info[1]), token)


@pytest.mark.parametrize("tool", ["audit_probe_sync_body_crash", "audit_probe_async_body_crash"])
async def test_a_tool_crash_logged_by_the_sdk_holds_no_token(mcp_db, probe_tools, caplog, tool):
    """The SDK ``logger.exception``s a crashing tool, chained to the tool's own
    exception (whose frames include the audit and scope wrappers). No frame on
    that chain may hold the AccessToken; the raw arguments in the tool's own
    frames are covered by the ``/mcp`` frame-vars strip below."""
    token, _ = _mint(mcp_db)
    async with _mcp_client(token) as client:
        result = await client.call_tool(tool, {"body": "short"})
    assert result.is_error
    [record] = [r for r in caplog.records if "raised an unexpected exception" in r.getMessage()]
    rendered = _sentry_render(record.exc_info[1])
    assert "ValueError" in rendered
    assert token not in rendered and "pic_mcp_" not in rendered


@pytest.mark.parametrize(
    ("url", "transaction"),
    [("https://api.panamaincontext.com/mcp", None), (None, "/mcp"), (None, "POST /mcp")],
)
def test_mcp_route_events_lose_frame_vars(url, transaction):
    """Defence in depth for the tool's *own* exceptions (its frames hold its args)."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "crash",
                    "stacktrace": {"frames": [{"function": "tool", "vars": {"body": MARKER}}]},
                }
            ]
        },
        "request": {"method": "POST"},
    }
    if url:
        event["request"]["url"] = url
    if transaction:
        event["transaction"] = transaction
    scrubbed = scrub_pii_event(event, {})
    assert MARKER not in json.dumps(scrubbed)
    assert scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]["function"] == "tool"


def test_every_registered_tool_is_audited():
    """Registry walk: a tool registered without pic_tool (bare server.tool) fails here."""
    tools = mcp_server._tool_manager.list_tools()
    assert tools
    for tool in tools:
        assert getattr(tool.fn, AUDITED_ATTR, False) is True, f"{tool.name} is not audited"
        assert isinstance(getattr(tool.fn, REQUIRED_SCOPE_ATTR, None), McpScope), tool.name


def test_the_registry_walk_catches_a_bare_tool():
    """The check above is not vacuous: ``server.tool()`` alone leaves no stamp."""
    server = new_mcp_server()

    @server.tool(name="bare")
    def bare() -> str:
        return "x"

    [tool] = server._tool_manager.list_tools()
    assert not getattr(tool.fn, AUDITED_ATTR, False)


# --- A cancelled caller (client timeout / disconnect, #689) ---------------------


async def _call_then_cancel(wrapper, is_async: bool, committed, release) -> None:
    """Call *wrapper* the way the SDK does, and cancel the caller once it committed.

    ``FuncMetadata.call_fn`` awaits an async tool directly and runs a sync one
    through ``anyio.to_thread.run_sync`` -- reproduced here, so the cancel hits
    exactly the await a client timeout cancels.
    """
    access = AccessToken(
        token=generate_token(), client_id="operator-laptop", scopes=["pipeline"], claims={}
    )
    reset = auth_context_var.set(AuthenticatedUser(access))
    try:
        with anyio.CancelScope() as scope:
            async with anyio.create_task_group() as tg:

                async def call() -> None:
                    if is_async:
                        await wrapper(claim_ids=[1, 2])
                    else:
                        await anyio.to_thread.run_sync(functools.partial(wrapper, claim_ids=[1, 2]))

                tg.start_soon(call)
                await committed.wait()
                scope.cancel()  # the client gave up after the write committed
                release()
        assert scope.cancelled_caught
    finally:
        auth_context_var.reset(reset)


async def test_a_sync_tool_cancelled_after_its_write_still_leaves_a_row(mcp_db):
    """The #689 scenario: the write commits, the client times out, the row lands."""
    committed, released = anyio.Event(), threading.Event()
    writes = []

    def slow_write_tool(claim_ids: list[int]) -> dict:
        writes.append(claim_ids)  # "the write committed"
        anyio.from_thread.run_sync(committed.set)
        released.wait(5)  # the slow tail (the in-line reindex before #689)
        return {"written": len(claim_ids)}

    wrapper = audit.audit_tool("cancel_probe_sync", McpScope.PIPELINE)(slow_write_tool)
    with anyio.fail_after(10):
        await _call_then_cancel(wrapper, False, committed, released.set)

    assert writes == [[1, 2]]
    [row] = _rows(mcp_db)
    assert (row.tool, row.token_name) == ("cancel_probe_sync", "operator-laptop")
    assert row.outcome == McpCallOutcome.OK.value  # the write committed
    assert row.error_class is None
    assert row.args_redacted == {"claim_ids": [1, 2]}


async def test_an_async_tool_cancelled_after_its_write_still_leaves_a_row(mcp_db):
    committed, release = anyio.Event(), anyio.Event()

    async def slow_write_tool(claim_ids: list[int]) -> dict:
        committed.set()
        await release.wait()  # cancelled here
        return {"written": len(claim_ids)}

    wrapper = audit.audit_tool("cancel_probe_async", McpScope.PIPELINE)(slow_write_tool)
    with anyio.fail_after(10):
        await _call_then_cancel(wrapper, True, committed, release.set)

    [row] = _rows(mcp_db)
    assert (row.tool, row.token_name) == ("cancel_probe_async", "operator-laptop")
    assert row.outcome == McpCallOutcome.ERROR.value
    assert row.error_class == "CancelledError"
    assert row.args_redacted == {"claim_ids": [1, 2]}


# --- Redaction -----------------------------------------------------------------


def test_short_strings_are_kept_and_long_ones_hashed():
    short = "x" * MAX_INLINE_STRING
    long = "y" * (MAX_INLINE_STRING + 1)
    out = redact_args({"short": short, "long": long})
    assert out["short"] == short
    assert out["long"]["len"] == MAX_INLINE_STRING + 1
    assert len(out["long"]["sha256"]) == 64


def test_redaction_walks_nested_values():
    class Payload(BaseModel):
        content: str
        api_key: str

    out = redact_args(
        {
            "items": [{"text": "z" * 500, "id": 3}],
            "model": Payload(content="c" * 300, api_key="sk-live"),
            "raw": b"\x00\x01",
            "ratio": math.nan,
            "when": datetime(2026, 10, 11, tzinfo=UTC),
        }
    )
    assert out["items"][0]["id"] == 3
    assert out["items"][0]["text"]["len"] == 500
    assert out["model"]["content"]["len"] == 300
    assert "api_key" not in out["model"]
    assert out["raw"]["len"] == 2
    assert out["ratio"] == "nan"
    assert out["when"].startswith("2026-10-11")
    json.dumps(out)  # JSONB-safe


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "access_token",
        "accessToken",
        "api_key",
        "apiKey",
        "client_secret",
        "password",
        "Authorization",
        "x-auth",
        "credentials",
        "private_key",
    ],
)
def test_credential_keys_are_dropped(key):
    assert is_credential_key(key)
    assert redact_args({key: "v", "ok": 1}) == {"ok": 1}


@pytest.mark.parametrize("key", ["max_tokens", "claim_key", "author", "research_id", "query"])
def test_ordinary_keys_are_kept(key):
    assert not is_credential_key(key)


# --- Admin read endpoint -------------------------------------------------------


def _seed_calls(db):
    token_a = McpToken(name="alpha", token_hash=hash_token("a"), scopes=["read"])
    token_b = McpToken(name="beta", token_hash=hash_token("b"), scopes=["read", "pipeline"])
    db.add_all([token_a, token_b])
    db.flush()
    rows = [
        (token_a, "ping", "read", "OK", None),
        (token_a, "search_knowledge", "read", "ERROR", "ValueError"),
        (token_b, "ping", "read", "DENIED", "ScopeDeniedError"),
        (token_b, "retry_task", "pipeline", "OK", None),
    ]
    for token, tool, scope, outcome, error_class in rows:
        db.add(
            McpToolCall(
                token_id=token.id,
                token_name=token.name,
                tool=tool,
                required_scope=scope,
                args_redacted={},
                outcome=outcome,
                error_class=error_class,
                duration_ms=5,
            )
        )
    db.commit()
    return token_a, token_b


def test_admin_list_requires_admin_token(test_client):
    assert test_client.get(CALLS_URL).status_code == 401


def test_admin_list_newest_first(test_db, test_client, admin_token):
    _seed_calls(test_db)
    response = test_client.get(CALLS_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert [i["tool"] for i in body["items"]] == [
        "retry_task",
        "ping",
        "search_knowledge",
        "ping",
    ]
    assert body["items"][1]["outcome"] == "DENIED"
    assert body["items"][1]["error_class"] == "ScopeDeniedError"


def test_admin_list_filters(test_db, test_client, admin_token):
    token_a, token_b = _seed_calls(test_db)

    def tools(**params):
        response = test_client.get(CALLS_URL, params=params)
        assert response.status_code == 200
        return sorted(i["tool"] for i in response.json()["items"])

    assert tools(token="alpha") == ["ping", "search_knowledge"]
    assert tools(token_id=token_b.id) == ["ping", "retry_task"]
    assert tools(tool="ping") == ["ping", "ping"]
    assert tools(outcome="DENIED") == ["ping"]
    assert tools(token="beta", outcome="OK") == ["retry_task"]
    assert tools(token="nobody") == []

    page = test_client.get(CALLS_URL, params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 4 and len(page["items"]) == 1


def test_admin_list_rejects_unknown_outcome(test_client, admin_token):
    assert test_client.get(CALLS_URL, params={"outcome": "MAYBE"}).status_code == 422


def test_audit_log_is_not_on_the_driver_surface():
    assert not is_driver_call_allowed("GET", CALLS_URL)
