"""Admin schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AdminLoginRequest(BaseModel):
    """Request admin magic link."""

    email: EmailStr


class AdminLoginResponse(BaseModel):
    """Response after requesting admin magic link."""

    message: str
    email_sent: bool


class VerifyTokenRequest(BaseModel):
    """Request to verify a magic link token."""

    token: str = Field(..., min_length=32, max_length=64)


class VerifyTokenResponse(BaseModel):
    """Response from token verification."""

    valid: bool
    action: str | None = None
    order_reference: str | None = None
    booking_reference: str | None = None
    admin_email: str | None = None
    expires_at: datetime | None = None


class MarkPaymentSentRequest(BaseModel):
    """Request to mark payment link as sent."""

    notes: str | None = Field(default=None, max_length=500)


class MarkPaidRequest(BaseModel):
    """Request to mark order as paid."""

    notes: str | None = Field(default=None, max_length=500)


class UpdateOrderNotesRequest(BaseModel):
    """Request to update order admin notes."""

    notes: str = Field(..., max_length=2000)


class AdminOrderListItem(BaseModel):
    """Order summary for admin list view."""

    reference: str
    customer_name: str
    customer_email: str
    customer_phone: str
    status: str
    payment_method: str | None = None
    grand_total: Decimal
    created_at: datetime
    paid_at: datetime | None = None
    bookings_count: int
    first_tour_date: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AdminOrderDetail(BaseModel):
    """Full order detail for admin view."""

    reference: str
    customer_name: str
    customer_email: str
    customer_phone: str
    customer_hotel: str | None = None
    customer_language: str
    status: str
    payment_method: str | None = None
    payment_link_sent_at: datetime | None = None
    payment_link_expires_at: datetime | None = None
    paid_at: datetime | None = None
    paypal_invoice_id: str | None = None
    paypal_invoice_url: str | None = None
    reservation_expires_at: datetime | None = None
    followup_1_sent_at: datetime | None = None
    followup_2_sent_at: datetime | None = None
    followup_3_sent_at: datetime | None = None
    subtotal: Decimal
    grand_total: Decimal
    admin_notes: str | None = None
    created_at: datetime
    updated_at: datetime
    bookings: list["AdminBookingDetail"]

    model_config = ConfigDict(from_attributes=True)


class AdminBookingDetail(BaseModel):
    """Booking detail for admin view."""

    reference: str
    tour_name: str
    tour_slug: str
    tour_date: datetime
    time_slot: str
    preferred_time: str | None = None
    status: str
    participants: dict
    total_participants: int
    total_residents: int
    total_tourists: int
    needs_transport: bool
    transport_direction: str | None = None
    hotel_name: str | None = None
    transport_quote_required: bool
    service_total: Decimal
    senior_discount: Decimal
    tickets_total: Decimal
    transport_total: Decimal
    grand_total: Decimal
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CalendarBookingItem(BaseModel):
    """Booking item for calendar view."""

    date: str  # ISO date string
    time_slot: str
    preferred_time: str | None = None
    booking_reference: str
    order_reference: str
    tour_name: str
    tour_slug: str
    total_participants: int
    status: str
    order_status: str
    payment_method: str | None = None
    customer_name: str
    needs_transport: bool
    grand_total: Decimal


class AdminCancelRequest(BaseModel):
    """Request to cancel an order from admin."""

    reason: str | None = Field(default=None, max_length=500)


class RescheduleBookingRequest(BaseModel):
    """Request to reschedule a booking."""

    new_date: str = Field(..., description="ISO date string YYYY-MM-DD")
    new_time_slot: str = Field(..., pattern="^(AM|PM)$")
    new_preferred_time: str | None = Field(default=None, max_length=20)
    reason: str | None = Field(
        default=None, max_length=500, description="Reason for reschedule (sent to customer)"
    )
    notify_customer: bool = True


class InvoicePreview(BaseModel):
    """Preview of a draft PayPal invoice for admin review."""

    invoice_id: str
    items: list[dict]
    due_date: str
    total: str
    customer_email: str
    customer_name: str


class InvoiceSentResponse(BaseModel):
    """Response after sending a PayPal invoice."""

    invoice_id: str
    payer_view_url: str | None = None
    message: str


# Update forward references
AdminOrderDetail.model_rebuild()
