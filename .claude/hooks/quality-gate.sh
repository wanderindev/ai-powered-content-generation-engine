#!/bin/bash
# .claude/hooks/quality-gate.sh
# Purpose: Prevent task completion until code quality checks pass
# Trigger: Stop hook
# Features: Retry limit to prevent infinite loops, Docker-aware

set -e

# Configuration
MAX_RETRIES=5
RETRY_FILE="/tmp/claude_quality_gate_retries_$$"
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Track retry count
if [[ -f "$RETRY_FILE" ]]; then
    RETRY_COUNT=$(cat "$RETRY_FILE")
else
    RETRY_COUNT=0
fi

# Increment retry count
RETRY_COUNT=$((RETRY_COUNT + 1))
echo "$RETRY_COUNT" > "$RETRY_FILE"

# Check if we've exceeded max retries
if [[ $RETRY_COUNT -gt $MAX_RETRIES ]]; then
    echo "=============================================="
    echo "QUALITY GATE: MAX RETRIES ($MAX_RETRIES) EXCEEDED"
    echo "=============================================="
    echo ""
    echo "The quality gate has failed $MAX_RETRIES times."
    echo "Allowing completion to prevent infinite loop."
    echo ""
    echo "Please manually review and fix the issues before deploying."
    rm -f "$RETRY_FILE"
    exit 0
fi

echo "=============================================="
echo "QUALITY GATE CHECK (Attempt $RETRY_COUNT/$MAX_RETRIES)"
echo "=============================================="

cd "$PROJECT_ROOT"

ERRORS=0

# Check if Docker backend container is running
DOCKER_RUNNING=false
if docker-compose ps backend 2>/dev/null | grep -q "Up"; then
    DOCKER_RUNNING=true
fi

# Check 1: Ruff Linting
echo ""
echo "[1/2] Running ruff check..."
if [[ "$DOCKER_RUNNING" == "true" ]]; then
    if ! docker-compose exec -T backend ruff check /app 2>&1; then
        echo ""
        echo "LINT ERRORS FOUND."
        echo "Run: docker-compose exec backend ruff check /app --fix"
        ERRORS=1
    else
        echo "Lint check passed."
    fi
else
    # Fallback to local if Docker not running
    if command -v ruff &> /dev/null; then
        cd backend
        if ! ruff check . 2>&1; then
            echo "LINT ERRORS FOUND. Run 'cd backend && ruff check . --fix'"
            ERRORS=1
        else
            echo "Lint check passed."
        fi
        cd "$PROJECT_ROOT"
    else
        echo "Docker not running and ruff not found locally - skipping lint check"
    fi
fi

# Check 2: Tests
echo ""
echo "[2/2] Running tests..."
if [[ "$DOCKER_RUNNING" == "true" ]]; then
    # Check if tests exist
    if [[ -d "backend/tests" ]] && [[ -n "$(ls -A backend/tests/test_*.py 2>/dev/null)" ]]; then
        # Run tests inside container with short output
        if ! docker-compose exec -T backend pytest -x -q --tb=short 2>&1; then
            echo ""
            echo "TESTS FAILED. Fix the failing tests before completing."
            ERRORS=1
        else
            echo "Tests passed."
        fi
    else
        echo "No tests found - skipping test check"
    fi
else
    # Fallback message if Docker not running
    if [[ -d "backend/tests" ]] && [[ -n "$(ls -A backend/tests/test_*.py 2>/dev/null)" ]]; then
        echo "Docker not running - skipping tests (run manually with docker-compose)"
    else
        echo "No tests found - skipping test check"
    fi
fi

echo ""
echo "=============================================="

if [[ $ERRORS -ne 0 ]]; then
    echo "QUALITY GATE FAILED"
    echo "Fix the issues above and try again."
    echo "Attempt $RETRY_COUNT of $MAX_RETRIES"
    echo "=============================================="
    exit 1
else
    echo "QUALITY GATE PASSED"
    echo "=============================================="
    rm -f "$RETRY_FILE"
    exit 0
fi
