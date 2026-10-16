"""Add the edu pipeline job types to the crawl_jobs job_type vocabulary (#346).

Guide generation, slide rendering, narration audio and video assembly move off
the request path onto the #277 background-job table: a full deck's
assemble-video runs ~15-20 minutes and 504'd at the api. vhost's 300s proxy
ceiling. The stable constraint name (issue #24 convention) makes this a clean
drop-and-recreate, exactly as 0021 did for FACT_CHECK.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0034_edu_job_types"
down_revision = "0033_grammar_review_diffs"
branch_labels = None
depends_on = None

_OLD_VALUES = ("WIKIMEDIA", "LOC", "SCORE", "FACT_CHECK")
_NEW_VALUES = (
    *_OLD_VALUES,
    "EDU_GENERATE_GUIDES",
    "EDU_RENDER_SLIDES",
    "EDU_GENERATE_AUDIO",
    "EDU_ASSEMBLE_VIDEO",
)

_CONSTRAINT = "ck_crawl_jobs_job_type"


def _in_clause(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"job_type IN ({allowed})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "crawl_jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "crawl_jobs", _in_clause(_NEW_VALUES))


def downgrade() -> None:
    # Rows of the new types would violate the narrowed constraint.
    op.execute(
        "DELETE FROM crawl_jobs WHERE job_type IN "
        "('EDU_GENERATE_GUIDES', 'EDU_RENDER_SLIDES', 'EDU_GENERATE_AUDIO', 'EDU_ASSEMBLE_VIDEO')"
    )
    op.drop_constraint(_CONSTRAINT, "crawl_jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "crawl_jobs", _in_clause(_OLD_VALUES))
