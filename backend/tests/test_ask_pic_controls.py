"""Ask PIC abuse and cost controls (#634, epic #619).

Covers ``app.services.ask_pic_controls``: the kill switch (as a dependency on
a test-only route, since the real route is #635's), client-IP extraction and
the daily-rotating hash, the per-IP / global limits, the worst-case budget
reservation, question storage, the Sentry guard, and the prune script.
"""

import ast
import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from app.core.config import get_settings
from app.models.ask_pic_query import AskPicQuery
from app.models.enums import AskPicQueryStatus
from app.services import ask_pic_controls as controls
from app.services.llm import (
    HAIKU_MODEL,
    SONNET_MODEL,
    LLMUsage,
    estimate_cost_usd,
    worst_case_cost_usd,
)
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from scripts.prune_ask_pic_queries import prune_ask_pic_queries

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ask_pic_enabled", True)
    monkeypatch.setattr(s, "ask_pic_ip_hash_secret", "test-secret")
    monkeypatch.setattr(s, "ask_pic_per_ip_per_hour", 3)
    monkeypatch.setattr(s, "ask_pic_global_per_day", 5)
    monkeypatch.setattr(s, "ask_pic_daily_budget_usd", 1.0)
    monkeypatch.setattr(s, "ask_pic_store_questions", False)
    monkeypatch.setattr(s, "ask_pic_max_question_chars", 500)
    return s


def _admit(db, ip="ip-a", now=NOW, question="When was Panamá Viejo founded?", **kw):
    return controls.admit_query(db, ip_hash=ip, language="en", question=question, now=now, **kw)


# --- Kill switch --------------------------------------------------------------


@pytest.fixture
def guarded_client():
    app = FastAPI()

    @app.get("/ask-test", dependencies=[Depends(controls.require_ask_pic_enabled)])
    def ask_test(request: Request):
        return {"ip": controls.client_ip(request)}

    return TestClient(app)


def test_disabled_answers_503(settings, monkeypatch, guarded_client, test_db):
    monkeypatch.setattr(settings, "ask_pic_enabled", False)
    assert guarded_client.get("/ask-test").status_code == 503
    assert test_db.query(AskPicQuery).count() == 0


def test_enabled_without_secret_answers_503(settings, monkeypatch, guarded_client):
    monkeypatch.setattr(settings, "ask_pic_ip_hash_secret", "")
    assert guarded_client.get("/ask-test").status_code == 503


def test_enabled_with_secret_passes(settings, guarded_client):
    assert guarded_client.get("/ask-test").status_code == 200


def test_defaults_are_off():
    fields = type(get_settings()).model_fields
    assert fields["ask_pic_enabled"].default is False
    assert fields["ask_pic_store_questions"].default is False
    assert fields["ask_pic_ip_hash_secret"].default == ""


# --- Client IP and hash -------------------------------------------------------


def test_client_ip_prefers_x_real_ip(settings, guarded_client):
    resp = guarded_client.get("/ask-test", headers={"X-Real-IP": " 203.0.113.7 "})
    assert resp.json() == {"ip": "203.0.113.7"}


def test_client_ip_falls_back_to_socket_peer(settings, guarded_client):
    assert guarded_client.get("/ask-test").json() == {"ip": "testclient"}


def test_hash_is_not_stable_across_days():
    day1 = controls.hash_ip("203.0.113.7", secret="s", day=date(2026, 10, 10))
    again = controls.hash_ip("203.0.113.7", secret="s", day=date(2026, 10, 10))
    day2 = controls.hash_ip("203.0.113.7", secret="s", day=date(2026, 10, 11))
    other_secret = controls.hash_ip("203.0.113.7", secret="t", day=date(2026, 10, 10))
    assert day1 == again
    assert day1 != day2
    assert day1 != other_secret
    assert len(day1) == 64 and "203.0.113.7" not in day1


def test_hash_client_ip_uses_utc_date(settings):
    request = Request({"type": "http", "headers": [(b"x-real-ip", b"198.51.100.1")]})
    before = controls.hash_client_ip(request, now=datetime(2026, 10, 10, 23, 59, tzinfo=UTC))
    after = controls.hash_client_ip(request, now=datetime(2026, 10, 11, 0, 1, tzinfo=UTC))
    assert before != after


