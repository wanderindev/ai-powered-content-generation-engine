"""Add ForeignKey constraint on media_candidates.media_id -> media.id.

Revision ID: 0005_media_candidate_media_id_fk
Revises: 0004_educator_verify_code_attempts
Create Date: 2026-05-30

``media_candidates.media_id`` links a reviewed candidate to the ``media`` row
created when it is approved and downloaded (see ``media_library`` upload flow),
but the column was declared as a plain integer with no referential integrity —
nothing stopped it pointing at a deleted ``media`` row (closes #22).

The two sibling cases in #22 — ``articles.research_id`` and
``articles.approved_by_id`` — already carry their FKs in production (the
``0000_baseline`` pg_dump reproduces ``articles_research_id_fkey`` and
``articles_approved_by_id_fkey``); only the SQLAlchemy models were missing the
declarations, which is fixed in the models alone with no DDL delta. This
migration therefore adds the one constraint that genuinely did not exist
anywhere.

``ON DELETE SET NULL`` matches the nullable "link set when approved" semantics:
deleting a ``media`` row nulls out any candidate's stale pointer rather than
blocking the delete. Production was verified free of orphan rows
(``media_id`` referencing a non-existent ``media.id``) before this lands, so the
constraint applies cleanly.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_media_candidate_media_id_fk"
down_revision = "0004_educator_verify_code_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'media_candidates_media_id_fkey'
                  AND conrelid = 'public.media_candidates'::regclass
            ) THEN
                ALTER TABLE public.media_candidates
                    ADD CONSTRAINT media_candidates_media_id_fkey
                    FOREIGN KEY (media_id) REFERENCES public.media(id)
                    ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.media_candidates "
        "DROP CONSTRAINT IF EXISTS media_candidates_media_id_fkey"
    )
