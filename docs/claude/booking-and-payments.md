# Tour Pricing, Checkout, Payments and Customer Order Links

> Split out of the root `CLAUDE.md` to keep it under Claude Code's size
> limit; same rules, same authority. "Above" / "below" references may
> point to `CLAUDE.md` or to a sibling file in `docs/claude/`.

### Tour Pricing & Open-Group Tours
Two mutually exclusive pricing paths meet in `create_order` (`app/api/orders.py`),
and the switch is one nullable JSONB column, `Tour.fixed_price_config`:

- **NULL → private tour.** `PricingEngine.calculate_full_price`
  (`app/services/pricing.py`) computes guide service cost from the per-position
  hourly ladder, plus attraction tickets and transport; a group over
  `tour.max_group_size` comes back `requires_quote` and the endpoint 400s.
- **Set → open-group tour.** `calculate_fixed_price` charges flat per-person
  rates — no tickets, no transport, no quote ceiling.

**A private-tour total is assembled from three DB sources, and only one of them
is `pricing_config`.** `calculate_full_price` adds guide service (the
`pricing_config` ladder) to two figures that table knows nothing about:

- **Tickets come off the `attractions` rows** reached through `tour.attractions`
  — ten `ticket_*` columns per attraction, five age bands (senior / adult /
  child 10-17 / child 5-10 / infant) **× resident vs tourist**, because
  Panamanian residents pay a different gate price. So a wrong ticket total is an
  `attractions` edit, and the resident/tourist split is a real input the booking
  form collects (`total_residents` / `total_tourists` on the booking row), not a
  presentation detail.
- **Transport is two regimes, split at 4 pax.** 5-8 and 9-15 use the flat
  `transport_hourly_rate_*` keys × (`tour.duration_hours` + `transport_extra_hours`),
  round-trip only and distance-independent — that is the `pricing_config` half.
  **1-4 pax instead prices from `zone_transport_rates`**: one row per
  (`from_zone_id` = the hotel's zone → `to_zone_id` = `tour.zone_id`) carrying
  `rate_small_group` (≤3 pax) and `rate_large_group` (4 pax), doubled when
  `direction == "BOTH"`. The hotel→zone mapping is what `GET /zones/hotels/search`
  exists to resolve.

**There are two quote flags on a price breakdown and they behave oppositely.**
`requires_quote` is set only by the group-size ceiling and `create_order` **400s**
on it. `transport_quote_required` is set when the 1-4 pax band has no hotel zone
(hotel not in the DB) or no matching `zone_transport_rates` row — and that one is
**not** an error: transport is priced at **0**, the order is created, and the flag
is persisted onto `bookings.transport_quote_required` and projected into the admin
order detail for a human to quote by hand. So a missing zone pair silently ships a
$0-transport booking rather than failing, which is the opposite of the
`pricing_config` fallback shape (defaults, not zeros). `ZoneAttractionRate` /
`zone_attraction_rates` is modelled and indexed but **read by nothing** — same
"docstring ahead of the code" status as the `followup_*` columns.

Setting that column is **not** a price override: it flips three behaviours at
once — the pricing path, *and* the availability check is skipped, *and* the
booking is written with `allow_multiple_per_slot=True`. That is why migration
`0028` seeded a **new** `panama-viejo-507` row rather than adding a config to the
year-round `panama-viejo` tour, which would have silently converted it. (`0028`
is also the only pure data-seed migration: it skips cleanly on a schema-only DB
and raises rather than reporting success having inserted nothing — and since
deploys don't run migrations, a seeded tour does not exist in prod until a human
upgrades.)

`calculate_fixed_price`'s config keys are additive by design: `senior` (a flat
price) and `child_10_17` (its own price) are optional, and omitting them
reproduces the original behaviour exactly — seniors pay the adult price minus
`senior_discount_pct`, 10-17-year-olds pay the adult rate — so the older
`siete-iglesias` config still prices as before.

