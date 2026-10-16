# Blog Articles — Visibility, Publishing, Slugs, Generation, Narration, Grammarly

> Split out of the root `CLAUDE.md` to keep it under Claude Code's size
> limit; same rules, same authority. "Above" / "below" references may
> point to `CLAUDE.md` or to a sibling file in `docs/claude/`.

### Blog Visibility Is Two Filters and One Copied Constant
"Public content" for blog articles is not just `status == "APPROVED"` — it is that
**plus** a `published_at` date gate **plus** the taxonomy-5 exclusion, and today
only some queries apply all three. Know which before adding or copying one:

- `GET /articles` is the full shape: `status == "APPROVED"`,
  `published_at IS NOT NULL`, `published_at <= now()`, `Taxonomy.id != 5`
  (`app/api/articles.py`). `GET /articles/latest-series` gates on `published_at IS
  NOT NULL` but **not** on `<= now()`.
- `/search`, `/articles/{id}`, `/articles/by-slug/{slug}`, the series listing and
  every count in `app/api/categories.py` filter on `APPROVED` **only** — so an
  approved-but-unpublished (or future-dated) article is reachable there and sorts
  first under `published_at.desc().nullsfirst()`. Match the endpoint you are
  extending; don't assume the list endpoint's rule is enforced globally.
