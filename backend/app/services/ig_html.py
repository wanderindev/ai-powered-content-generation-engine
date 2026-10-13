"""Build a self-contained 1080x1350 HTML page for one Instagram post.

Maps an ``InstagramPost``'s structured data onto the brand social-kit templates
(``ui_kits/social`` in the design system) and inlines the brand CSS, the Caveat
handwriting font and all images, producing standalone HTML that the headless
renderer (:mod:`app.services.ig_render`) screenshots into a JPEG. No browser
here — pure string assembly, fully unit-testable.

Brand assets are bundled under ``app/ig_assets/`` (social.css + the design
system's token files, which carry the ``--accent-*`` orange family the social
kit uses; the Caveat latin woff2; Diego's avatar). The corner logo is reused
from ``app/slide_assets/``. Nothing is fetched at render time — the Google
Fonts link in the design-kit files is replaced by a data-URI ``@font-face`` so
rendering is deterministic and offline.
"""

import base64
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, select_autoescape
from markupsafe import Markup, escape

from app.models.enums import InstagramPostType, InstagramTemplateVariant

_ASSETS = Path(__file__).resolve().parent.parent / "ig_assets"
_SLIDE_ASSETS = Path(__file__).resolve().parent.parent / "slide_assets"
# styles.css is import-only in the kit; flatten the token files + social.css.
_CSS_FILES = (
    "tokens/colors.css",
    "tokens/typography.css",
    "tokens/spacing.css",
    "tokens/effects.css",
    "social.css",
)

# Post copy is escaped; the assembled page (trusted CSS/JS) is not. The fact
# body's <b> spans survive because sanitize_fact returns pre-escaped markup
# that build_ig_html wraps in Markup().
_env = Environment(autoescape=select_autoescape(default=True))

POST_WIDTH = 1080
POST_HEIGHT = 1350


def sanitize_fact(raw: str | None) -> str | None:
    """Reduce a fact body to plain text plus ``<b>…</b>`` emphasis spans.

    The LLM (and the admin editor) author emphasis as literal ``<b>`` tags.
    Entities are unescaped first (so re-sanitizing stored output is a no-op),
    everything is HTML-escaped, then only the escaped ``<b>``/``</b>`` markers
    are restored — so no other markup (links, scripts, attributes) can reach
    the rendered page. Idempotent; safe to call on every write.
    """
    if raw is None:
        return None
    text = str(escape(Markup(raw.strip()).unescape()))
    text = text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    return text


@lru_cache(maxsize=1)
def _bundled_css() -> str:
    return "\n".join((_ASSETS / name).read_text() for name in _CSS_FILES)


@lru_cache(maxsize=1)
def _caveat_font_face() -> str:
    """Data-URI ``@font-face`` for Caveat (variable weight, latin subset)."""
    b64 = base64.b64encode((_ASSETS / "fonts" / "caveat-latin.woff2").read_bytes()).decode("ascii")
    return (
        "@font-face{font-family:'Caveat';font-style:normal;font-weight:400 700;"
        f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
        ":root{--font-hand:'Caveat',cursive}"
    )


@lru_cache(maxsize=4)
def _data_uri(path: Path, mime: str) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _logo_uri() -> str:
    return _data_uri(_SLIDE_ASSETS / "logo_512x512.png", "image/png")


def _avatar_uri() -> str:
    return _data_uri(_ASSETS / "foto-diego-feliu.avif", "image/avif")


