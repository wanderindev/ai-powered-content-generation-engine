"""Schema-layer tests for the edu_slides child table (#320).

The test DB is built from model metadata, so the model's CHECK / FK / unique
constraints are real here.
"""

import pytest
from app.models import EduMaterial, EduResearch, EduSlide, EduSuggestion
from sqlalchemy.exc import IntegrityError


def _slides_material(db):
    """A minimal slides-type EduMaterial to attach slides to."""
    s = EduSuggestion(title="t")
    db.add(s)
    db.flush()
    r = EduResearch(suggestion_id=s.id, content="x")
    db.add(r)
    db.flush()
    m = EduMaterial(
        research_id=r.id, material_type="slides", grade_band="3rd-6th", title="Deck"
    )
    db.add(m)
    db.flush()
    return m


def test_valid_slide_inserts_and_links(test_db):
    m = _slides_material(test_db)
    test_db.add(
        EduSlide(material_id=m.id, position=1, template="bullets", title="Hola", narration="...")
    )
    test_db.flush()
    test_db.refresh(m)
    assert [s.template for s in m.slides] == ["bullets"]


def test_bad_template_rejected(test_db):
    m = _slides_material(test_db)
    test_db.add(EduSlide(material_id=m.id, position=1, template="hologram"))
    with pytest.raises(IntegrityError):
        test_db.flush()


def test_duplicate_position_rejected(test_db):
    m = _slides_material(test_db)
    test_db.add(EduSlide(material_id=m.id, position=1, template="title"))
    test_db.flush()
    test_db.add(EduSlide(material_id=m.id, position=1, template="bullets"))
    with pytest.raises(IntegrityError):
        test_db.flush()


def test_slides_cascade_delete_with_material(test_db):
    m = _slides_material(test_db)
    test_db.add(EduSlide(material_id=m.id, position=1, template="title"))
    test_db.flush()
    test_db.delete(m)
    test_db.flush()
    assert test_db.query(EduSlide).filter(EduSlide.material_id == m.id).count() == 0


def test_media_pick_persists_and_nulls_on_media_delete(test_db):
    # Slides redesign: media_id/media_path are the human photo pick; deleting
    # the Media row must not orphan the slide (FK is ON DELETE SET NULL).
    from app.models.media import Media

    m = _slides_material(test_db)
    media = Media(
        filename="plaza.jpg",
        original_filename="plaza.jpg",
        file_path="https://cdn.example/photos/plaza.jpg",
        file_size=123,
        mime_type="image/jpeg",
        media_type="IMAGE",
        source="WIKIMEDIA",
    )
    test_db.add(media)
    test_db.flush()

    slide = EduSlide(
        material_id=m.id,
        position=1,
        template="bullets",
        media_id=media.id,
        media_path="https://cdn.example/photos/plaza.jpg",
    )
    test_db.add(slide)
    test_db.commit()

    test_db.delete(media)
    test_db.commit()
    test_db.refresh(slide)
    assert slide.media_id is None
    assert slide.media_path == "https://cdn.example/photos/plaza.jpg"  # URL pick survives
