"""Audit wrapper for MCP tools: one ``mcp_tool_calls`` row per call (#639).

:func:`audit_tool` is applied to every tool by ``app.mcp.server.pic_tool``
(the only registration path), *outside* ``require_scope``, so a call refused
for a missing scope is logged too. ``test_every_registered_tool_is_audited``
walks the registry for :data:`AUDITED_ATTR`.

What a row holds:

* the caller (``mcp_token_id`` claim and token name from the request's
  access token), the tool name and its required scope;
* the arguments after :func:`redact_args` -- strings longer than
  :data:`MAX_INLINE_STRING` characters become ``{"len": n, "sha256": hex}``
  and credential-named keys are dropped, so a research or article body passed
  to a tool is never stored, only its length and digest;
* ``outcome``: ``DENIED`` for :class:`~app.mcp.auth.ScopeDeniedError`,
  ``ERROR`` (with the exception's class name, never its message) for anything
  else raised -- including a cancelled async tool (``CancelledError``, the
  client timed out or disconnected; #689) -- ``OK`` otherwise; and the
  duration in milliseconds.

**A client timeout or disconnect does not lose the row (#689).** A sync tool
runs in a worker thread that anyio never abandons: a cancel waits for the
thread, and the thread writes the row itself after the tool body returns, so
the row's outcome is the tool's own (``OK`` when the write committed). An
async tool is cancelled at its next ``await``; the wrapper catches the cancel,
writes the row under a shielded cancel scope, and re-raises it. Either way the
row lands only when the tool body has finished, so a slow tool's row can trail
the client's timeout by minutes.

**The audit write never changes the tool's result.** It runs after the tool
returns or raises, in its own short ``SessionLocal()`` (no connection is held
across the tool body), and any failure is swallowed (a DB / pool failure is
logged and sent to Sentry; a redaction failure is logged and the row stores
``REDACTION_FAILED``): a successful call stays
successful and a failing one re-raises its own exception. The trade-off is
that a call can go unlogged while the database is unhealthy; Sentry is where
that shows.

**What the audit path can hand to Sentry.** Sentry serializes the locals of
every frame on a captured traceback, *and of every exception chained to it*
(``__cause__`` / ``__context__``). The ``AccessToken`` carries the raw bearer
token, and the wrapper and tool frames hold the raw arguments. So:

* the wrappers reduce the token to ``(token_id, token_name)`` on entry and
  never hold the ``AccessToken`` (nor does ``require_scope``'s ``check``);
* the tool runs inside ``try``, but the audit write happens *after* the
  ``except`` block has exited (the tool's exception is re-raised afterwards),
  so a write failure is never implicitly chained to the tool's exception;
* :func:`prepare_entry` redacts first, and :func:`record_call` -- the only
  place that captures -- receives only redacted scalars, and also clears
  ``__context__`` on the write error before logging / capturing it.

A write failure's Sentry event therefore holds no token and no raw argument
(``test_mcp_audit.py`` builds the real event for sync and async tools, OK /
ERROR / DENIED). This does *not* cover the tool's **own** exception: its
traceback includes the tool's frames, which hold the raw arguments by nature.
The SDK reports that as a tool error, and ``/mcp`` is in
``app.core.observability._VARS_STRIPPED_PATHS`` so frame locals are dropped
from any event Sentry attributes to the route.

Async tools do the write in a worker thread (sync SQLAlchemy must not block
the event loop), shielded from cancellation; sync tools already run in one
(the SDK's choice).
"""

from __future__ import annotations

import enum
import functools
import hashlib
import inspect
import logging
import math
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

import anyio.to_thread
import sentry_sdk
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver import Context
from pydantic import BaseModel

from app.core import database
from app.mcp.auth import ScopeDeniedError
from app.models.enums import McpCallOutcome, McpScope
from app.models.mcp_tool_call import McpToolCall

logger = logging.getLogger(__name__)

# Attribute set on an audited tool function; the registry walk asserts it.
AUDITED_ATTR = "__mcp_audited__"

