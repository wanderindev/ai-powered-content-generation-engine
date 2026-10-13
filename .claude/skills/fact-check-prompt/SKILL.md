---
name: fact-check-prompt
description: Generate a deep-research verification prompt for fact-check claims marked NEEDS_DEEP_RESEARCH (optionally scoped to one suggestion or research document). The prompt is saved to ai_generated_content/fact_check_prompts/ for the user to paste into Claude's deep research UI.
---

# Fact-Check Verification Prompt Generation

Hybrid tier 2 of the fact-checking pipeline (epic #373, issue #377). The
extract-only default mode (#389) queues every claim the verified-claims KB
cannot resolve as `NEEDS_DEEP_RESEARCH` — as does the web-search tier (#376,
now an API-only escape hatch) for claims it cannot confidently resolve; this
skill compiles those claims into a verification prompt for Claude's
deep-research UI. One template serves every subject level (suggestions,
research documents) because the input is just a claims list.

All file formats in this workflow are strict and non-interactive so an
automation (e.g. the pic-extension research driver) can run the loop without
a human in the middle.

## Trigger

`/fact-check-prompt` or `/fact-check-prompt <subject_type> <subject_id>`

## Arguments

- **subject_type** (optional): `SUGGESTION` or `RESEARCH`. With **subject_id**,
  scopes the prompt to that one subject.
- If omitted, collect ALL pending `NEEDS_DEEP_RESEARCH` claims across every
  subject — suggestions yield few claims each, so batching them into one
  prompt is the normal mode.

## Procedure

### 1. Collect Pending Claims

```bash
psql "$DATABASE_URL" -t -A -F '|' \
  -c "SELECT id, subject_type, subject_id, claim_type, claim_text
      FROM fact_check_claims
      WHERE verdict = 'NEEDS_DEEP_RESEARCH'
      ORDER BY subject_type, subject_id, id;"
```

With arguments, add `AND subject_type = '<TYPE>' AND subject_id = <ID>`.

If no rows are returned, report "No claims pending deep research" and stop.
Suggest running a fact-check first (`POST
/api/v1/admin/dashboard/suggestions/{id}/fact-check` or
`.../research/{id}/fact-check` — extract-only `mode=deep_research` by
default) if the operator expected claims here.

### 2. Batch the Claims

Split the rows into batches of **at most 25 claims** per prompt file (a deep
research run's internal search budget exhausts before ~30 claims). Each batch gets an id of the form
`<YYYYMMDD_HHMMSS>` (append `_b2`, `_b3`, … for subsequent batches from the
same run): `BATCH=$(date +%Y%m%d_%H%M%S)`.

### 3. Generate the Verification Prompt

One file per batch. **Claims are numbered by their `fact_check_claims.id`**
(the DB primary key), never by list position — that is what makes processing
unambiguous. Use this template verbatim except for the claim list:

```
REF: fact_check_<BATCH_ID>

You are verifying factual claims for a college-level historical education
platform about Panama. The claims below require verification against primary
and scholarly secondary sources (academic journals, books by recognized
historians, archival material); your job is to check each one against such
sources.

THE DELIVERABLE IS A MACHINE-READ VERDICT TABLE. Your report is parsed by a
script, not read by a person. The script looks for lines beginning with
"VERDICT:" and IGNORES EVERYTHING ELSE. A beautifully researched report that
states its verdicts only in prose, headings, or a summary table is DISCARDED
IN FULL and the whole research run is wasted. Write the verdict lines first,
before any narrative, and never substitute another format for them.

Your report MUST open with a section headed exactly "## Verdicts" containing
nothing but verdict lines — one per claim you researched, in the order the
claims are listed below. Each line, on a SINGLE line:

VERDICT: <claim number> | <VERIFIED or INACCURATE or DISPUTED or UNVERIFIABLE> | <note> | <citations>

Worked example of the required output:

## Verdicts
VERDICT: 41 | VERIFIED | - | Castillero Calvo, Historia General de Panamá, 2004
VERDICT: 42 | INACCURATE | The city was founded in 1519, not 1521 | Hernández Mora, Martín & Aram, Historical Archaeology 55(2), 2021
VERDICT: 43 | DISPUTED | Sources split between 1563 (decree) and 1565 (resumed function) | Wikipedia, Real Audiencia of Panama; PARES, A.G.I. Panamá 236
VERDICT: 44 | UNVERIFIABLE | - | -

Field rules:
- <claim number> — the number printed with the claim below. Copy it exactly;
  never renumber, never use list position.
- <note> — the correction for INACCURATE, the competing positions for
  DISPUTED, a dash otherwise.
- <citations> — full source citations (author, title, year — include a URL
  when one exists), semicolon-separated. MANDATORY for VERIFIED, INACCURATE,
  and DISPUTED (an uncited one is rejected); a dash for UNVERIFIABLE.
- Never wrap a verdict line, never bold or bullet it, never put prose on it.

After "## Verdicts" you may add "## Notes" with as much discussion, nuance,
and narrative as you like — it is ignored by the processor, so put every
caveat there rather than bending the verdict lines.

For EACH numbered claim, determine a verdict:
- VERIFIED — reliable sources confirm the claim
- INACCURATE — reliable sources contradict it; state the correct fact
- DISPUTED — reputable sources genuinely disagree; summarize the positions
- UNVERIFIABLE — you could not find sufficient reliable sourcing either way

Pick exactly one of those four labels per line. Hedged labels
("PARTIALLY VERIFIED", "VERIFIED with nuance", "VERIFIED / DISPUTED") are
NOT in the vocabulary and are rejected by the parser: choose the closest of
the four and put the nuance in <note> or in "## Notes".

Ground every verdict in sources you actually retrieved and read DURING THIS
SESSION. NEVER assign VERIFIED, INACCURATE, or DISPUTED from your own
knowledge alone — those verdicts require consulted sources, and their
citations must refer to those sources, never to works cited from memory.
UNVERIFIABLE is a research outcome, not an error state: use it only for a
claim you actually searched and still could not source either way. If your
research tools fail and you cannot search for a claim at all, OMIT that
claim's verdict line entirely and describe the tool failure in prose — an
unanswered claim is automatically re-queued for a later run, while any
verdict, even UNVERIFIABLE, is recorded as a finished check.

CLAIMS:
CLAIM <id> (<claim_type>): <claim_text>
CLAIM <id> (<claim_type>): <claim_text>
...

REMINDER — the deliverable: begin your report with "## Verdicts" followed by
one "VERDICT: <claim number> | <label> | <note> | <citations>" line per claim
you researched, using only the four labels above. Prose-only verdicts cannot
be read by the processor and the run is discarded. Put narrative in "## Notes"
after the verdict lines.
```

### 4. Save the Prompt(s)

```bash
mkdir -p ai_generated_content/fact_check_prompts/processed \
         ai_generated_content/fact_check_documents/processed
```

Write each batch to:
```
ai_generated_content/fact_check_prompts/fact_check_<BATCH_ID>_prompt.md
```

### 5. Report to User

Display:
- The file path(s) of the saved prompt(s) and how many claims each covers,
  grouped by subject (`SUGGESTION 12: 4 claims, RESEARCH 7: 11 claims, ...`)
- The next steps:
  1. Open the prompt file
  2. Paste or attach it in Claude UI with deep research enabled
  3. Download the result to
     `ai_generated_content/fact_check_documents/fact_check_<BATCH_ID>_verdicts.md`
  4. Run `/fact-check-process` to write the verdicts back to the database
