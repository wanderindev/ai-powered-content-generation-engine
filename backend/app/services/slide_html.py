"""Build a self-contained 1280x720 HTML page for one slide (#321, render half A).

Maps an ``EduSlide``'s structured data onto the brand slide-kit templates and
inlines the brand CSS + logos, producing standalone HTML that the headless
renderer (#321 half B) screenshots into a PNG. No browser here — pure string
assembly, fully unit-testable.

Brand assets (CSS tokens, slides.css, logos, heroicons) are bundled under
``app/slide_assets/`` so the backend image is self-contained (the design skill
under ``.claude/`` is not in the image).
"""

import base64
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, select_autoescape

from app.models.enums import SlideTemplate

_ASSETS = Path(__file__).resolve().parent.parent / "slide_assets"
# styles.css is import-only; flatten the four token files (its @imports) + slides.css.
_CSS_FILES = (
    "tokens/colors.css",
    "tokens/typography.css",
    "tokens/spacing.css",
    "tokens/effects.css",
    "slides.css",
)

# LLM-authored text is escaped; the assembled page (trusted CSS/JS) is not.
_env = Environment(autoescape=select_autoescape(default=True))


@lru_cache(maxsize=1)
def _bundled_css() -> str:
    return "\n".join((_ASSETS / name).read_text() for name in _CSS_FILES)


@lru_cache(maxsize=1)
def _heroicons_js() -> str:
    return (_ASSETS / "heroicons.js").read_text()


def heroicons_script() -> str:
    """The bundled heroicon-hydration JS (also inlined into ``build_slide_html``).

    Chromium does not execute the inline ``<head>`` ``<script>`` when a page is
    populated via Playwright's ``set_content``, so the headless renderer
    re-injects this with ``add_script_tag`` (which runs reliably and self-hydrates
    after ``load``) to fill the ``data-hicon`` markers. Direct browser opens of
    the HTML still use the inline copy.
    """
    return _heroicons_js()


@lru_cache(maxsize=1)
def _autofit_js() -> str:
    return (_ASSETS / "autofit.js").read_text()


def autofit_script() -> str:
    """The bundled shrink-to-fit JS (also inlined into ``build_slide_html``).

    Same ``set_content`` caveat as :func:`heroicons_script` — the headless
    renderer re-injects this via ``add_script_tag`` (after the heroicons, whose
    icons affect layout) and then waits for ``window.__autofit_done`` before
    capturing. It steps the ``--fit`` CSS var down until the slide's content
    boxes fit the fixed 1280x720 canvas.
    """
    return _autofit_js()


