"""Persist article slugs (EN + ES) as unique columns (#474).

``Article.slug`` / ``Article.slug_es`` were Python properties recomputed from the
titles on every read, so no slug could be matched in SQL and a title edit moved a
live URL. This adds the columns, backfills them with the same ``generate_slug``
the properties used, and locks them down with per-column unique constraints.

The backfill deliberately **fails loudly** on any pre-existing collision rather
than de-duplicating with a suffix: two articles whose titles slugify identically
already resolved arbitrarily under the old Python scan, and which one wins is an
editorial decision. The scan runs *before* the constraints are created so the
error message names every offending article instead of surfacing as an opaque
``UniqueViolation`` on whichever row Postgres happened to hit first.
"""

import sqlalchemy as sa
from alembic import op

from app.utils.slug import generate_slug

# revision identifiers, used by Alembic.
revision = "0029_article_slugs"
down_revision = "0028_panama_viejo_507_tour"
branch_labels = None
depends_on = None

_SLUG_CONSTRAINT = "uq_articles_slug"
_SLUG_ES_CONSTRAINT = "uq_articles_slug_es"


def _check_collisions(rows: list[tuple[int, str, str | None]]) -> None:
    """Raise if the computed slugs cannot satisfy the constraints being added.

    ``rows`` is ``(id, slug, slug_es)``. A row whose ``slug`` equals its own
    ``slug_es`` is fine and common — the ``slug = :s OR slug_es = :s`` lookup
    resolves both to the same article.
    """
    problems: list[str] = []

    empty = [str(article_id) for article_id, slug, _ in rows if not slug]
    if empty:
        problems.append(f"empty English slug for article(s) {', '.join(empty)}")

    by_slug: dict[str, list[int]] = {}
    by_slug_es: dict[str, list[int]] = {}
    for article_id, slug, slug_es in rows:
        by_slug.setdefault(slug, []).append(article_id)
        if slug_es:
            by_slug_es.setdefault(slug_es, []).append(article_id)

    for slug, ids in sorted(by_slug.items()):
        if len(ids) > 1:
            problems.append(f"duplicate slug '{slug}' on articles {sorted(ids)}")
    for slug, ids in sorted(by_slug_es.items()):
        if len(ids) > 1:
            problems.append(f"duplicate slug_es '{slug}' on articles {sorted(ids)}")

    # Cross-column: one article's Spanish slug matching a *different* article's
    # English slug makes the by-slug lookup ambiguous even though no single
    # unique constraint would reject it.
    for slug, es_ids in sorted(by_slug_es.items()):
        en_ids = [i for i in by_slug.get(slug, []) if i not in es_ids]
        if en_ids:
            problems.append(
                f"slug_es '{slug}' on articles {sorted(es_ids)} collides with the "
                f"English slug of articles {sorted(en_ids)}"
            )

    if problems:
        raise RuntimeError(
            "Cannot persist article slugs — retitle the offending articles first:\n  "
            + "\n  ".join(problems)
        )


def upgrade() -> None:
    op.add_column("articles", sa.Column("slug", sa.String(255), nullable=True))
    op.add_column("articles", sa.Column("slug_es", sa.String(255), nullable=True))

    conn = op.get_bind()
    articles = conn.execute(sa.text("SELECT id, title, title_es FROM articles")).fetchall()

    computed = [
        (
            row.id,
            generate_slug(row.title or ""),
            generate_slug(row.title_es) if row.title_es else None,
        )
        for row in articles
    ]

    # Abort before any constraint exists; the transaction rolls the columns back.
    _check_collisions(computed)

    update = sa.text("UPDATE articles SET slug = :slug, slug_es = :slug_es WHERE id = :id")
    for article_id, slug, slug_es in computed:
        conn.execute(update, {"id": article_id, "slug": slug, "slug_es": slug_es})

    op.alter_column("articles", "slug", nullable=False)
    op.create_unique_constraint(_SLUG_CONSTRAINT, "articles", ["slug"])
    op.create_unique_constraint(_SLUG_ES_CONSTRAINT, "articles", ["slug_es"])


def downgrade() -> None:
    op.drop_constraint(_SLUG_CONSTRAINT, "articles", type_="unique")
    op.drop_constraint(_SLUG_ES_CONSTRAINT, "articles", type_="unique")
    op.drop_column("articles", "slug_es")
    op.drop_column("articles", "slug")
