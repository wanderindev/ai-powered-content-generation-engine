---
name: nlm-add-content
description: Use this skill to add markdown/text content, URLs, or files as sources to an existing NotebookLM notebook.
---

# Add Content to a NotebookLM Notebook

## Prerequisites

- `nlm` CLI must be installed and on PATH (`uv tool install notebooklm-mcp-cli`)
- Authentication must be configured (`nlm login --check` to verify)

## Arguments

`$ARGUMENTS` - The notebook ID (or alias) followed by the content to add or instructions about where to get the content.

## Procedure

1. **Verify auth** by running `nlm login --check`. If it fails, tell the user to run `nlm login` first and stop.

2. **Verify the notebook exists**:
   ```bash
   nlm notebook get NOTEBOOK_ID --json
   ```
   If it fails, list available notebooks with `nlm notebook list --json` and help the user find the right one.

3. **Add content as a text source**:
   ```bash
   nlm source add NOTEBOOK_ID --text "MARKDOWN_CONTENT" --title "SOURCE_TITLE" --wait --json
   ```

   For content with special characters or multi-line content, use a temp file:
   ```bash
   cat > /tmp/nlm_source.txt << 'CONTENT_EOF'
   ... markdown content here ...
   CONTENT_EOF

   nlm source add NOTEBOOK_ID --text "$(cat /tmp/nlm_source.txt)" --title "Title" --wait --json
   ```

   Important notes:
   - Use `--title` to give the source a descriptive name
   - Use `--wait` to ensure processing completes before returning
   - For very large content (>100KB), break into multiple sources with numbered titles (e.g., "Document Part 1", "Document Part 2")

4. **To add a URL source instead**:
   ```bash
   nlm source add NOTEBOOK_ID --url "https://example.com" --wait --json
   ```

5. **To add a local file** (PDF, txt, md, docx, csv, mp3, mp4, jpg, png):
   ```bash
   nlm source add NOTEBOOK_ID --file /path/to/file.pdf --wait --json
   ```

6. **Return the results** including:
   - Source ID(s) created
   - Source title(s)
   - Processing status (ready or still processing)

## Listing Existing Sources

```bash
nlm source list NOTEBOOK_ID --json
```

## Example

```bash
# Add markdown text
nlm source add abc123 --text "# Research Notes\n\nKey findings..." --title "Research Notes" --wait --json

# Add a URL
nlm source add abc123 --url "https://en.wikipedia.org/wiki/Panama_Canal" --wait --json

# Add a PDF file
nlm source add abc123 --file ./documents/report.pdf --wait --json
```