@lru_cache(maxsize=8)
def _logo_data_uri(name: str) -> str:
    b64 = base64.b64encode((_ASSETS / name).read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _timeline_steps(lines: list[str]) -> list[dict]:
    """Parse 'year — label' lines into alternating above/below timeline steps."""
    steps = []
    for i, line in enumerate(lines):
        year, sep, label = line.partition("—")
        if not sep:
            year, sep, label = line.partition(" - ")
        if not sep:
            parts = line.split(None, 1)
            year, label = parts[0], (parts[1] if len(parts) > 1 else "")
        steps.append({"year": year.strip(), "label": label.strip(), "above": i % 2 == 0})
    return steps


# --- Per-template markup (the `.slide` element), mirroring ui_kits/slides/ ----

_T = {
    SlideTemplate.TITLE.value: """
<div class="slide t-title">
  {%- if image_url %}
  <div class="photo-layer"><img src="{{ image_url }}" alt=""></div>
  <div class="s-scrim"></div>
  {%- endif %}
  <div class="content">
    <div class="kicker"><img src="{{ logo_512 }}" alt="Panama In Context"><span style="color:#fff;font-weight:600;letter-spacing:.05em;font-size:18px">PANAMA IN CONTEXT</span></div>
    <p class="s-eyebrow on-dark" style="margin-top:22px">{{ eyebrow }}</p>
    <h1>{{ title }}</h1>
    {%- if subtitle %}<p class="sub">{{ subtitle }}</p>{%- endif %}
    <div class="byline"><span>Diego Raúl Feliu</span><span class="dot"></span><span>Panama In Context</span></div>
  </div>
</div>""",
    SlideTemplate.SECTION.value: """
<div class="slide t-section">
  <img class="s-logo tr" src="{{ logo_512 }}" alt="" style="height:40px">
  {%- if image_url %}<div class="photo-layer"><img src="{{ image_url }}" alt=""></div>{%- endif %}
  <div class="content">
    <div class="num">{{ "%02d"|format(section_no) if section_no else "01" }}</div>
    <h2>{{ title }}</h2>
    {%- if subtitle %}<p class="sub">{{ subtitle }}</p>{%- endif %}
  </div>
</div>""",
    SlideTemplate.BULLETS.value: """
<div class="slide t-bullets">
  <div class="slide-pad">
    <img class="s-logo tr" src="{{ logo_512 }}" alt="" style="height:40px">
    <div class="head"><p class="s-eyebrow">{{ eyebrow }}</p><h2 class="s-title">{{ title }}</h2><div class="s-rule"></div></div>
    <ul>
      {%- for b in bullets %}
      <li><span class="mk"><i data-hicon="check-circle-solid" style="font-size:30px"></i></span><div>{{ b }}</div></li>
      {%- endfor %}
    </ul>
  </div>
</div>""",
    SlideTemplate.TWO_COL_IMAGE_LEFT.value: """
<div class="slide t-split">
  <img class="s-logo tr" src="{{ logo_512 }}" alt="" style="height:40px">
  <div class="media">{% if image_url %}<img class="s-photo" src="{{ image_url }}" alt="">{% endif %}</div>
  <div class="copy">
    <p class="s-eyebrow">{{ eyebrow }}</p>
    <h2 class="s-title">{{ title }}</h2>
    {%- if lead %}<p class="s-body">{{ lead }}</p>{%- endif %}
    {%- if rest_bullets %}<ul>{%- for b in rest_bullets %}<li><span class="mk"><i data-hicon="map-pin" style="font-size:26px"></i></span>{{ b }}</li>{%- endfor %}</ul>{%- endif %}
  </div>
</div>""",
    SlideTemplate.TWO_COL_IMAGE_RIGHT.value: """
<div class="slide t-split img-right">
  <img class="s-logo tr" src="{{ logo_512 }}" alt="" style="height:40px">
  <div class="copy">
    <p class="s-eyebrow">{{ eyebrow }}</p>
    <h2 class="s-title">{{ title }}</h2>
    {%- if lead %}<p class="s-body">{{ lead }}</p>{%- endif %}
    {%- if rest_bullets %}<ul>{%- for b in rest_bullets %}<li><span class="mk"><i data-hicon="map-pin" style="font-size:26px"></i></span>{{ b }}</li>{%- endfor %}</ul>{%- endif %}
  </div>
  <div class="media">{% if image_url %}<img class="s-photo" src="{{ image_url }}" alt="">{% endif %}</div>
</div>""",
    SlideTemplate.FULL_IMAGE.value: """
<div class="slide t-image">
  <div class="media">{% if image_url %}<img class="s-photo" src="{{ image_url }}" alt="">{% endif %}</div>
  <div class="s-scrim"></div>
  <img class="s-logo tr" src="{{ logo_512 }}" alt="" style="height:40px">
  <div class="caption"><span class="chip">{{ eyebrow }}</span><h3>{{ title }}</h3>{% if caption_text %}<p>{{ caption_text }}</p>{% endif %}</div>
</div>""",
    SlideTemplate.TIMELINE.value: """
<div class="slide t-timeline">
  <div class="slide-pad">
    <img class="s-logo tr" src="{{ logo_512 }}" alt="" style="height:40px">
    <p class="s-eyebrow">{{ eyebrow }}</p>
    <h2 class="s-title">{{ title }}</h2>
    <div class="track">
      <div class="line" style="background:linear-gradient(to right, var(--brand-300), var(--color-brand))"></div>
      <div class="steps">
        {%- for s in steps %}
        <div class="step">{% if s.above %}<div class="above"><span class="yr">{{ s.year }}</span><span class="lbl">{{ s.label }}</span></div><div class="node"></div>{% else %}<div class="node"></div><div class="below"><span class="yr">{{ s.year }}</span><span class="lbl">{{ s.label }}</span></div>{% endif %}</div>
        {%- endfor %}
      </div>
    </div>
  </div>
</div>""",
    SlideTemplate.MAP.value: """
<div class="slide t-map">
  <img class="s-logo tr" src="{{ logo_512 }}" alt="" style="height:40px">
  <div class="copy">
    <p class="s-eyebrow">{{ eyebrow }}</p>
    <h2 class="s-title">{{ title }}</h2>
    <ol>{%- for loc in lines %}<li><span class="n">{{ loop.index }}</span>{{ loc }}</li>{%- endfor %}</ol>
  </div>
  <div class="mapwrap">
    <div class="mapfield">{% if image_url %}<img class="s-photo" src="{{ image_url }}" alt="" style="width:100%;height:100%;object-fit:cover">{% else %}<span class="hint">Map of the site</span>{% endif %}</div>
  </div>
</div>""",
    SlideTemplate.CLOSING.value: """
<div class="slide t-closing">
  {%- if image_url %}<div class="photo-layer"><img src="{{ image_url }}" alt=""></div>{%- endif %}
  <div class="content">
    <img class="logo" src="{{ logo_512 }}" alt="Panama In Context">
    <h2>{{ title or "Gracias" }}</h2>
    {%- if subtitle %}<p class="sub">{{ subtitle }}</p>{%- endif %}
    <div class="contact">
      <span><span class="ic"><i data-hicon="globe-americas" style="font-size:22px"></i></span>panamaincontext.com</span>
      <span><span class="ic"><i data-hicon="envelope" style="font-size:22px"></i></span>info@panamaincontext.com</span>
    </div>
  </div>
</div>""",
}


def _page(slide_html: str, lang: str) -> str:
    """Wrap the rendered slide in a self-contained 1280x720 page (no scaler).

    The bundled ``.standalone`` rule flex-centres a scaled slide for the
    in-browser deck; headless capture wants the opposite — neutralise that
    centring so ``.slide`` sits block-level at the top-left and screenshots at
    exactly 1280x720 (a flex row would otherwise shrink its width).
    """
    overrides = (
        "html,body{margin:0;padding:0;background:#fff}"
        "body.standalone{display:block;min-height:0;background:#fff}"
        ".slide{width:1280px;height:720px;position:relative;overflow:hidden;border-radius:0;box-shadow:none}"
        # The corner logo is the templates' first child, so later positioned
        # siblings (e.g. the map panel) would paint over it — lift it above.
        ".s-logo{z-index:5}"
        # Fit layer: every overflow-prone font-size/gap re-expressed through one
        # --fit var (default 1) so autofit.js can shrink content that would clip
        # on the fixed canvas, while slides.css stays byte-identical to the kit.
        ".slide{--fit:1}"
        ".s-title{font-size:calc(var(--s-title)*var(--fit))}"
        ".s-body{font-size:calc(var(--s-body)*var(--fit))}"
        ".t-bullets li{font-size:calc(var(--s-bullet)*var(--fit))}"
        ".t-bullets ul{gap:calc(24px*var(--fit))}"
        ".t-split .copy li{font-size:calc(22px*var(--fit))}"
        ".t-split .copy ul{gap:calc(16px*var(--fit))}"
        ".t-map .copy li{font-size:calc(22px*var(--fit))}"
        ".t-map .copy ol{gap:calc(18px*var(--fit))}"
        ".t-title h1{font-size:calc(var(--s-display)*var(--fit))}"
        ".t-section h2{font-size:calc(52px*var(--fit))}"
        ".t-timeline .yr{font-size:calc(30px*var(--fit))}"
        ".t-timeline .lbl{font-size:calc(17px*var(--fit))}"
        ".t-image .caption h3{font-size:calc(38px*var(--fit))}"
        ".t-closing h2{font-size:calc(56px*var(--fit))}"
    )
    return (
        f'<!DOCTYPE html>\n<html lang="{lang}">\n<head>\n<meta charset="UTF-8">\n'
        f"<style>{_bundled_css()}</style>\n<style>{overrides}</style>\n"
        f"<script>{_heroicons_js()}</script>\n<script>{_autofit_js()}</script>\n</head>\n"
        f'<body class="standalone">\n{slide_html}\n</body>\n</html>'
    )


_DEFAULT_EYEBROW = {"es": "Excursión académica", "en": "Academic Excursion"}


def build_slide_html(
    slide: dict,
    lang: str = "es",
    eyebrow: str | None = None,
    section_no: int | None = None,
) -> str:
    """Render one slide's structured data into a standalone 1280x720 HTML page.

    ``slide`` is an EduSlide-shaped dict (``template`` / ``title`` / ``body`` /
    ``image_url``). ``body`` is the newline-separated on-screen text; how it maps
    onto a template (bullets vs. lead+bullets vs. timeline steps) depends on the
    template. Unknown templates fall back to ``bullets``. ``eyebrow`` is the
    kit's kicker line (and the full-image chip label) — callers pass deck-level
    context (e.g. "Excursión académica · 3rd-6th"); defaults to the bare
    localized label. ``section_no`` drives the section divider's big numeral
    (1-based count of section slides so far; the kit shows no page numbers).
    """
    template = slide.get("template") or SlideTemplate.BULLETS.value
    if template not in _T:
        template = SlideTemplate.BULLETS.value

    lines = [ln.strip() for ln in (slide.get("body") or "").split("\n") if ln.strip()]
    ctx = {
        "title": slide.get("title") or "",
        "image_url": slide.get("image_url"),
        "lines": lines,
        "bullets": lines,
        "subtitle": lines[0] if lines else "",
        "lead": lines[0] if lines else "",
        "rest_bullets": lines[1:],
        "caption_text": lines[0] if lines else "",
        "steps": _timeline_steps(lines),
        "section_no": section_no,
        "eyebrow": eyebrow or _DEFAULT_EYEBROW.get(lang, _DEFAULT_EYEBROW["es"]),
        "logo_512": _logo_data_uri("logo_512x512.png"),
    }
    slide_html = _env.from_string(_T[template]).render(**ctx)
    return _page(slide_html, lang)
