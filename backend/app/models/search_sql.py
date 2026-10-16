"""SQL shared by the generated search columns: the DB prerequisites and the
generated-column expressions.

* Media search vectors (#590): mirrored by hand in
  alembic/versions/0041_media_search.py; tests/test_media_search.py asserts they
  match.
* Knowledge-chunk vector (#623): mirrored in
  alembic/versions/0044_knowledge_chunks.py; tests/test_knowledge_chunks.py
  asserts they match.

Generated columns need IMMUTABLE expressions, which is why neither
``concat_ws`` nor ``array_to_string`` appears below (both are only STABLE) and
why ``unaccent`` is wrapped in ``public.pic_unaccent``.
"""

SEARCH_PREREQUISITES_SQL = (
    "CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public",
    # unaccent() is STABLE; a generated column needs IMMUTABLE. Schema-qualified
    # dictionary + function so the expression does not depend on search_path.
    "CREATE OR REPLACE FUNCTION public.pic_unaccent(text) RETURNS text "
    "LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT "
    "AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$",
    # array_to_string() is STABLE; same reason.
    "CREATE OR REPLACE FUNCTION public.pic_array_to_text(varchar[]) RETURNS text "
    "LANGUAGE sql IMMUTABLE PARALLEL SAFE "
    "AS $$ SELECT coalesce(array_to_string($1, ' '), '') $$",
)

MEDIA_SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(coalesce(alt_text, '') || ' ' || "
    "coalesce(caption, '') || ' ' || coalesce(search_terms, ''))), 'B') || "
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(coalesce(attribution, ''))), 'D')"
)

CANDIDATE_SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(public.pic_array_to_text(tags) || ' ' || "
    "public.pic_array_to_text(source_categories))), 'B') || "
    "setweight(to_tsvector('english'::regconfig, "
    "public.pic_unaccent(coalesce(description, ''))), 'C')"
)

# knowledge_chunks.search_vector (#623): one row is one language, so the text
# search config is picked per row. The CASE over two regconfig literals keeps
# the expression IMMUTABLE (to_tsvector(regconfig, text) is), unlike a
# text -> regconfig cast. The heading path outranks the body.
KNOWLEDGE_CHUNK_SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector("
    "CASE WHEN language = 'es' THEN 'spanish'::regconfig ELSE 'english'::regconfig END, "
    "public.pic_unaccent(coalesce(heading_path, ''))), 'A') || "
    "setweight(to_tsvector("
    "CASE WHEN language = 'es' THEN 'spanish'::regconfig ELSE 'english'::regconfig END, "
    "public.pic_unaccent(coalesce(content, ''))), 'B')"
)