# Strings up to this many characters are stored as-is (ids, slugs, short
# queries); anything longer is stored as {"len", "sha256"}.
MAX_INLINE_STRING = 200
# Nesting deeper than this is not walked (an argument is never this deep).
MAX_DEPTH = 8
# Stored in place of the arguments when redaction itself failed.
REDACTION_FAILED = {"_redaction_failed": True}

# A key is a credential when any of its snake_case words is one of these.
# Word-based on purpose: ``max_tokens`` and ``claim_key`` are not credentials.
_CREDENTIAL_WORDS = frozenset(
    {
        "token",
        "secret",
        "password",
        "passwd",
        "passphrase",
        "authorization",
        "auth",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "apikey",
        "dsn",
    }
)
_CREDENTIAL_PHRASES = ("api_key", "private_key", "access_key", "session_key")
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")

_F = TypeVar("_F", bound=Callable[..., Any])


def is_credential_key(key: object) -> bool:
    """True when an argument key names a credential (its value is dropped)."""
    normalized = _CAMEL_BOUNDARY.sub(r"\1_\2", str(key)).lower()
    snake = _WORD_SPLIT.sub("_", normalized).strip("_")
    if any(phrase in snake for phrase in _CREDENTIAL_PHRASES):
        return True
    return any(word in _CREDENTIAL_WORDS for word in snake.split("_"))


def _digest(data: bytes, length: int) -> dict[str, Any]:
    return {"len": length, "sha256": hashlib.sha256(data).hexdigest()}


def _redact(value: Any, depth: int) -> Any:
    if depth > MAX_DEPTH:
        return {"_truncated": True}
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        # JSONB has no NaN / Infinity.
        return value if math.isfinite(value) else str(value)
    if isinstance(value, enum.Enum):
        return _redact(value.value, depth)
    if isinstance(value, str):
        if len(value) > MAX_INLINE_STRING:
            return _digest(value.encode("utf-8"), len(value))
        return value
    if isinstance(value, bytes | bytearray):
        return _digest(bytes(value), len(value))
    if isinstance(value, BaseModel):
        return _redact(value.model_dump(mode="json"), depth)
    if isinstance(value, Mapping):
        return {str(k): _redact(v, depth + 1) for k, v in value.items() if not is_credential_key(k)}
    if isinstance(value, list | tuple | set | frozenset):
        return [_redact(item, depth + 1) for item in value]
    return _redact(str(value), depth)


