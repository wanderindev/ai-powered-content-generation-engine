# Test-only Postgres 17 + pgvector for CI (#621/#622).
#
# CI builds this image instead of pulling pgvector/pgvector:pg17, because GitHub
# runners hit Docker Hub's unauthenticated pull rate limit on non-official
# images. The base is AWS's public mirror of the official postgres image. Its
# Debian build already has the apt.postgresql.org (PGDG) repo configured.
# Local runs keep using pgvector/pgvector:pg17 (see PIC_TEST_PG_IMAGE in
# backend/tests/conftest.py).
FROM public.ecr.aws/docker/library/postgres:17
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-17-pgvector \
    && rm -rf /var/lib/apt/lists/*
