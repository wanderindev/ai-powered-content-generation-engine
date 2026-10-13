---
name: nlm-fetch-content
description: Use this skill to retrieve source content, notebook details, notes, or AI summaries from a NotebookLM notebook.
---

# Fetch Content from a NotebookLM Notebook

## Prerequisites

- `nlm` CLI must be installed and on PATH (`uv tool install notebooklm-mcp-cli`)
- Authentication must be configured (`nlm login --check` to verify)

## Arguments

`$ARGUMENTS` - The notebook ID (or alias) and optionally what to fetch (sources, notes, or a specific source ID).

## Procedure

1. **Verify auth** by running `nlm login --check`. If it fails, tell the user to run `nlm login` first and stop.

2. **Get notebook details and list sources**:
   ```bash
   nlm notebook get NOTEBOOK_ID --json
   ```
   Returns the notebook title, source count, and list of sources with their IDs and titles.

3. **Get the raw text content of a specific source**:
   ```bash
   nlm source content SOURCE_ID --json
   ```
   Returns the full text content (from PDFs, webpages, pasted text, etc.) without AI processing. JSON output includes `content`, `title`, `source_type`, and `char_count`.

   To save to a file:
   ```bash
   nlm source content SOURCE_ID -o output.txt
   ```

4. **Get an AI-generated summary of a source**:
   ```bash
   nlm source describe SOURCE_ID --json
   ```
   Returns a summary and keyword chips.

5. **Fetch all source content from a notebook** by iterating:
   ```bash
   # First list sources
   nlm source list NOTEBOOK_ID --quiet
   # Returns source IDs, one per line

   # For each source ID, get content
   nlm source content SOURCE_ID_1 --json
   nlm source content SOURCE_ID_2 --json
   ```

6. **List notes in a notebook**:
   ```bash
   nlm note list NOTEBOOK_ID --json
   ```

7. **Get an AI summary of the entire notebook**:
   ```bash
   nlm notebook describe NOTEBOOK_ID --json
   ```

## Example

```bash
# Get notebook details
nlm notebook get abc123 --json

# List sources (IDs only)
nlm source list abc123 --quiet

# Get full text of a source
nlm source content src456 --json
# Output: {"content": "# Chapter 1\n\nThe canal was built...", "title": "Chapter 1", "char_count": 5432, ...}

# Download a report artifact
nlm download report abc123 -o report.md
```