# MCP server (epic #620)

Design and roadmap: `docs/design/mcp-server.md`. This file records what the
code does today and the traps in it. Skeleton shipped in #638: mount, token
auth, scopes, one `ping` tool, the token CLI. #639 added the tool-call audit
log (`mcp_tool_calls`). #640 added the six read tools, #641 the five
`pipeline`-scope write tools, #642 the four `publish_request` approval-gate
tools. #643 added the client config (`.mcp.json`), moved the
`/fact-check-prompt`, `/fact-check-process` and `/research-process` skills onto
the tools (SQL kept as a fallback section), and the before/after harness
(`scripts.mcp_before_after`, runbook `docs/portfolio/mcp/runbook.md`).

## Tool catalogue (16 tools)

| Tool | Scope | One line |
|---|---|---|
| `ping` | `read` | reachability + "which token am I" (`token_name`, `scopes`) |
| `search_knowledge` | `read` | hybrid search over articles + verified claims (internal visibility) |
| `get_article` | `read` | one article by id or EN/ES slug, any status; optional content / claims / pipeline |
| `find_related` | `read` | related articles from stored centroids (no Voyage call) |
| `check_claim` | `read` | has PIC already checked this statement? (KB + article claims) |
| `pipeline_status` | `read` | weekly-series goals, task counts, capacity |
| `explain_stuck_goal` | `read` | why one goal is stuck + a *suggested* action |
| `get_deep_research_batch` | `pipeline` | next ≤ 25 `NEEDS_DEEP_RESEARCH` claims as a ready prompt (writes nothing) |
| `submit_fact_check_verdicts` | `pipeline` | write a deep-research verdict report back (converges on replay) |
| `submit_research` | `pipeline` | store a research document for a suggestion (checks, then PENDING) |
| `retry_agent_task` | `pipeline` | retry a FAILED / re-check a WAITING task (refused on publish-path kinds) |
| `skip_agent_task` | `pipeline` | skip a task (refused on wait gates and publish-path kinds) |
| `request_publish` | `publish_request` | file a publish request for an admin |
| `request_unpublish` | `publish_request` | file an unpublish request |
| `request_article_approval` | `publish_request` | file an approve-article request |
| `get_approval_status` | `publish_request` | read one approval request |

Scopes are independent (none implies another): `read` for the read tools and
`ping`, `pipeline` for the five writes, `publish_request` for the four
approval-gate tools. The skills need `pipeline`; the operator's laptop token is
`read,pipeline`. In Claude Code a tool is addressed as `mcp__pic__<tool>`
(server name `pic` from `.mcp.json`).

## Shape

- **SDK**: official `mcp` 2.x (`mcp>=2.3,<3` in `backend/requirements.txt`).
  The v2 class is `mcp.server.mcpserver.MCPServer`; `mcp.server.fastmcp` still
  exists as a module but only raises `ModuleNotFoundError` pointing at the
  migration guide. Wire types live in the separate `mcp_types` package (e.g.
  `ToolAnnotations`, snake_case fields such as `read_only_hint`), and the
  SDK's HTTP client is **`httpx2`**, not `httpx`.
- **Code**: `app/mcp/server.py` (server, tools, ASGI endpoint, routes),
  `app/mcp/auth.py` (token verifier, `require_scope`, token hashing) and
  `app/mcp/audit.py` (the audit wrapper and argument redaction).
- **Transport**: Streamable HTTP, `stateless_http=True`, `json_response=True`.
  Every POST gets one JSON body; no SSE, no `Mcp-Session-Id`, so either
  uvicorn worker serves any request and the shared nginx edge is unchanged
  (the `api.` vhost's `location /` proxies `/mcp` like every other path, with
  `Host $host` and the 300s read timeout).

## `MCPServer()` hijacks root logging, so construction is wrapped

