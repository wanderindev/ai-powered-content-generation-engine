---
name: nlm-create-notebook
description: Use this skill to create a new NotebookLM notebook and optionally add markdown content as an initial source.
---

# Create a NotebookLM Notebook

## Prerequisites

- `nlm` CLI must be installed and on PATH (`uv tool install notebooklm-mcp-cli`)
- Authentication must be configured (`nlm login` or `nlm login --check` to verify)

## Arguments

`$ARGUMENTS` - A title for the notebook. If additional markdown content is provided, it will be added as a source.

## Procedure

1. **Verify auth** by running `nlm login --check`. If it fails, tell the user to run `nlm login` to authenticate first and stop.

2. **Create the notebook**:
   ```bash
   nlm notebook create "TITLE" --json
   ```
   Parse the JSON output to extract the `notebook_id`.

3. **If markdown content was provided**, add it as a text source. For content with special characters, write to a temp file first:
   ```bash
   cat > /tmp/nlm_source.txt << 'CONTENT_EOF'
   ... markdown content ...
   CONTENT_EOF

   nlm source add NOTEBOOK_ID --text "$(cat /tmp/nlm_source.txt)" --title "SOURCE_TITLE" --wait --json
   ```
   - `--wait` ensures the source is fully processed before returning
   - For very large content (>100KB), break into multiple sources with numbered titles
   - Parse the JSON output to extract the `source_id`

4. **Return the results** including:
   - `notebook_id` - needed for all subsequent operations
   - `source_id` (if content was added)
   - The notebook URL: `https://notebooklm.google.com/notebook/NOTEBOOK_ID`

## Example

```bash
nlm notebook create "Panama Canal History" --json
# Output: {"notebook_id": "abc123...", "title": "Panama Canal History", ...}

nlm source add abc123 --text "# Chapter 1\n\nThe canal was built..." --title "Chapter 1" --wait --json
# Output: {"source_id": "src456...", "status": "ready", ...}
```

## Listing Existing Notebooks

```bash
nlm notebook list --json
```