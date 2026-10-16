"""Unit tests for scripts.llm_usage_report (#716) — pure aggregation, no network."""

import pytest
from scripts import llm_usage_report as report


def test_row_reads_every_token_kind():
    row = report._row(
        {
            "uncached_input_tokens": 100,
            "cache_read_input_tokens": 300,
            "cache_creation": {"ephemeral_5m_input_tokens": 50, "ephemeral_1h_input_tokens": 10},
            "output_tokens": 7,
        }
    )
    assert row == {"uncached": 100, "cache_read": 300, "write_5m": 50, "write_1h": 10, "output": 7}


def test_row_tolerates_missing_cache_fields():
    assert report._row({"uncached_input_tokens": 5}) == {
        "uncached": 5,
        "cache_read": 0,
        "write_5m": 0,
        "write_1h": 0,
        "output": 0,
    }


def test_usd_prices_cache_kinds_and_dated_ids():
    t = {
        "uncached": 1_000_000,
        "cache_read": 1_000_000,
        "write_5m": 1_000_000,
        "write_1h": 1_000_000,
        "output": 1_000_000,
    }
    # Sonnet 5.5: 2 in + 10 out + 0.20 read + 2.50 5m write + 4.00 (2x in) 1h write.
    assert report.usd("claude-sonnet-5-5", t) == pytest.approx(18.70)
    # Usage rows carry dated snapshot ids for older models.
    assert report.usd("claude-haiku-4-5-20251001", t) == pytest.approx(1 + 5 + 0.1 + 1.25 + 2)
    assert report.usd("claude-unknown", t) is None


def test_read_share():
    t = {"uncached": 60, "cache_read": 30, "write_5m": 10, "write_1h": 0, "output": 99}
    assert report.read_share(t) == pytest.approx(0.30)
    assert report.read_share(dict.fromkeys(t, 0)) == 0.0


def test_main_refuses_without_admin_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_ADMIN_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["llm_usage_report"])
    assert report.main() == 2
    assert "ANTHROPIC_ADMIN_KEY" in capsys.readouterr().err
