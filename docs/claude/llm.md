# LLM Access Layer

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

## LLM Access Layer

**Every** Anthropic call goes through `create_message` in `backend/app/services/llm.py` —
never call `client.messages.create` directly. That seam owns model ids
(`SONNET_MODEL` / `OPUS_MODEL` / `HAIKU_MODEL` — bump a model there, nowhere else),
the shared client, token-usage logging, the in-process budget backstop, and
auto-streaming for large `max_tokens`. Recurring failure modes to design against:

- **Thinking tokens count toward `max_tokens`.** Sonnet 5.5, Opus 5.5 and Haiku 5.5
  all run adaptive thinking; at a high effort a large batch can spend the whole budget
  on thinking and get cut off before emitting output. Give call sites real headroom and
  pass `output_config={"effort": "low"}` for mechanical work (see the fact-check tiers).
- **`reject_on_truncation` defaults to `True`** and raises `TruncationError` when the
  response stops at `max_tokens`. Pass `reject_on_truncation=False` only where a
  length-capped result is genuinely acceptable, and trim it yourself
  (`research_summary.py`, `fact_check_corrections.py`). A prompt-only "stay under N
  words" instruction is *not* a length guarantee — that gap has 502-ed the admin
  Research "Summarize" action twice.
- **Never index `message.content[0]`** — block 0 can be a thinking block. Use
  `first_text()` / `parse_llm_json()`. Since #610 moved Haiku to 5.5 (which
  thinks by default) there is no direct-index site left in `app/` or
  `backend/scripts/` — the five Haiku sites that used to get away with it were
  converted, because a medium-effort Haiku 5.5 reply starts with a `thinking`
  block too. `first_text()` falls back to `content[0]` only when no block
  advertises a string `type` (which is what keeps `MagicMock` test mocks
  working); a response whose typed blocks include no `text` — a
  truncation-tolerant call cut off while still thinking — raises `ValueError`
  ("no text block") rather than an `AttributeError` on the thinking block.
- **`backend/scripts/` is on the seam too — keep it that way.** The last
  holdout, `backfill_media_tags.py`, built its own `anthropic.Anthropic(...)`,
  called `client.messages.create` with a hardcoded Haiku 4.5 id and indexed
  `content[0]`, so it ran with no usage logging, no `feature=` label, no budget
  backstop and no `TruncationError`. #610 moved it onto `create_message` +
  `HAIKU_MODEL` + `first_text` (`feature="backfill_media_tags"`), as #589 had
  done for `backfill_search_terms.py`. The two DeepL scripts
  (`translate_frontend.py`, `translate_taxonomy_category.py`) still build their
  own `deepl.Translator` — that is the translation seam, not this one. A new
  script that calls Claude belongs on `create_message` unless it has a stated
  reason not to.
