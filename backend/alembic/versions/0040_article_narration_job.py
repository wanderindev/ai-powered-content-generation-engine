"""Add ARTICLE_NARRATION to the crawl_jobs job_type vocabulary (#547).

Spanish article narration moves off the request path onto the #277
background-job table: a large article's per-segment ElevenLabs calls plus the
per-segment post-processing run well past the api. vhost's 300s proxy ceiling.
The stable constraint name (issue #24 convention) makes this a clean
drop-and-recreate, exactly as 0034 did for the edu job types.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0040_article_narration_job"
down_revision = "0039_es_accuracy_check"
branch_labels = None
depends_on = None

_OLD_VALUES = (
    "WIKIMEDIA",
    "LOC",
    "SCORE",
    "FACT_CHECK",
    "EDU_GENERATE_GUIDES",
    "EDU_RENDER_SLIDES",
    "EDU_GENERATE_AUDIO",
    "EDU_ASSEMBLE_VIDEO",
)
_NEW_VALUES = (*_OLD_VALUES, "ARTICLE_NARRATION")

_CONSTRAINT = "ck_crawl_jobs_job_type"


def _in_clause(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"job_type IN ({allowed})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "crawl_jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "crawl_jobs", _in_clause(_NEW_VALUES))


def downgrade() -> None:
    # Rows of the new type would violate the narrowed constraint.
    op.execute("DELETE FROM crawl_jobs WHERE job_type = 'ARTICLE_NARRATION'")
    op.drop_constraint(_CONSTRAINT, "crawl_jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "crawl_jobs", _in_clause(_OLD_VALUES))
