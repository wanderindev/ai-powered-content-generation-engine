"""Media library endpoints — browse, crawl, and manage the image catalog."""

import io
import logging
import re
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import PurePosixPath
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import Response
from PIL import Image as PILImage
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import validate_admin_token
from app.core.database import get_db
from app.models.article import Article
from app.models.crawl_job import CrawlJob, CrawlJobType
from app.models.media import Media
from app.models.media_candidate import CandidateStatus, MediaCandidate
from app.schemas.media_candidate import (
    CandidateListResponse,
    CatalogStats,
    CrawlJobListResponse,
    CrawlJobResponse,
    CrawlRequest,
    LOCCrawlRequest,
    MediaCandidateDetail,
    MediaCandidateItem,
    MediaImportRequest,
    ReviewRequest,
    ScoreRequest,
)
from app.services.crawl_jobs import active_job, run_crawl_job
from app.services.image_storage import ImageStorageService, make_storage_key
from app.services.loc import get_best_image_url

logger = logging.getLogger(__name__)

# Extension for the Spaces key of a URL-imported image, by the content type
# derived from its decoded bytes (image_storage.FORMAT_TO_CONTENT_TYPE values).
_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/tiff": ".tif",
}

router = APIRouter(
    prefix="/media-library",
    tags=["media-library"],
    dependencies=[Depends(validate_admin_token)],
)


def _kickoff_job(
    db: Session,
    background_tasks: BackgroundTasks,
    job_type: CrawlJobType,
    params: dict,
) -> CrawlJob:
    """Create a crawl job, schedule it off the request path, return the row.

    Refuses to start a second job of the same type while one is in flight (#277) —
    a pragmatic guard given the per-process rate limiter (cross-worker
    coordination is deferred, see services/crawl_jobs.py).
    """
    existing = active_job(db, job_type.value)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {job_type.value} job is already running (id={existing.id}).",
        )

    job = CrawlJob(job_type=job_type.value, params=params)
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_crawl_job, job.id)
    logger.info("Kicked off %s crawl job %d", job_type.value, job.id)
    return job


