"""Unit tests for the slide-HTML builder (app.services.slide_html, #321 half A).

Pure string assembly — no headless browser.
"""

import pytest
from app.models.enums import SlideTemplate
from app.services.slide_html import _timeline_steps, build_slide_html


def _slide(**kw):
    base = {"template": "bullets", "title": "Titulo", "body": "Uno\nDos\nTres", "image_url": None}
    base.update(kw)
    return base


def test_page_is_1280x720_and_self_contained():
    html = build_slide_html(_slide())
    assert "<!DOCTYPE html>" in html
    assert "width:1280px;height:720px" in html  # render override, fixed frame
    assert "--color-brand" in html  # brand CSS inlined
    assert "data-hicon" in html  # heroicon markers present


@pytest.mark.parametrize("template", [t.value for t in SlideTemplate])
def test_every_template_renders_with_class_and_title(template):
    html = build_slide_html(_slide(template=template, title="MiTitulo"))
    assert "slide t-" in html
    assert "MiTitulo" in html


def test_unknown_template_falls_back_to_bullets():
    html = build_slide_html(_slide(template="hologram"))
    assert "t-bullets" in html


def test_bullets_render_each_body_line():
    html = build_slide_html(_slide(template="bullets", body="Alpha\nBeta\nGamma"))
    assert "Alpha" in html and "Beta" in html and "Gamma" in html


def test_two_col_uses_lead_then_bullets():
    html = build_slide_html(
        _slide(template="two-col-image-left", body="Lead para\nPoint A\nPoint B")
    )
    assert "t-split" in html
    assert "Lead para" in html and "Point A" in html and "Point B" in html


def test_image_url_embedded_when_present():
    html = build_slide_html(_slide(template="full-image", image_url="https://cdn.example/x.jpg"))
    assert "https://cdn.example/x.jpg" in html


def test_llm_text_is_html_escaped():
    html = build_slide_html(_slide(title="<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_timeline_steps_parse_year_and_label():
    steps = _timeline_steps(["1519 — Fundada", "1671 - Destruida", "1914 Canal"])
    assert steps[0] == {"year": "1519", "label": "Fundada", "above": True}
    assert steps[1]["year"] == "1671" and steps[1]["label"] == "Destruida"
    assert steps[1]["above"] is False
    assert steps[2]["year"] == "1914" and steps[2]["label"] == "Canal"


def test_timeline_template_includes_years():
    html = build_slide_html(
        _slide(template="timeline", title="Linea", body="1519 — Fundada\n1671 — Caida")
    )
    assert "1519" in html and "Fundada" in html and "Caida" in html


# --- kit-fidelity re-skin (study-guide redesign, D9) ---


def test_content_templates_carry_the_eyebrow_kicker():
    for template in ("bullets", "two-col-image-left", "two-col-image-right", "timeline", "map"):
        html = build_slide_html(_slide(template=template), eyebrow="Excursión académica · 3rd-6th")
        assert 's-eyebrow' in html, template
        assert "Excursión académica · 3rd-6th" in html, template


def test_title_slide_eyebrow_is_on_dark():
    html = build_slide_html(_slide(template="title"), eyebrow="Excursión académica")
    assert 's-eyebrow on-dark' in html


def test_eyebrow_defaults_localized():
    assert "Excursión académica" in build_slide_html(_slide(template="bullets"))
    assert "Academic Excursion" in build_slide_html(_slide(template="bullets"), lang="en")


def test_full_image_caption_has_chip():
    html = build_slide_html(
        _slide(template="full-image", body="Una caption"), eyebrow="Excursión académica"
    )
    assert '<span class="chip">Excursión académica</span>' in html


def test_corner_logo_present_on_section_two_col_and_map():
    for template in ("section", "two-col-image-left", "two-col-image-right", "map", "bullets", "timeline"):
        html = build_slide_html(_slide(template=template))
        assert 'class="s-logo tr"' in html, template


# --- kit consistency polish (slides redesign: 40px logos, no pageno/foot chrome) ---


def test_corner_logo_is_uniform_40px_on_every_content_template():
    for template in (
        "section",
        "bullets",
        "two-col-image-left",
        "two-col-image-right",
        "full-image",
        "timeline",
        "map",
    ):
        html = build_slide_html(_slide(template=template))
        assert 'class="s-logo tr" src="data:image/png;base64,' in html, template
        assert 'style="height:40px"' in html, template


def test_no_page_number_or_foot_chrome():
    # The CSS rules still exist in the inlined stylesheet; the markup must not.
    for template in ("bullets", "two-col-image-left", "two-col-image-right", "timeline"):
        html = build_slide_html(_slide(template=template))
        assert 'class="s-pageno"' not in html, template
    assert 'class="s-foot"' not in build_slide_html(_slide(template="map"))


def test_closing_contact_email_is_info():
    html = build_slide_html(_slide(template="closing"))
    assert "info@panamaincontext.com" in html
    assert "hello@panamaincontext.com" not in html


def test_section_numeral_follows_section_no():
    assert ">03<" in build_slide_html(_slide(template="section"), section_no=3)
    assert ">01<" in build_slide_html(_slide(template="section"))


def test_page_carries_fit_layer_and_autofit_script():
    html = build_slide_html(_slide())
    assert ".slide{--fit:1}" in html
    assert "calc(var(--s-title)*var(--fit))" in html
    assert "__autofit_done" in html
