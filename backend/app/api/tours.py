"""Tour API endpoints."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.models import Tour, TourAttraction
from app.schemas.tour import (
    AttractionListItem,
    AvailabilitySlot,
    TourDetail,
    TourListItem,
)
from app.schemas.zone import ZoneBase
from app.services.availability import get_availability_service

router = APIRouter(prefix="/tours", tags=["tours"])


@router.get("", response_model=list[TourListItem])
def list_tours(
    lang: str = Query("en", description="Language code (en or es)"),
    db: Session = Depends(get_db),
):
    """
    List all active tours.

    Returns tour summaries for display in tour catalog.
    """
    tours = (
        db.query(Tour)
        .filter(Tour.is_active.is_(True))
        .order_by(Tour.display_order, Tour.name)
        .all()
    )

    use_spanish = lang == "es"

    return [
        TourListItem(
            id=t.id,
            slug=t.slug,
            name=t.name_es if use_spanish else t.name,
            name_es=t.name_es,
            duration_hours=t.duration_hours,
            max_group_size=t.max_group_size,
            zone_id=t.zone_id,
        )
        for t in tours
    ]


@router.get("/{slug}", response_model=TourDetail)
def get_tour(
    slug: str,
    lang: str = Query("en", description="Language code (en or es)"),
    db: Session = Depends(get_db),
):
    """
    Get tour details including attractions and ticket prices.

    Args:
        slug: Tour URL slug
        lang: Language code for localized content
    """
    tour = (
        db.query(Tour)
        .options(
            joinedload(Tour.zone),
            joinedload(Tour.attractions).joinedload(TourAttraction.attraction),
        )
        .filter(Tour.slug == slug)
        .filter(Tour.is_active.is_(True))
        .first()
    )

    if not tour:
        raise HTTPException(status_code=404, detail="Tour not found")

    use_spanish = lang == "es"

    # Build attractions list
    attractions = []
    for ta in sorted(tour.attractions, key=lambda x: x.visit_order):
        a = ta.attraction
        if a.is_active:
            attractions.append(
                AttractionListItem(
                    id=a.id,
                    name=a.name_es if use_spanish else a.name,
                    ticket_adult_tourist=a.ticket_adult_tourist,
                    ticket_adult_resident=a.ticket_adult_resident,
                    ticket_senior_tourist=a.ticket_senior_tourist,
                    ticket_senior_resident=a.ticket_senior_resident,
                    ticket_child_5_10_tourist=a.ticket_child_5_10_tourist,
                    ticket_child_5_10_resident=a.ticket_child_5_10_resident,
                    ticket_child_10_17_tourist=a.ticket_child_10_17_tourist,
                    ticket_child_10_17_resident=a.ticket_child_10_17_resident,
                    ticket_infant_tourist=a.ticket_infant_tourist,
                    ticket_infant_resident=a.ticket_infant_resident,
                    notes=a.notes,
                    is_optional=ta.is_optional,
                )
            )

    return TourDetail(
        id=tour.id,
        slug=tour.slug,
        name=tour.name_es if use_spanish else tour.name,
        name_es=tour.name_es,
        description=tour.description_es if use_spanish else tour.description,
        description_es=tour.description_es,
        duration_hours=tour.duration_hours,
        max_group_size=tour.max_group_size,
        zone_id=tour.zone_id,
        zone=ZoneBase(
            id=tour.zone.id,
            name=tour.zone.name_es if use_spanish else tour.zone.name,
            name_es=tour.zone.name_es,
            description=tour.zone.description_es if use_spanish else tour.zone.description,
            description_es=tour.zone.description_es,
        ),
        attractions=attractions,
        created_at=tour.created_at,
        updated_at=tour.updated_at,
    )


@router.get("/{slug}/availability", response_model=list[AvailabilitySlot])
def get_tour_availability(
    slug: str,
    start_date: date | None = Query(default=None, description="Start date (defaults to today)"),
    end_date: date | None = Query(
        default=None, description="End date (defaults to start + 90 days)"
    ),
    db: Session = Depends(get_db),
):
    """
    Get available time slots for a tour.

    Returns AM/PM availability for each date in the range.
    """
    # Verify tour exists
    tour = db.query(Tour).filter(Tour.slug == slug, Tour.is_active.is_(True)).first()
    if not tour:
        raise HTTPException(status_code=404, detail="Tour not found")

    # Set default dates
    if not start_date:
        start_date = date.today()
    if not end_date:
        end_date = start_date + timedelta(days=90)

    # Limit range to 365 days max
    if (end_date - start_date).days > 365:
        end_date = start_date + timedelta(days=365)

    availability_service = get_availability_service(db)
    return availability_service.get_availability_range(start_date, end_date)
