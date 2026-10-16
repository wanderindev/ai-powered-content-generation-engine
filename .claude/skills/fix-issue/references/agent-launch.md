# Agent-launch preamble + dispatch shape

After the brief is assembled and (unless `--no-review`) approved, the skill launches the agent. The brief content is **inlined** into the Agent tool's `prompt` parameter — not passed as a file path. The worktree is branched from `main` and may not contain the brief file (the brief was just written and isn't merged), so the agent must receive the full brief in its prompt.

## The Agent tool invocation

```
Agent({
  description: "fix-issue #<N> — <short>",
  subagent_type: "general-purpose",
  isolation: "worktree",
  run_in_background: true,
  model: "opus",                    // implementation runs on Opus — see model note below
  prompt: <PREAMBLE + "\n\n---\n\n" + FULL_BRIEF_CONTENT>
})
```

`run_in_background: true` so the orchestrator returns to the operator immediately and is notified on completion — do not poll, do not sleep.

**Model: dispatch implementation agents on `model: "opus"` (operator decision, 2026-07-25 — supersedes the 2026-05-30 Sonnet default).**

The brief remains the load-bearing artifact, and most implementation runs are mechanical execution of an already-verified contract — work Sonnet handled cleanly (#413 and #424 were both first-pass ready-for-review PRs with full suites green and zero brief-vs-source disagreements). **What the Opus tier buys is judgment in the case where the brief turns out to be wrong mid-run**: noticing that the source contradicts the contract, and correctly applying the four-shape disagreement taxonomy instead of silently papering over the mismatch. That is the failure mode a stronger tier actually mitigates — not the mechanical edits.

The original Sonnet default was a **cost** guardrail, and that constraint is gone (the operator moved to the $200 subscription tier, ~4x the prior usage headroom). But the guardrail's underlying insight still stands and must not be discarded with it: **the expensive failure mode is NOT the model tier per call — it is a stuck agent that balloons its own context** (a 300-400-tool-call run re-processes its growing context every turn, and on long runs that re-read happens uncached past the prompt-cache TTL). The thing that bounds that is the brief's **runaway-iteration guard** (see `brief-template.md`) — bail (push the branch, report blocked) at ~40 tool calls, on the third attempt at the same fix, or when self-recovering from a bad push. **Keep that guard regardless of tier.** Observed runs for calibration: #413 = 33 tool calls, #424 = 17.

Override to `"sonnet"` for a genuinely trivial mechanical sweep if you want to conserve quota; otherwise the default is Opus.

## The preamble (prepend to the inlined brief)

```
You are an autonomous agent resolving GitHub issue #<N> in the Panama In Context codebase. You were launched from the orchestrator's main session via the Agent tool with isolation: "worktree" — you are in an isolated git worktree branched from main. The orchestrator is NOT in the loop during your run.

Your complete briefing follows the separator below. It is your contract. It was assembled by verifying every codebase-fact claim against source at brief-writing time. If you read the source and it contradicts the brief, follow the source and flag the discrepancy in your PR description per the four-shape disagreement taxonomy in the brief.

Key reminders:
- Failure-mode policy: if any self-review item fails, push the branch and report it as BLOCKED, naming the failed item. An honest "blocked on X" report is a good outcome.
- Do not open the PR and do not merge. Push the branch; the orchestrator ships it with the personal:ship skill (pre-PR QA, PR, GitHub auto-merge).
- Stay strictly within the brief's IN scope. The OUT-of-scope list is binding.
- Report back when done: branch, ready-vs-blocked, the PR body, what shipped, any flags.

Begin by reading the brief below in full, then follow its "Begin by" section.

---

<FULL_BRIEF_CONTENT>
```

## After launch

The skill returns to the operator with the launch confirmation(s) (the agent id(s) and a one-line "agent dispatched for #N, will report on completion") and ends the turn. When each completion notification arrives, the skill proceeds to step 6 (report + instrument) of the workflow for that issue.

## Notes

- **This is the implementation agent.** It is dispatched by the orchestrator *after* a brief-writing agent has returned the brief and the orchestrator has gated it (see `brief-agent-launch.md` and `SKILL.md`). Do not confuse the two: the brief-agent is read-only and returns text; this agent is worktree-isolated and pushes a branch.
- **One or many.** A single `/fix-issue` invocation may dispatch several implementation agents (one per issue). Launch the non-file-overlapping ones in parallel (single message, multiple Agent calls); serialize or warn on overlaps.
- **The agent inherits the auto-approve fence.** `.claude/settings.json` deny rules and the user-level `personal` guard apply to the agent: force-push, push-to-main, non-auto merges, `gh pr create` without pre-PR QA, `.env*` writes, prod registry push. For an agent-friendly (no-prod-touch) fix, none of these should fire; if the agent reaches for a denied command, it has drifted out of scope and should report blocked rather than retry.
- **Branch and PR naming** are specified in the brief's PR-shape section; the agent follows them.
