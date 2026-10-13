"""Add Spanish fields to taxonomies and categories, rename tags Spanish column

Revision ID: 20260103_bilingual
Revises: 20260101_add_spanish_article_fields
Create Date: 2026-01-03
"""
from alembic import op
import sqlalchemy as sa

revision = '20260103_bilingual'
down_revision = '20260101_spanish_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add Spanish fields to taxonomies
    op.add_column('taxonomies', sa.Column('name_es', sa.String(100), nullable=True))
    op.add_column('taxonomies', sa.Column('description_es', sa.Text(), nullable=True))

    # Add Spanish fields to categories
    op.add_column('categories', sa.Column('name_es', sa.String(100), nullable=True))
    op.add_column('categories', sa.Column('description_es', sa.Text(), nullable=True))

    # Rename tag_name_spanish to name_es for consistency
    op.alter_column('tags', 'tag_name_spanish', new_column_name='name_es')


def downgrade() -> None:
    # Rename name_es back to tag_name_spanish
    op.alter_column('tags', 'name_es', new_column_name='tag_name_spanish')

    # Remove Spanish fields from categories
    op.drop_column('categories', 'description_es')
    op.drop_column('categories', 'name_es')

    # Remove Spanish fields from taxonomies
    op.drop_column('taxonomies', 'description_es')
    op.drop_column('taxonomies', 'name_es')
