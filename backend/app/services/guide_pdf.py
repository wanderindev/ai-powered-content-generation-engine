"""Branded study-guide PDF export (#324, re-skinned to the design-kit templates).

Renders a guide's structured blocks (``EduMaterial.content_blocks`` /
``content_blocks_es``) into the fixed-page study-guide template system
(816×1056px US Letter pages authored in the design kit's
``ui_kits/study_guides/``) and stores the PDF as the row's ``file_url``.

Pipeline (blocks path): block → kit-vocabulary HTML fragments → a headless-
chromium **measuring pass** (fragment heights at content width) → a pure
greedy **packing pass** (fragments into 816×1056 ``.page`` divs, splitting
list-type blocks at item boundaries) → final render with the kit's print CSS
(``@page {size: letter; margin: 0}``, one ``.page`` per sheet) → DO Spaces.

Brand rules (kit README + operator decision 2026-07-01): the logo appears on
the cover and closing pages ONLY; interior pages carry a quiet
``.g-foot`` "brand · guide title / page number" footer. Guide PDFs are **not**
watermarked.

Legacy markdown-only guides (no ``content_blocks``) keep the old flowing-
document renderer (``build_guide_html`` + ``render_html_to_pdf``).

Human-picked images (#6 of the redesign): ``image``/``map``/``cover`` blocks
render their ``media_path`` (an absolute CDN URL chosen by an editor in the
admin UI) — there is no automatic media matching in this path. Blocks without
a selection render a kit-styled dashed placeholder showing the ``image_query``.
"""

import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.edu_material import EduMaterial
from app.services.image_storage import ImageStorageService
from app.services.slide_html import heroicons_script
from app.utils.markdown import markdown_to_html

logger = logging.getLogger(__name__)

_ASSETS = Path(__file__).resolve().parent.parent / "slide_assets"
# styles.css is import-only; flatten the token files + the guide kit CSS.
_GUIDE_CSS_FILES = (
    "tokens/colors.css",
    "tokens/typography.css",
    "tokens/spacing.css",
    "tokens/effects.css",
    "guide.css",
)
_SET_CONTENT_TIMEOUT_MS = 20_000
_FINAL_SET_CONTENT_TIMEOUT_MS = 30_000  # final pass loads remote CDN photos

# --- Page geometry (mirrors guide.css :root) ---------------------------------
PAGE_W = 816
PAGE_H = 1056
PAGE_PAD = 76
CONTENT_W = PAGE_W - 2 * PAGE_PAD  # 664
# Vertical budget for packed content: below the top padding, above the
# .g-foot zone (bottom:40 + padding-top:14 + text line ≈ 70) with breathing room.
BOTTOM_RESERVE = 110
USABLE_H = PAGE_H - PAGE_PAD - BOTTOM_RESERVE  # 870
FRAG_GAP = 20  # .g-stack rhythm between fragments
HEAD_GAP = 30  # space between the section head and the first fragment

_STRINGS = {
    "en": {
        "eyebrow": "Study Guide",
        "brand": "Panama In Context",
        "section": "Section",
        "continued": "continued",
        "prepared": "Prepared by Diego Raúl Feliu",
        "meta_what": "Academic Excursions",
        "thanks": "Thank You",
        "closing_sub": "We look forward to exploring Panama's history with you on the excursion.",
        "missing_photo": "Photo to be selected",
        "did_you_know": "Did you know?",
        "key_terms": "Key Terms",
    },
    "es": {
        "eyebrow": "Guía de estudio",
        "brand": "Panama In Context",
        "section": "Sección",
        "continued": "continuación",
        "prepared": "Preparada por Diego Raúl Feliu",
        "meta_what": "Excursiones académicas",
        "thanks": "¡Gracias!",
        "closing_sub": "Esperamos explorar la historia de Panamá contigo durante la excursión.",
        "missing_photo": "Foto por seleccionar",
        "did_you_know": "¿Sabías que…?",
        "key_terms": "Términos clave",
    },
}

_CONTACT_WEB = "panamaincontext.com"
_CONTACT_EMAIL = "diego@panamaincontext.com"


@lru_cache(maxsize=1)
def _guide_css() -> str:
    return "\n".join((_ASSETS / name).read_text() for name in _GUIDE_CSS_FILES)


@lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    """The square hex emblem — the only logo the guide kit uses."""
    b64 = base64.b64encode((_ASSETS / "logo_512x512.png").read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _esc(text: str | None) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _strings(lang: str) -> dict:
    return _STRINGS.get(lang, _STRINGS["es"])


# --- Block → kit-vocabulary fragments ----------------------------------------

# Bold the short lead of a "Lead — detail" item, matching the kit's bullet style.
_LEAD_RE = re.compile(r"^(?P<lead>[^—]{2,60}?) — (?P<rest>.+)$", re.DOTALL)


def _lead_html(text: str) -> str:
    m = _LEAD_RE.match(text or "")
    if m:
        return f"<b>{_esc(m.group('lead'))}</b> — {_esc(m.group('rest'))}"
    return _esc(text)


def _icon(name: str, size: int) -> str:
    return f'<i data-hicon="{name}" style="font-size:{size}px"></i>'


@dataclass
class _Frag:
    """One packable content unit. Atomic fragments carry ``html``; splittable
    list-type fragments carry per-item ``items`` plus the wrapper to rebuild a
    partial list on the next page."""

    kind: str
    html: str | None = None
    items: list[str] | None = None
    wrap_open: str = ""
    wrap_close: str = ""
    item_gap: float = 0.0


def _bullet_li(text: str, icon: str = "check-circle-solid", size: int = 20) -> str:
    return f'<li><span class="mk">{_icon(icon, size)}</span><div>{_lead_html(text)}</div></li>'


def _figure_html(b: dict, lang: str) -> str:
    """A picked photo in the kit's ring frame, or the editor placeholder."""
    caption = b.get("caption")
    if b.get("media_path"):
        cap = f'<p class="g-caption">{_esc(caption)}</p>' if caption else ""
        return (
            f'<div class="g-figure"><img class="g-photo" src="{_esc(b["media_path"])}" '
            f'alt="{_esc(caption)}" style="height:260px"><span class="ring"></span></div>{cap}'
        )
    s = _strings(lang)
    cap = f'<p class="c">{_esc(caption)}</p>' if caption else ""
    query = b.get("image_query") or ""
    return (
        f'<div class="g-missing"><p class="q">{_esc(s["missing_photo"])}: {_esc(query)}</p>'
        f"{cap}</div>"
    )


def _terms_callout_frag(b: dict, lang: str) -> _Frag:
    """A key-terms box, splittable at term boundaries (a long vocabulary list
    must flow onto continuation pages — packed atomically it would clip)."""
    label = b.get("label") or _strings(lang)["key_terms"]
    return _Frag(
        "callout",
        items=[
            f"<dt>{_esc(t.get('term'))}</dt><dd>{_esc(t.get('definition'))}</dd>"
            for t in b.get("terms", [])
        ],
        wrap_open=(
            f'<div class="g-callout"><p class="lbl">{_icon("book-open", 16)} {_esc(label)}</p><dl>'
        ),
        wrap_close="</dl></div>",
        item_gap=12,  # .g-callout dl flex gap
    )


def _callout_html(b: dict, lang: str) -> str:
    label = b.get("label") or _strings(lang)["did_you_know"]
    return (
        f'<div class="g-callout"><p class="lbl">{_icon("sparkles-solid", 15)} {_esc(label)}</p>'
        f'<p style="margin:0;font-size:15px;line-height:1.5;color:var(--text-secondary)">'
        f"{_esc(b.get('text'))}</p></div>"
    )


def _block_to_frag(b: dict, lang: str, mc_number: int) -> _Frag | None:
    t = b.get("type")
    if t == "paragraph":
        return _Frag("paragraph", html=f'<p class="g-body">{_esc(b.get("text"))}</p>')
    if t == "bullets":
        return _Frag(
            "bullets",
            items=[_bullet_li(i) for i in b.get("items", [])],
            wrap_open='<ul class="g-bullets">',
            wrap_close="</ul>",
            item_gap=16,
        )
    if t == "ordered_list":
        items = [
            f'<li><span class="n">{n}</span><div>{_lead_html(i)}</div></li>'
            for n, i in enumerate(b.get("items", []), 1)
        ]
        return _Frag(
            "ordered_list",
            items=items,
            wrap_open='<ol class="g-numbers">',
            wrap_close="</ol>",
            item_gap=18,
        )
    if t == "quote":
        cite = f"<cite>— {_esc(b.get('attribution'))}</cite>" if b.get("attribution") else ""
        return _Frag("quote", html=f'<div class="g-quote"><p>{_esc(b.get("text"))}</p>{cite}</div>')
    if t == "timeline":
        evs = [
            f'<div class="ev"><span class="node"></span><div class="yr">{_esc(e.get("label"))}'
            f'</div><p class="d">{_esc(e.get("text"))}</p></div>'
            for e in b.get("events", [])
        ]
        return _Frag(
            "timeline",
            items=evs,
            wrap_open='<div class="p-vtimeline"><div class="track">',
            wrap_close="</div></div>",
            item_gap=30,  # .p-vtimeline .track flex gap
        )
    if t == "callout":
        if b.get("terms"):
            return _terms_callout_frag(b, lang)
        return _Frag("callout", html=_callout_html(b, lang))
    if t == "mc_question":
        choices = "".join(f"<li>{_esc(c)}</li>" for c in b.get("choices", []))
        return _Frag(
            "mc_question",
            html=(
                f'<div class="g-mcq"><p class="q"><span class="qn">{mc_number}.</span> '
                f'{_esc(b.get("question"))}</p><ol class="choices">{choices}</ol></div>'
            ),
        )
    if t == "image":
        return _Frag("image", html=_figure_html(b, lang))
    if t == "map":
        locs = "".join(_bullet_li(loc, icon="map-pin") for loc in b.get("locations", []))
        loc_list = f'<ul class="g-bullets" style="margin-top:16px">{locs}</ul>' if locs else ""
        return _Frag("map", html=_figure_html(b, lang) + loc_list)
    return None


def _group_sections(body: list[dict]) -> list[tuple[str | None, list[dict]]]:
    """Group body blocks into (section_title, blocks) runs, in order."""
    sections: list[tuple[str | None, list[dict]]] = []
    current_title: str | None = None
    current: list[dict] = []
    started = False
    for b in body:
        if b.get("type") == "section":
            if started and (current or current_title is not None):
                sections.append((current_title, current))
            current_title, current, started = b.get("title"), [], True
        else:
            current.append(b)
            started = True
    if started and (current or current_title is not None):
        sections.append((current_title, current))
    return sections


# --- Fixed pages (cover / heads / closing) -----------------------------------


def _cover_page_html(cover: dict | None, title: str, grade_band: str | None, lang: str) -> str:
    s = _strings(lang)
    cover = cover or {}
    if cover.get("media_path"):
        hero = (
            f'<div class="hero"><img src="{_esc(cover["media_path"])}" alt="">'
            f'<div class="tint"></div></div>'
        )
    else:
        hero = '<div class="hero hero-solid"></div>'
    subtitle = cover.get("subtitle")
    sub = f'<p class="sub">{_esc(subtitle)}</p>' if subtitle else ""
    what = f"{s['meta_what']} · {grade_band}" if grade_band else s["meta_what"]
    return (
        f'<div class="page p-title">{hero}<div class="body">'
        f'<div class="brandline"><img src="{_logo_data_uri()}" alt="Panama In Context">'
        f"<span>PANAMA IN CONTEXT</span></div>"
        f'<p class="g-eyebrow" style="margin-top:26px">{_esc(s["eyebrow"])}</p>'
        f"<h1>{_esc(cover.get('title') or title)}</h1>{sub}"
        f'<div class="meta"><span class="who">{_esc(s["prepared"])}</span>'
        f'<span class="what">{_esc(what)}</span></div>'
        f"</div></div>"
    )


def _closing_page_html(lang: str) -> str:
    s = _strings(lang)
    return (
        f'<div class="page p-closing"><div class="inner">'
        f'<img class="logo" src="{_logo_data_uri()}" alt="Panama In Context">'
        f"<h2>{_esc(s['thanks'])}</h2>"
        f'<p class="sub">{_esc(s["closing_sub"])}</p>'
        f'<div class="contact">'
        f'<span><span class="ic">{_icon("globe-americas", 20)}</span>{_CONTACT_WEB}</span>'
        f'<span><span class="ic">{_icon("envelope", 20)}</span>{_CONTACT_EMAIL}</span>'
        f"</div></div></div>"
    )


def _section_head_html(idx: int, title: str | None, lang: str, cont: bool = False) -> str:
    s = _strings(lang)
    if cont:
        label = f"{title or s['brand']} · {s['continued']}"
        return f'<p class="g-cont-eyebrow">{_esc(label)}</p>'
    if not title:
        return f'<div class="g-head"><p class="g-eyebrow">{_esc(s["eyebrow"])}</p></div>'
    return (
        f'<div class="g-head"><p class="g-eyebrow">{s["section"]} {idx}</p>'
        f'<h2 class="g-title">{_esc(title)}</h2><div class="g-rule"></div></div>'
    )


# --- Packing (pure — unit-testable without a browser) -------------------------


@dataclass
class _Unit:
    """One packable unit: a whole atomic fragment, or one item of a list.

    ``h`` is the unit's own height; ``wrap_h`` is the list wrapper's empty
    height (box padding + label for callouts, ~0 for plain lists), paid once
    per page the list appears on."""

    frag: int
    item: int | None
    h: float
    item_gap: float = 0.0
    wrap_h: float = 0.0


@dataclass
class _PackedSection:
    idx: int  # 1-based section number
    title: str | None
    pages: list[list[_Unit]] = field(default_factory=list)


def pack_units(
    units: list[_Unit],
    head_h: float,
    cont_head_h: float,
    usable_h: float = USABLE_H,
    frag_gap: float = FRAG_GAP,
    head_gap: float = HEAD_GAP,
) -> list[list[_Unit]]:
    """Greedily pack a section's units into pages.

    The first page starts below the section head; continuation pages below the
    smaller continuation eyebrow. Consecutive units of the same list fragment
    use the list's own item gap; unrelated fragments use the stack gap. A unit
    that starts a new run of its list (first on a page, or after another
    fragment) also pays the list's ``wrap_h``, since the wrapper is rebuilt
    there. A unit taller than an empty page is placed alone and clipped
    (logged) — with 3-4-sentence paragraphs this indicates degenerate content,
    not a layout bug.
    """
    pages: list[list[_Unit]] = []
    cur: list[_Unit] = []
    used = head_h + head_gap
    prev: _Unit | None = None
    for u in units:
        same_run = prev is not None and prev.frag == u.frag
        gap = 0.0 if not cur else (u.item_gap if same_run else frag_gap)
        need = u.h if same_run else u.h + u.wrap_h
        if cur and used + gap + need > usable_h:
            pages.append(cur)
            cur, used, gap, prev = [], cont_head_h + head_gap, 0.0, None
            need = u.h + u.wrap_h  # new page restarts the run
        if not cur and used + need > usable_h:
            logger.warning(
                "Guide fragment taller than a page (%.0fpx > %.0fpx available) — clipping",
                need,
                usable_h - used,
            )
        cur.append(u)
        used += gap + need
        prev = u
    if cur:
        pages.append(cur)
    return pages


def _units_for_frags(frags: list[_Frag], heights: dict[str, float], key_prefix: str) -> list[_Unit]:
    units: list[_Unit] = []
    for i, f in enumerate(frags):
        if f.items is not None:
            # Items are measured inside their wrapper; subtract the empty
            # wrapper's height so a merged run isn't charged for it per item.
            wrap_h = heights.get(f"{key_prefix}f{i}w", 0.0)
            for j in range(len(f.items)):
                h = max(0.0, heights[f"{key_prefix}f{i}i{j}"] - wrap_h)
                units.append(_Unit(frag=i, item=j, h=h, item_gap=f.item_gap, wrap_h=wrap_h))
        else:
            units.append(_Unit(frag=i, item=None, h=heights[f"{key_prefix}f{i}"]))
    return units


def _page_units_html(frags: list[_Frag], units: list[_Unit]) -> str:
    """Rebuild a page's fragment HTML, merging consecutive same-list units."""
    parts: list[str] = []
    i = 0
    while i < len(units):
        u = units[i]
        f = frags[u.frag]
        if u.item is None:
            parts.append(f.html or "")
            i += 1
            continue
        run = [u]
        while i + 1 < len(units) and units[i + 1].frag == u.frag:
            i += 1
            run.append(units[i])
        assert f.items is not None
        parts.append(f.wrap_open + "".join(f.items[x.item] for x in run) + f.wrap_close)
        i += 1
    return "".join(parts)


# --- Measuring + final render (headless chromium) -----------------------------


def _measure_snippets(frags_by_section: list[list[_Frag]], heads: list[str]) -> dict[str, str]:
    """id → snippet HTML for everything whose height the packer needs."""
    snippets: dict[str, str] = {}
    for s_i, frags in enumerate(frags_by_section):
        prefix = f"s{s_i}"
        for i, f in enumerate(frags):
            if f.items is not None:
                snippets[f"{prefix}f{i}w"] = f.wrap_open + f.wrap_close
                for j, item in enumerate(f.items):
                    snippets[f"{prefix}f{i}i{j}"] = f.wrap_open + item + f.wrap_close
            else:
                snippets[f"{prefix}f{i}"] = f.html or ""
    for s_i, head in enumerate(heads):
        snippets[f"h{s_i}"] = head
    return snippets


def _measure_doc(snippets: dict[str, str]) -> str:
    frags = "".join(
        f'<div class="mfrag" id="{k}" style="width:{CONTENT_W}px">{v}</div>'
        for k, v in snippets.items()
    )
    return (
        f'<!DOCTYPE html><html><head><meta charset="UTF-8">'
        f"<style>{_guide_css()}</style></head><body>"
        f'<div class="page" style="height:auto;overflow:visible">{frags}</div>'
        f"</body></html>"
    )


_MEASURE_JS = (
    "() => Object.fromEntries(Array.from(document.querySelectorAll('.mfrag'))"
    ".map(e => [e.id, e.getBoundingClientRect().height]))"
)


def render_guide_pdf_from_blocks(
    blocks: list[dict], title: str, grade_band: str | None, lang: str = "es"
) -> bytes:
    """Render structured guide blocks into a kit-faithful fixed-page PDF."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - dep is in requirements.txt
        raise RuntimeError("playwright is not installed") from e

    s = _strings(lang)
    cover = next((b for b in blocks if b.get("type") == "cover"), None)
    body = [b for b in blocks if b.get("type") != "cover"]
    sections = _group_sections(body)

    # Build fragments per section; MC questions number sequentially guide-wide.
    frags_by_section: list[list[_Frag]] = []
    mc_number = 0
    for _, section_blocks in sections:
        frags: list[_Frag] = []
        for b in section_blocks:
            if b.get("type") == "mc_question":
                mc_number += 1
            frag = _block_to_frag(b, lang, mc_number)
            if frag:
                frags.append(frag)
        frags_by_section.append(frags)

    heads = [_section_head_html(i + 1, title_, lang) for i, (title_, _) in enumerate(sections)]
    cont_heads = [
        _section_head_html(i + 1, title_, lang, cont=True) for i, (title_, _) in enumerate(sections)
    ]
    snippets = _measure_snippets(frags_by_section, heads)
    for i, ch in enumerate(cont_heads):
        snippets[f"ch{i}"] = ch

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception as e:
            raise RuntimeError(
                "Failed to launch headless chromium — is the browser binary installed "
                "(`playwright install chromium`)?"
            ) from e
        try:
            page = browser.new_page(viewport={"width": PAGE_W, "height": PAGE_H})

            # Pass 1: measure fragment heights at content width.
            page.set_content(
                _measure_doc(snippets), wait_until="load", timeout=_SET_CONTENT_TIMEOUT_MS
            )
            page.add_script_tag(content=heroicons_script())
            heights: dict[str, float] = page.evaluate(_MEASURE_JS)

            # Pass 2: pack into pages (pure) and assemble the final document.
            content_pages: list[str] = []
            page_no = 2  # the cover is page 1 (unnumbered, per the kit)
            foot_brand = f"{s['brand']} · {title}"
            for s_i, frags in enumerate(frags_by_section):
                units = _units_for_frags(frags, heights, f"s{s_i}")
                packed = pack_units(units, heights[f"h{s_i}"], heights[f"ch{s_i}"])
                for p_i, page_units in enumerate(packed):
                    head = heads[s_i] if p_i == 0 else cont_heads[s_i]
                    body_html = _page_units_html(frags, page_units)
                    content_pages.append(
                        f'<div class="page"><div class="page-pad">{head}'
                        f'<div class="g-stack" style="margin-top:{HEAD_GAP}px">{body_html}</div>'
                        f'<div class="g-foot"><span class="brand">{_esc(foot_brand)}</span>'
                        f'<span class="num">{page_no}</span></div>'
                        f"</div></div>"
                    )
                    page_no += 1

            final_html = (
                f'<!DOCTYPE html><html lang="{lang}"><head><meta charset="UTF-8">'
                f"<style>{_guide_css()}</style></head>"
                f'<body class="desk"><div class="doc">'
                f"{_cover_page_html(cover, title, grade_band, lang)}"
                f"{''.join(content_pages)}"
                f"{_closing_page_html(lang)}"
                f"</div></body></html>"
            )
            page.set_content(final_html, wait_until="load", timeout=_FINAL_SET_CONTENT_TIMEOUT_MS)
            page.add_script_tag(content=heroicons_script())
            return page.pdf(
                prefer_css_page_size=True,  # @page {size: letter; margin: 0} from guide.css
                print_background=True,
                display_header_footer=False,
            )
        finally:
            browser.close()


# --- Legacy markdown path (guides generated before #340) -----------------------

# Print-document stylesheet for the legacy flowing renderer, built on the brand
# CSS variables. Sizes are tuned for a Letter page.
_DOC_CSS = """
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; font-family: var(--font-sans); color: var(--text-body);
  font-size: 15px; line-height: 1.65; }
