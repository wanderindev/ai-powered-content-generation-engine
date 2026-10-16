# NotebookLM and the Research Workflow

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

## NotebookLM Integration

The `nlm` CLI (`notebooklm-mcp-cli`) is available for creating and querying Google NotebookLM notebooks. Use the `/nlm-*` skills:

| Skill | Purpose |
|-------|---------|
| `/nlm-create-notebook` | Create a new notebook, optionally with initial markdown source |
| `/nlm-add-content` | Add text, URLs, or files as sources to an existing notebook |
| `/nlm-fetch-content` | Retrieve source content, notes, or AI summaries from a notebook |
| `/nlm-query` | Send a question/prompt and get AI answers grounded in notebook sources |

Key commands: `nlm login --check`, `nlm notebook list --json`, `nlm notebook create`, `nlm source add`, `nlm notebook query`. Always use `--json` for parseable output and `--wait` when adding sources.

## Research Workflow

Skills for producing article research documents via Claude's deep research. The workflow: generate a prompt from DB data → paste into Claude UI → download result → process into DB.

Since #643 the three DB-writing/reading skills (`/fact-check-prompt`,
`/fact-check-process`, `/research-process`) call the PIC MCP server (`pic` in
the checked-in `.mcp.json`, bearer from `PIC_MCP_TOKEN`, a `read,pipeline`
token kept outside the repo) instead of psql, and keep the old psql procedure
as a `## Fallback: Direct SQL` section. `/research-prompt` still reads with
psql. Client setup and behaviour differences: `docs/claude/mcp.md` ("Clients").

| Skill | Purpose |
|-------|---------|
| `/research-prompt` | Generate a research prompt for an article suggestion (saves to `ai_generated_content/research_prompts/`) |
| `/research-process` | Submit downloaded research documents through the MCP `submit_research` tool (server-side checks, PENDING row); SQL fallback section |
| `/fact-check-prompt` | Compile `NEEDS_DEEP_RESEARCH` claims (queued by the extract-only external tier, #389, and by article grounding) into a deep-research verification prompt (saves to `ai_generated_content/fact_check_prompts/`) |
| `/fact-check-process` | Submit downloaded verdict documents through the MCP `submit_fact_check_verdicts` tool; SQL fallback section |

**Directory structure:**
```
ai_generated_content/
  research_prompts/           # Generated prompts
    processed/                # Moved here after research is processed
  research_documents/         # User downloads research here from Claude UI
    processed/                # Moved here after DB insertion
  fact_check_prompts/         # Generated claim-verification prompts
    processed/                # Moved here after verdicts are processed
  fact_check_documents/       # User downloads verdict docs here from Claude UI
    processed/                # Moved here after DB write
```

**The article half of that loop is driven by cron, and its state is the file
count.** `scripts/research-cron.sh` runs every ~30 minutes and shells out to
`claude -p "/research-process"` or `claude -p "/research-prompt"` with a fixed
`--allowedTools` list (which names `mcp__pic__submit_research`; the token comes
from `~/.config/pic/mcp.env`, since cron has no login shell), choosing between them purely by counting non-`processed/`
`*.md` files in the two directories: any pending doc → process it; otherwise no
pending prompt → generate the next one; otherwise do nothing, because the human's
Claude-UI deep-research step is the missing link. Two consequences: exactly **one
prompt is in flight at a time**, so a stray or abandoned `.md` left in
`research_prompts/` stalls prompt generation indefinitely; and the script
hardcodes the operator's checkout (`/home/javier/vc/panama-in-context`, logging to
`logs/research-cron.log` under it), which is the other half of why
`ai_generated_content/` never exists in an agent worktree. No cron equivalent
exists for the fact-check pair — those two skills are hand-run.

**The fact-check pair does, however, have an in-app API surface that the
research pair does not.** `POST
/admin/dashboard/fact-check/deep-research/generate-prompt` and
`.../deep-research/verdicts` (`app/api/dashboard.py`, over
`generate_deep_research_prompt` / `process_deep_research_verdicts` in
`app/services/fact_check.py`) are the HTTP mirror of `/fact-check-prompt` and
`/fact-check-process`, added so the loop can be driven by automation (the
"pic-extension") instead of psql. No frontend caller and no `KIND_CALLS` /
`DRIVER_ALLOWED_CALLS` entry uses them (admin-gated, for an external client),
but their service functions also back the MCP fact-check tools (#641).

- **generate-prompt is stateless**: nothing is persisted, and it returns
  `batch_id` / `claim_ids` / `pending_total` / `prompt_text` for the oldest
  `DEEP_RESEARCH_BATCH_LIMIT` claims (**25**), ordered subject_type →
  subject_id → id. Because it always takes the oldest, calling again returns
  the *same* claims until this batch's verdicts are submitted — call again
  while `pending_total` exceeds `claim_count` only after submitting (or with a
  different scope); 404 when nothing is pending. Scope is
  **every** subject type with `NEEDS_DEEP_RESEARCH` claims — there is no
  subject filter unless the caller passes one. That includes `ARTICLE`: since
  #555, article grounding queues a claim its research does not cover
  (`ABSENT`) as `NEEDS_DEEP_RESEARCH` rather than `UNVERIFIABLE`, so this loop
  is how those claims resolve (the agent driver's
  `WAIT_ARTICLE_CLAIMS_RESOLVED` gate holds the article's corrections until
  it has). A scoped call accepts `SUGGESTION` / `RESEARCH` / `EDU_RESEARCH`
  / `ARTICLE` (#574 — one article's claims can be drained on their own);
  anything else is a 400. `EDU_MATERIAL` grounding still maps `ABSENT` to
  `UNVERIFIABLE`, so it is not a scope.
- **verdicts mirrors the skill's validation**, and its conservatism is what
  makes it safe to automate: rows must still be `NEEDS_DEEP_RESEARCH` (a stale
  document from a re-run is skipped, not overwritten), out-of-batch verdicts
  are skipped and unanswered batch ids reported as missing, and a document
  whose **every** verdict is an uncited `UNVERIFIABLE` is rejected with a 400
  as a failed run — so the automation's drain chain stops instead of recording
  bogus checks. It writes `checked_via="DEEP_RESEARCH"` and feeds definitive
  verdicts into the verified-claims KB.
