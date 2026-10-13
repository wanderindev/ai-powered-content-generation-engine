"""Zone and hotel API endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.models import Hotel, Zone
from app.schemas.zone import HotelSearchResult, ZoneListItem

router = APIRouter(prefix="/zones", tags=["zones"])


@router.get("", response_model=list[ZoneListItem])
def list_zones(
    lang: str = Query("en", description="Language code (en or es)"),
    db: Session = Depends(get_db),
):
    """
    List all active zones for transport selection.

    Used to display zone options when hotel is not in database.
    """
    zones = db.query(Zone).filter(Zone.is_active.is_(True)).order_by(Zone.display_order).all()

    use_spanish = lang == "es"

    return [
        ZoneListItem(
            id=z.id,
            name=z.name_es if use_spanish else z.name,
            description=z.description_es if use_spanish else z.description,
        )
        for z in zones
    ]


@router.get("/hotels/search", response_model=list[HotelSearchResult])
def search_hotels(
    q: str = Query(..., min_length=2, description="Search query (minimum 2 characters)"),
    lang: str = Query("en", description="Language code (en or es)"),
    limit: int = Query(10, ge=1, le=50, description="Maximum results to return"),
    db: Session = Depends(get_db),
):
    """
    Search hotels by name (autocomplete).

    Returns matching hotels with their zone for transport pricing.
    """
    # Search in both name and name_es
    hotels = (
        db.query(Hotel)
        .options(joinedload(Hotel.zone))
        .filter(Hotel.is_active.is_(True))
        .filter(Hotel.name.ilike(f"%{q}%") | Hotel.name_es.ilike(f"%{q}%"))
        .order_by(Hotel.name)
        .limit(limit)
        .all()
    )

    use_spanish = lang == "es"

    return [
        HotelSearchResult(
            id=h.id,
            name=h.name_es if use_spanish and h.name_es else h.name,
            address=h.address_es if use_spanish and h.address_es else h.address,
            zone_id=h.zone_id,
            zone_name=h.zone.name_es if use_spanish else h.zone.name,
        )
        for h in hotels
    ]


@router.get("/hotels/{hotel_id}", response_model=HotelSearchResult)
def get_hotel(
    hotel_id: int,
    lang: str = Query("en", description="Language code (en or es)"),
    db: Session = Depends(get_db),
):
    """
    Get hotel details by ID.

    Used to retrieve hotel info for bookings.
    """
    hotel = (
        db.query(Hotel)
        .options(joinedload(Hotel.zone))
        .filter(Hotel.id == hotel_id, Hotel.is_active.is_(True))
        .first()
    )

    if not hotel:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Hotel not found")

    use_spanish = lang == "es"

    return HotelSearchResult(
        id=hotel.id,
        name=hotel.name_es if use_spanish and hotel.name_es else hotel.name,
        address=hotel.address_es if use_spanish and hotel.address_es else hotel.address,
        zone_id=hotel.zone_id,
        zone_name=hotel.zone.name_es if use_spanish else hotel.zone.name,
    )