**Double-booking is resolved by Postgres, not by the availability check.**
`can_book_slot` is a check-then-act SELECT two concurrent orders can both pass;
the actual guarantee is a partial UNIQUE index on `(tour_date, time_slot)`
covering the four slot-holding statuses and only rows with
`allow_multiple_per_slot = false` (#40, migration `0011`; predicate built from
`_ACTIVE_SLOT_STATUSES` in `app/models/booking.py`). `create_order` catches the
`IntegrityError` and returns **409**. Making a new status hold a slot means
editing that tuple *and* shipping a migration to rebuild the index.

**The public tour catalogue is hand-written React, not the `tours` table.**
`GET /tours` and `GET /tours/{slug}` exist and are correct, and
`bookingService.getTours` / `getTour` wrap them (`frontend/src/services/
booking.js`) — but **neither wrapper has a single caller**. There is no
`/tours/:slug` route: every guided tour and academic excursion is its own
component under `frontend/src/pages/tours/` or `pages/excursions/` behind its
own hardcoded, Spanish-only route path in `App.jsx`
(`tours-guiados/panama-viejo-507`, `excursiones-academicas/miraflores`, …). Only
three booking endpoints are actually consumed by the site —
`getAvailability` (`DatePicker.jsx`), `calculatePrice` (`PriceBreakdown.jsx`)
and `searchHotels` (`HotelAutocomplete.jsx`) — plus the order endpoints. So
inserting a `tours` row publishes nothing, and renaming a tour in the DB does
not change a word on the page.

**The booking modal and the cart drawer are single global instances, mounted
once in `App.jsx`.** `BookingModalProvider` (nested inside `CartProvider`, which
owns the drawer's open state) renders exactly one `<BookingModal>` and one
`<CartDrawer>` beside its children and publishes `openBookingModal({tour,
editItem})` through context — so a tour page opens the shared modal rather than
rendering its own, and the drawer's own edit-from-cart action resolves against
the same provider. The modal is remounted on each open by bumping a `key`
instead of resetting state in an effect, so its form state can live in
`useState` initializers derived from props. Which pages take that path is the
same open-group split recorded above: the **six private-tour pages** call
`useBookingModal`, while `SieteIglesiasPage` and `PanamaViejo507Page` build a
cart item themselves and call `addItem` directly (consistent with their
bypassing the pricing and availability APIs), and the **six academic-excursion
pages have no booking path at all** — neither hook appears in
`pages/excursions/`, which is the contact-form funnel, not a checkout.

The open-group tours diverge further, in three places that must be edited
together:

- **Their pages bypass the pricing and availability APIs entirely.**
  `PanamaViejo507Page.jsx` hardcodes both the sellable dates/times (`SCHEDULE`)
  and the price list (`PRICES`), calling neither `calculatePrice` nor
  `getAvailability`. Its own comment records why that is safe rather than
  duplicated truth: `create_order` reprices every order from
  `tour.fixed_price_config` server-side, so the client numbers are **display
  only**. The page also collapses its three sale bands onto the API's finer age
  vocabulary (`child5to17` → `child_10_17`), which is exactly why both child
  bands are priced identically in the config.
- **`POST /bookings/calculate-price` never consults `fixed_price_config`.** It
  calls `calculate_full_price` unconditionally, so quoting an open-group tour
  through the public price endpoint returns *private-tour* pricing. The
  `is_fixed_price` branch exists only in `create_order` — which is the other
  half of why a bespoke page was written instead of reusing the booking modal.
- **The open-group list is duplicated client-side** as a literal slug set,
  `openGroupTours = new Set(['siete-iglesias', 'panama-viejo-507'])` in
  `CartProvider.jsx`, so the cart stops such an item from blocking a slot. It
  mirrors `allow_multiple_per_slot` and nothing keeps the two in sync: a new
  open-group tour needs the DB config, this set, and a page + route.

**Availability is one global AM/PM calendar, not a per-tour one.** No tour id
appears anywhere in it: `get_blocking_reason` and `get_availability_range`
(`app/services/availability.py`) take only `(tour_date, time_slot)`, and the
partial UNIQUE index above is on those two columns alone. So a private booking on
*any* tour blocks that AM or PM slot for *every* other tour — the calendar models
the one guide, not the catalogue. `GET /tours/{slug}/availability` only validates
that the slug exists and is active, then returns that same global range, so its
answer does not depend on the tour in the path. This is also why the
`fixed_price_config` bullet above has to set `allow_multiple_per_slot=True`:
without it one open-group booking would close the slot for the whole day.
Watch the shape as well as the semantics — `get_availability_range` is a
day-by-day Python loop calling `is_slot_available` twice per day, and each call
issues one query and then a second when the first misses, so the endpoint's
**default 90-day window is up to ~360 round-trips** on one pinned pool
connection, and its 365-day ceiling up to ~1460. Widening that range, or adding a
caller that sweeps many dates, is a pool-budget decision (see the Connection Pool
section), not a free read.

**A slot hold expires in the availability query but not in the index.**
`get_blocking_reason` (`app/services/availability.py`) treats a
`PENDING_PAYMENT` / `PAYMENT_LINK_SENT` booking as blocking only while its
order's `reservation_expires_at` is in the future (or NULL — NULL blocks
forever); the partial UNIQUE index predicate is **status-only**, with no expiry
term. Nothing sweeps lapsed holds: `reservation_expires_at` is written once at
order creation and only ever *read* thereafter, and the sole background loop in
the lifespan is the Instagram scheduler. So once a hold lapses the slot reads as
available and `create_order` still **409**s on insert until a human moves that
booking out of an active status. Adding a sweep, or an expiry term to the index,
is a schema/behaviour decision — not a tidy-up.

