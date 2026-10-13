"""Tests for the article generation pipeline (3 phases)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.models.article import Article
from app.models.article_suggestion import ArticleSuggestion
from app.models.research import Research
from app.models.tag import Tag

# --- Helpers ---

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


def _make_client(*messages):
    """Mock Anthropic client whose calls consume `messages` in order across BOTH
    the create (short) and stream (long >= threshold) seams create_message uses,
    so a service's mixed streamed/non-streamed sequence is satisfied regardless
    of which path each call takes."""
    queue = list(messages)

    def _create(**kwargs):
        return queue.pop(0)

    def _stream(**kwargs):
        cm = MagicMock()
        cm.__enter__.return_value.get_final_message.return_value = queue.pop(0)
        cm.__exit__.return_value = False
        return cm

    client = MagicMock()
    client.messages.create.side_effect = _create
    client.messages.stream.side_effect = _stream
    return client


def _mock_anthropic_response(text: str):
    """Create a mock Anthropic client that returns the given text."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=text)]
    return _make_client(mock_msg)


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    """Bypass admin token validation for all tests in this module (#54).

    The router gates auth via ``dependencies=[Depends(validate_admin_token)]``,
    so we override the dependency rather than patching the imported name.
    """
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


SINGLE_OUTLINE_JSON = json.dumps({
    "articles": [
        {
            "title": "The Panama Canal: A Historical Overview",
            "summary": "An overview of the canal's construction and impact.",
            "outline": "## Introduction\n- Key context\n\n## Construction\n- Timeline\n\n## Impact\n- Economic effects",
            "estimated_word_count": 2000,
        }
    ]
})

SERIES_OUTLINE_JSON = json.dumps({
    "articles": [
        {
            "title": "The Canal: Part 1 - The French Attempt",
            "summary": "The failed French effort to build the canal.",
            "outline": "## French Era\n- De Lesseps\n\n## Failure\n- Disease",
            "estimated_word_count": 2000,
        },
        {
            "title": "The Canal: Part 2 - American Success",
            "summary": "How the US completed the canal.",
            "outline": "## American Takeover\n- Roosevelt\n\n## Completion\n- 1914",
            "estimated_word_count": 2000,
        },
    ]
})

ARTICLE_CONTENT = "## Introduction\n\nThe Panama Canal is one of the greatest engineering achievements in modern history. " * 20
EXCERPT_TEXT = "The Panama Canal transformed global trade. This article explores its remarkable history."
SUMMARY_TEXT = "This article examines the Panama Canal's construction. It covers the French and American efforts. The canal's impact on global trade is assessed."

TAGS_JSON = json.dumps({
    "assign": ["Panama Canal"],
    "suggest": ["Engineering History"],
})


def _create_research_chain(test_db, category_factory, research_status="APPROVED"):
    """Create a full suggestion -> research chain for testing."""
    category = category_factory()
    suggestion = ArticleSuggestion(
        title="Test Suggestion",
        category_id=category.id,
        status="APPROVED",
        main_topic="Canal construction",
        sub_topics=["French attempt", "American success"],
        point_of_view="narrative",
    )
    test_db.add(suggestion)
    test_db.flush()

    research = Research(
        suggestion_id=suggestion.id,
        content="Research content about Panama Canal history. " * 300,
        status=research_status,
    )
    test_db.add(research)
    test_db.flush()
    return category, suggestion, research


# --- Phase 1: Outline Generation ---


