---
name: research-process
description: Process downloaded research documents — validate quality, insert content into the production database, and move files to processed directories.
---

# Research Document Processing

## Trigger

`/research-process`

## Procedure

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

Read the full content of the research file.

#### c. Find the Matching Prompt

Look for the corresponding prompt at `ai_generated_content/research_prompts/suggestion_<ID>_prompt.md`. Read it to extract the sub_topics for quality checking.

If the prompt file doesn't exist, warn but continue processing (skip the sub-topic coverage check).

#### d. Quality Checks

Run these checks on the research document:

1. **Word count:** Must be >= 4,000 words. Count using a simple word split.
2. **Sources section:** Must contain a references/bibliography section. Search for headings like "Sources", "References", "Bibliography", "Works Cited", "Citations" (case-insensitive).
3. **Sub-topic coverage:** At least 2 of the sub_topics from the prompt must appear in the research document (match key phrases, case-insensitive).

If **any check fails**: report which checks failed, skip this file, do NOT move it to processed. Continue to the next file.

#### e. Check for Existing Research Record

```bash
psql "$DATABASE_URL" -t -A \
  -c "SELECT id FROM research WHERE suggestion_id = <ID>;"
```

#### f. Log Summary Then Write Immediately

Log a summary showing:
- Suggestion ID
- Word count
- Quality check results (all passed)
- Whether this will be an INSERT or UPDATE
- First ~200 characters of content as preview

**IMPORTANT: Do NOT ask for confirmation. Do NOT ask "Shall I proceed?" or any similar question. Proceed directly to the database write immediately after showing the summary.** The quality checks in step (d) are the safeguard — if they passed, the write is authorized.

#### g. Insert or Update

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

#### h. Move Files to Processed

```bash
mv ai_generated_content/research_documents/suggestion_<ID>_research.md ai_generated_content/research_documents/processed/
mv ai_generated_content/research_prompts/suggestion_<ID>_prompt.md ai_generated_content/research_prompts/processed/ 2>/dev/null
```

#### i. Report Result

For each file, report:
- Suggestion ID
- Action taken (inserted / updated)
- Word count
- Quality check summary

### 3. Final Summary

After all files are processed, report:
- Total files processed successfully
- Any files skipped (and why)
- Suggest running `/research-prompt` for the next unresearched suggestion

### Verification Query

After processing, verify the insertion:

```bash
psql "$DATABASE_URL" \
  -c "SELECT suggestion_id, LENGTH(content) as content_length, status, updated_at FROM research WHERE suggestion_id = <ID>;"
```