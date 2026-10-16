# Media Catalog and Article Feature Images

> Split out of the root `CLAUDE.md` to keep it under Claude Code's size
> limit; same rules, same authority. "Above" / "below" references may
> point to `CLAUDE.md` or to a sibling file in `docs/claude/`.

### The Media Catalog Is Crawl → Score → Approve
`media_candidates` is the crawl inbox, not the catalog: `wikimedia.py` walks
`PANAMA_SEED_CATEGORIES` on Commons and `loc.py` the Library of Congress, rows
land `PENDING` and are deduped by `UNIQUE (source, external_id)`.

- **The `SCORE` job scores the catalog, not a target.** `media_scoring.py` has
  Haiku rate each unscored `PENDING` row 0.0–1.0 for relevance to the *site's*
  subject matter (Panama history/geography/culture) — no article, excursion or
  query is part of the prompt — writing `relevance_score` + `relevance_notes`.
  Batches of 30 commit as they go, and a failed/malformed batch is rolled back
  and then **skipped by an `offset(skipped_batches * batch_size)`**, because its
  rows still match `relevance_score IS NULL` and would otherwise be re-selected
  forever.
- **Approving is what creates a `Media` row.** `POST
  /media-library/candidates/{id}/approve` downloads through
  `ImageStorageService` (the SSRF-pinned client, 20 MB cap, content type from
  the decoded bytes) to DO Spaces, generates a thumbnail, and copies the
  licence / attribution / source URL onto the new row. An upload failure is
  caught and the candidate is **still marked APPROVED with `media_id` NULL** for
  a later retry — so APPROVED is not proof an image exists.
- **`/public-media` is the educator-facing slice**, open to everyone (the #50
  educator gate was removed), and only APPROVED candidates with
  `relevance_score >= 0.7` whose `Media` is not some article's feature image.
  Its `_base_query` is an **INNER JOIN** `MediaCandidate.media_id == Media.id`,
  so a `Media` row with no candidate behind it — every `import-url` (`LOCAL`)
  image and every `GENERATED` feature image — can never appear there whatever
  its other attributes. The tag facet is the candidate's too: `?tag=` filters
  `MediaCandidate.tags` (a free-string `ARRAY(String(255))` on the crawl row)
  and `GET /public-media/tags` enumerates it with `distinct(unnest(...))`.
  That is **not** `Media.tags`, the shared `Tag`-table relationship via the
  `media_tags` association — which is mapped, backfillable
  (`scripts/backfill_media_tags.py`) and listed by **no endpoint at all**; its
  names (EN + ES) are, since #590, part of the media search document (a
  correlated subquery in `media_search_vector()`). `media_tags` had 0 rows in
  prod on 2026-10-06. Two tag vocabularies hang off one image; know which one a
  surface means.
- **`Media.search_terms` is the discoverability column, written at write time
  since #589.** It is a bilingual comma-separated keyword list (plus a
  generated English `alt_text`) produced by Haiku through
  `app/services/media_metadata.py` — the one home of the prompt — at three
  points: `import-url`, `PATCH /media-library/media/{id}` and candidate
  approve. Each commits the row **before** the call (Connection Pool rule) and
  generation is best-effort (`try_generate_search_metadata` never raises, and
  is skipped when `ANTHROPIC_API_KEY` is empty): on failure the column stays
  **NULL**, never `""`. `python -m scripts.backfill_search_terms` (same
  service, `feature="media_search_terms_backfill"`) fills rows that are NULL
  *or* the legacy `""` an older version wrote on failed batches; rows whose
  batch fails or that the model omits stay NULL and are skipped for the rest
  of that run, so the loop terminates and the next run retries them. A
  generated alt replaces only a *default* one (blank, or equal to the title) —
  never an operator-written alt. It feeds media search, but is no longer the
  only thing search runs on (next bullet).