`MCPServer.__init__` calls `logging.basicConfig(level="INFO")` with a rich
handler. Because `main.py` imports `app.mcp.server` before its own
`_configure_logging` runs, that would make ours a no-op in prod: root at INFO
(the third-party chatter #424 removed) and a `%(message)s` format.
`build_mcp_server` therefore restores the root logger's handlers and level
around construction (`_keep_root_logging`); `test_logging_config.py` caught it.

## Routes are exact paths, not a `Mount`

`mount_mcp(app)` (called at the end of `main.py`) appends two Starlette
`Route`s whose endpoint is the `McpHttpApp` ASGI object:

- `/mcp` — the MCP endpoint (outside `/api/v1`).
- `/.well-known/oauth-protected-resource/mcp` — RFC 9728 protected-resource
  metadata, served by the SDK.

Both are `include_in_schema=False`. A `app.mount("/mcp", …)` would be wrong
twice: Starlette 307s a bare `/mcp` to `/mcp/`, and the well-known URL must sit
at the host root (RFC 9728 inserts it *before* the resource path), not under
`/mcp/`. The SDK's own Starlette app routes by the full path, so the scope is
passed through unchanged.

## The session manager runs once per instance

`StreamableHTTPSessionManager.run()` raises if entered twice, and `/mcp`
answers nothing until it is running. `main.py`'s lifespan enters
`mcp_http.run()`, which calls `streamable_http_app()` **again** on every
entry (a fresh manager each time) and only then routes requests to it. That is
what lets the suite enter the app lifespan many times in one process
(`with TestClient(app)` in `test_database_session.py`, and every MCP test).
Outside a running lifespan the routes return **503** — `TestClient(app)`
without `with` never starts it, which is why the MCP tests drive the app
through `mcp_http.run()` themselves.

## Auth: tokens, scopes, two separate layers

- **Tokens** live in `mcp_tokens` (migration `0048_mcp_tokens`): `name`
  (unique), `token_hash` (SHA-256 hex, unique), `scopes` (`varchar[]`),
  `created_at`, `last_used_at`, `revoked_at`. Plaintext tokens start with
  `pic_mcp_` and are printed once by
  `python -m scripts.mcp_tokens create --name … --scopes read[,pipeline]`
  (`list` / `revoke --name …` too). Rows are never deleted.
- **`scopes` is an array, so its CHECK is not `status_check`.**
  `array_subset_check` (`app/models/enums.py`) builds
  `ck_mcp_tokens_scopes` as `scopes <@ ARRAY[...]::varchar[]` over `McpScope`
  (`read` / `pipeline` / `publish_request`); same stable-name, drop-and-recreate
  convention, and it is in `test_status_constraints.py` and
  `test_migration_check_vocabularies.py`.
- **Authentication is the SDK's.** `McpTokenVerifier` is passed with
  `AuthSettings(resource_server_url=https://api.panamaincontext.com/mcp,
  validate_token_resource=False, required_scopes=[])`; the SDK's
  `RequireAuthMiddleware` answers **401** with a `WWW-Authenticate` that names
  the metadata URL for a missing, unknown, non-`pic_mcp_` or revoked token,
  before any JSON-RPC is parsed. The DB lookup runs in a worker thread with
  its own short `SessionLocal()`, keyed by the token's SHA-256 (the plaintext
  never enters that frame); a DB error becomes a 401, not a 500.
  `last_used_at` is stamped at most once a minute per token (it is a liveness
  signal; the audit trail is `mcp_tool_calls`, below).
- **`issuer_url` is required by `AuthSettings` but there is no authorization
  server.** It is set to `https://api.panamaincontext.com` and appears in the
  protected-resource metadata; an OAuth-discovering client that tries to log
  in there will find nothing. Static bearer tokens are the only flow; full
  OAuth is a possible later step.
- **Authorization is per tool.** Any valid token may connect and
  `tools/list`. Calling a tool whose scope the token lacks returns an
  `is_error` result whose text starts `insufficient_scope:` (a
  `ScopeDeniedError`, a `ToolError` subclass, so the model can read it, the
  server logs no traceback, and the audit log can tell it from a failure). No scope
  implies another; `ping` itself requires `read`.
- **None of this touches the admin or driver auth.** An admin magic-link token
  or `agent_driver_token` is not an MCP token (no prefix, no row), and an MCP
  token is not accepted on any `/api/v1` route.

## Adding a tool

Register it with `pic_tool(server, McpScope.X, name=…, description=…,
annotations=ToolAnnotations(…))` inside `_register_tools` — never bare
`server.tool()`. `pic_tool` applies `require_scope` (stamps
`__mcp_required_scope__`) and, outside it, `audit_tool` (stamps
`__mcp_audited__`); `test_every_registered_tool_declares_a_scope` and
`test_every_registered_tool_is_audited` walk the registry for both.

- **Sync tools run in a worker thread** (`anyio.to_thread.run_sync`, the SDK's
  choice), and the request's contextvars (the access token) are visible there.
  So sync SQLAlchemy is fine in a tool.
- **Connection pool**: a tool opens its own `SessionLocal()`, closes it before
  returning, and never holds it across external I/O (Anthropic, Voyage, DeepL,
  Spaces) — close, call out, reopen. Long work returns a `crawl_jobs` id to
  poll, as the HTTP API does; the edge's 300s ceiling applies.
- **Host allowlist** (`TRANSPORT_SECURITY`): `api.panamaincontext.com` and
  localhost / 127.0.0.1 / [::1] (any port). Any other `Host` gets **421** —
  including a container-network name such as `backend:8000`. Origin is only
  checked when present.

## Audit log (`mcp_tool_calls`, #639)

