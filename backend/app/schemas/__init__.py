# Tour booking schemas
from app.schemas.admin import (
    AdminBookingDetail,
    AdminLoginRequest,
    AdminLoginResponse,
    AdminOrderDetail,
    AdminOrderListItem,
    MarkPaidRequest,
    MarkPaymentSentRequest,
    UpdateOrderNotesRequest,
    VerifyTokenRequest,
    VerifyTokenResponse,
)
from app.schemas.article import (
    ArticleBase,
    ArticleDetail,
    ArticleListItem,
    ArticleSeriesInfo,
)
from app.schemas.booking import (
    AttractionTickets,
    BookingItemRequest,
    CheckAvailabilityRequest,
    CheckAvailabilityResponse,
    ParticipantBreakdown,
    ParticipantCategory,
    PriceBreakdown,
    PriceCalculationRequest,
    TicketLineItem,
    TransportRequest,
)
from app.schemas.category import CategoryBase, CategoryWithArticles, TaxonomyBase
from app.schemas.order import (
    BookingResponse,
    CancelOrderRequest,
    CreateOrderRequest,
    CustomerInfo,
    OrderListItem,
    OrderResponse,
    RenewPaymentRequest,
)
from app.schemas.tour import (
    AttractionBase,
    AttractionListItem,
    AvailabilitySlot,
    TourDetail,
    TourListItem,
)
from app.schemas.zone import (
    HotelBase,
    HotelSearchResult,
    ZoneBase,
    ZoneListItem,
)

__all__ = [
    # Existing schemas
    "ArticleBase",
    "ArticleListItem",
    "ArticleDetail",
    "ArticleSeriesInfo",
    "CategoryBase",
    "CategoryWithArticles",
    "TaxonomyBase",
    # Zone/Hotel schemas
    "ZoneBase",
    "ZoneListItem",
    "HotelBase",
    "HotelSearchResult",
    # Tour schemas
    "AttractionBase",
    "AttractionListItem",
    "AvailabilitySlot",
    "TourDetail",
    "TourListItem",
    # Booking schemas
    "ParticipantCategory",
    "ParticipantBreakdown",
    "TransportRequest",
    "PriceCalculationRequest",
    "TicketLineItem",
    "AttractionTickets",
    "PriceBreakdown",
    "BookingItemRequest",
    "CheckAvailabilityRequest",
    "CheckAvailabilityResponse",
    # Order schemas
    "CustomerInfo",
    "CreateOrderRequest",
    "BookingResponse",
    "OrderResponse",
    "OrderListItem",
    "CancelOrderRequest",
    "RenewPaymentRequest",
    # Admin schemas
    "AdminLoginRequest",
    "AdminLoginResponse",
    "VerifyTokenRequest",
    "VerifyTokenResponse",
    "MarkPaymentSentRequest",
    "MarkPaidRequest",
    "UpdateOrderNotesRequest",
    "AdminOrderListItem",
    "AdminOrderDetail",
    "AdminBookingDetail",
]
