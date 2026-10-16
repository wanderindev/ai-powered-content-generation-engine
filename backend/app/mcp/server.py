"""The PIC MCP server: official ``mcp`` 2.x SDK, served inside the FastAPI app (#638).

Shape (see ``docs/claude/mcp.md`` and ``docs/design/mcp-server.md``):

* ``mcp_server`` is an ``MCPServer`` (v2 name; ``mcp.server.fastmcp`` is gone)
  configured as an OAuth *resource server* with a static-token verifier
  (``app.mcp.auth``). Tools are registered with :func:`pic_tool`, which
  requires a scope on every tool.
* Transport is Streamable HTTP with ``stateless_http=True`` and
  ``json_response=True``: every POST is answered with one JSON body, no SSE
  stream and no server-side session, so the shared nginx edge needs no change
  and either uvicorn worker can serve any request.
* :func:`mount_mcp` adds two exact-path routes to the FastAPI app, ``/mcp``
  and the RFC 9728 metadata URL ``/.well-known/oauth-protected-resource/mcp``.
  They are *not* a ``Mount("/mcp")``: a mount would 307 a bare ``/mcp`` to
  ``/mcp/`` and would put the well-known URL under ``/mcp/`` where clients do
  not look.
* The SDK's session manager runs once per instance, so :class:`McpHttpApp`
  builds a fresh Streamable HTTP app on every lifespan entry (``main.py``
  enters :meth:`McpHttpApp.run`). Before that, requests get a 503.

Connection-pool rule: a tool opens its own ``SessionLocal()``, closes it
before returning, and never holds it across external I/O (Anthropic, Voyage,
DeepL, Spaces). Sync tools run in a worker thread (the SDK's choice), so
blocking SQLAlchemy calls are fine there.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from typing import Any, TypeVar

from fastapi import FastAPI
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app.mcp.audit import audit_tool
from app.mcp.auth import McpTokenVerifier, require_scope
from app.mcp.tools import approvals as approval_tools
from app.mcp.tools import knowledge as knowledge_tools
from app.mcp.tools import pipeline as pipeline_tools
from app.mcp.tools import writes as write_tools
from app.models.enums import McpScope

MCP_PATH = "/mcp"
PUBLIC_HOST = "api.panamaincontext.com"
RESOURCE_SERVER_URL = f"https://{PUBLIC_HOST}{MCP_PATH}"
# RFC 9728 §3.1: the well-known segment goes between host and resource path.
PROTECTED_RESOURCE_METADATA_PATH = f"/.well-known/oauth-protected-resource{MCP_PATH}"

# Host-header allowlist (DNS-rebinding protection): anything else gets a 421.
# The public vhost forwards `Host $host` (docker/frontend/nginx.prod.conf), and
# localhost covers the dev stack and the in-process test client.
TRANSPORT_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        PUBLIC_HOST,
        "localhost",
        "localhost:*",
        "127.0.0.1",
        "127.0.0.1:*",
        "[::1]:*",
    ],
    allowed_origins=[
        f"https://{PUBLIC_HOST}",
        "http://localhost:*",
        "http://127.0.0.1:*",
        "http://[::1]:*",
    ],
)

_F = TypeVar("_F", bound=Callable[..., Any])

# Every read tool (#640) carries these hints. Hints only: the scope check is
# the enforcement.
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

# (name, title, function, description) of the read-scope tools (#640).
READ_TOOLS: tuple[tuple[str, str, Callable[..., Any], str], ...] = (
    (
        "search_knowledge",
        "Search PIC knowledge",
        knowledge_tools.search_knowledge,
        knowledge_tools.SEARCH_KNOWLEDGE_DESCRIPTION,
    ),
    (
        "get_article",
        "Get article",
        knowledge_tools.get_article,
        knowledge_tools.GET_ARTICLE_DESCRIPTION,
    ),
    (
        "find_related",
        "Find related articles",
        knowledge_tools.find_related,
        knowledge_tools.FIND_RELATED_DESCRIPTION,
    ),
    (
        "check_claim",
        "Check a claim",
        knowledge_tools.check_claim,
        knowledge_tools.CHECK_CLAIM_DESCRIPTION,
    ),
    (
        "pipeline_status",
        "Pipeline status",
        pipeline_tools.pipeline_status,
        pipeline_tools.PIPELINE_STATUS_DESCRIPTION,
    ),
    (
        "explain_stuck_goal",
        "Explain a stuck goal",
        pipeline_tools.explain_stuck_goal,
        pipeline_tools.EXPLAIN_STUCK_GOAL_DESCRIPTION,
    ),
)


def _write_hints(*, destructive: bool) -> ToolAnnotations:
    """Hints of a pipeline write (#641). Every one converges on replay, so
    all are idempotent; ``destructive`` marks the ones that overwrite or are
    irreversible."""
    return ToolAnnotations(
        read_only_hint=False,
        destructive_hint=destructive,
        idempotent_hint=True,
        open_world_hint=False,
    )


# (name, title, function, description, annotations) of the pipeline-scope
# tools (#641). get_deep_research_batch writes nothing (READ_ONLY hints); it
# is pipeline scope because it is the first step of the write loop.
PIPELINE_TOOLS: tuple[tuple[str, str, Callable[..., Any], str, ToolAnnotations], ...] = (
    (
        "get_deep_research_batch",
        "Get a deep-research claim batch",
        write_tools.get_deep_research_batch,
        write_tools.GET_DEEP_RESEARCH_BATCH_DESCRIPTION,
        READ_ONLY,
    ),
    (
        "submit_fact_check_verdicts",
        "Submit deep-research verdicts",
        write_tools.submit_fact_check_verdicts,
        write_tools.SUBMIT_FACT_CHECK_VERDICTS_DESCRIPTION,
        # Fills NEEDS_DEEP_RESEARCH rows only; a resolved claim is never overwritten.
        _write_hints(destructive=False),
    ),
    (
        "submit_research",
        "Submit research",
        write_tools.submit_research,
        write_tools.SUBMIT_RESEARCH_DESCRIPTION,
        # replace=true overwrites a PENDING / REJECTED research body.
        _write_hints(destructive=True),
    ),
    (
        "retry_agent_task",
        "Retry a pipeline task",
        write_tools.retry_agent_task,
        write_tools.RETRY_AGENT_TASK_DESCRIPTION,
        _write_hints(destructive=False),
    ),
    (
        "skip_agent_task",
        "Skip a pipeline task",
        write_tools.skip_agent_task,
        write_tools.SKIP_AGENT_TASK_DESCRIPTION,
        # SKIPPED is terminal and satisfies dependents: it cannot be undone.
        _write_hints(destructive=True),
    ),
)


# (name, title, function, description, annotations) of the publish_request
# tools (#642). The three request_* tools only file an approval request; an
# admin executes it in the admin UI. Not destructive: nothing changes until then.
PUBLISH_REQUEST_TOOLS: tuple[tuple[str, str, Callable[..., Any], str, ToolAnnotations], ...] = (
    (
        "request_publish",
        "Request publishing an article",
        approval_tools.request_publish,
        approval_tools.REQUEST_PUBLISH_DESCRIPTION,
        _write_hints(destructive=False),
    ),
    (
        "request_unpublish",
        "Request unpublishing an article",
        approval_tools.request_unpublish,
        approval_tools.REQUEST_UNPUBLISH_DESCRIPTION,
        _write_hints(destructive=False),
    ),
    (
        "request_article_approval",
        "Request approving an article",
        approval_tools.request_article_approval,
        approval_tools.REQUEST_ARTICLE_APPROVAL_DESCRIPTION,
        _write_hints(destructive=False),
    ),
    (
        "get_approval_status",
        "Get an approval request's status",
        approval_tools.get_approval_status,
        approval_tools.GET_APPROVAL_STATUS_DESCRIPTION,
        READ_ONLY,
    ),
)


@contextmanager
def _keep_root_logging() -> Iterator[None]:
    """Undo the root-logger changes ``MCPServer.__init__`` makes.

    The SDK constructor calls ``logging.basicConfig(level=INFO)`` with a rich
    handler. On a fresh process (prod) that runs before ``main.py``'s own
    ``_configure_logging``, which would then be a no-op: root at INFO (the
    third-party chatter #424 removed) and the rich ``%(message)s`` format
    instead of ours. Restore the root's handlers and level afterwards.
    """
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


def build_mcp_server() -> MCPServer:
    """Construct the server and register its tools."""
    with _keep_root_logging():
        server = _new_server()
    _register_tools(server)
    return server


def _new_server() -> MCPServer:
    return MCPServer(
        name="panama-in-context",
        instructions=(
            "Tools for Panama In Context (bilingual Panama history blog, tours and "
            "academic excursions). Each tool requires a scope on your token: read, "
            "pipeline or publish_request."
        ),
        token_verifier=McpTokenVerifier(),
        auth=AuthSettings(
            # PIC is a resource server only: tokens are minted out of band by
            # `python -m scripts.mcp_tokens`, there is no OAuth authorization
            # server. `issuer_url` is required by the SDK and is advertised in
            # the protected-resource metadata; it points at this API.
            issuer_url=f"https://{PUBLIC_HOST}",
            resource_server_url=RESOURCE_SERVER_URL,
            # Our verifier is the audience check (a token row exists only for
            # this server), so the RFC 8707 resource comparison is off.
            validate_token_resource=False,
            # Any valid token may connect; per-tool scopes are `require_scope`.
            required_scopes=[],
        ),
    )


def pic_tool(server: MCPServer, scope: McpScope, **tool_kwargs: Any) -> Callable[[_F], _F]:
    """Register a tool that requires *scope* and is audited. The one way tools are added.

    Order matters: the audit wrapper (#639) sits *outside* ``require_scope`` so
    a call refused for a missing scope is logged as ``DENIED``. Any later
    cross-cutting wrapper belongs here too, so no tool can skip it.
    """

    def decorate(fn: _F) -> _F:
        tool_name = tool_kwargs.get("name") or fn.__name__
        checked = require_scope(scope)(fn)
        audited = audit_tool(tool_name, scope)(checked)
        server.tool(**tool_kwargs)(audited)
        return audited

    return decorate


def _register_tools(server: MCPServer) -> None:
    @pic_tool(
        server,
        McpScope.READ,
        name="ping",
        title="Ping",
        description=(
            "Check that the PIC MCP server is reachable and that your token is valid. "
            "Use it once when connecting; it does nothing else. Returns "
            '{"ok": true, "token_name": str, "scopes": [str], "server_time": ISO-8601 UTC}. '
            'Example: ping() -> {"ok": true, "token_name": "operator-laptop", '
            '"scopes": ["read"], "server_time": "2026-10-11T12:00:00+00:00"}'
        ),
        annotations=READ_ONLY,
    )
    def ping() -> dict[str, Any]:
        access = get_access_token()
        return {
            "ok": True,
            "token_name": access.client_id if access else None,
            "scopes": sorted(access.scopes) if access else [],
            "server_time": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    for name, title, fn, description in READ_TOOLS:
        pic_tool(
            server,
            McpScope.READ,
            name=name,
            title=title,
            description=description,
            annotations=READ_ONLY,
        )(fn)

    for name, title, fn, description, hints in PIPELINE_TOOLS:
        pic_tool(
            server,
            McpScope.PIPELINE,
            name=name,
            title=title,
            description=description,
            annotations=hints,
        )(fn)

    for name, title, fn, description, hints in PUBLISH_REQUEST_TOOLS:
        pic_tool(
            server,
            McpScope.PUBLISH_REQUEST,
            name=name,
            title=title,
            description=description,
            annotations=hints,
        )(fn)


class McpHttpApp:
    """ASGI endpoint for the MCP routes, rebuilt on every lifespan entry.

    ``StreamableHTTPSessionManager.run()`` may be entered once per instance,
    but one process can run the FastAPI lifespan more than once (each
    ``with TestClient(app)`` in the suite; uvicorn ``--reload``). Each
    :meth:`run` therefore calls ``streamable_http_app()`` again, which gives
    the server a new session manager, and routes requests to that app.
    """

    def __init__(self, server: MCPServer) -> None:
        self.server = server
        self._app: ASGIApp | None = None

    def _build(self) -> ASGIApp:
        return self.server.streamable_http_app(
            streamable_http_path=MCP_PATH,
            stateless_http=True,
            json_response=True,
            transport_security=TRANSPORT_SECURITY,
        )

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        app = self._build()
        async with self.server.session_manager.run():
            self._app = app
            try:
                yield
            finally:
                self._app = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._app is None:
            response = PlainTextResponse("MCP server is not running", status_code=503)
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


mcp_server = build_mcp_server()
mcp_http = McpHttpApp(mcp_server)


def mount_mcp(app: FastAPI) -> None:
    """Route ``/mcp`` and its protected-resource metadata URL to the MCP app."""
    for path in (MCP_PATH, PROTECTED_RESOURCE_METADATA_PATH):
        app.router.routes.append(Route(path, endpoint=mcp_http, include_in_schema=False))
