"""Public media API — read-only browsing of approved, high-relevance images."""

import mimetypes
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.article import Article
from app.models.media import Media
from app.models.media_candidate import MediaCandidate
from app.schemas.public_media import (
    PublicMediaDetail,
    PublicMediaItem,
    PublicMediaListResponse,
    PublicMediaStats,
    PublicMediaTags,
)
from app.services.media_search import (
    apply_ranked_search,
    media_search_vector,
    media_trigram_text,
)

# Open to everyone, no login. The educator email gate (#50) that used to sit on
# this router was removed so the teacher-facing materials are self-serve, like
# /public-edu. The public filter in _base_query is the only thing deciding what
# is exposed.
router = APIRouter(prefix="/public-media", tags=["public-media"])

# Subquery: article feature image IDs to exclude
_feature_image_ids = select(Article.feature_image_id).where(Article.feature_image_id.is_not(None))


def _base_query():
    """Return the base select joining Media + MediaCandidate with public filters."""
    return (
        select(Media, MediaCandidate)
        .join(MediaCandidate, MediaCandidate.media_id == Media.id)
        .where(
            MediaCandidate.status == "APPROVED",
            MediaCandidate.relevance_score >= 0.7,
            Media.id.notin_(_feature_image_ids),
        )
    )


def _to_item(media: Media, candidate: MediaCandidate) -> PublicMediaItem:
    """Map a Media + MediaCandidate row pair to a PublicMediaItem."""
    return PublicMediaItem(
        id=media.id,
        title=media.title,
        alt_text=media.alt_text,
        thumbnail_url=media.thumbnail_path,
        download_url=media.file_path,
        attribution=media.attribution,
        license=media.license,
        license_url=media.license_url,
        source_url=media.source_url,
        source=media.source,
        width=media.width,
        height=media.height,
        relevance_score=candidate.relevance_score,
        tags=candidate.tags or [],
        description=candidate.description,
    )


@router.get("", response_model=PublicMediaListResponse)
def list_public_media(
    db: Session = Depends(get_db),
    search: str | None = Query(
        None,
        description=(
            "Ranked search over title, alt text, caption, search terms, credit "
            "and the candidate's description/tags"
        ),
    ),
    tag: str | None = Query(None, description="Filter by tag"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=60),
):
    """List approved, high-relevance media with optional search and tag filters."""
    query = _base_query()

    if tag:
        query = query.where(MediaCandidate.tags.any(tag))

    if search:
        # Ranked search (#590) — the same helper as the admin picker.
        query, total = apply_ranked_search(
            db,
            query,
            search,
            vector=media_search_vector(),
            trigram_text=media_trigram_text(),
        )
        query = query.order_by(MediaCandidate.relevance_score.desc(), Media.id.desc())
    else:
        count_query = select(func.count()).select_from(query.subquery())
        total = db.execute(count_query).scalar() or 0
        query = query.order_by(MediaCandidate.relevance_score.desc())

    # Paginate
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    rows = db.execute(query).all()

    items = [_to_item(media, candidate) for media, candidate in rows]

    return PublicMediaListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/stats", response_model=PublicMediaStats)
def get_public_media_stats(
    db: Session = Depends(get_db),
):
    """Return total count of qualifying public media."""
    count_query = select(func.count()).select_from(_base_query().subquery())
    total = db.execute(count_query).scalar() or 0
    return PublicMediaStats(total=total)


@router.get("/tags", response_model=PublicMediaTags)
def get_public_media_tags(
    db: Session = Depends(get_db),
):
    """Return distinct tags from qualifying public media, sorted alphabetically."""
    # Build a simpler query that only selects the tags column
    candidate_ids_q = (
        select(MediaCandidate.id)
        .join(Media, MediaCandidate.media_id == Media.id)
        .where(
            MediaCandidate.status == "APPROVED",
            MediaCandidate.relevance_score >= 0.7,
            Media.id.notin_(_feature_image_ids),
            MediaCandidate.tags.is_not(None),
        )
        .subquery()
    )
    tag_query = (
        select(func.distinct(func.unnest(MediaCandidate.tags)).label("tag"))
        .where(MediaCandidate.id.in_(select(candidate_ids_q.c.id)))
        .order_by(literal_column("tag"))
    )
    rows = db.execute(tag_query).all()
    return PublicMediaTags(tags=[row[0] for row in rows])


@router.get("/{media_id}", response_model=PublicMediaDetail)
def get_public_media_detail(
    media_id: int,
    db: Session = Depends(get_db),
):
    """Get a single public media item by ID."""
    query = _base_query().where(Media.id == media_id)
    row = db.execute(query).first()
    if not row:
        raise HTTPException(status_code=404, detail="Media not found")

    media, candidate = row
    item = PublicMediaDetail(
        id=media.id,
        title=media.title,
        alt_text=media.alt_text,
        thumbnail_url=media.thumbnail_path,
        download_url=media.file_path,
        attribution=media.attribution,
        license=media.license,
        license_url=media.license_url,
        source_url=media.source_url,
        source=media.source,
        width=media.width,
        height=media.height,
        relevance_score=candidate.relevance_score,
        tags=candidate.tags or [],
        description=candidate.description,
        search_terms=media.search_terms,
    )
    return item


@router.get("/{media_id}/download")
def download_public_media(
    media_id: int,
    db: Session = Depends(get_db),
):
    """Proxy-download a public media file with Content-Disposition: attachment."""
    query = _base_query().where(Media.id == media_id)
    row = db.execute(query).first()
    if not row:
        raise HTTPException(status_code=404, detail="Media not found")

    media, _candidate = row
    file_url = media.file_path
    if not file_url:
        raise HTTPException(status_code=404, detail="No file available")

    # Derive filename from URL path
    parsed = urlparse(file_url)
    filename = parsed.path.rsplit("/", 1)[-1] or "download"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    # Stream the file from object storage
    with httpx.Client(timeout=30) as client:
        upstream = client.get(file_url)
        if upstream.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch file")

    return StreamingResponse(
        iter([upstream.content]),
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
