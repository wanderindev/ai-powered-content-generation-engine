"""Tests for app.utils.markdown — markdown conversion, series-header extraction, and link stripping."""

from app.utils.markdown import (
    extract_series_header,
    html_to_markdown,
    markdown_to_html,
    strip_bio_links,
    strip_inline_emphasis,
)
from app.services.series_sections import split_series_sections


class TestMarkdownToHtml:
    """Markdown→HTML conversion using the configured python-markdown extensions."""

    def test_empty_returns_empty(self):
        assert markdown_to_html("") == ""

    def test_h1_produces_h1_tag(self):
        out = markdown_to_html("# Hello")
        assert "<h1" in out
        assert "Hello" in out

    def test_fenced_code_block(self):
        out = markdown_to_html("```python\ncode = 1\n```")
        assert "<pre>" in out or "<pre " in out
        assert "<code" in out

    def test_table_extension(self):
        md = "| a | b |\n| - | - |\n| 1 | 2 |\n"
        out = markdown_to_html(md)
        assert "<table>" in out

    def test_nl2br_produces_break(self):
        out = markdown_to_html("line1\nline2")
        assert "<br />" in out or "<br>" in out


class TestMarkdownSanitization:
    """The rendered HTML is sanitized via nh3 against an allowlist (issue #111).

    Raw HTML embedded in the markdown source must not survive into the output if
    it can execute, while every tag/attribute the configured extensions legitimately
    emit must be preserved.
    """

    # --- malicious HTML is stripped --------------------------------------------

    def test_script_tag_removed(self):
        out = markdown_to_html("Hello\n\n<script>alert('xss')</script>")
        assert "<script" not in out
        assert "alert" not in out

    def test_img_onerror_handler_removed(self):
        out = markdown_to_html('<img src="x" onerror="alert(1)">')
        assert "onerror" not in out
        # the img element itself may remain, but the event handler must be gone

    def test_event_handler_attribute_removed(self):
        out = markdown_to_html('<p onclick="steal()">click</p>')
        assert "onclick" not in out
        assert "steal" not in out

    def test_javascript_href_removed(self):
        out = markdown_to_html('<a href="javascript:alert(1)">x</a>')
        assert "javascript:" not in out

    def test_iframe_removed(self):
        out = markdown_to_html('<iframe src="https://evil.example"></iframe>')
        assert "<iframe" not in out

    def test_style_tag_removed(self):
        out = markdown_to_html("<style>body{display:none}</style>text")
        assert "<style" not in out

    def test_inline_style_attribute_removed(self):
        out = markdown_to_html('<p style="position:fixed">hi</p>')
        assert "style=" not in out

    # --- legitimate formatting is preserved ------------------------------------

    def test_heading_id_anchor_preserved(self):
        # toc adds id="..." to headings for in-page navigation
        out = markdown_to_html("## Section Title")
        assert "id=" in out

    def test_fenced_code_language_class_preserved(self):
        out = markdown_to_html("```python\nx = 1\n```")
        assert "language-python" in out

    def test_table_structure_preserved(self):
        md = "| a | b |\n| - | - |\n| 1 | 2 |\n"
        out = markdown_to_html(md)
        assert "<table>" in out
        assert "<td>" in out

    def test_safe_link_preserved(self):
        out = markdown_to_html("[example](https://example.com)")
        assert 'href="https://example.com"' in out

    def test_relative_link_preserved(self):
        out = markdown_to_html("[blog](/en/blog/some-article)")
        assert 'href="/en/blog/some-article"' in out

    def test_formatting_tags_preserved(self):
        out = markdown_to_html("**bold** and *italic*")
        assert "<strong>" in out
        assert "<em>" in out


class TestExtractSeriesHeader:
    """Extract an 'About this Article' / 'part' header above the first `\\n---\\n`."""

    def test_empty_returns_empty_pair(self):
        assert extract_series_header("") == ("", "")

    def test_no_separator_returns_empty_header_and_original(self):
        content = "Just a regular article with no separator."
        assert extract_series_header(content) == ("", content)

    def test_about_this_article_marker_triggers_split(self):
        content = "About this Article\nIntro\n---\nBody paragraph."
        header, body = extract_series_header(content)
        assert header == "About this Article\nIntro"
        assert body == "Body paragraph."

    def test_part_marker_triggers_split(self):
        # The substring "part" appears in "part 1 of 3"; the source matches
        # case-insensitively (line 57 lowercases first_part).
        content = "This is part 1 of 3\n---\nThe real content."
        header, body = extract_series_header(content)
        assert header == "This is part 1 of 3"
        assert body == "The real content."

    def test_separator_without_marker_returns_full_content(self):
        # Has a "---" separator but neither "About this Article" nor "part"
        content = "Just an intro\n---\nThe body."
        assert extract_series_header(content) == ("", content)


class TestHtmlToMarkdown:
    """HTML→Markdown round-trip using markdownify with the configured options."""

    def test_empty_returns_empty(self):
        assert html_to_markdown("") == ""

    def test_h1_becomes_atx_heading(self):
        out = html_to_markdown("<h1>Hello</h1>")
        assert "# Hello" in out

    def test_strong_uses_asterisk_symbol(self):
        # strong_em_symbol="*" → <strong> becomes **bold**
        out = html_to_markdown("<strong>bold</strong>")
        assert "*bold*" in out

    def test_unordered_list_uses_hyphen_bullet(self):
        out = html_to_markdown("<ul><li>a</li><li>b</li></ul>")
        # Lines start with "- " per bullets="-"
        assert "- a" in out
        assert "- b" in out