.guide-header { display: flex; align-items: flex-end; justify-content: space-between;
  gap: 24px; border-bottom: 3px solid var(--color-brand); padding-bottom: 14px; margin-bottom: 26px; }
.guide-header img { height: 40px; width: auto; }
.guide-header .meta { text-align: right; }
.guide-eyebrow { color: var(--color-brand); font-weight: 600; letter-spacing: .05em;
  text-transform: uppercase; font-size: 11px; }
.guide-title { color: var(--text-heading); font-size: 25px; font-weight: 600;
  letter-spacing: -.02em; margin: 5px 0 0; line-height: 1.2; }
.guide-band { color: var(--text-muted); font-size: 12px; margin-top: 3px; }
.content h1 { color: var(--brand-900); font-size: 22px; font-weight: 600; margin: 26px 0 10px;
  letter-spacing: -.02em; }
.content h2 { color: var(--brand-700); font-size: 18px; font-weight: 600; margin: 24px 0 9px;
  padding-bottom: 5px; border-bottom: 2px solid var(--brand-100); page-break-after: avoid; }
.content h3 { color: var(--brand-600); font-size: 15.5px; font-weight: 600; margin: 18px 0 6px;
  page-break-after: avoid; }
.content h4 { color: var(--text-heading); font-size: 14px; font-weight: 600; margin: 14px 0 4px; }
.content p { margin: 0 0 11px; }
.content ul, .content ol { margin: 0 0 12px; padding-left: 22px; }
.content li { margin: 0 0 5px; padding-left: 2px; }
.content a { color: var(--text-link); text-decoration: none; }
.content strong { color: var(--text-heading); font-weight: 600; }
.content em { font-style: italic; }
.content blockquote { margin: 14px 0; padding: 8px 16px; border-left: 4px solid var(--brand-300);
  background: var(--brand-50); color: var(--text-secondary); border-radius: 4px; }
