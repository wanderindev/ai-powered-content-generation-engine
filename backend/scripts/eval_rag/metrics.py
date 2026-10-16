"""Article-level retrieval metrics for the RAG eval (#627).

Pure functions over a ranked list of article ids (best first, duplicates
ignored after their first position) and the set of relevant ids. Relevance is
binary. When there is nothing relevant (an unanswerable question) every metric
is ``None``, and :func:`mean` skips ``None``, so unanswerable questions never
drag an average down; their score distribution is reported separately
(:func:`floor_candidates`).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def _dedupe(ranked: Iterable) -> list:
    seen: set = set()
    out = []
    for item in ranked:
        if item is None or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def recall_at_k(ranked: Sequence, relevant: Iterable, k: int) -> float | None:
    """Share of the relevant ids found in the top ``k``; ``None`` if none are relevant."""
    rel = set(relevant)
    if not rel:
        return None
    top = set(_dedupe(ranked)[:k])
    return len(top & rel) / len(rel)


def reciprocal_rank(ranked: Sequence, relevant: Iterable) -> float | None:
    """``1 / rank`` of the first relevant id (0.0 if absent); ``None`` if none are relevant."""
    rel = set(relevant)
    if not rel:
        return None
    for rank, item in enumerate(_dedupe(ranked), start=1):
        if item in rel:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence, relevant: Iterable, k: int = 10) -> float | None:
    """Binary-relevance nDCG@k: ``sum(1/log2(rank+1))`` over hits / the ideal ordering."""
    rel = set(relevant)
    if not rel:
        return None
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(_dedupe(ranked)[:k], start=1)
        if item in rel
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(rel), k) + 1))
    return dcg / ideal


def question_metrics(ranked: Sequence, relevant: Iterable) -> dict[str, float | None]:
    """The metric set the results table reports, for one question."""
    rel = list(relevant)
    return {
        "recall@1": recall_at_k(ranked, rel, 1),
        "recall@5": recall_at_k(ranked, rel, 5),
        "recall@10": recall_at_k(ranked, rel, 10),
        "mrr": reciprocal_rank(ranked, rel),
        "ndcg@10": ndcg_at_k(ranked, rel, 10),
    }


def mean(values: Iterable[float | None]) -> float | None:
    """Mean of the non-``None`` values, or ``None`` when there are none."""
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def percentile(values: Iterable[float], pct: float) -> float | None:
    """Nearest-rank percentile (the bench script's definition), ``None`` if empty."""
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    index = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[index]


def distribution(values: Iterable[float | None]) -> dict[str, float | int | None]:
    """n / min / p25 / median / p75 / max / mean of the non-``None`` values."""
    vals = [v for v in values if v is not None]
    return {
        "n": len(vals),
        "min": min(vals) if vals else None,
        "p25": percentile(vals, 25),
        "median": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "max": max(vals) if vals else None,
        "mean": mean(vals),
    }


def floor_candidates(
    answerable: Iterable[float | None], unanswerable: Iterable[float | None]
) -> dict[str, dict | None]:
    """Abstain-floor candidates for one confidence signal (higher = more confident).

    A question is *answered* when its signal is ``>= floor``. Returns three
    candidates, each with the floor and the share of answerable questions kept
    and of unanswerable questions abstained:

    * ``keep_95`` — the highest floor that still answers >= 95% of answerable
      questions (the 5th-percentile answerable score);
    * ``balanced`` — the observed value that maximises
      ``(kept + abstained) / 2``, lowest floor on a tie;
    * ``abstain_all`` — just above the highest unanswerable score, so every
      unanswerable question abstains.

    ``None`` for a candidate when either side has no scores.
    """
    pos = sorted(v for v in answerable if v is not None)
    neg = sorted(v for v in unanswerable if v is not None)
    if not pos or not neg:
        return {"keep_95": None, "balanced": None, "abstain_all": None}

    def rates(floor: float) -> dict:
        kept = sum(v >= floor for v in pos) / len(pos)
        abstained = sum(v < floor for v in neg) / len(neg)
        return {
            "floor": floor,
            "answerable_kept": kept,
            "unanswerable_abstained": abstained,
            "balanced_accuracy": (kept + abstained) / 2,
        }

    keep_index = math.floor(0.05 * len(pos))
    keep_95 = rates(pos[keep_index])
    best = None
    for floor in sorted(set(pos + neg)):
        candidate = rates(floor)
        if best is None or candidate["balanced_accuracy"] > best["balanced_accuracy"]:
            best = candidate
    top_neg = neg[-1]
    abstain_all = rates(math.nextafter(top_neg, math.inf))
    return {"keep_95": keep_95, "balanced": best, "abstain_all": abstain_all}


# --- Binary classification (pipeline-memory eval, #632) ------------------------------


def confusion(expected: Iterable[bool], predicted: Iterable[bool]) -> dict[str, int]:
    """TP / FP / FN / TN counts of two aligned boolean sequences."""
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for want, got in zip(expected, predicted, strict=True):
        if want and got:
            counts["tp"] += 1
        elif got:
            counts["fp"] += 1
        elif want:
            counts["fn"] += 1
        else:
            counts["tn"] += 1
    return counts


def precision_recall_f1(counts: dict[str, int]) -> dict[str, float | None]:
    """Precision, recall and F1 from :func:`confusion` counts.

    ``precision`` is ``None`` when nothing was predicted positive, ``recall``
    when nothing is positive; ``f1`` is ``None`` unless both exist (0.0 when
    both are 0).
    """
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def threshold_grid(start: float, stop: float, step: float) -> list[float]:
    """``start`` to ``stop`` inclusive in ``step`` increments, rounded to 4 places."""
    if step <= 0:
        raise ValueError("step must be > 0")
    out, i = [], 0
    while True:
        value = round(start + i * step, 4)
        if value > stop + 1e-9:
            return out
        out.append(value)
        i += 1


def threshold_sweep(
    scores: Sequence[float | None], expected: Sequence[bool], thresholds: Iterable[float]
) -> list[dict]:
    """Per threshold: predicted positive iff ``score >= threshold`` (``None`` never is).

    Each row has the threshold, the confusion counts and precision / recall / F1.
    """
    rows = []
    for threshold in thresholds:
        predicted = [s is not None and s >= threshold for s in scores]
        counts = confusion(expected, predicted)
        rows.append({"threshold": threshold, **counts, **precision_recall_f1(counts)})
    return rows


def select_threshold(rows: Sequence[dict], *, min_precision: float = 0.9) -> dict:
    """The selection rule: max F1 among rows with precision >= ``min_precision``.

    Ties go to the **higher** threshold (fewer flags; for a retrieval floor,
    fewer judged pairs). Returns ``{"row": <row or None>, "rule": str,
    "met_precision": bool}``. When no row meets the precision bar, ``row`` is
    the max-F1 row overall (same tie-break) and ``met_precision`` is False.
    """

    def best(candidates):
        scored = [r for r in candidates if r.get("f1") is not None]
        if not scored:
            return None
        return max(scored, key=lambda r: (r["f1"], r["threshold"]))

    rule = f"max F1 with precision >= {min_precision:g}, ties to the higher threshold"
    eligible = [
        r for r in rows if r.get("precision") is not None and r["precision"] >= min_precision
    ]
    row = best(eligible)
    if row is not None:
        return {"row": row, "rule": rule, "met_precision": True}
    return {"row": best(rows), "rule": rule, "met_precision": False}
