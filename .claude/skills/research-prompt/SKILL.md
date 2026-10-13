---
name: research-prompt
description: Generate a research prompt for a given article suggestion ID (or the next unresearched one). The prompt is saved to ai_generated_content/research_prompts/ for the user to paste into Claude's deep research UI.
---

# Research Prompt Generation

## Trigger

`/research-prompt` or `/research-prompt <suggestion_id>`

## Arguments

- **suggestion_id** (optional): The article suggestion ID to generate a prompt for. If omitted, auto-detect the next unresearched suggestion.

## Procedure

### 1. Determine Target Suggestion ID

If an ID is provided as an argument, use it directly.

If no ID is provided (or "next"), query the production database to find the next suggestion needing research:

```bash
psql "$DATABASE_URL" -t -A \
  -c "SELECT COALESCE(
    (SELECT r.suggestion_id FROM research r WHERE r.content = '' ORDER BY r.suggestion_id LIMIT 1),
    (SELECT s.id FROM article_suggestions s LEFT JOIN research r ON r.suggestion_id = s.id WHERE r.id IS NULL ORDER BY s.id LIMIT 1)
  );"
```

The logic: first find the lowest suggestion_id in `research` with empty content; if none, find the lowest `article_suggestions.id` with no research record at all.

### 2. Fetch Context from Database

Run a single query joining suggestions, categories, and taxonomies:

```bash
psql "$DATABASE_URL" \
  -c "SELECT s.id, s.title, s.main_topic, s.sub_topics, s.point_of_view,
      c.name as category_name, c.description as category_description,
      t.name as taxonomy_name, t.description as taxonomy_description
    FROM article_suggestions s
    JOIN categories c ON s.category_id = c.id
    JOIN taxonomies t ON c.taxonomy_id = t.id
    WHERE s.id = <TARGET_ID>;"
```

If no result is returned, report the error and stop.

### 3. Generate the Research Prompt

Use this template as a **starting point**, then tailor it based on the specific taxonomy, category, and topic context:

```
REF: suggestion_<ID>

Research the topic described below for a college-level historical
education platform about Panama.

CONTEXT:
This article is part of the "<taxonomy_name>" series, specifically
in the category "<category_name>": <category_description>

RESEARCH FOCUS:
Title: <title>

<main_topic>

Key areas to investigate:
- <sub_topic_1>
- <sub_topic_2>
- <sub_topic_3>

Point of view: <point_of_view>

REQUIREMENTS:
- Prioritize primary and scholarly secondary sources (academic journals,
  books by recognized historians of colonial Panama and the Spanish Main)
- Distinguish between well-documented facts and areas of historical debate
- Include specific dates, names, and figures where available
- Provide full source citations so claims can be verified
- Include a formal "## References" or "## Sources" section at the end
  listing all works cited (this is required for automated processing)
- Target approximately 5000-8000 words of substantive, narrative prose
  at a college academic level
```

**Tailoring guidance:** Analyze the taxonomy, category, and topic to adjust:
- Source guidance: for non-colonial topics (e.g., modern Panama, canal era), adjust the source recommendation away from "colonial Panama and the Spanish Main" to match the era
- Investigation areas: refine based on the sub_topics — if they're very specific, the prompt can be more directed; if broad, keep it open
- The goal is a prompt that gives Claude's deep researcher enough context to do excellent work without over-constraining the output format

### 4. Save the Prompt

Write the generated prompt to:
```
ai_generated_content/research_prompts/suggestion_<ID>_prompt.md
```

### 5. Report to User

Display:
- The file path of the saved prompt
- A reminder of the next steps:
  1. Open the prompt file
  2. Paste or attach it in Claude UI with deep research enabled
  3. Download the research result to `ai_generated_content/research_documents/suggestion_<ID>_research.md`
  4. Run `/research-process` to insert the research into the database