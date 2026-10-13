"""crawl_jobs: track async media-acquisition crawls off the request path.

Part of issue #277 (move LOC/Wikimedia crawls off the request path):

* Adds the ``crawl_jobs`` table. The admin ``POST /media-library/crawl`` /
  ``/crawl-loc`` / ``/score`` endpoints now create a row, kick off a background
  task, and return ``202`` + a job id; a status-poll endpoint reads progress and
  final stats from this table. State lives in the DB (not process memory) so any
  of the 3 uvicorn workers can serve the poll.
* ``status`` and ``job_type`` are closed vocabularies — String + named CHECK
  (issue #24), mirroring ``CrawlJobStatus`` / ``CrawlJobType`` in
  ``app.models.enums``. The stable ``ck_crawl_jobs_*`` names make adding a value
  a clean drop-and-recreate later.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0013_crawl_jobs"
down_revision = "0012_edu_materials_unique_key"
branch_labels = None
depends_on = None

_STATUS_VALUES = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED")
_TYPE_VALUES = ("WIKIMEDIA", "LOC", "SCORE")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(length=20), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="PENDING", nullable=False
        ),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("units_total", sa.Integer(), nullable=True),
        sa.Column("units_done", sa.Integer(), server_default="0", nullable=False),
        sa.Column("current_unit", sa.String(length=500), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            _in_clause("status", _STATUS_VALUES), name="ck_crawl_jobs_status"
        ),
        sa.CheckConstraint(
            _in_clause("job_type", _TYPE_VALUES), name="ck_crawl_jobs_job_type"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("crawl_jobs")
