"""Tests for branded study-guide PDF export (app.services.guide_pdf, #324).

The kit-faithful renderer splits into pure pieces (fragment builders, the
greedy packer, page assembly) tested without a browser, an orchestration test
with the chromium render mocked, and a real-chromium end-to-end render gated
behind browser availability.
"""

import functools
from types import SimpleNamespace
from unittest.mock import patch

import fitz
import pytest
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_suggestion import EduSuggestion
from app.services.guide_pdf import (
    USABLE_H,
    _block_to_frag,
    _closing_page_html,
    _cover_page_html,
    _group_sections,
    _lead_html,
    _measure_snippets,
    _page_units_html,
    _section_head_html,
    _Unit,
    _units_for_frags,
    build_guide_html,
    generate_guide_pdf,
    pack_units,
)


@functools.lru_cache(maxsize=1)
def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            browser.close()
        return True
    except Exception:
        return False


requires_chromium = pytest.mark.skipif(
    not _chromium_available(),
    reason="headless chromium not installed — run under scripts/run-tests.sh --build",
)

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


class _FakeStorage:
    def __init__(self):
        self.uploads = []

    def upload_image(self, data, key, content_type="image/jpeg"):
        self.uploads.append((key, content_type, data))
        return f"https://cdn.example/{key}"

    def close(self):
        pass


def _minimal_pdf() -> bytes:
    """A real 1-page PDF to stand in for the chromium render output."""
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    data = doc.tobytes()
    doc.close()
    return data


