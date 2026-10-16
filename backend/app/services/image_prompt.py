"""Image prompt generation service — calls Sonnet to produce a Gemini prompt."""

from app.models.article import Article
from app.prompts.image_generation import build_image_prompt
from app.services.llm import SONNET_MODEL, create_message, first_text


def generate_image_prompt(article: Article) -> str:
    """Generate a watercolor image prompt for the given article."""
    system_prompt = build_image_prompt(article.title, article.content)

    # The prompt itself is short, but Sonnet 5.5's always-on adaptive thinking
    # counts toward max_tokens — 1024 total would truncate almost every call.
    message = create_message(
        feature="image_prompt",
        model=SONNET_MODEL,
        max_tokens=4000,
        output_config={"effort": "high"},
        messages=[
            {
                "role": "user",
                "content": "Generate the watercolor image prompt for this article.",
            }
        ],
        system=system_prompt,
    )

    return first_text(message).strip()