- `GET /articles/{id}/related` (#629) applies the **full** rule to the source
  *and* the results (through `public_article_criteria()` in knowledge
  retrieval). It 404s any non-public id and excludes the article's series
  family. It is served from stored chunk vectors only, plus a 10-minute
  per-worker cache and a 10-minute `Cache-Control`, which stack: an unpublished
  article can stay listed (and an unpublished source keep answering 200) for up
  to ~20 minutes (#657). It is registered **after** `/by-slug/{slug}` so it
  cannot capture `by-slug/related`. Design: `docs/design/rag-knowledge-layer.md`.
- **The series block inside an article detail is filtered less than that, and
  asymmetrically.** `_build_article_detail` builds `series_info.articles` — and
  the `prev_article` / `next_article` nav derived from the same list — down two
  branches that do not agree: viewing a series **member** queries the siblings
  with `status == "APPROVED"` but prepends `article.series_parent` *unfiltered*;
  viewing a series **parent** reads `article.series_children` straight off the
  relationship with **no filter at all**. Neither branch looks at `published_at`.
  So a DRAFT or unpublished sibling is listed — id, title, excerpt, slug — in the
  public response for an approved article. Filter in the serializer, not just at
  the lookup, when adding to this shape.
- **`GET /articles` sorts by historical chronology, not recency.** The list
  endpoint orders `Article.category_id` → `Research.suggestion_id` →
  `Article.research_id` → `series_parent_id.nullsfirst()` → `series_order`,
  reached through a single `outerjoin(Research)`: `categories.id` follows
  Panama's era order and, within a category, the suggestion then research id
  follow the order topics were commissioned, so the blog reads as a history
  rather than a changelog. `Article.category_id` is the denormalized copy of the
  suggestion's category (verified identical across all 63 prod articles), which
  is what keeps this to one join. `published_at.desc().nullsfirst()` is still
  the ordering on `/search`, and `/articles/latest-series` still orders
  `published_at.desc()` — recency is now the *exception*, so don't copy it into
  a new blog listing by habit. **That ordering is now load-bearing on the
  frontend as well**: `ArticleListItem` carries `suggestion_id` /
  `suggestion_title` (localized, batch-loaded research → suggestion in one extra
  query) and the blog page groups **consecutive** items by `suggestion_id`
  (`groupBySuggestion` in `BlogList.jsx`), so groups are contiguous only
  *because* the list arrives sorted this way. A re-sort at either end silently
  shatters them — which is why the page's old client-side id re-sort was
  deleted. The one client-side reorder that survives is the blog's
  **series-order toggle**: `BlogList.jsx` reverses whole *groups* after
  `groupBySuggestion` has already run (`newestFirst`, default off, so the API's
  oldest-first chronology is still what a visitor lands on), which flips the
  series without touching the grouping pass or `series_order` within a group.
  Reorder after grouping, never before.
- **`EXCLUDED_TAXONOMY_ID = 5` is one shared constant, but applying it is still
  per-module** — since #626 it lives in `app/models/category.py` (a guard in
  `tests/test_knowledge_retrieval.py` fails on a second definition) and
  `articles.py`, `search.py`, `categories.py` and knowledge retrieval import it.
  A new public router gets no exclusion by inheritance; it has to filter on it.

**Nothing on the public site searches articles today, so `/search` is a live
endpoint with no live caller.** Three separate surfaces are half-wired and none
of them meet: the `/search` router is **English-only** — no `lang` param, it
`ILIKE`s `title` / `content` / `excerpt` (never the `_es` columns) and returns
the English `title` / `slug` / `excerpt`, so it is also the one article
serializer outside the ES→EN fallback described under Article Slugs; `GET
/articles` *does* carry a bilingual `search=` param matching all six EN+ES
columns and inheriting the list endpoint's three filters and chronological
order, but `BlogList.jsx` calls `getArticles(i18n.language, 100)` and never
passes it; and the only search input in the tree, `BlogFilters.jsx`, is an
**unimported component** whose box has no state, no handler and a hardcoded
English placeholder. So "improve blog search relevance" done in `search.py`
changes nothing a visitor can see — wiring the existing `search=` param through
`BlogList` is the shorter path, and the docstring's promise of "PostgreSQL
full-text search when available" is aspirational (the code is `ILIKE` only).

**The blog is unpaginated in practice too, and the same way.** `list_articles`
declares `page: int = Query(1, ge=1)` and `limit: int = Query(20, ge=1, le=100)`
but its `response_model` is a bare `list[ArticleListItem]` — **no total, no page
count** — and `getArticles(language, limit, search)`
(`frontend/src/services/articles.js`) never sends `page` at all, so `BlogList`'s
`getArticles(i18n.language, 100)` is one un-paged request at the `le=100`
ceiling. Past 100 approved articles (63 in prod today) the tail is silently
dropped from the blog with no error and no "next page" affordance. The matching
UI stub is `components/sections/blog/Pagination.jsx`, a second **unimported**
component in the same directory as `BlogFilters.jsx` — hardcoded "Showing 1 to 3
of 12 results", `href="#"` links, English-only. So "add pagination to the blog"
means adding a count to the response shape as well as wiring a component; it is
not reachable by importing the one that is already there.

**The admin editorial calendar applies none of these filters.** `GET
/admin/dashboard/editorial-calendar?year&month` (`app/api/dashboard.py`) is the
one surface that puts blog articles and Instagram posts on a single timeline
(`AdminEditorialCalendar.jsx`, routed at `/admin/editorial-calendar`): articles
are selected by `published_at` falling in the month with **no status filter at
all** — `is_live` (`status == "APPROVED"` *and* `published_at <= now`) is
computed onto the response rather than applied to the query — and Instagram
posts by `func.coalesce(published_at, scheduled_at)`, so a scheduled-but-
unpublished post paints on its future date. Admin-only, so that is deliberate;
don't copy the query into a public listing.

Publication is passive read-time filtering on `published_at` (see the Instagram
scheduler below for the one thing that actually *acts* at a future time).

### Publishing a Blog Article Is a Write-Side Gate
`PATCH /admin/dashboard/articles/{id}/publish` (`app/api/dashboard.py`) is the
only route that sets `published_at`, and it **400s** unless four preconditions
already hold: `status == "APPROVED"`, non-empty `content_es`, a
`feature_image_id`, and — for any article in a series — the `ABOUT_MARKER_EN`
header that `generate_sections_for_series` writes
(`app/services/series_sections.py`, where the marker strings double as the
service's own idempotency signal). The driver's `SET_PUBLISH_DATE` kind calls
this endpoint, so a missing translation or feature image fails that task rather
than shipping a half-finished article. It calls it **without** `published_at`
(#558): an omitted date means the server picks the slot — the article's own
existing date, else a series sibling's, else the next free 12:00 UTC day, one
series per day (`app/services/publish_slot.py`, detail in
`docs/claude/agent-driver.md`). The admin UI always sends a date and that path
is unchanged. A changed date also moves the article's derived, still-scheduled
Instagram promo with it (#588, see `docs/claude/instagram.md`). `PATCH
.../unpublish` clears `published_at` (and unschedules the article's derived,
still-scheduled Instagram promo, #588); it leaves `status` alone.

**The rules live in `app/services/article_publishing.py` since #642**, not in
the routes: `publish_article` (with `publish_blockers`, the four
preconditions in check order), `unpublish_article`, and `transition_status`
/ `approve_article` (the `VALID_TRANSITIONS` table behind `PATCH
/{entity}/{id}/status`). None of them commits, and they raise domain errors
the routes map to the same 404 / 400 messages. Their other caller is the MCP
approval executor (`app/services/mcp_approvals.py`): an admin approving an
agent's publish / unpublish / approve request runs the same function, inside
a savepoint, so a precondition that fails at decision time records the
request `FAILED` and leaves the article untouched. Change a rule there, not
in a route.

**Blog articles are fact-checked but not fact-check-gated.** `ARTICLE` is a
grounding subject (see the Fact-Check Pipeline below), yet `enforce_material_gate`
is wired only into the edu endpoints — an `INACCURATE` article claim does not
block this publish. Don't assume the edu gate covers the blog.

### Article Slugs Are Persisted; Category Slugs Are Still Computed
`Article.slug` / `slug_es` became real columns in #474 (`String(255)`, each with
its own UNIQUE constraint, migration `0029_article_slugs.py`), joining
`Tour.slug` (`String(100)`, unique). A **category's** slug remains a call-site
derivation of its name via `generate_slug` (`app/utils/slug.py`, accent-folding +
`ñ`→`n` so a Spanish name slugs cleanly). Consequences a new endpoint has to
design around:

- **Article slugs match in SQL; category slugs still do not.**
  `/articles/by-slug/{slug}` is one indexed statement — `or_(Article.slug ==
  slug, Article.slug_es == slug)` filtered to `APPROVED`, with Postgres
  BitmapOr-ing the two unique-constraint btrees. `/categories/{category_slug}`
  still resolves by pulling rows and comparing computed slugs in Python, and the
  `category` / `taxonomy` query params on the list endpoint are `ILIKE %name%`
  matches, not slug lookups.
- **Any surviving Python slug scan must stay a cheap projection** — ids and names
  only, resolve the id, then load the one row. Materializing every approved
  article as a full entity pulls both ~2000-word bodies per row, multiplied by the
  cartesian product of any collection `joinedload` (`tags` × `series_children`),
  and — because a `Session` pins its connection until close — holds one of the 5
  per-worker pool slots for the whole transfer. A burst of article views on that
  shape drained the pool and surfaced as `QueuePool limit ...` in
  `validate_admin_token` (Sentry `PIC-PYTHON-FASTAPI-1W`, PR #472). Persisting the
  columns removed that shape from `by-slug`; it did not remove the rule.
- **`app/services/article_slugs.py` owns the only edit-time write path.**
  `sync_article_slugs` recomputes from the titles while the article is not live and
  **freezes** once `status == APPROVED` **or** `published_at` is set — so a title
  edit can no longer silently move a live URL, and the slug survives an
  unpublish/republish. It **raises `SlugCollisionError` instead of
  suffix-deduplicating** (no `-2`): the admin callers in `dashboard.py` /
  `admin.py` turn that into a **400**, while `grammar_check.py` deliberately lets
  it propagate. Insert-time column defaults derive both slugs from the titles, so
  a new row is never slug-less.
- **The ES→EN fallback moved to the response layer**, not the lookup — every
  serializer picks `slug_es if use_spanish and slug_es else slug`, while the
  lookup matches either column.
- **The blog is the only language-prefixed part of the route table, and the
  article path differs by language.** `App.jsx` declares `en/blog` + `es/blog` +
  bare `blog`, and `en/articles/:slug` + `es/articulos/:slug` + bare
  `articles/:slug` — every other public route is a single hardcoded Spanish
  path with the language chosen client-side by `i18next-browser-languagedetector`.
  `Blog.jsx` / `Article.jsx` sync the two directions themselves: the URL prefix
  drives `i18n.changeLanguage` on mount, a language switch rewrites the path
  (`navigate(..., {replace: true})`), and `Article.jsx` re-fetches and then
  redirects to `buildArticlePath(lang, data.slug)` — so `/en/articles/<es-slug>`
  self-corrects to the canonical path, because the lookup matches either column
  and the response carries the language's slug. A bare, unprefixed URL is never
  rewritten. The prefixed shape is what internal links build (`BlogList.jsx`,
  `FeaturedArticles.jsx`, `Navbar.jsx`'s `langPrefix`) and it has to be repeated
  in `AppShell.jsx`'s `CONTENT_PREFIXES` for the analytics `ViewContent` event,
  so adding a localized route means touching those too. Note the link builders'
  "with language prefix for SEO" comment is aspirational in the same way the
  `/search` docstring is — see "The Public Site Has No Meta / SEO Layer".

## Blog Article Pipeline (`app/services/article_generation.py`)

Suggestions (`suggestion_generation.py`, Sonnet — its `max_tokens` is *computed*
from `num_suggestions` over a `THINKING_RESERVE` because a fixed cap truncated
the JSON near the top of the allowed range, #101; behind the off-by-default
`SUGGESTION_DEDUP_ENABLED`, `suggestion_dedup.py` also steers the prompt with
the nearest existing article titles and flags each new suggestion as a
*possible duplicate* of an article or older suggestion — advisory only, any
Voyage failure degrades to the unchanged path; flag off is byte-for-byte the
old prompt, #630, see `docs/design/rag-knowledge-layer.md`) → a human-run deep-research
document uploaded and validated (`research.py` / `research_common.py`) → a
**3-phase** generation pipeline: outline, write, tag. Prompt builders live in
`app/prompts/`, not inline in the services.

**The condense step is deliberately asymmetric — the easiest thing here to
"simplify" wrong.** Research longer than `CONDENSE_THRESHOLD` (2500 words) is
condensed before the *content* prompt (#102), and that condensation is keyed to
the specific article's outline (broad context for the topic plus detailed
retention of the facts that outline's sections need), so a series' sibling-article
facts are not discarded. **Phase-1 outlining is never condensed**: the
series-split decision needs the complete fact set, and condensing first both
degrades that decision and suppresses the >4000-word split heuristic in
`build_outline_prompt`.

Model tiers per phase are evidence-backed the same way the LLM section describes:
condense, outline and content run on **Opus** (`OPUS_MODEL`, Opus 5.5 since
2026-09-29; the A/Bs below were measured on Opus 5) with `output_config={"effort":
"high"}`; excerpt and AI summary run on **Haiku**; tagging runs on **Sonnet**
with `output_config={"effort": "low"}` and `max_tokens=2000` — upgraded from
Haiku because tagging is a judgment call (what is central, what generalizes,
which person-name is canonical) and Haiku produced both the 25-tag sprawl below
and a vocabulary where 200 of 291 tags matched no article. Same evidence-backed
discipline as the grammar check's Sonnet-not-Haiku decision, in the other
direction. Every Opus `max_tokens` carries a measured comment explaining its
number — condense at
16,000 because it feeds the *full* research and thinking scales with input,
content at 12,000 because the prompt is small and the task is bounded prose
against a fixed outline. Tagging assigns existing `Tag` rows and files
unrecognised ones as `PENDING` for admin review rather than inventing tags.

**The tag cap is enforced in code, not just asked for in the prompt.**
`MAX_ARTICLE_TAGS` (5, `article_generation.py`) bounds the tags an article ends
up with *counting the ones already attached*, and `generate_tags` ranks
candidates existing-before-new then shortest-first and drops any whose word set
contains — or is contained by — a kept tag's. That collapses overlapping
variants of one subject to their shortest form ("Portobelo" beats "Portobelo
Fair" and "Portobelo Fair Trade Monopoly"). The prompt asks for the same limit
and at most 2 new tags, but a prompt instruction is not a guarantee: article 228
collected 25 tags, mostly one-off `PENDING` variants, before the code-side limit
existed. Same discipline as the corrections and grammar-check parsers — the
service constrains what the model returns rather than trusting it.

**Article bodies carry no bold or italics, and that is enforced the same way
(#540).** `build_article_prompt` forbids `**` / `*` / `__` / `_` (titles of
works and foreign terms included), and `generate_article_content` runs its output
through `strip_inline_emphasis` (`app/utils/markdown.py`) anyway. The reason is
the Grammarly flow: Grammarly drops a run whenever it rewords the text inside it,
and the meaning check compares stored markdown, so a lost marker read as a
changed paragraph and sound suggestions were reverted or filed for review. The
helper removes only *paired* runs and leaves list markers, `* * *` rules, code,
`\*` escapes, URLs and `snake_case` alone. Headings (`##`) and the
deterministic series sections (`**About this Article**`, `**Continuar
leyendo**`, which narration's `_CTA_HEADER` matches) keep their markup, because
the prompt rule covers generated prose only. Articles generated before the rule
were cleaned with `scripts/one_off/strip_article_emphasis.py`, which also removes
the *stray* markers that earlier proofread passes left mid-word.

**The tagging call is also the only writer of `Tag.name_es`.** Nothing
downstream translates tags: `app/api/admin.py` carries bulk DeepL endpoints for
taxonomies (`/translate/taxonomies/all`) and categories
(`/translate/categories/all`) but **none for tags**, the admin's manual
`POST /admin/dashboard/tags` accepts only a `name`, and every public serializer
falls back (`t.name_es if use_spanish and t.name_es else t.name`, three call
sites in `articles.py`) — so an untagged-in-Spanish row ships English on the
Spanish site, which is how the 2026-09 series shipped 11 of them once the
pipeline went unattended. `generate_tags` therefore asks for
`{"name", "name_es"}` per suggestion and stores both; a bare string is still
tolerated and leaves `name_es` NULL. Sonnet with the article in context is the
deliberate choice over DeepL here, for the same reason the edu pipeline
overrides DeepL's cover titles — short proper nouns ("Philip II" → "Felipe II",
"Henry Morgan" unchanged).

Upload caps live in `research_common.py` (#89): **5 MB** for a research document
(markdown text) and **75 MB** for an edu material binary, both read at
`max_bytes + 1` so an oversized file is rejected without being fully buffered —
413 on oversize, 400 on non-UTF-8.

**Upload validation is three checks, and one of them edits the document.**
`validate_research_document` (`research_common.py`, shared by the blog and edu
uploads) runs: a **4000-word hard floor** that returns immediately — note it
reports the two unrun checks as `True`, so a short document's `checks` payload
is not evidence they passed; a deterministic regex for a
`Sources|References|Bibliography|Works Cited|Citations` heading; and a Haiku
sub-topic-coverage pass over only the **first 15,000 chars** that passes on
`subtopics_found >= 2` regardless of the total (no sub-topics on the suggestion
skips it as passing, and a malformed LLM response degrades to 0 found, i.e.
fails — conservative in the same direction as the fact-check tiers). When the
references heading is missing, `_generate_references_section` asks Haiku to
compile one from the inline citations and **appends it after a `---`**; the
result dict's `content` is that rewritten document and `upload_research` stores
it, so what lands in `research.content` is not always the bytes uploaded.
Re-uploading over APPROVED research is a **409** unless `force=true` (#88,
because generated articles would be orphaned), and any accepted re-upload resets
`status` to `PENDING`, identical file or not. Since #641 the route's write path
is `research_intake.submit_research`, shared with the MCP `submit_research`
tool; the route calls it with `replace=True, converge_identical=False`, which
keeps exactly that behaviour. The MCP tool differs on purpose: an identical
body returns the existing row unchanged (`replayed`, any status), a different
one needs `replace=true`, and APPROVED research is never overwritten (see
`mcp.md`).

### Article Narration Is Spanish-Only, and Its Storage Key Is Timestamped
`app/services/article_audio.py` narrates a published article in Diego's voice —
the **second** consumer of the ElevenLabs seam, alongside the edu slide pipeline
— behind `POST /admin/dashboard/articles/{id}/generate-audio`, storing the MP3
URL in `articles.audio_url_es` (`String(500)`, migration `0030`) and the
per-segment manifest in `articles.audio_manifest_es` (JSONB, migration `0031`).
Eight properties, each a deliberate departure from a rule stated elsewhere in
this file:

- **The Spaces key is timestamped, not deterministic.**
  `store_article_audio` writes `articles/audio/{article_id}/es_{epoch}.mp3` and
  then best-effort deletes the superseded object, because overwriting a fixed
  key would leave browsers and the Spaces CDN serving the stale narration. That
  is the *opposite* of the edu rule below ("every rendered artifact overwrites
  in place at a deterministic Spaces key"), which buys the same safety with a
  per-load `?v=` on admin links instead. Both are live; pick per artifact, and
  know which one the surface you are extending uses.
- **Generation is a `crawl_jobs` background job (`ARTICLE_NARRATION`, #547).**
  A large article is ~130 sequential TTS calls plus post-processing, past the
  `api.` vhost's 300s ceiling, so `generate-audio` returns **202 + a job id**
  and the drawer polls `GET /media-library/jobs/{id}` (via `runBackgroundJob`
  in `services/admin.js`), showing `Segment i/N` from `current_unit`. What can
  be refused without I/O still is, synchronously: 404, no Spanish content, and
  the `"changed"`-with-no-edits 400 (`plan_narration`, the pure planning half
  of `synthesize_article`). One job per article: the same mode joins the
  running job, a different mode is a 409. The runner
  (`app/services/article_narration_job.py`) commits per segment with
  `expire_on_commit=False`, so no connection is pinned across TTS / ffmpeg,
  and re-reads the article with `populate_existing=True` before writing — a
  plain `get()` would return the cached row even after a delete. On success
  `job.stats` is the old `ArticleAudioResponse` body.
- **Regeneration is per segment, and the segment key is a content hash (#494).**
  Every synthesized segment MP3 is *also* stored at
  `articles/audio/{article_id}/segments/{sha256[:16]}.mp3`, and the ordered
  `{hash, key, is_heading, duration_ms}` list is persisted on
  `audio_manifest_es`. `synthesize_article(..., mode="changed")` downloads and
  reuses every segment whose text hash is still in the manifest and pays
  ElevenLabs only for the rest, then re-concatenates and re-uploads the whole
  read; `mode="all"` (the default, and what a body-less POST gets) resynthesizes
  everything. Because matching is by *content*, splitting or merging paragraphs
  only re-pays for the text that actually changed. So this surface carries
  **both** key conventions at once — a timestamped key for the finished read, a
  deterministic content-hash key for the parts, which is stale-proof precisely
  because the key moves when the text does. Three behaviours to preserve: a
  missing or undownloadable stored segment falls back to synthesis rather than
  failing, segment objects absent from the new manifest are best-effort deleted
  as orphans, and a `"changed"` run with nothing changed raises `ValueError` →
  400 rather than re-paying. Pre-manifest audio has no manifest, so it
  regenerates fully once and that backfills it.
  `GET /admin/dashboard/articles/{id}/audio-segments` diffs the saved Spanish
  text against the manifest so the drawer can show what a `"changed"` run would
  cost — and it reads the **saved** article, which is why the drawer blocks on
  unsaved editor changes. The drawer calls it on **every** generate click and
  goes straight to a paid `"all"` run only when it reports `has_audio` and
  `has_manifest` both false (#594); otherwise it shows the Only changed /
  Whole article chooser. Don't restore a shortcut keyed on the drawer's local
  `audioUrlEs` — a stale bundle once wiped it from a 202 job response and
  started an unasked 51-segment re-narration (article 246, job 300). The manifest is read at *public* read time too:
  `manifest_total_duration_ms` sums the segment durations plus the same concat
  silences (so it has to keep mirroring the assembly loop) and
  `_build_article_detail` returns it as `ArticleDetail.audio_duration_ms`, which
  is `None` for a pre-manifest narration — the player then reports the length
  itself once metadata loads.
- **One generation per paragraph, stitched.** `segment_article` splits on blank
  lines and each segment is synthesized separately with `previous_text` /
  `next_text` context, because a long single generation starts energetic and
  flattens into a monotone. **Headings are the exception: they get
  `previous_text` but no `next_text`**, because feeding the section opener as
  next-context made the voice read the heading with an unfinished, leaning-
  forward inflection even with a terminal period. Pauses are **real silence at
  concat** (1.0s between paragraphs, 1.4s before a heading), not `<break>`
  tags. Voice settings are
  measured (`stability=0.40`, `style=0.30`, `speed=0.9`), and `eleven_v3` was
  A/B-tested and **rejected** — better acting, but too loose a reproduction of
  the cloned voice to pass as Diego. Evidence-backed like the model tiers above;
  changing it is not a default.
- **What the voice reads is not byte-for-byte the article.** `segment_article`
  speaks section headings as `Parte N: <heading>.` (a bare heading dropped
  after a paragraph confused listeners), leaving `Introducción:` /
  `Conclusión:` headings unnumbered, and applies a narration-only respelling
  map (`_PRONUNCIATION`: `Ngäbe` → `Nobe`, the Panamanian pronunciation) —
  the stored text is untouched. Because the segment hash is taken from the
  *spoken* text, changing either rule re-flags every affected segment for a
  `"changed"` run; that is the intended lever, not drift. It also ends the
  read at a `**Continuar leyendo**` header with no `---` before it (how 36
  older series members carry the CTA).
- **Every segment is post-processed before concat, and the stored segments
  stay raw (#547).** `app/services/narration_postprocess.py` trims tail blips
  (the next segment's first word, let in by `next_text`; in 13 % of raw
  segments, all 75 articles), normalizes each clip to -20 LUFS with a limiter
  only when the gain would clip, re-paces paragraphs of 8+ words outside
  105–150 wpm (factor clamped to 0.80–1.15), and pads a uniform 350 ms tail.
  It runs on a 2-worker pool overlapping the TTS calls, and `assemble` streams
  WAVs through ffmpeg's concat demuxer. Never build the read in memory with
  pydub again: that OOM-killed article 69 (132 segments) on the 4 GB droplet.
  Consequences: the manifest's `duration_ms` is the **processed** length (what
  the player's displayed duration needs), every `"changed"` run re-processes
  reused segments from the raw originals, and the orchestration tests mock
  `process_segment` / `assemble` while the DSP tests are ffmpeg-gated.
  `alimiter` must be passed `level=0`: its default auto-level scales the
  output back up to 0 dBFS. The 2026-09-05 back-catalogue rebuild has that
  (+~1 dB on limited segments, -0.4 dB peaks) and was left as is.
  `scripts/one_off/postprocess_article_audio.py` now imports this module and
  exists only for a deliberate back-catalogue rerun after a tuning change.
  Slide decks are deliberately **not** processed: no stitching, half the
  loudness spread, default voice speed (measured on #547).
- **Every run pitch-checks the raw clips and re-takes high ones (#601).**
  `synthesize_article` measures each non-heading raw clip's median F0
  (`app/services/narration_pitch.py`, a port of `f0_frames` from
  `scripts/one_off/measure_narration_pitch.py`, kept bit-for-bit — including
  the *biased* autocorrelation normalisation — because the thresholds were
  calibrated with it in #600; switching to NCCF means re-measuring first;
  the frames are FFT-ed in 1,000-frame chunks since #607, which is
  bit-identical and caps the working set at ~46 MB instead of ~415 MB per 90 s).
  Clips under 3 s voiced are skipped. The baseline is the median of the
  article's measurable segments, reused ones included; only segments
  synthesized in *this* run are suspects (`> +15 %`, upward only — the
  closest "not Diego" take was +16.9 %, so don't raise it). Under 10 %
  suspects each one is re-requested up to twice with identical text, context
  and settings; the first in-threshold take wins, else the lowest-pitch take
  stays and is reported unresolved. At 10 %+ nothing is retried. The result
  is overwritten on `articles.audio_qa_es` (JSONB, migration `0042`) every
  run, summarized in the job's `stats.voice_check`, shown in the drawer
  (timestamps seek the player) and as a "N to listen" grid pill. Positions
  there are **1-based**, title included — the `audio-segments` chooser's
  numbering, not the 0-based manifest index the #600 notes use. Two things
  that look like bugs and aren't: an accepted re-take is post-processed a
  *second* time (the first take's WAV was already made on the pool while the
  rest of the article was still synthesizing — keeping that overlap is worth
  one extra ffmpeg pass per re-take), and a pitch measurement that fails to
  decode counts as skipped, never fails the paid run. Swapping an accepted
  take in is equally best-effort (#608, `_apply_retake`): it is
  post-processed into its **own** `NNNN.retake.wav` first and uploaded over
  the hash key only if that succeeded, so a failed ffmpeg pass or Spaces
  upload leaves the original raw object, WAV and manifest duration in
  agreement (the invariant a later `mode="changed"` run relies on); the
  failure is logged + Sentry-captured, and the paragraph is reported
  unresolved at its original pitch with a `reason` key on both its
  `retried` and `unresolved` entries. Pitch is necessary, not
  sufficient: 14 of 23 high clips the operator heard were Diego, so the UI
  says "listen to these", and a speaker-verification model was measured and
  rejected (no better, ~1 GB of PyTorch).
- **Spanish only, on both surfaces.** Narration is generated from `content_es`
  (400 when it is empty), the admin control lives on the drawer's ES tab, and
  `_build_article_detail` populates the public `ArticleDetail.audio_url` only
  when `use_spanish` — so there is no English narration to "fix" as missing. The
  list surface follows the same rule: `ArticleListItem.has_audio` (the blog
  card's listen badge) is true only when `lang=es` *and* `audio_url_es` is set.
  `synthesize` in `audio_tts.py` passes `voice_settings` / `previous_text` /
  `next_text` **only when set**, because the SDK distinguishes omitted from an
  explicit null.

### Grammarly Proofread Flow Is Body-Only and Server-Assembled
The manual Grammarly pass (the one the disabled LLM grammar stage never
replaced) is driven by the PIC Companion extension against Grammarly's *web
editor*, through two dashboard endpoints — and the contract is that the client
never sees markdown and never holds the series sections:

- `GET /admin/dashboard/articles/{id}/grammar-source?language=` returns the
  article **body** as HTML (`markdown_to_html`) with the About / Continue Reading
  blocks stripped by `split_series_sections` (`app/services/series_sections.py` —
  bilingual, tolerant of `* * *` rules and `***` bold, and the only splitter that
  guarantees `about + body + cont == content`; `extract_series_header` in
  `utils/markdown.py` is the older EN-only heuristic), plus a SHA-256
  `fingerprint` of the full column and the body's paragraph `block_count`.
- `POST .../grammar-apply?language=` re-splits the *current* row, converts with
  `html_to_markdown`, and refuses on fingerprint mismatch (**409**, concurrent
  edit) or a paragraph-count change (**400** with a diff preview — a lossy
  read-back or a merged paragraph). `dry_run` writes nothing; an **empty
  `accepted` list stamps `grammar_checked_<lang>_at` without rewriting the
  content**, so a clean article never churns through the normalising round
  trip; otherwise the body is written between the untouched sections and one
  `GrammarCheckFix` row per accepted suggestion is recorded with
  `source=GRAMMARLY` (`GrammarFixSource`, migration `0032`; the LLM pass writes
  `LLM`, and the #557 Spanish accuracy check `ES_ACCURACY` — migration `0039`;
  see `docs/claude/agent-driver.md`). The extension's dry run posting the source HTML back verbatim must
  come back `changed=false` — that is the acceptance test for its DOM
  serializer, and `tests/test_grammar_apply.py` pins the same round trip.
- **Writing the body and stamping it checked are separable, and the stamp is
  what the queue reads.** `GrammarApplyRequest` carries `stamp: bool = True`
  plus an advisory `score: int | None` (`app/schemas/grammar_check.py`).
  `stamp=False` still writes the corrected body, the `GrammarCheckFix` rows and
  the `GrammarReviewDiff` rows, but leaves `grammar_checked_<lang>_at` unset —
  so the article keeps its corrections *and* stays queued for the next grammar
  run. The extension sends it when Grammarly's final overall score is still at
  or below the operator's bar (96 or less as of 2026-09-22); `score` is that
  figure and is recorded in the log line only, never persisted. The response
  reports `stamped`, and `checked_at` is `None` whenever the stamp was skipped.
  Note the empty-`accepted` shortcut above is a *stamp-only* write, so
  `stamp=False` with an empty `accepted` list writes nothing at all.
- **The queue is `GET /admin/dashboard/articles`, and readiness is server-side
  (#556).** The extension lists every article and queues `has_content` /
  `has_spanish` with no stamp, `APPROVED` only unless its `grammarIncludeDrafts`
  setting is on; it does **not** read the flags below yet (pic-extension#38,
  open). Pipeline-driven articles are drafts until `APPROVE_ARTICLE`, so by
  default it doesn't offer them during the waits at all. `ArticleListItem` now also
  carries `ready_for_grammar_en` / `_es`: for an article the agent driver is
  running (it has a `WAIT_GRAMMAR_CHECKED_<LANG>` task) it is true only while
  that wait is `READY` / `WAITING`, so that (once the extension reads it) Grammarly never polishes text a pending
  fact-check correction (or, for ES, the translation) would rewrite; with no
  wait task it is the old content-and-no-stamp rule. A stamp always makes it
  false. Computed from one grouped `agent_tasks` query (`_grammar_wait_states`
  in `dashboard.py`), not per row. The stamps are also what the driver's
  grammar waits read (`docs/claude/agent-driver.md`).