def redact_args(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The JSON-safe, body-free form of a tool call's arguments, as stored."""
    return _redact(dict(arguments), 0)


def _call_arguments(sig: inspect.Signature, args: tuple, kwargs: dict) -> dict[str, Any]:
    """Name the call's arguments; drop the SDK-injected ``Context``."""
    if args:
        try:
            named = dict(sig.bind_partial(*args, **kwargs).arguments)
        except TypeError:
            named = {**{f"_arg{i}": a for i, a in enumerate(args)}, **kwargs}
    else:
        named = dict(kwargs)
    return {k: v for k, v in named.items() if not isinstance(v, Context)}


def _token_identity(access: AccessToken | None) -> tuple[int | None, str | None]:
    if access is None:
        return None, None
    token_id = (access.claims or {}).get("mcp_token_id")
    return (token_id if isinstance(token_id, int) else None), access.client_id


def _classify(exc: BaseException | None) -> tuple[McpCallOutcome, str | None]:
    if exc is None:
        return McpCallOutcome.OK, None
    if isinstance(exc, ScopeDeniedError):
        return McpCallOutcome.DENIED, type(exc).__name__
    return McpCallOutcome.ERROR, type(exc).__name__


def prepare_entry(
    *,
    tool: str,
    scope: McpScope,
    token_id: int | None,
    token_name: str | None,
    arguments: Mapping[str, Any],
    exc: BaseException | None,
    duration_ms: int,
) -> dict[str, Any]:
    """Reduce a call to the scalar, redacted fields of its audit row. Never raises.

    Runs *before* :func:`record_call`, so the raw arguments never sit in a
    frame that a Sentry capture can serialize. A redaction failure is logged by
    class name only (no ``exc_info``: its frames would hold the raw value).
    """
    try:
        args_redacted = redact_args(arguments)
    except Exception as redact_exc:
        logger.warning(
            "MCP audit: could not redact arguments of %s (%s)", tool, type(redact_exc).__name__
        )
        args_redacted = dict(REDACTION_FAILED)
    outcome, error_class = _classify(exc)
    return {
        "tool": tool,
        "required_scope": scope.value,
        "token_id": token_id,
        "token_name": token_name,
        "args_redacted": args_redacted,
        "outcome": outcome.value,
        "error_class": error_class,
        "duration_ms": duration_ms,
    }


def record_call(
    *,
    tool: str,
    required_scope: str,
    token_id: int | None,
    token_name: str | None,
    args_redacted: dict[str, Any],
    outcome: str,
    error_class: str | None,
    duration_ms: int,
) -> None:
    """Write one audit row in its own session. Never raises.

    Takes only already-redacted scalars on purpose: a failure here is sent to
    Sentry, which serializes the locals of every frame on the traceback, and
    this frame is where that traceback starts. Never pass it the
    ``AccessToken`` (it carries the raw bearer token) or the raw arguments,
    and never call it from inside an ``except`` block that handles the tool's
    exception.
    """
    try:
        db = database.SessionLocal()
        try:
            db.add(
                McpToolCall(
                    token_id=token_id,
                    token_name=token_name,
                    tool=tool,
                    required_scope=required_scope,
                    args_redacted=args_redacted,
                    outcome=outcome,
                    error_class=error_class,
                    duration_ms=duration_ms,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception as write_exc:
        # Belt and braces: never let a chained exception (whose frames could
        # hold the token or raw arguments) ride along with this one.
        write_exc.__context__ = None
        write_exc.__suppress_context__ = True
        logger.exception("MCP audit: failed to write the mcp_tool_calls row for %s", tool)
        sentry_sdk.capture_exception(write_exc)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def audit_tool(tool: str, scope: McpScope) -> Callable[[_F], _F]:
    """Decorator: log every call of the tool *tool* to ``mcp_tool_calls``.

    Apply it outermost (``pic_tool`` does), so scope denials are seen. Works
    for sync and async tools; ``functools.wraps`` keeps the signature the SDK
    derives the input schema from, and copies the inner scope stamp.

    The wrappers reduce the access token to ``(token_id, token_name)`` at
    once and never hold the ``AccessToken`` as a local, and they write the
    row after leaving the ``except`` block (see the module docstring).
    """

    def decorate(fn: _F) -> _F:
        sig = inspect.signature(fn)

        def entry(token: tuple, args: tuple, kwargs: dict, exc, started: float) -> dict:
            try:
                arguments = _call_arguments(sig, args, kwargs)
            except Exception:
                arguments = REDACTION_FAILED
            return prepare_entry(
                tool=tool,
                scope=scope,
                token_id=token[0],
                token_name=token[1],
                arguments=arguments,
                exc=exc,
                duration_ms=_elapsed_ms(started),
            )

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                token = _token_identity(get_access_token())
                started = time.monotonic()
                error: BaseException | None = None
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    error = exc
                except anyio.get_cancelled_exc_class() as cancelled:
                    # The client timed out or disconnected (#689): audit the
                    # call as ERROR / CancelledError, then let the cancel go on.
                    error = cancelled
                # Outside the except block on purpose: nothing chains to `error`.
                row = entry(token, args, kwargs, error, started)
                # Shielded: a cancel arriving now must not drop the row (#689).
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(functools.partial(record_call, **row))
                if error is not None:
                    raise error
                return result

            wrapper: Any = async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                token = _token_identity(get_access_token())
                started = time.monotonic()
                error: Exception | None = None
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    error = exc
                # Outside the except block on purpose: a write failure must not
                # get the tool's exception (and its frames) as __context__.
                record_call(**entry(token, args, kwargs, error, started))
                if error is not None:
                    raise error
                return result

            wrapper = sync_wrapper
        setattr(wrapper, AUDITED_ATTR, True)
        return wrapper

    return decorate
