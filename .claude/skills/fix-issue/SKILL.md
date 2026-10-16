---
name: fix-issue
description: Resolve one or more GitHub issues with autonomous agents — invoked as "/fix-issue <number> [<number>...] [--review|--no-review]". For each issue the skill spawns a read-only brief-writing agent that verifies the issue's claims against the actual source and drafts a tight brief, gates the returned brief, then launches a worktree-isolated implementation agent that pushes a branch and reports back, then ships each ready branch with personal:ship (pre-PR QA, PR, GitHub auto-merge). Single id is reviewed by default; multiple ids run autonomously by default (override with --review / --no-review). Use when the operator says any of "fix issue N", "fix #N", "fix issues N and M", "have an agent fix N", "batch-fix these issues", "dispatch issue N to an agent", "run the autonomous fix on N", or otherwise wants filed issues resolved by unattended agents. The skill orchestrates brief-writing and dispatch (the load-bearing work); the launched agents do the verification and the code. The methodology this encodes is documented in docs/pilot/phase-1-synthesis.md and docs/pilot/phase-2-addendum.md.
---

# fix-issue — autonomous issue resolution (one or many)

## What this skill does — and what it doesn't

This skill takes one or more GitHub issue numbers and drives each to a shipped change written by an autonomous agent. It encodes the audit-to-autonomy methodology from the Phase 1 synthesis and the Phase 2 addendum (`docs/pilot/phase-2-addendum.md`). The central finding that methodology produced: **the brief is the load-bearing variable for autonomous quality — not the agent, not the issue.** The skill therefore spends almost all its effort on producing and gating tight briefs, and treats the code-writing dispatch as the easy part.

For each issue, the pipeline is **two agents, orchestrator-coordinated**:

1. **Brief-writing agent (read-only).** Reads the issue and the source, walks the verification checklist, assembles the brief from the template, and **returns it**. It writes no files and dispatches nothing. (`references/brief-agent-launch.md`.)
2. **Implementation agent (worktree-isolated).** Receives the gated brief inlined in its prompt, makes the change, runs lint/build, pushes its branch, reports back. (`references/agent-launch.md`.)

The **orchestrator** (the main session running this skill) sits between them: it spawns the brief-agents (in parallel for a batch), saves and **gates** each returned brief, runs the review gate when applicable, dispatches the implementation agents (in parallel), and logs outcomes. The orchestrator does NOT write the briefs itself, does NOT write code, and does NOT merge directly: it ships each ready branch with the `personal:ship` skill (pre-PR QA → PR → GitHub auto-merge → the post-merge steps in CLAUDE.md `## Shipping`).

A single id is just a batch of one: same pipeline, with the review gate on by default.

> **Why brief-writing is delegated to an agent now (a change from the single-issue-only version).** Doing N verifications serially in the orchestrator's own context does not scale and bloats context. Parallel read-only brief-agents fix that. The methodology's integrity is preserved by two compensating controls that did not exist before: the brief-agent's prompt *mandates* the verification checklist, and the orchestrator runs a *non-skippable* well-formedness + production-touch gate on every returned brief (below). Review is the third, mode-dependent layer.

## Invocation & modes

`/fix-issue <number> [<number> ...] [--review | --no-review]`

- **One id, no flag → review ON.** Assemble the brief, surface it, wait for approval before dispatching.
- **Two or more ids, no flag → review OFF (autonomous).** Gate and dispatch without a per-brief approval stop.
- **`--review`** forces the review gate ON (e.g. a batch you want to eyeball). **`--no-review`** forces it OFF (e.g. a single fix you trust). The explicit flag always wins.

The review gate is the only mode-dependent gate. Every other gate below runs in all modes.

## Gates

1. **Agent-friendly gate (non-negotiable).** Each issue must carry the `agent-friendly` label. Checked by the orchestrator up front (before spawning brief-agents, to avoid wasting runs). Any id missing the label: list them and STOP — require an explicit per-issue override before including them. The methodology reserves autonomous execution for the audited-tractable subset.
2. **Verification gate (non-negotiable).** No brief is assembled from the issue body alone. The brief-agent must satisfy `references/verification-checklist.md` against current source. Enforced by the brief-agent's prompt and confirmed by gate 3.
3. **Well-formedness + production-touch gate (non-negotiable — the autonomous-mode safety net).** On every returned brief the orchestrator checks: no unfilled `{{placeholders}}`; IN/OUT scope present; failure-mode + self-review sections present; agent-friendly confirmed; and `production-touch: none`. If a brief has unfilled placeholders or missing sections → HOLD that issue and surface it. If `production-touch: YES` or the brief-agent flagged `tractable: no` → HOLD and surface (the label may be wrong; the issue may need pair-mode). **This gate is never skipped, even with `--no-review`** — it is what makes autonomous batch dispatch safe.
4. **File-overlap check (advisory).** Compare the brief-agents' IN-scope file lists across the batch. Overlapping issues will collide at merge time (each agent works in its own worktree/branch/PR, so work isn't corrupted, but PRs will conflict). Flag overlaps to the operator; prefer to run a broad sweep (many files) alone or first.
5. **Review gate (mode-dependent — see Invocation & modes).** When ON: surface the gated brief(s) + verification summaries and wait for approval; incorporate edits/redirects before dispatching. When OFF: skipped — gates 1–4 still apply.

