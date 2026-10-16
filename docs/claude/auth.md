# Admin & Educator Authentication

> Split out of the root `CLAUDE.md` to keep it under Claude Code's size
> limit; same rules, same authority. "Above" / "below" references may
> point to `CLAUDE.md` or to a sibling file in `docs/claude/`.

### Admin & Educator Authentication
Both gates are **`Authorization: Bearer <token>` header only** and live in
`app/api/dependencies.py`:

- **Admin** (`validate_admin_token`, #54) resolves a `magic_links` row *joined
  against `admin_emails`* and requires an **active allowlist entry** (#56) — a
  valid, unexpired magic link whose email is missing or inactive still 401s. The
  transitional `?token=` query fallback was removed once every client sent the
  header (epic #263), to keep credentials out of access logs, Referer headers,
  browser history and Sentry; a new client or script that appends `?token=` gets
  a 401. The `dev-token` bypass requires **both** `settings.debug` and
  `settings.environment == "development"`, and emits a WARN log plus a Sentry
  breadcrumb on every use so it can never fire silently.
- **Educator — removed.** `require_educator_access` (#50) used to gate
  `/public-media` on the opaque token from `/educators/access` / `/signup`; it
  was deleted when the educational-materials section was opened to everyone, so
  **no route validates an educator token any more**. `educator_service`'s token
  issuance (`validate_token`, the 7-day `ACCESS_DURATION_DAYS` window) and the
  `/educators/*` endpoints survive, but only `/educators/session` and
  `/educators/unsubscribe` still read a token.

The *issuance* half lives in `app/api/auth.py` and has its own load-bearing
choices (#57):

- `POST /auth/admin/request-access` mints `secrets.token_urlsafe(32)` with a
  **7-day** expiry (`ADMIN_LINK_VALIDITY`, raised from 24h on 2026-10-04), only for an **active** `admin_emails` row, and returns the same
  "if this email is registered…" message either way so the endpoint is not an
  account-enumeration oracle.
- **The token is delivered in the URL *fragment*** — `{frontend_url}/admin#token=…`,
  read by the SPA from `window.location.hash`. Browsers never send a fragment to
  the server, so it stays out of access logs and Referer headers. This is the
  same reason the `?token=` query fallback was removed; building the link with
  `?token=` re-opens it.
- `POST /auth/admin/logout` **deletes the `magic_links` row**. Admin tokens are
  reusable for their whole 7-day window, so a client-side-only logout leaves a
  replayable token in browser history; server-side revocation is what makes
  logout immediate. The `dev-token` bypass resolves to a synthetic in-memory link
  with no DB row and is skipped.
- **Single-use is per action, not universal.** `verify_token` rejects an already
  used link only for `CANCEL_BOOKING` / `RENEW_PAYMENT`; `ADMIN_ACCESS` and
  `VIEW_ORDER` stay valid until they expire.

**Email case is normalized per schema, not globally — and the admin allowlist is
the one gap that fails silently.** `normalize_email` (`app/schemas/_common.py`,
strip + lowercase, mounted as a `field_validator`) exists because `EmailStr`
validates syntax without normalizing case, and it is wired into exactly four
fields: both educator schemas and both subscribe ones — i.e. everything that
writes the `educators` `UNIQUE(email)` row. `contact`, `orders` and **both admin
email schemas** (`AdminLoginRequest`, `AdminEmailCreateRequest`) take bare
`EmailStr`, so they store and compare whatever case was typed. That matters at
the gate because the two halves disagree: `POST
/admin/dashboard/settings/admin-emails` de-dupes **case-insensitively**
(`func.lower(AdminEmail.email) == body.email.lower()`) but stores the address
verbatim, while `POST /auth/admin/request-access` looks it up
**case-sensitively** (`AdminEmail.email == request.email`, `app/api/auth.py`).
An allowlist row seeded as `Diego@Example.com` therefore never matches a
lowercase request — and because that endpoint deliberately answers "if this
email is registered…" either way (the no-enumeration rule above), the failure is
indistinguishable from success and no link is sent. Check the stored case before
debugging a magic link that never arrives.

**The allowlist is the DB table only — `ADMIN_EMAILS` in the environment is dead
config, and it fails the same invisible way.** `Settings` declares
`admin_emails: str = ""` under a "comma-separated" comment
(`app/core/config.py`) and `.env.example` ships `ADMIN_EMAILS=admin@example.com`,
but **nothing reads that field**: no dependency, no endpoint, no service, and no
startup hook that seeds the table from it. Every gate goes through the
`admin_emails` *table* (`AdminEmail`, `app/models/magic_link.py`), whose only
write path is `POST /admin/dashboard/settings/admin-emails` — which itself needs
an existing admin token, so the first row is a direct SQL insert. Granting
someone admin access by adding their address to the droplet's `.env` therefore
does nothing at all, and the no-enumeration response above means they just never
receive a link. (`openai_api_key` is the other dead `Settings` field — declared,
never read, and there is no `openai` package in `requirements.txt`. Both are
harmless precisely because of the `extra = "ignore"` rule above, but neither is
evidence of a feature.)

**The client half of that gate is `AdminAuthProvider.jsx` plus one `authFetch`
helper, and it has no interceptor layer** — worth knowing before adding an admin
page or call:

- **The token is threaded explicitly, not ambiently.** `authFetch(token, url,
  options)` (`frontend/src/services/admin.js`) is a thin `fetch` wrapper that
  merges `Authorization: Bearer ${token}` into the caller's headers (and leaves
  FormData without a `Content-Type` so the browser sets the multipart boundary);
  every one of the ~100 `adminService` methods takes `token` as its first
  argument, sourced from `useAdminAuth()` in the page. There is no configured
  client to add a header to.
- **Nothing handles 401.** No method in `admin.js` inspects `res.status`; a
  `!res.ok` becomes a generic `throw new Error('Failed to fetch …')` (some parse
  `detail` first). So an expired token surfaces as an arbitrary failure toast
  on whatever was clicked, not a re-login — the only revalidation is the
  provider's mount-time `verifyToken`, which logs out on failure.
- **The token lives in `sessionStorage`** (`adminToken` / `adminEmail`), so it
  dies with the tab — the 7-day TTL survives that only because the emailed link
  stays reusable: re-clicking it signs back in all week; the landing token is read from `window.location.hash`
  (`?token=` is still accepted as a legacy landing fallback, which is *how the
  SPA extracts it*, not how it transmits it) and stripped with
  `history.replaceState`. The route guard is a single `!isAuthenticated →
  <Navigate to="/admin/login">` in `components/layout/AdminLayout.jsx`.
- **`import.meta.env.DEV` short-circuits the whole bootstrap.** In any dev build
  the provider initializes `token = 'dev-token'` and `isAuthenticated = true`
  with **no server round-trip at all**, whatever the backend is configured to
  do — so the two-condition backend gate (`settings.debug` *and*
  `environment == "development"`) is the only thing that actually decides, and a
  dev frontend pointed at a non-dev backend looks logged in until the first call
  401s.
- **Every admin route is `lazy()`-imported** in `App.jsx` (#18) so the AG Grid
  (~500 kB) and TipTap (~300 kB) chunks stay out of the bundle public visitors
  download. A new admin page added as a static import silently undoes that.

**The educator client half is a different provider with opposite choices — don't
read it off the admin one.** `EducatorAuthProvider.jsx` stores `{email, token,
expiresAt}` as one JSON blob in **`localStorage`** under `educator_access` (so it
survives the tab, matching the 7-day server window, where the admin token dies
with it), and `services/educators.js` owns the read/write helpers. On mount it **re-validates against the server** via
`POST /educators/session` — the router's fourth endpoint, alongside `access`,
`signup` and `unsubscribe` — and the three outcomes are deliberate: valid
refreshes the stored `expires_at`; invalid drops the token but **keeps the email**
for a one-field re-entry; a network error sets `verifyError` and **refuses
access** rather than trusting stale local state. There is no
`import.meta.env.DEV` bypass on this side at all. Stored state is trusted
optimistically only until that call returns, which is what `loading` covers.

**One `educators` row carries two decoupled concerns** (`app/models/educator.py`),
and conflating them breaks one of the two:

- **Gate access** (#50/#52/#53) is entitlement-by-presence — being in the table is
  the entitlement, and there is deliberately **no email-ownership step** (no
  confirmation link, no code). `access(email)` re-issues a fresh
  `access_token` + 7-day window to any row that is not UNSUBSCRIBED; `signup`
  creates the row and grants access immediately. **Its `granted` flag is a
  deliberate enumeration oracle** (#58 — "accepted, by design", per the
  `access` docstring), i.e. the opposite call from the admin
  `request-access` endpoint's uniform "if this email is registered…" above.
  The reasoning is that registration is open and unauthenticated — anyone can
  `signup` *any* address and get a token at once — so "is X a registered
  educator" is neither a credential-attack step nor worth losing the inline
  signup UX (the since-deleted `EducatorAccessGate` branched on `granted` with
  no email round-trip), and the surface that does need spam protection, the newsletter,
  is separately gated by double opt-in. Collapsing this response to a constant
  to match the admin rule undoes an accepted decision.
- **Mailing list** (`app/services/mailing_list.py`) is double opt-in and owns
  `status`: PENDING → CONFIRMED via a single-use `confirm_token`. A gate-only
  signup therefore stays **PENDING forever** — that is correct, not a stuck row,
  because the email was never proven. Requiring CONFIRMED for gate access would
  undo the low-friction design.

`UNSUBSCRIBED` is the shared kill switch: it revokes both gate access and list
membership. Unsubscribe is token-authenticated (#51), not email-only.

**There are two unsubscribe endpoints, in two routers, and only the educator one
matches that description** — check which one a change is about before reasoning
from the sentence above:

- `POST /educators/unsubscribe` (`app/api/educators.py` →
  `educator_service.unsubscribe`) is the #51 one: the row is derived from the
  caller's own `access_token`, there is no email parameter, and it sets
  `status = UNSUBSCRIBED`, clears `access_token` / `access_expires_at` and
  `mailing_list = False` — the full kill switch.
- `POST /unsubscribe` (`app/api/subscribe.py` → `mailing_list.unsubscribe`)
  takes `UnsubscribeRequest {email: EmailStr}` and **no token at all**, and it
  is the one the public page uses: `Unsubscribe.jsx` reads the address from a
  `?email=` query param (base64-decoded with `atob` when it does not look like
  an address — obfuscation, not authentication) and POSTs it. It is also
  **narrower than the kill switch by design**: a row that still has gate access
  (`access_expires_at is not None`) only has `mailing_list` flipped to False and
  keeps `status = CONFIRMED`, so opting out of the list does not cost a teacher
  their materials access. `UNSUBSCRIBED` is reached only when there is no gate
  access to preserve.
