"""Ranked media search: stored generated search_vector columns (#590).

Media search used to be three different unindexed ILIKE scans, ANDed per word,
so a long picker query matched nothing and plurals / accents missed. Search now
runs full-text over a STORED generated ``tsvector`` on ``media`` and
``media_candidates`` (GIN-indexed). Stored rather than query-time vectors:
building the vectors per query measured ~0.7 s per pass over ~7.7k rows, the
stored columns ~30-40 ms.

Generated expressions must be IMMUTABLE, so this also creates two wrappers:
``public.pic_unaccent`` (``unaccent`` is only STABLE) and
``public.pic_array_to_text`` (``array_to_string`` likewise). They need the
``unaccent`` extension; ``pg_trgm`` backs the query-time ``word_similarity``
typo fallback. Both extensions are operator-approved on the shared DO cluster
and created ``IF NOT EXISTS`` in ``public``.

Adding a STORED generated column rewrites the table under ACCESS EXCLUSIVE —
brief here, since both tables are small (~7.7k media / ~16k candidates).
Indexes are not CONCURRENTLY: that cannot run inside Alembic's transaction,
and the tables are tiny.

The models declare ``search_vector`` on the Table but exclude it from the
mapper (no ORM attribute), so until this is applied only search requests fail;
no other read or write ever names the column.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0041_media_search"
down_revision = "0040_article_narration_job"
branch_labels = None
depends_on = None

# Must match app/models/search_sql.py (tests/test_media_search.py asserts it).
_PREREQUISITES = (
    "CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public",
    "CREATE OR REPLACE FUNCTION public.pic_unaccent(text) RETURNS text "
    "LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT "
    "AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$",
    "CREATE OR REPLACE FUNCTION public.pic_array_to_text(varchar[]) RETURNS text "
    "LANGUAGE sql IMMUTABLE PARALLEL SAFE "
    "AS $$ SELECT coalesce(array_to_string($1, ' '), '') $$",
)

# Must match app/models/search_sql.py MEDIA_SEARCH_VECTOR_SQL.
_MEDIA_VECTOR = (
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(coalesce(alt_text, '') || ' ' || "
    "coalesce(caption, '') || ' ' || coalesce(search_terms, ''))), 'B') || "
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(coalesce(attribution, ''))), 'D')"
)

# Must match app/models/search_sql.py CANDIDATE_SEARCH_VECTOR_SQL.
_CANDIDATE_VECTOR = (
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(public.pic_array_to_text(tags) || ' ' || "
    "public.pic_array_to_text(source_categories))), 'B') || "
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(coalesce(description, ''))), 'C')"
)


def upgrade() -> None:
    for stmt in _PREREQUISITES:
        op.execute(stmt)

    op.execute(
        "ALTER TABLE media ADD COLUMN search_vector tsvector "
        f"GENERATED ALWAYS AS ({_MEDIA_VECTOR}) STORED"
    )
    op.execute(
        "ALTER TABLE media_candidates ADD COLUMN search_vector tsvector "
        f"GENERATED ALWAYS AS ({_CANDIDATE_VECTOR}) STORED"
    )

    op.execute("CREATE INDEX ix_media_search_vector ON media USING gin (search_vector)")
    op.execute(
        "CREATE INDEX ix_media_candidates_search_vector "
        "ON media_candidates USING gin (search_vector)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_media_candidates_search_vector")
    op.execute("DROP INDEX IF EXISTS ix_media_search_vector")
    op.execute("ALTER TABLE media_candidates DROP COLUMN IF EXISTS search_vector")
    op.execute("ALTER TABLE media DROP COLUMN IF EXISTS search_vector")
    op.execute("DROP FUNCTION IF EXISTS public.pic_array_to_text(varchar[])")
    op.execute("DROP FUNCTION IF EXISTS public.pic_unaccent(text)")
    # The unaccent / pg_trgm extensions are deliberately NOT dropped: they are
    # database-scoped on a shared managed cluster, later objects may rely on
    # them, and CREATE EXTENSION IF NOT EXISTS keeps a re-upgrade idempotent.
