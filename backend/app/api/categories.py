from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.article import Article
from app.models.category import EXCLUDED_TAXONOMY_ID, Category, Taxonomy
from app.schemas.category import CategoryBase, TaxonomyBase
from app.utils.slug import generate_slug

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryBase])
def list_categories(
    taxonomy_id: int = None,
    db: Session = Depends(get_db),
):
    """
    List all categories with article counts.

    - Excludes Notable Figures taxonomy
    - Can filter by taxonomy_id
    """
    query = (
        db.query(
            Category,
            func.count(Article.id).filter(Article.status == "APPROVED").label("article_count"),
        )
        .outerjoin(Article)
        .filter(Category.taxonomy_id != EXCLUDED_TAXONOMY_ID)
        .group_by(Category.id)
    )

    if taxonomy_id:
        query = query.filter(Category.taxonomy_id == taxonomy_id)

    results = query.all()

    return [
        CategoryBase(
            id=cat.id,
            name=cat.name,
            description=cat.description,
            taxonomy_id=cat.taxonomy_id,
            article_count=count,
        )
        for cat, count in results
    ]


@router.get("/with-content", response_model=list[CategoryBase])
def list_categories_with_content(
    db: Session = Depends(get_db),
):
    """
    List only categories that have approved articles.
    """
    query = (
        db.query(Category, func.count(Article.id).label("article_count"))
        .join(Article)
        .filter(Article.status == "APPROVED")
        .filter(Category.taxonomy_id != EXCLUDED_TAXONOMY_ID)
        .group_by(Category.id)
        .having(func.count(Article.id) > 0)
    )

    results = query.all()

    return [
        CategoryBase(
            id=cat.id,
            name=cat.name,
            description=cat.description,
            taxonomy_id=cat.taxonomy_id,
            article_count=count,
        )
        for cat, count in results
    ]


@router.get("/taxonomies", response_model=list[TaxonomyBase])
def list_taxonomies(
    db: Session = Depends(get_db),
):
    """List all taxonomies except Notable Figures."""
    taxonomies = db.query(Taxonomy).filter(Taxonomy.id != EXCLUDED_TAXONOMY_ID).all()

    return [
        TaxonomyBase(
            id=t.id,
            name=t.name,
            description=t.description,
        )
        for t in taxonomies
    ]


@router.get("/{category_slug}")
def get_category_by_slug(
    category_slug: str,
    db: Session = Depends(get_db),
):
    """Get category by slug with taxonomy info."""
    categories = (
        db.query(Category).join(Taxonomy).filter(Category.taxonomy_id != EXCLUDED_TAXONOMY_ID).all()
    )

    # Find matching category by computed slug
    category = next((c for c in categories if generate_slug(c.name) == category_slug), None)

    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    # Count articles
    article_count = (
        db.query(func.count(Article.id))
        .filter(Article.category_id == category.id)
        .filter(Article.status == "APPROVED")
        .scalar()
    )

    return {
        "id": category.id,
        "name": category.name,
        "slug": generate_slug(category.name),
        "description": category.description,
        "taxonomy_id": category.taxonomy_id,
        "taxonomy_name": category.taxonomy.name,
        "article_count": article_count,
    }
