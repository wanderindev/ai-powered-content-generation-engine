"""Instagram social pipeline admin API endpoints.

Drafting, rendering and scheduling live here; the actual Graph API publisher
and the due-post scheduler are a separate slice (``services/instagram.py``) —
until it lands, publish-now answers 503 so the admin UI degrades gracefully.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import validate_admin_token
from app.core.database import get_db
from app.models.article import Article
from app.models.enums import ContentStatus, InstagramPostType, InstagramTemplateVariant
from app.models.instagram_post import InstagramPost
from app.schemas.dashboard import StatusUpdateResponse
from app.schemas.instagram import (
    ArticlePromoGenerateRequest,
    InstagramPostDetail,
    InstagramPostListItem,
    InstagramPostUpdate,
    InstagramPublishStateResponse,
    InstagramRenderResponse,
    InstagramScheduleRequest,
    InstagramStatusUpdateRequest,
    PromotableArticle,
    SabiasQueGenerateRequest,
)
from app.services.ig_html import sanitize_fact
from app.services.ig_render import render_instagram_post
from app.services.instagram import InstagramPublisher, publish_post
from app.services.instagram_generation import (
    generate_article_promo_post,
    generate_sabias_que_posts,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/dashboard/instagram",
    tags=["instagram"],
    dependencies=[Depends(validate_admin_token)],
)

VALID_TRANSITIONS = {
    "PENDING": {"APPROVED", "REJECTED"},
    "APPROVED": {"PENDING", "REJECTED"},
    "REJECTED": {"PENDING", "APPROVED"},
}


def _get_post(db: Session, post_id: int) -> InstagramPost:
    post = db.query(InstagramPost).filter(InstagramPost.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Instagram post not found")
    return post


def _require_unpublished(post: InstagramPost) -> None:
    if post.published_at is not None:
        raise HTTPException(status_code=400, detail="Post is already published on Instagram")


def _detail(post: InstagramPost) -> InstagramPostDetail:
    return InstagramPostDetail(
        id=post.id,
        post_type=post.post_type,
        template_variant=post.template_variant,
        status=post.status,
        derived_state=post.derived_state,
        research_id=post.research_id,
        article_id=post.article_id,
        title=post.title,
        fact=post.fact,
        polaroid_caption=post.polaroid_caption,
        kicker=post.kicker,
        meta_text=post.meta_text,
        caption=post.caption,
        image_query=post.image_query,
        media_id=post.media_id,
        media_path=post.media_path,
        image_url=post.image_url,
        scheduled_at=post.scheduled_at,
        published_at=post.published_at,
        publish_error=post.publish_error,
        publish_attempts=post.publish_attempts,
        ig_media_id=post.ig_media_id,
        ig_permalink=post.ig_permalink,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


def _publish_state(post: InstagramPost) -> InstagramPublishStateResponse:
    return InstagramPublishStateResponse(
        id=post.id,
        status=post.status,
        derived_state=post.derived_state,
        scheduled_at=post.scheduled_at,
        published_at=post.published_at,
        publish_error=post.publish_error,
        ig_permalink=post.ig_permalink,
    )


# --- Posts CRUD ---


@router.get("/posts", response_model=list[InstagramPostListItem])
def list_instagram_posts(db: Session = Depends(get_db)):
    """List all Instagram posts, newest first."""
    rows = db.query(InstagramPost).order_by(InstagramPost.id.desc()).all()
    return [
        InstagramPostListItem(
            id=p.id,
            post_type=p.post_type,
            template_variant=p.template_variant,
            title=p.title,
            status=p.status,
            derived_state=p.derived_state,
            has_image=p.image_url is not None,
            media_path=p.media_path,
            image_url=p.image_url,
            scheduled_at=p.scheduled_at,
            published_at=p.published_at,
            publish_error=p.publish_error,
            created_at=p.created_at,
        )
        for p in rows
    ]


@router.get("/posts/{post_id}", response_model=InstagramPostDetail)
def get_instagram_post(
    post_id: int = Path(..., description="Instagram post ID"),
    db: Session = Depends(get_db),
):
    """Get one Instagram post with all fields."""
    return _detail(_get_post(db, post_id))


@router.patch("/posts/{post_id}", response_model=InstagramPostDetail)
def update_instagram_post(
    post_id: int = Path(..., description="Instagram post ID"),
    body: InstagramPostUpdate = ...,
    db: Session = Depends(get_db),
):
    """Apply content edits from the admin drawer (blocked once published)."""
    post = _get_post(db, post_id)
    _require_unpublished(post)

    if body.template_variant is not None:
        if body.template_variant not in InstagramTemplateVariant.values():
            raise HTTPException(status_code=400, detail="Unknown template variant")
        post.template_variant = body.template_variant
    if body.title is not None:
        post.title = body.title
    if body.fact is not None:
        post.fact = sanitize_fact(body.fact)
    if body.polaroid_caption is not None:
        post.polaroid_caption = body.polaroid_caption or None
    if body.kicker is not None:
        post.kicker = body.kicker or None
    if body.meta_text is not None:
        post.meta_text = body.meta_text or None
    if body.caption is not None:
        post.caption = body.caption or None
    if body.image_query is not None:
        post.image_query = body.image_query or None
    if body.clear_media:
        post.media_id = None
        post.media_path = None
    else:
        if body.media_id is not None:
            post.media_id = body.media_id
        if body.media_path is not None:
            post.media_path = body.media_path

    db.commit()
    db.refresh(post)
    return _detail(post)


@router.patch("/posts/{post_id}/status", response_model=StatusUpdateResponse)
def update_instagram_post_status(
    post_id: int = Path(..., description="Instagram post ID"),
    body: InstagramStatusUpdateRequest = ...,
    db: Session = Depends(get_db),
):
    """Update editorial status. Leaving APPROVED cancels any pending schedule."""
    post = _get_post(db, post_id)
    _require_unpublished(post)

    if body.status not in VALID_TRANSITIONS.get(post.status, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transition from {post.status} to {body.status}",
        )

    post.status = body.status
    if body.status != ContentStatus.APPROVED.value:
        post.scheduled_at = None
    db.commit()
    return StatusUpdateResponse(id=post.id, status=post.status)


@router.delete("/posts/{post_id}", response_model=StatusUpdateResponse)
def delete_instagram_post(
    post_id: int = Path(..., description="Instagram post ID"),
    db: Session = Depends(get_db),
):
    """Delete a draft (published posts are history — they stay)."""
    post = _get_post(db, post_id)
    _require_unpublished(post)
    db.delete(post)
    db.commit()
    return StatusUpdateResponse(id=post_id, status="DELETED")


# --- Generation ---


@router.post("/generate/sabias-que", response_model=list[InstagramPostDetail])
def generate_sabias_que(
    body: SabiasQueGenerateRequest,
    db: Session = Depends(get_db),
):
    """Generate 3 sabías-que fact-card drafts from a research document."""
    posts = generate_sabias_que_posts(db, body.research_id)
    return [_detail(p) for p in posts]


@router.post("/generate/article-promo", response_model=InstagramPostDetail)
def generate_article_promo(
    body: ArticlePromoGenerateRequest,
    db: Session = Depends(get_db),
):
    """Generate one "Nuevo en el blog" draft for an article series."""
    return _detail(generate_article_promo_post(db, body.article_id))


@router.get("/promotable-articles", response_model=list[PromotableArticle])
def list_promotable_articles(db: Session = Depends(get_db)):
    """Approved series parents / standalone articles, for the generate modal."""
    rows = (
        db.query(Article)
        .options(selectinload(Article.series_children))
        .filter(
            Article.series_parent_id.is_(None),
            Article.status == ContentStatus.APPROVED.value,
        )
        .order_by(Article.published_at.desc().nullslast(), Article.id.desc())
        .all()
    )
    return [
        PromotableArticle(
            id=a.id,
            title=a.title_es or a.title,
            series_size=1 + len(a.series_children or []),
            published_at=a.published_at,
        )
        for a in rows
    ]


# --- Rendering ---


@router.post("/posts/{post_id}/render", response_model=InstagramRenderResponse)
def render_post_image(
    post_id: int = Path(..., description="Instagram post ID"),
    db: Session = Depends(get_db),
):
    """Render the branded 1080x1350 JPEG for this post (sync — Playwright)."""
    post = _get_post(db, post_id)
    _require_unpublished(post)

    image_url = render_instagram_post(post)
    db.commit()
    return InstagramRenderResponse(
        id=post.id,
        image_url=image_url,
        photo_missing=(
            post.post_type == InstagramPostType.SABIAS_QUE.value and not post.media_path
        ),
    )


# --- Scheduling / publication ---


@router.patch("/posts/{post_id}/schedule", response_model=InstagramPublishStateResponse)
def schedule_instagram_post(
    post_id: int = Path(..., description="Instagram post ID"),
    body: InstagramScheduleRequest = ...,
    db: Session = Depends(get_db),
):
    """Queue an approved, rendered post for publication at ``scheduled_at``.

    A past timestamp means "due now — next scheduler tick". Re-scheduling a
    FAILED post clears its error (that is the operator's retry path).
    """
    post = _get_post(db, post_id)
    _require_unpublished(post)
    if post.status != ContentStatus.APPROVED.value:
        raise HTTPException(status_code=400, detail="Only approved posts can be scheduled")
    if not post.image_url:
        raise HTTPException(status_code=400, detail="Render the post image before scheduling")

    post.scheduled_at = body.scheduled_at
    post.publish_error = None
    post.publishing_started_at = None
    db.commit()
    db.refresh(post)
    return _publish_state(post)


@router.patch("/posts/{post_id}/unschedule", response_model=InstagramPublishStateResponse)
def unschedule_instagram_post(
    post_id: int = Path(..., description="Instagram post ID"),
    db: Session = Depends(get_db),
):
    """Remove a post from the publication queue."""
    post = _get_post(db, post_id)
    _require_unpublished(post)
    post.scheduled_at = None
    post.publish_error = None
    post.publishing_started_at = None
    db.commit()
    db.refresh(post)
    return _publish_state(post)


@router.post("/posts/{post_id}/publish-now", response_model=InstagramPublishStateResponse)
def publish_instagram_post_now(
    post_id: int = Path(..., description="Instagram post ID"),
    db: Session = Depends(get_db),
):
    """Publish immediately via the Graph API (sync — network call in-request).

    503 while credentials are unconfigured. On failure the row is parked with
    ``publish_error`` and the detail is surfaced; publish-now clears a prior
    error first (it *is* the retry).
    """
    post = _get_post(db, post_id)
    _require_unpublished(post)
    if post.status != ContentStatus.APPROVED.value:
        raise HTTPException(status_code=400, detail="Only approved posts can be published")
    if not post.image_url:
        raise HTTPException(status_code=400, detail="Render the post image before publishing")

    with InstagramPublisher() as publisher:
        if not publisher.enabled:
            raise HTTPException(status_code=503, detail="Instagram publishing is not configured")
        post.publish_error = None
        post.publishing_started_at = datetime.now(UTC)
        db.commit()
        publish_post(db, post, publisher)

    db.refresh(post)
    return _publish_state(post)