# --- Rate limits ----------------------------------------------------------------


def test_per_ip_limit(settings, test_db):
    for _ in range(3):
        assert _admit(test_db).status is None
    blocked = _admit(test_db)
    assert blocked.status == AskPicQueryStatus.RATE_LIMITED.value
    # Another IP is unaffected.
    assert _admit(test_db, ip="ip-b").status is None


def test_per_ip_limit_is_a_rolling_hour(settings, test_db):
    for _ in range(3):
        _admit(test_db, now=NOW - timedelta(minutes=61))
    assert _admit(test_db).status is None


def test_refused_requests_do_not_extend_the_lockout(settings, test_db):
    for _ in range(3):
        _admit(test_db, now=NOW - timedelta(minutes=50))
    for _ in range(5):
        assert _admit(test_db, now=NOW - timedelta(minutes=40)).status == "RATE_LIMITED"
    # The three admitted rows age out at +60 min; the RATE_LIMITED ones don't count.
    assert _admit(test_db, now=NOW + timedelta(minutes=11)).status is None


def test_invalid_requests_count_toward_the_limit(settings, test_db):
    for _ in range(3):
        _admit(test_db, status=AskPicQueryStatus.INVALID)
    assert _admit(test_db).status == "RATE_LIMITED"


def test_global_daily_limit(settings, test_db):
    for i in range(5):
        assert _admit(test_db, ip=f"ip-{i}").status is None
    assert _admit(test_db, ip="ip-new").status == "RATE_LIMITED"
    # A new UTC day resets the global count.
    tomorrow = NOW.replace(hour=0) + timedelta(days=1)
    assert _admit(test_db, ip="ip-new", now=tomorrow).status is None


# --- Budget -------------------------------------------------------------------


def test_worst_case_cost_prices_cache_write_and_full_output():
    # Sonnet: cache write 2.50 > input 2.00; output 10.00 per 1M.
    assert worst_case_cost_usd(SONNET_MODEL, input_tokens=1_000_000, max_tokens=0) == 2.5
    assert worst_case_cost_usd(SONNET_MODEL, input_tokens=0, max_tokens=100_000) == 1.0
    with pytest.raises(ValueError):
        worst_case_cost_usd("unpriced-model", input_tokens=1, max_tokens=1)


def test_reserve_then_finish_replaces_reservation(settings, test_db):
    q = _admit(test_db)
    assert controls.reserve_llm_call(
        test_db, q, model=HAIKU_MODEL, input_tokens=10_000, max_tokens=4000, now=NOW
    )
    reserved = worst_case_cost_usd(HAIKU_MODEL, input_tokens=10_000, max_tokens=4000)
    assert float(q.cost_usd) == pytest.approx(reserved)

    usage = LLMUsage(input_tokens=2000, output_tokens=300, cache_read_input_tokens=1000)
    controls.finish_query(
        test_db,
        q,
        status=AskPicQueryStatus.ANSWERED,
        usage=usage,
        embed_tokens=20,
        retrieval_ms=120,
        llm_ms=900,
        cited_article_ids=[3, 7],
    )
    test_db.refresh(q)
    expected = (2000 * 0.10 + 300 * 0.50 + 1000 * 0.01 + 20 * 0.06) / 1_000_000
    assert float(q.cost_usd) == pytest.approx(expected, abs=1e-6)
    assert q.status == "ANSWERED"
    assert q.model == HAIKU_MODEL
    assert (q.input_tokens, q.output_tokens, q.cache_read_input_tokens) == (2000, 300, 1000)
    assert q.embed_tokens == 20 and q.cited_article_ids == [3, 7]
    assert (q.retrieval_ms, q.llm_ms) == (120, 900)