@router.post("/crawl", response_model=CrawlJobResponse, status_code=status.HTTP_202_ACCEPTED)
def trigger_crawl(
    request: CrawlRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Kick off a Wikimedia Commons crawl as a background job. Poll GET /jobs/{id}."""
    return _kickoff_job(db, background_tasks, CrawlJobType.WIKIMEDIA, request.model_dump())


@router.post("/crawl-loc", response_model=CrawlJobResponse, status_code=status.HTTP_202_ACCEPTED)
def trigger_loc_crawl(
    request: LOCCrawlRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Kick off a Library of Congress crawl as a background job. Poll GET /jobs/{id}."""
    return _kickoff_job(db, background_tasks, CrawlJobType.LOC, request.model_dump())


@router.post("/score", response_model=CrawlJobResponse, status_code=status.HTTP_202_ACCEPTED)
def trigger_scoring(
    request: ScoreRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Kick off LLM relevance scoring as a background job. Poll GET /jobs/{id}."""
    return _kickoff_job(db, background_tasks, CrawlJobType.SCORE, request.model_dump())


@router.get("/jobs", response_model=CrawlJobListResponse)
def list_crawl_jobs(
    db: Session = Depends(get_db),
    job_type: str | None = Query(None, description="Filter by job type: WIKIMEDIA, LOC, SCORE"),
    limit: int = Query(20, ge=1, le=100),
):
    """List recent crawl/score jobs, newest first (for resuming progress polling)."""
    query = select(CrawlJob)
    if job_type:
        query = query.where(CrawlJob.job_type == job_type.upper())
    # id desc as a stable tiebreaker — jobs created in the same instant otherwise
    # order arbitrarily.
    query = query.order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc()).limit(limit)
    jobs = db.execute(query).scalars().all()
    return CrawlJobListResponse(items=[CrawlJobResponse.model_validate(j) for j in jobs])


@router.get("/jobs/{job_id}", response_model=CrawlJobResponse)
def get_crawl_job(
    job_id: int,
    db: Session = Depends(get_db),
):
    """Get the status, progress, and final stats of a crawl/score job."""
    job = db.get(CrawlJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/candidates", response_model=CandidateListResponse)
def list_candidates(
    db: Session = Depends(get_db),
    status: str | None = Query(None, description="Filter by status: PENDING, APPROVED, REJECTED"),
    source: str | None = Query(None, description="Filter by source: WIKIMEDIA, LOC"),
    min_score: float | None = Query(None, ge=0, le=1, description="Minimum relevance score"),
    search: str | None = Query(None, description="Search in title and description"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """List media candidates with filtering and pagination."""
    query = select(MediaCandidate)

    if status:
        query = query.where(MediaCandidate.status == status.upper())
    if source:
        query = query.where(MediaCandidate.source == source.upper())
    if min_score is not None:
        query = query.where(MediaCandidate.relevance_score >= min_score)
    if search:
        # Tokenized AND across title+description (same semantics as /media, so
        # a query behaves identically in the library grid and the image picker).
        haystack = func.concat_ws(" ", MediaCandidate.title, MediaCandidate.description)
        for token in search.split():
            query = query.where(haystack.ilike(f"%{token}%"))

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar() or 0

    # Order by relevance (scored first, then by score desc), then by crawled_at
    query = query.order_by(
        MediaCandidate.relevance_score.desc().nulls_last(),
        MediaCandidate.crawled_at.desc().nulls_last(),
    )

    # Paginate
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    candidates = db.execute(query).scalars().all()

    # Batch-fetch thumbnail URLs for candidates with media_id
    media_ids = [c.media_id for c in candidates if c.media_id]
    thumb_map = {}
    if media_ids:
        thumb_rows = db.execute(
            select(Media.id, Media.thumbnail_path).where(Media.id.in_(media_ids))
        ).all()
        thumb_map = {row[0]: row[1] for row in thumb_rows if row[1]}

    items = []
    for c in candidates:
        item = MediaCandidateItem.model_validate(c)
        if c.media_id and c.media_id in thumb_map:
            item.thumbnail_cdn_url = thumb_map[c.media_id]
        items.append(item)

    return CandidateListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


def _enrich_detail(candidate: MediaCandidate, db: Session) -> MediaCandidateDetail:
    """Build a MediaCandidateDetail with thumbnail_cdn_url from linked media."""
    detail = MediaCandidateDetail.model_validate(candidate)
    if candidate.media_id:
        media = db.get(Media, candidate.media_id)
        if media and media.thumbnail_path:
            detail.thumbnail_cdn_url = media.thumbnail_path
    return detail


@router.get("/candidates/{candidate_id}", response_model=MediaCandidateDetail)
def get_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
):
    """Get full details of a single candidate."""
    candidate = db.get(MediaCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return _enrich_detail(candidate, db)


@router.post("/candidates/{candidate_id}/approve", response_model=MediaCandidateDetail)
def approve_candidate(
    candidate_id: int,
    request: ReviewRequest,
    db: Session = Depends(get_db),
):
    """Approve a media candidate — downloads image to DO Spaces and creates a Media record."""
    candidate = db.get(MediaCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if candidate.status == CandidateStatus.APPROVED.value:
        raise HTTPException(status_code=400, detail="Candidate already approved")

    now = datetime.now(UTC)

    # Download the image and upload to DO Spaces
    media_record = None
    try:
        key = make_storage_key(candidate.source, candidate.external_id)

        # For LOC candidates, fetch the best available image URL from item detail
        download_url = candidate.external_url
        if candidate.source == "LOC" and candidate.page_url:
            best_url = get_best_image_url(candidate.page_url)
            if best_url:
                download_url = best_url
                logger.info("LOC best image URL for %d: %s", candidate_id, best_url)

        with ImageStorageService() as storage:
            cdn_url, file_size, content_type, thumbnail_url = storage.download_and_upload(
                source_url=download_url,
                key=key,
            )

        # Determine source type for Media record (must match media_source_type enum in DB)
        source_type = candidate.source  # WIKIMEDIA or LOC

        media_record = Media(
            filename=key.rsplit("/", 1)[-1],
            original_filename=candidate.external_id,
            file_path=cdn_url,
            thumbnail_path=thumbnail_url,
            file_size=file_size,
            mime_type=content_type,
            media_type="IMAGE",
            source=source_type,
            title=candidate.title[:255] if candidate.title else None,
            alt_text=candidate.title[:255] if candidate.title else None,
            attribution=candidate.author,
            license=candidate.license,
            license_url=candidate.license_url,
            source_url=candidate.page_url,
            commons_id=candidate.external_id if candidate.source == "WIKIMEDIA" else None,
            width=candidate.width,
            height=candidate.height,
            created_at=now,
            updated_at=now,
        )
        db.add(media_record)
        db.flush()

        candidate.media_id = media_record.id
        logger.info(
            "Uploaded candidate %d to Spaces: %s (media_id=%d)",
            candidate_id,
            cdn_url,
            media_record.id,
        )
    except Exception as e:
        logger.error("Failed to upload candidate %d to Spaces: %s", candidate_id, e)
        # Still approve the candidate even if upload fails — can retry later
        logger.info("Approving candidate %d without image upload", candidate_id)

    candidate.status = CandidateStatus.APPROVED.value
    candidate.review_notes = request.notes
    candidate.reviewed_at = now
    db.commit()
    db.refresh(candidate)

    return _enrich_detail(candidate, db)


@router.post("/candidates/{candidate_id}/reject", response_model=MediaCandidateDetail)
def reject_candidate(
    candidate_id: int,
    request: ReviewRequest,
    db: Session = Depends(get_db),
):
    """Reject a media candidate."""
    candidate = db.get(MediaCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if candidate.status == CandidateStatus.REJECTED.value:
        raise HTTPException(status_code=400, detail="Candidate already rejected")

    candidate.status = CandidateStatus.REJECTED.value
    candidate.review_notes = request.notes
    candidate.reviewed_at = datetime.now(UTC)
    db.commit()
    db.refresh(candidate)

    return _enrich_detail(candidate, db)


@router.get("/media")
def list_media(
    db: Session = Depends(get_db),
    search: str | None = Query(None, description="Search in title, caption and search terms"),
    source: str | None = Query(
        None, description="Filter by source, comma-separated: WIKIMEDIA,LOC"
    ),
    exclude_feature_images: bool = Query(
        False, description="Exclude images used as article feature images"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """List production media records (images uploaded to Spaces)."""
    query = select(Media).where(Media.media_type == "IMAGE")

    if source:
        sources = [s.strip().upper() for s in source.split(",") if s.strip()]
        if len(sources) == 1:
            query = query.where(Media.source == sources[0])
        else:
            query = query.where(Media.source.in_(sources))
    if exclude_feature_images:
        feature_ids = select(Article.feature_image_id).where(Article.feature_image_id.is_not(None))
        query = query.where(Media.id.notin_(feature_ids))
    if search:
        # Match the library's discoverability: titles are terse English
        # LOC/Wikimedia captions, so topical and Spanish queries only hit via
        # caption and the bilingual Haiku search_terms (what /public-media uses).
        # Tokenized AND, not a phrase match — "Casco Antiguo wall" must find an
        # image whose combined text has the words anywhere, in any order.
        haystack = func.concat_ws(" ", Media.title, Media.caption, Media.search_terms)
        for token in search.split():
            query = query.where(haystack.ilike(f"%{token}%"))

    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar() or 0

    query = query.order_by(Media.created_at.desc())
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    records = db.execute(query).scalars().all()

    return {
        "items": [_media_item(m) for m in records],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _media_item(m: Media) -> dict:
    """The picker's media item shape — shared by /media and /media/import-url."""
    return {
        "id": m.id,
        "title": m.title,
        "file_path": m.file_path,
        "thumbnail_path": m.thumbnail_path,
        "source": m.source,
        "license": m.license,
        "attribution": m.attribution,
        "width": m.width,
        "height": m.height,
    }


@router.post("/media/import-url", status_code=status.HTTP_201_CREATED)
def import_media_from_url(
    request: MediaImportRequest,
    db: Session = Depends(get_db),
):
    """Import an external image URL into the media library.

    The picker's escape hatch: when the crawled catalog has no good photo, an
    editor pastes any web image URL and it becomes a normal Media row —
    downloaded through the SSRF-pinned client (size-capped, content type
    derived from the decoded bytes, SVG rejected), re-hosted on DO Spaces with
    a generated thumbnail. ``source="LOCAL"`` marks editor-imported images
    (unused by the crawl pipelines).
    """
    url = request.url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="URL must be http(s)")

    # Re-importing the same URL returns the existing row instead of a duplicate.
    existing = (
        db.execute(select(Media).where(Media.source_url == url, Media.media_type == "IMAGE"))
        .scalars()
        .first()
    )
    if existing:
        return _media_item(existing)

    try:
        with ImageStorageService() as storage:
            image_bytes, content_type = storage.download_image(url)
            width, height = PILImage.open(io.BytesIO(image_bytes)).size

            ext = _CONTENT_TYPE_EXT.get(content_type, ".jpg")
            stem = PurePosixPath(parsed.path).stem
            safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:80] or "image"
            # Hash prefix keys the object by URL: unique across same-named
            # files, stable if the same URL is ever re-fetched.
            key = f"media/local/{sha1(url.encode()).hexdigest()[:12]}_{safe_stem}{ext}"

            cdn_url = storage.upload_image(image_bytes, key, content_type)
            thumbnail_url = storage.generate_thumbnail(image_bytes, key)
    except ValueError as e:
        # Oversized body / undecodable bytes / disallowed format (e.g. SVG).
        raise HTTPException(status_code=400, detail=str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch the image: {e}") from e

    media_record = Media(
        filename=key.rsplit("/", 1)[-1],
        original_filename=PurePosixPath(parsed.path).name or key.rsplit("/", 1)[-1],
        file_path=cdn_url,
        thumbnail_path=thumbnail_url,
        file_size=len(image_bytes),
        mime_type=content_type,
        media_type="IMAGE",
        source="LOCAL",
        title=(request.title or safe_stem.replace("_", " "))[:255],
        alt_text=(request.title or safe_stem.replace("_", " "))[:255],
        source_url=url,
        width=width,
        height=height,
    )
    db.add(media_record)
    db.commit()
    db.refresh(media_record)

    logger.info("Imported media from URL %s as media_id=%d", url, media_record.id)
    return _media_item(media_record)


ALLOWED_IMAGE_HOSTS = {
    "upload.wikimedia.org",
    "tile.loc.gov",
    "www.loc.gov",
    "loc.gov",
    "cdn.loc.gov",
}


@router.get("/image-proxy")
def proxy_image(
    url: str = Query(..., description="External image URL to proxy"),
    db: Session = Depends(get_db),
):
    """Proxy external images to avoid CORS/ORB browser blocking."""
    parsed = urlparse(url)
    if parsed.hostname not in ALLOWED_IMAGE_HOSTS:
        raise HTTPException(status_code=400, detail="Host not allowed")

    try:
        headers = {"User-Agent": "PanamaInContext/1.0 (image-proxy; panamaincontext.com)"}
        with httpx.Client(timeout=15, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.warning("Image proxy got %d for %s", e.response.status_code, url[:120])
        raise HTTPException(status_code=502, detail=f"Upstream returned {e.response.status_code}")
    except httpx.HTTPError as e:
        logger.warning("Image proxy error for %s: %s", url[:120], e)
        raise HTTPException(status_code=502, detail="Failed to fetch image")

    content_type = resp.headers.get("content-type", "image/jpeg")
    return Response(
        content=resp.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/stats", response_model=CatalogStats)
def get_catalog_stats(
    db: Session = Depends(get_db),
):
    """Get aggregate statistics about the media catalog."""
    total = db.execute(select(func.count(MediaCandidate.id))).scalar() or 0

    # By source
    source_rows = db.execute(
        select(MediaCandidate.source, func.count(MediaCandidate.id)).group_by(MediaCandidate.source)
    ).all()
    by_source = {row[0]: row[1] for row in source_rows}

    # By status
    status_rows = db.execute(
        select(MediaCandidate.status, func.count(MediaCandidate.id)).group_by(MediaCandidate.status)
    ).all()
    by_status = {row[0]: row[1] for row in status_rows}

    # Scoring stats
    scored = (
        db.execute(
            select(func.count(MediaCandidate.id)).where(MediaCandidate.relevance_score.is_not(None))
        ).scalar()
        or 0
    )

    avg_relevance = None
    if scored > 0:
        avg_relevance = db.execute(
            select(func.avg(MediaCandidate.relevance_score)).where(
                MediaCandidate.relevance_score.is_not(None)
            )
        ).scalar()
        if avg_relevance is not None:
            avg_relevance = round(float(avg_relevance), 3)

    return CatalogStats(
        total=total,
        by_source=by_source,
        by_status=by_status,
        scored=scored,
        unscored=total - scored,
        avg_relevance=avg_relevance,
    )