# Shrink-to-fit pass for copy that can overflow the fixed canvas: the fact card
# body (an absolutely positioned column that must stay clear of the polaroid)
# and the announcement headline. Chromium doesn't run inline <head> scripts on
# Playwright's set_content, so the renderer re-injects this via add_script_tag
# and waits for the done flag (same mechanism as the slide renderer).
_IGFIT_JS = """
(function () {
  function step(el, min) {
    if (!el) return;
    var size = parseFloat(getComputedStyle(el).fontSize);
    var guard = 40;
    while (guard-- > 0 && size > min && overflows(el)) {
      size -= 1;
      el.style.fontSize = size + "px";
    }
  }
  function overflows(el) {
    var max = parseFloat(el.dataset.fitMax || "0");
    return max > 0 && el.scrollHeight > max;
  }
  function run() {
    document.querySelectorAll("[data-fit-max]").forEach(function (el) {
      step(el, parseFloat(el.dataset.fitMin || "16"));
    });
    window.__igfit_done = true;
  }
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(run);
  } else {
    run();
  }
})();
"""


def igfit_script() -> str:
    """The shrink-to-fit JS (also inlined into the page for direct opens)."""
    return _IGFIT_JS


# --- Per-template markup (the `.post` element), mirroring ui_kits/social/ ----
# Fixed motifs (heading, hand-drawn connector, lockup, chip, CTA) are kept
# verbatim from the kit; only the authored copy and images are variables.

_FACT_AZUL = """
<div class="post fact" style="background:radial-gradient(120% 90% at 20% 0%, #1c5d7e 0%, #124a68 45%, #0d3a52 100%)">
  <svg class="connector" width="150" height="760" viewBox="0 0 150 760" style="left:44px;top:330px" fill="none">
    <path d="M60 8 C60 90 18 150 18 230 C18 320 96 360 96 450 C96 540 30 590 30 690 L30 752" stroke="rgba(255,255,255,0.45)" stroke-width="3" stroke-linecap="round"/>
    <circle cx="60" cy="8" r="7" fill="#fff" opacity="0.8"/>
    <circle cx="30" cy="752" r="7" fill="var(--accent-500)"/>
  </svg>
  <div style="position:absolute;top:96px;left:96px"><div class="pic-lockup" style="--wm:#fff">
    <img src="{{ logo }}" alt="">
    <div class="wm"><span class="b1">PANAMA</span><span class="b2">IN CONTEXT</span><span class="b3">by Diego</span></div>
  </div></div>
  <h2 class="sabias" style="position:absolute;top:300px;left:150px;font-size:100px"><span class="q">¿Sabías que</span></h2>
  <div class="body" data-fit-max="{{ 700 if polaroid_src else 800 }}" data-fit-min="22" style="position:absolute;top:440px;left:150px;width:540px;color:#eaf4fa">{{ fact_html }}</div>
  {%- if polaroid_src %}
  <div class="polaroid" style="position:absolute;right:88px;bottom:104px;width:430px;transform:rotate(-5deg)">
    <img src="{{ polaroid_src }}" alt="" style="height:360px">
    <div class="cap">{{ polaroid_caption }}</div>
  </div>
  {%- endif %}
</div>"""

_FACT_CLARO = """
<div class="post fact" style="background:radial-gradient(130% 120% at 82% 0%, #f1eee9 0%, #e6e2da 100%)">
  <svg class="connector" width="160" height="800" viewBox="0 0 160 800" style="left:40px;top:300px" fill="none">
    <path d="M70 8 C70 96 20 150 20 240 C20 336 104 380 104 476 C104 566 34 612 34 720 L34 792" stroke="var(--accent-500)" stroke-width="3" stroke-linecap="round"/>
    <circle cx="70" cy="8" r="7" fill="var(--brand-600)"/>
    <circle cx="34" cy="792" r="7" fill="var(--accent-500)"/>
  </svg>
  <div style="position:absolute;top:96px;left:96px"><div class="pic-lockup" style="--wm:#0f3a52">
    <img src="{{ logo }}" alt="">
    <div class="wm"><span class="b1">PANAMA</span><span class="b2">IN CONTEXT</span><span class="b3">by Diego</span></div>
  </div></div>
  <h2 class="sabias" style="position:absolute;top:290px;left:158px;font-size:100px"><span class="q">Sabías que</span></h2>
  <div class="body" data-fit-max="{{ 690 if polaroid_src else 790 }}" data-fit-min="22" style="position:absolute;top:430px;left:158px;width:540px;color:#33404a">{{ fact_html }}</div>
  {%- if polaroid_src %}
  <div class="polaroid" style="position:absolute;right:96px;bottom:110px;width:430px;transform:rotate(4deg)">
    <img src="{{ polaroid_src }}" alt="" style="height:360px;filter:sepia(0.5) contrast(0.95)">
    <div class="cap">{{ polaroid_caption }}</div>
  </div>
  {%- endif %}
</div>"""

