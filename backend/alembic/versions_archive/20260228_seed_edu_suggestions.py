"""Seed 7 excursion-based edu suggestions.

Revision ID: 20260228_seed_edu_suggestions
Revises: 20260228_edu_content_tables
Create Date: 2026-02-28
"""

from alembic import op
from sqlalchemy import text

revision = "20260228_seed_edu_suggestions"
down_revision = "20260228_edu_content_tables"


EXCURSION_SUGGESTIONS = [
    {
        "excursion_key": "casco-antiguo-plazas",
        "title": "Casco Antiguo: Plazas and Churches",
        "grade_bands": ["3rd-6th", "7th-8th", "9th-12th"],
    },
    {
        "excursion_key": "casco-antiguo-museos",
        "title": "Casco Antiguo Museum Circuit",
        "grade_bands": ["3rd-6th", "7th-8th", "9th-12th"],
    },
    {
        "excursion_key": "panama-viejo",
        "title": "Panama Viejo Archeological Complex",
        "grade_bands": ["3rd-6th", "7th-8th", "9th-12th"],
    },
    {
        "excursion_key": "biomuseo",
        "title": "Museum of Biodiversity",
        "grade_bands": ["3rd-6th", "7th-8th", "9th-12th"],
    },
    {
        "excursion_key": "miraflores",
        "title": "Miraflores Visitor Center",
        "grade_bands": ["4th-6th"],
    },
    {
        "excursion_key": "inter-religiosa-abrahamica",
        "title": "Panama's Abrahamic Religions Journey",
        "grade_bands": ["9th-12th"],
    },
    {
        "excursion_key": "inter-religiosa-oriental",
        "title": "Panama's Eastern Religions Journey",
        "grade_bands": ["9th-12th"],
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    for s in EXCURSION_SUGGESTIONS:
        bands = "{" + ",".join(f'"{b}"' for b in s["grade_bands"]) + "}"
        conn.execute(
            text(
                "INSERT INTO edu_suggestions (excursion_key, title, grade_bands, status) "
                "VALUES (:key, :title, :bands, 'APPROVED')"
            ),
            {"key": s["excursion_key"], "title": s["title"], "bands": bands},
        )


def downgrade() -> None:
    keys = [s["excursion_key"] for s in EXCURSION_SUGGESTIONS]
    op.get_bind().execute(
        text("DELETE FROM edu_suggestions WHERE excursion_key = ANY(:keys)"),
        {"keys": keys},
    )
