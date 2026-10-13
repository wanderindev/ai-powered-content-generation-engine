"""Prompt builder for historical article suggestion generation."""


def build_historical_suggestion_prompt(
    taxonomy_name: str,
    taxonomy_description: str | None,
    category_name: str,
    category_description: str | None,
    num_suggestions: int,
    existing_titles: list[str],
) -> str:
    """Build prompt for generating chronologically-ordered article suggestions."""
    titles_block = "\n".join(f"- {t}" for t in existing_titles) if existing_titles else "(none yet)"

    return f"""You are an expert researcher for Panama In Context, a blog dedicated to exploring how historical events and cultural elements have shaped Panama's national identity.

CONTEXT
Taxonomy: {taxonomy_name}
Taxonomy Description: {taxonomy_description or "N/A"}
Category: {category_name}
Category Description: {category_description or "N/A"}
Number of Suggestions Needed: {num_suggestions}

Existing Articles Titles:
{titles_block}

SPECIAL INSTRUCTION FOR CHRONOLOGY
- Articles within this category MUST follow a chronological progression.
- If no existing articles, begin coverage with the earliest events or foundational aspects of this historical period.
- If there are existing articles, identify the most recent event or topic already covered, and continue forward in time.
- Each suggestion should cover the next significant event or topic IN CHRONOLOGICAL ORDER.

TASK
Generate {num_suggestions} historically accurate article suggestions for this category. Each suggestion should:
1. Have a compelling title
2. Include a detailed main topic (around 5 sentences) describing what the article will cover
3. Feature 3-7 well-defined subtopics, each with a title and 1-2 sentences describing its content
4. Include a clear point of view or angle (2-3 sentences)
5. Be distinct from existing articles
6. Be historically accurate and fact-checked
7. Build on each other in STRICT chronological sequence

RESEARCH REQUIREMENTS
- You must fact-check each suggestion to ensure historical accuracy
- Verify that historical events mentioned actually occurred during the timeframe covered by the category
- Confirm that historical figures and sites mentioned actually existed
- If you're uncertain about a historical fact, adjust your suggestion or note the uncertainty
- Carefully consider dates to maintain chronological accuracy

FORMAT YOUR RESPONSE AS JSON:
{{
  "suggestions": [
    {{
      "title": "Clear, engaging title",
      "main_topic": "Detailed description of the article focus (around 5 sentences)",
      "sub_topics": [
        {{
          "title": "Subtopic 1 Title",
          "description": "1-2 sentence description of this subtopic"
        }},
        {{
          "title": "Subtopic 2 Title",
          "description": "1-2 sentence description of this subtopic"
        }}
      ],
      "point_of_view": "Unique angle or perspective (2-3 sentences)"
    }}
  ]
}}

EXAMPLE SUGGESTION:
{{
  "suggestions": [
    {{
      "title": "The Founding of Early Spanish Settlements in Panama",
      "main_topic": "This article explores the initial establishment of Spanish outposts across the Panamanian isthmus in the early 16th century. It examines the strategic considerations that guided site selection and the challenges faced by early colonists. The analysis covers the administrative structures implemented to govern these fledgling settlements and their relationships with indigenous populations. It also details how these early foundations shaped Panama's subsequent colonial development and established patterns of land use and resource extraction.",
      "sub_topics": [
        {{
          "title": "First Spanish Expeditions",
          "description": "The pioneering journeys that led to the identification of settlement sites, including the roles of early explorers in mapping the isthmus and identifying valuable resources."
        }},
        {{
          "title": "Interactions with Indigenous Groups",
          "description": "The complex relationships that developed between Spanish settlers and various indigenous peoples, ranging from violent confrontation to strategic alliances and tribute arrangements."
        }},
        {{
          "title": "Early Economic Structures",
          "description": "The establishment of encomienda systems, agricultural production, and the first attempts at resource extraction that formed the economic basis of early colonial settlements."
        }},
        {{
          "title": "Administrative Hierarchies",
          "description": "The implementation of Spanish governance models, the appointment of colonial officials, and the creation of cabildos (town councils) to manage local affairs."
        }}
      ],
      "point_of_view": "This article approaches early Spanish settlement through the lens of institutional development and environmental adaptation. It emphasizes how geographic realities shaped colonial ambitions, and how the administrative choices made during this foundational period had lasting consequences for Panama's development. The narrative recognizes both Spanish imperial objectives and indigenous agency in shaping the eventual character of colonial Panama."
    }}
  ]
}}

Think carefully and thoroughly about each suggestion. Verify historical facts before including them and ensure strict chronological progression."""
