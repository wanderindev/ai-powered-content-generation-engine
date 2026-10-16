---
name: research-process
description: Process downloaded research documents — validate quality, insert content into the production database, and move files to processed directories.
---

# Research Document Processing

**Default path: the PIC MCP server** (`pic` in the repo's `.mcp.json`, tool
`mcp__pic__submit_research`, scope `pipeline`; see `docs/claude/mcp.md`). The
tool runs the same service as the admin research upload
(`research_intake.submit_research`): the quality checks, the insert, and the
replay / conflict rules all live server-side, and the call is audited in
`mcp_tool_calls`. **Fallback: direct SQL** (the section at the end) — only
when the MCP server is unreachable or `PIC_MCP_TOKEN` is unset. Say in the
report which path was used.

This skill also runs unattended from `scripts/research-cron.sh` (`claude -p
"/research-process"`), whose `--allowedTools` list names the MCP tool.

## Trigger

`/research-process`

## Procedure (MCP — default)

### 1. Scan for Research Documents

Look for `.md` files in `ai_generated_content/research_documents/` (exclude the `processed/` subdirectory).

```bash
ls ai_generated_content/research_documents/*.md 2>/dev/null
```

If no files found, report "No research documents to process" and suggest running `/research-prompt` for the next suggestion.

### 2. Process Each File

For each `.md` file found:

#### a. Extract Suggestion ID

The filename must match the pattern `suggestion_<ID>_research.md`. If it doesn't match, skip the file with a warning.

#### b. Read the Research Document

Read the full content of the research file. (The matching prompt in
`ai_generated_content/research_prompts/` is not needed on this path: the
server judges sub-topic coverage against the suggestion's stored
`sub_topics`.)

#### c. Submit It

Call `mcp__pic__submit_research` with `suggestion_id=<ID>` and `markdown` =
the **full file content, verbatim**. Do not pass `replace` unless the operator
explicitly asked to replace existing research for that suggestion.

**IMPORTANT: Do NOT ask for confirmation.** The server's quality checks are
the safeguard: if they pass, the write is authorized.

The server checks (the same rules this skill used to run by hand, now in
`research_common.validate_research_document`):

1. **Word count** ≥ 4,000.
2. **Sources section** — a Sources / References / Bibliography / Works Cited /
   Citations heading. When missing, the server compiles one from the inline
   citations and appends it (`references_generated: true`) instead of failing.
3. **Sub-topic coverage** — at least 2 of the suggestion's stored sub-topics,
   judged by a model.

Outcomes (`{"accepted", "action", "replayed", "suggestion_id",
"suggestion_title", "research_id", "status", "word_count",
"references_generated", "checks", "truncated"}`):

- **`action: "inserted"` / `"updated"`** — stored as `PENDING` for the
  operator's review in the admin UI. Move the files (step d).
- **`action: "replayed"`** — this exact body is already stored (a re-run after
  a crash). Nothing changed; move the files (step d).
- **`accepted: false`, `action: "rejected"`** — report which `checks` failed;
  nothing was stored. Do NOT move the file. Continue to the next file.
- **`conflict: ...` error** — the suggestion already has research with a
  different body (or APPROVED research, which only the operator can replace in
  the admin UI). Do NOT move the file; report it so the operator can decide.
  The old SQL path silently overwrote existing research here — this is a
  deliberate behaviour change.
- **`not_found: ...` error** — no such suggestion; skip with a warning.
- **`insufficient_scope:` / 401 / connection error** — no usable `pipeline`
  token or server. Leave the file in place and report; under cron the next run
  tries again.

#### d. Move Files to Processed

```bash
mv ai_generated_content/research_documents/suggestion_<ID>_research.md ai_generated_content/research_documents/processed/
mv ai_generated_content/research_prompts/suggestion_<ID>_prompt.md ai_generated_content/research_prompts/processed/ 2>/dev/null
```

#### e. Report Result

For each file, report:
- Suggestion ID (and `suggestion_title`), `research_id`
- Action taken (inserted / updated / replayed / rejected / conflict)
- `word_count`, `references_generated`
- Quality check summary (`checks`)

### 3. Final Summary

After all files are processed, report:
- Total files processed successfully
- Any files skipped (and why)
- The path used (MCP or SQL fallback)
- Suggest running `/research-prompt` for the next unresearched suggestion

---

## Fallback: Direct SQL (pre-MCP path)

Use only when the MCP path is unavailable. It needs a production
`DATABASE_URL` in the shell, bypasses the MCP audit log, runs the quality checks
by hand (a cruder sub-topic match against the prompt file, and no generated
references), and **overwrites existing research without a conflict check —
including APPROVED research**, so check the step (SQL e) result before an
UPDATE and stop if that row is APPROVED. Steps 1, 2a and 2b are the same as
above; then:

#### SQL c. Find the Matching Prompt

Look for the corresponding prompt at `ai_generated_content/research_prompts/suggestion_<ID>_prompt.md`. Read it to extract the sub_topics for quality checking.

If the prompt file doesn't exist, warn but continue processing (skip the sub-topic coverage check).

#### SQL d. Quality Checks

Run these checks on the research document:

1. **Word count:** Must be >= 4,000 words. Count using a simple word split.
2. **Sources section:** Must contain a references/bibliography section. Search for headings like "Sources", "References", "Bibliography", "Works Cited", "Citations" (case-insensitive).
3. **Sub-topic coverage:** At least 2 of the sub_topics from the prompt must appear in the research document (match key phrases, case-insensitive).

If **any check fails**: report which checks failed, skip this file, do NOT move it to processed. Continue to the next file.

#### SQL e. Check for Existing Research Record

```bash
psql "$DATABASE_URL" -t -A \
  -c "SELECT id, status FROM research WHERE suggestion_id = <ID>;"
```

#### SQL f. Log Summary Then Write Immediately

Log a summary showing:
- Suggestion ID
- Word count
- Quality check results (all passed)
- Whether this will be an INSERT or UPDATE
- First ~200 characters of content as preview

**IMPORTANT: Do NOT ask for confirmation. Do NOT ask "Shall I proceed?" or any similar question. Proceed directly to the database write immediately after showing the summary.** The quality checks in step (SQL d) are the safeguard — if they passed, the write is authorized.

#### SQL g. Insert or Update

Use `$$` dollar-quoting for SQL safety with markdown content. Pipe the SQL via stdin to psql:

**If record exists (UPDATE):**
```bash
cat <<'SQLEOF' | psql "$DATABASE_URL"
UPDATE research
SET content = $$<MARKDOWN_CONTENT>$$,
    updated_at = NOW()
WHERE suggestion_id = <ID>;
SQLEOF
```

**If no record exists (INSERT):**
```bash
cat <<'SQLEOF' | psql "$DATABASE_URL"
INSERT INTO research (suggestion_id, content, status, created_at, updated_at)
VALUES (<ID>, $$<MARKDOWN_CONTENT>$$, 'PENDING', NOW(), NOW());
SQLEOF
```

**Important:** If the markdown content itself contains `$$`, use a tagged dollar-quote like `$research$...$research$` instead.

#### SQL h. Move Files and Report

Same commands as step (d) of the MCP procedure, then report as in step (e),
saying the SQL fallback was used.

### Verification Query (read-only; works after either path)

After processing, verify the insertion:

```bash
psql "$DATABASE_URL" \
  -c "SELECT suggestion_id, LENGTH(content) as content_length, status, updated_at FROM research WHERE suggestion_id = <ID>;"
```