def test_budget_exhaustion_blocks_before_the_llm_call(settings, test_db):
    spent = _admit(test_db, ip="ip-spent")
    controls.finish_query(test_db, spent, status=AskPicQueryStatus.ANSWERED, extra_cost_usd=0.95)

    q = _admit(test_db)
    assert q.status is None  # 0.95 < 1.00: admitted
    # Worst case of this call is 0.10 → 1.05 > 1.00: refused, nothing reserved.
    ok = controls.reserve_llm_call(
        test_db, q, model=SONNET_MODEL, input_tokens=0, max_tokens=10_000, now=NOW
    )
    assert ok is False
    test_db.refresh(q)
    assert q.status == "BUDGET_EXCEEDED"
    assert q.cost_usd == Decimal(0)


def test_budget_refusal_after_retrieval_still_counts_toward_rate_limit(settings, test_db):
    """Regression (#634 review): just under the cap, admit passes but reserve
    refuses. Those rows paid for retrieval, so they must count and cost."""
    spent = _admit(test_db, ip="ip-spent")
    controls.finish_query(test_db, spent, status=AskPicQueryStatus.ANSWERED, extra_cost_usd=0.98)
    for _ in range(3):
        q = _admit(test_db)
        assert q.status is None
        assert not controls.reserve_llm_call(
            test_db,
            q,
            model=SONNET_MODEL,
            input_tokens=0,
            max_tokens=10_000,
            embed_tokens=50,
            extra_cost_usd=0.0001,
            now=NOW,
        )
        test_db.refresh(q)
        assert q.status == "BUDGET_EXCEEDED" and q.counts_toward_limits is True
        assert q.embed_tokens == 50
        assert float(q.cost_usd) == pytest.approx(0.0001 + 50 * 0.06 / 1_000_000, abs=1e-6)
    assert _admit(test_db).status == "RATE_LIMITED"


def test_min_cost_refuses_admission_before_retrieval(settings, test_db):
    spent = _admit(test_db, ip="ip-spent")
    controls.finish_query(test_db, spent, status=AskPicQueryStatus.ANSWERED, extra_cost_usd=0.98)
    q = _admit(test_db, min_cost_usd=0.05)
    assert q.status == "BUDGET_EXCEEDED" and q.counts_toward_limits is False
    assert _admit(test_db, min_cost_usd=0.01).status is None


def test_invalid_past_the_limit_is_rate_limited(settings, test_db):
    for _ in range(3):
        _admit(test_db, status=AskPicQueryStatus.INVALID)
    q = _admit(test_db, status=AskPicQueryStatus.INVALID)
    assert q.status == "RATE_LIMITED" and q.counts_toward_limits is False


def test_haiku_long_prompt_tier():
    short = worst_case_cost_usd(HAIKU_MODEL, input_tokens=100_000, max_tokens=0)
    long = worst_case_cost_usd(HAIKU_MODEL, input_tokens=100_001, max_tokens=0)
    assert short == pytest.approx(100_000 * 0.125 / 1_000_000)
    assert long == pytest.approx(100_001 * 0.625 / 1_000_000)
    usage = LLMUsage(input_tokens=60_000, cache_read_input_tokens=60_000, output_tokens=1000)
    assert estimate_cost_usd(HAIKU_MODEL, usage) == pytest.approx(
        (60_000 * 0.50 + 60_000 * 0.05 + 1000 * 2.50) / 1_000_000
    )


def test_reservation_counts_against_concurrent_queries(settings, test_db):
    first = _admit(test_db, ip="ip-1")
    second = _admit(test_db, ip="ip-2")
    # First reserves 0.60 worst case and is still in flight.
    assert controls.reserve_llm_call(
        test_db, first, model=SONNET_MODEL, input_tokens=0, max_tokens=60_000, now=NOW
    )
    # Second's 0.60 would exceed the 1.00 cap with first's reservation.
    assert not controls.reserve_llm_call(
        test_db, second, model=SONNET_MODEL, input_tokens=0, max_tokens=60_000, now=NOW
    )


def test_spent_budget_refuses_admission(settings, test_db):
    spent = _admit(test_db, ip="ip-spent")
    controls.finish_query(test_db, spent, status=AskPicQueryStatus.ANSWERED, extra_cost_usd=1.0)
    assert _admit(test_db).status == "BUDGET_EXCEEDED"
    # Budget is per UTC day.
    assert _admit(test_db, now=NOW + timedelta(days=1)).status is None


