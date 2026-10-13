"""Admin API endpoints for translation and content management."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import validate_admin_token
from app.core.database import get_db
from app.models.article import Article
from app.models.category import Category, Taxonomy
from app.schemas.series import (
    SeriesSectionResult,
    SeriesSectionStatus,
    SuggestionTranslationResponse,
)
from app.schemas.translation import (
    BulkTranslationResponse,
    CategoryTranslationResponse,
    TaxonomyTranslationResponse,
    TranslationResponse,
)
from app.services.series_sections import SeriesSectionGenerator
from app.services.translation import get_translation_service

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(validate_admin_token)],
)


def _get_article_or_404(article_id: int, db: Session) -> Article:
    """Get article by ID or raise 404."""
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")
    return article


def _ensure_translation_service():
    """Get translation service or raise 503 if not configured."""
    service = get_translation_service()
    if not service:
        raise HTTPException(
            status_code=503,
            detail="Translation service not configured. Check DEEPL_API environment variable.",
        )
    return service


@router.post("/translate/article/{article_id}/title", response_model=TranslationResponse)
async def translate_article_title(
    article_id: int,
    db: Session = Depends(get_db),
) -> TranslationResponse:
    """
    Translate article title from English to Spanish.

    Saves the translated title to the article's title_es field.
    """
    service = _ensure_translation_service()
    article = _get_article_or_404(article_id, db)

    if not article.title:
        raise HTTPException(status_code=400, detail="Article has no title to translate")

    translated = await service.translate_text(article.title)

    # Save to database
    article.title_es = translated
    db.commit()

    return TranslationResponse(
        success=True,
        field="title",
        original=article.title,
        translated=translated,
        article_id=article_id,
    )


@router.post("/translate/article/{article_id}/excerpt", response_model=TranslationResponse)
async def translate_article_excerpt(
    article_id: int,
    db: Session = Depends(get_db),
) -> TranslationResponse:
    """
    Translate article excerpt from English to Spanish.

    Saves the translated excerpt to the article's excerpt_es field.
    """
    service = _ensure_translation_service()
    article = _get_article_or_404(article_id, db)

    if not article.excerpt:
        raise HTTPException(status_code=400, detail="Article has no excerpt to translate")

    translated = await service.translate_text(article.excerpt)

    # Save to database
    article.excerpt_es = translated
    db.commit()

    return TranslationResponse(
        success=True,
        field="excerpt",
        original=article.excerpt,
        translated=translated,
        article_id=article_id,
    )


@router.post("/translate/article/{article_id}/content", response_model=TranslationResponse)
async def translate_article_content(
    article_id: int,
    db: Session = Depends(get_db),
) -> TranslationResponse:
    """
    Translate article content (Markdown) from English to Spanish.

    Uses Round-Trip Middleware to preserve Markdown formatting:
    1. Convert MD → HTML
    2. Protect code blocks
    3. Translate with DeepL (tag_handling=html)
    4. Convert HTML → MD

    Saves the translated content to the article's content_es field.
    """
    service = _ensure_translation_service()
    article = _get_article_or_404(article_id, db)

    if not article.content:
        raise HTTPException(status_code=400, detail="Article has no content to translate")

    translated = await service.translate_markdown(article.content)

    # Save to database
    article.content_es = translated
    db.commit()

    return TranslationResponse(
        success=True,
        field="content",
        original=article.content,
        translated=translated,
        article_id=article_id,
    )


@router.post("/translate/taxonomy/{taxonomy_id}", response_model=TaxonomyTranslationResponse)
async def translate_taxonomy(
    taxonomy_id: int,
    db: Session = Depends(get_db),
) -> TaxonomyTranslationResponse:
    """
    Translate taxonomy name and description from English to Spanish.

    Saves the translated values to the taxonomy's name_es and description_es fields.
    """
    service = _ensure_translation_service()

    taxonomy = db.query(Taxonomy).filter(Taxonomy.id == taxonomy_id).first()
    if not taxonomy:
        raise HTTPException(status_code=404, detail=f"Taxonomy {taxonomy_id} not found")

    if not taxonomy.name:
        raise HTTPException(status_code=400, detail="Taxonomy has no name to translate")

    # Translate name
    name_translated = await service.translate_text(taxonomy.name)
    taxonomy.name_es = name_translated

    # Translate description if present
    description_translated = None
    if taxonomy.description:
        description_translated = await service.translate_text(taxonomy.description)
        taxonomy.description_es = description_translated

    db.commit()

    return TaxonomyTranslationResponse(
        taxonomy_id=taxonomy_id,
        name_original=taxonomy.name,
        name_translated=name_translated,
        description_original=taxonomy.description,
        description_translated=description_translated,
    )


@router.post("/translate/category/{category_id}", response_model=CategoryTranslationResponse)
async def translate_category(
    category_id: int,
    db: Session = Depends(get_db),
) -> CategoryTranslationResponse:
    """
    Translate category name and description from English to Spanish.

    Saves the translated values to the category's name_es and description_es fields.
    """
    service = _ensure_translation_service()

    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail=f"Category {category_id} not found")

    if not category.name:
        raise HTTPException(status_code=400, detail="Category has no name to translate")

    # Translate name
    name_translated = await service.translate_text(category.name)
    category.name_es = name_translated

    # Translate description if present
    description_translated = None
    if category.description:
        description_translated = await service.translate_text(category.description)
        category.description_es = description_translated

    db.commit()

    return CategoryTranslationResponse(
        category_id=category_id,
        name_original=category.name,
        name_translated=name_translated,
        description_original=category.description,
        description_translated=description_translated,
    )


@router.post("/translate/taxonomies/all", response_model=BulkTranslationResponse)
async def translate_all_taxonomies(
    db: Session = Depends(get_db),
) -> BulkTranslationResponse:
    """
    Translate all taxonomies that are missing Spanish translations.

    Only translates taxonomies where name_es is NULL.
    """
    service = _ensure_translation_service()

    taxonomies = db.query(Taxonomy).filter(Taxonomy.name_es.is_(None)).all()

    translated_count = 0
    for taxonomy in taxonomies:
        if taxonomy.name:
            taxonomy.name_es = await service.translate_text(taxonomy.name)
            if taxonomy.description:
                taxonomy.description_es = await service.translate_text(taxonomy.description)
            translated_count += 1

    db.commit()

    skipped = len(taxonomies) - translated_count

    return BulkTranslationResponse(
        translated_count=translated_count,
        skipped_count=skipped,
        message=f"Translated {translated_count} taxonomies, skipped {skipped}",
    )


@router.post("/translate/categories/all", response_model=BulkTranslationResponse)
async def translate_all_categories(
    db: Session = Depends(get_db),
) -> BulkTranslationResponse:
    """
    Translate all categories that are missing Spanish translations.

    Only translates categories where name_es is NULL.
    """
    service = _ensure_translation_service()

    categories = db.query(Category).filter(Category.name_es.is_(None)).all()

    translated_count = 0
    for category in categories:
        if category.name:
            category.name_es = await service.translate_text(category.name)
            if category.description:
                category.description_es = await service.translate_text(category.description)
            translated_count += 1

    db.commit()

    skipped = len(categories) - translated_count

    return BulkTranslationResponse(
        translated_count=translated_count,
        skipped_count=skipped,
        message=f"Translated {translated_count} categories, skipped {skipped}",
    )


@router.post(
    "/translate/suggestion/{suggestion_id}/title",
    response_model=SuggestionTranslationResponse,
)
async def translate_suggestion_title(
    suggestion_id: int,
    db: Session = Depends(get_db),
) -> SuggestionTranslationResponse:
    """
    Translate article suggestion title from English to Spanish.

    This is the series title that appears in the "About this Article" section.
    """
    service = _ensure_translation_service()

    # Get suggestion
    query = text("SELECT id, title, title_es FROM article_suggestions WHERE id = :id")
    result = db.execute(query, {"id": suggestion_id}).first()

    if not result:
        raise HTTPException(status_code=404, detail=f"Suggestion {suggestion_id} not found")

    if not result[1]:
        raise HTTPException(status_code=400, detail="Suggestion has no title")

    # Translate
    translated = await service.translate_text(result[1])

    # Save to database
    update_query = text("UPDATE article_suggestions SET title_es = :title_es WHERE id = :id")
    db.execute(update_query, {"id": suggestion_id, "title_es": translated})
    db.commit()

    return SuggestionTranslationResponse(
        success=True,
        suggestion_id=suggestion_id,
        original=result[1],
        translated=translated,
    )


@router.get(
    "/articles/series/{series_parent_id}/status",
    response_model=SeriesSectionStatus,
)
async def get_series_section_status(
    series_parent_id: int,
    db: Session = Depends(get_db),
) -> SeriesSectionStatus:
    """
    Check if a series is ready for section generation.

    Returns status including:
    - Whether suggestion title is translated
    - How many articles are translated
    - Whether sections can be generated
    """
    generator = SeriesSectionGenerator(db)
    can_generate, message, suggestion, articles = generator.can_generate_sections(series_parent_id)

    if not suggestion:
        raise HTTPException(status_code=404, detail=message)

    translated_count = sum(1 for a in articles if a.has_content_es)

    return SeriesSectionStatus(
        series_parent_id=series_parent_id,
        suggestion_id=suggestion.id,
        suggestion_title=suggestion.title,
        suggestion_title_es=suggestion.title_es,
        total_articles=len(articles),
        translated_articles=translated_count,
        can_generate=can_generate,
        message=message,
    )


@router.post(
    "/articles/series/{series_parent_id}/generate-sections",
    response_model=SeriesSectionResult,
)
async def generate_series_sections(
    series_parent_id: int,
    db: Session = Depends(get_db),
) -> SeriesSectionResult:
    """
    Generate About/Continue Reading sections for all articles in a series.

    Prerequisites:
    - All articles in series must have content_es (translated)
    - article_suggestions.title_es must exist

    Actions:
    - Generates sections for both content and content_es
    - Prepends About section, appends Continue Reading section
    """
    generator = SeriesSectionGenerator(db)
    success, message, articles_updated = generator.generate_sections_for_series(series_parent_id)

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return SeriesSectionResult(
        success=success,
        articles_updated=articles_updated,
        message=message,
    )
