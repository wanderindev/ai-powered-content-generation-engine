"""Tests for SeriesSectionGenerator's refuse-on-rerun idempotency policy (#99).

These tests exercise the refusal in generate_sections_for_series, which
matches the HTTP-400 surface used by the other regeneration-guarded flows
in the article pipeline (outline-gen, write_article, translate_article).
The underlying generate_about_section / generate_continue_reading_section
helpers are exercised indirectly via the happy-path test.
"""

import pytest
from app.models.article import Article
from app.services.series_sections import (
    ABOUT_MARKER_EN,
    ABOUT_MARKER_ES,
    SeriesSectionGenerator,
)
from fastapi import HTTPException


@pytest.fixture
def two_part_series(test_db, suggestion_factory, research_factory, article_factory):
    """Build a 2-article published series with both EN and ES content and
    titles set on the suggestion, ready to feed SeriesSectionGenerator.

    Returns (parent_article, child_article, suggestion, research).
    """
    suggestion = suggestion_factory(title="History of the Panama Canal")
    suggestion.title_es = "Historia del Canal de Panamá"
    test_db.add(suggestion)
    test_db.commit()
    research = research_factory(suggestion=suggestion)

    parent = article_factory(
        title="History of the Panama Canal — Part 1",
        content="Part 1 body content.",
        research_id=research.id,
        series_order=1,
    )
    parent.title_es = "Historia del Canal de Panamá — Parte 1"
    parent.content_es = "Contenido de la parte 1."
    test_db.add(parent)

    child = article_factory(
        title="History of the Panama Canal — Part 2",
        content="Part 2 body content.",
        research_id=research.id,
        series_parent=parent,
        series_order=2,
    )
    child.title_es = "Historia del Canal de Panamá — Parte 2"
    child.content_es = "Contenido de la parte 2."
    test_db.add(child)

    test_db.commit()
    test_db.refresh(parent)
    test_db.refresh(child)
    return parent, child, suggestion, research


class TestSeriesSectionsIdempotencyRefusal:
    """Refuse-on-rerun policy for #99 — raises HTTPException(400) if any
    series article already carries the about-marker in either language."""

    def test_refusal_when_en_marker_present_in_content(self, test_db, two_part_series):
        parent, child, _, _ = two_part_series

        # Simulate an already-processed parent article (EN side only).
        parent.content = f"{ABOUT_MARKER_EN}\n\nIntro blurb...\n\n---\n\n" + (parent.content or "")
        test_db.add(parent)
        test_db.commit()

        # Capture pre-call state so we can verify nothing was written.
        before_parent_content = parent.content
        before_parent_content_es = parent.content_es
        before_child_content = child.content
        before_child_content_es = child.content_es

        generator = SeriesSectionGenerator(test_db)
        with pytest.raises(HTTPException) as exc_info:
            generator.generate_sections_for_series(parent.id)

        assert exc_info.value.status_code == 400
        assert "EN about-marker" in exc_info.value.detail
        assert str(parent.id) in exc_info.value.detail
        assert "#99" in exc_info.value.detail

        # No double-append should have occurred — content unchanged.
        test_db.refresh(parent)
        test_db.refresh(child)
        assert parent.content == before_parent_content
        assert parent.content_es == before_parent_content_es
        assert child.content == before_child_content
        assert child.content_es == before_child_content_es

    def test_refusal_when_es_marker_present_in_content_es(self, test_db, two_part_series):
        parent, child, _, _ = two_part_series

        # Simulate an already-processed child article (ES side only).
        child.content_es = f"{ABOUT_MARKER_ES}\n\nIntroducción...\n\n---\n\n" + (
            child.content_es or ""
        )
        test_db.add(child)
        test_db.commit()

        before_parent_content = parent.content
        before_parent_content_es = parent.content_es
        before_child_content = child.content
        before_child_content_es = child.content_es

        generator = SeriesSectionGenerator(test_db)
        with pytest.raises(HTTPException) as exc_info:
            generator.generate_sections_for_series(parent.id)

        assert exc_info.value.status_code == 400
        assert "ES about-marker" in exc_info.value.detail
        assert str(child.id) in exc_info.value.detail

        test_db.refresh(parent)
        test_db.refresh(child)
        assert parent.content == before_parent_content
        assert parent.content_es == before_parent_content_es
        assert child.content == before_child_content
        assert child.content_es == before_child_content_es

    def test_happy_path_generates_when_no_markers_present(self, test_db, two_part_series):
        parent, child, _, _ = two_part_series

        # Sanity-check: no markers present anywhere before the call.
        for col in (
            parent.content,
            parent.content_es,
            child.content,
            child.content_es,
        ):
            assert ABOUT_MARKER_EN not in (col or "")
            assert ABOUT_MARKER_ES not in (col or "")

        generator = SeriesSectionGenerator(test_db)
        success, message, updated = generator.generate_sections_for_series(parent.id)

        assert success is True
        assert updated == 2
        assert "Generated sections" in message

        # Re-read; each article now carries the about-marker exactly once
        # in both languages.
        for aid in (parent.id, child.id):
            article = test_db.query(Article).filter(Article.id == aid).first()
            assert article.content.count(ABOUT_MARKER_EN) == 1
            assert article.content_es.count(ABOUT_MARKER_ES) == 1


