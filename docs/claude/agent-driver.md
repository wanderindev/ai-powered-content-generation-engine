# Weekly-Series Agent Driver (epic #422)

> Split out of the root `CLAUDE.md` to keep it under Claude Code's size
> limit; same rules, same authority. "Above" / "below" references may
> point to `CLAUDE.md` or to a sibling file in `docs/claude/`.

### Weekly-Series Agent Driver (epic #422)
An unattended driver runs the weekly content pipeline as a graph of `agent_tasks`
rows (`app/models/agent_task.py`, migration `0025_agent_tasks.py`). `plan_week`
(`app/services/weekly_plan.py`) is an **insert-only ensure-pass**: it only creates
missing rows keyed by `(goal_key, kind, subject_type, subject_id)` and never mutates
a row it did not just create — promoting `PENDING` → `READY` is the driver's job.
Lease/attempt behaviour is configured by `agent_task_lease_minutes` /
`agent_task_max_attempts` (`app/core/config.py`).

**The cadence — and with it the goal-key shape — is a flag, so "weekly" is only
the default.** `weekly_plan_daily_cadence` (`app/core/config.py`, default
`False`) switches `goal_key_for` and `_sabias_target_dates` (`weekly_plan.py`) between
one series per ISO week — `weekly_series:YYYY-Www`, sabías-qué
Tuesday/Wednesday — and one series per **day**:
`daily_series:YYYY-MM-DD`, sabías-qué day+1 and day+2. (The
article publish date used to be planned here too — the coming Friday / day+1 —
until #558 moved it to run time, and the promo — the coming Saturday / day+2 —
until #576 made it follow that date; see "The publish date is picked when the
series is finished" below.) For an *existing* goal the flag no longer
decides anything: `plan_week` passes `_sabias_target_dates` the resolved goal
key's prefix (`daily_series:` or not), so a goal expanded after a flip keeps
its own IG date pattern (#587). Its comment records it being turned
on in production on 2026-09-03 to burn through the research backlog, and since
the code default is `False` that is an env var on the droplet, not the checked-in
value — so read the setting before assuming a drive's goal key. Two consequences:
the goal-key **prefix is not a constant**, so anything written against
`weekly_series:` misses every daily goal (`GET /admin/dashboard/agent-tasks`
takes `goal_key` as an exact-match filter and orders `goal_key.desc()`, which
sorts *every* `weekly_series:` goal above *every* `daily_series:` one once both
exist); and the ensure-pass converges per goal, so under daily cadence a drive
on a later date plans a fresh goal rather than resuming yesterday's unfinished
one — unless the in-flight cap below refuses it.

**A new goal starts only while fewer than `weekly_plan_max_in_flight` series are
unfinished (#559, default 2).** Daily cadence alone started ~7 series a week,
far faster than the operator's laptop work (deep research, Grammarly, the wait
kinds) drains them. So `plan_week` first checks whether the call would start a
**new** goal — no row carries this call's goal key yet *and* no explicit
`research_id` was passed — and, if so, counts `unfinished_goals`: every goal key
with any row not `DONE` / `SKIPPED` (`PENDING`, `READY`, `LEASED`, `WAITING`
**and `FAILED`** all hold a slot). At the cap it plans nothing and answers
**200** with `at_capacity: true`, `goal_key` / `research_id` **null**,
`created: 0`, `tasks: []`, `max_in_flight`, `in_flight_goals` (the keys holding
the slots) and a human `detail` — deliberately not the 404 "No research
available", so the driver (managed-agents#62) ends its session normally instead
of emailing a problem, and the check runs *before* research resolution, so
capacity wins over an empty backlog. Every other answer carries
`at_capacity: false`. Three things to know:

- **Expansion is never capped.** Every `PLAN_ARTICLES` row's call re-sends its
  goal's `week_start` **and, since #573, its `goal_key`**, so it targets an
  existing goal and skips the check — the goal is itself one of the slots, and
  capping it would deadlock. The explicit `goal_key` wins over the key
  recomputed from `week_start` (which still anchors the IG target dates),
  because recomputing misses in two cases: a row planned before #418 has no
  `week_start` (today's date is used), and flipping `weekly_plan_daily_cadence`
  mid-goal changes the key's shape. A miss answers `at_capacity` or plans an
  unrelated goal, and the driver marks the `PLAN_ARTICLES` row DONE with
  nothing expanded. `goal_key` is shape-checked (422 unless
  `weekly_series:YYYY-Www` / `daily_series:YYYY-MM-DD`) and must already have
  rows (404 otherwise), so it can only expand — never start a goal past the
  cap. Rows planned before #573 carry no `goal_key` in their payload, so since
  #587 `build_calls` falls back to the row's own `agent_tasks.goal_key` column
  (into a copy of the payload, never the row's JSONB) — every `PLAN_ARTICLES`
  row, pre-#573 included, now sends its goal key. The column is used only when
  it matches the plan-week shape pattern (`GOAL_KEY_PATTERN`); otherwise it is
  omitted and the old recomputed-key behaviour applies. The IG target dates
  follow the goal key's shape, not the live cadence setting. An
  explicit `research_id` (the operator's override / re-plan path) is never
  capped either. Only the driver's empty-queue call and the admin "Plan This
  Week" button (both empty-body) are paced.
- **Stuck goals hold slots forever, honestly.** An old goal parked on a
  `FAILED` row (with its dependents `PENDING` behind it), including a legacy
  `weekly_series:` goal or a September daily goal, counts until a human acts.
  The admin queue's per-task **skip** action (`POST .../agent-tasks/{id}/action`,
  `{"action": "skip"}`) marks the row `SKIPPED` and promotes what it unblocks;
  skip down to the last row (or retry, if it can still succeed) and the goal
  stops counting. `in_flight_goals` in the at-capacity answer names exactly
  which goals to look at. There is no age-based exclusion on purpose.
- **`0` or a negative value disables the cap.** `weekly_plan_daily_cadence`
  still decides a **new** goal's key shape, and that shape then decides the
  goal's IG target dates; the cap only decides *whether* a new key gets
  planned.

Authentication has three dependencies in `app/api/dependencies.py`, not one:

- `validate_admin_token` — ordinary admin magic-link auth.
- `validate_admin_or_driver_token` — also accepts the long-lived
  `agent_driver_token` service token, wholesale, on the **agent-tasks router only**.
- `validate_admin_or_allowlisted_driver_token` — gates the `dashboard`, `instagram`
  and `admin` routers; accepts the driver token **only** for a `(method, path)` on
  `DRIVER_ALLOWED_CALLS`, and otherwise falls through to admin validation so an
  off-surface driver token 401s like any unknown token (no 403 oracle).

An empty `agent_driver_token` setting disables driver auth everywhere; admin
magic-link tokens are unaffected.

**The graph the planner emits is fixed chains plus two expansion rows.**
`_ARTICLE_CHAIN` and `_IG_CHAIN` (`app/services/weekly_plan.py`) are ordered
tuples, each task depending on the previous one, and the IG order
(approve → render → schedule) is a **hard precondition of the endpoints** —
`PATCH /instagram/posts/{id}/schedule` requires the post APPROVED *and* already
rendered — not a stylistic preference. The article chain has **one fan-in**:
`GENERATE_SERIES_SECTIONS` regenerates the *whole* series and requires every
member translated, so the chain is planned in two passes split at
`_SERIES_SECTIONS_AT` — every article's chain up to the feature image first, then
series sections onward — and that task depends on its own predecessor **plus
every article's `TRANSLATE` task and every article's ES grammar step**
(`WAIT_GRAMMAR_CHECKED_ES`, or a legacy `GRAMMAR_CHECK_ES`; #558), not just its
own. The ES grammar step is the last one that changes the text, so the series
meets there with final copy. Three planner behaviours
follow from the insert-only rule and are easy to misread as bugs:

- **Fan-out is modelled as a row, not a callback.** The per-article tasks cannot
  exist before `GENERATE_OUTLINE` creates the `articles` rows, so the planner
  emits an explicit `PLAN_ARTICLES` task whose execution is *calling plan-week
  again* — the ensure-pass **is** the expansion, and being a row makes it
  inspectable, leasable, retryable and failable like every other step. There are
  **two per goal** (one on the research, one on the series parent article), which
  coexist under the unique constraint because their subjects differ.
- **The planner writes `SKIPPED` rows itself**, with a `payload["reason"]`,
  whenever a `READY` row would be un-runnable: an outline for research that
  already has articles, sabías-que drafts that already exist, series sections for
  a standalone article, or a step behind a planner flag. There are
  **two such flags, both defaulting to `False`**: the feature image while
  `weekly_plan_feature_images_enabled` is off (the endpoint has existed since
  #419); and every Instagram step while `weekly_plan_ig_posts_enabled` is off (#552 — the
  IG account is not connected, so drafts only piled up in the content
  calendar): `GENERATE_SABIAS_QUE`, `GENERATE_ARTICLE_PROMO` and any per-post
  `APPROVE_IG_POST` / `RENDER_IG_POST` / `SCHEDULE_IG_POST` chain, reason
  `instagram posts disabled (weekly_plan_ig_posts_enabled)`. With that flag off
  the generators never run, so no drafts exist and the per-post chains are
  normally **never planned at all** — the week is just the article chains; a
  draft that already exists (pre-flag or hand-made) gets its chain planned
  SKIPPED. Both `PLAN_ARTICLES` rows still run, since SKIPPED satisfies their
  deps. (A third, `weekly_plan_grammar_checks_enabled`, planned the LLM
  `GRAMMAR_CHECK_EN` / `_ES` steps SKIPPED; #556 removed it along with those
  steps — see the grammar waits below.) Both flags gate the planner only; neither disables its endpoint, and
  flipping one affects only rows planned afterwards (insert-only). The chain
  shape stays uniform either way.
- **Three sabías-que drafts are generated; the week schedules two.** The two
  lowest-id drafts get task rows (lowest → the earlier target date, next → the
  later one; Tuesday/Wednesday under weekly cadence) and the
  surplus draft gets **none** — left a `PENDING` draft for manual use, and
  deliberately *not* marked `SKIPPED`, because the planner never touches rows it
  did not create.

**The driver is a dumb shell loop; the pipeline knowledge is `KIND_CALLS`.**
Each `AgentTaskKind` renders the ordered HTTP calls that execute it and those
calls ship *in the `next` response*, so the driver is a stateless
claim-call-report loop with no pipeline knowledge of its own — which is what
lets the pipeline be tested in this repo. `tests/test_agent_task_driver.py`
asserts `set(KIND_CALLS) == {k.value for k in AgentTaskKind}`, so a new kind
cannot ship without someone deciding what it calls. The two multi-step shapes
(poll a background job; preview-then-apply corrections) carry a `{placeholder}`
plus a prose `notes` contract on purpose — deliberately not a DSL.

Three state-machine rules in `agent_task_driver.py` are easy to "simplify" into
a stalled or cascading week:

- **`SKIPPED` satisfies a dependency, exactly like `DONE`.** Reporting either
  promotes every `PENDING` row in the goal whose deps are all terminal-satisfied.
  The planner creates `SKIPPED` rows on purpose (the feature-image gate, series
  sections for a standalone article), so counting only `DONE` silently stalls the
  whole week. One pass suffices — a freshly promoted row is `READY`, and `READY`
  never satisfies a dependency. **The planner makes a skipped step a
  pass-through, not a shortcut (#702):** `_Planner.ensure` adds, transitively,
  every `SKIPPED` dependency's own `depends_on` to the new row's, so the stored
  graph keeps the order and the driver needs no rule of its own. Without it a
  standalone article's `APPROVE_ARTICLE` (behind the "not a series" sections row
  and the flag-skipped feature image) started `READY` before `WRITE_ARTICLE`;
  the same pass-through now holds the promo `PLAN_ARTICLES` behind the parent's
  approval while IG posts are off, and the recovery path's `WRITE_ARTICLE` /
  research `PLAN_ARTICLES` behind `WAIT_RESEARCH_READY`. Insert-only: rows
  planned before #702 keep their old `depends_on`.
- **`FAILED` never cascades.** Under `agent_task_max_attempts` the row goes back
  to `READY`; at the ceiling it stays `FAILED` — and either way its dependents
  stay `PENDING`, because a human may fix the step by hand and re-run it.
- **The lease sweep runs at the top of `next`**, not on a background tick. A
  claim is `FOR UPDATE SKIP LOCKED` and `attempts` increments on claim only; an
  expired lease returns the row to `READY` unless `attempts` already hit the
  ceiling, in which case it goes `FAILED` so a task that reliably kills its
  driver cannot loop between claimed and expired forever. `report_task`
  deliberately does **not** check the leaseholder (single-driver deployment;
  `leased_by` is bookkeeping, not an ownership lock), so a late report against a
  swept-back `READY` row is accepted and only terminal/`PENDING` states 409.

**Steps blocked on the operator go `WAITING`, not `FAILED` (#553).** Some
steps can only finish after the operator works on the laptop (deep research in
the pic-extension, Grammarly EN/ES). Those are **wait kinds**: an entry in
`WAIT_CONDITIONS` (`agent_task_driver.py`) maps the kind to a read-only
`condition(db, subject_id)` returning `None` when it holds or a short
operator-facing "waiting for" text, and the kind's `KIND_CALLS` entry is
`_wait_check(kind)` — one `GET /admin/dashboard/agent-tasks/wait-check/{kind}/{subject_id}`
(agent-tasks router, so no allowlist entry). `satisfied=false` → the driver
reports `WAITING` with that `waiting_for`; `satisfied=true` → `DONE`. The
lifecycle rules:

- **`WAITING` refunds the claim's attempt** (`attempts - 1`), so a week of
  daily drives waiting on the operator never parks the row at
  `agent_task_max_attempts`. It records `waiting_for` and sets
  `next_check_at = now + agent_task_wait_recheck_hours` (default 20h, i.e. the
  next daily drive).
- **`next` claims `READY` rows *and* `WAITING` rows whose `next_check_at` has
  passed** — in the same locking select, not via a sweep, so the admin page
  keeps showing `WAITING` until a driver actually re-checks. Within one drive a
  freshly waiting row is not handed out again, so the driver's loop ends.
- **`WAITING` satisfies nothing**: dependents stay `PENDING` until the gate
  reports `DONE`. Any non-`WAITING` report clears `waiting_for` /
  `next_check_at`.
- **Only wait kinds may report `WAITING`** (400 otherwise). A driver that
  predates it reports `FAILED` on a wait task, which still works — the old
  three-attempts-and-park path.
- From the admin queue, `retry` on a `WAITING` row means **"check now"**
  (`READY`, `next_check_at` cleared, attempts reset) and `skip` is allowed.

#553 shipped the mechanism with an empty `WAIT_CONDITIONS`; the concrete gates
are research-ready (#554, `WAIT_RESEARCH_READY`), article deep research (#555,
`WAIT_ARTICLE_CLAIMS_RESOLVED`) and Grammarly EN/ES (#556,
`WAIT_GRAMMAR_CHECKED_EN` / `_ES`).

**The research gate replaced the research fact-check steps (#554).** The
operator fact-checks research and applies its corrections *before* approving
it, so new plans no longer carry `RESEARCH_FACT_CHECK` /
`APPLY_RESEARCH_CORRECTIONS`. Wave 0 is now `WAIT_RESEARCH_READY` (a READY
root) → `GENERATE_OUTLINE` and `GENERATE_SABIAS_QUE` → `PLAN_ARTICLES`. Its
condition (`_claims_resolved(FactCheckSubject.RESEARCH, …)` in
`agent_task_driver.py`, reusable for any fact-check subject) holds when the
research has **no `PENDING` / `NEEDS_DEEP_RESEARCH` claims — including no claims
at all**, the operator's call: approved research nobody fact-checked is ready.
It does **not** look at `Research.status`; the planner only picks `APPROVED`
research to begin with (`next_research_for_planning`, and an explicit
`research_id` 400s otherwise), so unapproved research never gets a goal. Three
traps:

- **The planner does not pre-evaluate the gate** — it always plans it `READY`
  and the first wait-check decides, so an already-clean research costs one
  claim and one GET.
- **In-flight goals keep their legacy rows.** If the goal already has an
  `APPLY_RESEARCH_CORRECTIONS` row, the planner chains anything new off it and
  plans no gate (insert-only: the existing outline's `depends_on` already points
  at it). The two old kinds therefore stay in `AgentTaskKind`, the CHECK and
  `KIND_CALLS`; don't delete them while such goals can exist.
- **The research corrections skip (#481, below) is now legacy-only** — the
  research-side planner code path that used it is gone (and since #555 the
  article side too).

**The article gate holds corrections until deep research lands (#555).**
Article grounding now stores `ABSENT` claims as `NEEDS_DEEP_RESEARCH`
(`docs/claude/fact-check.md`), so each article chain is now
`ARTICLE_FACT_CHECK` → `WAIT_ARTICLE_CLAIMS_RESOLVED` →
`APPLY_ARTICLE_CORRECTIONS`. The gate reuses
`_claims_resolved(FactCheckSubject.ARTICLE, …)`: no `PENDING` /
`NEEDS_DEEP_RESEARCH` claims (none at all also holds; the corrections preview
then 400s "run a fact-check first", which is the right failure for an unchecked
article). Two traps:

- **In-flight chains keep fact-check → corrections.** When the article already
  has an `APPLY_ARTICLE_CORRECTIONS` row, the planner plans no gate for it — the
  existing corrections row's `depends_on` points at the fact-check and the
  ensure-pass never rewrites it, so a gate there would gate nothing (it would
  only sit in the queue as a `WAITING` row). Siblings planned later get the gate.
- **`APPLY_ARTICLE_CORRECTIONS` is never planned `SKIPPED` any more** — the
  #481 plan-time "no correctable claims" skip is retired. A `SKIPPED`
  corrections row satisfies the next step (`WAIT_GRAMMAR_CHECKED_EN` since
  #556) at insert time, so the chain
  would run around the gate (and the fact-check); and the claims visible at
  plan time are stale, because `ARTICLE_FACT_CHECK` re-grounds. A resolved-clean
  article now costs one wait-check plus the preview's 200-with-no-edits.

**Grammar is the operator's Grammarly pass, waited for in EN and ES (#556).**
New plans no longer carry the LLM `GRAMMAR_CHECK_EN` / `GRAMMAR_CHECK_ES`
steps; the chain is now `APPLY_ARTICLE_CORRECTIONS` → `WAIT_GRAMMAR_CHECKED_EN`
→ `GENERATE_TAGS` → `TRANSLATE` → `ES_ACCURACY_CHECK` (#557, below) →
`WAIT_GRAMMAR_CHECKED_ES` → `GENERATE_FEATURE_IMAGE`. Each wait holds until the article's
`grammar_checked_en_at` / `grammar_checked_es_at` stamp exists — written by
`grammar-apply` from the pic-extension (`docs/claude/blog.md`; an empty
`accepted` list stamps, `stamp=False` deliberately does not). The EN wait sits
after the corrections so a correction never rewrites Grammarly'd text, and
before `TRANSLATE` so the Spanish is made from the polished English. Three
traps:

- **The extension's readiness is meant to come from the same rows.**
  `GET /admin/dashboard/articles` (the extension's queue) exposes
  `ready_for_grammar_en` / `_es`, true only while that article's wait is
  `READY` / `WAITING` — so `PENDING` (corrections or translation still ahead),
  `LEASED`, `DONE` and `SKIPPED` all read false, and so does any stamped
  article. An article with **no** wait task (not pipeline-driven, or planned
  before #556) keeps the old has-content-and-no-stamp rule. The extension
  doesn't read these flags yet (it still queues on content-and-no-stamp,
  `APPROVED` only unless `grammarIncludeDrafts` is on, until pic-extension#38
  lands). Driven articles are drafts until `APPROVE_ARTICLE`, so by default it
  doesn't offer them during the waits; with drafts included it can offer one
  whose wait is still `PENDING`; once it does, skipping a wait from the admin queue will also drop
  it from the extension's queue.
- **In-flight chains keep their LLM rows.** When the article already has a
  `GRAMMAR_CHECK_EN` (or `_ES`) row, the planner chains off it and plans no
  wait (`_LEGACY_GRAMMAR` in `weekly_plan.py`) — the next step's `depends_on`
  already points at it. The two LLM kinds stay in the enum, the CHECK and
  `KIND_CALLS`, and the endpoint plus the admin grid buttons stay for manual
  runs; only the planner stopped using them.
- **`weekly_plan_grammar_checks_enabled` is gone.** With no LLM grammar step
  left to plan it would have been inert config; `Settings` ignores unknown env
  keys, so a leftover `WEEKLY_PLAN_GRAMMAR_CHECKS_ENABLED` on the droplet is
  harmless.

**After `TRANSLATE`, the Spanish is checked against the English (#557).**
`ES_ACCURACY_CHECK` is a normal (non-wait) kind calling `POST
/admin/articles/{id}/es-accuracy-check` (`app/services/es_accuracy.py`, on the
`/admin` pipeline-step router next to grammar-check). One Sonnet call
(`effort: "low"`, `feature="es_accuracy"`) reads numbered EN/ES segment pairs —
title, excerpt, then the bodies paragraph by paragraph (or the two whole bodies
as one segment when the paragraph counts differ) — and returns `FIND ||| REPLACE`
fixes for the **Spanish only**, meaning differences only (numbers, dates, names,
negations, omitted or added claims), never style: Grammarly owns style, and
runs next. It reuses the grammar check's parser and exactly-once applier; every
parsed pair is a `grammar_check_fixes` row with `source='ES_ACCURACY'`,
`language='es'` (migration `0039` widened `ck_grammar_check_fixes_source` along
with `ck_agent_tasks_kind`). Four traps:

- **Body only.** Both bodies are cut with `split_series_sections` and the ES
  About / Continue Reading blocks are re-attached byte-for-byte, so a FIND that
  only occurs in a section is skipped as "not found".
- **No stamp, so replays converge by re-running.** Nothing on the article
  records that the check ran; zero fixes is a 200, and a replay after a lost
  response re-spends one Sonnet call and comes back with its already-applied
  FINDs skipped (or `NONE`) — never a 400 (#495).
- **REPLACE goes through `normalize_es_thousands`** (#531), so a fix cannot
  reintroduce the dot thousands form; a pair that normalisation turns into a
  no-op is dropped without a row. The prompt also says separator differences
  are not errors.
- **In-flight chains get no check.** When the article's chain already has its
  ES grammar row — legacy `GRAMMAR_CHECK_ES`, or a `WAIT_GRAMMAR_CHECKED_ES`
  planned before #557 — the planner inserts no `ES_ACCURACY_CHECK`
  (`_ES_GRAMMAR_KINDS` in `weekly_plan.py`): that row already depends on
  `TRANSLATE` and is never rewritten, so a check planned now would run in
  parallel with Grammarly instead of before it. Because the ES wait now depends
  on the check, `ready_for_grammar_es` stays false until the
  check is `DONE` (the wait is `PENDING` until then).

**The publish date is picked when the series is finished (#558).** With the
operator waits an article takes days, so a date fixed at planning time was
stale by the time `SET_PUBLISH_DATE` ran. New `SET_PUBLISH_DATE` rows carry no
payload, and the kind's call is `PATCH /admin/dashboard/articles/{id}/publish`
with **no body**; the endpoint then picks the instant itself
(`pick_publish_slot`, `app/services/publish_slot.py`): the next 12:00 UTC slot
on a Panama calendar day (UTC-5, no DST) that is at least tomorrow and on which
no article **outside this series** has a `published_at` (any status, any hour —
a manual publish blocks its day too). One series per day. Four traps:

- **Same date for the whole series, and replays converge (#495).** An article
  that already has a `published_at` keeps it; otherwise a series member
  (`series_parent_id` parent + children) that already has one lends it. Only
  the first member to publish picks a slot, so the siblings' calls never grab
  the next day.
- **Legacy rows ignore their payload.** `KIND_CALLS` renders at claim time, so
  an in-flight `SET_PUBLISH_DATE` planned with `target_publish_date` also goes
  through the run-time pick — its stale planned date is deliberately dropped.
- **In-flight series keep the narrower fan-in.** An existing
  `GENERATE_SERIES_SECTIONS` row keeps its `TRANSLATE`-only `depends_on`
  (insert-only); only rows planned now get the ES-grammar fan-in. `TRANSLATE`
  stays in the new fan-in because a legacy `GRAMMAR_CHECK_ES` may be `SKIPPED`
  (planned behind the retired grammar-checks flag), which satisfies at once.
- **The admin UI still sends a date**, which is applied as given — manual
  publishing is unchanged. No locking: the single serial driver is what keeps
  two series from picking the same day at once.

The Instagram promo follows the publish date (#576). The planner no longer
gives the promo's `SCHEDULE_IG_POST` a `target_date`; it plans it with no
payload and a **second dependency on the parent article's `SET_PUBLISH_DATE`**,
and `_schedule_ig_post` then sends **no body**, so the schedule endpoint derives
publish day + 1 at 17:00 UTC from the real `published_at` (and 409s if there is
none yet — the driver reports FAILED). Replays converge on the same time. A
legacy promo row planned before #576 still carries its stale `target_date`
(day+2 / the coming Saturday) and sends it — insert-only; with
`weekly_plan_ig_posts_enabled` off such chains were planned SKIPPED anyway.
Sabías-qué rows keep their planned `target_date` (Tue/Wed, or day+1/day+2 on
the daily cadence). None of this posts anything while
`instagram_publishing_enabled` is off (see `instagram.md`).

**A stalled week is unstuck from the admin queue, not by SSH — and that is a
third endpoint, not the report protocol.** `POST
/admin/dashboard/agent-tasks/{task_id}/action` (`apply_task_action` in
`agent_task_driver.py`, #420) takes `retry` (from `FAILED`, or `WAITING` as
"check now") or `skip`, and `report_task` cannot
serve either because it rejects any starting state other than `LEASED` / `READY`.
Two rules in it are easy to get wrong:

- **`retry` resets `attempts` to 0**, deliberately. At the ceiling the sweep
  fails an expired lease outright and a `FAILED` report parks the row, so a
  retry that left the counter alone would buy one attempt and no lease-expiry
  grace. The row's `error` is left as visible history for the next report to
  overwrite.
- **`skip` is allowed only from `PENDING` / `READY` / `FAILED` / `WAITING`** (`_SKIPPABLE`).
  `LEASED` is excluded on purpose — a driver is actively working the row, so the
  operator waits for its report or for the sweep. A skip marks the row `SKIPPED`
  and promotes whatever that unblocks, which works precisely because `SKIPPED`
  satisfies a dependency exactly like `DONE` (above).
- **Every applied action is logged, and replays converge (#641).** The body
  takes an optional `reason`; `apply_task_action` appends an
  `agent_task_actions` row (`action`, `previous_status`, `reason`, `actor` =
  the admin email, `agent-driver`, or `mcp:<token>` from the MCP
  `retry_agent_task` / `skip_agent_task` tools, which call this same function
  and require the reason). `skip` on a `SKIPPED` row and `retry` on a `READY`
  row (the state a retry produces) return 200 with `replayed: true`, change
  nothing and write no row; they used to 409. Retry on LEASED / DONE /
  PENDING is still a 409, even right after a retry.

`AdminAgentTasks.jsx` is the page, and it is deliberately a plain grouped list
rather than an AG Grid.

**A driver-called endpoint must converge on replay, not 400 "already done"
(#495).** The driver can lose a response *after* the server has committed — the
2026-09-01 drive timed out client-side while `generate-outlines` was still
working, the three W36 articles were written, and the replayed
`GENERATE_OUTLINE` task then hit the endpoint's old 400 "Articles already exist"
guard. Because `FAILED` never cascades (above), that would have parked the task
at `agent_task_max_attempts` and stranded every dependent task of the week. So
`POST /admin/dashboard/articles/generate-outlines` now **returns the existing
articles with 200 and `articles_created=0`**, in the same
`OutlineGenerateResponse` shape, instead of refusing. Note this is *not* the
same lever as the planner's `SKIPPED` row for the same condition: the planner
still skips a fresh outline step when articles exist so no claim is spent on a
no-op, while the endpoint's idempotency is what rescues a replay the planner
never saw. A new driver-facing endpoint needs the endpoint half too — an
already-satisfied precondition is a success for a replaying driver, and only a
genuinely un-runnable state should 4xx.

Two more endpoints have since taken that shape from the same failure mode: the
corrections preview (below), and `generate_sections_for_series`, which now
returns `(True, "Series sections already generated", 0)` when **every** member
already carries `ABOUT_MARKER_EN`. The planner schedules one series-sections
task per member while each run covers the whole series, so the siblings have to
converge rather than park on the #99 refuse-on-rerun 400 — which now fires only
for a **partially** marked series, an inconsistent state a human has to
untangle.

**Rule when touching the driver:** a new or changed `KIND_CALLS` entry
(`app/services/agent_task_driver.py`) needs a matching **anchored,
method-qualified** regex in `DRIVER_ALLOWED_CALLS` unless it targets the
agent-tasks router. A prefix or route *template* over-grants (`/articles/{id}`
also serves GET/PUT/DELETE; `PATCH /{entity}/{entity_id}/status` is generic over
suggestions/research/articles). `tests/test_agent_task_driver.py` binds the two
lists — #444 exists precisely because that binding was missing and the driver
401'd on everything except `PLAN_ARTICLES`.

**Corrections steps skip when there is nothing to apply (#481) — historical
at plan time; the runtime half below is what still runs.** `plan_week` used
to plan `APPLY_ARTICLE_CORRECTIONS` (until #555) and `APPLY_RESEARCH_CORRECTIONS` (until #554) as `SKIPPED`
(reason `no correctable claims`) once the subject's fact-check has resolved with
no `INACCURATE`+correction claim — every existing research is in that state, so
without the skip the week dies on the corrections preview's 400 every Saturday.
Unresolved verdicts (`PENDING` / `NEEDS_DEEP_RESEARCH`) keep the step runnable
so it parks for a human instead of skipping past uncorrected content.

A plan-time skip cannot help a task planned *before* its fact-check ran, so the
preview endpoint carries the matching runtime rule and is now **three-way**: a
resolved-clean subject is a **200** with `correctable_claims=0` and an empty
`edits` list, unresolved verdicts are still a **400** (retry later / park), and a
subject with *no* claims at all is still a **400** ("run a fact-check first") so
an unchecked subject cannot silently no-op. Apply mirrors it — an empty `edits`
list returns 200 with refreshed counts and corrects nothing — because the driver
pipes the previewed edits through verbatim, so an empty preview arrives as an
empty apply.
