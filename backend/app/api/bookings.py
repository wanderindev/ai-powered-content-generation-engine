"""Booking API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.models import Hotel, Tour, TourAttraction
from app.schemas.booking import (
    CheckAvailabilityRequest,
    CheckAvailabilityResponse,
    PriceBreakdown,
    PriceCalculationRequest,
)
from app.services.availability import get_availability_service
from app.services.pricing import get_pricing_engine

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post("/calculate-price", response_model=PriceBreakdown)
def calculate_price(
    request: PriceCalculationRequest,
    db: Session = Depends(get_db),
):
    """
    Calculate price for a potential booking (live pricing).

    Returns complete breakdown including:
    - Guide service cost
    - Senior discount
    - Ticket costs per attraction
    - Transport cost (if applicable)
    - Grand total

    For groups larger than the tour's max group size, returns requires_quote=True.
    """
    # Get tour
    tour = (
        db.query(Tour)
        .options(
            joinedload(Tour.attractions).joinedload(TourAttraction.attraction),
        )
        .filter(Tour.slug == request.tour_slug)
        .filter(Tour.is_active.is_(True))
        .first()
    )

    if not tour:
        raise HTTPException(status_code=404, detail="Tour not found")

    # Get hotel zone if transport requested
    hotel_zone_id = None
    if request.transport and request.transport.hotel_id:
        hotel = db.query(Hotel).filter(Hotel.id == request.transport.hotel_id).first()
        if hotel:
            hotel_zone_id = hotel.zone_id

    # Calculate price
    pricing_engine = get_pricing_engine(db)
    return pricing_engine.calculate_full_price(
        tour=tour,
        participants=request.participants,
        transport=request.transport,
        hotel_zone_id=hotel_zone_id,
    )


@router.post("/check-availability", response_model=CheckAvailabilityResponse)
def check_availability(
    request: CheckAvailabilityRequest,
    db: Session = Depends(get_db),
):
    """
    Check if a specific slot is available.

    Returns availability status and reason if not available.
    """
    # Verify tour exists
    tour = db.query(Tour).filter(Tour.slug == request.tour_slug, Tour.is_active.is_(True)).first()

    if not tour:
        raise HTTPException(status_code=404, detail="Tour not found")

    availability_service = get_availability_service(db)
    can_book, reason = availability_service.can_book_slot(
        tour_date=request.tour_date,
        time_slot=request.time_slot,
    )

    return CheckAvailabilityResponse(
        available=can_book,
        reason=reason,
    )
