# Instagram — Generation, Scheduler, Publishing

> Split out of the root `CLAUDE.md` to keep it under Claude Code's size
> limit; same rules, same authority. "Above" / "below" references may
> point to `CLAUDE.md` or to a sibling file in `docs/claude/`.

### Instagram Post Generation (`app/services/instagram_generation.py`)
What produces the rows the renderer and scheduler below consume. Two post kinds
(`InstagramPostType`), both writing **PENDING** drafts for human review — the
pipeline never publishes what it generated:

- **`SABIAS_QUE`** — 3 "¿Sabías que…?" fact cards from an approved blog
  `Research`, on Sonnet, condensed first past the same 2500-word
  `CONDENSE_THRESHOLD` the edu pipeline uses. The batch alternates
  `template_variant` (AZUL / CLARO) so a generated grid isn't repetitive.
- **`ARTICLE_PROMO`** — one "Nuevo en el blog" announcement. Template data
  (title, kicker, meta line, article count) is derived from the DB by
  `series_promo_fields`; **the LLM writes only the caption**. Only a series
  *parent* or a standalone article is a valid target — a series member 400s with
  a pointer to its parent — and the article must be APPROVED.

Three properties mirror rules stated elsewhere in this file, deliberately:

- **The parser never invents content.** Blocks split on `---`, a block yields a
  post only if it has a non-empty `FACT`, malformed blocks are *dropped* and a
  short batch is the human's cue to regenerate (same discipline as the
  fact-check corrections and grammar-check parsers). Zero parsed posts is a 502.
- **`sanitize_fact` (`app/services/ig_html.py`) is the third sanitization
  boundary**, alongside `markdown_to_html` and `image_storage`'s decoded-bytes
  content type. A fact body is reduced to plain text plus `<b>…</b>` emphasis:
  entities are unescaped, everything is HTML-escaped, then *only* the escaped
  `<b>`/`</b>` markers are restored — so no link, attribute or script can reach
  the rendered card. It is idempotent, and applied on every write.
- **No automatic media matching**, exactly as in the edu path: the polaroid
  photo is a human pick (`media_id` / `media_path`) and `image_query` is only
  the picker's search hint.

### Instagram Scheduler (`app/services/instagram_scheduler.py`)

The only thing in this codebase that *acts* at a future time — blog/edu
"scheduling" is passive read-time filtering on `published_at`. An asyncio loop
started from the FastAPI lifespan (gated by `INSTAGRAM_SCHEDULER_ENABLED`, and
never started when `environment == "test"`) ticks every
`instagram_scheduler_interval_seconds` and publishes due posts. Its concurrency
contract is deliberate and multi-worker / deploy-overlap safe: a tick
claims **one row at a time** with `FOR UPDATE SKIP LOCKED` and commits
`publishing_started_at` before publishing, the network call happens outside any
row lock, and a stale-claim sweep parks rows whose worker died mid-publish.
**FAILED rows (`publish_error` set) are never re-picked** — retry is an operator
action, so there are no retry storms by construction; don't add automatic retry.
Known residual: a crash between Instagram accepting a post and our commit parks a
live post as FAILED, reconciled manually.

### Publishing Is a Two-Step Container Dance Against a Public URL
`app/services/instagram.py` holds the Graph API side: `InstagramPublisher` plus
`publish_post`, shared by the scheduler above and `POST
/admin/dashboard/instagram/posts/{id}/publish-now` (which makes the network
call **in-request**, on a sync handler). Four properties shape what can be
built on it:

- **The container expires after 24h, which is why containers are created at
  publish time and never ahead of schedule.** Publishing is Meta's two-step
  dance — `POST {account_id}/media` with a publicly reachable `image_url` plus
  the caption, poll the returned container's `status_code` until `FINISHED`
  (`_CONTAINER_POLL_INTERVAL_SECONDS` 2s, `_CONTAINER_POLL_TIMEOUT_SECONDS`
  30s; `ERROR` fails loud), then `POST {account_id}/media_publish`. Pre-warming
  containers for scheduled posts is not an optimization available here.
- **Its input is a public JPEG URL, which is what the renderer exists to
  produce.** `render_instagram_post` (`app/services/ig_render.py`) screenshots
  the fixed 1080×1350 canvas, converts PNG→JPEG (the Graph API's `image_url`
  accepts JPEG only) and uploads public-read to a **timestamped** key
  `instagram/{post_id}/render-{ts}.jpg`, best-effort deleting the previous
  artifact. So the IG renderer follows the *article-narration* key convention,
  not the edu deterministic-key one — two timestamped surfaces against one
  deterministic one. Pick per artifact; neither rule is global.
- **The permalink fetch is best-effort.** It happens after `media_publish`, so
  the post is already live: a failure logs a warning and returns
  `permalink=None` rather than failing the publish — the same "the remote side
  effect already happened" discipline as the PayPal orphan capture.
- **`publish_post` never raises**; it parks `publish_error`, increments
  `publish_attempts`, clears `publishing_started_at` and returns a bool. Since
  the scheduler never re-picks a FAILED row, the operator's retry path is
  **re-scheduling or publish-now, each of which clears `publish_error` first**
  (unschedule clears it too). Both endpoints also 400 unless the post is
  APPROVED and already rendered — the precondition the planner's IG chain order
  encodes.

All Graph HTTP goes through `make_hardened_client` + `run_with_retry`, and
missing `instagram_access_token` / `instagram_account_id` leaves `enabled`
False, so publish-now 503s and the scheduler skips — the empty-credentials
shape described under Configuration & Startup.

