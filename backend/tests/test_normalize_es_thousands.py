"""Tests for scripts.normalize_es_thousands (#531)."""

from app.models.article import Article
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_suggestion import EduSuggestion
from app.utils.guide_blocks import map_prose
from scripts.normalize_es_thousands import normalize_es_thousands_rows


def _guide(db, blocks_es, content_es=None):
    s = EduSuggestion(title="t")
    db.add(s)
    db.flush()
    r = EduResearch(suggestion_id=s.id, content="x")
    db.add(r)
    db.flush()
    m = EduMaterial(
        research_id=r.id,
        material_type="guide",
        grade_band="3rd-6th",
        title="G",
        content="c",
        content_es=content_es,
        content_blocks_es=blocks_es,
        status="PENDING",
    )
    db.add(m)
    db.commit()
    return m


def test_dry_run_reports_but_writes_nothing(test_db, article_factory):
    a = article_factory(title="Dry")
    a.content_es = "Unos 3.000 hombres y 1,500 más."
    test_db.commit()

    result = normalize_es_thousands_rows(test_db, execute=False)

    assert [(c.table, c.row_id, c.column) for c in result["changes"]] == [
        ("articles", a.id, "content_es")
    ]
    assert result["changes"][0].numbers[0].startswith("3.000 -> 3,000")
    test_db.expire_all()
    assert test_db.get(Article, a.id).content_es == "Unos 3.000 hombres y 1,500 más."


def test_execute_rewrites_article_columns(test_db, article_factory):
    a = article_factory(title="Wet")
    a.content_es = "Hasta 10.000 indígenas."
    a.title_es = "Los 2.000 de Portobelo"
    a.excerpt_es = "Unos 1.200 soldados."
    clean = article_factory(title="Clean")
    clean.content_es = "Ya 1,500 y 3.5 millas."
    test_db.commit()

    result = normalize_es_thousands_rows(test_db, execute=True)

    assert {c.row_id for c in result["changes"]} == {a.id}
    test_db.expire_all()
    a = test_db.get(Article, a.id)
    assert a.content_es == "Hasta 10,000 indígenas."
    assert a.title_es == "Los 2,000 de Portobelo"
    assert a.excerpt_es == "Unos 1,200 soldados."
    assert test_db.get(Article, clean.id).content_es == "Ya 1,500 y 3.5 millas."


def test_narrated_articles_are_reported(test_db, article_factory):
    narrated = article_factory(title="Narrated")
    narrated.content_es = "Eran 4.000."
    narrated.audio_url_es = "https://cdn/a.mp3"
    silent = article_factory(title="Silent")
    silent.content_es = "Eran 5.000."
    test_db.commit()

    result = normalize_es_thousands_rows(test_db, execute=False)

    assert result["narrated_articles"] == [narrated.id]


def test_edu_blocks_prose_normalised_machine_keys_kept(test_db):
    blocks = [
        {"type": "paragraph", "text": "Unos 2.500 trabajadores"},
        {"type": "image", "image_query": "1.500 workers", "caption": "Hacia 1.914"},
    ]
    m = _guide(test_db, blocks, content_es="Unos 2.500 trabajadores")

    normalize_es_thousands_rows(test_db, execute=True)

    test_db.expire_all()
    m = test_db.get(EduMaterial, m.id)
    assert m.content_blocks_es[0]["text"] == "Unos 2,500 trabajadores"
    assert m.content_blocks_es[1]["image_query"] == "1.500 workers"  # machine key untouched
    assert m.content_blocks_es[1]["caption"] == "Hacia 1,914"
    assert m.content_es == "Unos 2,500 trabajadores"


def test_map_prose_does_not_mutate_input():
    blocks = [{"type": "paragraph", "text": "a"}, {"type": "image", "image_query": "a"}]
    out = map_prose(blocks, str.upper)
    assert out == [{"type": "paragraph", "text": "A"}, {"type": "image", "image_query": "a"}]
    assert blocks[0]["text"] == "a"
    assert map_prose(None, str.upper) is None
