"""Bearer-token auth and per-tool scopes for the MCP server (#638).

Two layers, deliberately separate:

* **Authentication** is the SDK's: ``McpTokenVerifier`` is handed to
  ``MCPServer(token_verifier=..., auth=AuthSettings(...))``, whose
  ``RequireAuthMiddleware`` answers 401 (with ``WWW-Authenticate`` pointing at
  the protected-resource metadata) for a missing, unknown or revoked token
  before any MCP message is parsed. ``AuthSettings.required_scopes`` stays
  empty: a valid token of *any* scope may connect and list tools.
* **Authorization** is per tool: :func:`require_scope` wraps a tool function
  and raises ``ToolError`` (an ``is_error`` result the model can read, not a
  crash) when the caller's token lacks the scope. No scope implies another.

Tokens are stored as SHA-256 hex digests (``mcp_tokens.token_hash``); the
plaintext exists only in the output of ``python -m scripts.mcp_tokens create``.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import anyio.to_thread
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError

from app.core import database
from app.models.enums import McpScope
from app.models.mcp_token import McpToken

logger = logging.getLogger(__name__)

# 32 random bytes, URL-safe; the prefix makes a leaked token greppable
# (and lets a secrets scanner be taught the shape later).
TOKEN_PREFIX = "pic_mcp_"

# ``last_used_at`` is a "is this token still in use?" signal, not an audit
# trail (that is #639's ``mcp_tool_calls``), so stamp it at most this often
# instead of writing a row on every request.
LAST_USED_RESOLUTION = timedelta(minutes=1)

# Attribute set on a scope-checked tool function so a registry walk (#639)
# can assert every tool declares one.
REQUIRED_SCOPE_ATTR = "__mcp_required_scope__"

_F = TypeVar("_F", bound=Callable[..., Any])


class ScopeDeniedError(ToolError):
    """The caller's token lacks the tool's scope (an ``insufficient_scope`` result).

    A ``ToolError`` subclass, so the SDK still answers with an ``is_error``
    result the model can read; its own type lets the audit wrapper
    (``app.mcp.audit``) record the call as ``DENIED`` rather than ``ERROR``.
    """


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _lookup_token(token_hash: str) -> tuple[int, str, list[str]] | None:
    """Sync DB half of verification: one short session, closed before returning.

    Takes the token's *digest*, never the plaintext, and returns plain values:
    a DB failure here is logged with its traceback (and so reaches Sentry,
    which records frame locals), so no frame on that traceback may hold the
    bearer token.
    """
    db = database.SessionLocal()
    try:
        row = db.query(McpToken).filter(McpToken.token_hash == token_hash).first()
        if row is None or row.revoked_at is not None:
            return None
        now = datetime.now(UTC)
        if row.last_used_at is None or now - row.last_used_at >= LAST_USED_RESOLUTION:
            row.last_used_at = now
            db.commit()
        return row.id, row.name, list(row.scopes)
    finally:
        db.close()


async def _lookup_or_none(token_hash: str) -> tuple[int, str, list[str]] | None:
    """Run the lookup in a worker thread; a failure is logged and means "unknown".

    The ``except`` lives here, in a frame that holds only the digest, because
    a logged traceback starts at the frame that catches.
    """
    try:
        return await anyio.to_thread.run_sync(_lookup_token, token_hash)
    except Exception:
        logger.exception("MCP token lookup failed")
        return None


class McpTokenVerifier:
    """``TokenVerifier`` backed by ``mcp_tokens``.

    The lookup runs in a worker thread (sync SQLAlchemy must not block the
    event loop) and holds its pooled connection only for the lookup itself.
    A DB failure is logged and treated as "not authenticated" (401), never a 500
    that would leak a traceback through the SDK's error path.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith(TOKEN_PREFIX):
            return None
        found = await _lookup_or_none(hash_token(token))
        if found is None:
            return None
        token_id, name, scopes = found
        return AccessToken(
            token=token,
            client_id=name,
            scopes=scopes,
            claims={"mcp_token_id": token_id},
        )


def _granted_scopes() -> list[str]:
    """The caller's scopes, read without keeping the ``AccessToken`` in a frame
    that a ``ScopeDeniedError`` traceback passes through."""
    access = get_access_token()
    return list(access.scopes) if access is not None else []


def require_scope(scope: McpScope) -> Callable[[_F], _F]:
    """Decorator: refuse the tool call unless the caller's token carries *scope*.

    Apply it *under* ``@server.tool()`` (i.e. closest to the function). Works
    for sync and async tools; ``functools.wraps`` keeps the signature the SDK
    derives the tool's input schema from. Sync tools run in a worker thread,
    which inherits the request's context, so the token is visible there too.
    """

    def check() -> None:
        # No AccessToken local here: this frame is on the denial's traceback.
        if scope.value not in _granted_scopes():
            raise ScopeDeniedError(
                f"insufficient_scope: this tool requires the '{scope.value}' scope, "
                "which this MCP token does not have."
            )

    def decorate(fn: _F) -> _F:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                check()
                return await fn(*args, **kwargs)

            wrapper: Any = async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                check()
                return fn(*args, **kwargs)

            wrapper = sync_wrapper
        setattr(wrapper, REQUIRED_SCOPE_ATTR, scope)
        return wrapper

    return decorate
