"""Seed the Panamá Viejo 507th anniversary tour (open group, fixed price).

Revision ID: 0028_panama_viejo_507_tour
Revises: 0027_edu_material_fact_check_subject
Create Date: 2026-08-11

A weekend-only open-group tour for the 15-16 August 2026 anniversary, sold at flat
per-person rates ($15 adult / $10 jubilado / $5 menor 5-17 / under-5 free) rather
than through the pricing engine.

This is a NEW row rather than a `fixed_price_config` on the existing `panama-viejo`
tour on purpose: setting that column converts a tour to fixed pricing *and* exempts
it from availability blocking, which would silently change how the year-round
private Panamá Viejo tour is sold.

Both child bands carry the same $5 price because the booking page sells a single
"Menores (5 a 17)" band and collapses it onto one API category — equal prices make
that collapse unable to change what anyone is charged.

The seed is data, not schema, so it is written to be safe on a schema-only database
(a fresh dev/CI DB has no zones for the FK to point at, and skips) while failing
loudly on a populated one rather than reporting success having inserted nothing.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0028_panama_viejo_507_tour"
down_revision = "0027_edu_material_fact_check_subject"
branch_labels = None
depends_on = None

_SLUG = "panama-viejo-507"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            v_zone_id integer;
        BEGIN
            -- The anniversary tour visits the same site as the year-round tour,
            -- so it belongs in the same transport zone.
            SELECT zone_id INTO v_zone_id FROM tours WHERE slug = 'panama-viejo';
            IF v_zone_id IS NULL THEN
                SELECT id INTO v_zone_id FROM zones ORDER BY id LIMIT 1;
            END IF;

            IF v_zone_id IS NULL THEN
                -- Schema-only database: no zones exist for the FK to reference,
                -- so there is nothing to seed. Seeding is a data concern there.
                RAISE NOTICE 'no zones present - skipping % seed', '{_SLUG}';
                RETURN;
            END IF;

            -- The baseline tours were seeded with explicit ids, which can leave
            -- the sequence behind them; realign it before inserting.
            PERFORM setval(
                pg_get_serial_sequence('tours', 'id'),
                COALESCE((SELECT MAX(id) FROM tours), 1),
                true
            );

            INSERT INTO tours (
                slug, name, name_es, description, description_es,
                duration_hours, max_group_size, zone_id, is_active, display_order,
                fixed_price_config
            )
            VALUES (
                '{_SLUG}',
                'Panama Viejo 507th Anniversary Tour',
                'Tour de Panamá Viejo - 507 Aniversario',
                'Open-group walking tour of the Panamá Viejo archaeological site for '
                'the 507th anniversary weekend, 15-16 August 2026. Conducted in Spanish.',
                'Recorrido en grupo abierto por el sitio arqueológico de Panamá Viejo '
                'durante el fin de semana del 507 aniversario, 15 y 16 de agosto de '
                '2026. Se realiza en español.',
                2.00,
                15,
                v_zone_id,
                true,
                8,
                '{{
                    "adult": 15.00,
                    "senior": 10.00,
                    "child_10_17": 5.00,
                    "child_5_10": 5.00,
                    "child_under_5": 0
                }}'::jsonb
            )
            ON CONFLICT (slug) DO NOTHING;

            IF NOT EXISTS (SELECT 1 FROM tours WHERE slug = '{_SLUG}') THEN
                RAISE EXCEPTION 'tour % was not created', '{_SLUG}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Bookings reference tours; refuse to strand them rather than cascade.
    op.execute(
        f"DELETE FROM tours WHERE slug = '{_SLUG}' "
        f"AND NOT EXISTS (SELECT 1 FROM bookings b "
        f"JOIN tours t ON t.id = b.tour_id WHERE t.slug = '{_SLUG}')"
    )