- **All three model ids are bare, and the request surface is narrower than
  the SDK's.** Sonnet 5.5 (`claude-sonnet-5-5`), Opus 5.5 (`claude-opus-5-5`)
  and, since #610, Haiku 5.5 (`claude-haiku-5-5`) have **no published dated
  snapshot** — the bare id is the only valid string (never append a date); the
  seam's comment says to pin a `-YYYYMMDD` id should one ever appear. On all
  three, manual `budget_tokens` thinking, non-default `temperature` / `top_p` /
  `top_k` and an assistant prefill are **rejected with a 400**; on Sonnet and
  Opus so are `thinking={"type": "disabled"}` (at **every** effort level) and
  forced `tool_choice` (`any` / `tool`) — Haiku 5.5 accepts both, but nothing
  here uses them. That is why none of those kwargs appears anywhere in `app/`;
  effort is the only thinking control. The two call sites that want thinking
  explicitly pass `thinking={"type": "adaptive"}` (`edu_material_generation.py`'s
  condense, `suggestion_generation.py`), which is equivalent to omitting it.
  **Opus 5.5's and Haiku 5.5's effort default is `medium`, not `high`**
  (Sonnet 5.5's stays `high`); every Opus call site sets `effort`, and every
  Haiku call site passes `output_config={"effort": "medium"}` explicitly with a
  cap raised for thinking (#610 — a ~40-word excerpt measured 389 output
  tokens, past the old 256 cap). Haiku 5.5 also uses the Opus 4.7+ tokenizer,
  so the same text is ~30% more tokens than on Haiku 4.5.
- **Structured outputs started with Ask PIC (#694).**
  `ask_pic.run_pipeline` sends `output_config={"effort": ..., "format":
  {"type": "json_schema", "schema": ANSWER_SCHEMA}}` (schema in
  `app/prompts/ask_pic.py`), because Haiku 5.5 sometimes wrote prose before
  the JSON and `parse_llm_json` (strict: the whole text block, fences allowed)
  rejected it. Structured outputs work on all three 5.5 models and keep
  adaptive thinking; a forced `tool_choice` was not used because Haiku 5.5
  **skips thinking** when the tool is forced (and Sonnet/Opus 5.5 400 on it).
  The schema must be strict — `additionalProperties: false` on every object,
  every key `required`, no `minLength` / `maximum`-style constraints. Ask PIC
  still parses defensively (`_answer_json`: strict, then the last top-level
  JSON object with a `status`), and logs/evals report which path parsed; do
  not loosen `parse_llm_json` itself, other callers rely on its strictness.
  Two later Haiku sites reuse the pattern at effort `low`: the #696 query
  rewrite (`ask_pic.rewrite_queries`) and the #697 chunk situating sentences
  (`knowledge/context.py`, `feature="knowledge_context"`, one call per
  article language from the indexer's background task; any failure means
  "no sentence", never an exception).
- **Prompt caching is opt-in per call site — see "Prompt caching" below.** Three
  sites use it (grounding, two eval-harness calls; the web-search fact-check
  that also did was removed in #751); the
  backstop counts cache *writes* but not cache *reads*.
- **`max_tokens` also picks the timeout.** A call streams iff `max_tokens >=
  anthropic_stream_min_tokens` (8000), and streamed calls get
  `anthropic_stream_timeout_seconds` (600s) while non-streamed ones get
  `anthropic_timeout_seconds` (**120s**). So dropping a cap from 8000 to 7500 —
  or writing a small-`max_tokens`, `effort: "high"` call site — silently moves
  that call onto the 120s budget, which adaptive thinking can outrun. Pass
  `stream=` explicitly when the number is near the boundary.
- **List prices live next to the model ids** (`PRICE_PER_MILLION_TOKENS`,
  `estimate_cost_usd`, `worst_case_cost_usd` in `llm.py`, #634); bump them with
  the ids. An unpriced model raises `ValueError` there, so Ask PIC's USD cap
  fails closed. (`consistency_check.py` still carries its own $3/$15 placeholder.)
- **The budget backstop is cumulative, input-only and never auto-resets.**
  `anthropic_budget_input_tokens` (default 20,000,000, 0/negative disables)
  is checked against a **process-lifetime** accumulator of uncached input +
  cache writes (`backstop_tokens`, #716; cache reads are not counted) *before*
  each call, and
  once tripped every LLM feature in that worker raises `BudgetExceededError`
  until the process restarts — nothing in `app/` calls `reset_session_usage()`,
  it exists for tests and long-lived workers. It counts input tokens only and is
  per-worker (prod runs `--workers 2`), so it is a runaway-loop backstop, not
  billing: real per-feature spend is the `feature=` label in the structured logs.
- **Refusals raise, after a server-side fallback.** `create_message` adds
  `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`, via
  `extra_body` / `extra_headers` on the regular `messages` namespace so the
  typed `Message` still parses) to every Sonnet/Opus call — on a safety-
  classifier decline the API re-runs the request on Anthropic's recommended
  model for that refusal category and returns its answer, logged as `fell back
  after a refusal`. Haiku calls are sent without it: Haiku 5.5 runs safety
  classifiers but Anthropic documents **no server-side fallback** for it, so a
  Haiku decline goes straight to `RefusalError`. If the final response is
  still `stop_reason == "refusal"` the seam raises `RefusalError` (a
  `ValueError`, like `TruncationError`) instead of handing an empty or partial
  `content` to a parser. Sonnet 5.5's `"default"` fallback retries only `cyber`
  and `frontier_llm` declines, so a `bio` / `general_harms` decline surfaces as
  the error — and the top-level `usage` covers only the attempt that served, so
  a fallback's declined first attempt is invisible to the budget backstop.
- Opus 5.5 runs the blog article pipeline (condense, outline, content) and, since
  2026-10-04 by operator decision, **every edu generation call** — guide condense /
  outline / content, deck condense / slides, and slide narration — through the
  single `EDU_MODEL` constant in `edu_material_generation.py`. Everything else
  stays on Sonnet 5.5 deliberately. Adding another Opus call site is an
  evidence-backed decision, not a default. The switch from Opus 5 / Sonnet 5 on
  2026-09-29 kept every call site's `effort` and `max_tokens`: the caps were
  measured on the 5.0 models, and Opus 5.5 tends to think more per turn at a
  given effort, so a `TruncationError` on an Opus stage is the first thing to
  check against the `output_tokens` in the logs.
- Downgrading a call site is equally evidence-backed. The article grammar check
  (`grammar_check.py`, #409) is **Sonnet, not Haiku** — benchmarked 2026-07-21 on
  articles 213/211, Haiku found 1 of 5 real fixes. It is also corrections-only:
  the model returns `FIND: … ||| REPLACE: …` lines and never rewrites, a
  malformed line is *dropped* rather than guessed at (the #433/#434 lesson from
  the fact-check corrections parser), and a pair applies only when its FIND occurs
  **exactly once** across that language's title + excerpt + content. Every parsed
  pair writes an append-only `GrammarCheckFix` row, applied or skipped-with-reason.
- **The Grammarly write path has a reviewer in front of it, not an author
  (#518).** `POST /admin/dashboard/articles/{id}/grammar-apply` runs
  `run_meaning_check` (`app/services/grammar_verdicts.py`) before writing:
  Sonnet (`effort: "low"`, `feature="grammar_verdicts"`) reads only the
  *changed* paragraphs side by side and returns one verdict per pair —
  `KEEP`, `REVERT ||| FIND … ||| REPLACE …` (undone through
  `grammar_revert.revert_snippet`, exactly-once and markdown-emphasis
  tolerant, because Grammarly's snippets are plain text and the body has
  `*situado*`), or `REVIEW`. What it cannot vouch for — a `REVIEW`, a revert
  whose FIND was not unique, a pair with no verdict, or a failed model call — is
  **still written** (the operator accepted it) but filed as a
  `grammar_review_diffs` row (migration `0033`), surfaced as a "N to review"
  pill in the admin articles grid. Reverted accepted items land in
  `grammar_check_fixes` as `applied=False` / `reverted by meaning check: …`.
  `dry_run` never calls the model; `grammar_meaning_check_enabled=False` or an
  empty Anthropic key skips it (never crashes). **The verdict prompt is
  calibrated, so treat edits to it as a benchmark question.** The first version
  reverted on changed "emphasis", which Sonnet read as rhetorical intensity once
  bodies lost their bold/italics (#540), so it reverted edits for dropping
  words like "verdadero" or "both". The 2026-09-27 rewrite names what to KEEP
  and what to REVERT, plus three claim shifts a looser draft missed (would/could
  -> did, forbidden -> unable, idiom swaps). It cut over-reverts from 7-8 to 0-1
  of 26 labelled KEEPs and kept 13-15 of 15 real meaning changes. The one weak
  class is broken punctuation or grammar, missed in some runs.
  `scripts/one_off/eval_grammar_verdicts.py` replays the labelled set against the
  deployed prompt or a candidate file. `effort` stays `"low"`: `"medium"`
  missed more real changes. The first live run that made
  this necessary (article 245 EN, 25 accepted, 18 Clarity rewrites that
  changed meaning) was undone with
  `scripts/one_off/revert_grammarly_fixes.py` (#519), which walks the audit
  rows newest-first so stacked fixes unwind.
- The seam covers **Anthropic** calls, not every model call. Feature-image
  generation (`app/services/gemini_image.py`, #419) deliberately sits outside it:
  one POST to a fixed Google endpoint over plain `httpx`, no SDK, no hardened
  transport and no retry layer (the driver's `agent_task_max_attempts` is the
  retry). Its module docstring states each choice — don't "fix" it by routing it
  through `llm.py`. But the *prompt* it sends does come through the seam:
  `POST /admin/dashboard/articles/{id}/generate-image` first calls
  `generate_image_prompt` (`app/services/image_prompt.py`) — Sonnet via
  `create_message`, `feature="image_prompt"`, `max_tokens=4000` with
  `effort: "high"` because default adaptive thinking would truncate a 1024-token
  call — and only then POSTs the returned watercolor prompt to Gemini. So feature
  images are **two** model calls behind one endpoint — the only way a feature
  image is made. The admin grid's Generate / Regenerate image action and the
  driver's `GENERATE_FEATURE_IMAGE` both call it; the old manual loop
  (`image-prompt` + copy into gemini.google.com + `upload-image`) was removed in
  #580. The session is closed across both calls (Connection Pool rule).

## Prompt caching (#716)

Caching is a byte-exact **prefix** match (render order tools → system →
messages): a breakpoint caches everything before it, and calls that start with
the same bytes read it back at the cache-read price instead of full input. On
all three 5.5 models the minimum cacheable prefix is **512 tokens** — shorter
prefixes silently don't cache (`cache_write=0`, no error). Writes cost 1.25×
input (5-minute TTL) or 2× (`"1h"`); reads cost 0.05× on Opus 5.5 and 0.1× on
Sonnet / Haiku 5.5 (`PRICE_PER_MILLION_TOKENS`). A read refreshes the TTL, so
what decides it is the **start-to-start gap** between calls sharing the prefix:
under 5 minutes → default TTL; 5–60 minutes → `"1h"` pays only from the third
call; longer → nothing helps.

**How to add caching to a call site** (helpers in `llm.py`; `create_message`
passes the markers through untouched):

1. Put the stable part first. If the call site's prompt has the instructions
   *after* the varying part, reordering it changes the prompt — and a
   calibrated prompt (the grammar verdicts) needs its eval re-run first.
2. Mark the end of the stable prefix with `cached_text(text)` — as
   `system=[cached_text(SYSTEM)]`, or as the first block of the user turn
   followed by an unmarked `{"type": "text", "text": varying}` block. Never put
   the marker after the varying part: that pays the write premium on every call
   and reads nothing.
3. For server-tool requests and multi-turn resumes, pass
   `cache_control=cache_control()` (top-level automatic caching) instead; the
   API then caches across the server-side sampling iterations too.
4. Measure: run the call twice inside the TTL and check the
   `anthropic call: feature=… cache_read=… cache_write=…` log line. Keep the
   change only if the second call reads and the saving beats the write premium.

**Which features cache today**

| feature | model | what is cached | measured (#716) |
|---|---|---|---|
| `fact_check_grounding` | Sonnet | research doc block, one per article, read by every claim batch (#413) | — (pre-existing) |
| `rag_eval_reader_drafts` (script) | Opus | title listing + drafting rules (~3.6k tokens) | batches 2..N read 3,647 tokens: −$0.014/batch (~11%) |
| `eval_judge` (script) | Sonnet | judge system prompt + output schema (~940 tokens) | calls 2..N read 936 tokens: −$0.0017/call (~30% of judge input on short prompts) |

**Checked and left uncached** (prefix sizes from `count_tokens`, 2026-10-13):
`grammar_check` system 292 tokens, `es_accuracy` 484, `consistency_check`
template 394, `media_scoring` 419, `knowledge_context` 192 — all under the 512
minimum. `grammar_verdicts` (892) runs once per Grammarly apply, far apart.
`ask_pic` (665-token system on Haiku 5.5) saves at most ~$0.00006 a call and
prod traffic is sparse, so writes would be a surcharge. The **edu** pipeline
condenses the same research doc (~27k Opus tokens) once per band and again for
slides/narration, and the **blog** pipeline condenses the same research
(~14k) once per series article — but in both prompts the document comes *after*
per-band / per-article instructions, the calls sharing it start more than 5
minutes apart, and a document-first reorder of a high-effort Opus prompt with a
1-hour TTL saves roughly $0.05–0.10 per job. Revisit if generation volume grows.

**The backstop and caching.** `backstop_tokens(usage)` = uncached input +
cache writes. Writes are full-price-plus input, and a marker placed after the
varying part writes a fresh entry on every call, so they count. Reads don't:
they bill at 5–10% of input, and counting them would trip the 20M cap on
legitimate cached batch work at a fraction of the spend it was sized for. The
`process_total_input` in the usage log is this same number.

**Measuring spend.** `python -m scripts.llm_usage_report --days 30 --api-key-id
<id> [--daily] [--org-wide]` (from `backend/`, `ANTHROPIC_ADMIN_KEY` in the
environment — never a flag or a file in the repo) prints uncached input, cache
reads/writes, output, cache-read share and list-price USD per model from the
Usage Admin API. The API can't see `feature=` labels; per-feature attribution
comes only from the usage log lines. Prod and local `backend/.env` share one
API key, so the report includes local evals and scripts.
