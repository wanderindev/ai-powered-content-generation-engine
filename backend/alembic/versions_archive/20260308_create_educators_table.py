"""Create educators table for gated access to educational materials.

Revision ID: 20260308b
Revises: 20260308a
"""

from alembic import op
import sqlalchemy as sa

revision = "20260308b"
down_revision = "20260308a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "educators",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("institution", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("language", sa.String(5), nullable=False, server_default="es"),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("confirm_token", sa.String(64), nullable=True),
        sa.Column("verify_code", sa.String(6), nullable=True),
        sa.Column("verify_code_expires_at", sa.DateTime(), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("last_access_at", sa.DateTime(), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(), nullable=True),
        sa.Column("mailing_list", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_table("educators")