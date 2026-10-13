"""Widen alembic_version.version_num from varchar(32) to varchar(255).

Revision ID: 0003a_widen_alembic_version
Revises: 0003_normalize_educator_emails
Create Date: 2026-05-30

Alembic created ``alembic_version.version_num`` as ``varchar(32)`` (its historic
default) when this database was first stamped. Several revision ids in this
project exceed 32 characters — notably ``0004_educator_verify_code_attempts``
(34 chars). When Alembic writes that id into ``version_num`` after running the
0004 upgrade, Postgres raises ``StringDataRightTruncation`` and the whole
``alembic upgrade`` aborts. Production was stuck at ``0002`` for exactly this
reason: 0003 (30 chars) fits, but the chain dies at 0004.

This revision's own id is 27 chars, so it still fits the narrow column; running
it widens ``version_num`` to ``varchar(255)`` (modern Alembic's default) so every
following revision — 0004, 0005 and beyond — can record its id. It is inserted
between 0003 and 0004 precisely so the column is wide before 0004 needs it.

The change is a metadata-only column type widening; the single stored row
(``version_num``) is untouched.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003a_widen_alembic_version"
down_revision = "0003_normalize_educator_emails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE alembic_version "
        "ALTER COLUMN version_num TYPE character varying(255)"
    )


def downgrade() -> None:
    # Safe to narrow back: by the time this downgrade runs the stored version is
    # this revision's own id (27 chars), and Alembic then records 0003 (30) —
    # both fit varchar(32).
    op.execute(
        "ALTER TABLE alembic_version "
        "ALTER COLUMN version_num TYPE character varying(32)"
    )
