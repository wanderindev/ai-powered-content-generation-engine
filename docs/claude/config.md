# Configuration & Startup

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

### Configuration & Startup (`app/core/config.py`, `app/main.py`)
One pydantic-settings `Settings` class read from `.env`, exposed through an
`@lru_cache`d `get_settings()`. Two conventions in it are load-bearing:

- **`class Config: extra = "ignore"` is deliberate.** Pydantic's default
  (`extra="forbid"`) turned a single new `ELEVENLABS_*` key in the prod `.env`
  into a backend crash-loop on startup, so unknown env vars must stay ignorable —
  that is what makes it safe to add an env key ahead of the code that reads it.
  Don't "tighten" it back.
- **Empty credentials disable a feature, they don't crash it.** Instagram
  publishing without `instagram_access_token` / `instagram_account_id` answers
  **503** on publish-now and the scheduler leaves due rows *queued* (not FAILED)
  until credentials land — and credentials alone are not enough: the
  `instagram_publishing_enabled` kill switch defaults **off** (#576, publish-now
  409s, nothing is claimed; see `docs/claude/instagram.md`); Gemini feature-image generation without `gemini_api_key`
  and translation without `deepl_api` likewise 503; an empty `VOYAGE_API_KEY`
  makes the Voyage seam raise `EmbeddingsDisabledError` (callers degrade or 503,
  and the knowledge indexer behind every article write skips quietly — conftest
  forces it empty so no test embeds for real); an empty `agent_driver_token`
  turns driver auth off. Follow that shape for a new integration.

The one deliberate exception is **PayPal, which fails closed at boot**:
`_check_paypal_config` raises at startup when `environment == "production"` and
`paypal_client_id` is set but `paypal_webhook_id` is not, because
`verify_webhook_signature` would otherwise silently 401 every real webhook (#32).
Misconfiguration that would be *invisible* at runtime belongs in that check.

**Eval-gated feature flags default off.** Ask PIC's
`ask_pic_query_rewrite_enabled` (#696, env `ASK_PIC_QUERY_REWRITE_ENABLED`)
adds a Haiku call before retrieval; it stays `False` until the RAG eval shows
a gain (`docs/design/rag-knowledge-layer.md` §6). Turning it on raises the
admission worst case and every query's ledger cost, so it is a measured
decision, not a toggle to flip for "better answers".

**`Settings` is not the only configuration surface — the booking side has a
second one in the database.** `pricing_config` (`app/models/pricing_config.py`)
is a `key`/`value` **string** table holding the whole private-tour price list
(`base_fee`, the six `hourly_rate_pax_*` ladder steps, `senior_discount_percent`,
`transport_hourly_rate_5_8` / `_9_15`, `transport_extra_hours`) *and* the booking
lifecycle timings (`payment_link_validity_hours`, `reservation_validity_days`).
Nothing about it is in `.env`, so "change the payment-link window" or "change a
guide rate" is a DB edit, not a redeploy. It is not the *only* booking price
source, though — attraction tickets and the small-group transport matrix live in
their own tables with none of the fallback behaviour below (see "Tour Pricing &
Open-Group Tours"). Three properties to design around:

- **Every read carries its own hardcoded fallback.** `PricingEngine._load_config`
  slurps the table into a dict at construction and each rate is
  `get_config(key, Decimal(...))`; the endpoints use a local
  `_get_config_value(db, key, default)` (declared separately in
  `app/api/orders.py` and `app/api/booking_admin.py`). A missing row is therefore
  *silent* — an empty `pricing_config` prices at the defaults rather than
  failing, so "the numbers look wrong" can mean "the table is empty", and
  changing a default means changing it at every call site.
- **Only the price keys are admin-editable.** `GET`/`PUT
  /admin/dashboard/settings/pricing` filters both directions through
  `PRICING_KEYS` (`app/api/dashboard.py`), which lists the 11 pricing keys and
  **not** the timing ones — `payment_link_validity_hours` and
  `reservation_validity_days` are reachable only by direct SQL.
- **The model's docstring is ahead of the code.** It also documents
  `followup_1_hours` / `_2_` / `_3_`, and `orders` carries the matching
  `followup_1_sent_at` … `_3_` columns, but nothing reads those keys and nothing writes
  those columns (they are only projected into the admin order detail, and
  `NotificationService.request_followup_email` has no caller) — consistent with
  there being no
  booking-side background loop.
