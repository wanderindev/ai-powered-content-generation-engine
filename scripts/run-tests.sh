#!/usr/bin/env bash
#
# run-tests.sh — Canonical backend pytest/ruff runner (issue #204)
#
# Runs the backend test suite (or any backend command) in a throwaway container
# built from the project image, mounting THIS worktree's code + the host docker
# socket. This is the one command agents and the operator should use, from any
# worktree or subdirectory.
#
# Why a one-off `docker run` instead of `docker-compose exec backend ...`:
#   - The operator usually has `docker-compose up -d` running against the MAIN
#     checkout, so `exec` would test the main code, not your worktree, and a
#     fresh `up` from a worktree collides on host ports 8000/5433.
#   - This script publishes NO ports (no collision) and bind-mounts the worktree
#     resolved via `git rev-parse --show-toplevel` (always the right code).
#   - It runs as root (-u 0:0) and mounts /var/run/docker.sock so testcontainers
#     (backend/tests/conftest.py) can spawn its ephemeral Postgres. Dev-only —
#     same rationale as the backend service in docker-compose.yml.
#
# Usage:
#   scripts/run-tests.sh                                  # full suite (pytest -q)
#   scripts/run-tests.sh pytest tests/test_articles.py -q --tb=short   # one file
#   scripts/run-tests.sh ruff check app                  # lint (the CI gate)
#   scripts/run-tests.sh --build [...]                   # rebuild image first
#   scripts/run-tests.sh -h | --help
#
# Image: reuses ${PIC_TEST_IMAGE:-panama-in-context-backend}. The image only
# supplies the Python environment; your code is bind-mounted, so a stale image
# is fine UNLESS backend/requirements.txt changed — then pass --build.

set -euo pipefail

usage() {
  sed -n '3,30p' "$0" | sed 's/^# \{0,1\}//'
}

REPO_ROOT="$(git rev-parse --show-toplevel)"
IMAGE="${PIC_TEST_IMAGE:-panama-in-context-backend}"
FORCE_BUILD=0

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--build" ]]; then
  FORCE_BUILD=1
  shift
fi

# Build a stable-tagged fallback image when the project image is missing
# (fresh machine) or a rebuild was explicitly requested (deps changed).
if [[ "$FORCE_BUILD" -eq 1 ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  IMAGE="pic-backend-test"
  echo ">> Building $IMAGE from $REPO_ROOT/backend ..." >&2
  docker build -t "$IMAGE" "$REPO_ROOT/backend"
fi

# No args → default to the quiet full suite.
if [[ "$#" -eq 0 ]]; then
  set -- pytest -q
fi

exec docker run --rm -u 0:0 \
  -v "$REPO_ROOT/backend/app:/app/app" \
  -v "$REPO_ROOT/backend/tests:/app/tests" \
  -v "$REPO_ROOT/backend/alembic:/app/alembic" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e ENVIRONMENT=test \
  -e DATABASE_URL=postgresql://test:test@localhost:1/none \
  -w /app "$IMAGE" "$@"
