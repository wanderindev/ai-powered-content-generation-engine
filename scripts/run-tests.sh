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
#   scripts/run-tests.sh --build [...]                   # force a rebuild first
#   scripts/run-tests.sh -h | --help
#
# Image (issue #707): the image only supplies the Python environment — your
# code is bind-mounted — so it is keyed on what shapes that environment. The
# script hashes backend/requirements.txt + backend/Dockerfile and runs
# `pic-backend-test:<hash>` (content-addressed, so concurrent worktrees never
# retag an image a running suite uses). When that tag is missing it first tries
# to adopt the compose-built `panama-in-context-backend` (if its baked-in
# requirements.txt matches, it is just tagged — no build), else it builds the
# tag, printing one stderr line saying why. A current image costs only the hash
# check. No flag is needed after a dependency change.
#   PIC_TEST_IMAGE=<image>  use exactly this image (no hash check; built only
#                           if absent or with --build, into that tag).
#
# Empty-mount guard (issue #448): the bind mounts are resolved by the DOCKER
# DAEMON's filesystem, not this shell's. In an environment where the repo path
# exists only locally (e.g. a managed-agents sandbox whose /workspace the host
# daemon cannot see), docker silently mounts empty directories and pytest/ruff
# run against nothing. A check inside the container refuses to run in that
# case; a host-side check cannot catch it. See wanderindev/managed-agents#43.

set -euo pipefail

usage() {
  sed -n '3,44p' "$0" | sed 's/^# \{0,1\}//'
}

REPO_ROOT="$(git rev-parse --show-toplevel)"
FORCE_BUILD=0

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--build" ]]; then
  FORCE_BUILD=1
  shift
fi

# Hash of everything that shapes the image's environment (not the app code,
# which is bind-mounted). Changing either file yields a new tag.
DEPS_HASH="$(cat "$REPO_ROOT/backend/requirements.txt" "$REPO_ROOT/backend/Dockerfile" \
  | sha256sum | cut -c1-16)"
COMPOSE_IMAGE="panama-in-context-backend"

build_image() {  # $1 = tag, $2 = reason (one stderr line)
  echo ">> run-tests.sh: building $1 — $2" >&2
  docker build -q --label "pic.test.deps-hash=$DEPS_HASH" -t "$1" \
    "$REPO_ROOT/backend" >/dev/null
}

if [[ -n "${PIC_TEST_IMAGE:-}" ]]; then
  # Explicit override: trust it as-is; build into it only when asked or absent.
  IMAGE="$PIC_TEST_IMAGE"
  if [[ "$FORCE_BUILD" -eq 1 ]]; then
    build_image "$IMAGE" "--build requested"
  elif ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    build_image "$IMAGE" "PIC_TEST_IMAGE does not exist yet"
  fi
else
  IMAGE="pic-backend-test:$DEPS_HASH"
  if [[ "$FORCE_BUILD" -eq 1 ]]; then
    build_image "$IMAGE" "--build requested"
  elif ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    # Adopt the compose-built dev image when its baked-in requirements.txt is
    # the current one (it cannot vouch for the Dockerfile, but that rarely
    # changes and --build is the escape hatch). Tagging is atomic and harmless.
    REQ_HASH=""
    IMG_REQ_HASH=""
    if docker image inspect "$COMPOSE_IMAGE" >/dev/null 2>&1; then
      IMG_REQ="$(docker run --rm --entrypoint cat "$COMPOSE_IMAGE" \
        /app/requirements.txt 2>/dev/null)" || IMG_REQ=""
      if [[ -n "$IMG_REQ" ]]; then
        # $(...) strips trailing newlines, so hash both sides the same way.
        IMG_REQ_HASH="$(printf '%s' "$IMG_REQ" | sha256sum | cut -d' ' -f1)"
        REQ_HASH="$(printf '%s' "$(cat "$REPO_ROOT/backend/requirements.txt")" \
          | sha256sum | cut -d' ' -f1)"
      fi
    fi
    if [[ -n "$IMG_REQ_HASH" && "$IMG_REQ_HASH" == "$REQ_HASH" ]]; then
      docker tag "$COMPOSE_IMAGE" "$IMAGE"
    elif [[ -n "$IMG_REQ_HASH" ]]; then
      build_image "$IMAGE" "$COMPOSE_IMAGE predates backend/requirements.txt"
    else
      build_image "$IMAGE" "no test image for this requirements.txt + Dockerfile yet"
    fi
  fi
fi

# No args → default to the quiet full suite.
if [[ "$#" -eq 0 ]]; then
  set -- pytest -q
fi

# Runs inside the container before the real command: refuse to proceed if the
# mounted code is empty (the daemon resolved the bind mounts to nothing).
MOUNT_GUARD='
for d in /app/app /app/tests; do
  if [ -z "$(ls -A "$d" 2>/dev/null)" ]; then
    echo "ERROR: run-tests.sh: $d is EMPTY inside the container." >&2
    echo "The docker daemon resolved the bind mounts against ITS filesystem and" >&2
    echo "found nothing at the repo path. This happens when the repo exists only" >&2
    echo "in the calling environment (e.g. a sandbox workspace the host daemon" >&2
    echo "cannot see). Refusing to run against an empty tree — a green result" >&2
    echo "here would be meaningless. See issue #448 / managed-agents#43." >&2
    exit 2
  fi
done
exec "$@"'

exec docker run --rm -u 0:0 \
  -v "$REPO_ROOT/backend/app:/app/app" \
  -v "$REPO_ROOT/backend/tests:/app/tests" \
  -v "$REPO_ROOT/backend/alembic:/app/alembic" \
  -v "$REPO_ROOT/backend/scripts:/app/scripts" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e ENVIRONMENT=test \
  -e PIC_TEST_PG_IMAGE \
  -e DATABASE_URL=postgresql://test:test@localhost:1/none \
  -w /app "$IMAGE" sh -c "$MOUNT_GUARD" mount-guard "$@"
