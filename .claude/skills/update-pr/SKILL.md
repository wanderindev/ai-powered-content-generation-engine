---
name: update-pr
description: Update an open PR's branch with the latest main and resolve any merge conflicts, then push so CI re-runs — invoked as "/update-pr [<number>]". With a number, targets that PR. With no number, targets the lowest-numbered open PR that is behind main (or has conflicts). All work happens in a throwaway git worktree so the operator's main checkout (e.g. an in-progress pair-mode issue) is never touched. Merges main into the PR branch (never rebases — force-push is denied), unions the project's append-only docs automatically, and STOPS-and-surfaces real source-code conflicts rather than guessing. Use when the operator says any of "update PR N", "update the next PR", "merge main into PR N", "resolve conflicts on N", "bring N up to date with main", "update branch on N", or is churning through a backlog of agent PRs that have fallen behind main. Does NOT merge the PR itself (the operator merges) and does NOT wait for CI.
---

# update-pr — bring a PR branch up to date with main

## Problem this solves

Multiple autonomous agents open PRs off `main` while the operator works a pair-mode issue in the main checkout. As PRs merge, the still-open ones fall behind `main` and accumulate conflicts. Updating each branch + resolving conflicts by hand is slow, and every push triggers a ~2-minute CI run the operator otherwise babysits. This skill does the update-and-resolve in an **isolated worktree** (so the operator's working tree is untouched), pushes once, and hands back — CI runs on its own.

## Invocation

`/update-pr [<number>]`

- **With a number** → update that PR.
- **No number** → update the **lowest-numbered open PR that needs it** (behind `main` or conflicting). "Needs it" matters: skip PRs already current — updating them is a no-op that still burns a CI run.

## Hard constraints (from `.claude/settings.json` deny rules)

- **Merge, never rebase.** Force-push is denied (`--force`, `-f`, `--force-with-lease`). The update is `git merge origin/main` into the PR branch — a normal merge commit, pushed with a plain `git push`. Never `git rebase` the PR branch.
- **Never push to `main`/`master`** (denied) and **never `gh pr merge`** (denied) — this skill updates the PR branch only; the operator merges the PR.
- **Worktree isolation is mandatory.** The operator is actively working in the main checkout. Do ALL git work in a dedicated worktree under `.claude/worktrees/`. Do not `git checkout` a different branch in the main checkout, do not touch its index.
- **Clean up with `git worktree remove`** (`rm -rf` is denied). Use `--force` only after `git merge --abort` if the worktree is mid-merge.

## The core principle for conflicts

**A clean abort is a good outcome.** Never push a conflict resolution you are not confident is correct. For these agent PRs, conflicts fall into two buckets:

1. **Append-only project docs → auto-resolve by union.** These are the common case and are safe:
   - `docs/agent-fixes/agent-friendly-outcomes.md` (markdown table; agents append one row per issue)
   - `docs/agent-fixes/prompts/INDEX.md` (append-only list)
   - Any similar append-only log/register under `docs/`.

   Resolution = **keep every row/line from both sides** (main's rows + the PR's added row(s)), preserving order, no duplicates. Each PR adds its own issue-keyed row, so there is nothing to reconcile — just union.

2. **Anything else → resolve only if unambiguous; otherwise STOP-and-surface.** Real source code, lockfiles (`package-lock.json`), Alembic migration version chains, schema — if the correct merge isn't obvious and safe, run `git merge --abort`, remove the worktree, leave the PR exactly as it was, and report to the operator that PR #N has a real conflict needing manual/pair resolution (name the files). This mirrors the `fix-issue` draft-PR escape hatch: an honest "this one needs you" beats a guessed merge that breaks the branch.

## Workflow

### Step 1 — Select the target PR
- **Number given:** `gh pr view <N> --json number,title,state,isDraft,headRefName,baseRefName,mergeStateStatus,mergeable`. Confirm `state == OPEN`. If closed/merged, report and stop.
- **No number:** `gh pr list --state open --base main --json number,title,isDraft,headRefName,mergeStateStatus --limit 100`. Sort ascending by `number`. Pick the lowest whose `mergeStateStatus` is `BEHIND` or `DIRTY` (needs update). `mergeStateStatus` is computed async by GitHub and may be `UNKNOWN`; if every candidate is `UNKNOWN`, fall back to the lowest open PR and let the merge attempt in Step 3 be the source of truth. If all are `CLEAN`, report "all open PRs are current with main — nothing to update" and stop.

Capture the head branch name (`headRefName`) — call it `$BR`.

### Step 2 — Fetch and create the isolated worktree
```bash
git fetch origin --prune
WT=".claude/worktrees/update-pr-<N>"
git worktree add --detach "$WT" "origin/$BR"   # detached at the FRESH remote tip — no stale local branch
```
Checking out `origin/$BR` **detached** guarantees you start from the exact remote head with no local-branch staleness, and sidesteps the `git reset --hard origin/*` deny rule. If `$WT` already exists from a prior run, remove it first (`git worktree remove --force "$WT"`) then re-add. The push in Step 6 sends the detached HEAD back to the branch by explicit refspec — no local tracking branch is needed.

### Step 3 — Merge main into the PR branch (inside the worktree)
```bash
git -C "$WT" merge origin/main --no-edit
```
- **"Already up to date."** → the PR was not actually behind. Report it, remove the worktree, stop (don't push — nothing changed).
- **Merge succeeds with no conflicts** → go to Step 5.
- **Conflicts reported** → Step 4.

### Step 4 — Resolve conflicts
List them: `git -C "$WT" diff --name-only --diff-filter=U`.

For each conflicted file:
- **Append-only doc (bucket 1 above):** open it, take the union of both sides' rows/lines, remove the conflict markers, write the merged result, `git -C "$WT" add <file>`.
- **Anything else (bucket 2):** if and only if the resolution is unambiguous and you can state why it's correct, resolve and `add` it. Otherwise **abort**: `git -C "$WT" merge --abort`, `git worktree remove --force "$WT"`, and report to the operator (Step 7, abort path). Do not continue.

After resolving all conflicts: `git -C "$WT" commit --no-edit` to complete the merge.

### Step 5 — Verify before pushing (non-negotiable gate)
- No leftover conflict markers anywhere in the worktree:
  `git -C "$WT" diff --check` **and** `grep -rn '^\(<<<<<<<\|=======\|>>>>>>>\)' "$WT" --include='*' || true` — any hit means abort the push and surface.
- `git -C "$WT" status` shows a clean tree (the merge is committed, nothing unstaged).
- Sanity-glance the merge diff for the conflicted files (`git -C "$WT" show --stat HEAD`): the change set should be the union you intended, nothing deleted unexpectedly.

### Step 6 — Push
```bash
git -C "$WT" push origin HEAD:"$BR"
```
Plain push (the merge commit is a fast-forward of the PR branch — no force needed). This triggers CI automatically.

### Step 7 — Clean up and report
- `git worktree remove "$WT"` (use `--force` if it complains about the merge state, after the push has succeeded).
- **Success report:** PR number + title, "merged `main` into `$BR` and pushed", whether there were conflicts and how each was resolved (name files + bucket), and "CI is now running — I did not wait for it." Link the PR.
- **Abort report:** PR number + title, the conflicted file(s), why they couldn't be safely auto-resolved, and "left the PR untouched — this one needs manual or pair-mode resolution." Suggest the operator handle it in their pair-mode session or with a fresh agent.

## Notes

- **Does not wait for CI.** The whole point is to not babysit the pipeline. Report and end. If the operator wants the next one, they re-invoke (or wrap this in `/loop` to churn the backlog — e.g. `/loop /update-pr` to walk the open PRs lowest-first, one CI run at a time).
- **Does not merge the PR.** Even if CI would pass, merging is the operator's call and is deny-blocked.
- **One PR per invocation.** No-number mode picks exactly one (the lowest that needs it), not a batch — each push is its own CI run, and the operator stays in control of the cadence.
- **Always read PR state FRESH (cost/correctness guardrail, 2026-05-30).** PRs merge while you run. At the START of every invocation, `git fetch origin --prune` and re-run the `gh pr list`/`gh pr view` query — never act on a PR-list read from earlier in the conversation. If a `gh pr view <N>` errors (e.g. the PR was merged/closed), the target is gone: re-list and pick again; do not let one failed lookup cascade or cancel a batch. If a tool result comes back empty or a command is cancelled, re-run that ONE command once — do NOT fire a storm of `echo`/`cat`/probe commands; that bloats context and is what corrupts long-session state. When in doubt about your own git state, run `git status` / `git worktree list` once and proceed from the truth, not from memory.
- **Outcomes log.** If this skill itself had to resolve a conflict in `agent-friendly-outcomes.md`, that's expected churn — no separate logging needed; the PR's own history records it.
