---
name: fact-check-process
description: Process downloaded deep-research verdict documents — parse the verdict lines, write them back to the production fact_check_claims table, and move files to processed directories.
---

# Fact-Check Verdict Processing

Hybrid tier 2 of the fact-checking pipeline (epic #373, issue #377). Consumes
the verdict documents the operator downloads from Claude's deep-research UI
(created against prompts from `/fact-check-prompt`) and writes each verdict
back to the `fact_check_claims` row it belongs to.

Like `/research-process`, this is a **non-interactive** flow: the validation
steps are the safeguard, and the DB writes happen without asking for
confirmation.

**Default path: the PIC MCP server** (`pic` in the repo's `.mcp.json`, tool
`mcp__pic__submit_fact_check_verdicts`, scope `pipeline`; see
`docs/claude/mcp.md`). The validation the SQL path does by hand — vocabulary,
mandatory citations, still-pending rows, out-of-batch ids, the failed-run
rejection, the verified-claims KB upsert, the claim re-index — runs
server-side in `fact_check.submit_deep_research_verdicts`, the same function
the admin API's verdict upload calls, and the call is audited in
`mcp_tool_calls`. **Fallback: direct SQL** (the section at the end) — only when
the MCP server is unreachable or `PIC_MCP_TOKEN` is unset, or for the
operator-confirmed failed-run override, which MCP deliberately cannot do. Say
in the report which path was used.

## Trigger

`/fact-check-process`

## Procedure (MCP — default)

### 1. Scan for Verdict Documents

Look for `.md` files in `ai_generated_content/fact_check_documents/`
(exclude the `processed/` subdirectory).

```bash
ls ai_generated_content/fact_check_documents/*.md 2>/dev/null
```

If no files are found, report "No verdict documents to process" and suggest
running `/fact-check-prompt` if there are claims pending deep research.

### 2. Process Each File

For each `.md` file found:

#### a. Extract the Batch ID

The filename must match `fact_check_<BATCH_ID>_verdicts.md`. If it doesn't
match, skip the file with a warning.

#### b. Collect the Batch's Claim IDs

Read the matching prompt at
`ai_generated_content/fact_check_prompts/fact_check_<BATCH_ID>_prompt.md`
and extract the ids from its `CLAIM <id> (...)` lines, in order. These are the
`claim_ids` the tool needs: they are what lets the server skip a verdict the
prompt did not ask for and report the unanswered ones as missing.

If the prompt file doesn't exist, warn, and use the ids of the document's own
`VERDICT: <id> | ...` lines instead (the server still refuses any claim that is
not `NEEDS_DEEP_RESEARCH`, but the out-of-batch check is then void — say so in
the summary). More than 25 ids is a malformed prompt: skip the file with a
warning.

#### c. Submit the Document

Call `mcp__pic__submit_fact_check_verdicts` with:

- `claim_ids` — from step (b);
- `document` — the **full file content, verbatim** (do not extract the verdict
  lines yourself; the server parses them and ignores everything else);
- `batch_id` — the `<BATCH_ID>` from the filename, only when it is exactly
  `YYYYMMDD_HHMMSS` (a `_b2`-style id from the SQL fallback is omitted).

**IMPORTANT: Do NOT ask for confirmation.** The server-side validation is the
safeguard; if a line passes it, the write is authorized.

Outcomes:

- **Result** `{"batch_id", "written", "skipped": [{"claim_id", "reason"}],
  "missing_claim_ids", "kb_upserts", "replayed", "truncated"}` — log the file
  name, `written`, `kb_upserts`, every skipped claim with its reason, and the
  missing ids (they stay `NEEDS_DEEP_RESEARCH` and ride along in the next
  `/fact-check-prompt` batch).
- **`replayed: true`** — this document was already submitted (every claim is
  "no longer pending"). Nothing was written; that is success, not an error.
- **`rejected: ...` error** — either every verdict is an uncited
  `UNVERIFIABLE` (the signature of a deep-research run whose web tools never
  came up) or there is no `VERDICT:` line at all. Nothing was written. Leave
  the file in place and tell the operator to re-run the research. If the
  operator has read the document and confirms the claims were genuinely
  researched, the only way to record it anyway is the SQL fallback's override.
- **`insufficient_scope:` / 401 / connection error** — no usable `pipeline`
  token or server. Leave the file in place and report; use the SQL fallback
  only if the operator asked for it.

#### d. Move Files to Processed

Move the pair when the call succeeded (including `replayed: true`) **and**
`missing_claim_ids` is empty. If claims are missing, leave both files in place
(the operator may re-run the deep research) and say so — re-submitting the
same document later is harmless, because it converges on replay.

```bash
mv ai_generated_content/fact_check_documents/fact_check_<BATCH_ID>_verdicts.md \
   ai_generated_content/fact_check_documents/processed/
mv ai_generated_content/fact_check_prompts/fact_check_<BATCH_ID>_prompt.md \
   ai_generated_content/fact_check_prompts/processed/ 2>/dev/null
```

### 3. Final Summary

After all files are processed, report:

- Per file: `written`, `kb_upserts`, skipped (with reasons), missing, and
  whether it was a replay.
- **Every INACCURATE and DISPUTED claim that was written**, with its
  correction / competing positions — read them from the document's own
  `VERDICT:` lines for the ids that were neither skipped nor missing. These are
  the findings that need editorial action. (The tool result does not name the
  claims' subjects; to group them by subject, look the claims up in the admin
  UI or run the fallback's read-only verification query.)
- If any claims remain pending, suggest `/fact-check-prompt` to batch them.

---

## Fallback: Direct SQL (pre-MCP path)

Use only when the MCP path is unavailable, or for the operator-confirmed
failed-run override. It needs a production `DATABASE_URL` in the shell and
bypasses the MCP audit log. Steps 1, 2a and 2b (the prompt's claim ids) are the
same as above; then:

#### SQL c. Parse the Verdict Lines

Read the file and extract every line matching (anywhere in the file, though
they normally sit under a `## Verdicts` heading):

```
VERDICT: <claim id> | <VERIFIED|INACCURATE|DISPUTED|UNVERIFIABLE> | <note> | <citations>
```

The claim id is the `fact_check_claims.id` primary key echoed from the
prompt. Ignore any other lines. If a claim id appears twice, keep the last
occurrence and warn. Report **missing verdicts** (prompt ids with no verdict
line — they stay `NEEDS_DEEP_RESEARCH`) and skip **unknown verdicts** (ids not
in the prompt) with a warning; never write a verdict the prompt didn't ask
for. If the prompt file doesn't exist, warn and validate each id against the
database in the next step.

#### SQL d. Validate Each Verdict

**Failed-run check first:** if EVERY verdict line in the document is
UNVERIFIABLE with a dash citation, reject the whole file — that is the
signature of a deep-research run whose web tools never came up (it should
have omitted the lines, but broken runs have emitted them anyway; observed
2026-07-13). Write nothing, leave the file in place, and tell the operator
to re-run the research. Only if the operator has read the document and
confirms the claims were genuinely researched may the verdicts be written
as a deliberate override.

For every remaining verdict line, apply the same conservatism as
`process_deep_research_verdicts`:

1. **Vocabulary** — the verdict must be one of VERIFIED / INACCURATE /
   DISPUTED / UNVERIFIABLE. Anything else: skip the line with a warning.
2. **Citations** — VERIFIED, INACCURATE, and DISPUTED require a non-dash
   citation. An uncited definitive verdict is skipped (row stays
   `NEEDS_DEEP_RESEARCH`) with a warning — never written.
3. **Row state** — confirm the ids exist and are still pending:

```bash
psql "$DATABASE_URL" -t -A -F '|' \
  -c "SELECT id, verdict FROM fact_check_claims WHERE id IN (<ids>);"
```

   Ids not in the table are skipped with a warning. Ids whose verdict is no
   longer `NEEDS_DEEP_RESEARCH` are skipped with a warning (a re-run of the
   fact-check replaced the rows after the prompt was generated — the
   verdict doc is stale for those claims).

#### SQL e. Log Summary Then Write Immediately

Log a summary: file name, verdict counts by type, and every skipped line
with its reason.

**IMPORTANT: Do NOT ask for confirmation. Do NOT ask "Shall I proceed?" or
any similar question. Proceed directly to the database write immediately
after showing the summary.** The validations in steps (SQL c)/(SQL d) are the
safeguard — if a line passed them, the write is authorized.

#### SQL f. Write the Verdicts

One UPDATE per claim, piped to psql in a single heredoc per file. Use `$$`
dollar-quoting for the free-text fields; if a note or citation itself
contains `$$`, use a tagged quote like `$fc$...$fc$`. Write NULL (not an
empty string or a dash) when the note or citation is a dash.

```bash
cat <<'SQLEOF' | psql "$DATABASE_URL"
UPDATE fact_check_claims
SET verdict = '<VERDICT>',
    correction = $$<NOTE>$$,        -- NULL when the note is a dash
    citation = $$<CITATIONS>$$,     -- NULL when the citations are a dash
    checked_via = 'DEEP_RESEARCH',
    checked_at = NOW(),
    updated_at = NOW()
WHERE id = <ID> AND verdict = 'NEEDS_DEEP_RESEARCH';
SQLEOF
```

The `AND verdict = 'NEEDS_DEEP_RESEARCH'` guard makes the write idempotent
and race-safe against a concurrent re-check. Check psql's `UPDATE n` tallies
against the expected count and report any zero-row updates.

#### SQL g. Feed the Verified-Claims Knowledge Base

Every VERIFIED / INACCURATE / DISPUTED verdict just written is reusable
knowledge (#378): later fact-check runs inherit it instead of re-verifying.
Upsert them into `verified_claims`, keyed by the same normalization the
backend uses (lowercase, alphanumerics + spaces, collapsed whitespace).
Skip UNVERIFIABLE — it carries nothing reusable. One statement per claim,
same heredoc/dollar-quoting rules as step (f):

```bash
cat <<'SQLEOF' | psql "$DATABASE_URL"
INSERT INTO verified_claims
  (claim_key, claim_text, claim_type, verdict, correction, citation, source_method)
SELECT
  btrim(regexp_replace(regexp_replace(lower(c.claim_text), '[^a-z0-9\s]', '', 'g'), '\s+', ' ', 'g')),
  c.claim_text, c.claim_type, c.verdict, c.correction, c.citation, 'DEEP_RESEARCH'
FROM fact_check_claims c
WHERE c.id = <ID> AND c.verdict IN ('VERIFIED', 'INACCURATE', 'DISPUTED')
ON CONFLICT (claim_key) DO UPDATE
SET verdict = EXCLUDED.verdict,
    correction = EXCLUDED.correction,
    citation = EXCLUDED.citation,
    source_method = EXCLUDED.source_method,
    updated_at = NOW();
SQLEOF
```

(Selecting from `fact_check_claims` by id reuses the values written in step
(SQL f) verbatim — no re-quoting of free text needed.)

#### SQL h. Move Files to Processed

Same rule and commands as step (d) of the MCP procedure: move the pair only
when every verdict line was either written or deliberately skipped AND there
are no missing verdicts.

The SQL path does **not** re-index the resolved article claims in the
knowledge layer (the MCP tool and the admin upload do, through
`reindex_claims`). After a SQL-path write, have the operator run
`python -m scripts.reindex_knowledge --all` in the backend container: step
(SQL g) upserts the verified-claims KB, and only `--all` refreshes the KB
chunks (`--article <id>` re-indexes one article's claims only).

#### SQL Final Summary

Report as in step 3 above, grouped by subject (`SUGGESTION <id>` /
`RESEARCH <id>` / `ARTICLE <id>` …) with counts written by verdict, and say the
SQL fallback was used.

### Verification Query (read-only; works after either path)

```bash
psql "$DATABASE_URL" \
  -c "SELECT subject_type, subject_id, verdict, COUNT(*)
      FROM fact_check_claims
      GROUP BY subject_type, subject_id, verdict
      ORDER BY subject_type, subject_id, verdict;"
```
