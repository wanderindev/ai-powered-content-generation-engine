import re

import markdown
import nh3

# Allowlist for sanitizing the HTML produced by ``markdown_to_html``. The set is
# the union of every tag/attribute the configured python-markdown extensions can
# emit — headings (toc), tables, fenced code, line breaks (nl2br), lists. Anything
# outside the allowlist (``<script>``, event handlers, ``<iframe>``, ``<style>``,
# inline styles) is dropped, closing the stored-XSS gap from raw HTML in the
# markdown source. See issue #111.
_ALLOWED_TAGS: set[str] = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "blockquote",
    "pre",
    "code",
    "span",
    "a",
    "img",
    "strong",
    "em",
    "b",
    "i",
    "del",
    "sub",
    "sup",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}

_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    # toc adds ``id`` to headings for in-page anchors.
    "h1": {"id"},
    "h2": {"id"},
    "h3": {"id"},
    "h4": {"id"},
    "h5": {"id"},
    "h6": {"id"},
    # fenced_code emits ``class="language-..."`` for syntax highlighting.
    "code": {"class"},
    "pre": {"class"},
    "span": {"class"},
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    # tables extension emits cell alignment; keep the semantic ``align`` attribute
    # but never inline ``style`` (an XSS surface, stripped by omission).
    "th": {"align"},
    "td": {"align"},
}

# Restrict link/image URL schemes — strips ``javascript:`` and ``data:`` vectors
# while keeping ordinary and relative links.
_ALLOWED_URL_SCHEMES: set[str] = {"http", "https", "mailto"}


def sanitize_html(html: str) -> str:
    """Sanitize HTML against the article allowlist (see ``_ALLOWED_TAGS``)."""
    if not html:
        return ""
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )


def markdown_to_html(content: str) -> str:
    """
    Convert markdown content to sanitized HTML.

    The rendered HTML is passed through ``nh3`` (see ``sanitize_html``) so that
    raw HTML in the markdown source cannot inject scripts or event handlers into
    the page — this function is the single sanitization boundary for every
    ``content_html`` consumer (articles, edu materials, admin dashboard).

    Args:
        content: Markdown formatted string

    Returns:
        Sanitized HTML formatted string
    """
    if not content:
        return ""

    md = markdown.Markdown(
        extensions=[
            "fenced_code",
            "tables",
            "toc",
            "nl2br",
            "sane_lists",
        ],
        extension_configs={
            "toc": {
                "permalink": False,
            }
        },
    )

    return sanitize_html(md.convert(content))


def extract_series_header(content: str) -> tuple[str, str]:
    """
    Extract and remove the series header from article content.

    The series header is the "About this Article" section at the top
    that lists other articles in the series.

    Args:
        content: Full article content in markdown

    Returns:
        Tuple of (series_header, remaining_content)
    """
    if not content:
        return "", ""

    # Find the first horizontal rule (---) which separates header from content
    parts = content.split("\n---\n", 1)

    if len(parts) == 2:
        # Check if the first part looks like a series header
        first_part = parts[0].strip()
        if "About this Article" in first_part or "part" in first_part.lower():
            return first_part, parts[1].strip()

    # No series header found, return full content
    return "", content


def html_to_markdown(html: str) -> str:
    """
    Convert HTML content back to Markdown.

    Args:
        html: HTML formatted string

    Returns:
        Markdown formatted string
    """
    if not html:
        return ""

    from markdownify import markdownify

    return markdownify(
        html,
        heading_style="ATX",
        bullets="-",
        strong_em_symbol="*",
    ).strip()


def strip_bio_links(content: str) -> str:
    """
    Convert bio article links to plain text.

    Bio articles are excluded from MVP, so links like:
    [Christopher Columbus](/en/notable-figures/colonial-era-figures/christopher-columbus)

    Should become just: Christopher Columbus

    Args:
        content: Markdown content with potential bio links

    Returns:
        Content with bio links converted to plain text
    """
    import re

    # Pattern to match markdown links to notable-figures
    # [Link Text](/en/notable-figures/...) or [Link Text](https://panamaincontext.com/en/notable-figures/...)
    pattern = r"\[([^\]]+)\]\([^)]*notable-figures[^)]*\)"

    # Replace with just the link text
    return re.sub(pattern, r"\1", content)


