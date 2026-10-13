---
name: nlm-query
description: Use this skill to send a prompt or question to a NotebookLM notebook and get an AI-generated answer based on its sources.
---

# Query a NotebookLM Notebook

## Prerequisites

- `nlm` CLI must be installed and on PATH (`uv tool install notebooklm-mcp-cli`)
- Authentication must be configured (`nlm login --check` to verify)
- The notebook must have at least one source added

## Arguments

`$ARGUMENTS` - The notebook ID (or alias) followed by the query/prompt to send.

## Procedure

1. **Verify auth** by running `nlm login --check`. If it fails, tell the user to run `nlm login` first and stop.

2. **Send the query**:
   ```bash
   nlm notebook query NOTEBOOK_ID "YOUR QUESTION OR PROMPT" --json
   ```
   The JSON output includes:
   - `answer` - The AI-generated response based on notebook sources
   - `conversation_id` - For follow-up questions in the same conversation
   - `sources_used` - Which sources were referenced

3. **For follow-up questions** in the same conversation:
   ```bash
   nlm notebook query NOTEBOOK_ID "FOLLOW UP QUESTION" -c CONVERSATION_ID --json
   ```

4. **To query specific sources only** (not all sources in the notebook):
   ```bash
   nlm notebook query NOTEBOOK_ID "QUESTION" -s SOURCE_ID1,SOURCE_ID2 --json
   ```

5. **To configure the chat behavior** before querying (optional):
   ```bash
   # Set to learning guide mode
   nlm chat configure NOTEBOOK_ID --goal learning_guide

   # Set a custom system prompt
   nlm chat configure NOTEBOOK_ID --goal custom --prompt "You are a fact-checker. Verify claims against the provided sources."

   # Set response length
   nlm chat configure NOTEBOOK_ID --response-length longer
   ```

## Use Cases

### Research prompt
```bash
nlm notebook query NOTEBOOK_ID "Summarize the key findings and identify any gaps in the research"
```

### Fact-checking prompt
```bash
nlm chat configure NOTEBOOK_ID --goal custom --prompt "You are a fact-checker. For each claim, cite the specific source and quote that supports or contradicts it."
nlm notebook query NOTEBOOK_ID "Verify the following claims: ..."
```

### Extract structured data
```bash
nlm notebook query NOTEBOOK_ID "Extract all dates, names, and key events. Format as a markdown table."
```

## Important Notes

- Queries are answered by NotebookLM's AI based ONLY on the sources in the notebook
- Free tier has ~50 queries/day rate limit
- Query timeout defaults to 120 seconds (configurable via `NOTEBOOKLM_QUERY_TIMEOUT` env var)
- For finding NEW sources, use `nlm research start` instead