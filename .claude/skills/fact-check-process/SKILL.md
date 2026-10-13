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

## Trigger

`/fact-check-process`

## Procedure

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

#### b. Parse the Verdict Lines

Read the file and extract every line matching (anywhere in the file, though
they normally sit under a `## Verdicts` heading):

```
VERDICT: <claim id> | <VERIFIED|INACCURATE|DISPUTED|UNVERIFIABLE> | <note> | <citations>
```

The claim id is the `fact_check_claims.id` primary key echoed from the
prompt. Ignore any other lines. If a claim id appears twice, keep the last
occurrence and warn.

#### c. Cross-Check Against the Prompt

Read the matching prompt at
`ai_generated_content/fact_check_prompts/fact_check_<BATCH_ID>_prompt.md`
and extract the claim ids it listed (`CLAIM <id> (...)` lines). Report:

- **Missing verdicts** — prompt ids with no verdict line. Their rows stay
  `NEEDS_DEEP_RESEARCH`; they will ride along in the next
  `/fact-check-prompt` batch.
- **Unknown verdicts** — verdict ids that were not in the prompt. Skip them
  with a warning; never write a verdict the prompt didn't ask for.

If the prompt file doesn't exist, warn and fall back to validating each id
against the database in step (d).

#### d. Validate Each Verdict

**Failed-run check first:** if EVERY verdict line in the document is
UNVERIFIABLE with a dash citation, reject the whole file — that is the
signature of a deep-research run whose web tools never came up (it should
have omitted the lines, but broken runs have emitted them anyway; observed
2026-07-13). Write nothing, leave the file in place, and tell the operator
to re-run the research. Only if the operator has read the document and
confirms the claims were genuinely researched may the verdicts be written
as a deliberate override.

For every remaining verdict line, apply the same conservatism as the
web-search tier (#376):

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
   web-search tier replaced the rows after the prompt was generated — the
   verdict doc is stale for those claims).

#### e. Log Summary Then Write Immediately

Log a summary: file name, verdict counts by type, and every skipped line
with its reason.

**IMPORTANT: Do NOT ask for confirmation. Do NOT ask "Shall I proceed?" or
any similar question. Proceed directly to the database write immediately
after showing the summary.** The validations in steps (c)/(d) are the
safeguard — if a line passed them, the write is authorized.

#### f. Write the Verdicts

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

#### g. Feed the Verified-Claims Knowledge Base

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
(f) verbatim — no re-quoting of free text needed.)

#### h. Move Files to Processed

Only move the pair when **every** verdict line was either written or
deliberately skipped AND there are no missing verdicts. If verdicts are
missing, leave both files in place (the operator may re-run the deep
research) and say so.

```bash
mv ai_generated_content/fact_check_documents/fact_check_<BATCH_ID>_verdicts.md \
   ai_generated_content/fact_check_documents/processed/
mv ai_generated_content/fact_check_prompts/fact_check_<BATCH_ID>_prompt.md \
   ai_generated_content/fact_check_prompts/processed/ 2>/dev/null
```

### 3. Final Summary

After all files are processed, report — grouped by subject so the operator
knows which suggestion/research to act on:

- Per subject (`SUGGESTION <id>` / `RESEARCH <id>`): counts written by
  verdict, and **list every INACCURATE and DISPUTED claim** with its
  correction/positions — these are the findings that need editorial action.
- Total lines written, skipped (with reasons), and still pending.
- If any claims remain `NEEDS_DEEP_RESEARCH`, suggest `/fact-check-prompt`
  to re-batch them.

### Verification Query

```bash
psql "$DATABASE_URL" \
  -c "SELECT subject_type, subject_id, verdict, COUNT(*)
      FROM fact_check_claims
      GROUP BY subject_type, subject_id, verdict
      ORDER BY subject_type, subject_id, verdict;"
```