class TestSeriesSectionsOrmGeneration:
    """Tests for the ORM-based content mutation in generate_sections_for_series.

    Pins down behavior that the prior raw-SQL UPDATE provided structurally
    (about prepended, body unchanged, continue appended) and behavior that
    the ORM path newly restores (updated_at trigger firing).
    """

    def test_generated_content_has_correct_shape(self, test_db, two_part_series):
        parent, child, _, _ = two_part_series

        original_parent_content = parent.content
        original_parent_content_es = parent.content_es
        original_child_content = child.content
        original_child_content_es = child.content_es

        generator = SeriesSectionGenerator(test_db)
        success, _, _ = generator.generate_sections_for_series(parent.id)
        assert success is True

        test_db.refresh(parent)
        test_db.refresh(child)

        # Parent (first in series): about at start, body preserved,
        # continue-section appended.
        assert parent.content.startswith(ABOUT_MARKER_EN)
        assert original_parent_content in parent.content
        assert "**Continue Reading**" in parent.content
        assert parent.content.index(ABOUT_MARKER_EN) < parent.content.index(original_parent_content)
        assert parent.content.index(original_parent_content) < parent.content.index(
            "**Continue Reading**"
        )

        assert parent.content_es.startswith(ABOUT_MARKER_ES)
        assert original_parent_content_es in parent.content_es
        assert "**Continuar leyendo**" in parent.content_es

        # Child (last in series): about at start, body preserved, NO
        # continue-section (last article has no next).
        assert child.content.startswith(ABOUT_MARKER_EN)
        assert original_child_content in child.content
        assert "**Continue Reading**" not in child.content

        assert child.content_es.startswith(ABOUT_MARKER_ES)
        assert original_child_content_es in child.content_es
        assert "**Continuar leyendo**" not in child.content_es


class TestSeriesSectionsEndpoint:
    """End-to-end tests for POST /admin/dashboard/articles/{id}/series-sections.

    Exercises the endpoint surface: success path returns 200/success=true,
    re-run path returns HTTP 400 with the refusal detail (#99 policy).
    """

    ENDPOINT_FMT = "/api/v1/admin/dashboard/articles/{id}/series-sections"

    def test_endpoint_rerun_is_an_idempotent_no_op(self, test_client, admin_token, two_part_series):
        """The weekly planner schedules a series-sections task per member and
        each regenerates the whole series — a rerun after full success must
        converge (200, 0 updated), not park the sibling tasks on a 400."""
        parent, _, _, _ = two_part_series

        url = self.ENDPOINT_FMT.format(id=parent.id)

        # First call: clean state, should succeed.
        first = test_client.post(f"{url}?token={admin_token}")
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["success"] is True
        assert first_body["articles_updated"] == 2

        # Second call: every article carries the about-marker — no-op success.
        second = test_client.post(f"{url}?token={admin_token}")
        assert second.status_code == 200, second.text
        second_body = second.json()
        assert second_body["success"] is True
        assert second_body["articles_updated"] == 0
        assert "already generated" in second_body["message"]

    def test_endpoint_still_refuses_a_partially_marked_series(
        self, test_client, admin_token, two_part_series, test_db
    ):
        """Only SOME articles carrying the marker is an inconsistent state a
        human has to untangle — the #99 refuse-on-rerun 400 stays for that."""
        parent, child, _, _ = two_part_series

        url = self.ENDPOINT_FMT.format(id=parent.id)
        first = test_client.post(f"{url}?token={admin_token}")
        assert first.status_code == 200, first.text

        # Strip the markers from the child only.
        from app.services.series_sections import ABOUT_MARKER_EN

        test_db.refresh(child)
        child.content = child.content.split(ABOUT_MARKER_EN)[-1]
        test_db.commit()

        second = test_client.post(f"{url}?token={admin_token}")
        assert second.status_code == 400, second.text
        detail = second.json().get("detail", "")
        assert "already has series sections" in detail
        assert "#99" in detail
