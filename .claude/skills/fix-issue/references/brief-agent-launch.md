# Brief-writing agent — launch preamble + return contract

The orchestrator spawns **one brief-writing agent per issue** (in parallel for a batch). The brief-agent is **read-only**: it reads the issue and the codebase, performs the codebase-fact verification, assembles the brief from the template, and **returns it** to the orchestrator. It does NOT write files, edit code, create branches, or dispatch the implementation agent — the orchestrator does all of that after collecting and gating the returned brief.

This is the division that lets `/fix-issue` scale to a batch: the load-bearing verification work is parallelized across read-only agents instead of being done serially in the orchestrator's own context. The methodology's discipline is preserved by (a) this prompt mandating the verification checklist, and (b) the orchestrator's non-skippable well-formedness + production-touch gate on every returned brief.

## The Agent tool invocation

```
Agent({
  description: "brief #<N> — <short>",
  subagent_type: "general-purpose",
  // NO isolation / NO worktree — read-only against the main checkout.
  run_in_background: true,   // parallel for a batch; foreground is fine for a lone id
  prompt: <PREAMBLE with <N> substituted>
})
```

For a batch, launch all brief-agents **in a single message** (multiple Agent calls) so they run concurrently. They read the same main checkout in parallel — safe, because they only read.

## The preamble (substitute `<N>`)

```
You are a brief-writing agent for the fix-issue skill in the Panama In Context repo. You are READ-ONLY: read the issue and the source, verify, and RETURN a brief. Do NOT edit code, write files, create branches, run lint/build, or launch other agents — the orchestrator does all of that after you return.

Your job is the load-bearing step of the audit-to-autonomy methodology: produce a brief so tight that an implementation agent can resolve issue #<N> unsupervised without hitting an ambiguity it has to resolve on its own. A loose brief is the documented cause of autonomous failures — verification against source is the gate.

Do this:
1. `gh issue view <N> --json number,title,body,labels` — read the full body. Note whether the `agent-friendly` label is present.
2. Read `.claude/skills/fix-issue/references/verification-checklist.md` and walk it IN FULL against the CURRENT source. Open every file the issue references; confirm paths and line numbers are current; count the real sites yourself if it is a sweep; identify and READ the canonical pattern to mirror (cite its file:line); record any issue-body-vs-source drift (when they disagree, the source wins and the brief says so).
3. Read `.claude/skills/fix-issue/references/brief-template.md` and fill EVERY {{placeholder}} with a fact you verified this run. Strip all placeholders — a brief shipped with a {{...}} still in it is broken. Match the brief's length to the issue's real complexity.
4. Determine IN/OUT scope, the production-touch verdict, and pre-resolve every ambiguity you can anticipate.

Return EXACTLY two sections and nothing that needs a follow-up turn:

## VERIFICATION SUMMARY
- agent-friendly label present: yes/no
- issue-body-vs-source drift: each claim that differed, with the correction (or "none")
- canonical pattern: file:line to mirror (or "n/a")
- site count: the real count if a sweep (or "n/a")
- IN-scope files: explicit list of file paths the fix will touch — the orchestrator uses this for a cross-issue overlap check, so be precise (for a broad sweep, say so and list the directories)
- production-touch: none / YES (if YES, say so loudly — prod DB, .env, deploy, or auth/payment/PII code — the agent-friendly label is probably wrong)
- tractable for autonomy: yes / no + one line why

## BRIEF
A single fenced ```markdown block containing the COMPLETE assembled brief, ready to inline verbatim into the implementation agent's prompt.

If verification shows the issue is NOT tractable for autonomous execution (production touch, a required endpoint/method/field doesn't exist, structural impossibility, or the agent-friendly label looks wrong), set "tractable for autonomy: no", explain in the summary, and still return your best brief with the blocker noted at its top. The orchestrator decides whether to dispatch, hold, or surface.
```

## What the orchestrator does with the return

1. Save the BRIEF to `docs/agent-fixes/prompts/fix-issue-<N>.md` and add an INDEX line (prompt-preservation).
2. Run the **well-formedness gate**: no unfilled `{{placeholders}}`; IN/OUT scope present; production-touch line present; failure-mode + self-review sections present; agent-friendly confirmed. A brief that fails is HELD and surfaced for that one issue.
3. Run the **production-touch gate**: if `production-touch: YES` or `tractable: no`, hold that issue and surface — do not auto-dispatch.
4. Run the **cross-issue file-overlap check** using the IN-scope file lists.
5. Review gate (if on for this invocation), then dispatch the implementation agent per `agent-launch.md`.
