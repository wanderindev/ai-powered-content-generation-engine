# Forms & Mailing List

[← Back to overview](README.md)

Two small systems share the email backbone: the contact form (how schools and tourists reach Diego) and the mailing list (a double opt-in newsletter). They're modest on their own, but this is where most of the "don't lose a lead, don't leak an address" decisions live. Until September there was a third system here, an email gate in front of the teachers' media library. It's gone, and the last section says why.

## Email delivery

Everything outbound goes through one function, `send_email`: stdlib `smtplib` talking to Google Workspace's SMTP relay (`smtp-relay.gmail.com:587`) over STARTTLS. There's **no SMTP auth**: the droplet's outbound IP is allowlisted in Workspace instead, which is simpler and keeps credentials out of the environment. It replaced an earlier third-party integration (Composio) that swallowed Gmail-side failures, so the rule since then is that a failed send raises a typed `EmailDeliveryError`, captured to Sentry before it propagates, never a silent boolean.

On top of that transport sits a small policy layer, the notification service, which decides what a failure means. **Customer-facing failures propagate**: a customer who never gets their booking link is stranded, so the request has to fail. **Admin-side failures are logged and absorbed**: a missed admin alert is recoverable, because the order or booking is right there in the admin. Booking confirmations, order updates and admin magic links all go through it, and its bodies are Jinja2 templates (bilingual for customers) rendered with autoescape on, so a name typed into a booking form can't turn into live HTML in Diego's inbox.

Two callers go straight to the transport instead. The mailing list renders its confirmation email from its own Jinja2 environment, with the same autoescape setting. The contact form owns its own failure policy, described below.

## Testing email without sending email

On 2026-10-05 an agent running the backend suite inside a managed-agents sandbox delivered real mail through the relay. Nothing in the test setup stopped a test from reaching `smtplib.SMTP`, and the relay authorizes by sender IP, not by credentials, so it accepted the messages. The bounces for a test fixture's `john@example.com` landed in Diego's inbox.

The suite now has three layers, so any one of them is enough:

- An autouse `smtp_outbox` fixture replaces `smtplib.SMTP` with a recorder that never opens a socket. Tests that want to assert on what was sent request it by name.
- The test configuration points the relay host at `smtp.invalid` (a name RFC 2606 reserves), so a send that somehow gets past the fixture fails DNS instead of reaching Google.
- `send_email` itself refuses to touch the real `smtplib.SMTP` class when the environment is `test`, raising `RealSendBlockedError`. It's a `RuntimeError`, not an `EmailDeliveryError`, on purpose: the admin-side paths that absorb delivery errors would otherwise swallow it and the test would pass.

The lesson is about agents more than email: when code runs in someone else's sandbox, "it only works from an allowlisted machine" stops being a safety property.

## The contact form

![Contact page](images/contact.png "screenshot")

The contact form has two interest types, *field trips* and *tours*, pre-selectable from a query parameter so a "Contact us about field trips" button lands on the right variant. Type-specific fields are stored in a JSONB column so the form can grow without a migration each time.

The design rule here is **persist before send**. The submission is committed to the `contact_submissions` table *first*, and only then is the admin notification attempted. If the email fails, the error is stored on the row and the request still succeeds, so a lead is never lost because an email hiccupped. The submission is the source of truth; the email is a best-effort notification. That's also why the contact form calls `send_email` directly rather than going through the notification service: it needs to record the failure on its own row.

## The mailing list (double opt-in)

The newsletter is a proper double opt-in. Subscribing creates a `PENDING` row with a single-use confirmation token and sends a confirmation email; clicking the link flips the row to `CONFIRMED` and clears the token. The home page carries two calls to action: a newsletter box (`source='blog'`) and an educators box (`source='home_cta'`). The educators box goes through the educator signup endpoint with the newsletter opt-in on: it records the teacher, starts the same double opt-in, and sends them on to the educational materials.

That `source` column is intentionally an *open* vocabulary (plain `String`, no constraint). It holds identifiers for the CTAs that drove each signup, which grow over time, and a closed check constraint would be the wrong tool. It's the one deliberate exception to the project's "every status column gets a constraint" rule, and the model says so in a comment.

Concurrency is handled by the database: the email column is `UNIQUE`, so a check-then-insert race resolves cleanly. The loser catches the integrity error, re-fetches the existing row and goes down the existing-subscriber path instead of crashing. One `educators` table holds all of this; it replaced what used to be a Google Sheet.

Unsubscribing has two paths, and only one is in use. The public `/unsubscribe` page turns the newsletter off. It is deliberately narrow: it only takes an address off the list, and for a teacher who signed up through the educators box it flips the newsletter flag and leaves the rest of the row alone. The older, token-authenticated unsubscribe from the gate era still exists on the API, but no page calls it anymore.

## The educator gate, and why it's gone

Until September the media library section of the public educational materials sat behind a soft gate. A teacher entered an email, got an opaque seven-day token, and was in; there was no verification step, because the point was never security. It was a way to know who used the material and to grow the educator list, and I spent real care on it: server-side tokens instead of an earlier forgeable `base64(email:expiry)` scheme, re-validation on every mount, a token-authenticated unsubscribe.

In September I took it out. The materials were always meant to be free, and a teacher who wants a presentation or an image shouldn't have to hand over an email first. Removing it was one PR, because the gate was one dependency on one router plus one frontend component, and both had to go: dropping only the component would have left the API answering 401. Every teacher now gets every published deliverable and the whole public image library without signing in.

What's left is honest leftovers: the `educators` table and the newsletter CTA, which still matter, and some of the gate's plumbing that no page reads anymore and that is due for removal.

---

[← Admin Dashboard, Auth & CMS](12-admin-auth-and-cms.md) · [Back to overview](README.md) · Next: [Architecture, Data & Operations →](14-architecture-data-and-operations.md)
