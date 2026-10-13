"""Prompt-building for feature image generation.

Pure string function — no AI calls happen here.
"""


def build_image_prompt(title: str, content: str) -> str:
    """Build a system prompt that instructs Sonnet to generate a Gemini-ready
    watercolor painting prompt for an article's feature image."""
    # Truncate content to stay within reasonable token limits
    truncated = content[:12000] if len(content) > 12000 else content

    return f"""You are an art director for a Panama history education website.
Your job is to read the article below and produce a single image generation
prompt for Google Gemini that will create the article's feature image.

ESTABLISHED STYLE:
- Medium: Watercolor painting
- Aspect ratio: 16:9 (landscape)
- Mood: Evocative, contemplative, historically resonant
- Subject matter: Landscapes, symbolic objects, abstract concepts, architecture,
  natural elements, maps, documents, waterways, ships, flora/fauna
- Color palette: Warm tropical tones, ocean blues, jungle greens, golden light
- Composition: Atmospheric, slightly impressionistic, editorial illustration feel

RULES:
- Generate exactly ONE prompt, nothing else
- Do NOT include people or human figures of any kind
- Do NOT depict literal historical scenes — convey the theme symbolically
- Do NOT include text, labels, watermarks, or borders in the image
- Do NOT use cultural stereotyping of indigenous peoples
- Focus on the dominant theme, mood, or metaphor of the article
- The prompt should be self-contained (Gemini won't see the article)
- Start the prompt directly — no preamble or explanation

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{truncated}"""
