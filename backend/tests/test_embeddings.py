"""Tests for the Voyage embeddings seam (#622).

No real HTTP: ``_get_client`` is patched to an ``httpx.Client`` over a
``MockTransport``, and the key is set on the ``lru_cache``d settings singleton so
an ambient ``VOYAGE_API_KEY`` can never reach the network.
"""

import json
import logging

import httpx
import pytest
from app.core.config import get_settings
from app.services import embeddings
from app.services.embeddings import (
    EmbeddingsDisabledError,
    EmbeddingsError,
    embed_documents,
    embed_query,
    rerank,
)

FAKE_KEY = "test-voyage-key"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "voyage_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "embedding_model", "voyage-4")
    monkeypatch.setattr(settings, "embedding_dimensions", 1024)
    monkeypatch.setattr(settings, "rerank_model", "rerank-2.5")


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    # tenacity sleeps between retries; keep the retry tests instant.
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None)


@pytest.fixture
def mock_voyage(monkeypatch):
    """Install a MockTransport handler; returns the list of captured requests."""
    state = {"handler": None, "requests": []}

    def dispatch(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["handler"](request)

    client = httpx.Client(transport=httpx.MockTransport(dispatch))
    monkeypatch.setattr(embeddings, "_get_client", lambda: client)

    def install(handler):
        state["handler"] = handler
        return state["requests"]

    return install


def _embeddings_ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    inputs = body["input"]
    # Return out of order to prove the seam sorts by index.
    data = [{"index": i, "embedding": [float(len(t)), float(i)]} for i, t in enumerate(inputs)]
    return httpx.Response(
        200, json={"data": list(reversed(data)), "usage": {"total_tokens": 10 * len(inputs)}}
    )


def test_embed_documents_payload_and_order(mock_voyage, caplog):
    requests = mock_voyage(_embeddings_ok)
    with caplog.at_level(logging.INFO, logger="app.services.embeddings"):
        vectors = embed_documents(["a", "bb", "ccc"], feature="test_feature")

    assert vectors == [[1.0, 0.0], [2.0, 1.0], [3.0, 2.0]]
    assert len(requests) == 1
    req = requests[0]
    assert str(req.url) == "https://api.voyageai.com/v1/embeddings"
    assert req.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert json.loads(req.content) == {
        "input": ["a", "bb", "ccc"],
        "model": "voyage-4",
        "input_type": "document",
        "output_dimension": 1024,
    }
    line = next(r.getMessage() for r in caplog.records if "voyage call" in r.getMessage())
    assert "feature=test_feature" in line
    assert "total_tokens=30" in line
    assert "est_cost_usd=0.000002" in line
    assert FAKE_KEY not in caplog.text


def test_embed_query_uses_query_input_type(mock_voyage):
    requests = mock_voyage(_embeddings_ok)
    assert embed_query("hola") == [4.0, 0.0]
    assert json.loads(requests[0].content)["input_type"] == "query"


def test_embed_queries_batches_on_the_query_budget(mock_voyage, monkeypatch):
    """One request for many queries (#628), query input type, query timeout."""
    requests = mock_voyage(_embeddings_ok)
    seen = []
    real_post = embeddings._post

    def spy(url, payload, api_key, **kwargs):
        seen.append(kwargs)
        return real_post(url, payload, api_key, **kwargs)

    monkeypatch.setattr(embeddings, "_post", spy)
    assert embeddings.embed_queries(["a", "bb"]) == [[1.0, 0.0], [2.0, 1.0]]
    assert len(requests) == 1
    assert json.loads(requests[0].content)["input_type"] == "query"
    assert seen == [
        {
            "timeout": embeddings.QUERY_TIMEOUT_SECONDS,
            "max_attempts": embeddings.QUERY_MAX_ATTEMPTS,
        }
    ]
    assert embeddings.embed_queries([]) == []


def test_model_override_and_usage_totals(mock_voyage):
    """The eval's per-call model (#627); production callers get settings.embedding_model."""
    requests = mock_voyage(_embeddings_ok)
    embeddings.reset_usage_totals()
    embed_query("hola", model="voyage-4-lite")
    embed_documents(["a", "bb"], model="voyage-4-large")
    embed_query("hola")
    assert [json.loads(r.content)["model"] for r in requests] == [
        "voyage-4-lite",
        "voyage-4-large",
        "voyage-4",
    ]
    assert embeddings.get_usage_totals() == {
        ("embeddings", "voyage-4-lite"): 10,
        ("embeddings", "voyage-4-large"): 20,
        ("embeddings", "voyage-4"): 10,
    }
    embeddings.reset_usage_totals()
    assert embeddings.get_usage_totals() == {}


def test_batches_by_count(mock_voyage, monkeypatch):
    monkeypatch.setattr(embeddings, "MAX_INPUTS_PER_REQUEST", 2)
    requests = mock_voyage(_embeddings_ok)
    vectors = embed_documents(["a", "b", "c", "d", "e"])
    assert [len(json.loads(r.content)["input"]) for r in requests] == [2, 2, 1]
    assert len(vectors) == 5


def test_batches_by_estimated_tokens(mock_voyage, monkeypatch):
    # voyage-4 limit 320K * 0.8 = 256K estimated tokens; each text ~100K.
    requests = mock_voyage(_embeddings_ok)
    texts = ["x" * 300_000, "y" * 300_000, "z" * 300_000]
    vectors = embed_documents(texts)
    assert [len(json.loads(r.content)["input"]) for r in requests] == [2, 1]
    assert len(vectors) == 3


def test_oversized_single_text_still_sent_alone(mock_voyage):
    requests = mock_voyage(_embeddings_ok)
    embed_documents(["short", "x" * 2_000_000, "short"])
    assert [len(json.loads(r.content)["input"]) for r in requests] == [1, 1, 1]


def test_disabled_key_raises_before_any_request(mock_voyage, monkeypatch):
    monkeypatch.setattr(get_settings(), "voyage_api_key", "")
    requests = mock_voyage(_embeddings_ok)
    with pytest.raises(EmbeddingsDisabledError):
        embed_documents(["a"])
    with pytest.raises(EmbeddingsDisabledError):
        embed_query("a")
    with pytest.raises(EmbeddingsDisabledError):
        rerank("q", ["d"])
    assert requests == []


def test_empty_input_makes_no_request(mock_voyage):
    requests = mock_voyage(_embeddings_ok)
    assert embed_documents([]) == []
    assert rerank("q", []) == []
    assert requests == []


def test_retries_on_429_then_succeeds(mock_voyage):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"detail": "rate limited"})
        return _embeddings_ok(request)

    mock_voyage(handler)
    assert embed_documents(["a"]) == [[1.0, 0.0]]
    assert calls["n"] == 3


