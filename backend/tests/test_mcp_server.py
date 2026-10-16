"""MCP server skeleton (#638): mount, token auth, scopes, ``ping``, token CLI.

The server is driven through the real FastAPI ``app`` with the SDK's own
``Client`` over an in-process ASGI transport (``httpx2.ASGITransport``), so
the routes, the SDK auth middleware, the host allowlist and the lifespan
hook (``mcp_http.run()``) are all exercised as deployed.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx2
import pytest
from app.core import database
from app.main import app
from app.mcp.auth import REQUIRED_SCOPE_ATTR, generate_token, hash_token
from app.mcp.server import (
    MCP_PATH,
    PROTECTED_RESOURCE_METADATA_PATH,
    RESOURCE_SERVER_URL,
    build_mcp_server,
    mcp_http,
    mcp_server,
)
from app.models.enums import McpScope
from app.models.mcp_token import McpToken
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from scripts import mcp_tokens as cli
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

BASE = "http://localhost"
INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}
JSON_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def mcp_db(test_engine, test_db):
    """Point every ``SessionLocal()`` (the verifier's, the CLI's) at the test DB."""
    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with (
        patch.object(database, "SessionLocal", factory),
        patch.object(cli, "SessionLocal", factory),
    ):
        yield test_db


def _mint(db, *, name="agent", scopes=("read",), revoked=False) -> str:
    token = generate_token()
    db.add(
        McpToken(
            name=name,
            token_hash=hash_token(token),
            scopes=list(scopes),
            revoked_at=datetime.now(UTC) if revoked else None,
        )
    )
    db.commit()
    return token


@asynccontextmanager
async def _http(token: str | None = None, base_url: str = BASE):
    """Raw in-process HTTP client against the app, with the MCP lifespan running."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with mcp_http.run():
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=base_url, headers=headers
        ) as http:
            yield http


@asynccontextmanager
async def _mcp_client(token: str | None):
    async with _http(token) as http:
        async with Client(streamable_http_client(f"{BASE}{MCP_PATH}", http_client=http)) as client:
            yield client


# --- Auth at the transport -----------------------------------------------------


async def test_missing_token_is_401_with_resource_metadata(mcp_db):
    async with _http() as http:
        response = await http.post(MCP_PATH, json=INIT_BODY, headers=JSON_HEADERS)
    assert response.status_code == 401
    assert PROTECTED_RESOURCE_METADATA_PATH in response.headers["www-authenticate"]


async def test_missing_token_fails_the_sdk_client(mcp_db):
    with pytest.raises(Exception):  # noqa: B017 - the SDK surfaces the 401 as a transport error
        async with _mcp_client(None) as client:
            await client.call_tool("ping", {})


async def test_unknown_token_is_401(mcp_db):
    async with _http(generate_token()) as http:
        response = await http.post(MCP_PATH, json=INIT_BODY, headers=JSON_HEADERS)
    assert response.status_code == 401


async def test_revoked_token_is_401(mcp_db):
    token = _mint(mcp_db, revoked=True)
    async with _http(token) as http:
        response = await http.post(MCP_PATH, json=INIT_BODY, headers=JSON_HEADERS)
    assert response.status_code == 401


async def test_admin_style_token_is_not_an_mcp_token(mcp_db):
    """A token without the MCP prefix never reaches the DB lookup."""
    with patch("app.mcp.auth._lookup_token") as lookup:
        async with _http("dev-token") as http:
            response = await http.post(MCP_PATH, json=INIT_BODY, headers=JSON_HEADERS)
    assert response.status_code == 401
    lookup.assert_not_called()


async def test_unlisted_host_is_421(mcp_db):
    token = _mint(mcp_db)
    async with _http(token, base_url="http://evil.example") as http:
        response = await http.post(MCP_PATH, json=INIT_BODY, headers=JSON_HEADERS)
    assert response.status_code == 421


async def test_protected_resource_metadata_is_served(mcp_db):
    async with _http() as http:
        response = await http.get(PROTECTED_RESOURCE_METADATA_PATH)
    assert response.status_code == 200
    assert response.json()["resource"] == RESOURCE_SERVER_URL


async def test_mcp_route_is_503_before_the_lifespan_runs():
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as http:
        response = await http.post(MCP_PATH, json=INIT_BODY, headers=JSON_HEADERS)
    assert response.status_code == 503


# --- Tools and scopes ----------------------------------------------------------