# --- Inline emphasis stripping (#540) ----------------------------------------
# Article bodies carry no bold or italics: Grammarly drops a run whenever it
# rewords the text inside it, and the meaning check reads markdown, so a lost
# ``*`` looks like a changed paragraph and a sound suggestion gets reverted or
# filed for review. The emphasis Opus added was decoration anyway, applied
# inconsistently across articles. The helper removes the markers and keeps the
# words; anything that is not emphasis is shielded first so it survives
# byte-for-byte.
_EMPHASIS_PROTECT_RE = re.compile(
    r"\\[\\`*_{}\[\]()#+\-.!]"  # backslash escapes (markdownify writes \* for a literal *)
    r"|(`+).+?\1"  # inline code spans
    r"|\]\([^)\s]*(?:\s+\"[^\"]*\")?\)"  # link / image targets
    r"|<[a-zA-Z][a-zA-Z0-9+.-]*:[^>\s]+>"  # autolinks
    r"|https?://[^\s)\]>]+"  # bare URLs
)
_EMPHASIS_PATTERNS = (
    # Longest markers first so ***x*** does not leave a stray pair behind. A
    # run's content may not contain its own marker, so an opener that cannot
    # close (``**Note:**text``) is left alone rather than paired with a later
    # run's closer.
    re.compile(r"(?<![\w*])\*\*\*(?=[^\s*])([^*]+?)(?<=[^\s*])\*\*\*(?![\w*])"),
    re.compile(r"(?<![\w*])\*\*(?=[^\s*])((?:(?!\*\*).)+?)(?<=[^\s*])\*\*(?![\w*])"),
    re.compile(r"(?<![\w_])__(?=[^\s_])((?:(?!__).)+?)(?<=[^\s_])__(?![\w_])"),
    re.compile(r"(?<![\w*])\*(?=[^\s*])([^*]+?)(?<=[^\s*])\*(?![\w*])"),
    # Intra-word underscores (snake_case) may sit inside an _italic_ run.
    re.compile(r"(?<![\w_])_(?=[^\s_])((?:(?<=\w)_(?=\w)|[^_])+?)(?<=[^\s_])_(?![\w_])"),
)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_RULE_RE = re.compile(r"^\s*([*_-])(?:\s*\1){2,}\s*$")


def _strip_line_emphasis(line: str) -> str:
    shielded: list[str] = []

    def _shield(match: re.Match) -> str:
        shielded.append(match.group(0))
        return f"\ue000{len(shielded) - 1}\ue001"

    text = _EMPHASIS_PROTECT_RE.sub(_shield, line)
    previous = None
    # Repeat to a fixed point so nested runs (**bold *italic* bold**) unwrap fully.
    while previous != text:
        previous = text
        for pattern in _EMPHASIS_PATTERNS:
            text = pattern.sub(r"\1", text)
    return re.sub(r"\ue000(\d+)\ue001", lambda m: shielded[int(m.group(1))], text)


def strip_inline_emphasis(content: str) -> str:
    """Remove markdown bold / italic markers from ``content``, keeping the text.

    Handles ``***x***``, ``**x**``, ``__x__``, ``*x*`` and ``_x_`` (nested runs
    too). Leaves alone: list markers, horizontal rules (``* * *``, ``***``),
    fenced and inline code, backslash-escaped ``\\*`` / ``\\_``, link and image
    targets, bare URLs, intra-word underscores (``snake_case``) and a lone or
    spaced ``*`` (``5 * 3``). Emphasis never spans lines in stored articles
    (one paragraph per line), so matching is per line. Idempotent.
    """
    if not content:
        return content
    lines = content.split("\n")
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or _RULE_RE.match(line):
            continue
        lines[i] = _strip_line_emphasis(line)
    return "\n".join(lines)