Every tool call writes one row (migration `0049_mcp_tool_calls`): `token_id`
(FK to `mcp_tokens`), `token_name` (snapshot), `tool`, `required_scope`,
`args_redacted` (JSONB), `outcome`, `error_class`, `duration_ms`,
`created_at`. `outcome` (`McpCallOutcome`: `OK` / `ERROR` / `DENIED`) and
`required_scope` (`McpScope`) are `String` + named CHECKs, so **a new scope is
a drop-and-recreate on `ck_mcp_tool_calls_required_scope` as well as on
`ck_mcp_tokens_scopes`** (`test_migration_check_vocabularies.py` checks both).

- **The wrapper is outermost**, so a scope denial is logged as `DENIED`
  (`error_class = ScopeDeniedError`). Anything else raised is `ERROR` with the
  exception's *class name* only — the message is never stored (it can quote
  arguments). Calls the SDK rejects before the function runs (unknown tool,
  arguments failing the input schema) never reach the wrapper and are not
  logged.
- **Redaction** (`redact_args`): strings longer than `MAX_INLINE_STRING`
  (200) characters become `{"len": n, "sha256": hex}`, bytes always do; keys
  whose snake_case words include a credential word (`token`, `secret`,
  `password`, `auth`, `authorization`, `cookie`, `credential(s)`, `bearer`,
  `apikey`, `dsn`) or phrase (`api_key`, `private_key`, `access_key`,
  `session_key`) are dropped at any depth — word-based, so `max_tokens` and
  `claim_key` survive. The SDK-injected `Context` is never logged. So a
  research or article body passed to a tool is stored only as length + digest
  (`test_article_body_argument_is_never_stored` greps the raw row).
- **The audit write never changes the tool's result.** It runs after the tool
  returns or raises, in its own short `SessionLocal()` (worker thread for async
  tools), and any failure is swallowed: a DB / pool failure is
  `logger.exception` + `sentry_sdk.capture_exception`; a redaction failure is
  a `logger.warning` naming only the exception class, and the row stores
  `{"_redaction_failed": true}`. A call can therefore go unlogged while the DB
  is unhealthy; Sentry is where that shows.
