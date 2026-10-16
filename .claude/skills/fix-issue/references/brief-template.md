# Brief template

This is the de-experimented distillation of the Phase 2 F-1 through F-4 prompt corpus (`docs/phase-2/prompts/`). It carries the disciplines without the experiment scaffolding (no wave-coordination notes, no methodology-testing framing, no F-N nomenclature). The `fix-issue` brief-writing agent fills the `{{PLACEHOLDERS}}` from the verification step (`verification-checklist.md`) and returns the result; the orchestrator gates it and inlines it into the implementation agent's prompt.

Every placeholder must become a statement verified against source this session. If a placeholder can't be filled from verification, the verification is incomplete — go back.

---

```markdown
# Fix brief — issue #{{N}}: {{ISSUE_TITLE}}

## Identification

You are an autonomous agent resolving issue #{{N}} in the {{REPO}} codebase. You were launched via the Agent tool with `isolation: "worktree"` — you are in an isolated git worktree branched from `main`. The orchestrator is NOT in the loop during your run; you finish, push your branch and report; the orchestrator ships it with `personal:ship`.

This brief is your contract. Every codebase fact below was verified against source at brief-writing time. If you read the source and it contradicts this brief, follow the source and flag the discrepancy in your PR description (see the disagreement taxonomy below).

## Operational notes

{{OPERATIONAL_NOTES}}
<!-- e.g. for frontend lint/build: "Your worktree has no frontend/node_modules (gitignored). Near the start of your run: `ln -s {{MAIN_CHECKOUT_ABS_PATH}}/frontend/node_modules frontend/node_modules`. Zero diff impact." -->
<!-- e.g. for backend: `scripts/run-tests.sh` is the canonical, worktree-safe runner — see the TEST DISCIPLINE and backend-test notes below. -->
<!-- TEST DISCIPLINE (cost guardrail, 2026-05-30; revised) — the brief MUST tell a backend agent to follow this exact flow:
     1. ITERATE on the adjacent test files only: `scripts/run-tests.sh pytest tests/<target_file>.py -q --tb=short` (NOT `-v`, never the full suite during iteration) — fast feedback, tiny output.
     2. ONCE the adjacent tests pass, run the FULL suite ONCE as the final gate, `-q`: `scripts/run-tests.sh`. This is UNCONDITIONAL — do NOT gate it on "did I touch shared code?", because that is exactly the judgment that fails (a conftest/fixture change can silently break distant tests while the adjacent file stays green — the fix-issue-shared-fixture-full-suite lesson). A passing full run prints ~one line, so it is nearly free in tokens; the cost only appears on failure, where you want it.
     3. If the final full run FAILS: fix it, but DROP BACK to scoped runs to iterate — do NOT loop on the full suite (a full-suite retry loop is the expensive pattern). Re-run the full suite once more only to confirm green. If you cannot reach green within the runaway bounds (~40 tool calls / Failure-mode guard), push the branch and report it BLOCKED, naming the failing tests.
     Also: reuse an existing venv / skip redundant `pip install` if deps are already present. Rationale: the dominant token sink is a failing-test retry loop with verbose output — scoped+quiet iteration plus a single quiet full-suite gate kills it while still catching cross-test breakage before the CI round-trip (so the ready-vs-blocked decision is correct). -->
<!-- STALE-STATE DISCIPLINE: if a tool result comes back empty or a command is cancelled, do NOT fire a storm of retry/probe commands — re-run the ONE command once, and if still unclear, proceed from what you know or push and report blocked. Probe storms bloat context. -->
<!-- BACKEND TESTS — use `scripts/run-tests.sh` (issue #204). It is the one canonical, worktree-safe runner: it spins a throwaway container from the project image, bind-mounts THIS worktree's `backend/` + the docker socket (so testcontainers can spawn its Postgres), runs as root, and publishes NO host ports — so it never collides with the operator's running `docker-compose` stack and always tests the worktree's code (not the main checkout). Do NOT spin `docker-compose up` from a worktree (port 8000/5433 conflicts) and do NOT write a temp `docker-compose.agent.yml`. Invocations: `scripts/run-tests.sh` (full suite), `scripts/run-tests.sh pytest tests/<file>.py -q --tb=short` (iterate one file), `scripts/run-tests.sh ruff check app` (the exact CI lint gate). Pass `--build` only if you changed `backend/requirements.txt`. -->

## When this brief and the source disagree — the four shapes

Recognize which shape applies and respond accordingly:

1. **Brief said exclude, source implies include** → include it and flag in the PR description.
2. **Brief is factually wrong about the codebase** → follow the source, not the brief; flag in the PR description.
3. **Brief is correct for the primary case but didn't anticipate a sub-case** → follow the brief AND surface the tension in the PR description for the reviewer to decide.
4. **You see a clearly-improvable adjacent thing within the issue's intent** → make the improvement and flag it transparently. Do NOT expand scope beyond the issue's intent.

In all four: the PR description is where you surface the disagreement. Never silently work around a brief-vs-source mismatch.

## The task (verified facts)

{{TASK_VERIFIED_FACTS}}
<!-- The fix, stated against verified file:line references. The exact change. The canonical pattern to mirror (with its file:line), if applicable. The exact count of sites, if a sweep. Any issue-body-vs-source drift, with the correction. -->

## Scope

### IN scope
{{IN_SCOPE}}

### OUT of scope (do NOT touch)
{{OUT_OF_SCOPE}}
<!-- The OUT list is as load-bearing as the IN list. Name the adjacent files/regions the agent must not modify. -->

## Default rules for likely ambiguities

{{DEFAULT_RULES}}
<!-- Pre-resolved answers to every ambiguity anticipated in verification: exact variable names, exact strings, which of two patterns to follow, what to do with adjacent-but-out-of-scope code, etc. Each rule here is an ambiguity the agent won't have to resolve unsupervised. -->

## Failure-mode escape hatch

If the brief's primary path is blocked — the operation is structurally impossible, a required endpoint/method/field doesn't exist, the change would require out-of-scope work — STOP, push the branch, and report it as **blocked** with exactly what's blocked. If the issue body anticipates an alternative (e.g., "if X isn't possible, the right answer is Y"), implement that alternative and note it. An honest "blocked on X; did Y instead" report is a good outcome; a branch reported ready that silently worked around the block is a worse one.

**Runaway-iteration guard (cost guardrail, 2026-05-30).** This is binding. If you find yourself in ANY of these states, STOP immediately, push the branch, and report it as blocked, describing where you got stuck — do NOT keep grinding:
- You have made roughly **40+ tool calls** on this single issue, or
- You are on your **third attempt** at the same fix (e.g. commit → test fail → redo → fail again), or
- You have already committed/pushed and are now **rewriting/reverting your own commits** to recover from a mistake, or
- A parallel tool batch got cancelled and you are unsure of your own state.

A stuck agent that keeps retrying is the single most expensive failure mode — its context balloons and is re-processed every turn, and on long runs the re-read happens uncached past the prompt-cache TTL. Bailing to a blocked report at the ~40-call mark and letting the operator look is far cheaper than a 400-call self-recovery loop. When you bail, say in your report exactly what you tried and where it broke.

## Self-review checklist (before pushing)

Run this list. If any item fails, report the branch as **blocked**, naming the failed item.

{{SELF_REVIEW_CHECKLIST}}
<!-- Issue-specific checklist. Always include: only the in-scope files modified; lint clean vs main baseline (no new issues); build succeeds; PR description complete; production-touch line present. -->

## PR shape

- **Branch**: `fix/issue-{{N}}-{{SHORT_SLUG}}`
- **Title**: `fix(#{{N}}): {{SHORT_DESCRIPTION}}`
- **PR body** (the orchestrator opens the PR from your report, so include all of it): a one-line summary; a **"Production touch: yes / no — verified by:"** line; the self-review checklist with each item marked; a test plan; `Closes #{{N}}`; and the `🤖 Generated with [Claude Code](https://claude.com/claude-code)` footer.
- **Ready vs blocked**: ready if all self-review items pass; blocked otherwise (failure-mode escape hatch).
- **Do not open the PR and do not merge.** Commit, push the branch (`git push -u origin <branch>`), and report. The orchestrator ships ready branches with the `personal:ship` skill (pre-PR QA → PR → GitHub auto-merge → post-merge steps); the guard hook refuses `gh pr create` without that QA anyway.

## Deliverable on completion

Report back to the orchestrator with: the branch name, ready-vs-blocked status, the PR body (summary, production-touch line, self-review checklist, test plan, `Closes #{{N}}`), what shipped, and any flags you surfaced. {{INSTRUMENTATION}}
<!-- Default: just "Report back to the orchestrator." Do NOT instruct the agent to write docs/agent-fixes/agent-friendly-outcomes.md — that file is git-untracked and the ORCHESTRATOR appends the row locally after you report (cost guardrail 2026-05-30: agents writing it in their worktrees made every concurrent PR conflict on it). Add a session-report instruction here only for unusually complex issues. -->

## Begin by

1. {{FIRST_STEP}} <!-- e.g. symlink node_modules, if frontend lint/build needed -->
2. Read the issue (`gh issue view {{N}}`) and the files named in "The task" above; confirm the verified facts still hold.
3. Make the change, staying strictly within IN scope.
4. Run the project's lint/build/test as named in operational notes; iterate until clean.
5. Self-review checklist.
6. Push the branch (ready if every checklist item passed; blocked otherwise).
7. {{INSTRUMENTATION_STEP}} <!-- leave blank by default — the orchestrator writes the local-only outcomes-log row after you report; the agent does NOT touch docs/agent-fixes/agent-friendly-outcomes.md -->
8. Report back and STOP.
```

---

## Notes for the skill filling this template

- **Strip every placeholder.** A brief shipped with a `{{...}}` still in it is a broken brief. If you can't fill one, the verification was incomplete.
- **Keep it as short as the issue allows.** The F-N briefs ranged ~100-160 lines; a one-character fix needs far less than a 30-site sweep. Match the brief's length to the issue's actual complexity. Tightness is not verbosity.
- **The OUT-of-scope list is where scope-creep is prevented.** Spend real effort on it, especially for sweeps and same-file work.
- **Inline the filled brief into the agent's prompt** (per `agent-launch.md`) — do not rely on the brief file being present in the agent's worktree.
