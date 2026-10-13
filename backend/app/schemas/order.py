"""Order schemas."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.booking import BookingItemRequest


class CustomerInfo(BaseModel):
    """Customer information for order."""

    name: str = Field(..., min_length=2, max_length=200)
    email: EmailStr
    phone: str = Field(..., min_length=5, max_length=50)
    hotel: str | None = Field(default=None, max_length=200)
    language: str = Field(default="en", pattern="^(en|es)$")


class CreateOrderRequest(BaseModel):
    """Request to create an order from cart checkout."""

    customer: CustomerInfo
    payment_method: str = Field(..., pattern="^(PAYPAL|CUANTO|YAPPY)$")
    bookings: list[BookingItemRequest] = Field(..., min_length=1)


class BookingResponse(BaseModel):
    """Booking data in order response."""

    reference: str
    tour_name: str
    tour_name_es: str
    tour_slug: str
    tour_date: date
    time_slot: str
    preferred_time: str | None = None
    participants: dict
    total_participants: int
    total_residents: int
    total_tourists: int
    needs_transport: bool = False
    hotel_name: str | None = None
    service_total: Decimal
    senior_discount: Decimal
    tickets_total: Decimal
    transport_total: Decimal
    grand_total: Decimal
    status: str

    model_config = ConfigDict(from_attributes=True)


class OrderResponse(BaseModel):
    """Order response after creation or retrieval."""

    reference: str
    status: str
    customer_name: str
    customer_email: str
    customer_phone: str
    customer_hotel: str | None = None
    customer_language: str
    payment_method: str | None = None
    subtotal: Decimal
    grand_total: Decimal
    bookings: list[BookingResponse]
    created_at: datetime
    payment_link_expires_at: datetime | None = None
    paypal_invoice_url: str | None = None
    # Raw VIEW_ORDER magic-link token, returned ONLY by create_order so the
    # confirmation page can fetch the order without the token ever entering the
    # URL (it travels via router state). Never populated on GET responses. (#36)
    magic_link_token: str | None = None

    model_config = ConfigDict(from_attributes=True)


class OrderListItem(BaseModel):
    """Order summary for list views."""

    reference: str
    customer_name: str
    customer_email: str
    status: str
    payment_method: str | None = None
    grand_total: Decimal
    created_at: datetime
    bookings_count: int

    model_config = ConfigDict(from_attributes=True)


class CancelOrderRequest(BaseModel):
    """Request to cancel an order."""

    reason: str | None = Field(default=None, max_length=500)


class RenewPaymentRequest(BaseModel):
    """Request to renew payment link."""

    pass  # No additional fields needed