_ANNOUNCE = """
<div class="post">
  <div class="announce">
    {%- if bg_src %}
    <img class="bg" src="{{ bg_src }}" alt="">
    {%- else %}
    <div class="bg" style="background:radial-gradient(120% 90% at 20% 0%, #1c5d7e 0%, #124a68 45%, #0d3a52 100%)"></div>
    {%- endif %}
    <div class="scrim"></div>
    <div class="inner">
      <div class="top"><div class="pic-lockup" style="--wm:#fff">
        <img src="{{ logo }}" alt="">
        <div class="wm"><span class="b1">PANAMA</span><span class="b2">IN CONTEXT</span><span class="b3">by Diego</span></div>
      </div></div>
      <div class="mid">
        <span class="chip"><span class="dot"></span>Nuevo en el blog</span>
        {%- if kicker %}<p class="kicker" style="margin-top:26px">{{ kicker }}</p>{%- endif %}
        <h1 data-fit-max="360" data-fit-min="40">{{ title }}</h1>
        <div class="meta">
          <img class="av" src="{{ avatar }}" alt="">
          <span>{{ meta_text }}</span>
        </div>
        <span class="cta">Léelo ahora — enlace en la bio&nbsp;&rarr;</span>
      </div>
    </div>
  </div>
</div>"""


def _page(post_html: str) -> str:
    """Wrap the rendered post in a self-contained 1080x1350 page (no scaler).

    The kit previews posts inside a zoomed ``.scaler``; headless capture wants
    the ``.post`` element block-level at the top-left, exactly 1080x1350.
    """
    overrides = (
        "html,body{margin:0;padding:0;background:#fff}"
        f".post{{width:{POST_WIDTH}px;height:{POST_HEIGHT}px}}"
    )
    return (
        '<!DOCTYPE html>\n<html lang="es">\n<head>\n<meta charset="UTF-8">\n'
        f"<style>{_caveat_font_face()}</style>\n"
        f"<style>{_bundled_css()}</style>\n<style>{overrides}</style>\n"
        f"<script>{_IGFIT_JS}</script>\n</head>\n"
        f"<body>\n{post_html}\n</body>\n</html>"
    )


def build_ig_html(post) -> str:
    """Render one ``InstagramPost`` into a standalone 1080x1350 HTML page.

    Sabías-que posts pick their card treatment from ``template_variant``
    (default AZUL); promos always use the announcement template. ``fact`` is
    re-sanitized here as a defensive backstop — only ``<b>`` spans survive.
    """
    logo = _logo_uri()
    if post.post_type == InstagramPostType.ARTICLE_PROMO.value:
        html = _env.from_string(_ANNOUNCE).render(
            logo=logo,
            avatar=_avatar_uri(),
            bg_src=post.media_path or None,
            kicker=post.kicker or "",
            title=post.title or "",
            meta_text=post.meta_text or "por Diego Raúl Feliu",
        )
    else:
        variant = post.template_variant or InstagramTemplateVariant.AZUL.value
        template = _FACT_CLARO if variant == InstagramTemplateVariant.CLARO.value else _FACT_AZUL
        html = _env.from_string(template).render(
            logo=logo,
            fact_html=Markup(sanitize_fact(post.fact) or ""),
            polaroid_src=post.media_path or None,
            polaroid_caption=post.polaroid_caption or "",
        )
    return _page(html)