### Publishing Is Off Until the Operator Says Otherwise (#576)
A digital-marketing agency runs @panamaincontextbydiego's posting strategy, so
**credentials alone must never post**. `instagram_publishing_enabled`
(`INSTAGRAM_PUBLISHING_ENABLED`) defaults to **`False`** and is a second gate
on top of the credentials, exposed as `InstagramPublisher.publishing_enabled`:

- **publish-now answers 409** (configured but deliberately off — not 503, which
  still means "no credentials"), before any Graph request;
- **the scheduler claims nothing**: due rows stay SCHEDULED, never FAILED,
  exactly like the empty-credentials case. The loop itself keeps running
  (`instagram_scheduler_enabled` still defaults to `True`) because it also
  hosts the token check below;
- **`publish_image` itself raises `InstagramPublishingDisabledError`** before
  the container call — defence in depth, so a future caller that forgets the
  check still cannot post;
- generate / render / approve / schedule are untouched, so drafts and the
  editorial calendar stay usable. `GET .../instagram/publishing-status`
  (`{configured, publishing_enabled}`) is what the admin grid reads to show a
  "Publishing off" pill and disable the publish-now button.

Keep it separate from `weekly_plan_ig_posts_enabled` (planner-only: whether the
driver *plans* IG posts — see `agent-driver.md`). Flipping the switch on is an
operator decision, never an agent's.

**`POST /admin/dashboard/instagram/verify` is the never-posting dry run**
(`verify_instagram` in `app/services/instagram.py`), and it works with the
switch off — that is its point. Optional body `{"post_id": N}`. Steps, each
reported with `ok` / `detail` / `data` and independent of the others:
`account` (`GET {account_id}?fields=username` must be
`EXPECTED_USERNAME = "panamaincontextbydiego"`), `token` (`GET /debug_token`
with the token inspecting itself: `is_valid`, `instagram_content_publish` in
`scopes`, `expires_at` — 0 means never), and with a rendered post `container`
(create the media container for its JPEG and poll it to `FINISHED`). It **never
calls `media_publish`**; the unused container expires after 24h, which is the
property of the two-step dance that makes a real-credential dry run possible.
The endpoint closes its DB session before the Graph round-trips (pool rule) and
is **not** on `DRIVER_ALLOWED_CALLS` — neither is publish-now — so the driver
token can reach neither.

**The token never rides in a URL it does not have to.** Every Graph call sends
it as `Authorization: Bearer`, because Sentry's httpx integration turns each
request's query string into a breadcrumb attached to later events. The one
exception is `debug_token`, whose `input_token` must be a query parameter;
`scrub_breadcrumb` (`app/core/observability.py`) redacts it. Don't move the
token back into `params` / `data`. Exceptions are the other vector:
`InstagramPublisher._request` catches **every** failure (not just
`httpx.HTTPError` — also the SSRF transport's `SSRFError`, a `ValueError`, and a
non-JSON body) and raises a fresh `InstagramPublishError` outside the `except`
block, with a message built from the exception type or Meta's error payload
(never `str(e)`, which can carry the URL), so nothing token-bearing is chained;
`scrub_pii_event` also redacts frame locals as a second line. The daily check
and every verify step catch `Exception`, so neither can escape to the
scheduler loop's `capture_exception` or a 500.

**`debug_token` may be refused.** It needs an app token or a token belonging
to one of the app's developers / admins. When it fails, verify's token step and
the daily check fall back to `GET me/permissions`: that proves the token works
and whether `instagram_content_publish` is granted, but not the expiry, so the
step passes on scope with "expiry unknown" and the daily check only logs (a
Sentry warning goes out only if both calls fail).

**Token expiry is watched, not refreshed.** Long-lived tokens last ~60 days and
nothing renews them. `warn_if_token_expiring` Sentry-warns (`capture_message`,
level warning, a fixed message so repeats group) when the token is invalid or
has fewer than `instagram_token_warn_days` (default 7) left. It runs from
verify and from the scheduler tick **once a day per worker**
(`TOKEN_CHECK_EVERY`, process-local `_last_token_check_at`), whenever
credentials exist, switch on or off; a failed check is logged + warned and
still counts as that day's attempt.

**Article promos derive their time from the publish date (#576).** `PATCH
.../posts/{id}/schedule` takes `scheduled_at` as optional: omitted or null, an
`ARTICLE_PROMO` is scheduled for the day after its article's `published_at`
at 17:00 UTC (`PROMO_DAYS_AFTER_PUBLISH`, `PROMO_PUBLISH_TIME_UTC` and
`promo_time_for`, in `app/services/instagram_scheduler.py` since #588), and the
call **409s while the article has no publish date**; any other post type 400s
without a time. An explicit time still wins (manual scheduling is unchanged).
A past time is accepted and means "due at the next tick" — with the switch off,
just queued. Sabías-qué posts keep explicit, planned times: their cards come
from the research and never point at the article.
The time follows later re-dates (#588): re-dating the article (`PATCH
.../articles/{id}/publish` with a different date) moves a promo whose
`scheduled_at` still equals the time derived from the old date and that is still
SCHEDULED (`follow_article_publish_date`, called by the publish and unpublish
endpoints before their commit). Unpublishing the article unschedules that promo,
and it is **not** re-scheduled on a later re-publish — the operator re-schedules
it. Explicitly timed, FAILED, claimed and published promos never move. There is
no "was derived" column, so an explicit time that happens to equal the derived
one is treated as derived and follows too.
