from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.article import Article
from app.models.category import Category, Taxonomy
from app.schemas.article import ArticleSearchResult

router = APIRouter(prefix="/search", tags=["search"])

# Taxonomy ID for Notable Figures (bio articles to exclude)
EXCLUDED_TAXONOMY_ID = 5


@router.get("", response_model=list[ArticleSearchResult])
def search_articles(
    q: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """
    Search articles by title, content, or excerpt.

    Uses PostgreSQL full-text search when available,
    falls back to ILIKE for simple matching.
    """
    search_term = f"%{q}%"

    articles = (
        db.query(Article)
        .join(Category)
        .join(Taxonomy)
        .filter(Article.status == "APPROVED")
        .filter(Taxonomy.id != EXCLUDED_TAXONOMY_ID)
        .filter(
            or_(
                Article.title.ilike(search_term),
                Article.content.ilike(search_term),
                Article.excerpt.ilike(search_term),
            )
        )
        .order_by(
            # Prioritize title matches
            case((Article.title.ilike(search_term), 1), else_=2),
            Article.published_at.desc().nullsfirst(),
        )
        .limit(limit)
        .all()
    )

    return [
        ArticleSearchResult(
            id=a.id,
            title=a.title,
            slug=a.slug,
            excerpt=a.excerpt,
            published_at=a.published_at,
            category_name=a.category.name,
            highlight=_extract_highlight(a.content or "", q),
        )
        for a in articles
    ]


def _extract_highlight(content: str, query: str, context_chars: int = 100) -> str:
    """
    Extract a snippet of content around the search term.
    """
    if not content or not query:
        return ""

    query_lower = query.lower()
    content_lower = content.lower()

    pos = content_lower.find(query_lower)
    if pos == -1:
        # Return first part of content if no match found
        return content[: context_chars * 2] + "..." if len(content) > context_chars * 2 else content

    # Extract context around the match
    start = max(0, pos - context_chars)
    end = min(len(content), pos + len(query) + context_chars)

    snippet = content[start:end]

    # Add ellipsis if needed
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."

    return snippet
