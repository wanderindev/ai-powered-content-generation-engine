"""RAG eval metric functions on hand-computed examples (#627). Pure; no DB."""

import math

import pytest
from scripts.eval_rag.metrics import (
    distribution,
    floor_candidates,
    mean,
    ndcg_at_k,
    percentile,
    question_metrics,
    recall_at_k,
    reciprocal_rank,
)


def test_recall_at_k_single_relevant():
    ranked = [5, 3, 9, 1]
    assert recall_at_k(ranked, [3], 1) == 0.0
    assert recall_at_k(ranked, [3], 2) == 1.0
    assert recall_at_k(ranked, [3], 10) == 1.0


def test_recall_at_k_multiple_relevant():
    ranked = [5, 3, 9, 1, 7]
    assert recall_at_k(ranked, [3, 7], 1) == 0.0
    assert recall_at_k(ranked, [3, 7], 2) == 0.5
    assert recall_at_k(ranked, [3, 7], 5) == 1.0


def test_no_relevant_result_scores_zero():
    ranked = [1, 2, 3]
    assert recall_at_k(ranked, [42], 10) == 0.0
    assert reciprocal_rank(ranked, [42]) == 0.0
    assert ndcg_at_k(ranked, [42]) == 0.0
    assert question_metrics([], [42]) == {
        "recall@1": 0.0,
        "recall@5": 0.0,
        "recall@10": 0.0,
        "mrr": 0.0,
        "ndcg@10": 0.0,
    }


def test_empty_relevant_set_is_none_everywhere():
    """Unanswerable questions have no metric; they must not drag averages to 0."""
    assert set(question_metrics([1, 2], []).values()) == {None}


def test_reciprocal_rank_first_hit():
    assert reciprocal_rank([4, 8, 2], [2, 8]) == pytest.approx(1 / 2)
    assert reciprocal_rank([2], [2]) == 1.0


def test_duplicates_and_none_are_ignored():
    # The same article twice (e.g. two KB groups) counts once; None (KB claim) is skipped.
    ranked = [None, 4, 4, 2]
    assert reciprocal_rank(ranked, [2]) == pytest.approx(1 / 2)
    assert recall_at_k(ranked, [2], 2) == 1.0


def test_ndcg_single_relevant_at_rank_3():
    # DCG = 1/log2(4) = 0.5; IDCG = 1.
    assert ndcg_at_k([9, 8, 7], [7]) == pytest.approx(0.5)


def test_ndcg_multiple_relevant():
    # Relevant at ranks 1 and 3: DCG = 1 + 1/log2(4) = 1.5;
    # IDCG (two relevant) = 1 + 1/log2(3).
    expected = 1.5 / (1 + 1 / math.log2(3))
    assert ndcg_at_k([1, 5, 2, 6], [1, 2]) == pytest.approx(expected)
    assert ndcg_at_k([1, 2, 5], [1, 2]) == pytest.approx(1.0)


def test_ndcg_respects_k():
    assert ndcg_at_k([1, 2, 3], [3], k=2) == 0.0


def test_mean_skips_none():
    assert mean([1.0, None, 0.0]) == 0.5
    assert mean([None]) is None


def test_percentile_nearest_rank():
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 100
    assert percentile([], 50) is None


def test_distribution():
    d = distribution([0.2, None, 0.4, 0.6])
    assert d["n"] == 3
    assert d["min"] == 0.2 and d["max"] == 0.6 and d["median"] == 0.4
    assert d["mean"] == pytest.approx(0.4)


def test_floor_candidates_separable():
    answerable = [0.8, 0.7, 0.9, 0.75]
    unanswerable = [0.3, 0.5, 0.4]
    floors = floor_candidates(answerable, unanswerable)
    assert floors["balanced"]["floor"] == 0.7
    assert floors["balanced"]["answerable_kept"] == 1.0
    assert floors["balanced"]["unanswerable_abstained"] == 1.0
    assert floors["keep_95"]["floor"] == 0.7  # the lowest answerable score
    assert floors["abstain_all"]["floor"] > 0.5
    assert floors["abstain_all"]["unanswerable_abstained"] == 1.0


def test_floor_candidates_overlapping():
    answerable = [0.2, 0.6, 0.7, 0.8]
    unanswerable = [0.65, 0.1]
    floors = floor_candidates(answerable, unanswerable)
    # abstain_all must sit above 0.65: it loses the 0.2 and 0.6 answerable ones.
    assert floors["abstain_all"]["answerable_kept"] == 0.5
    assert floors["abstain_all"]["unanswerable_abstained"] == 1.0
    best = floors["balanced"]
    assert best["balanced_accuracy"] >= floors["abstain_all"]["balanced_accuracy"]


def test_floor_candidates_need_both_sides():
    assert floor_candidates([0.5], []) == {"keep_95": None, "balanced": None, "abstain_all": None}
    assert floor_candidates([None], [0.1])["balanced"] is None
