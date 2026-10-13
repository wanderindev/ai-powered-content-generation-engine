"""Tests for Instagram post generation (sabías-que + article promo)."""

from unittest.mock import MagicMock, patch

import pytest
from app.models import ArticleSuggestion, Media, Research
from app.models.article import Article
from app.models.enums import InstagramPostType, InstagramTemplateVariant
from app.services.ig_html import sanitize_fact
from app.services.instagram_generation import (
    _parse_sabias_que_response,
    generate_article_promo_post,
    generate_sabias_que_posts,
)
from fastapi import HTTPException


def _mock_msg(text: str):
    return MagicMock(content=[MagicMock(text=text)])


def _make_client(*messages):
    """Mock Anthropic client serving `messages` in order across both seams."""
    queue = list(messages)
    record = MagicMock(name="llm_call")

    def _create(**kwargs):
        record(**kwargs)
        return queue.pop(0)

    def _stream(**kwargs):
        record(**kwargs)
        cm = MagicMock()
        cm.__enter__.return_value.get_final_message.return_value = queue.pop(0)
        cm.__exit__.return_value = False
        return cm

    client = MagicMock()
    client.messages.create.side_effect = _create
    client.messages.stream.side_effect = _stream
    client.llm_calls = record
    return client


def _seed_research(test_db, category_factory, *, words=100):
    category = category_factory()
    suggestion = ArticleSuggestion(
        title="Panamá Viejo",
        category_id=category.id,
        status="APPROVED",
        main_topic="Panamá Viejo",
        sub_topics=["Founding"],
        point_of_view="narrative",
    )
    test_db.add(suggestion)
    test_db.flush()
    research = Research(
        suggestion_id=suggestion.id,
        # Exactly five words per repetition, so `words` is the real word count.
        content="Historical facts about old Panama. " * (words // 5),
        status="APPROVED",
    )
    test_db.add(research)
    test_db.commit()
    return research


SABIAS_RESPONSE = """POST 1
TITLE: Arco Chato
FACT: el <b>Arco Chato</b> se mantuvo en pie casi <b>tres siglos</b> sin soporte alguno?
POLAROID: El Arco Chato, Casco Antiguo
IMAGE_QUERY: Arco Chato ruins
CAPTION:
Una hazaña de ingeniería colonial.
Conócelo en nuestros tours — enlace en la bio.
#PanamaInContext #HistoriaDePanama
---
POST 2
TITLE: Plaza de toros
FACT: la <b>Plaza Herrera</b> funcionó originalmente como una <b>plaza de toros</b>?
POLAROID: Plaza Herrera, hoy
IMAGE_QUERY: Plaza Herrera Panama
CAPTION:
Del ruedo al rincón tranquilo.
#PanamaInContext #HistoriaDePanama
---
POST 3
TITLE: Tercer dato
FACT: la ciudad fue <b>trasladada</b> completa tras el ataque de 1671?
POLAROID: Ruinas de Panamá Viejo
IMAGE_QUERY: Panama Viejo ruins
CAPTION:
La mudanza que fundó el Casco.
#PanamaInContext #HistoriaDePanama"""


class TestParser:
    def test_parses_three_posts_with_all_fields(self):
        posts = _parse_sabias_que_response(SABIAS_RESPONSE)
        assert len(posts) == 3
        assert posts[0]["title"] == "Arco Chato"
        assert "<b>Arco Chato</b>" in posts[0]["fact"]
        assert posts[0]["polaroid_caption"] == "El Arco Chato, Casco Antiguo"
        assert posts[0]["image_query"] == "Arco Chato ruins"
        assert "enlace en la bio" in posts[0]["caption"]
        assert posts[1]["caption"].endswith("#PanamaInContext #HistoriaDePanama")

    def test_blocks_without_fact_are_skipped(self):
        text = "POST 1\nTITLE: Sin hecho\nIMAGE_QUERY: something\n---\n" + SABIAS_RESPONSE
        posts = _parse_sabias_que_response(text)
        assert len(posts) == 3  # the FACT-less block is dropped

    def test_garbage_response_yields_nothing(self):
        assert _parse_sabias_que_response("I could not comply with the format.") == []

    def test_missing_title_falls_back_to_fact_prefix(self):
        posts = _parse_sabias_que_response("POST 1\nFACT: un dato corto?\n")
        assert posts[0]["title"] == "un dato corto?"


class TestSanitizeFact:
    def test_keeps_b_spans_only(self):
        assert sanitize_fact("el <b>Arco</b> resistió") == "el <b>Arco</b> resistió"

    def test_strips_other_markup(self):
        out = sanitize_fact('<script>alert(1)</script> y <i>esto</i> <b onload="x">no</b>')
        assert "<script>" not in out
        assert "<i>" not in out
        # A <b> carrying attributes is NOT restored (only bare <b> pairs are).
        assert "<b onload" not in out

    def test_idempotent(self):
        once = sanitize_fact("dato con <b>énfasis</b> & ampersand")
        assert sanitize_fact(once) == once

    def test_none_passthrough(self):
        assert sanitize_fact(None) is None


class TestGenerateSabiasQue:
    @patch("app.services.llm.get_client")
    def test_short_research_one_call_three_pending_drafts(
        self, mock_get_client, test_db, category_factory
    ):
        research = _seed_research(test_db, category_factory, words=100)
        client = _make_client(_mock_msg(SABIAS_RESPONSE))
        mock_get_client.return_value = client

        posts = generate_sabias_que_posts(test_db, research.id)

        assert client.llm_calls.call_count == 1  # no condense for short research
        assert len(posts) == 3
        assert all(p.status == "PENDING" for p in posts)
        assert all(p.post_type == InstagramPostType.SABIAS_QUE.value for p in posts)
        assert all(p.research_id == research.id for p in posts)
        assert [p.template_variant for p in posts] == [
            InstagramTemplateVariant.AZUL.value,
            InstagramTemplateVariant.CLARO.value,
            InstagramTemplateVariant.AZUL.value,
        ]

    @patch("app.services.llm.get_client")
    def test_long_research_condenses_first(self, mock_get_client, test_db, category_factory):
        research = _seed_research(test_db, category_factory, words=3000)
        client = _make_client(_mock_msg("Condensed briefing."), _mock_msg(SABIAS_RESPONSE))
        mock_get_client.return_value = client

        generate_sabias_que_posts(test_db, research.id)

        assert client.llm_calls.call_count == 2
        # The generation call received the briefing, not the raw research.
        gen_kwargs = client.llm_calls.call_args_list[1].kwargs
        assert "Condensed briefing." in gen_kwargs["messages"][0]["content"]

    @patch("app.services.llm.get_client")
    def test_unparseable_response_is_502(self, mock_get_client, test_db, category_factory):
        research = _seed_research(test_db, category_factory)
        mock_get_client.return_value = _make_client(_mock_msg("no posts here"))

        with pytest.raises(HTTPException) as exc:
            generate_sabias_que_posts(test_db, research.id)
        assert exc.value.status_code == 502

    def test_missing_research_is_404(self, test_db):
        with pytest.raises(HTTPException) as exc:
            generate_sabias_que_posts(test_db, 99999)
        assert exc.value.status_code == 404


def _seed_series(test_db, category_factory, *, children=2, with_feature_image=True):
    research = _seed_research(test_db, category_factory)
    feature = None
    if with_feature_image:
        feature = Media(
            filename="feature.jpg",
            original_filename="feature.jpg",
            file_path="https://cdn.example.com/media/feature.jpg",
            file_size=1234,
            mime_type="image/jpeg",
            media_type="IMAGE",
            source="LOCAL",
        )
        test_db.add(feature)
        test_db.flush()
    parent = Article(
        research_id=research.id,
        category_id=research.suggestion.category_id,
        title="The Three Panamas",
        title_es="Los Tres Panamás",
        status="APPROVED",
        feature_image_id=feature.id if feature else None,
    )
    test_db.add(parent)
    test_db.flush()
    for i in range(children):
        test_db.add(
            Article(
                research_id=research.id,
                category_id=parent.category_id,
                title=f"Part {i + 2}",
                title_es=f"Parte {i + 2}",
                status="APPROVED",
                series_parent_id=parent.id,
                series_order=i + 1,
            )
        )
    test_db.commit()
    return parent


PROMO_RESPONSE = """CAPTION:
Nueva serie en el blog sobre los tres Panamás.
Léela completa — enlace en la bio.
#PanamaInContext #HistoriaDePanama"""


class TestGenerateArticlePromo:
    @patch("app.services.llm.get_client")
    def test_series_parent_promo_fields_from_db(self, mock_get_client, test_db, category_factory):
        parent = _seed_series(test_db, category_factory, children=2)
        mock_get_client.return_value = _make_client(_mock_msg(PROMO_RESPONSE))

        post = generate_article_promo_post(test_db, parent.id)

        assert post.post_type == InstagramPostType.ARTICLE_PROMO.value
        assert post.status == "PENDING"
        assert post.article_id == parent.id
        assert post.title == "Los Tres Panamás"  # Spanish title preferred
        assert post.kicker == "Serie · 3 artículos"
        assert post.meta_text == "por Diego Raúl Feliu · 3 artículos"
        assert post.caption.startswith("Nueva serie en el blog")
        # Background defaults to the feature image, still operator-swappable.
        assert post.media_path == "https://cdn.example.com/media/feature.jpg"

    @patch("app.services.llm.get_client")
    def test_standalone_article_gets_historia_kicker(
        self, mock_get_client, test_db, category_factory
    ):
        parent = _seed_series(test_db, category_factory, children=0, with_feature_image=False)
        mock_get_client.return_value = _make_client(_mock_msg(PROMO_RESPONSE))

        post = generate_article_promo_post(test_db, parent.id)

        assert post.kicker == "Historia"
        assert post.meta_text == "por Diego Raúl Feliu"
        assert post.media_path is None

    def test_series_member_is_rejected(self, test_db, category_factory):
        parent = _seed_series(test_db, category_factory, children=1)
        member = (
            test_db.query(Article).filter(Article.series_parent_id == parent.id).first()
        )
        with pytest.raises(HTTPException) as exc:
            generate_article_promo_post(test_db, member.id)
        assert exc.value.status_code == 400
        assert "series parent" in exc.value.detail

    def test_unapproved_article_is_rejected(self, test_db, category_factory):
        parent = _seed_series(test_db, category_factory, children=0)
        parent.status = "PENDING"
        test_db.commit()
        with pytest.raises(HTTPException) as exc:
            generate_article_promo_post(test_db, parent.id)
        assert exc.value.status_code == 400

    def test_missing_article_is_404(self, test_db):
        with pytest.raises(HTTPException) as exc:
            generate_article_promo_post(test_db, 99999)
        assert exc.value.status_code == 404
