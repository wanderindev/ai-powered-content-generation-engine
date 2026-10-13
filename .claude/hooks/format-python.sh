#!/bin/bash
# .claude/hooks/format-python.sh
# Purpose: Auto-format Python files after edits so Claude sees clean code
# Trigger: PostToolUse (after Edit tool on *.py files)

# Get the file that was edited from environment
# CLAUDE_TOOL_INPUT contains JSON with the file path
FILE_PATH=$(echo "$CLAUDE_TOOL_INPUT" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')

# Only process Python files in the backend directory
if [[ "$FILE_PATH" == *backend/*.py ]]; then
    # Convert absolute path to container-relative path
    CONTAINER_PATH=$(echo "$FILE_PATH" | sed 's|.*/backend/|/app/|')

    # Check if the backend container is running
    if docker-compose ps backend 2>/dev/null | grep -q "Up"; then
        # Format the specific file inside the container (silent)
        docker-compose exec -T backend ruff format "$CONTAINER_PATH" 2>/dev/null

        # Fix auto-fixable issues inside the container (silent)
        docker-compose exec -T backend ruff check "$CONTAINER_PATH" --fix --silent 2>/dev/null
    fi
fi

# Always exit 0 - formatting is best-effort, not blocking
exit 0