async def test_ping_ok_and_stamps_last_used(mcp_db):
    token = _mint(mcp_db, name="operator-laptop", scopes=("read", "pipeline"))
    async with _mcp_client(token) as client:
        result = await client.call_tool("ping", {})
    assert not result.is_error
    assert result.structured_content["ok"] is True
    assert result.structured_content["token_name"] == "operator-laptop"
    assert result.structured_content["scopes"] == ["pipeline", "read"]

    mcp_db.expire_all()
    row = mcp_db.query(McpToken).one()
    assert row.last_used_at is not None


async def test_insufficient_scope_is_a_clean_tool_error(mcp_db):
    token = _mint(mcp_db, scopes=("pipeline",))
    async with _mcp_client(token) as client:
        tools = await client.list_tools()  # any valid token may connect and list
        result = await client.call_tool("ping", {})
    assert "ping" in {tool.name for tool in tools.tools}
    assert result.is_error
    assert "insufficient_scope" in result.content[0].text
    assert "'read'" in result.content[0].text


async def test_last_used_is_not_rewritten_within_the_resolution(mcp_db):
    token = _mint(mcp_db)
    recent = datetime.now(UTC) - timedelta(seconds=5)
    mcp_db.query(McpToken).update({McpToken.last_used_at: recent})
    mcp_db.commit()
    async with _mcp_client(token) as client:
        await client.call_tool("ping", {})
    mcp_db.expire_all()
    assert mcp_db.query(McpToken).one().last_used_at == recent


async def test_lifespan_can_be_entered_more_than_once(mcp_db):
    """The SDK's session manager runs once per instance; mcp_http rebuilds it."""
    token = _mint(mcp_db)
    for _ in range(2):
        async with _mcp_client(token) as client:
            assert not (await client.call_tool("ping", {})).is_error


def test_every_registered_tool_declares_a_scope():
    """The seam #639 builds on: tools are added through pic_tool only."""
    tools = mcp_server._tool_manager.list_tools()
    assert tools
    for tool in tools:
        assert isinstance(getattr(tool.fn, REQUIRED_SCOPE_ATTR, None), McpScope), tool.name


def test_building_the_server_leaves_root_logging_alone():
    """MCPServer.__init__ calls logging.basicConfig; #424's root config must survive."""
    import logging

    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        root.handlers = []
        root.setLevel(logging.WARNING)
        build_mcp_server()
        assert root.handlers == []
        assert root.level == logging.WARNING
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


def test_mcp_routes_stay_out_of_the_openapi_schema():
    paths = app.openapi()["paths"]
    assert MCP_PATH not in paths
    assert PROTECTED_RESOURCE_METADATA_PATH not in paths


# --- Model constraint ------------------------------------------------------------


def test_scope_outside_the_vocabulary_is_rejected(test_db):
    test_db.add(McpToken(name="bad", token_hash=hash_token("x"), scopes=["read", "admin"]))
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()


# --- Token CLI -----------------------------------------------------------------


def test_cli_create_prints_token_once_and_stores_only_the_hash(mcp_db, capsys):
    assert cli.main(["create", "--name", "sentry-triage", "--scopes", "read, pipeline,read"]) == 0
    out = capsys.readouterr().out
    token = out.strip().splitlines()[-1]
    assert token.startswith("pic_mcp_")

    row = mcp_db.query(McpToken).one()
    assert row.scopes == ["read", "pipeline"]
    assert row.token_hash == hash_token(token)
    assert token not in row.token_hash

    assert cli.main(["list"]) == 0
    listing = capsys.readouterr().out
    assert "sentry-triage" in listing and "active" in listing
    assert token not in listing


def test_cli_create_rejects_unknown_scope_and_duplicate_name(mcp_db, capsys):
    assert cli.main(["create", "--name", "x", "--scopes", "admin"]) == 2
    assert "unknown scope" in capsys.readouterr().err
    assert cli.main(["create", "--name", "x", "--scopes", "read"]) == 0
    assert cli.main(["create", "--name", "x", "--scopes", "read"]) == 2
    assert "already exists" in capsys.readouterr().err


async def test_cli_revoke_kills_the_token(mcp_db, capsys):
    assert cli.main(["create", "--name", "temp", "--scopes", "read"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert cli.main(["revoke", "--name", "temp"]) == 0
    assert cli.main(["revoke", "--name", "missing"]) == 2

    mcp_db.expire_all()
    assert mcp_db.query(McpToken).one().revoked_at is not None
    async with _http(token) as http:
        response = await http.post(MCP_PATH, json=INIT_BODY, headers=JSON_HEADERS)
    assert response.status_code == 401