.content code { font-family: var(--font-mono); font-size: .9em; background: var(--surface-subtle);
  padding: 1px 5px; border-radius: 4px; }
.content pre { background: var(--surface-subtle); padding: 12px; border-radius: 6px;
  white-space: pre-wrap; word-wrap: break-word; }
.content pre code { background: none; padding: 0; }
.content table { width: 100%; border-collapse: collapse; margin: 14px 0; font-size: 13px; }
.content th { background: var(--brand-50); color: var(--text-heading); text-align: left;
  padding: 7px 10px; border: 1px solid var(--border); font-weight: 600; }
.content td { padding: 7px 10px; border: 1px solid var(--border); vertical-align: top; }
.content img { max-width: 100%; height: auto; border-radius: 6px; }
.content hr { border: none; border-top: 1px solid var(--border); margin: 22px 0; }
.content h2, .content h3, .content h4 { page-break-inside: avoid; }
.content li, .content tr, .content blockquote { page-break-inside: avoid; }
.content blockquote footer { color: var(--text-muted); font-size: 12px; margin-top: 5px; font-style: normal; }
"""

_FOOTER_TEMPLATE = (
    '<div style="font-size:9px;color:#9ca3af;width:100%;text-align:center;'
    'font-family:Arial,sans-serif;">'
    '<span class="pageNumber"></span> / <span class="totalPages"></span></div>'
)

_TOKEN_CSS = ("tokens/colors.css", "tokens/typography.css")


@lru_cache(maxsize=1)
def _brand_tokens_css() -> str:
    return "\n".join((_ASSETS / name).read_text() for name in _TOKEN_CSS)


@lru_cache(maxsize=1)
def _logo_h_data_uri() -> str:
    b64 = base64.b64encode((_ASSETS / "logo-h-116.png").read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def build_guide_html(content_md: str, title: str, grade_band: str | None, lang: str = "es") -> str:
    """Wrap a legacy guide's markdown in a branded, print-ready HTML document.

    The markdown is sanitised to HTML via :func:`markdown_to_html` (the same
    allowlist the admin preview uses), then placed under a brand header. Returns
    a self-contained page (brand tokens + doc CSS inlined, logo as a data URI).
    """
    content_html = markdown_to_html(content_md or "")
    eyebrow = "Guía de estudio" if lang == "es" else "Study guide"
    band_html = f'<div class="guide-band">{grade_band}</div>' if grade_band else ""
    safe_title = _esc(title)
    return (
        f'<!DOCTYPE html>\n<html lang="{lang}">\n<head>\n<meta charset="UTF-8">\n'
        f"<style>{_brand_tokens_css()}</style>\n<style>{_DOC_CSS}</style>\n</head>\n"
        f"<body>\n"
        f'<header class="guide-header">'
        f'<img src="{_logo_h_data_uri()}" alt="Panama In Context">'
        f'<div class="meta"><div class="guide-eyebrow">{eyebrow}</div>'
        f'<div class="guide-title">{safe_title}</div>{band_html}</div>'
        f"</header>\n"
        f'<main class="content">\n{content_html}\n</main>\n'
        f"</body>\n</html>"
    )


def render_html_to_pdf(html: str) -> bytes:
    """Render a standalone HTML document to PDF bytes via headless chromium.

    Legacy flowing-document path (auto-paginated by chromium with a page-number
    footer). Raises RuntimeError if the browser binary isn't installed.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - dep is in requirements.txt
        raise RuntimeError("playwright is not installed") from e

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception as e:
            raise RuntimeError(
                "Failed to launch headless chromium — is the browser binary installed "
                "(`playwright install chromium`)?"
            ) from e
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load", timeout=_SET_CONTENT_TIMEOUT_MS)
            return page.pdf(
                format="Letter",
                print_background=True,
                margin={"top": "2.2cm", "bottom": "1.9cm", "left": "1.8cm", "right": "1.8cm"},
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=_FOOTER_TEMPLATE,
            )
        finally:
            browser.close()


