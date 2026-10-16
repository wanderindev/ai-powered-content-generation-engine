# Transactional Email

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

### Transactional Email
`app/services/email_sender.py` is the single transport (`send_email`, stdlib
`smtplib` over the IP-allowlisted relay — no SMTP auth) and it **raises
`EmailDeliveryError` on any failure; silent bool returns are not supported** (#67
— the Composio path it replaced swallowed Gmail-side failures). It Sentry-captures
before re-raising, so callers must not re-capture.

`app/services/notifications.py` layers the delivery *policy* on top, and the split
is deliberate (#70, #36/#37): **customer-facing failures propagate**
`EmailDeliveryError` (a stranded customer cannot reach their booking), while
**admin-side failures are caught and logged** (a missed admin alert is recoverable
in the admin UI). Every method returns `None` — a boolean "sent" flag is exactly
the bug #70 fixed, because it conflated either-success with both-success.

Bodies are Jinja2 templates in `app/templates/email/` (customer templates are
bilingual `*.en.html` / `*.es.html`; admin ones are single) rendered with
`select_autoescape` **on**, which is what stops an attacker-supplied
`customer_name` rendering as live HTML in Diego's inbox (#31). Building a body by
string interpolation instead of a template re-opens that hole.

**`notifications.py` is not the only caller of the transport.** Two modules call
`send_email` directly: `app/services/mailing_list.py` (its own Jinja2 `Environment`
with the same `select_autoescape`, for the double-opt-in confirmation), and
`app/api/contact.py`, which is the **one body still assembled by f-string
interpolation** — `_format_contact_email` inlines the submitter's name, message,
school, hotel address and the rest straight into an HTML table with no escaping,
i.e. exactly the shape the paragraph above says re-opens #31. Know it is there
before concluding every outbound body is autoescaped. The rest of that handler is
sound and worth copying for a new form: the `ContactSubmission` row is committed
**before** the send so form data is never lost, and an `EmailDeliveryError` is
recorded on the row (`email_error`) rather than failing the request — the
admin-side-absorbs half of the policy above.
