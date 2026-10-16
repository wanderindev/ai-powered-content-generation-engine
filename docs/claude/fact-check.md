# Fact-Check Pipeline (epic #373; edu extension #459)

> Split out of the root `CLAUDE.md` to keep it under Claude Code's size
> limit; same rules, same authority. "Above" / "below" references may
> point to `CLAUDE.md` or to a sibling file in `docs/claude/`.

## Fact-Check Pipeline (epic #373; edu extension #459)

Claims live in `fact_check_claims` keyed by `(subject_type, subject_id)`
(`FactCheckSubject` in `app/models/enums.py`), and the **subject decides the
tier** (`_run_fact_check_for_subject` in `app/services/fact_check.py`):

- **External** — `SUGGESTION`, `RESEARCH`, `EDU_RESEARCH` (#460). Always the
  extract-only, deep-research-first tier (`extract_for_deep_research`, #389):
  Haiku extraction + verified-claims KB matching, with everything unresolved
  queued as `NEEDS_DEEP_RESEARCH` for the manual loop. The kickoff endpoints
  take no `mode` (a stray `?mode=` is ignored, and an old job row's
  `params.mode` is too). **History:** a Sonnet + web-search tier (#376) sat
  beside it as an API-only escape hatch (`mode=web_search`). It ran once
  (2026-07-12, 193 claims across 2 subjects, the cost measurement behind
  #389), was tuned in #743–#749 and removed in #751 with zero callers. Its
  `WEB_SEARCH` rows (~193 claims, ~192 KB rows) are historical: the
  `FactCheckMethod` value and its CHECK constraints stay, and the KB keeps
  serving those verdicts via `KB_MATCH`. If the claude.ai / extension
  deep-research route ever goes away, the fallback is Gemini or OpenAI deep
  research over the API, not reviving this tier. That tier was also the only
  writer of `PENDING` claim rows (capped-run overflow); prod had none when it
  was removed, and `extract_for_deep_research` still flips any `PENDING` rows
  it finds to `NEEDS_DEEP_RESEARCH` (after KB matching) in place.
- **Grounding** — `ARTICLE`, `EDU_MATERIAL` (#461/#464): judges derived text
  against the research it was written from, no web access. Always against the
  **full** research, never a material's condensed `source_briefing` (that would
  ratify whatever the condense pass dropped). Only `guide` and `slides` are
  groundable (`GROUNDABLE_MATERIAL_TYPES`) — a `video` is a render of its deck's
  narration, so checking the deck *is* the video's fact-check, and the endpoint
  refuses a video with that pointer.

All tiers are verdict-conservative: a malformed or missing LLM response degrades
to `UNVERIFIABLE` / `NEEDS_DEEP_RESEARCH`, never to `VERIFIED`.

**Citation rule per tier.** Deep research requires evidence for three
verdicts — `VERIFIED`, `INACCURATE` and `DISPUTED`, exactly the KB-reusable
set:

- **Deep research** (`_DEEP_VERDICTS_REQUIRING_CITATION`): any non-dash
  citation (a named archive or source counts; no URL required); an uncited line
  is skipped and the claim stays queued. `UNVERIFIABLE` needs none.
- **Grounding** has no citation rule — it judges against one research document
  and never feeds the KB.

**Grounding's `ABSENT` label is mapped per subject (#555).** `ground_subject`
is shared by both grounding subjects, and `grounding_verdict` applies
`_GROUNDING_VERDICT_OVERRIDES` over the base `_GROUNDING_VERDICTS`: for an
`ARTICLE`, a claim its research neither supports nor contradicts — and a claim
the judge returned no line for, which is treated as `ABSENT` — is stored
`NEEDS_DEEP_RESEARCH`, so the deep-research drain resolves it (the
generate-prompt endpoint has no subject filter by default, and a scoped call
accepts `subject_type=ARTICLE` + `subject_id` to drain one article, #574). `EDU_MATERIAL` has
no override and keeps `ABSENT` → `UNVERIFIABLE`. An unknown label is still
`UNVERIFIABLE` for both. The agent driver's `WAIT_ARTICLE_CLAIMS_RESOLVED` gate
holds an article's corrections step until those claims are resolved
(`docs/claude/agent-driver.md`). Article claims grounded before #555 keep their
`UNVERIFIABLE` rows; only a re-ground rewrites them.

**A re-ground still replaces the rows wholesale, but a claim it would queue
reuses what is already known first (#575).** For each claim that maps to
`NEEDS_DEEP_RESEARCH` — so only an `ARTICLE`'s `ABSENT`, since the lookup keys
off the mapped verdict and `EDU_MATERIAL` never maps there; that includes a
claim the judge returned no line for, which is treated as `ABSENT` and so also
gets the carry-over and the KB lookup — `ground_subject`
looks in order at: (1) the subject's own earlier row with the same `claim_key`
(`_normalize`, the KB's key) that deep research resolved
(`checked_via=DEEP_RESEARCH`, verdict `VERIFIED` / `INACCURATE` / `DISPUTED` /
`UNVERIFIABLE`), which is carried over **unchanged** — still `DEEP_RESEARCH`,
original `checked_at`, correction and citation; (2) the verified-claims KB via
`match_claims_against_kb` (exact key, then the Haiku pass), inherited as
`KB_MATCH` like the external tier does; and only then queues it. `SUPPORTED` /
`CONTRADICTED` claims always keep their fresh grounding verdict — the KB never
overrides what the article's own research says. Three consequences:

- **`CORRECTED` is not carried over.** It describes content a correction
  edited; if re-extracted text asserts the claim verbatim, the content asserts
  it again, so it falls through to the KB — which still holds `INACCURATE` for
  that key — and the corrections flow can fix it again.
- **A carried-over or KB-inherited `INACCURATE` is gated and correctable** like
  any other: corrections select `INACCURATE` rows with a non-null `correction`
  and never look at `checked_via`.
- **The match is exact on `claim_key` for (1).** Re-extraction that rephrases a
  claim misses the carry-over and relies on the KB's fuzzy pass, which only
  covers `VERIFIED` / `INACCURATE` / `DISPUTED` — a rephrased deep-research
  `UNVERIFIABLE` is queued again.
- **The KB lookup can never fail the grounding (#586).** Any exception from
  `match_claims_against_kb` inside `ground_subject` — a Haiku error,
  `BudgetExceededError`, `RefusalError`, `TruncationError`, a network error —
  is logged and Sentry-captured, the claims stay `NEEDS_DEEP_RESEARCH` /
  `GROUNDING`, and the job still succeeds, so the paid-for extract and judge
  calls are kept. The caller's `progress_callback` is invoked once right before
  the KB's Haiku pass (only when there is one — an exact-key-only lookup makes
  no LLM call), so the job runner's committing `progress` frees the pool slot
  the prior-row / KB reads pinned. Nothing has been written at that point —
  the delete comes after — and `match_claims_against_kb` snapshots the KB rows
  into plain values before that call so its Haiku loop never re-SELECTs.

**`EduMaterial.content` is never a material's source of truth** — it is a derived
projection for both groundable types, and that is the trap this epic kept hitting:

| Material | Real container | `content` is |
|----------|----------------|--------------|
| `guide` | `content_blocks` / `content_blocks_es` (JSONB, #340) — what the branded PDF renders | a `blocks_to_markdown` projection |
| `slides` | the `edu_slides` rows — `title`/`body` feed the renderer (#321), `narration` feeds the TTS (#322) | a `slides_preview_text` preview |

So claim extraction *and* correction write-back read and write the real container
and re-derive `content` (`app/utils/guide_blocks.py`, `app/utils/slide_text.py` —
generic string-leaf / `(slide_id, field, text)` segment walkers, so a new
`GuideBlockType` needs no fact-check change; machine keys such as `image_query`,
stored urls/ids and an mc question's `answer` are excluded from the walk). A
correction written to `content` would never reach the PDF or the audio and would
be clobbered by the next regeneration. A deck is Spanish-native (#320), so it is
grounded in Spanish and `REPLACE` snippets must stay speakable.

**Claim writes feed the RAG index (#625).** VERIFIED / CORRECTED article claims
and every verified-claims KB row are `knowledge_chunks` rows
(`app/services/knowledge/claims.py`; detail in
`docs/design/rag-knowledge-layer.md`). A new path that writes article claim
verdicts or the KB must trigger `reindex_claims` / `schedule_claims_reindex`
after its commit — today that is `run_fact_check_job`'s tail, the
deep-research verdicts endpoint, the MCP `submit_fact_check_verdicts` tool
(#641; the `/fact-check-process` skill's default path since #643) and
corrections apply. Direct SQL (the `/fact-check-process` skill's SQL fallback)
is only repaired by `python -m scripts.reindex_knowledge --all`. A KB chunk is a fact only when
`meta.verdict = VERIFIED`; INACCURATE / DISPUTED rows are indexed for matching.

### Pipeline gates (#462)
`app/services/fact_check_gate.py` is what the claim state is *for*, on two tiers:
`INACCURATE` blocks every gated step; `NEEDS_DEEP_RESEARCH` / `DISPUTED` block
only the public ones; `CORRECTED` (#387) and never-fact-checked never block.
`enforce_material_gate(..., STEP_INTERNAL)` guards translate, generate-pdf,
generate-audio and assemble-video; `STEP_PUBLIC` guards publish; a refusal is a
409. Soft warnings live in the **frontend** on purpose — a backend "warning"
needs an override flag, and an override flag is a block everyone learns to
bypass, so what the backend refuses it refuses outright.

**That whole frontend half is one hook module**, `frontend/src/hooks/
useFactCheck.js`, shared by five admin grids — so a soft gate is added there,
not in a page. It exports two pure functions plus the hook: `factCheckState`
collapses a counts map to one of six kinds (`none` / `flagged` / `open` /
`disputed` / `corrected` / `passed`), and `factCheckWarning` turns that into the
approve-anyway sentence. Four properties to know:

- **The two vocabularies differ by surface.** The blog grids
  (`AdminSuggestions`, `AdminResearch`, `AdminArticles`) call
  `factCheckWarning` on a status change to `APPROVED` and warn on *anything*
  non-clean, including never-checked; `AdminEduMaterials` instead reads
  `factCheckState` directly and warns on exactly two cases the backend lets
  through — `open`/`disputed` before translate (a later correction means paying
  DeepL for the same guide twice) and `none` before publish. Both are
  warn-then-proceed dialogs; neither can refuse.
- **`CORRECTED` is terminal green and `DISPUTED` is tracked apart from
  flagged**, so the red count can reach zero: only `INACCURATE` claims are
  auto-correctable by the corrections flow, and disputed ones need editorial
  judgment rather than a fix action.
- **A failed summary load is indistinguishable from never-checked.** The
  mount-time `getFactCheckSummary` is deliberately non-fatal (it only
  `console.error`s so the grid still works), which leaves the map empty — every
  badge then reads "Not checked" and every approve fires the unchecked warning.
  Check the network tab before concluding a subject lost its claims.
- **Running a check is a client-side poll loop with no server-side memory of the
  wait**: kick off the 202, then poll the `crawl_jobs` row every
  `POLL_INTERVAL_MS` (5s) up to `MAX_POLLS` (540, i.e. 45 minutes) and toast the
  result. Giving up only tells the operator to reload later — closing the tab
  loses the notification, not the job.

The invariant that makes the gate meaningful: **replacing a material's text
invalidates its claims.** Any endpoint that rewrites blocks or slide rows must
call `invalidate_material_claims` (already wired into generate-slides,
regenerate-slides, generate-narration and both manual-edit endpoints) — stale
verdicts would otherwise block on an error already written away, or pass
regenerated text on an older run's `VERIFIED` rows.

### How `CORRECTED` is reached (`app/services/fact_check_corrections.py`, #387)
The gate's escape hatch is not "an admin edits the text" — it is a two-endpoint
flow (`.../fact-check/{subject_type}/{id}/corrections/preview` then `/apply`,
`RESEARCH` and `ARTICLE` only) that the driver's `APPLY_*_CORRECTIONS` kinds
drive. Four rules make it safe to run unattended:

- **Edits, not rewrites.** One Sonnet call per language returns plain-text
  `FIND` / `REPLACE` blocks, and an edit applies only when its `FIND` occurs
  **exactly once** across that language's content — so the model can never touch
  unrelated prose. Same discipline as the grammar check and the IG parser.
- **Preview then apply.** `build_preview` generates and validates statelessly;
  `apply_corrections` re-validates the previewed payload and writes. What you
  preview is what applies — the LLM is not called again at apply time.
- **Per-claim atomicity.** A claim flips to `CORRECTED` only when *every* one of
  its edits applies (both languages, for articles with Spanish content); if any
  edit fails the claim is left completely untouched, so content is never
  half-corrected while still flagged `INACCURATE`.
- **`EDIT_SKIPPED` resolves too, and the KB stays `INACCURATE`.** When the model
  reports the content never asserted the inaccuracy, applying echoes the skip
  back and the claim flips to `CORRECTED` without touching a byte. `CORRECTED`
  describes *the content*, not the world — `upsert_verified_claims` is never
  called here and skips `CORRECTED` rows by construction.

### The verified-claims KB (`app/models/verified_claim.py`, #378)
Panama-history claims repeat constantly across subjects, so resolved verdicts are
upserted into `verified_claims` and inherited by later runs
(`checked_via=KB_MATCH`) instead of being re-verified. Three rules govern what
may enter it and how it is keyed:

- **Only externally-resolved, reusable verdicts.** `VERIFIED` / `INACCURATE` /
  `DISPUTED` reached via `DEEP_RESEARCH` (or the historical `WEB_SEARCH`,
  #751) qualify —
  `PENDING` / `UNVERIFIABLE` / `NEEDS_DEEP_RESEARCH` carry no reusable
  information, `KB_MATCH` rows came *from* the KB, and **grounding verdicts are
  relative to one research document, not the historical record**, so feeding the
  grounding tier into the KB would promote "consistent with this source" to
  "true".
- **`claim_key` is `_normalize(text)`** — lowercase, alphanumerics and spaces
  only, whitespace collapsed — and it is UNIQUE. The `/fact-check-process` skill
  mirrors that normalization in SQL, so changing one without the other silently
  stops deduping. `upsert_verified_claims` also stages the batch in a dict:
  `SessionLocal` runs `autoflush=False`, so two claims normalizing to the same
  key would both INSERT and 500 the whole upload.
- **Matching is two-stage and conservative.** An exact normalized-text match
  (free, deterministic) first, then a Haiku confirmation pass over word-overlap
  candidates that is told to prefer `NONE` and whose returned id is accepted only
  if it was among *that* claim's candidates.
- **Vector candidates are behind `kb_vector_matching_enabled` (#628, default
  off).** On, the exact key is a keyed query (no load of the whole KB), the
  remaining claims get one batched `embed_queries` call and their candidates
  are the nearest `VERIFIED_CLAIM` chunks (`retrieval.search`, all three KB
  verdicts — never `facts_only`) at or above `kb_vector_min_similarity`, at
  most `kb_vector_top_k`; the Haiku pass and its guard are the same function.
  `before_llm` then runs before the Voyage call as well as before Haiku.
  Disabled embeddings or a failed query embedding fall back to word overlap
  (one log line per call). `python -m scripts.eval_kb_matching` is the
  read-only before/after replay to run before promoting it.

### Cross-article consistency, shadow mode (`app/services/consistency_check.py`, #631)
Behind `cross_article_consistency_enabled` (default **off**). Off, the
fact-check runner is unchanged: the job tail reads the setting and returns,
with no query and no call. On, after an `ARTICLE` job has committed
`SUCCEEDED`, closed its session and run the #625 claims reindex,
`run_consistency_check` compares the article's claims with facts from
**other** articles and the KB, and records each pair a Sonnet judge
(`effort: "low"`, `feature="consistency_check"`, batched) labels `CONTRADICTS`
in `consistency_findings` (migration `0046`). No new job type, and the driver's
poll sees `SUCCEEDED` before the check starts.

- **Shadow mode.** It never changes a `fact_check_claims` verdict, never gates
  publishing, is in no `KIND_CALLS` / `DRIVER_ALLOWED_CALLS` entry and never
  raises: a failed judge batch is skipped, and any other failure is logged and
  sent to Sentry with the findings left as they were. Turning it into a gate is
  an operator decision that needs #632's numbers.
- **Retrieval.** One batched `embed_queries` for the claims, then
  `retrieval.search(mode="vector", visibility="internal",
  source_types=[ARTICLE_CLAIM, VERIFIED_CLAIM], exclude_article_ids=[article],
  query_vector=...)` per claim. KB chunks have no `article_id`, so the exclusion
  keeps them. Bounded by `consistency_max_claims` (40),
  `consistency_top_k` per claim (3), `consistency_min_similarity` (0.5,
  kept by #632), `consistency_max_pairs` per article (120, most similar first)
  and `consistency_batch_pairs` per Sonnet call (20). No session is open across
  the Voyage or Sonnet calls.
- **INACCURATE KB rows are not filtered out, they are reframed.** The search
  does not pass `facts_only`, because a claim that repeats a known falsehood is
  what the check should surface. The judge sees each reference as an
  established fact plus, where there is one, the `KNOWN FALSEHOOD` it replaced.
  For an INACCURATE row the fact is its **correction**, and a row with no
  correction is skipped. A DISPUTED row is skipped, since neither side is a
  fact. A CORRECTED article claim is its correction, with the original claim
  shown as the falsehood. KB candidates are reloaded by id, so the row's current
  verdict wins over a lagging chunk.
- **Parse conservatively.** Lines are `PAIR: n | LABEL | explanation`. Missing
  or malformed lines, an unknown label, or a `CONTRADICTS` with an empty
  explanation all count as `UNRELATED` and never become a finding.
- **The table.** Only `CONTRADICTS` rows are stored; the CHECK also allows
  `CONSISTENT` / `UNRELATED`, and per-label counts are in the log. `claim_id`
  is `ON DELETE SET NULL` with `claim_text` copied, because a re-run
  re-inserts the claims under new ids. A CASCADE would wipe resolved findings
  every time. `other_source_id` is polymorphic and has no FK. `other_text` is
  the fact the judge saw. A re-run where every batch was judged replaces the
  article's **open** findings and keeps resolved ones. A run with a failed batch
  only adds. A pair whose (claim, other) text already has a finding is never
  inserted again, so a dismissal sticks.
- **Admin.** `GET /admin/dashboard/articles/{id}/consistency-findings` and
  `POST .../consistency-findings/{fid}/resolve` (`DISMISSED` | `RESOLVED`).
  Resolving is idempotent; the first resolution sticks. The articles grid's
  `consistency_open_count` comes from its own query in a SAVEPOINT, so the
  listing works (count 0) before 0046 is applied. The drawer's EN tab lists the
  open findings.
- **Cost.** `create_message` logs tokens per call. One INFO line per article
  (`consistency check: article=… pairs=… input_tokens=… est_cost_usd=…
  elapsed_ms=…`) gives the per-article totals. The estimate uses a $3 / $15
  per 1M placeholder, because no Sonnet 5.5 price is recorded in the repo.
