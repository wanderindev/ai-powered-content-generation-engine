# Agent-launch preamble + dispatch shape

After the brief is assembled and (unless `--no-review`) approved, the skill launches the agent. The brief content is **inlined** into the Agent tool's `prompt` parameter — not passed as a file path. The worktree is branched from `main` and may not contain the brief file (the brief was just written and isn't merged), so the agent must receive the full brief in its prompt.

## The Agent tool invocation

```
Agent({
  description: "fix-issue #<N> — <short>",
  subagent_type: "general-purpose",
  isolation: "worktree",
  run_in_background: true,
  model: "sonnet",                  // implementation runs on Sonnet — see cost note below
  prompt: <PREAMBLE + "\n\n---\n\n" + FULL_BRIEF_CONTENT>
})
```

`run_in_background: true` so the orchestrator returns to the operator immediately and is notified on completion — do not poll, do not sleep.

**Model: dispatch implementation agents on `model: "sonnet"` (cost guardrail, 2026-05-30).** The load-bearing thinking is the brief, which is written by the (Opus) brief-agent and the orchestrator. The implementation agent is executing an already-verified contract — mechanical edits a brief has de-risked — so Sonnet is sufficient and materially cheaper. The expensive failure mode is NOT the model tier per call; it is a **stuck agent that balloons its own context** (a 300-400-tool-call run re-processes its growing context every turn, and on long runs that re-read happens uncached past the 5-min prompt-cache TTL at the 1M premium tier). Sonnet keeps that compounding cheaper. The brief's runaway-iteration guard (see `brief-template.md`) is what bounds the loop itself. Only override back to Opus for an issue the brief explicitly flags as needing heavier reasoning during implementation.

## The preamble (prepend to the inlined brief)

```
You are an autonomous agent resolving GitHub issue #<N> in the Panama In Context codebase. You were launched from the orchestrator's main session via the Agent tool with isolation: "worktree" — you are in an isolated git worktree branched from main. The orchestrator is NOT in the loop during your run.

Your complete briefing follows the separator below. It is your contract. It was assembled by verifying every codebase-fact claim against source at brief-writing time. If you read the source and it contradicts the brief, follow the source and flag the discrepancy in your PR description per the four-shape disagreement taxonomy in the brief.

Key reminders:
- Failure-mode policy: if any self-review item fails, open the PR as a DRAFT with a comment naming the failed item. A draft with an honest "blocked on X" comment is a good outcome.
- Do not merge. The gh pr merge* deny rule blocks you; the operator merges.
- Stay strictly within the brief's IN scope. The OUT-of-scope list is binding.
- Report back when done: PR number, draft-vs-ready, what shipped, any flags.

Begin by reading the brief below in full, then follow its "Begin by" section.

---

<FULL_BRIEF_CONTENT>
```

## After launch

The skill returns to the operator with the launch confirmation(s) (the agent id(s) and a one-line "agent dispatched for #N, will report on completion") and ends the turn. When each completion notification arrives, the skill proceeds to step 6 (report + instrument) of the workflow for that issue.

## Notes

- **This is the implementation agent.** It is dispatched by the orchestrator *after* a brief-writing agent has returned the brief and the orchestrator has gated it (see `brief-agent-launch.md` and `SKILL.md`). Do not confuse the two: the brief-agent is read-only and returns text; this agent is worktree-isolated and opens a PR.
- **One or many.** A single `/fix-issue` invocation may dispatch several implementation agents (one per issue). Launch the non-file-overlapping ones in parallel (single message, multiple Agent calls); serialize or warn on overlaps.
- **The agent inherits the auto-approve fence.** `.claude/settings.json` deny rules apply to the agent: prod DB, force-push, push-to-main, `gh pr merge`, `.env*` writes, prod registry push. For an agent-friendly (no-prod-touch) fix, none of these should fire; if the agent reaches for a denied command, it has drifted out of scope and should surface in a draft PR rather than retry.
- **Branch and PR naming** are specified in the brief's PR-shape section; the agent follows them.
