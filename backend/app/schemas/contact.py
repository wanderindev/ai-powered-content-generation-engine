from pydantic import BaseModel, EmailStr


class ContactFormBase(BaseModel):
    """Base fields common to both form types."""

    first_name: str
    last_name: str
    email: EmailStr
    message: str | None = None


class FieldTripContactForm(ContactFormBase):
    """Contact form for field trip inquiries."""

    interest: str = "field-trips"
    school_name: str | None = None
    grade_levels: list[str] = []
    field_trips: list[str] = []


class TourContactForm(ContactFormBase):
    """Contact form for tour inquiries."""

    interest: str = "tours"
    country: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    adults: int | None = None
    children_under_5: int | None = None
    children_5_to_17: int | None = None
    tours: list[str] = []
    hotel_address: str | None = None
    needs_transport: bool = False


class ContactFormRequest(BaseModel):
    """
    Unified contact form request that handles both types.
    The interest field determines which fields are relevant.
    """

    interest: str
    first_name: str
    last_name: str
    email: EmailStr
    message: str | None = None

    # Field trip specific
    school_name: str | None = None
    grade_levels: list[str] = []
    field_trips: list[str] = []

    # Tour specific
    country: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    adults: int | None = None
    children_under_5: int | None = None
    children_5_to_17: int | None = None
    tours: list[str] = []
    hotel_address: str | None = None
    needs_transport: bool = False


class ContactFormResponse(BaseModel):
    """Response after submitting contact form."""

    success: bool
    message: str