def test_retries_on_5xx_until_exhausted(mock_voyage):
    requests = mock_voyage(lambda r: httpx.Response(503, text="unavailable"))
    with pytest.raises(EmbeddingsError, match="HTTP 503"):
        embed_documents(["a"])
    assert len(requests) == embeddings.MAX_ATTEMPTS


def test_query_path_has_a_shorter_budget_than_the_document_path(mock_voyage):
    # #626: a reader-facing search must not sit through 4 x 60s.
    assert embeddings.QUERY_MAX_ATTEMPTS < embeddings.MAX_ATTEMPTS
    assert embeddings.QUERY_TIMEOUT_SECONDS < embeddings.TIMEOUT_SECONDS
    requests = mock_voyage(lambda r: httpx.Response(503, text="unavailable"))
    with pytest.raises(EmbeddingsError, match="HTTP 503"):
        embed_query("a")
    assert len(requests) == embeddings.QUERY_MAX_ATTEMPTS
    with pytest.raises(EmbeddingsError, match="HTTP 503"):
        rerank("q", ["d"])
    assert len(requests) == 2 * embeddings.QUERY_MAX_ATTEMPTS
    for req in requests:
        assert req.extensions["timeout"]["read"] == embeddings.QUERY_TIMEOUT_SECONDS


def test_document_path_keeps_the_long_timeout(mock_voyage):
    requests = mock_voyage(_embeddings_ok)
    embed_documents(["a"])
    assert requests[0].extensions["timeout"]["read"] == embeddings.TIMEOUT_SECONDS


def test_no_retry_on_4xx(mock_voyage):
    requests = mock_voyage(lambda r: httpx.Response(400, json={"detail": "bad input"}))
    with pytest.raises(EmbeddingsError, match="HTTP 400"):
        embed_documents(["a"])
    assert len(requests) == 1


def test_count_mismatch_raises(mock_voyage):
    mock_voyage(lambda r: httpx.Response(200, json={"data": [], "usage": {"total_tokens": 1}}))
    with pytest.raises(EmbeddingsError, match="0 embeddings for 1 inputs"):
        embed_query("a")


def test_rerank_payload_and_sorting(mock_voyage, caplog):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "relevance_score": 0.2},
                    {"index": 2, "relevance_score": 0.9},
                ],
                "usage": {"total_tokens": 1000},
            },
        )

    requests = mock_voyage(handler)
    with caplog.at_level(logging.INFO, logger="app.services.embeddings"):
        results = rerank("canal", ["a", "b", "c"], top_k=2, feature="rr")

    assert [(r.index, r.relevance_score) for r in results] == [(2, 0.9), (0, 0.2)]
    req = requests[0]
    assert str(req.url) == "https://api.voyageai.com/v1/rerank"
    assert json.loads(req.content) == {
        "query": "canal",
        "documents": ["a", "b", "c"],
        "model": "rerank-2.5",
        "top_k": 2,
    }
    assert "feature=rr" in caplog.text
    assert "est_cost_usd=0.000050" in caplog.text


def test_rerank_rejects_too_many_documents(mock_voyage):
    mock_voyage(_embeddings_ok)
    with pytest.raises(ValueError):
        rerank("q", ["d"] * (embeddings.MAX_RERANK_DOCUMENTS + 1))


def test_estimate_cost_central_table():
    assert embeddings.estimate_cost("voyage-4", 1_000_000) == pytest.approx(0.06)
    assert embeddings.estimate_cost("voyage-4-lite", 1_000_000) == pytest.approx(0.02)
    assert embeddings.estimate_cost("unknown-model", 1_000_000) == 0.0