## Workflow

### Step 0 — Parse the invocation
Collect the issue ids and detect `--review` / `--no-review`. Resolve the review mode per *Invocation & modes*.

### Step 1 — Agent-friendly gate (orchestrator, up front)
`gh issue view <N> --json number,title,labels` for each id (run them in parallel). Apply gate 1. Drop or override any id lacking `agent-friendly` before proceeding.

### Step 2 — Spawn brief-writing agents (parallel, read-only)
One per surviving id, per `references/brief-agent-launch.md`. For a batch, launch them all in a single message (multiple Agent tool calls) so they run concurrently. They read the main checkout — no worktree.

### Step 3 — Collect and gate the briefs
As briefs return: save each to `docs/agent-fixes/prompts/fix-issue-<N>.md` and add a one-line `INDEX.md` entry (prompt-preservation). Run gates 3 and 4. Hold and surface any issue that fails; let the rest proceed.

### Step 4 — Review gate
Per the resolved mode (gate 5). ON: present the brief(s) and verification findings (drift, canonical pattern, IN/OUT scope, counts, production-touch verdict); wait for approval. OFF: proceed directly with the gate-3-clean briefs.

### Step 5 — Dispatch implementation agents
For each cleared issue, launch via the Agent tool per `references/agent-launch.md`:
- `subagent_type: "general-purpose"`, `isolation: "worktree"`, `run_in_background: true`
- `prompt`: the orienting preamble with the **full brief content inlined** (the worktree is branched from `main` and may not contain the brief file).

For a batch, dispatch the non-overlapping issues in parallel (single message, multiple Agent calls). For file-overlapping issues, either serialize them or dispatch with an explicit conflict warning. The skill then returns to the operator with the launch confirmations and ends the turn — completions notify automatically (do not poll).

### Step 6 — Report and instrument (on each agent's completion)
As each implementation agent reports back:
- Summarize its branch (ready-vs-blocked, what shipped, any flags surfaced).
- **The ORCHESTRATOR appends the outcomes row, not the agent.** Append one row to `docs/agent-fixes/agent-friendly-outcomes.md` (`Agent attempted: yes`, `Outcome: not-yet-attempted`; flip to `clean-merge` / `needs-revision` once it ships). This file is **git-untracked / local-only** (see `.gitignore`) — do NOT commit it and do NOT include it in any PR. It used to be appended by each implementation agent inside its own worktree, which made every concurrent agent PR conflict on this one file, forcing an `update-pr` + CI run per PR. Keeping it local and orchestrator-written removes that conflict class entirely (cost guardrail, 2026-05-30).
- **Ready** → run `personal:ship` on that branch, using the agent's reported PR body (summary, production-touch line, self-review checklist, test plan, `Closes #N`) as the PR body (one at a time when branches overlap; merge `origin/main` in if it fell behind). **Blocked** → don't open a PR; report the branch and the agent's blocker to the operator.

## Production-touch posture

This skill targets `agent-friendly` issues, which by the six-criterion rubric exclude auth/payment/PII handling and schema migrations. The expected production-touch is **none**. The brief's PR-shape section requires a "Production touch: yes/no" line, and gate 3 holds any brief whose verification surfaced a production touch — the label may be wrong, or the issue may need pair-mode rather than autonomous handling.

## Instrumentation level (operator standing decision, 2026-05-28)

- **Outcomes-log row: yes** — one per dispatched issue, **written by the orchestrator into the local-only (git-untracked) log**, never by the implementation agent and never committed. Cheap; gives ongoing data; feeds the eventual repo-2 comparison the addendum names.
- **Full session report: no, by default.** The PR description carries the substance. A brief may request one for an unusually complex issue, but it is not the default.

## Worked methodology reference

The disciplines this skill enforces were validated across 16 autonomous runs on PIC (Phase 2, F-1 through F-4). The evidence: `docs/phase-2/agent-friendly-outcomes.md` (16 clean-merge), `docs/methodology/cross-session-register.md`, `docs/pilot/phase-2-addendum.md` §3 (the seven fixing-phase patterns). The skill operationalizes addendum §3's pattern 3 (brief-tightness disciplines), pattern 4 (agent-vs-brief taxonomy), and pattern 5 (failure-mode escape hatch).

Batch mode — multiple independent agent-friendly issues, the file-overlap check, parallel brief-writing and dispatch — was the deliberate future extension the single-issue version named; it is now built here. The 2026-05-28 "one issue at a time" standing decision is superseded by the orchestrator-coordinated two-agent pipeline (operator decision, 2026-05-29), whose autonomous-mode safety rests on gate 3 being non-skippable.
