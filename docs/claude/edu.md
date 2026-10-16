# Edu Content Generation, Media Production, Taxonomy and Public Deliverables

> Split out of the root `CLAUDE.md` to keep it under Claude Code's size
> limit; same rules, same authority. "Above" / "below" references may
> point to `CLAUDE.md` or to a sibling file in `docs/claude/`.

## Edu Content Generation (`app/services/edu_material_generation.py`)

What produces the text the media pipeline below renders. `edu_materials` is a
**discriminated union** (#95, `ck_edu_materials_one_research_fk`:
`num_nonnulls(research_id, blog_research_id) = 1`) — *generated* materials hang
off an `EduResearch` via `research_id`, *uploaded* NotebookLM artifacts off a blog
`Research` via `blog_research_id`. Uploaded rows leave `research_id` NULL, so
under Postgres' NULLS DISTINCT they never collide on
`uq_edu_materials_research_type_band`, the constraint (#97) that backstops the
read-then-write per-band upsert. Publication is separate from approval (#326,
mirroring `Article.published_at`): public means APPROVED **and** `published_at`
set and in the past.

**Study guides are per grade band, and each band commits independently** (#90).
`generate_edu_materials` loops the suggestion's bands through condense → outline →
content; a band that succeeds is durable even if a later band raises, a band that
fails is rolled back and recorded in `failed_bands`, and the loop continues — so a
re-run regenerates only the missing/PENDING/failed bands, with APPROVED bands
skipped (no LLM call) unless `force`. The admin "Generate Study Guides" modal
makes that re-run reachable: `GET /edu/research/available-for-materials` lists
approved research while any band lacks an APPROVED guide and returns
`bands_to_generate` (it used to drop research with *any* material,
so a lost band could not be resumed from the UI). `len(grade_bands) > MAX_GRADE_BANDS` (6,
`app/schemas/edu.py`) raises *before* the loop spends anything (#92). The condense
step runs with **adaptive thinking and `effort: "high"`** at 20,000 tokens
(#338) — deciding what to keep is a judgment task, and at a handful of guides the
cost is negligible. The band's `focus_for` lens is threaded
through every stage.

**Every LLM call in this module is `EDU_MODEL` — Opus 5.5 since 2026-10-04 (operator
decision; it was Sonnet 5.5 before, and the caps below were measured on Sonnet,
so a `TruncationError` after the switch means Opus out-thought a cap) — at an
explicit `effort: "high"` (Opus 5.5 would default to `medium`), so its
`max_tokens` is thinking headroom first and output second.** Six `feature=`
labels, but only three distinct caps: `edu_outline` 32,000, `edu_narration`
64,000, `edu_content` 64,000, `edu_slides` 40,000 (the named
`SLIDES_MAX_TOKENS`, shared by the full-deck call *and*
`regenerate_slides_subset`), and both condense passes — the guide's
`edu_condense` and the deck's `edu_slides_condense` — routed through one
`_condense_research` helper at `CONDENSE_MAX_TOKENS` (40,000). So a `feature=`
label here does not imply its own cap, and the two condense labels exist to
separate the spend in the logs, not the budget. Each number carries a measured
comment, the same discipline as the blog pipeline's Opus caps above, and two of
them moved on 2026-09-22: `edu_slides` **truncated in prod** at 12,000 (Sentry
`PIC-PYTHON-FASTAPI-1Z`) because planning a 16–22 slide arc over a ~3500-word
briefing spent the budget before the Spanish JSON was emitted — surfacing as a
`create_message` truncation-guard 500 rather than silent short output, the guard
being deliberately left armed here; `edu_content` was raised **pre-emptively**
in the same session, after a 9th–12th guide measured 15,217 output tokens
against its 16,000 cap, i.e. one richer research document away from the same
failure — and that 20,000 then **did** truncate after the Sonnet 5.5 switch (Panama
History Museum 9th–12th, job 246, 2026-10-03). **All of them roughly doubled
on the Opus switch**: the first two Opus runs of that same band (jobs 264/265)
truncated `edu_outline` at 12,000 both times, and `edu_condense` used 19.4k of
its 20,000. Opus also overshot the condense word target (~7,700–8,000 words
against 4,500), which inflates the outline's input. Opus 5.5 allows 128K output
when streaming. Every cap sits above `anthropic_stream_min_tokens` (8000), so all
of them stream, and the SDK's 600s timeout applies per read, not per call.
Trimming one under 8000 would move it onto the 120s non-streamed budget, which is
the trap the LLM Access Layer records.

**Neither `excursion_key` nor `grade_bands` is required on an `EduSuggestion`**
(both columns are nullable), and the two halves of the pipeline disagree about
what that means. `app/services/edu_research.py` is **mode-aware on both axes** by
design — no `excursion_key` drops all pre-visit / on-site / guided-tour framing,
no `grade_bands` targets one general educator level — so a standalone,
non-excursion edu topic is a supported shape, not a malformed row. But
`generate_edu_materials` is `for band in (suggestion.grade_bands or [])`: a
band-less suggestion runs the loop zero times and returns three empty lists with
**no error and no LLM call**, which reads as a silent no-op rather than the
"no grade adaptation" case the research prompt promised. Handle the empty-band
suggestion explicitly before assuming a generation call did nothing because of a
gate.

**Decks are a deliberate two-stage flow** (#320) — collapsing them re-creates the
bug the redesign fixed:

1. `generate_edu_slides` writes **content only** (template / title / body /
   `image_query`, **no narration**) from a condensed briefing of the *research*,
   persisted on `EduMaterial.source_briefing`. It is generated from the research
   and **not** from the approved guide's text, which starved decks down to ~7
   minutes. A human then edits, picks real photos for the `image_query`
   placeholders, and approves.
2. `generate_edu_narration` writes Diego's script against the **final** slides plus
   that **same** persisted briefing — so narration and on-screen content share the
   facts the human approved, with no re-condense drift — budgeted to land the video
   at 15–17 minutes.

`force` on slides **replaces every row**, dropping media picks, narration and
rendered artifacts by design. The `positions` subset paths are the non-destructive
ones: `regenerate_slides_subset` re-rolls only the targeted slides in place
(keeping `id`, photo pick, narration and audio) and clears just the now-stale
frame `image_url`; a targeted narration rewrite clears only `audio_url` /
`duration_ms`. Both are strict — the response must cover exactly the targeted
positions or nothing is saved.

Two more rules the generators enforce for the operator, not the model:
excursion-linked guides are titled from the excursion's `display_name` /
`display_name_es`, never the LLM's echo, and `apply_spanish_block_overrides`
re-imposes the canonical ES cover and review-section titles after DeepL; while
`validate_review_ladder` is **warn-only by design** — callers log its problems
rather than failing a multi-minute run, because the admin edit UI is the
correction path.

**Uploaded materials are validated then watermarked, both fail-closed.**
`POST /edu/materials/upload` accepts `slides` (PDF), `infographic` (PNG) and
`podcast` (m4a) from an APPROVED blog research, checks the **magic bytes** against
the declared `material_type` first (#94 — a mismatched file used to fail
watermarking and ship a garbage row under the wrong extension), strips the "Made
with NotebookLM" corner watermark (#312), then applies ours. A failure at either
step **aborts the upload with a 500**: never ship an un-watermarked or NLM-branded
file. (Generated guide PDFs are the deliberate contrast — they are not
watermarked.)

## Edu Media Production (epics #316–#324)

A slides material becomes a narrated video in three separate admin steps, each
its own endpoint in `app/api/edu.py`, each resumable over a `positions` subset:
**render-slides** (#321) screenshots each slide's brand HTML with headless
chromium → `EduSlide.image_url`; **generate-audio** (#322) synthesizes each
`EduSlide.narration` to a Diego-voice MP3 → `audio_url` + `duration_ms`;
**assemble-video** (#323) muxes frame + audio per slide (hard cuts, ~0.5s hold,
identical encode params so the final concat runs `-c copy`) into one 1280x720
H.264 material. `assemble-video` **400s** unless every slide already has both
`image_url` and `audio_url`. Guides take the parallel path: **generate-pdf**
(#324) renders `content_blocks` through a chromium measuring pass and a greedy
packing pass into 816×1056 pages (legacy markdown-only guides keep the old
flowing renderer). Four properties are load-bearing:

- **Four of them run as background jobs, and what runs Playwright must stay
  synchronous.** Guide generation, render-slides, generate-audio and
  assemble-video validate in the request (404/400/409/422 still come back at
  once) and then return **202 + a `crawl_jobs` id** (#346 — a full deck's
  assemble-video is ~15-20 minutes and 504'd at the api. vhost's 300s ceiling);
  generate-slides, narration, translate and generate-pdf are still in-request.
  Playwright's **sync** API cannot run inside an asyncio event loop, so the job
  runner `run_edu_job` is a plain `def` — Starlette runs sync background tasks in
  the threadpool, where no loop is running — and
  `generate_edu_guide_pdf_endpoint`, the one in-request Playwright handler left
  here, is plain `def` for the same reason.
  Converting any of them to `async def` breaks rendering at runtime, not at
  import. The rule is codebase-wide, not edu-only: the Instagram post renderer
  (`ig_render.py`, whose `POST /admin/dashboard/instagram/posts/{id}/render`
  handler is likewise plain `def`) drives the same sync Playwright API.
- **Narration audio is cached and resumable**, keyed by `(material, position)` on
  disk, with a 24h TTL sweep of abandoned run dirs. A re-run skips slides already
  synthesized, so a mid-deck failure does not re-pay ElevenLabs — don't "simplify"
  the cache away. `duration_ms` is what times each frame in the video stage.
- **No automatic media matching in the edu path.** A slide's / block's
  `media_path` is an absolute CDN URL a human picked in the admin editor;
  `image_query` is only the picker's search hint and is never auto-resolved (an
  unpicked block renders a placeholder showing it). Automatic candidate scoring
  exists for *article* feature images (`media_scoring.py`), not here. Brand rules
  for guide PDFs: logo on the cover and closing pages only, quiet footer on
  interior pages, and guides are **not** watermarked.
- **Every rendered artifact overwrites in place at a deterministic Spaces key**
  — this is the *edu* convention, and blog article narration deliberately does
  the opposite (see "Article Narration Is Spanish-Only" above) — so regenerating
  one never changes its URL: `materials/guide/{research_id}/
  {band}_{lang}.pdf` (`guide_pdf_key`), `materials/video/{research_id}/{band}.mp4`,
  and the per-slide `materials/slides/frames/{material_id}/{NN}.png` /
  `.../audio/{material_id}/{NN}.mp3`. Two consequences the admin carries:
  a re-render leaves the browser serving its **cached** copy, so admin links
  append a per-load `?v=<timestamp>` (`frontend/src/utils/cacheBust.js`) while
  the *stored* URL is left untouched; and since a guide renders **one PDF per
  language** into two keys while the row's `file_url` remembers only the **last**
  render, `_guide_pdf_urls` (`app/api/edu.py`) HEAD-probes the other language
  (`ImageStorageService.object_exists` / `public_url`) and returns `pdf_urls
  {en, es}` on the detail response — degrading to `None` on storage trouble
  rather than failing the load. So a guide's `file_url` is not "the guide's
  PDF"; it is whichever language was rendered last.
  **`{band}` in those keys is a slug, not the canonical band id**:
  `guide_pdf_key` (`app/services/guide_pdf.py`) and the video upload
  (`app/services/video_assembly.py`) both write
  `(grade_band or "none").replace("-", "_")`, so `4th-8th` becomes `4th_8th` and
  a band-less row becomes the literal `none`. Constructing or HEAD-probing a key
  from the canonical id — which is what `app/core/excursions.py` and every band
  dict in `app/prompts/edu_material_generation.py` hold — misses the object.
  And these keys are load-bearing **outside** the app: the public
  academic-excursions page hardcodes two of them as sample deliverables,
  `materials/video/4/4th_8th.mp4` and `materials/guide/4/4th_8th_es.pdf` in
  `frontend/src/components/sections/academic/Methodology.jsx` (#521/#522, which
  also deleted the old `ResourcesSection.jsx`). That is exactly what the
  deterministic key buys — a re-render keeps the marketing link valid — but it
  also means deleting or re-keying that one material breaks a public page, with
  nothing on the backend side referencing it.

### Rendered Brand Assets Are Vendored, Not Read From the Design Skill
Every headless-chromium render builds **standalone** HTML first: `slide_html.py`
and `ig_html.py` are pure string assembly with no browser at all, fully
unit-testable, and only `slide_render.py` / `ig_render.py` / `guide_pdf.py`
launch chromium. What that HTML inlines comes from `backend/app/slide_assets/`
and `backend/app/ig_assets/` (the kit CSS, `guide.css`, the four `tokens/*.css`
files, logos, `heroicons.js` / `autofit.js`, the Caveat woff2, Diego's avatar) —
**copies** of the design skill's `ui_kits/`, checked into the backend package.
The prod image copies only `backend/` (`COPY backend/ .`), so `.claude/` is not
in it at all. Consequences:

- **Editing the skill changes no render.** A brand change has to be copied into
  the asset dirs, and the two have already diverged: `ig_assets/tokens/colors.css`
  carries the `--accent-*` social orange family, `slide_assets/tokens/colors.css`
  predates it. Diff before assuming they match.
- **Nothing is fetched at render time** — the kit's Google Fonts `<link>` is
  replaced by a data-URI `@font-face` — so rendering is deterministic and works
  offline. Reintroducing a network fetch makes renders flaky in the container.
- **Chromium does not execute inline `<head>` scripts** under Playwright's
  `set_content`, so the renderers re-inject the heroicon / autofit / igfit
  scripts with `add_script_tag`. Direct browser opens of the same HTML use the
  inline copy; dropping the re-injection breaks only the rendered output.

### Canonical excursion taxonomy (`app/core/excursions.py`, #319)
What every edu deliverable is keyed to, and the single source of truth
reconciling three vocabularies that do **not** transform into each other
mechanically:

- The public site's i18n / route key is underscored (`public_key`), the admin
  pipeline's `edu_suggestions.excursion_key` is hyphenated, and the map is spelled
  out explicitly because it is irregular — `casco-antiguo-plazas` →
  `casco_plazas`, *not* `casco_antiguo_plazas`. Derive one from the other and you
  will silently miss rows; go through `public_key_for` / `edu_keys_for_public`.
- Grade bands use canonical ids (`"3rd-6th"`), never the frontend display label
  (`"3rd - 6th Grade"`), and each `(excursion, band)` carries its own **focus**
  (#339) fed into the study-guide condense so bands differ by analytical lens, not
  just voice and length (`focus_for`).
- **Religious is two excursions** sharing one public page —
  `inter-religiosa-abrahamica` and `inter-religiosa-oriental` both map to
  `religious`, each owing its own video + study guide (operator decision), so
  `public_key` → excursion is one-to-many. **Museum-circuit deliverables are
  per museum, not per combination** (#523): `museo-del-canal` is its own
  excursion under the circuit's `casco_museums` page, alongside the circuit
  key `casco-antiguo-museos` (kept — its research is approved and fact-checked),
  and later museums follow the same pattern (`museo-de-historia` and
  `museo-de-la-mola` are the next two). Ten excursions × their bands =
  **24 owed deliverable pairs** (`owed_combos`), which is what the coverage report
  and the public delivery surface (#326) count against.

**A band id is a bare string with silent fallbacks, so adding or renaming one is
a coordinated change.** `edu_materials.grade_band` is `String(20)` and
`edu_suggestions.grade_bands` an `ARRAY(String)` — no enum, no `status_check`,
nothing validates a band id against the taxonomy. Every per-band lookup in
`app/prompts/edu_material_generation.py` is a `.get(band, <7th-8th default>)`
(`_voice_for_band`, `_review_ladder_for_band`, `_word_target_for_band`,
`_condensed_target_for_band`, plus `SLIDES_CONDENSED_TARGET.get(band, 3500)` at
its three call sites), so an unrecognised band **generates middle-school-voiced
content at middle-school lengths instead of raising** — and `public_edu` treats
it as an "extra" trailing alphabetically rather than dropping it. The id
therefore has to be spelled the same in six places: `_BAND_FOCUS` and the
`Excursion` tuple in `app/core/excursions.py`, the five band dicts in
`app/prompts/edu_material_generation.py` (`GRADE_BAND_VOICE`,
`GRADE_BAND_WORD_TARGET`, `GRADE_BAND_CONDENSED_TARGET`,
`SLIDES_CONDENSED_TARGET`, `GRADE_BAND_REVIEW_LADDER`), `allGradeBands` in
`frontend/src/components/admin/EditDrawer.jsx`, and the per-excursion locale
strings in both `translation.json` files. Existing rows are **not** covered by a
migration: the 2026-08-29 widening of Miraflores from `4th-6th` to `4th-8th`
(PR #484) touched all of the above and the prod rows were updated by hand.

### The Public Deliverables Surface Is No-Login (#326/#327)
`GET /public-edu/deliverables` (`app/api/public_edu.py`) is the teacher-facing
read of everything above, and — like `/public-media` since its educator gate
was removed — it is **self-serve, with no gate at all**: the router declares no
dependencies. What it surfaces is narrower than "published":

- Only `slides` and `guide` materials that are `APPROVED` **and** have
  `published_at` set and in the past, and only for an `excursion_key` that
  resolves in the canonical taxonomy — a material tied to an unknown excursion
  is silently dropped, not an error.
- **A deck is published; the video is inherited.** The operator publishes the
  *slides* material; the playable MP4 is looked up on a **sibling `video`
  material** keyed by `(research_id, grade_band)` with a non-null `file_url`, and
  a slides deck with no assembled sibling is omitted entirely (nothing to play).
  Publishing the video row itself is not how a video reaches the public page.
  The admin grid mirrors that: `AdminEduMaterials.jsx` filters `video` out of the
  list client-side (`HIDDEN_TYPES`), because the MP4 is reached, regenerated and
  published through its deck and its own status is ignored everywhere. A `video`
  row absent from the admin grid is deliberate, not a query bug — the row, the
  storage key and the `assemble-video` upsert all still exist.
- Thumbnails are derived, never stored: the deck's poster is the lowest-position
  slide's rendered `image_url`, the guide's is its cover block's human-picked
  `media_path`. Output order is canonical excursion order then canonical band
  order, with any unexpected band trailing alphabetically.

**The teacher-facing SPA that consumes it is `/materiales-educativos`, and it is
open to everyone — no section is gated.**
`EduMaterialsLayout` is a sibling of `AppShell` in `App.jsx` (#309) — its own
dashboard chrome and its own EN/ES switcher instead of the public
Navbar/Footer — with five nav entries, of which **three are
`ComingSoonSection` placeholders**: only `biblioteca` (`MediaLibrarySection` →
`PublicMediaLibrary`) and `presentaciones` (`PresentationsSection` →
`getPublicDeliverables`) render anything, while `infografias`,
`guias-de-audio` and `trivia-historica` are title-only stubs and any other
path redirects to `biblioteca`. Neither section is gated: `biblioteca` used to
wrap its body in an `<EducatorAccessGate>` email wall, removed together with
the backend `require_educator_access` dependency (a frontend-only removal would
just 401). `EducatorAuthProvider` is still mounted in `App.jsx` and still
re-validates any stored token on mount, but nothing consumes its context now.
So "fix the infographics page" is a page that does not exist yet.