Time-boxed homepage promos expire in code: `HeroSection.jsx` gates on a
`PROMO_ENDS_AFTER` constant compared against the **local** calendar date (not UTC,
which would end a Panama promo at 7pm the previous evening), because the 2026
Holy Week takeover ran two months past the event before someone removed it by
hand (PR #158).

### Checkout Is a Client-Side Cart, and the Client Cannot Send a Price
The cart has **no server representation at all**: `CartProvider.jsx` keeps
`{items, createdAt}` in React state and mirrors it to `localStorage` under
`pic_cart` on every change, with no expiry and no API call — nothing holds a
slot until `POST /orders`, which `Checkout.jsx` fires once and then
`clearCart()`s. Four properties follow:

- **An order *is* the whole cart.** `CreateOrderRequest` is `{customer,
  payment_method, bookings}` with `min_length=1` on the list, so one order, one
  PayPal invoice and one `VIEW_ORDER` magic link cover N bookings across
  different tours and dates. Each gets its own reference from
  `_generate_reference` — `ORD-XXXXX` for the order, `PIC-XXXXX` per booking, 5
  chars from an alphabet with look-alikes removed — so "reference" is ambiguous
  in this codebase: every customer and admin order route keys on the *order*
  reference, and `POST /admin/bookings/{booking_reference}/reschedule` is the
  one route keyed on a *booking's*.
- **`CreateOrderRequest` has no money field.** `create_order` reloads each tour
  by slug and re-runs `calculate_fixed_price` / `calculate_full_price` itself,
  summing `subtotal` and `discount_total` server-side. So every number the
  client shows — `PriceBreakdown`, the cart total, `PanamaViejo507Page.jsx`'s
  hardcoded `PRICES` — is **display only**, and a mismatch is a display bug,
  never a mispriced order. That is the general rule the open-group page's
  comment states for its own case.
- **A slot collision *within one cart* is caught only client-side.**
  `can_book_slot` runs per item against the DB and knows nothing about the other
  items in the same request, so two private-tour items for the same
  `(tour_date, time_slot)` both pass and the second INSERT trips
  `uq_active_booking_slot` at the single commit — the whole order then 409s with
  "just booked by someone else", which is misleading in that case. The only
  thing preventing it is `getBookedSlots` in `CartProvider` (consumed by
  `BookingModal.jsx`), which exempts the `openGroupTours` slugs.
- **The order survives a failed confirmation email.** `notify_new_order` is
  wrapped in a `try/except EmailDeliveryError` that logs and returns the
  response anyway — the admin-side-absorbs half of the notification policy —
  with a `TODO(#37 layer 2)` for persisting `Order.notification_failed_at`, so
  today a stranded customer is visible only in Sentry and the logs.

### Payments: One Paid Transition, Two Callers (#32/#33/#34)

**PayPal is the only *integrated* method, not the only method offered.**
`PaymentMethod` (`app/models/enums.py`) has three values — `PAYPAL`, `CUANTO`
(credit card via the Cuanto App) and `YAPPY` (Banco General) — `orders.payment_method`
is a `String(20)` + CHECK carrying whichever the customer picked, and
`Checkout.jsx` offers all three as radios with **`credit_card` (→ `CUANTO`)
selected by default**. Only the PayPal branch is automated: every invoice
endpoint 400s with "Order payment method is not PayPal"
(`booking_admin.py`), the reschedule handler's invoice re-issue is guarded on
`payment_method == "PAYPAL"`, and the webhook below can only ever fire for a
PayPal order. A CUANTO/YAPPY order therefore sits `PENDING` until an admin
sends a link out of band and calls `POST .../orders/{reference}/mark-payment-sent`
(which only stamps `payment_link_sent_at` + `PAYMENT_LINK_SENT` and logs the
link in the status-log note), then `mark-paid`. Two asymmetries in the admin UI
follow: mark-payment-sent is exposed **only** in the `AdminCalendar` drawer and
**only** when `payment_method === 'CUANTO'`, so a YAPPY order has no
payment-link-sent affordance anywhere, while "Mark Paid Manually" is
method-agnostic in both drawers. So "payments" in this codebase is one
automated path plus a manual one, and the manual one is the default.

`POST /webhooks/paypal` is the only inbound webhook. It verifies the PayPal
signature before touching anything (401 on failure) and routes on `event_type`
(`INVOICING.INVOICE.PAID` → paid, `INVOICING.INVOICE.CANCELLED` → expired;
anything else is ignored, not an error). That handler is **`async def` on
purpose** — it awaits `request.body()`; the earlier sync version spawned a fresh
event loop on a threadpool worker to do that, which is undefined behaviour (#33).
Its `_handle_*` helpers stay sync because they only do DB work. (This is the
opposite of the Playwright endpoints below, which must stay sync — decide per
handler what it actually awaits.)

`apply_paid_transition` (`app/services/orders.py`) is the **single home** for
"mark order paid", shared by the webhook and the admin
`POST /admin/bookings/orders/{reference}/mark-paid`. Its contract:

- **The caller must already hold the row lock.** Both call sites load the order
  with `.with_for_update(of=Order)` — `of=Order` specifically, because the
  `joinedload(Order.bookings)` makes a LEFT OUTER JOIN and Postgres rejects
  `FOR UPDATE` on the nullable side of one. The lock is what serializes a
  concurrent webhook against an admin clicking mark-paid.
- **It is idempotent**: already-PAID returns `False` without mutating or
  committing, so the caller fires `notify_payment_received` only on a real
  transition.
- **Redelivery has two layers**: a cheap `paypal_event_id` lookup short-circuits
  the common retry, and the UNIQUE `booking_status_log.paypal_event_id`
  (migration `0008`) is the race backstop. A new payment-side transition belongs
  in this module with the same shape, not inline in a handler.

Both callers **catch `EmailDeliveryError` around the payment-received
notification** — the money transition is already committed, so a mail failure
must not fail the webhook (and hand PayPal a retry) or the admin action. That is
consistent with the transactional-email policy below, not an exception to it: the
service still propagates, the payment call sites choose to absorb.

**Outbound PayPal writes have no compensating action, so drift is recorded
rather than undone (#41).** The invoice lifecycle lives in
`app/api/booking_admin.py` — `POST /admin/bookings/orders/{reference}/create-invoice`
(draft, 400s if one already exists), `.../send-invoice` (emails the customer,
moves the order to `PAYMENT_LINK_SENT` and stamps `payment_link_expires_at`),
`.../cancel-invoice` — and every one of them mutates remote PayPal state
**before** the local write. There is no way to un-ring that bell, so the module
never plain-`db.commit()`s after a PayPal call: `_commit_or_capture_orphan`
rolls back on `SQLAlchemyError`, calls `_capture_paypal_orphan` (Sentry tag
`paypal_orphan`, context carrying the order/invoice ids and a `side_effect`
string naming what already happened remotely — e.g. "customer was emailed the
invoice; DB still shows status=PENDING"), and 500s pointing at manual
reconciliation. A new endpoint that mutates PayPal belongs behind that helper,
and a `paypal_orphan` Sentry event is a reconciliation task, not a bug at the
throw site. The whole router is admin-gated at declaration
(`APIRouter(prefix="/admin/bookings", dependencies=[Depends(validate_admin_token)])`),
so the per-endpoint `magic_link` params are only there to fill
`booking_status_log.changed_by`.

### Customer Order Links Are Their Own Token Gate
The manage-my-booking surface — `GET /orders/{reference}`, `POST .../cancel`,
`POST .../renew-payment` — is authenticated by a per-order `VIEW_ORDER` magic
link sent in the `Authorization: Bearer` header (same `?token=` removal, epic
#263). It is validated by `_validate_magic_link` in `app/api/orders.py` — its
**own** validator, not one of the `app/api/dependencies.py` gates — and it is
stricter than `auth.py`'s `verify_token`: it rejects **any** link whose `used_at` is set,
whatever the action, and every handler additionally 403s when
`magic_link.order_id` is not that order. The link is minted in `create_order`
with a lifetime tracking `reservation_validity_days` (default 7, not the old
fixed 30 — #43), and the raw token is returned **only** by `create_order`
(`OrderResponse.magic_link_token`) so the confirmation page can fetch the order
without the token ever entering a URL (#36).

**That "never in a URL" holds for the confirmation page, not for the emailed
link — which uses the same fragment trick as the admin one.**
`OrderResponse.magic_link_token` is populated on create only and never on a GET,
and the confirmation page receives it via router state; but
`notifications.py` builds the manage-booking link as
`{frontend_url}/booking/manage?reference=<ref>#token=<token>`, so the reference
rides in the query and the credential rides in the **fragment** browsers never
send to the server — the identical no-access-log / no-Referer reasoning as
`{frontend_url}/admin#token=…`. `BookingManage.jsx` reads
`window.location.hash` first and falls back to `?token=` only for magic links
already in flight before #43. So the fragment is the codebase-wide convention
for putting a token in a link, on both the admin and customer sides; a new
emailed link belongs in the fragment, and the `Authorization: Bearer` rule above
governs the API call the page then makes, not the link itself.

- **Cancel is one-shot.** It accepts only `PENDING` / `PAYMENT_LINK_SENT`,
  cancels every booking in the order, logs `changed_by="customer"`, and sets
  `used_at` — so the same link no longer opens the order afterwards.
- **Renew-payment is a manual follow-up, not a re-issue.** Nothing mints a new
  payment link: the endpoint clears `payment_link_expires_at`, best-effort
  cancels the live PayPal invoice (failures are logged + Sentry-captured, never
  blocking), leaves the magic link valid so the customer keeps access, and its
  real effect is the admin notification — fired **once**, guarded by the
  most-recent status-log note (`_RENEWAL_REQUEST_NOTE`, #38) so a repeat request
  is acknowledged without re-notifying. It refuses outright once
  `reservation_expires_at` has passed, which is the same expiry the availability
  query honours and the slot index does not.