- **Media search is one ranked helper, `app/services/media_search.py` (#590),**
  behind all three endpoints: the picker (`GET /media-library/media`), the
  library grid (`GET /media-library/candidates`) and `/public-media`. It runs
  on STORED generated `search_vector` tsvector columns on `media` and
  `media_candidates` (migration 0041, GIN-indexed; SQL single-sourced in
  `app/models/search_sql.py` and mirrored by hand in the migration — a test
  asserts they match). One `english` config over `public.pic_unaccent` (an
  IMMUTABLE wrapper, as is `public.pic_array_to_text`), so plurals fold and
  "Colón" = "colon". Semantics: **OR** across query words (a long picker
  `image_query` ranks instead of emptying), with words that are stopwords in
  *either* English or Spanish dropped (so `del` / `de` don't match every
  bilingual `search_terms` row), ordered by `ts_rank` — weights A title / B
  alt text, caption, search_terms, candidate tags + source categories,
  `Media.tags` names / C candidate description / D credit — then
  `relevance_score`. Only when full-text finds **nothing** does it fall back to
  trigram `word_similarity >= 0.6` per token (typos); no trigram index, by
  design — it runs only on a miss and the function form is not index-usable.
  The columns are declared on the `Table` (so `create_all` builds them and
  their GIN indexes) but excluded from the mapper (`exclude_properties`): there
  is **no ORM attribute**, and only `media_search.py` references them, via
  `Media.__table__.c.search_vector` / `MediaCandidate.__table__.c.search_vector`.
  `deferred=True` was tried first and is not enough — a deferred column still
  renders inside `select(Media).subquery()`, which is the count idiom of every
  no-search path, so the picker, the grid and `/public-media` would have 500ed
  on their default load before 0041 was applied. As it is, no entity SELECT,
  count subquery, INSERT … RETURNING or refresh names them, every non-search
  read and write keeps working between a deploy and `alembic upgrade head`
  (tested by hiding the columns), and only search 500s in that window. The app
  never writes them. Two performance/recall notes: `/media` and `/public-media`
  match against a *concatenated* vector (media ‖ candidate ‖ a correlated
  `media_tags` subquery), which cannot use `ix_media_search_vector` — a seq
  scan, fine at ~7.7k rows (`/candidates` matches its column directly and can
  use its index); and the Spanish-stopword filter also drops English words
  that are Spanish stopwords (`sea`, `era`, `son`), which then match only via
  the trigram fallback when they are the whole query. The test DB gets the extensions and functions from `conftest.py`
  (`test_engine`) before `create_all`, since `create_all` cannot create them.
  New trap: a NULL-`search_terms` row is still findable by title / alt /
  caption / credit / candidate description and tags, so "search finds nothing"
  now means nothing matched even fuzzily — not an unrun backfill. The backfill
  still improves Spanish recall.
- **Media keywords never become `Tag` rows.** `Tag` is the *article* tag
  vocabulary — `article_generation.py` feeds every row into the
  article-tagging prompt and the dashboard lists them all — so an editor's
  import keywords are merged (case-insensitive de-dupe, operator terms first)
  into `Media.search_terms` instead.
- **`POST /media-library/media/import-url`** is the picker's escape hatch — any
  http(s) image URL, re-hosted on Spaces with `source="LOCAL"`. Since #589 it
  also takes `title`, `caption` (UI "Description"), `attribution` ("Credit"),
  `source_page_url`, `license`, `license_url`, `keywords` and `alt_text`.
  `source_url` stores the **source page** when one is given (the image URL
  otherwise), so dedupe matches `source_url == url` **or** a `LOCAL` row whose
  `filename` starts with the URL's 12-char sha1 prefix (the Spaces key embeds
  it). A re-import returns the existing row unchanged — new metadata is not
  applied. When generation fails but the operator gave keywords, those are
  stored, so the row is not NULL and the backfill skips it; any later
  title / caption / credit edit regenerates it.
- **`PATCH /media-library/media/{id}`** (#589) is a true partial update
  (`exclude_unset`; an explicit `null` clears a field, but `title: null` is a
  400). Changing `title`, `caption` or `attribution` regenerates
  `search_terms` — the current terms are fed in as keywords, so operator terms
  carry forward — unless the body itself sets `search_terms`; a failure leaves
  them unchanged. The API accepts any media id, but the only UI is the library
  page's **Imported** tab (`ImportedMediaPanel` → `MediaEditModal`, which sends
  only changed fields, since an unchanged `search_terms` in the body would
  suppress regeneration); the page's "Add from link" button opens
  `MediaImportModal`. Crawled media stay edited through the candidate flow.
- **`GET /media-library/image-proxy` is the one media fetch that bypasses
  `http_client.py`**: a bare `httpx.Client(follow_redirects=True)` behind a
  static `ALLOWED_IMAGE_HOSTS` set checked pre-flight on the *requested* host
  only (admin-gated). Know it is there before concluding the hardened transport
  covers every outbound image fetch.

### Article Feature Images Live on a Docker Volume, Not the CDN
`_persist_feature_image` (`app/api/dashboard.py`) writes a timestamped
`article_{id}_{YYYYmmddHHMMSSffffff}.jpg` into `/app/static/images/articles` —
the `backend_static` named volume in both compose files — and stores a
**site-relative** `/static/images/articles/...` path in `Media.file_path` with
`source="GENERATED"`, served by the API's own `StaticFiles` mount. Its only
caller is `POST .../articles/{id}/generate-image` (#419): Sonnet writes the
watercolor prompt, Gemini paints it, and the admin grid's Generate / Regenerate
image action and the driver's `GENERATE_FEATURE_IMAGE` step both call it. The
manual prompt-copy + `upload-image` loop was removed in #580. Three
consequences:

- **Every regeneration is a new URL** (#580). nginx serves `/static/` with a
  one-day `max-age`, so the old fixed `article_{id}.{ext}` name kept the
  superseded image on screen for up to a day. The article's own previous
  generated row is reused (same `media_id`) and its old file deleted *after*
  the commit — best-effort, a failed delete only logs. A feature image assigned
  from the catalog (`assign-media`, a CDN URL) is never rewritten or deleted:
  generating then creates a fresh `Media` row. Snapshots of the old path (an
  Instagram promo's `media_path`) go stale; the IG renderer cannot load a
  relative `/static/` path anyway (`set_content` has no base URL).

- **`Media.file_path` has two shapes** — an absolute CDN URL for catalog, edu and
  imported media, a relative `/static/...` path for feature images. The frontend
  branches on `startsWith('http')` (`FeaturedArticles.jsx`); any new consumer has
  to as well.
- **The slot only ever holds a PIL-re-encoded JPEG.** `gemini_image.py`
  decodes whatever Gemini returns and re-saves it as JPEG q90, so there is no
  declared-content-type check left to trust: the user-upload path, which
  validated the declared type only, went with #580.
