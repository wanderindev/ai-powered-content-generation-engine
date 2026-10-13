"""
Tests for mailing-list confirmation email template rendering.

Regression tests for issue #80 (raw f-string HTML in
mailing_list._send_confirmation_email; pattern parallel to #31). They render
the subscribe-confirmation templates directly via the service's Jinja2
Environment with an HTML-bearing payload in the one interpolated variable
(`confirm_url`), and assert that:

1. The raw HTML payload is NOT present (autoescape neutralized it).
2. The escaped form is present.
3. A benign confirm_url still renders inside the href (happy-path sanity).

No DB is needed — the template only interpolates a single string, so this
file is independent of the Postgres testcontainer used by the rest of the
suite. Mirrors tests/test_notifications.py.
"""

import pytest
from app.services.mailing_list import _env

# Payload we expect to be escaped wherever confirm_url is rendered.
SCRIPT_PAYLOAD = "<script>alert(1)</script>"
SCRIPT_ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;"


class TestSubscribeConfirmTemplates:
    @pytest.mark.parametrize("language", ["en", "es"])
    def test_confirm_url_payload_is_escaped(self, language):
        url = f"https://x/?token=safe{SCRIPT_PAYLOAD}&lang={language}"
        out = _env.get_template(f"subscribe_confirm.{language}.html").render(confirm_url=url)
        # Raw <script> must not survive; the escaped entity form must appear.
        assert SCRIPT_PAYLOAD not in out
        assert SCRIPT_ESCAPED in out

    @pytest.mark.parametrize("language", ["en", "es"])
    def test_attribute_breakout_is_neutralized(self, language):
        # A payload that tries to break out of the href attribute value.
        url = 'https://x"><script>alert(1)</script>'
        out = _env.get_template(f"subscribe_confirm.{language}.html").render(confirm_url=url)
        assert "<script>alert(1)</script>" not in out
        assert "&#34;" in out  # escaped double-quote inside the attribute

    @pytest.mark.parametrize("language", ["en", "es"])
    def test_happy_path_renders_url_in_href(self, language):
        # & in a normal query string must be attribute-escaped to &amp;.
        url = f"https://panamaincontext.com/confirm-subscription?token=abc123&lang={language}"
        out = _env.get_template(f"subscribe_confirm.{language}.html").render(confirm_url=url)
        assert (
            f'href="https://panamaincontext.com/confirm-subscription'
            f'?token=abc123&amp;lang={language}"' in out
        )

    def test_happy_path_copy_es(self):
        out = _env.get_template("subscribe_confirm.es.html").render(
            confirm_url="https://example.com/confirm"
        )
        assert "Confirma tu suscripción" in out
        assert "Confirmar Suscripción" in out

    def test_happy_path_copy_en(self):
        out = _env.get_template("subscribe_confirm.en.html").render(
            confirm_url="https://example.com/confirm"
        )
        assert "Confirm your subscription" in out
        assert "Confirm Subscription" in out