def _guide_material(db, content_es="# Hola\n\nContenido.", content=None, mtype="guide"):
    s = EduSuggestion(title="Panamá Viejo")
    db.add(s)
    db.flush()
    r = EduResearch(suggestion_id=s.id, content="x")
    db.add(r)
    db.flush()
    m = EduMaterial(
        research_id=r.id,
        material_type=mtype,
        grade_band="3rd-6th",
        title="Panamá Viejo: la primera ciudad",
        content=content,
        content_es=content_es,
        status="APPROVED",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


# --- fragment builders (pure — kit vocabulary) --------------------------------


class TestBlockFragments:
    def test_paragraph_uses_g_body(self):
        f = _block_to_frag({"type": "paragraph", "text": "Texto."}, "es", 0)
        assert f.html == '<p class="g-body">Texto.</p>'

    def test_bullets_are_brand_marked_and_splittable(self):
        f = _block_to_frag({"type": "bullets", "items": ["uno", "dos"]}, "es", 0)
        assert f.wrap_open == '<ul class="g-bullets">'
        assert len(f.items) == 2
        assert 'data-hicon="check-circle-solid"' in f.items[0]
        assert f.item_gap == 16

    def test_bullet_lead_is_bolded(self):
        assert _lead_html("Strategic wealth — the city was rich.") == (
            "<b>Strategic wealth</b> — the city was rich."
        )
        # No em-dash → plain escaped text.
        assert _lead_html("Just a sentence.") == "Just a sentence."

    def test_ordered_list_uses_number_badges(self):
        f = _block_to_frag({"type": "ordered_list", "items": ["a", "b", "c"]}, "es", 0)
        assert f.wrap_open == '<ol class="g-numbers">'
        assert '<span class="n">3</span>' in f.items[2]

    def test_quote_uses_g_quote_with_cite(self):
        f = _block_to_frag({"type": "quote", "text": "Cita", "attribution": "Cronista"}, "es", 0)
        assert 'class="g-quote"' in f.html
        assert "<cite>— Cronista</cite>" in f.html

    def test_timeline_uses_kit_track_events(self):
        f = _block_to_frag(
            {"type": "timeline", "events": [{"label": "1519", "text": "Fundación"}]}, "es", 0
        )
        assert f.wrap_open.startswith('<div class="p-vtimeline">')
        assert '<div class="yr">1519</div>' in f.items[0]
        assert '<span class="node">' in f.items[0]

    def test_terms_callout_is_splittable_with_dl_and_book_icon(self):
        f = _block_to_frag(
            {
                "type": "callout",
                "label": "Términos clave",
                "terms": [{"term": "istmo", "definition": "puente de tierra"}],
            },
            "es",
            0,
        )
        # Splittable at term boundaries — a long vocabulary list must flow
        # onto continuation pages instead of clipping at the page bottom.
        assert f.html is None
        assert f.wrap_open.startswith('<div class="g-callout">')
        assert 'data-hicon="book-open"' in f.wrap_open
        assert f.items == ["<dt>istmo</dt><dd>puente de tierra</dd>"]
        assert f.item_gap == 12
        assert f.wrap_close == "</dl></div>"

    def test_text_callout_renders_sparkles_aside(self):
        f = _block_to_frag({"type": "callout", "text": "Un dato curioso."}, "es", 0)
        assert 'data-hicon="sparkles-solid"' in f.html
        assert "Un dato curioso." in f.html
        assert "¿Sabías que…?" in f.html  # lang-aware default label

    def test_mc_question_numbered_with_lettered_choices(self):
        f = _block_to_frag(
            {"type": "mc_question", "question": "¿Cuándo?", "choices": ["1519", "1671"]}, "es", 3
        )
        assert 'class="g-mcq"' in f.html
        assert '<span class="qn">3.</span>' in f.html
        assert '<ol class="choices"><li>1519</li><li>1671</li></ol>' in f.html

    def test_image_with_media_path_renders_ring_figure(self):
        f = _block_to_frag(
            {
                "type": "image",
                "image_query": "tower",
                "caption": "La torre",
                "media_path": "https://cdn/x.jpg",
            },
            "es",
            0,
        )
        assert '<img class="g-photo" src="https://cdn/x.jpg"' in f.html
        assert '<span class="ring"></span>' in f.html
        assert '<p class="g-caption">La torre</p>' in f.html
        # Landscape (3:2) is the default frame shape at full content width.
        assert "aspect-ratio:3 / 2" in f.html
        assert "width:62%" not in f.html

    def test_image_aspect_wide_and_portrait_frames(self):
        base = {"type": "image", "image_query": "tower", "media_path": "https://cdn/x.jpg"}
        wide = _block_to_frag({**base, "aspect": "wide"}, "es", 0)
        assert "aspect-ratio:21 / 9" in wide.html
        # Portrait narrows and centers the figure so it fits the page budget.
        portrait = _block_to_frag({**base, "aspect": "portrait"}, "es", 0)
        assert "aspect-ratio:3 / 4" in portrait.html
        assert 'style="width:62%;margin:0 auto"' in portrait.html
        # An unknown aspect falls back to the landscape default.
        junk = _block_to_frag({**base, "aspect": "circle"}, "es", 0)
        assert "aspect-ratio:3 / 2" in junk.html

    def test_image_without_media_renders_editor_placeholder(self):
        f = _block_to_frag({"type": "image", "image_query": "colonial tower"}, "es", 0)
        assert 'class="g-missing"' in f.html
        assert "Foto por seleccionar" in f.html
        assert "colonial tower" in f.html

    def test_map_locations_use_map_pins(self):
        f = _block_to_frag(
            {"type": "map", "image_query": "map", "locations": ["Catedral"]}, "es", 0
        )
        assert 'data-hicon="map-pin"' in f.html
        assert "Catedral" in f.html

    def test_text_is_escaped(self):
        f = _block_to_frag({"type": "paragraph", "text": "<script>x</script>"}, "es", 0)
        assert "<script>" not in f.html
        assert "&lt;script&gt;" in f.html


class TestSectionGrouping:
    def test_groups_blocks_under_their_sections(self):
        body = [
            {"type": "section", "title": "A"},
            {"type": "paragraph", "text": "p1"},
            {"type": "section", "title": "B"},
            {"type": "paragraph", "text": "p2"},
            {"type": "paragraph", "text": "p3"},
        ]
        groups = _group_sections(body)
        assert [t for t, _ in groups] == ["A", "B"]
        assert [len(bs) for _, bs in groups] == [1, 2]

    def test_leading_blocks_before_first_section_get_untitled_group(self):
        body = [{"type": "paragraph", "text": "intro"}, {"type": "section", "title": "A"}]
        groups = _group_sections(body)
        assert groups[0][0] is None and len(groups[0][1]) == 1
        assert groups[1][0] == "A"

    def test_empty_body(self):
        assert _group_sections([]) == []


class TestFixedPages:
    def test_cover_without_photo_uses_solid_band(self):
        html = _cover_page_html(
            {"type": "cover", "title": "Panamá Viejo", "subtitle": "Sub"}, "F", "3rd-6th", "es"
        )
        assert "hero-solid" in html
        assert "Guía de estudio" in html
        assert "<h1>Panamá Viejo</h1>" in html
        assert "3rd-6th" in html

    def test_cover_with_picked_photo_gets_hero_band_and_tint(self):
        html = _cover_page_html(
            {"type": "cover", "media_path": "https://cdn/hero.jpg"}, "T", None, "en"
        )
        assert '<img src="https://cdn/hero.jpg"' in html
        assert 'class="tint"' in html
        assert "hero-solid" not in html
        assert "Study Guide" in html

    def test_closing_has_logo_thanks_and_contact(self):
        html = _closing_page_html("es")
        assert 'class="page p-closing"' in html
        assert "¡Gracias!" in html
        assert "panamaincontext.com" in html
        assert "diego@panamaincontext.com" in html
        assert 'data-hicon="globe-americas"' in html

    def test_section_head_first_vs_continuation(self):
        first = _section_head_html(2, "Contexto histórico", "es")
        assert "Sección 2" in first
        assert '<h2 class="g-title">Contexto histórico</h2>' in first
        assert 'class="g-rule"' in first
        cont = _section_head_html(2, "Contexto histórico", "es", cont=True)
        assert "g-cont-eyebrow" in cont
        assert "continuación" in cont
        assert "g-rule" not in cont


# --- packing (pure) ------------------------------------------------------------


def _u(frag, item, h, gap=16.0, wrap=0.0):
    return _Unit(frag=frag, item=item, h=h, item_gap=gap, wrap_h=wrap)


class TestPackUnits:
    def test_everything_fits_on_one_page(self):
        units = [_u(0, None, 100), _u(1, None, 100)]
        pages = pack_units(units, head_h=100, cont_head_h=30)
        assert len(pages) == 1
        assert [x.frag for x in pages[0]] == [0, 1]

    def test_overflow_starts_a_new_page(self):
        units = [_u(0, None, 500), _u(1, None, 500)]
        # head 100 + 30 gap + 500 + 20 + 500 > 870 → second frag moves on.
        pages = pack_units(units, head_h=100, cont_head_h=30)
        assert len(pages) == 2
        assert [x.frag for x in pages[1]] == [1]

    def test_list_splits_at_item_boundary(self):
        items = [_u(0, j, 200) for j in range(5)]  # 5×200 + gaps ≫ one page
        pages = pack_units(items, head_h=100, cont_head_h=30)
        assert len(pages) == 2
        assert [x.item for x in pages[0]] == [0, 1, 2]  # 130 head + 200*3 + 16*2 = 762
        assert [x.item for x in pages[1]] == [3, 4]

    def test_item_gap_used_within_a_list_frag_gap_between_frags(self):
        # Same-list items use the list's own gap (0 here); a different fragment
        # pays FRAG_GAP (20) — which is what pushes it to the next page.
        units = [_u(0, 0, 400, gap=0), _u(0, 1, 400, gap=0), _u(1, None, 400)]
        pages = pack_units(units, head_h=0, cont_head_h=0, usable_h=830)
        # head 0 + HEAD_GAP 30 + 400 + 0 + 400 = 830 fits; + 20 + 400 does not.
        assert [[x.frag for x in p] for p in pages] == [[0, 0], [1]]

    def test_oversized_unit_placed_alone(self):
        units = [_u(0, None, USABLE_H + 500)]
        pages = pack_units(units, head_h=100, cont_head_h=30)
        assert len(pages) == 1 and len(pages[0]) == 1

    def test_wrapper_overhead_charged_once_per_page(self):
        # Callout-style list: box overhead 100 is paid at each run start.
        # Page 1: 30 head-gap + (100+300) + 12 + 300 = 742 fits; +12+300 overflows.
        # Page 2 restarts the run: 30 + (100+300) = 430.
        units = [_u(0, j, 300, gap=12, wrap=100) for j in range(3)]
        pages = pack_units(units, head_h=0, cont_head_h=0)
        assert [[x.item for x in p] for p in pages] == [[0, 1], [2]]

    def test_wrapper_overhead_recharged_after_interleaved_frag(self):
        # A different fragment between two runs of the same list means the
        # second run rebuilds the wrapper and pays wrap_h again.
        units = [_u(0, 0, 300, gap=12, wrap=100), _u(1, None, 200), _u(0, 1, 300, gap=12, wrap=100)]
        pages = pack_units(units, head_h=0, cont_head_h=0, usable_h=1000)
        # 30 + 400 + 20 + 200 + 20 + 400 = 1070 > 1000 → last unit moves on.
        assert [[x.frag for x in p] for p in pages] == [[0, 1], [0]]


class TestPageUnitsHtml:
    def test_merges_consecutive_list_units_into_one_wrapper(self):
        frags = [
            _block_to_frag({"type": "bullets", "items": ["alpha", "beta", "gamma"]}, "es", 0),
            _block_to_frag({"type": "paragraph", "text": "prose"}, "es", 0),
        ]
        # This page carries the list's first two items plus the paragraph.
        units = [_u(0, 0, 10), _u(0, 1, 10), _u(1, None, 10)]
        html = _page_units_html(frags, units)
        assert html.count('<ul class="g-bullets">') == 1  # one wrapper for both items
        assert "alpha" in html and "beta" in html
        assert "gamma" not in html  # third item packed onto a later page
        assert '<p class="g-body">prose</p>' in html

    def test_partial_list_keeps_explicit_numbering(self):
        frags = [_block_to_frag({"type": "ordered_list", "items": ["a", "b", "c"]}, "es", 0)]
        html = _page_units_html(frags, [_u(0, 2, 10, gap=18)])
        assert '<span class="n">3</span>' in html  # third item keeps its badge number

    def test_split_callout_rebuilds_labelled_box_on_each_page(self):
        f = _block_to_frag(
            {
                "type": "callout",
                "terms": [{"term": f"t{j}", "definition": f"d{j}"} for j in range(3)],
            },
            "es",
            0,
        )
        page1 = _page_units_html([f], [_u(0, 0, 10, gap=12), _u(0, 1, 10, gap=12)])
        assert page1.count('class="g-callout"') == 1  # one box for the run
        assert "Términos clave" in page1  # lang-aware default label
        assert "<dt>t1</dt>" in page1 and "<dt>t2</dt>" not in page1
        page2 = _page_units_html([f], [_u(0, 2, 10, gap=12)])
        assert "<dt>t2</dt>" in page2
        assert "Términos clave" in page2  # continuation box keeps its label


class TestMeasuring:
    def test_snippets_include_empty_wrapper_for_list_frags(self):
        frags = [
            _block_to_frag(
                {"type": "callout", "terms": [{"term": "istmo", "definition": "puente"}]}, "es", 0
            ),
            _block_to_frag({"type": "paragraph", "text": "prose"}, "es", 0),
        ]
        snippets = _measure_snippets([frags], ["<div>head</div>"])
        assert snippets["s0f0w"] == frags[0].wrap_open + frags[0].wrap_close
        assert snippets["s0f0i0"] == frags[0].wrap_open + frags[0].items[0] + frags[0].wrap_close
        assert snippets["s0f1"] == frags[1].html
        assert "s0f1w" not in snippets  # atomic frags have no wrapper snippet

    def test_units_subtract_wrapper_overhead_from_item_heights(self):
        f = _block_to_frag(
            {"type": "callout", "terms": [{"term": f"t{j}", "definition": "d"} for j in range(2)]},
            "es",
            0,
        )
        heights = {"s0f0w": 80.0, "s0f0i0": 150.0, "s0f0i1": 140.0}
        units = _units_for_frags([f], heights, "s0")
        assert [u.h for u in units] == [70.0, 60.0]
        assert all(u.wrap_h == 80.0 for u in units)


# --- generate_guide_pdf (chromium mocked; no watermark) -------------------------


class TestGenerateGuidePdf:
    def test_legacy_markdown_renders_without_watermark(self, test_db):
        material = _guide_material(test_db, content_es="# Hola\n\nContenido de la guía.")
        storage = _FakeStorage()
        with (
            patch(
                "app.services.guide_pdf.render_html_to_pdf", return_value=_minimal_pdf()
            ) as render,
            patch("app.services.guide_pdf.ImageStorageService", return_value=storage),
        ):
            generate_guide_pdf(material, test_db, language="es")

        render.assert_called_once()
        assert material.file_url == (
            f"https://cdn.example/materials/guide/{material.research_id}/3rd_6th_es.pdf"
        )
        key, content_type, data = storage.uploads[0]
        assert content_type == "application/pdf"
        assert data[:4] == b"%PDF"
        # Guide PDFs are NOT watermarked (operator decision 2026-07-01).
        doc = fitz.open(stream=data, filetype="pdf")
        assert "panamaincontext.com" not in doc[0].get_text()
        doc.close()

    def test_blocks_path_uses_fixed_page_renderer(self, test_db):
        material = _guide_material(test_db, content_es=None)
        material.content_blocks_es = [
            {"type": "cover", "title": "C"},
            {"type": "section", "title": "S"},
            {"type": "paragraph", "text": "P"},
        ]
        test_db.commit()
        storage = _FakeStorage()
        with (
            patch(
                "app.services.guide_pdf.render_guide_pdf_from_blocks",
                return_value=_minimal_pdf(),
            ) as render,
            patch("app.services.guide_pdf.ImageStorageService", return_value=storage),
        ):
            generate_guide_pdf(material, test_db, language="es")
        render.assert_called_once_with(
            material.content_blocks_es, material.title, "3rd-6th", lang="es"
        )
        assert material.file_url

    def test_non_guide_raises(self, test_db):
        material = _guide_material(test_db, mtype="slides")
        with pytest.raises(ValueError, match="not a study guide"):
            generate_guide_pdf(material, test_db)

    def test_missing_language_content_raises(self, test_db):
        material = _guide_material(test_db, content_es=None)
        with pytest.raises(ValueError, match="no es content"):
            generate_guide_pdf(material, test_db, language="es")


# --- POST /materials/{id}/generate-pdf ------------------------------------------


class TestGuidePdfEndpoint:
    URL = "/api/v1/admin/dashboard/edu/materials/{id}/generate-pdf"

    def test_missing_material_404(self, test_client, test_db):
        assert test_client.post(self.URL.format(id=999999)).status_code == 404

    def test_non_guide_400(self, test_client, test_db):
        m = _guide_material(test_db, mtype="slides")
        assert test_client.post(self.URL.format(id=m.id)).status_code == 400

    def test_no_spanish_content_400_without_calling_service(self, test_client, test_db):
        m = _guide_material(test_db, content_es=None)
        with patch("app.api.edu.generate_guide_pdf") as svc:
            resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 400
        svc.assert_not_called()

    @patch("app.api.edu.generate_guide_pdf")
    def test_success_returns_file_url(self, mock_svc, test_client, test_db):
        m = _guide_material(test_db, content_es="# Hola\n\nTexto.")

        def fake(material, db, language="es"):
            material.file_url = f"https://cdn.example/g/{material.id}_{language}.pdf"
            return material

        mock_svc.side_effect = fake

        resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "es"
        assert body["file_url"].endswith("_es.pdf")
        assert body["grade_band"] == "3rd-6th"


# --- real chromium renders (gated) ----------------------------------------------


@requires_chromium
def test_real_chromium_legacy_markdown_render(test_db):
    from app.services.guide_pdf import render_html_to_pdf

    html = build_guide_html(
        "# La Catedral\n\nTexto en español con acentos: á é í ó ú ñ ¿?\n\n- Uno\n- Dos",
        "Panamá Viejo",
        "3rd-6th",
    )
    pdf = render_html_to_pdf(html)
    assert pdf[:4] == b"%PDF"
    doc = fitz.open(stream=pdf, filetype="pdf")
    assert doc.page_count >= 1
    text = doc[0].get_text()
    doc.close()
    assert "Catedral" in text
    assert "Panamá Viejo" in text  # accents survive the render


@requires_chromium
def test_real_chromium_fixed_page_blocks_render():
    """End-to-end kit render: cover + packed content pages + closing, Letter-sized."""
    from app.services.guide_pdf import render_guide_pdf_from_blocks

    para = (
        "La ciudad fue fundada en la costa del Pacífico. Su puerto conectaba dos océanos. "
        "Las mulas cruzaban el istmo cargadas de plata. Todo el imperio pasaba por aquí."
    )
    blocks = [
        {"type": "cover", "title": "Panamá Viejo", "subtitle": "La primera ciudad del Pacífico"},
        {"type": "section", "title": "Contexto histórico"},
        *[{"type": "paragraph", "text": para} for _ in range(10)],  # forces pagination
        {"type": "bullets", "items": [f"Punto importante número {i}" for i in range(1, 7)]},
        {"type": "timeline", "events": [{"label": "1519", "text": "Fundación de la ciudad"}]},
        {"type": "section", "title": "Pon a prueba tus conocimientos"},
        {"type": "mc_question", "question": "¿Cuándo se fundó?", "choices": ["1519", "1671"]},
        {"type": "mc_question", "question": "¿Quién la atacó?", "choices": ["Morgan", "Drake"]},
        {"type": "section", "title": "Para reflexionar"},
        {"type": "callout", "label": "Para reflexionar", "text": "¿Qué harías tú?", "terms": []},
        {"type": "image", "image_query": "colonial tower", "caption": "La torre"},
    ]
    pdf = render_guide_pdf_from_blocks(blocks, "Panamá Viejo", "3rd-6th", lang="es")
    assert pdf[:4] == b"%PDF"
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        # Letter-sized pages (816×1056 px @96dpi == 612×792 pt).
        assert round(doc[0].rect.width) == 612
        assert round(doc[0].rect.height) == 792
        # Cover + ≥2 packed content pages + closing.
        assert doc.page_count >= 4
        assert "Panamá Viejo" in doc[0].get_text()  # cover title
        body_text = "".join(doc[i].get_text() for i in range(1, doc.page_count - 1))
        assert "Contexto histórico" in body_text  # section head
        assert "Foto por seleccionar" in body_text  # unpicked image placeholder
        closing_text = doc[doc.page_count - 1].get_text()
        assert "panamaincontext.com" in closing_text  # closing contact
    finally:
        doc.close()


@requires_chromium
def test_real_chromium_long_vocabulary_flows_onto_continuation_pages():
    """A key-terms callout taller than one page splits at term boundaries
    instead of clipping at the page bottom (the 'Vocabulario clave' bug)."""
    from app.services.guide_pdf import render_guide_pdf_from_blocks

    definition = (
        "Una explicación de dos líneas completas que describe el término con el "
        "detalle suficiente para que un estudiante de primaria lo entienda bien."
    )
    blocks = [
        {"type": "cover", "title": "Panamá Viejo"},
        {"type": "section", "title": "Vocabulario clave"},
        {
            "type": "callout",
            "label": "Términos clave",
            "terms": [{"term": f"Término {i}", "definition": definition} for i in range(1, 15)],
        },
    ]
    pdf = render_guide_pdf_from_blocks(blocks, "Panamá Viejo", "3rd-6th", lang="es")
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        assert doc.page_count >= 4  # cover + ≥2 vocabulary pages + closing
        # CSS text-transform uppercases the label/eyebrow in the text layer.
        content_texts = [doc[i].get_text().lower() for i in range(1, doc.page_count - 1)]
        # The callout box (with its label) is rebuilt on every page it spans.
        assert sum("términos clave" in t for t in content_texts) >= 2
        # Every term made it into the document — nothing was clipped away.
        joined = "".join(content_texts)
        for i in range(1, 15):
            assert f"término {i}" in joined
        # Terms after the split render on a continuation page, below its eyebrow.
        assert any("continuación" in t and "término 14" in t for t in content_texts)
    finally:
        doc.close()


@requires_chromium
def test_real_chromium_timeline_page_respects_footer_reserve():
    """A multi-event timeline followed by paragraphs must not push text into
    the footer reserve (the page-9 overflow: the events' 30px spacing was a
    :last-child-trimmed padding, invisible when items are measured alone, so
    the packer under-counted the timeline by ~30px per event)."""
    from app.services.guide_pdf import BOTTOM_RESERVE, PAGE_H, render_guide_pdf_from_blocks

    para = (
        "En enero de 1671, el bucanero galés Henry Morgan dirigió una fuerza que "
        "cruzó el istmo y derrotó a las tropas españolas que defendían las afueras "
        "de la ciudad, y un incendio redujo a cenizas la mayor parte de la ciudad "
        "construida en madera, cuyo origen los historiadores aún discuten hoy."
    )
    events = [
        {
            "label": str(1519 + i * 25),
            "text": "Un acontecimiento importante en la historia de la ciudad colonial.",
        }
        for i in range(6)
    ]
    blocks = [
        {"type": "cover", "title": "Panamá Viejo"},
        {"type": "section", "title": "Contexto histórico"},
        {"type": "paragraph", "text": para},
        {"type": "timeline", "events": events},
        *[{"type": "paragraph", "text": para} for _ in range(4)],
    ]
    pdf = render_guide_pdf_from_blocks(blocks, "Panamá Viejo", "3rd-6th", lang="es")
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        # Packed content must stay above the footer reserve. Pages are 792pt
        # for 1056px, so px→pt is 0.75; allow a couple points of slack.
        content_bottom_pt = (PAGE_H - BOTTOM_RESERVE) * 0.75
        for i in range(1, doc.page_count - 1):
            for x0, y0, x1, y1, text, *_ in doc[i].get_text("blocks"):
                if text.lstrip().startswith("Panama In Context ·") or text.strip().isdigit():
                    continue  # the running footer (brand line / page number)
                assert y1 <= content_bottom_pt + 2, (
                    f"page {i + 1}: {text[:60]!r} extends to y={y1:.0f}pt, past "
                    f"the content budget at {content_bottom_pt:.0f}pt"
                )
        # Nothing was clipped away: every paragraph and event made it in.
        joined = "".join(doc[i].get_text() for i in range(1, doc.page_count - 1))
        assert joined.count("Henry Morgan") == 5
        for e in events:
            assert e["label"] in joined
    finally:
        doc.close()


def test_guide_pdf_key_is_deterministic_per_language():
    from app.services.guide_pdf import guide_pdf_key

    material = EduMaterial(material_type="guide", research_id=7, grade_band="3rd-6th", title="t")
    assert guide_pdf_key(material, "es") == "materials/guide/7/3rd_6th_es.pdf"
    assert guide_pdf_key(material, "en") == "materials/guide/7/3rd_6th_en.pdf"
    assert (
        guide_pdf_key(
            EduMaterial(material_type="guide", research_id=7, grade_band=None, title="t"), "en"
        )
        == "materials/guide/7/none_en.pdf"
    )
