#!/usr/bin/env bash
#
# research-cron.sh — Automated research prompt generation and processing
#
# Workflow:
#   1. If no prompts pending → generate the next one
#   2. If a prompt exists but no research doc → do nothing (waiting for user)
#   3. If a research doc exists → process it into the database
#
# Intended to run via cron every ~30 minutes.

set -euo pipefail

PROJECT_DIR="/home/javier/vc/panama-in-context"
PROMPTS_DIR="$PROJECT_DIR/ai_generated_content/research_prompts"
DOCS_DIR="$PROJECT_DIR/ai_generated_content/research_documents"
LOG_FILE="$PROJECT_DIR/logs/research-cron.log"

CLAUDE="/home/javier/.nvm/versions/node/v23.3.0/bin/claude"
# /research-process writes through the PIC MCP server (`pic` in the repo's
# .mcp.json, tool submit_research, #643). Bash stays allowed for the skill's
# file moves and for its direct-SQL fallback section.
ALLOWED_TOOLS="Bash,Read,Write,Grep,Glob,Edit,mcp__pic__submit_research"

# cron does not read the login shell, so the MCP bearer token (a `read,pipeline`
# token minted with `python -m scripts.mcp_tokens`) comes from an env file
# OUTSIDE the repo, e.g. a line `PIC_MCP_TOKEN=...` (chmod 600). Without it,
# .mcp.json sends an empty bearer, the server answers 401, and the skill leaves
# the research document in place for the next run. The `pic` server must also
# have been approved once in an interactive `claude` session in this checkout
# (project .mcp.json servers are opt-in); see docs/claude/mcp.md.
MCP_ENV_FILE="${PIC_MCP_ENV_FILE:-$HOME/.config/pic/mcp.env}"
if [ -f "$MCP_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$MCP_ENV_FILE"
  set +a
fi

mkdir -p "$(dirname "$LOG_FILE")"
cd "$PROJECT_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Count .md files in a directory (excluding processed/ subdirectory)
count_md() {
  find "$1" -maxdepth 1 -name '*.md' -type f 2>/dev/null | wc -l
}

log "=== Research cron started ==="

pending_prompts=$(count_md "$PROMPTS_DIR")
pending_docs=$(count_md "$DOCS_DIR")

log "Pending prompts: $pending_prompts | Pending research docs: $pending_docs"

if [ "$pending_docs" -gt 0 ]; then
  # Research documents are waiting — process them into the DB
  log "Processing research documents..."
  $CLAUDE -p "/research-process" \
    --allowedTools "$ALLOWED_TOOLS" \
    >> "$LOG_FILE" 2>&1
  log "Processing complete."

elif [ "$pending_prompts" -eq 0 ]; then
  # No prompts and no docs — generate the next prompt
  log "Generating next research prompt..."
  $CLAUDE -p "/research-prompt" \
    --allowedTools "$ALLOWED_TOOLS" \
    >> "$LOG_FILE" 2>&1
  log "Prompt generation complete."

else
  # Prompts exist but no research docs yet — user hasn't done the Claude UI step
  log "Waiting for user to run deep research and download results. Nothing to do."
fi

log "=== Research cron finished ==="