class TestStripBioLinks:
    """Links to notable-figures pages are flattened to plain text."""

    def test_relative_bio_link_stripped(self):
        md = "[Christopher Columbus](/en/notable-figures/colonial-era-figures/christopher-columbus)"
        assert strip_bio_links(md) == "Christopher Columbus"

    def test_absolute_bio_link_stripped(self):
        md = "[Balboa](https://panamaincontext.com/es/notable-figures/balboa)"
        assert strip_bio_links(md) == "Balboa"

    def test_non_bio_link_preserved(self):
        md = "[Regular Link](https://example.com/some/path)"
        assert strip_bio_links(md) == md

    def test_mixed_only_bio_link_stripped(self):
        md = (
            "See [Balboa](/en/notable-figures/balboa) and "
            "[the article](https://example.com/article) for details."
        )
        out = strip_bio_links(md)
        assert "Balboa" in out
        assert "[Balboa]" not in out
        # The non-bio link is left intact
        assert "[the article](https://example.com/article)" in out

    def test_empty_string(self):
        assert strip_bio_links("") == ""


class TestStripInlineEmphasis:
    """#540 — article bodies carry no bold/italics; everything else survives."""

    def test_bold_italic_and_bold_italic(self):
        md = "The **Spanish Crown** sent *galleons* to ***Portobelo***."
        assert strip_inline_emphasis(md) == "The Spanish Crown sent galleons to Portobelo."

    def test_underscore_forms(self):
        assert strip_inline_emphasis("__bold__ and _italic_") == "bold and italic"

    def test_nested_runs_unwrap(self):
        md = "**bold *nested* bold** and *it **nest** it*"
        assert strip_inline_emphasis(md) == "bold nested bold and it nest it"

    def test_titles_and_foreign_terms_are_stripped_too(self):
        md = "*Recopilación de Leyes de Indias* (1680) and the *encomienda*."
        assert strip_inline_emphasis(md) == "Recopilación de Leyes de Indias (1680) and the encomienda."

    def test_headings_lose_emphasis_but_keep_level(self):
        assert strip_inline_emphasis("## **Heading**") == "## Heading"

    def test_list_markers_and_rules_untouched(self):
        md = "* item with *emph*\n- other\n* * *\n***\n---\n___"
        assert strip_inline_emphasis(md) == "* item with emph\n- other\n* * *\n***\n---\n___"

    def test_spaced_or_intra_word_asterisks_untouched(self):
        md = "5 * 3 * 2 = 30, and 5*3*2, a lone * star"
        assert strip_inline_emphasis(md) == md

    def test_intra_word_underscores_untouched(self):
        md = "snake_case_name and file_name_here"
        assert strip_inline_emphasis(md) == md
        assert strip_inline_emphasis("_a snake_case term_") == "a snake_case term"

    def test_escaped_markers_untouched(self):
        md = "An escaped \\*literal\\* and \\_this\\_"
        assert strip_inline_emphasis(md) == md

    def test_code_spans_and_fences_untouched(self):
        md = "Code `**not**` here\n```\n**in fence**\n```\nafter **fence**"
        assert strip_inline_emphasis(md) == "Code `**not**` here\n```\n**in fence**\n```\nafter fence"

    def test_link_text_stripped_but_urls_untouched(self):
        md = "[**link** text](https://ex.com/a_b_/c*d*) and https://x.com/_u_/"
        assert strip_inline_emphasis(md) == "[link text](https://ex.com/a_b_/c*d*) and https://x.com/_u_/"

    def test_image_target_untouched(self):
        assert strip_inline_emphasis("![alt *x*](https://cdn/x_y_.png)") == "![alt x](https://cdn/x_y_.png)"

    def test_unclosable_opener_is_not_paired_with_a_later_run(self):
        md = "**Note:**text stays, **bold**, and (*paren*)"
        assert strip_inline_emphasis(md) == "**Note:**text stays, bold, and (paren)"

    def test_idempotent_and_empty(self):
        md = "The **Spanish Crown** and *galleons*."
        once = strip_inline_emphasis(md)
        assert strip_inline_emphasis(once) == once
        assert strip_inline_emphasis("") == ""
        assert strip_inline_emphasis(None) is None

    def test_body_only_strip_keeps_series_sections_byte_identical(self):
        """The one-off script strips the body and re-attaches the sections."""
        about = "**Acerca de este artículo**\n\nParte 2 de *la serie*.\n\n---\n\n"
        body = "## Inicio\n\nLa **Corona** envió *galeones*.\n"
        cont = "\n**Continuar leyendo**\n\n- [Parte 3](/es/articulos/x)"
        content = about + body + cont
        a, b, c = split_series_sections(content)
        assert a + b + c == content
        rebuilt = a + strip_inline_emphasis(b) + c
        assert rebuilt == content.replace("**Corona**", "Corona").replace("*galeones*", "galeones")
        assert "*la serie*" in rebuilt and "**Continuar leyendo**" in rebuilt
