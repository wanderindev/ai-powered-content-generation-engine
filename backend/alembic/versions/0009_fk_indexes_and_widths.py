"""FK indexes and string-width canonicalization (#25, #27).

Revision ID: 0009_fk_indexes_and_widths
Revises: 0008_booking_status_log_paypal_event_id
Create Date: 2026-05-31

Two mechanical schema-cleanup deltas, safe and additive:

#25 — Postgres does not auto-index the referencing side of a foreign key, so
most FK columns were doing sequential scans on JOINs and ON DELETE CASCADE.
This adds btree indexes on the FK columns that were still unindexed, using the
``ix_<table>_<column>`` convention. Columns already covered by an existing index
or as the leading edge of a composite unique constraint are intentionally
skipped (see the baseline schema): ``articles.category_id/research_id/
series_parent_id``, ``article_suggestions.category_id``, ``research.suggestion_id``
(unique), ``zone_attraction_rates.zone_id``, ``zone_transport_rates.from_zone_id``
and ``tour_attractions.tour_id`` (composite-UQ leading edges).

#27 — Canonical string widths. All ``email`` columns standardise on
``String(254)`` (RFC 5321 max); only the two undersized outliers are migrated
(``users.email`` 120→254 — fixes admin logins refusing long addresses; and
``booking_status_log.changed_by`` 100→254). Entity ``name``/``name_es`` columns
standardise on ``String(200)`` (taxonomies, categories, zones; tours/hotels are
already 200). ``Tag`` is genuinely smaller — both ``name`` and ``name_es`` to
``String(100)``. The two narrowings (categories/taxonomies name 255→200,
tags.name_es 255→100) were verified against prod max lengths before writing this.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009_fk_indexes_and_widths"
down_revision = "0008_booking_status_log_paypal_event_id"
branch_labels = None
depends_on = None

# (index_name, table, column) — FK columns that were unindexed before this revision.
_FK_INDEXES = [
    ("ix_articles_feature_image_id", "articles", "feature_image_id"),
    ("ix_articles_approved_by_id", "articles", "approved_by_id"),
    ("ix_article_suggestions_approved_by_id", "article_suggestions", "approved_by_id"),
    ("ix_research_approved_by_id", "research", "approved_by_id"),
    ("ix_tags_approved_by_id", "tags", "approved_by_id"),
    ("ix_tours_zone_id", "tours", "zone_id"),
    ("ix_bookings_order_id", "bookings", "order_id"),
    ("ix_bookings_tour_id", "bookings", "tour_id"),
    ("ix_bookings_hotel_id", "bookings", "hotel_id"),
    ("ix_booking_status_log_booking_id", "booking_status_log", "booking_id"),
    ("ix_booking_status_log_order_id", "booking_status_log", "order_id"),
    ("ix_magic_links_order_id", "magic_links", "order_id"),
    ("ix_magic_links_booking_id", "magic_links", "booking_id"),
    ("ix_edu_research_suggestion_id", "edu_research", "suggestion_id"),
    ("ix_edu_materials_research_id", "edu_materials", "research_id"),
    ("ix_edu_materials_blog_research_id", "edu_materials", "blog_research_id"),
    ("ix_edu_material_files_material_id", "edu_material_files", "material_id"),
    ("ix_zone_attraction_rates_attraction_id", "zone_attraction_rates", "attraction_id"),
    ("ix_zone_transport_rates_to_zone_id", "zone_transport_rates", "to_zone_id"),
    ("ix_tour_attractions_attraction_id", "tour_attractions", "attraction_id"),
]

# (table, column, new_len, old_len) — string-width canonicalisation.
_WIDTHS = [
    ("users", "email", 254, 120),
    ("booking_status_log", "changed_by", 254, 100),
    ("taxonomies", "name", 200, 255),
    ("taxonomies", "name_es", 200, 100),
    ("categories", "name", 200, 255),
    ("categories", "name_es", 200, 100),
    ("zones", "name", 200, 100),
    ("zones", "name_es", 200, 100),
    ("tags", "name", 100, 50),
    ("tags", "name_es", 100, 255),
]


def upgrade() -> None:
    for name, table, column in _FK_INDEXES:
        op.create_index(op.f(name), table, [column], unique=False)

    for table, column, new_len, old_len in _WIDTHS:
        op.alter_column(
            table,
            column,
            type_=sa.String(length=new_len),
            existing_type=sa.String(length=old_len),
        )


def downgrade() -> None:
    for table, column, new_len, old_len in _WIDTHS:
        op.alter_column(
            table,
            column,
            type_=sa.String(length=old_len),
            existing_type=sa.String(length=new_len),
        )

    for name, table, _column in _FK_INDEXES:
        op.drop_index(op.f(name), table_name=table)
