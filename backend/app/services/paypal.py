"""
PayPal Invoicing API integration.

Creates and sends PayPal invoices for tour bookings.
All methods are synchronous to match the FastAPI sync endpoint pattern used in this project.
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import get_settings
from app.models import Booking, Order

logger = logging.getLogger(__name__)


class PayPalService:
    """
    PayPal Invoicing API v2 integration.

    Supports a two-step workflow:
    1. create_draft_invoice() — creates a draft for admin review
    2. send_invoice() — sends the draft to the customer
    """

    def __init__(self):
        settings = get_settings()
        self.base_url = settings.paypal_api_url
        self.client_id = settings.paypal_client_id
        self.client_secret = settings.paypal_client_secret
        self.business_email = settings.paypal_business_email
        self.webhook_id = settings.paypal_webhook_id
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    def _get_access_token(self) -> str:
        """Get OAuth2 access token from PayPal (cached)."""
        if self._access_token and self._token_expires_at:
            if datetime.now(UTC) < self._token_expires_at:
                return self._access_token

        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.base_url}/v1/oauth2/token",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

            self._access_token = data["access_token"]
            self._token_expires_at = datetime.now(UTC) + timedelta(
                seconds=data.get("expires_in", 3600) - 300
            )
            return self._access_token

    def _headers(self) -> dict:
        token = self._get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def create_draft_invoice(
        self,
        order: Order,
        bookings: list[Booking],
        due_hours: int = 48,
    ) -> dict:
        """
        Create a draft PayPal invoice (not yet sent to customer).

        Returns dict with invoice_id and item details for admin preview.
        """
        items = []
        for booking in bookings:
            tour_name = booking.tour.name
            items.append(
                {
                    "name": f"Tour: {tour_name}",
                    "description": (
                        f"Date: {booking.tour_date.isoformat()}, "
                        f"Time: {booking.time_slot}, "
                        f"Participants: {booking.total_participants}"
                    ),
                    "quantity": "1",
                    "unit_amount": {
                        "currency_code": "USD",
                        "value": str(booking.grand_total),
                    },
                }
            )

        due_date = (datetime.now(UTC) + timedelta(hours=due_hours)).strftime("%Y-%m-%d")

        invoice_data = {
            "detail": {
                "invoice_number": order.reference,
                "reference": order.reference,
                "currency_code": "USD",
                "note": "Thank you for booking with Panama In Context!",
                "terms_and_conditions": (
                    "Payment is due within 48 hours to confirm your reservation. "
                    "Cancellations made 7 or more days before the tour date receive a full refund "
                    "(minus any bank or processing fees). Cancellations made less than 7 days "
                    "before the tour date are not eligible for a refund."
                ),
                "payment_term": {
                    "term_type": "DUE_ON_DATE_SPECIFIED",
                    "due_date": due_date,
                },
            },
            "invoicer": {
                "name": {"given_name": "Panama In Context"},
                "email_address": self.business_email,
            },
            "primary_recipients": [
                {
                    "billing_info": {
                        "name": {"given_name": order.customer_name},
                        "email_address": order.customer_email,
                    }
                }
            ],
            "items": items,
        }

        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.base_url}/v2/invoicing/invoices",
                headers=self._headers(),
                json=invoice_data,
            )
            response.raise_for_status()
            invoice = response.json()

        invoice_id = invoice["id"]
        logger.info("Created draft PayPal invoice %s for order %s", invoice_id, order.reference)

        return {
            "invoice_id": invoice_id,
            "items": items,
            "due_date": due_date,
            "total": str(order.grand_total),
            "customer_email": order.customer_email,
            "customer_name": order.customer_name,
        }

    def send_invoice(self, invoice_id: str) -> str | None:
        """
        Send a draft invoice to the customer.

        Returns the payer_view_url (payment link) or None.
        """
        with httpx.Client(timeout=30) as client:
            # Send the invoice
            response = client.post(
                f"{self.base_url}/v2/invoicing/invoices/{invoice_id}/send",
                headers=self._headers(),
                json={"send_to_invoicer": True},
            )
            response.raise_for_status()

            # Fetch invoice details to get payer view URL
            detail_response = client.get(
                f"{self.base_url}/v2/invoicing/invoices/{invoice_id}",
                headers=self._headers(),
            )
            detail_response.raise_for_status()
            invoice_detail = detail_response.json()

        payer_view_url = None
        for link in invoice_detail.get("links", []):
            if link.get("rel") == "payer-view":
                payer_view_url = link.get("href")
                break

        logger.info("Sent PayPal invoice %s, payer URL: %s", invoice_id, payer_view_url)
        return payer_view_url

    def get_invoice_status(self, invoice_id: str) -> str:
        """Get the current status of an invoice."""
        with httpx.Client(timeout=30) as client:
            response = client.get(
                f"{self.base_url}/v2/invoicing/invoices/{invoice_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json().get("status", "UNKNOWN")

    def cancel_invoice(self, invoice_id: str) -> bool:
        """Cancel an invoice (sends cancellation notice to customer)."""
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.base_url}/v2/invoicing/invoices/{invoice_id}/cancel",
                headers=self._headers(),
                json={
                    "subject": "Invoice cancelled",
                    "note": "This invoice has been cancelled.",
                    "send_to_invoicer": True,
                    "send_to_recipient": True,
                },
            )
            success = response.status_code in (200, 204)
            if success:
                logger.info("Cancelled PayPal invoice %s", invoice_id)
            else:
                logger.warning(
                    "Failed to cancel PayPal invoice %s: %s", invoice_id, response.status_code
                )
            return success

    def verify_webhook_signature(
        self,
        transmission_id: str,
        transmission_time: str,
        event_body: str,
        cert_url: str,
        actual_signature: str,
        auth_algo: str,
    ) -> bool:
        """
        Verify PayPal webhook signature using PayPal's verification API.

        Fails closed: if ``webhook_id`` is not configured we cannot verify the
        signature, so we reject (issue #32). The route turns a ``False`` into a
        401. A misconfigured deploy that drops the env var therefore refuses
        webhooks rather than silently accepting unsigned ones. The startup guard
        in ``app.main`` catches that misconfiguration even earlier in prod.
        """
        if not self.webhook_id:
            logger.warning(
                "PayPal webhook signature verification skipped: webhook_id not "
                "configured — rejecting webhook (fail closed)"
            )
            return False

        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.base_url}/v1/notifications/verify-webhook-signature",
                headers=self._headers(),
                json={
                    "auth_algo": auth_algo,
                    "cert_url": cert_url,
                    "transmission_id": transmission_id,
                    "transmission_sig": actual_signature,
                    "transmission_time": transmission_time,
                    "webhook_id": self.webhook_id,
                    "webhook_event": event_body,
                },
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("verification_status") == "SUCCESS"

            logger.warning(
                "Webhook signature verification request failed: %s", response.status_code
            )
            return False


def get_paypal_service() -> PayPalService:
    """Get PayPal service instance."""
    return PayPalService()
