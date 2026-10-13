from pydantic import BaseModel, ConfigDict

from app.utils.slug import generate_slug


class TaxonomyBase(BaseModel):
    id: int
    name: str
    description: str | None = None

    model_config = ConfigDict(from_attributes=True)


class CategoryBase(BaseModel):
    id: int
    name: str
    description: str | None = None
    taxonomy_id: int
    article_count: int = 0

    @property
    def slug(self) -> str:
        return generate_slug(self.name)

    model_config = ConfigDict(from_attributes=True)


class CategoryWithArticles(CategoryBase):
    """Category with its articles for category listing pages."""

    taxonomy_name: str
    articles: list["ArticleBase"] = []


# Import here to avoid circular imports
from app.schemas.article import ArticleBase  # noqa: E402

CategoryWithArticles.model_rebuild()
