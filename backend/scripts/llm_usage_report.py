"""Anthropic spend + cache-hit report for one API key, from the Usage Admin API.

Pulls ``GET /v1/organizations/usage_report/messages`` (daily buckets, grouped
by API key and model) and prints, per model: uncached input, cache reads,
cache writes (5m / 1h), output, the cache-read share of input, and a list-price
USD estimate. Read-only — no model calls, no DB access, no writes.

The Admin key is read from the ``ANTHROPIC_ADMIN_KEY`` environment variable
only (never a flag, never a default). The API cannot attribute spend to our
internal ``feature=`` labels; per-feature attribution comes from the
``anthropic call: feature=…`` log lines ``create_message`` writes (#716).

Usage (from ``backend/``)::

    ANTHROPIC_ADMIN_KEY=… python -m scripts.llm_usage_report --days 30 \\
        --api-key-id <the "Panama In Context" key's apikey_… id> [--org-wide] [--daily]

``--org-wide`` additionally prints every key's cache-read share for context.
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx
from app.services.llm import PRICE_PER_MILLION_TOKENS

USAGE_URL = "https://api.anthropic.com/v1/organizations/usage_report/messages"

# Older models still visible in a 30-day window. USD per 1M tokens:
# (input, output, cache read, 5m cache write). 1h writes bill at 2x input.
_LEGACY_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25),
    "claude-sonnet-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30, 3.75),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),
}

_FIELDS = ("uncached", "cache_read", "write_5m", "write_1h", "output")


def _price(model: str) -> tuple[float, float, float, float] | None:
    bare = re.sub(r"-\d{8}$", "", model)  # usage rows carry dated snapshot ids
    return PRICE_PER_MILLION_TOKENS.get(bare) or _LEGACY_PRICES.get(bare)


def fetch_buckets(admin_key: str, days: int, api_key_id: str | None) -> list[dict]:
    """Return every daily bucket for the window, following pagination."""
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start = end - timedelta(days=days)
    params: list[tuple[str, str]] = [
        ("starting_at", start.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("ending_at", end.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("bucket_width", "1d"),
        ("limit", "31"),
        ("group_by[]", "api_key_id"),
        ("group_by[]", "model"),
    ]
    if api_key_id:
        params.append(("api_key_ids[]", api_key_id))
    headers = {"x-api-key": admin_key, "anthropic-version": "2023-06-01"}
    buckets: list[dict] = []
    page: str | None = None
    with httpx.Client(timeout=60) as client:
        while True:
            query = params + ([("page", page)] if page else [])
            resp = client.get(USAGE_URL, params=query, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            buckets.extend(body.get("data", []))
            if not body.get("has_more"):
                return buckets
            page = body.get("next_page")


def _row(result: dict) -> dict[str, int]:
    creation = result.get("cache_creation") or {}
    return {
        "uncached": int(result.get("uncached_input_tokens") or 0),
        "cache_read": int(result.get("cache_read_input_tokens") or 0),
        "write_5m": int(creation.get("ephemeral_5m_input_tokens") or 0),
        "write_1h": int(creation.get("ephemeral_1h_input_tokens") or 0),
        "output": int(result.get("output_tokens") or 0),
    }


def usd(model: str, t: dict[str, int]) -> float | None:
    """List-price USD for one model's totals; None when the model is unpriced."""
    price = _price(model)
    if price is None:
        return None
    p_in, p_out, p_read, p_w5 = price
    return (
        t["uncached"] * p_in
        + t["output"] * p_out
        + t["cache_read"] * p_read
        + t["write_5m"] * p_w5
        + t["write_1h"] * p_in * 2
    ) / 1_000_000


def read_share(t: dict[str, int]) -> float:
    total_in = t["uncached"] + t["cache_read"] + t["write_5m"] + t["write_1h"]
    return t["cache_read"] / total_in if total_in else 0.0


def _print_table(title: str, totals: dict[str, dict[str, int]]) -> None:
    print(f"\n## {title}")
    print(
        "| model | uncached in | cache read | cache write 5m | cache write 1h | output | read share | est. USD |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    grand = dict.fromkeys(_FIELDS, 0)
    grand_usd = 0.0
    for model, t in sorted(totals.items()):
        cost = usd(model, t)
        grand_usd += cost or 0.0
        for f in _FIELDS:
            grand[f] += t[f]
        cost_s = f"${cost:,.2f}" if cost is not None else "unpriced"
        print(
            f"| {model} | {t['uncached']:,} | {t['cache_read']:,} | {t['write_5m']:,} | "
            f"{t['write_1h']:,} | {t['output']:,} | {read_share(t):.1%} | {cost_s} |"
        )
    print(
        f"| **total** | {grand['uncached']:,} | {grand['cache_read']:,} | {grand['write_5m']:,} | "
        f"{grand['write_1h']:,} | {grand['output']:,} | {read_share(grand):.1%} | ${grand_usd:,.2f} |"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--api-key-id", help="Restrict to one API key (apikey_…)")
    parser.add_argument("--org-wide", action="store_true", help="Also print per-key read share")
    parser.add_argument("--daily", action="store_true", help="Also print per-day totals")
    args = parser.parse_args()

    admin_key = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
    if not admin_key:
        print("ANTHROPIC_ADMIN_KEY is not set", file=sys.stderr)
        return 2

    buckets = fetch_buckets(admin_key, args.days, None if args.org_wide else args.api_key_id)
    by_model: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(_FIELDS, 0))
    by_key: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(_FIELDS, 0))
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(_FIELDS, 0))
    for bucket in buckets:
        day = (bucket.get("starting_at") or "")[:10]
        for result in bucket.get("results", []):
            row = _row(result)
            key = result.get("api_key_id") or "(console/none)"
            for f in _FIELDS:
                by_key[key][f] += row[f]
            if args.api_key_id and key != args.api_key_id:
                continue
            model = result.get("model") or "?"
            for f in _FIELDS:
                by_model[model][f] += row[f]
                by_day[day][f] += row[f]

    scope = args.api_key_id or "whole org"
    _print_table(f"Last {args.days} days — {scope}, by model", by_model)
    if args.daily:
        print("\n## Per day (all models)")
        print("| day | uncached in | cache read | cache write | output | read share |")
        print("|---|---:|---:|---:|---:|---:|")
        for day, t in sorted(by_day.items()):
            print(
                f"| {day} | {t['uncached']:,} | {t['cache_read']:,} | "
                f"{t['write_5m'] + t['write_1h']:,} | {t['output']:,} | {read_share(t):.1%} |"
            )
    if args.org_wide:
        print("\n## Org-wide cache-read share by API key (context only)")
        print("| api key | input (all kinds) | read share |")
        print("|---|---:|---:|")
        for key, t in sorted(by_key.items(), key=lambda kv: -sum(kv[1].values())):
            total_in = t["uncached"] + t["cache_read"] + t["write_5m"] + t["write_1h"]
            print(f"| {key} | {total_in:,} | {read_share(t):.1%} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