def test_error_without_usage_keeps_the_reservation(settings, test_db):
    q = _admit(test_db)
    controls.reserve_llm_call(test_db, q, model=SONNET_MODEL, input_tokens=0, max_tokens=10_000)
    controls.finish_query(test_db, q, status=AskPicQueryStatus.ERROR)
    test_db.refresh(q)
    assert float(q.cost_usd) == pytest.approx(0.10)


# --- Question storage -----------------------------------------------------------


def test_no_question_stored_when_setting_off(settings, test_db):
    q = _admit(test_db, question="secret question text")
    test_db.refresh(q)
    assert q.question is None
    assert q.ip_hash == "ip-a" and q.language == "en"


def test_question_stored_and_capped_when_setting_on(settings, monkeypatch, test_db):
    monkeypatch.setattr(settings, "ask_pic_store_questions", True)
    monkeypatch.setattr(settings, "ask_pic_max_question_chars", 10)
    q = _admit(test_db, question="0123456789abcdef")
    test_db.refresh(q)
    assert q.question == "0123456789"


def test_unsupported_language_is_stored_as_null(settings, test_db):
    q = controls.admit_query(
        test_db, ip_hash="x", language="fr", question=None, status=AskPicQueryStatus.INVALID
    )
    assert q.language is None and q.status == "INVALID"


# --- Sentry guard ----------------------------------------------------------------

_SENTRY_CONTEXT_CALLS = {"add_breadcrumb", "set_context", "set_extra", "set_tag", "set_user"}


def test_module_never_hands_user_text_to_sentry():
    """No Sentry breadcrumb / context call, so no ``data=`` with user text."""
    tree = ast.parse(inspect.getsource(controls))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert name not in _SENTRY_CONTEXT_CALLS, f"Sentry context call {name!r}"
            if name in {"capture_message", "capture_exception"}:
                assert not any(kw.arg == "data" for kw in node.keywords)
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names]
            module = getattr(node, "module", "") or ""
            assert "sentry_sdk" not in module and "sentry_sdk" not in names


def test_question_text_never_reaches_sentry_or_logs(settings, monkeypatch, test_db, caplog):
    monkeypatch.setattr(settings, "ask_pic_store_questions", True)
    secret = "UNIQUE-QUESTION-TEXT-634"
    with (
        patch("sentry_sdk.add_breadcrumb") as crumb,
        patch("sentry_sdk.set_context") as ctx,
        caplog.at_level("INFO", logger="app"),
    ):
        q = _admit(test_db, question=secret)
        controls.reserve_llm_call(test_db, q, model=HAIKU_MODEL, input_tokens=10, max_tokens=10)
        controls.finish_query(test_db, q, status=AskPicQueryStatus.ANSWERED, usage=LLMUsage(1, 1))
        for _ in range(4):
            _admit(test_db, question=secret)  # hits RATE_LIMITED
    assert crumb.call_count == 0 and ctx.call_count == 0
    assert secret not in caplog.text


# --- Prune script -------------------------------------------------------------


def _row(db, created_at):
    db.add(AskPicQuery(ip_hash="h", language="es", created_at=created_at, status="ANSWERED"))
    db.commit()


def test_prune_deletes_rows_past_retention(settings, monkeypatch, test_db):
    monkeypatch.setattr(settings, "ask_pic_retention_days", 30)
    now = datetime.now(UTC)
    _row(test_db, now - timedelta(days=31))
    _row(test_db, now - timedelta(days=1))
    assert prune_ask_pic_queries(test_db) == 1
    assert test_db.query(AskPicQuery).count() == 1


def test_prune_dry_run_deletes_nothing(settings, test_db):
    _row(test_db, datetime.now(UTC) - timedelta(days=45))
    assert prune_ask_pic_queries(test_db, retention_days=30, dry_run=True) == 1
    assert test_db.query(AskPicQuery).count() == 1


def test_prune_custom_window(settings, test_db):
    _row(test_db, datetime.now(UTC) - timedelta(days=8))
    assert prune_ask_pic_queries(test_db, retention_days=7) == 1
