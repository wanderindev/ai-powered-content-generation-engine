import re
import unicodedata


def generate_slug(text: str) -> str:
    """
    Generate a URL-friendly slug from text.

    Handles Spanish characters:
    - Replaces ñ with n
    - Removes accents from vowels (á→a, é→e, etc.)
    - Converts to lowercase
    - Replaces spaces with hyphens
    - Removes special characters

    Args:
        text: The text to convert to a slug

    Returns:
        URL-friendly slug string
    """
    if not text:
        return ""

    # Normalize unicode characters (NFD decomposition)
    # This separates accented characters into base + combining mark
    text = unicodedata.normalize("NFD", text)

    # Remove combining marks (accents)
    text = "".join(char for char in text if not unicodedata.combining(char))

    # Handle ñ specifically (it might not decompose the same way)
    text = text.replace("ñ", "n").replace("Ñ", "n")

    # Convert to lowercase
    text = text.lower()

    # Replace spaces and underscores with hyphens
    text = re.sub(r"[\s_]+", "-", text)

    # Remove characters that aren't alphanumeric or hyphens
    text = re.sub(r"[^a-z0-9\-]", "", text)

    # Remove multiple consecutive hyphens
    text = re.sub(r"-+", "-", text)

    # Remove leading/trailing hyphens
    text = text.strip("-")

    return text


def generate_slug_es(text: str) -> str:
    """
    Generate slug specifically optimized for Spanish text.
    Alias for generate_slug since both handle Spanish characters.
    """
    return generate_slug(text)