# --- Orchestrator --------------------------------------------------------------


def generate_guide_pdf(material: EduMaterial, db: Session, language: str = "es") -> EduMaterial:
    """Render a guide material into a branded PDF and store it on ``file_url``.

    Structured guides (#340) render through the kit-faithful fixed-page
    renderer; legacy markdown-only guides keep the flowing-document renderer.
    Guide PDFs are NOT watermarked (operator decision 2026-07-01) — brand
    presence is the logo on the cover and closing pages only. Mutates the row;
    caller commits. Raises ``ValueError`` if the material isn't a guide or
    lacks content in the requested language.
    """
    if material.material_type != "guide":
        raise ValueError("Material is not a study guide")

    blocks = material.content_blocks_es if language == "es" else material.content_blocks
    if blocks:
        pdf_bytes = render_guide_pdf_from_blocks(
            blocks, material.title, material.grade_band, lang=language
        )
    else:
        content_md = material.content_es if language == "es" else material.content
        if not (content_md or "").strip():
            raise ValueError(f"Guide has no {language} content to render")
        html = build_guide_html(content_md, material.title, material.grade_band, lang=language)
        pdf_bytes = render_html_to_pdf(html)

    storage = ImageStorageService()
    try:
        band_slug = (material.grade_band or "none").replace("-", "_")
        key = f"materials/guide/{material.research_id}/{band_slug}_{language}.pdf"
        material.file_url = storage.upload_image(pdf_bytes, key, "application/pdf")
    finally:
        storage.close()

    material.updated_at = datetime.now(UTC)
    logger.info(
        "Rendered %s guide PDF for material id=%s band=%s (%d bytes)",
        language,
        material.id,
        material.grade_band,
        len(pdf_bytes),
    )
    return material