- **A client timeout or disconnect still leaves a row (#689) — but late.**
  A sync tool runs in an anyio worker thread that a cancel never abandons; the
  thread writes the row itself once the body returns, with the body's own
  outcome (`OK` if the write committed). An async tool is cancelled at its
  next `await`; the wrapper catches the cancel, writes `ERROR` /
  `CancelledError` under a shielded cancel scope, and re-raises. The row's
  `created_at` is the *end* of the call, so a window query run right after a
  client timeout can miss a call still finishing (#643's "unaudited" submit
  was row 2, written 113 s in). Claude Code times out each HTTP MCP request
  after **60 s** by default (raise with `MCP_TOOL_TIMEOUT` or the per-server
  timeout; the env var's own default is ~28 h) — keep tool bodies well under it.
- **Sentry vs. the token and the arguments.** Sentry serializes the locals of
  every frame on a captured traceback *and on every chained exception*
  (`__cause__` / `__context__`); `AccessToken` carries the raw `pic_mcp_`
  bearer (the scrubber does not reliably catch its repr), and the tool and
  wrapper frames hold the raw arguments. What is guaranteed, and tested:
  - **No `AccessToken` local on any MCP path that reaches Sentry.** The audit
    wrappers reduce it to `(token_id, token_name)` on entry; `require_scope`'s
    `check` reads scopes through `_granted_scopes()` (its frame is on every
    denial's traceback); the verifier hashes first, and the `except` that logs a
    lookup failure (`_lookup_or_none`) holds only the digest.
  - **A failed audit write carries neither token nor raw arguments.** The
    wrappers write the row *after* leaving the `except` that caught the tool's
    exception (then re-raise it), so the write error is never implicitly
    chained to it; `record_call` takes only redacted scalars and also clears
    `__context__` before logging / capturing.
    `test_a_failed_audit_write_sends_no_token_or_body_to_sentry` builds the
    real SDK event (whole chain, locals on, through `scrub_pii_event`) for sync
    and async tools and OK / ERROR / DENIED.
  - **Not guaranteed by code shape: the tool's own crash.** The SDK
    `logger.exception`s an unexpected tool exception, chained to the original,
    whose frames (the tool's, and the wrappers') hold the raw arguments by
    nature. That is covered by the scrubber instead: `/mcp` is in
    `_VARS_STRIPPED_PATHS` (`app/core/observability.py`), so frame locals are
    dropped from any event Sentry attributes to the route. A tool should still
    not put a body in an exception *message*, which is not filtered.
  Keep this shape when touching either file: never call `record_call` (or
  anything that logs with a traceback) inside the tool's `except`, and never
  bind an `AccessToken` to a local on these paths.
- **Read it** at `GET /api/v1/admin/dashboard/mcp/calls` (`app/api/mcp_admin.py`;
  filters `token` = name, `token_id`, `tool`, `outcome`; `limit` ≤ 500,
  `offset`; newest first, with `total`). Admin-gated like `/admin/dashboard`
  and deliberately **not** on `DRIVER_ALLOWED_CALLS`. There is no admin grid
  yet. Nothing prunes the table.

## Read tools (#640)

Code: `app/mcp/tools/knowledge.py`, `app/mcp/tools/pipeline.py`, size cap in
`app/mcp/tools/bounds.py`. The functions are plain (no decorators) and are
registered in `server.py` from the `READ_TOOLS` tuple through `pic_tool`, all
with scope `read` and the shared `READ_ONLY` annotations. Each description says
when to use the tool, when not to, the return shape and one example;
`test_every_registered_tool_has_a_description_and_annotations` checks every
registered tool.

| Tool | Reads | Notes |
|---|---|---|
| `search_knowledge(query, language?, source_types?, k)` | `retrieval.search` | hybrid, no rerank, grouped by article, 3 chunks/group, 600-char snippets |
| `get_article(id_or_slug, language, include)` | `articles`, `fact_check_claims`, `agent_tasks` | id or EN/ES slug; `include` ⊆ content / claims / pipeline; body cut at 12k chars |
| `find_related(article_id, k)` | `related.find_related` (B2 centroids) | stored vectors only, no Voyage call |
| `check_claim(text, k)` | `retrieval.search` over `VERIFIED_CLAIM` + `ARTICLE_CLAIM`, plus a `claim_key` exact match | hydrates verdict / correction / citation from `verified_claims` / `fact_check_claims` |
| `pipeline_status(goal_key?)` | `agent_tasks`, `unfinished_goals`, capacity settings | ≤ 10 goals, ≤ 5 rows per status list per goal |
| `explain_stuck_goal(goal_key)` | `agent_tasks` only | verdict + blockers + *suggested* action |

- **Visibility.** Search and related use the retrieval service's `internal`
  visibility: APPROVED articles **including scheduled ones and taxonomy 5**,
  plus the KB. Drafts never appear there (no retrieval visibility includes
  them). `get_article` is a direct lookup and returns **any** status, drafts
  included, because pipeline diagnosis is about not-yet-approved articles.
- **`check_claim` returns INACCURATE and DISPUTED KB rows too** (that PIC
  found a statement false is an answer); the description tells the agent that
  only VERIFIED / CORRECTED are evidence and that "no match" means "not
  checked". It walks `SearchResult.chunks`, not the article groups, so several
  claims of one article are separate matches.
- **Degradation.** Without `VOYAGE_API_KEY` (or on a failed query embedding)
  the searches run full-text only and list it in `degraded`; they do not
  error. A full-sentence claim rarely matches in FTS-only mode
  (`websearch_to_tsquery` ANDs every word), which is what the `claim_key`
  exact match is for.
- **Pool.** `retrieval.search` embeds before its first statement and a
  `Session` checks out a connection only on its first statement, so calling it
  first on a fresh `SessionLocal()` holds no connection across the Voyage call.
  Keep it the session's first statement; that is also why no tool asks for a
  rerank (it runs after the stage queries, with the connection held).
- **Pipeline advice is a heuristic, not policy.** FAILED: `retry` when the
  error looks transient (timeout / 429 / 5xx / pool), `skip` for optional
  kinds (feature image, Instagram steps), else `investigate`. WAITING: `wait`
  (retry = "check now" once the operator is done). LEASED: `wait` (an expired
  lease is swept by the next claim). PENDING with satisfied or missing deps:
  `investigate`. `allowed_actions` mirrors `apply_task_action`. The actions
  themselves are #641.
- **Bounds.** Tools clip their own text fields; `bound_result` then halves the
  longest string (> 160 chars) or pops the last item of the longest list
  until the compact JSON is ≤ 20,000 chars, and sets `truncated`.
- **Inputs vs. the audit redaction.** `redact_args` stores lists of short
  strings verbatim; every list input here (`source_types`, `include`) is a
  closed enum with `maxItems ≤ 3`, and free strings have a `maxLength`
  (`test_tool_inputs_cannot_carry_a_body_as_a_list_of_short_strings` walks the
  input schemas). A future tool with a free-text list input needs a redaction
  change first.
- **Errors** are `ToolError`s starting `not_found:` / `invalid_goal_key:`; the
  SDK prefixes them with `Error executing tool <name>: `. They never quote the
  caller's text.

## Pipeline write tools (#641)

Code: `app/mcp/tools/writes.py`, registered from `PIPELINE_TOOLS` in
`server.py` with scope `pipeline`. **Each calls the service function its HTTP
twin calls**, and the routes were refactored onto those functions so there is
one implementation (`test_each_tool_uses_the_http_twins_service_function`
asserts the identity, `test_tools_call_the_service_functions` patches them).

| Tool | Service (HTTP twin) | Hints |
|---|---|---|
| `get_deep_research_batch(subject_type?, subject_id?)` | `fact_check.deep_research_batch` (`POST .../fact-check/deep-research/generate-prompt`) | read-only, idempotent |
| `submit_fact_check_verdicts(claim_ids, document, batch_id?)` | `fact_check.submit_deep_research_verdicts` (`.../deep-research/verdicts`) | not destructive, idempotent |
| `submit_research(suggestion_id, markdown, replace=false)` | `research_intake.submit_research` (`POST .../research/upload`) | destructive, idempotent |
| `retry_agent_task(task_id, reason)` | `agent_task_driver.apply_task_action` (`POST .../agent-tasks/{id}/action`) | not destructive, idempotent |
| `skip_agent_task(task_id, reason)` | same | destructive (SKIPPED is terminal), idempotent |

- **`get_deep_research_batch` writes nothing.** It is pipeline scope because
  it is the loop's first step; its hints say read-only. "Nothing pending" is a
  structured result (`nothing_pending: true`, `prompt_text: null`), not the
  route's 404. The prompt is not clipped: when 25 claims would exceed the
  20k result cap the tool asks the service for fewer (`limit`) and sets
  `limited_by_size`. Only a single claim too long for the cap by itself falls
  through to `bound_result` (then `truncated: true`).
- **`submit_fact_check_verdicts` takes `claim_ids`, not just `batch_id`.**
  The batch is stateless (`batch_id` is a timestamp label), so the batch's ids
  are what make the out-of-batch skip and the missing report work; `batch_id`
  is optional and echoed. A failed-run document (every verdict an uncited
  UNVERIFIABLE) or one with no `VERDICT:` line is a `rejected:` tool error,
  like the route's 400. A replay writes nothing and returns `replayed: true`
  (every verdict skipped as `claim is no longer pending (...)`). The claim
  reindex the route queues as a BackgroundTask runs **after the session is
  closed, in a daemon thread** (`_reindex_off_request` → `reindex_claims`,
  Voyage; never raises), so the tool returns without waiting for it (#689: in
  line it took 113 s in prod — Voyage's 60 s × 4-attempt document path —
  and blew Claude Code's 60 s request timeout). A worker restart can drop an
  in-flight reindex; the next one or `scripts.reindex_knowledge` catches up.
- **`submit_research` is the in-app `/research-process`.** The validation was
  already a service (`research_common.validate_research_document`: ≥ 4,000
  words, a Sources/References heading — compiled by Haiku from inline
  citations when missing — and ≥ 2 sub-topics covered, judged by Haiku against
  the suggestion's stored `sub_topics`, not the skill's prompt file). #641
  moved the upload route's insert/update into `research_intake.submit_research`.
  **The two callers differ only in flags, and so in behaviour:**
  - *MCP tool* (`converge_identical=True`, `force_available=False`, the
    agent's `replace`): the same body (trimmed, or followed by exactly the
    `---` + `## References` / `## Sources` section intake appends) returns the
    existing row unchanged with `action: "replayed"`, whatever its status, and
    runs no checks. A different body is `conflict:` unless `replace=true`;
    APPROVED research is never overwritten (no `force`; the conflict says only
    the operator can replace it), so an agent can never orphan articles.
  - *Upload route* (`replace=True`, its `force`, `converge_identical=False`):
    exactly its pre-#641 behaviour. No replay short-circuit: APPROVED is a 409
    without `force` even for the same file, and every accepted re-upload,
    identical or not, is re-validated, rewritten and reset to PENDING (so
    re-uploading a REJECTED document sends it back to review).

  Failed checks are a result
  (`accepted: false`, `checks`), not an error. The service commits before the
  Haiku calls and re-checks the existing row before writing. Nothing follows
  a research write on either path (not indexed, fact check kicked off from the
  admin grid, `WAIT_RESEARCH_READY` polls).
- **Retry/skip store a reason.** `apply_task_action` now takes `reason` and
  `actor` and appends an `agent_task_actions` row (migration
  `0050_agent_task_actions`: `task_id`, `action` CHECK over `AgentTaskAction`,
  `previous_status` CHECK over `AgentTaskStatus`, `reason`, `actor`,
  `created_at`). The tools require `reason` (≤ 1000 chars) and set `actor` to
  `mcp:<token name>` (read from the access token without binding it to a
  local); the HTTP route takes an optional `reason` and records the admin
  email (or `agent-driver`). Replays converge on both paths and write no row:
  `skip` on SKIPPED, `retry` on READY (the state a retry produces). History
  does not count: once the driver has claimed or finished the row a repeat
  retry is a 409 / `conflict:` naming LEASED / DONE, as are the other
  disallowed states. The response carries `replayed`.
- **Some kinds are the operator's alone (#686).** `MCP_REFUSED_TASK_ACTIONS`
  in `writes.py` maps kind → refused actions, checked (on the kind column
  only) before `apply_task_action`, so a refusal writes no action row and
  changes no status; it is still audited in `mcp_tool_calls` (`ERROR`,
  `ToolError`). The error starts `refused:` and names the kind and the admin
  Agent Tasks queue. Refused for every token, whatever its scopes:
  - **skip** on the four wait gates (`WAIT_RESEARCH_READY`,
    `WAIT_ARTICLE_CLAIMS_RESOLVED`, `WAIT_GRAMMAR_CHECKED_EN` / `_ES`):
    SKIPPED satisfies dependents like DONE, so the driver would run
    `APPROVE_ARTICLE` → `SET_PUBLISH_DATE` with unresolved claims or no
    Grammarly pass. **retry** on them stays allowed: it only makes the driver
    re-check the condition now.
  - **skip** on the check steps that feed those gates (#690):
    `ARTICLE_FACT_CHECK`, `APPLY_ARTICLE_CORRECTIONS`, and the legacy
    `RESEARCH_FACT_CHECK` / `APPLY_RESEARCH_CORRECTIONS` / `GRAMMAR_CHECK_EN` /
    `_ES` of goals planned before the wait kinds. `WAIT_ARTICLE_CLAIMS_RESOLVED`
    holds when the article has **no** claims, so a skipped fact-check hollows
    the gate out, and blog articles are not fact-check-gated at publish.
    **retry** stays allowed: it re-runs the check.
  - **skip** on a **PENDING** task with an unfinished dependency in its
    goal (#690, `_skips_ahead`), whatever the kind. The article chain is
    linear and a skip promotes what it unblocks, so skipping, say,
    `GENERATE_SERIES_SECTIONS` behind a FAILED `ARTICLE_FACT_CHECK` would
    promote `APPROVE_ARTICLE` past the fact-check. A PENDING row whose
    dependencies are all DONE / SKIPPED (or outside the goal: the "not
    promoted" blocker) stays skippable; read unlocked because DONE / SKIPPED
    never revert.
  - **retry and skip** on the publish path (`APPROVE_ARTICLE`,
    `SET_PUBLISH_DATE`, `APPROVE_IG_POST`, `SCHEDULE_IG_POST`): each one
    approves, publishes or schedules, which over MCP is an approval request
    (#642), never an action. Checked against every `KIND_CALLS` entry; no
    other kind approves, publishes or schedules.

  The admin `POST .../agent-tasks/{id}/action` is untouched. A new wait kind,
  a step a gate relies on, or a publish-path kind belongs in the map
  (`test_refused_set_is_every_wait_gate_skip_and_every_publish_path_action`
  binds it to `WAIT_CONDITIONS`). `explain_stuck_goal` keeps `allowed_actions`
  mirroring the admin queue but appends "Only the operator can … from the
  admin Agent Tasks queue" to the rationale of a refused kind (it still
  suggests `skip` for a failed `APPROVE_IG_POST` / `SCHEDULE_IG_POST`).
- **Errors** are raised after the `except` that caught the service exception
  has exited (no implicit chaining) and quote only our own messages.

## Approval gates (#642)

Code: `app/mcp/tools/approvals.py` (tools, registered from
`PUBLISH_REQUEST_TOOLS` with scope `publish_request`),
`app/services/mcp_approvals.py` (create / decide / execute),
`app/services/article_publishing.py` (the publish rules, shared with the
admin routes), `app/api/mcp_approvals.py` (admin queue), admin page
`frontend/src/pages/admin/AdminApprovals.jsx` at `/admin/approvals`.

| Tool | Does |
|---|---|
| `request_publish(article_id, rationale, publish_at?)` | files a `PUBLISH` request (`publish_at` must carry a timezone; omitted = server-picked slot at execution) |
| `request_unpublish(article_id, rationale)` | files an `UNPUBLISH` request |
| `request_article_approval(article_id, rationale)` | files an `APPROVE_ARTICLE` request (status -> APPROVED; does not publish) |
| `get_approval_status(request_id)` | reads one request (read-only hints) |

- **They never execute.** The only thing a tool writes is an
  `mcp_approval_requests` row (migration `0051_mcp_approval_requests`:
  `action` CHECK over `McpApprovalAction`, `article_id` FK (cascade),
  `params` JSONB, `rationale` (≤ 2000 chars in the input schema; hashed in the
  audit when > 200), `requested_by_token_id` FK to `mcp_tokens` +
  `requested_by` name snapshot, `status` CHECK over `McpApprovalStatus`,
  `decided_by` admin email, `decided_at`, `result` JSONB).
  `test_no_mcp_path_can_publish_unpublish_or_approve` calls every registered
  tool with an all-scopes token against a ready, a live and a draft article
  and asserts none changes and no request leaves `PENDING`.
- **No stored `APPROVED` status.** The ticket listed PENDING / APPROVED /
  REJECTED / EXECUTED / FAILED; approve executes in the same transaction, so a
  request goes PENDING -> EXECUTED or FAILED (or REJECTED) and an
  "approved, not run" state never exists.
- **Replays converge.** One PENDING row per (article, action) — a partial
  unique index `uq_mcp_approval_requests_pending` backs it under concurrency.
  The same action again returns that row with `replayed: true` (rationale may
  differ); different `params` is `conflict:` naming the pending request id.
  Requests that could only do nothing are refused up front: approving an
  APPROVED article, unpublishing one with no `published_at`. A publish request
  reports `publish_blockers` (the unmet preconditions now), informational only.
- **Execution** (`mcp_approvals.decide`): `SELECT … FOR UPDATE` on the request,
  then the action in `db.begin_nested()`. `ArticleNotFoundError`,
  `PublishPreconditionError`, `InvalidStatusTransitionError` and
  `SlugCollisionError` roll the savepoint back and record `FAILED` with
  `result.error` = the message (the route's 400 text); anything else
  propagates and the request stays PENDING. The second of two concurrent
  approvals waits on the lock, sees the decided row and gets it back with
  `replayed: true` (200), so the action runs once; reject on a decided-approved
  row (or approve on REJECTED) is a 409. Conditions are checked at decision
  time, not request time.
- **Admin surface**: `GET /api/v1/admin/dashboard/mcp/approvals?status=`,
  `POST …/{id}/approve`, `POST …/{id}/reject`, gated by plain
  `validate_admin_token` (not the driver-allowlisting dependency) and absent
  from `DRIVER_ALLOWED_CALLS`; a driver token gets 401. No email is sent on a
  new request (the ticket's optional notification was not built); the
  Approvals page in the admin sidebar is the queue.

## Clients (#643)

### Claude Code: `.mcp.json` at the repo root

```json
{"mcpServers": {"pic": {"type": "http",
  "url": "${PIC_MCP_URL:-https://api.panamaincontext.com/mcp}",
  "headers": {"Authorization": "Bearer ${PIC_MCP_TOKEN:-}"}}}}
```

- **No token in the repo.** Claude Code expands `${VAR}` and
  `${VAR:-default}` in an `.mcp.json` server's `url` and `headers` (also
  `command`, `args`, `env`) from the environment of the `claude` process. Both
  references carry a default on purpose: an unset variable *without* one makes
  Claude Code reject the whole config, whereas `${PIC_MCP_TOKEN:-}` sends an
  empty bearer, which the server answers with a plain **401**. So an agent
  worktree, CI, or a fresh clone without the variable just gets a `pic` server
  that fails auth, harmlessly; nothing else breaks. gitleaks scans the tree, so
  keep it that way (no literal `pic_mcp_…` anywhere, including docs examples).
- **Setting the token (outside the repo).** Mint it on the droplet
  (`docker exec pic-backend python -m scripts.mcp_tokens create --name
  operator-laptop --scopes read,pipeline`; printed once) and keep it in a
  file the repo never sees, e.g. `~/.config/pic/mcp.env` (chmod 600) holding
  `PIC_MCP_TOKEN=…` and `PIC_MCP_TOKEN_READ=…` (see the next bullet for which
  one a shell should export).
  `PIC_MCP_TOKEN` (`read,pipeline`) is what `.mcp.json` sends;
  `PIC_MCP_TOKEN_READ` (`read` only) is for the Inspector and demos — to run
  Claude Code read-only, start it with
  `PIC_MCP_TOKEN="$PIC_MCP_TOKEN_READ" claude`.
- **Keep the `read,pipeline` token out of everyday shells.** Any session in
  which `pic` is approved (and every subagent it spawns) can call the prod
  pipeline writes with whatever `PIC_MCP_TOKEN` it inherits. So export
  `PIC_MCP_TOKEN` only for skill / cron runs — the cron sources the env file
  itself, and a manual skill run can be `PIC_MCP_TOKEN="$(…)" claude -p …` —
  and let interactive shells carry only `PIC_MCP_TOKEN_READ`.
- **Local server.** `docker-compose up -d backend db`, mint a token against
  the **local** DB (`docker-compose exec backend python -m scripts.mcp_tokens
  create --name local-dev --scopes read,pipeline`; check `DATABASE_URL` first,
  the dev container can point at prod), then start Claude Code with
  `PIC_MCP_URL=http://localhost:8000/mcp PIC_MCP_TOKEN=<local token> claude`.
  `localhost:<port>` is on the host allowlist; a container name such as
  `backend:8000` gets 421.
- **Project servers are opt-in.** Claude Code asks once per checkout whether to
  enable the servers in a project `.mcp.json` (stored in that checkout's local
  settings), and a non-interactive `claude -p` does not ask. So approve `pic`
  once in an interactive session in the operator checkout before relying on it
  from cron (`scripts/research-cron.sh`, which also reads the token from
  `${PIC_MCP_ENV_FILE:-~/.config/pic/mcp.env}` because cron has no login shell).
  `/mcp` inside Claude Code shows whether `pic` connected.
- **`claude -p` also needs the tool allowed**: `--allowedTools` takes
  `mcp__pic__<tool>` names (the cron allows `mcp__pic__submit_research`).

### Skills on MCP

`/fact-check-prompt` → `get_deep_research_batch`, `/fact-check-process` →
`submit_fact_check_verdicts`, `/research-process` → `submit_research`. Each
skill keeps its old psql procedure as a `## Fallback: Direct SQL` section
(MCP unreachable / no token, or the operator-confirmed failed-run override,
which MCP cannot do). The file conventions under `ai_generated_content/` are
unchanged. Behaviour that differs from the SQL path:

- **One fact-check batch per `/fact-check-prompt` run.** The batch is the
  oldest pending claims and stateless, so a second `get_deep_research_batch`
  before the first batch's verdicts are submitted returns the *same* claims
  under a new `batch_id` (the tool description says so since #643; "call again
  while `pending_total > claim_count`" holds only *after* submitting, or with a
  different scope). The SQL path writes several batch files in one run.
- **`/research-process` no longer overwrites.** Existing research with a
  different body is a `conflict:` (the file stays put) unless the operator asks
  for `replace`, and APPROVED research is never replaced over MCP. The SQL path
  silently UPDATEd, APPROVED rows included.
- **Server-side checks differ from the skill's old hand checks**: sub-topic
  coverage is judged by Haiku against the suggestion's stored `sub_topics`
  (not the prompt file), and a missing Sources section is compiled and
  appended rather than failing the file.
- **The whole document travels as a tool argument.** `submit_research`'s
  `markdown` is the full 5-8k-word body, so the agent emits it as output
  tokens (≈ 10-15k tokens per document); the audit stores only its length +
  sha256. Verdict documents are small.
- **The SQL fallback skips the claim re-index** the tool does
  (`reindex_claims`); run `python -m scripts.reindex_knowledge --all` after a
  fallback write (only `--all` refreshes the verified-claims KB chunks).

### MCP Inspector

`npx @modelcontextprotocol/inspector`, transport **Streamable HTTP**, URL
`http://localhost:8000/mcp` or `https://api.panamaincontext.com/mcp`, header
`Authorization: Bearer <token>`. Use the Inspector's default **proxy**
connection for prod: a direct browser connection sends a cross-origin request
from `localhost:6274`, which the prod CORS config (site origins only) does not
allow. Steps and the screenshots to capture are in
`docs/portfolio/mcp/runbook.md`.

## Traps (quick list)

- An HTTP 401 (with a `WWW-Authenticate` naming the metadata URL) means the
  bearer is missing / empty / unknown / revoked / not `pic_mcp_…`; a tool result starting `insufficient_scope:` means
  the token is valid but lacks the tool's scope. Neither is a server fault.
- 421 = `Host` not on the allowlist (e.g. a Docker service name); 503 = the
  app's lifespan is not running (`TestClient(app)` without `with`).
- Arguments that fail the input schema and unknown tools are rejected by the
  SDK **before** the audit wrapper, so they leave no `mcp_tool_calls` row.
- `submit_fact_check_verdicts`'s `batch_id` must be exactly `YYYYMMDD_HHMMSS`;
  a `_b2`-suffixed id from the SQL fallback fails the schema (omit it).
- Replays converge (`replayed: true`), so measuring the old path and the MCP
  path needs two **different** batches (`docs/portfolio/mcp/runbook.md`).

## Tests

`tests/test_mcp_server.py` drives the real `app` with the SDK's `Client` over
`httpx2.ASGITransport` inside `mcp_http.run()`, and patches
`app.core.database.SessionLocal` to the test engine (the verifier reads it
through the module so the patch reaches it). `tests/test_mcp_write_tools.py`
covers #641 the same way (seeded claims, tasks, suggestions; the research
validator is patched). `tests/test_mcp_read_tools.py`
calls the read tool functions directly under the same patch, plus a few calls
over `/mcp`; it fakes `embeddings.embed_query` and asserts no rerank.
`tests/test_mcp_approvals.py` covers #642 (tools, admin decisions,
concurrency with two real sessions, the driver denial, the walk over every
registered tool).
`tests/test_mcp_before_after.py` covers the #643 harness (parsing, analysis,
rendering, and the read-only `measure` query). The SDK client round trip
(initialize, `tools/list`, calls) that an Inspector session shows is already
exercised by `test_mcp_server.py`.
