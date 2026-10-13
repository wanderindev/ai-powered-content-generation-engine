"""Add FACT_CHECK to the crawl_jobs job_type vocabulary (#384).

Fact-check runs move off the request path onto the #277 background-job table:
research-sized runs (8+ sequential web-search batches) exceed the 1200s proxy
timeout, so the endpoints now return 202 + a job id. The stable constraint
name (issue #24 convention) makes this a clean drop-and-recreate.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0021_fact_check_job_type"
down_revision = "0020_verified_claims"
branch_labels = None
depends_on = None

_OLD_VALUES = ("WIKIMEDIA", "LOC", "SCORE")
_NEW_VALUES = (*_OLD_VALUES, "FACT_CHECK")

_CONSTRAINT = "ck_crawl_jobs_job_type"


def _in_clause(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"job_type IN ({allowed})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "crawl_jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "crawl_jobs", _in_clause(_NEW_VALUES))


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "crawl_jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "crawl_jobs", _in_clause(_OLD_VALUES))
