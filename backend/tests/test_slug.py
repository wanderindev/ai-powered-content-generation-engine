"""Tests for app.utils.slug — URL slug generation from arbitrary text."""

from app.utils.slug import generate_slug, generate_slug_es


class TestGenerateSlug:
    """Input→output assertions per the function's documented behavior."""

    def test_basic_lowercase_and_spaces(self):
        assert generate_slug("Panama Canal History") == "panama-canal-history"

    def test_accents_removed_lowercased(self):
        # á → a, ó → o, lowercased
        assert generate_slug("Construcción del Canal") == "construccion-del-canal"

    def test_enye_and_accent_combined(self):
        # ñ → n, á → a
        assert generate_slug("Año Nuevo en Panamá") == "ano-nuevo-en-panama"

    def test_strips_leading_and_trailing_whitespace(self):
        assert generate_slug("  leading and trailing  ") == "leading-and-trailing"

    def test_collapses_multiple_spaces(self):
        assert generate_slug("Multiple   Spaces") == "multiple-spaces"

    def test_underscores_become_hyphens(self):
        assert generate_slug("snake_case_text") == "snake-case-text"

    def test_special_chars_stripped(self):
        assert generate_slug("Special!@#$Chars") == "specialchars"

    def test_collapses_consecutive_hyphens_and_strips(self):
        assert generate_slug("---leading---hyphens---") == "leading-hyphens"

    def test_empty_string(self):
        assert generate_slug("") == ""

    def test_lowercase_enye(self):
        assert generate_slug("Niño") == "nino"

    def test_uppercase_enye_replaced_with_lowercase_n(self):
        # Source line 33: Ñ is replaced with lowercase "n", then line 36 lowercases everything
        assert generate_slug("Ñandú") == "nandu"


class TestGenerateSlugEs:
    """The Spanish variant is documented as an alias for generate_slug."""

    def test_is_alias_of_generate_slug(self):
        # Per docstring: "Alias for generate_slug since both handle Spanish characters."
        assert generate_slug_es("Año") == generate_slug("Año")
