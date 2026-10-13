"""create_contact_submissions_table

Revision ID: c8037ff2414c
Revises: 20260325a
Create Date: 2026-04-05 22:16:16.697155+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8037ff2414c'
down_revision: Union[str, None] = '20260325a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contact_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("interest", sa.String(20), nullable=False),
        sa.Column("first_name", sa.String(200), nullable=False),
        sa.Column("last_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("form_data", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("email_sent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("email_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("contact_submissions")
