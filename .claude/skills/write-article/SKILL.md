---
name: write-article
description: Write the next article (or a specific one) in the "Wander in Dev" series about building Panama In Context. Articles are written in first person as a senior fullstack developer sharing design decisions with fellow developers.
---

# Write Article — Wander in Dev Series

## Trigger

`/write-article` or `/write-article <article_number>`

## Arguments

- **article_number** (optional): Article number 1–10 to write. If omitted, auto-detect the next unwritten article.
- **--full** (optional flag): After writing the article, automatically run the full publishing pipeline:
  1. `/article-assets-to-png` — export mermaid diagrams and code blocks as images
  2. `/article-to-docx` — convert the Markdown + images into a formatted Word document

  This produces three outputs in one pass: the `.md` file, the `article-NN-assets/` folder, and the `.docx` file.

## Procedure

### 1. Determine Which Article to Write

If a number is provided, use it directly.

If no number is provided, check which articles already exist:

```
docs/wander-in-dev/
  01-structuring-fastapi-monolith.md
  02-sqlalchemy-bilingual-content-model.md
  03-layered-pydantic-schemas.md
  04-search-series-eager-loading.md
  05-domain-driven-pricing-engine.md
  06-bilingual-react-routing.md
  07-translating-markdown-at-scale.md
  08-four-phase-llm-pipeline.md
  09-when-llms-ignore-constraints.md
  10-orchestrating-llms-retrospective.md
```

The next article is the lowest number that does not yet have a file. If all 10 exist, inform the user the series is complete.

### 2. Read the Proposal

Read the article's outline and mermaid diagrams from `docs/article-series-proposal.md`. The proposal contains the title, 6-point outline, and 2 mermaid diagram descriptions for each article.

### 3. Read Relevant Source Code

Based on the article number, read the relevant source files to extract real code patterns. The key files per article:

| Article | Key Source Files |
|---------|-----------------|
| 1 | `backend/app/main.py`, `backend/app/core/config.py`, `backend/app/core/database.py`, `backend/app/api/articles.py` (as example router) |
| 2 | `backend/app/models/article.py`, `backend/app/core/database.py`, any Alembic migration using `create_type=False` |
| 3 | `backend/app/schemas/article.py`, `backend/app/schemas/series.py`, `backend/app/api/articles.py` (response construction) |
| 4 | `backend/app/api/search.py`, `backend/app/api/articles.py` (slug lookup, series nav, joinedload) |
| 5 | `backend/app/services/pricing.py`, `backend/app/models/pricing_config.py`, `backend/app/models/booking.py`, `backend/app/services/availability.py` |
| 6 | `frontend/src/App.jsx`, `frontend/src/services/articles.js`, `frontend/src/components/layout/AdminLayout.jsx` |
| 7 | `backend/app/services/translation.py`, `backend/app/utils/markdown.py` |
| 8 | `backend/app/services/article_generation.py`, `backend/app/prompts/article_generation.py`, `backend/app/services/suggestion_generation.py` |
| 9 | `backend/app/services/edu_material_generation.py`, `backend/app/prompts/edu_material_generation.py`, `backend/app/services/edu_research.py` |
| 10 | All `services/`, `prompts/`, `models/` directories (skim for the full LLM map) |

Read the relevant files and extract the specific code patterns needed. Do NOT include entire files — extract the most illustrative fragments.

### 4. Write the Article

Write the article following these constraints:

**Format:** Markdown with YAML front matter:
```yaml
---
title: "Article Title"
series: "Wander in Dev"
series_number: N
date: YYYY-MM-DD
tags: [relevant, tech, tags]
---
```

**Target length:** 800–1,200 words of prose (excluding code blocks and diagrams).

**Voice and tone:**
- First person ("I", "my", "we" when addressing the reader collectively)
- Senior fullstack developer sharing thought process with fellow developers
- Conversational but technically precise — like a conference talk or a well-written engineering blog
- Explain the *why* behind decisions, not just the *what*
- Be honest about tradeoffs ("this isn't perfect, but here's why it works")
- No corporate jargon, no filler, no "In this article we will..."
- Open with a hook — a problem, a question, or a concrete scenario
- Close with a takeaway or a bridge to the next article

**Code blocks:**
- Include focused code snippets showing the *pattern*, not the full implementation
- Use comments or pseudo-code to abbreviate non-essential parts: `# ... validation, error handling ...`
- Always include the language identifier in fenced code blocks (```python, ```jsx, etc.)
- Maximum ~30 lines per code block; prefer 10–20 lines
- Code MUST come from the actual codebase — read it, then trim it

**Mermaid diagrams:**
- Include both mermaid diagrams from the proposal for this article
- Wrap in ```mermaid fenced code blocks
- These are already defined in the proposal — use them as-is or refine if needed

**Structure:**
- The outline in the proposal has 6 points — these map roughly to sections, but you don't need rigid H2s for each. Let the writing flow naturally.
- Use H2 (`##`) for major sections and H3 (`###`) sparingly
- The article should feel like a continuous narrative, not a checklist

### 5. Save the Article

Create the output directory if it doesn't exist:
```
mkdir -p docs/wander-in-dev/
```

Write the article to:
```
docs/wander-in-dev/NN-slug.md
```

Using the filenames from the table in step 1.

### 6. Full Pipeline (if `--full` flag is set)

If the `--full` flag is present, continue with the publishing pipeline after saving the article:

**Step 6a: Generate image assets**

Run the `/article-assets-to-png` skill on the newly written Markdown file. This calls the mermaid.ink and Carbonara APIs to produce `diagram-NN.jpg/png` and `code-NN.png` files in the `article-NN-assets/` folder.

Important notes for the API calls:
- Use a **60-second timeout** for Carbonara requests (the service cold-starts slowly)
- Retry up to **3 times** per request with a 3-second wait between retries
- Add a **1-second delay** between all API calls
- Mermaid diagrams: detect Content-Type to save with correct `.jpg` or `.png` extension
- Code blocks: use `theme: "one-dark"`, `backgroundColor: "#E0E0E0"`, `windowControls: false`

**Step 6b: Convert to DOCX**

Run the `/article-to-docx` skill on the same Markdown file. This:
1. Validates that the asset counts match the Markdown block counts
2. Converts the Markdown to a formatted Word document with embedded images
3. Saves as `docs/wander-in-dev/NN-slug.docx`

### 7. Report to User

Display:
- The file path of the written article
- The word count (prose only, excluding code blocks and front matter)
- Which article number it is in the series (e.g., "Article 3 of 10")
- If `--full` was used: the assets folder path, number of diagrams/code images generated, and the DOCX file path
- A one-line summary of the next article in the series (if any)