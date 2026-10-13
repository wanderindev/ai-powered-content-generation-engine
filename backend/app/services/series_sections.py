"""
Service for generating article series sections.

Generates "About this Article" and "Continue Reading" sections for articles
that are part of a series, with proper bilingual support and links.
"""

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.article_suggestion import ArticleSuggestion
from app.models.research import Research
from app.utils.slug import generate_slug

# Marker strings used by the about-this-article header. These are the
# deterministic signal that an article has already been processed by
# generate_sections_for_series. Used both by the in-service idempotency
# refusal below and by scripts/one_off/cleanup_articles_39_40.py.
ABOUT_MARKER_EN = "**About this Article**"
ABOUT_MARKER_ES = "**Acerca de este artículo**"


@dataclass
class SeriesArticleInfo:
    """Info about an article in a series."""

    id: int
    title: str
    title_es: str | None
    slug: str
    slug_es: str
    series_order: int
    has_content_es: bool


@dataclass
class SuggestionInfo:
    """Info about the article suggestion (series parent)."""

    id: int
    title: str
    title_es: str | None


class SeriesSectionGenerator:
    """Generates About/Continue Reading sections for article series."""

    def __init__(self, db: Session):
        self.db = db

    def get_series_info(
        self, series_parent_id: int
    ) -> tuple[SuggestionInfo | None, list[SeriesArticleInfo]]:
        """
        Get suggestion and articles info for a series.

        Returns:
            Tuple of (suggestion_info, list of article infos)
        """
        # Get the series parent article to find the research/suggestion
        parent = self.db.query(Article).filter(Article.id == series_parent_id).first()
        if not parent:
            return None, []

        # Get suggestion via research
        suggestion_row = (
            self.db.query(ArticleSuggestion)
            .join(Research, Research.suggestion_id == ArticleSuggestion.id)
            .filter(Research.id == parent.research_id)
            .first()
        )

        if not suggestion_row:
            return None, []

        suggestion = SuggestionInfo(
            id=suggestion_row.id,
            title=suggestion_row.title,
            title_es=suggestion_row.title_es,
        )

        # Get all articles in the series (parent + children)
        article_rows = (
            self.db.query(Article)
            .filter(
                or_(
                    Article.id == series_parent_id,
                    Article.series_parent_id == series_parent_id,
                )
            )
            .order_by(func.coalesce(Article.series_order, 1))
            .all()
        )

        articles = [
            SeriesArticleInfo(
                id=row.id,
                title=row.title,
                title_es=row.title_es,
                slug=generate_slug(row.title),
                slug_es=generate_slug(row.title_es) if row.title_es else generate_slug(row.title),
                series_order=row.series_order if row.series_order else 1,
                has_content_es=row.content_es is not None,
            )
            for row in article_rows
        ]

        return suggestion, articles

    def can_generate_sections(
        self, series_parent_id: int
    ) -> tuple[bool, str, SuggestionInfo | None, list[SeriesArticleInfo]]:
        """
        Check if sections can be generated for a series.

        Returns:
            Tuple of (can_generate, message, suggestion_info, articles)
        """
        suggestion, articles = self.get_series_info(series_parent_id)

        if not suggestion:
            return False, "Series not found or no suggestion linked", None, []

        if not articles:
            return False, "No articles found in series", suggestion, []

        # Check if suggestion has Spanish title
        if not suggestion.title_es:
            return (
                False,
                f"Suggestion '{suggestion.title}' has not been translated",
                suggestion,
                articles,
            )

        # Check if all articles have Spanish content
        untranslated = [a for a in articles if not a.has_content_es]
        if untranslated:
            titles = ", ".join([a.title[:30] + "..." for a in untranslated[:3]])
            return (
                False,
                f"{len(untranslated)} articles not translated: {titles}",
                suggestion,
                articles,
            )

        return True, "Ready to generate sections", suggestion, articles

    def generate_about_section(
        self,
        current_article: SeriesArticleInfo,
        all_articles: list[SeriesArticleInfo],
        suggestion: SuggestionInfo,
        language: str = "en",
    ) -> str:
        """Generate the About this Article section."""
        total_parts = len(all_articles)

        if language == "es":
            header = ABOUT_MARKER_ES
            intro = f"Este artículo es la parte {current_article.series_order} de una serie de {total_parts} partes sobre {suggestion.title_es}."
            list_header = "Artículos de esta serie:"
            here_text = "(Estás aquí)"
            base_url = "/es/articulos"
        else:
            header = ABOUT_MARKER_EN
            intro = f"This article is part {current_article.series_order} of a {total_parts}-part series about {suggestion.title}."
            list_header = "Articles in this series:"
            here_text = "(You are here)"
            base_url = "/en/articles"

        lines = [header, "", intro, "", list_header, ""]

        for article in all_articles:
            title = article.title_es if language == "es" else article.title
            slug = article.slug_es if language == "es" else article.slug

            if article.id == current_article.id:
                # Current article - no link
                lines.append(f"- {title} {here_text}")
            else:
                # Link to other article
                lines.append(f"- [{title}]({base_url}/{slug})")

        lines.extend(["", "---", ""])

        return "\n".join(lines)

    def generate_continue_reading_section(
        self,
        current_article: SeriesArticleInfo,
        next_article: SeriesArticleInfo | None,
        language: str = "en",
    ) -> str:
        """Generate the Continue Reading section."""
        if not next_article:
            return ""

        if language == "es":
            header = "**Continuar leyendo**"
            intro = (
                f"¿Listo para la siguiente parte? Continúa a la Parte {next_article.series_order}:"
            )
            title = next_article.title_es
            slug = next_article.slug_es
            base_url = "/es/articulos"
        else:
            header = "**Continue Reading**"
            intro = f"Ready for the next part? Continue to Part {next_article.series_order}:"
            title = next_article.title
            slug = next_article.slug
            base_url = "/en/articles"

        lines = [
            "",
            "",  # Blank line before --- to prevent markdown rendering issues
            "---",
            "",
            header,
            "",
            f"{intro} [{title}]({base_url}/{slug})",
        ]

        return "\n".join(lines)

    def generate_sections_for_series(self, series_parent_id: int) -> tuple[bool, str, int]:
        """
        Generate sections for all articles in a series.

        Raises HTTPException(400) if any article in the series already
        carries an about-marker — the refuse-on-rerun policy for #99 that
        matches the convention of the other regeneration-guarded flows
        (outline-gen, write_article, translate_article).

        Returns:
            Tuple of (success, message, articles_updated_count) for the
            non-idempotency failure modes (series-not-found, untranslated)
            and on success.
        """
        can_generate, message, suggestion, articles = self.can_generate_sections(series_parent_id)

        if not can_generate:
            return False, message, 0

        # Pre-load the Article ORM rows once: used for both the safety-net
        # idempotency check and the per-article mutation below. Same filter
        # as get_series_info's article query, but returns the full ORM rows
        # so we can mutate content/content_es.
        article_rows: dict[int, Article] = {
            row.id: row
            for row in self.db.query(Article)
            .filter(
                or_(
                    Article.id == series_parent_id,
                    Article.series_parent_id == series_parent_id,
                )
            )
            .all()
        }

        # Refuse-on-rerun policy for #99. Check ALL articles before mutating
        # any. The continue-marker is not checked: it is conditionally
        # absent for the last article in the series and would produce false
        # positives. The about-marker is the deterministic signal.
        # HTTPException(400) matches the surface of the other
        # regeneration-guarded flows in this pipeline (write_article,
        # translate_article, generate-outlines).
        for info in articles:
            row = article_rows.get(info.id)
            if row is None:
                continue
            if row.content and ABOUT_MARKER_EN in row.content:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Article {row.id} already has series sections "
                        f"(EN about-marker found); refusing to regenerate "
                        f"(see #99)."
                    ),
                )
            if row.content_es and ABOUT_MARKER_ES in row.content_es:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Article {row.id} already has series sections "
                        f"(ES about-marker found); refusing to regenerate "
                        f"(see #99)."
                    ),
                )

        articles_updated = 0

        for i, info in enumerate(articles):
            # Find next article (if any)
            next_article = articles[i + 1] if i + 1 < len(articles) else None

            # Generate English sections
            about_en = self.generate_about_section(info, articles, suggestion, "en")
            continue_en = self.generate_continue_reading_section(info, next_article, "en")

            # Generate Spanish sections
            about_es = self.generate_about_section(info, articles, suggestion, "es")
            continue_es = self.generate_continue_reading_section(info, next_article, "es")

            # Update the article via ORM. This restores the updated_at
            # onupdate trigger that the prior raw-SQL UPDATE bypassed.
            row = article_rows.get(info.id)
            if row is None:
                continue
            row.content = about_en + (row.content or "") + continue_en
            row.content_es = about_es + (row.content_es or "") + continue_es
            self.db.add(row)
            articles_updated += 1

        self.db.commit()

        return True, f"Generated sections for {articles_updated} articles", articles_updated