class TestGenerateOutlines:
    """Tests for POST /admin/dashboard/articles/generate-outlines."""

    @patch("app.services.llm.get_client")
    def test_creates_single_article(
        self, mock_get_client, test_client, test_db, category_factory
    ):
        mock_get_client.return_value = _mock_anthropic_response(SINGLE_OUTLINE_JSON)
        category, suggestion, research = _create_research_chain(test_db, category_factory)

        response = test_client.post(
            "/api/v1/admin/dashboard/articles/generate-outlines",
            params={"token": "any"},
            json={"research_id": research.id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["research_id"] == research.id
        assert data["articles_created"] == 1
        assert data["is_series"] is False
        assert data["articles"][0]["title"] == "The Panama Canal: A Historical Overview"

    @patch("app.services.llm.get_client")
    def test_rejects_non_approved_research(
        self, mock_get_client, test_client, test_db, category_factory
    ):
        category, suggestion, research = _create_research_chain(
            test_db, category_factory, research_status="PENDING"
        )

        response = test_client.post(
            "/api/v1/admin/dashboard/articles/generate-outlines",
            params={"token": "any"},
            json={"research_id": research.id},
        )

        assert response.status_code == 400
        assert "APPROVED" in response.json()["detail"]

    @patch("app.services.llm.get_client")
    def test_rejects_duplicate_articles(
        self, mock_get_client, test_client, test_db, category_factory
    ):
        category, suggestion, research = _create_research_chain(test_db, category_factory)

        # Create existing article for this research
        article = Article(
            research_id=research.id,
            category_id=category.id,
            title="Existing Article",
            status="PENDING",
        )
        test_db.add(article)
        test_db.flush()

        response = test_client.post(
            "/api/v1/admin/dashboard/articles/generate-outlines",
            params={"token": "any"},
            json={"research_id": research.id},
        )

        assert response.status_code == 400
        assert "already exist" in response.json()["detail"]

    @patch("app.services.llm.get_client")
    def test_creates_series(
        self, mock_get_client, test_client, test_db, category_factory
    ):
        mock_get_client.return_value = _mock_anthropic_response(SERIES_OUTLINE_JSON)
        category, suggestion, research = _create_research_chain(test_db, category_factory)

        response = test_client.post(
            "/api/v1/admin/dashboard/articles/generate-outlines",
            params={"token": "any"},
            json={"research_id": research.id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["articles_created"] == 2
        assert data["is_series"] is True

        articles_data = data["articles"]
        assert articles_data[0]["is_series_parent"] is True
        assert articles_data[0]["series_order"] == 1
        assert articles_data[1]["series_order"] == 2

        # Verify DB parent/child relationship
        parent = test_db.query(Article).filter(
            Article.id == articles_data[0]["article_id"]
        ).first()
        child = test_db.query(Article).filter(
            Article.id == articles_data[1]["article_id"]
        ).first()
        assert child.series_parent_id == parent.id


class TestGenerateOutlinesDefensiveParsing:
    """parse_llm_json normalizes bad LLM responses to ValueError (issue #77)."""

    @patch("app.services.llm.get_client")
    def test_missing_articles_key_raises_value_error(
        self, mock_get_client, test_db, category_factory
    ):
        from app.services.article_generation import generate_outlines

        mock_get_client.return_value = _mock_anthropic_response(json.dumps({"wrong_key": []}))
        category, suggestion, research = _create_research_chain(test_db, category_factory)
        taxonomy = category.taxonomy

        with pytest.raises(ValueError, match="missing required key"):
            generate_outlines(research, suggestion, category, taxonomy)

    @patch("app.services.llm.get_client")
    def test_malformed_json_raises_value_error(self, mock_get_client, test_db, category_factory):
        from app.services.article_generation import generate_outlines

        mock_get_client.return_value = _mock_anthropic_response("not valid json")
        category, suggestion, research = _create_research_chain(test_db, category_factory)
        taxonomy = category.taxonomy

        with pytest.raises(ValueError, match="malformed JSON"):
            generate_outlines(research, suggestion, category, taxonomy)


class TestArticleCondensation:
    """#102: outlining sees the full research; the content step condenses with an
    outline-targeted briefing (broad context + focus) on Sonnet."""

    @patch("app.services.llm.get_client")
    def test_outline_uses_full_research(
        self, mock_get_client, test_db, category_factory
    ):
        from app.services.article_generation import generate_outlines

        client = _make_client(
            MagicMock(content=[MagicMock(text=SINGLE_OUTLINE_JSON)]),  # outline (Sonnet)
        )
        mock_get_client.return_value = client

        category, suggestion, research = _create_research_chain(test_db, category_factory)
        research.content = "word " * 3000  # above CONDENSE_THRESHOLD (2500)
        test_db.flush()

        result = generate_outlines(research, suggestion, category, category.taxonomy)

        assert len(result) == 1
        # Phase 1 never condenses: a single outline call (streamed), no condense.
        assert client.messages.create.call_count == 0
        assert client.messages.stream.call_count == 1
        # The raw research — not a condensed briefing — feeds the outline prompt.
        outline_prompt = client.messages.stream.call_args.kwargs["messages"][0]["content"]
        assert "word word word" in outline_prompt

    @patch("app.services.llm.get_client")
    def test_long_research_condensed_before_content(
        self, mock_get_client, test_db, category_factory
    ):
        from app.services.article_generation import generate_article_content
        from app.services.llm import SONNET_MODEL

        client = _make_client(
            MagicMock(content=[MagicMock(text="Condensed briefing")]),  # condense (Sonnet)
            MagicMock(content=[MagicMock(text=ARTICLE_CONTENT)]),  # content (Sonnet)
        )
        mock_get_client.return_value = client

        category, suggestion, research = _create_research_chain(test_db, category_factory)
        research.content = "word " * 3000
        test_db.flush()

        article = Article(
            research_id=research.id,
            category_id=category.id,
            title="T",
            outline="## Distinctive Section\n- a detail",
            status="PENDING",
        )
        test_db.add(article)
        test_db.flush()

        content = generate_article_content(
            article, research, suggestion, category, category.taxonomy
        )

        assert content == ARTICLE_CONTENT
        # Both calls stream: the condense cap sits at the streaming threshold
        # (thinking counts toward max_tokens on Sonnet 5) and content is 12k.
        assert client.messages.create.call_count == 0
        assert client.messages.stream.call_count == 2  # condense, then content
        # The focus condensation runs on Sonnet and is keyed to the article outline.
        condense_call = client.messages.stream.call_args_list[0].kwargs
        assert condense_call["model"] == SONNET_MODEL
        condense_prompt = condense_call["messages"][0]["content"]
        assert "## Distinctive Section" in condense_prompt
        assert "word word word" in condense_prompt
        # The condensed briefing — not the raw research — feeds the content prompt.
        content_prompt = client.messages.stream.call_args_list[1].kwargs["messages"][0]["content"]
        assert "Condensed briefing" in content_prompt

    @patch("app.services.llm.get_client")
    def test_short_research_skips_condensation_before_content(
        self, mock_get_client, test_db, category_factory
    ):
        from app.services.article_generation import generate_article_content

        client = _make_client(
            MagicMock(content=[MagicMock(text=ARTICLE_CONTENT)]),  # content only
        )
        mock_get_client.return_value = client

        category, suggestion, research = _create_research_chain(test_db, category_factory)
        research.content = "word " * 100  # below threshold
        test_db.flush()

        article = Article(
            research_id=research.id,
            category_id=category.id,
            title="T",
            outline="## Outline",
            status="PENDING",
        )
        test_db.add(article)
        test_db.flush()

        content = generate_article_content(
            article, research, suggestion, category, category.taxonomy
        )

        assert content == ARTICLE_CONTENT
        # No condense call — only the single content call (streamed).
        assert client.messages.create.call_count == 0
        assert client.messages.stream.call_count == 1
        # Full research (verbatim) feeds the content prompt.
        content_prompt = client.messages.stream.call_args.kwargs["messages"][0]["content"]
        assert "word word word" in content_prompt


# --- Phase 2: Article Writing ---


class TestWriteArticle:
    """Tests for POST /admin/dashboard/articles/{id}/write."""

    @patch("app.services.llm.get_client")
    def test_writes_article_content(
        self, mock_get_client, test_client, test_db, category_factory
    ):
        # content (streamed, 8k) then excerpt + summary (Haiku, non-streamed)
        mock_get_client.return_value = _make_client(
            MagicMock(content=[MagicMock(text=ARTICLE_CONTENT)]),
            MagicMock(content=[MagicMock(text=EXCERPT_TEXT)]),
            MagicMock(content=[MagicMock(text=SUMMARY_TEXT)]),
        )

        category, suggestion, research = _create_research_chain(test_db, category_factory)

        article = Article(
            research_id=research.id,
            category_id=category.id,
            title="The Panama Canal: A Historical Overview",
            outline="## Introduction\n- Key context\n\n## Construction\n- Timeline",
            outline_status="APPROVED",
            status="PENDING",
        )
        test_db.add(article)
        test_db.flush()

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/write",
            params={"token": "any"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["article_id"] == article.id
        assert data["word_count"] > 0
        assert data["excerpt"] == EXCERPT_TEXT
        assert data["ai_summary"] == SUMMARY_TEXT

    def test_requires_outline(self, test_client, test_db, category_factory):
        category, suggestion, research = _create_research_chain(test_db, category_factory)
        article = Article(
            research_id=research.id,
            category_id=category.id,
            title="No Outline Article",
            status="PENDING",
        )
        test_db.add(article)
        test_db.flush()

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/write",
            params={"token": "any"},
        )

        assert response.status_code == 400
        assert "outline" in response.json()["detail"].lower()

    def test_rejects_unapproved_outline(self, test_client, test_db, category_factory):
        # Server-side outline-review gate (#104): an outline exists but has not
        # been APPROVED — writing must be refused, mirroring the research gate.
        category, suggestion, research = _create_research_chain(test_db, category_factory)
        article = Article(
            research_id=research.id,
            category_id=category.id,
            title="Unapproved Outline Article",
            outline="## Introduction\n- Key context",
            outline_status="PENDING",
            status="PENDING",
        )
        test_db.add(article)
        test_db.flush()

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/write",
            params={"token": "any"},
        )

        assert response.status_code == 400
        assert "approved" in response.json()["detail"].lower()

    def test_rejects_existing_content(self, test_client, test_db, category_factory):
        category, suggestion, research = _create_research_chain(test_db, category_factory)
        article = Article(
            research_id=research.id,
            category_id=category.id,
            title="Already Written Article",
            outline="## Outline",
            outline_status="APPROVED",
            content="Already has content",
            status="PENDING",
        )
        test_db.add(article)
        test_db.flush()

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/write",
            params={"token": "any"},
        )

        assert response.status_code == 400
        assert "already has content" in response.json()["detail"].lower()


# --- Phase 3: Tagging ---


class TestGenerateTags:
    """Tests for POST /admin/dashboard/articles/{id}/generate-tags."""

    @patch("app.services.llm.get_client")
    def test_assigns_and_creates_tags(
        self, mock_get_client, test_client, test_db, category_factory, research_factory
    ):
        mock_get_client.return_value = _mock_anthropic_response(TAGS_JSON)

        from datetime import UTC, datetime

        existing_tag = Tag(
            name="Panama Canal",
            status="APPROVED",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        test_db.add(existing_tag)
        test_db.flush()

        category = category_factory()
        article = Article(
            research_id=research_factory().id,
            category_id=category.id,
            title="Tagging Test Article",
            content="Article content about the Panama Canal " * 100,
            outline="## Outline",
            status="PENDING",
        )
        test_db.add(article)
        test_db.flush()

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/generate-tags",
            params={"token": "any"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["article_id"] == article.id
        assert existing_tag.id in data["assigned_tag_ids"]
        assert "Engineering History" in data["suggested_new_tags"]

        # Verify new tag was created as PENDING
        new_tag = test_db.query(Tag).filter(Tag.name == "Engineering History").first()
        assert new_tag is not None
        assert new_tag.status == "PENDING"

    def test_requires_content(self, test_client, test_db, category_factory, research_factory):
        category = category_factory()
        article = Article(
            research_id=research_factory().id,
            category_id=category.id,
            title="No Content Article",
            outline="## Outline",
            status="PENDING",
        )
        test_db.add(article)
        test_db.flush()

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/generate-tags",
            params={"token": "any"},
        )

        assert response.status_code == 400
        assert "no content" in response.json()["detail"].lower()
