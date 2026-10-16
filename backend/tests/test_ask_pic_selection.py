"""Ask PIC passage selection (#695): language dedup, per-article cap, claim chunks.

Pure unit tests over a hand-built reranked chunk list: ``ask_pic._passages``
needs no database, no Voyage and no model.
"""

from types import SimpleNamespace

import pytest
from app.services import ask_pic
from app.services.knowledge import retrieval
from app.services.knowledge.chunker import TWIN_KEY
from scripts.eval_rag import cli as eval_cli
from scripts.eval_rag import selection as selection_eval

ARTICLE = "ARTICLE"
CLAIM = "ARTICLE_CLAIM"
KB = "VERIFIED_CLAIM"


_DEFAULT = object()


def _chunk(cid, article, lang="en", index=0, source_type=ARTICLE, key=_DEFAULT):
    """A hit; an ARTICLE chunk's twin key defaults to one that follows its index."""
    if key is _DEFAULT:
        key = f"shape:0:{index}-{index}" if source_type == ARTICLE else None
    return retrieval.ChunkHit(
        chunk_id=cid,
        source_type=source_type,
        source_id=cid,
        article_id=article,
        language=lang,
        chunk_index=index,
        heading_path=None,
        content=f"chunk {cid}",
        meta={TWIN_KEY: key} if key else None,
    )


def _result(*chunks):
    return retrieval.SearchResult(
        query="q", mode="hybrid", visibility="public", reranked=True, chunks=list(chunks)
    )


def _sel(n=8, cap=None, dedup=False, skip=False, replace=False, by_index=False):
    return ask_pic.PassageSelection(
        max_passages=n,
        per_article_cap=cap,
        language_dedup=dedup,
        skip_covered_claims=skip,
        paragraph_replaces_claims=replace,
        twin_by_index=by_index,
    )


def _ids(passages):
    return [p.chunk_id for p in passages]


def test_no_rule_keeps_rank_order_and_drops_kb_chunks():
    result = _result(
        _chunk(1, 10),
        _chunk(2, None, source_type=KB),
        _chunk(3, 10, "es"),
        _chunk(4, 11, source_type=CLAIM),
        _chunk(5, 12),
    )
    passages = ask_pic._passages(result, "en", _sel(n=3))
    assert _ids(passages) == [1, 3, 4]
    assert [p.passage_id for p in passages] == ["P1", "P2", "P3"]


def test_language_dedup_keeps_the_question_language_in_the_first_slot():
    # ES twin ranks first, EN twin of the same paragraph third: the EN one
    # takes the ES one's slot, and the freed slot goes to the next chunk.
    result = _result(
        _chunk(1, 10, "es", index=2),
        _chunk(2, 11, "en", index=0),
        _chunk(3, 10, "en", index=2),
        _chunk(4, 12, "en", index=0),
    )
    passages = ask_pic._passages(result, "en", _sel(n=3, dedup=True))
    assert _ids(passages) == [3, 2, 4]
    # A Spanish question keeps the Spanish twin.
    assert _ids(ask_pic._passages(result, "es", _sel(n=3, dedup=True))) == [1, 2, 4]
    # Off: both twins take a slot.
    assert _ids(ask_pic._passages(result, "en", _sel(n=3))) == [1, 2, 3]


def test_language_dedup_only_pairs_same_article_same_position():
    result = _result(
        _chunk(1, 10, "en", index=0),
        _chunk(2, 10, "es", index=1),  # other position: not a twin
        _chunk(3, 11, "es", index=0),  # other article: not a twin
        # Claims are all chunk_index 0: never a twin of paragraph 0.
        _chunk(4, 10, "en", index=0, source_type=CLAIM),
    )
    passages = ask_pic._passages(result, "en", _sel(dedup=True))
    assert _ids(passages) == [1, 2, 3, 4]


def test_twins_pair_by_twin_key_not_chunk_index():
    """#713: EN and ES pack differently, so the same paragraphs can sit at
    different chunk indexes and the same index can hold different text."""
    result = _result(
        _chunk(1, 10, "es", index=4, key="s:2:3-5"),
        _chunk(2, 10, "en", index=4, key="s:2:6-8"),  # same index, other paragraphs
        _chunk(3, 10, "en", index=3, key="s:2:3-5"),  # the real twin of 1
        _chunk(4, 11, "en", index=0),
    )
    passages = ask_pic._passages(result, "en", _sel(dedup=True))
    assert _ids(passages) == [3, 2, 4]
    # The pre-#713 rule pairs 1 with 2 (same index), so 2 replaces 1 and 3,
    # the real twin of 1, takes a slot of its own.
    by_index = ask_pic._passages(result, "en", _sel(dedup=True, by_index=True))
    assert _ids(by_index) == [2, 3, 4]


def test_chunks_without_a_twin_key_are_never_deduplicated():
    # Indexed before #713: no key, so no dedup (never a guessed pair).
    result = _result(
        _chunk(1, 10, "es", index=0, key=None),
        _chunk(2, 10, "en", index=0, key=None),
        _chunk(3, 10, "en", index=1, key="s:0:1-1"),
    )
    assert _ids(ask_pic._passages(result, "en", _sel(dedup=True))) == [1, 2, 3]


def test_passages_carry_the_twin_key():
    (passage,) = ask_pic._passages(_result(_chunk(1, 10, key="s:0:0-1")), "en")
    assert passage.twin_key == "s:0:0-1"


def test_per_article_cap():
    result = _result(
        _chunk(1, 10, index=0),
        _chunk(2, 10, index=1),
        _chunk(3, 10, index=2),
        _chunk(4, 10, source_type=CLAIM),
        _chunk(5, 11, index=0),
        _chunk(6, 12, index=0),
    )
    assert _ids(ask_pic._passages(result, "en", _sel(n=4, cap=2))) == [1, 2, 5, 6]
    assert _ids(ask_pic._passages(result, "en", _sel(n=4))) == [1, 2, 3, 4]


def test_a_twin_swap_does_not_count_against_the_cap():
    result = _result(
        _chunk(1, 10, "es", index=0),
        _chunk(2, 10, "en", index=1),
        _chunk(3, 10, "en", index=0),  # twin of 1: swaps in, no new slot
        _chunk(4, 11, "en", index=0),
    )
    passages = ask_pic._passages(result, "en", _sel(cap=2, dedup=True))
    assert _ids(passages) == [3, 2, 4]


def test_claim_is_skipped_once_a_paragraph_of_its_article_is_selected():
    result = _result(
        _chunk(1, 10, source_type=CLAIM),  # before any paragraph of 10: kept
        _chunk(2, 10, index=3),
        _chunk(3, 10, source_type=CLAIM),  # paragraph of 10 already in: skipped
        _chunk(4, 11, source_type=CLAIM),  # other article: kept
        _chunk(5, 12, index=0),
    )
    assert _ids(ask_pic._passages(result, "en", _sel(skip=True))) == [1, 2, 4, 5]
    assert _ids(ask_pic._passages(result, "en", _sel())) == [1, 2, 3, 4, 5]


def test_paragraph_replaces_its_articles_earlier_claims():
    result = _result(
        _chunk(1, 10, source_type=CLAIM),
        _chunk(2, 11, source_type=CLAIM),
        _chunk(3, 10, index=3),  # drops claim 1
        _chunk(4, 10, source_type=CLAIM),  # skipped
        _chunk(5, 12, index=0),
        _chunk(6, 13, index=0),
    )
    passages = ask_pic._passages(result, "en", _sel(n=4, replace=True))
    assert _ids(passages) == [2, 3, 5, 6]


def test_replaced_claims_free_the_cap_for_the_paragraph():
    # Two claims of article 10 fill its cap; its paragraph still gets in
    # because it replaces them.
    result = _result(
        _chunk(1, 10, source_type=CLAIM),
        _chunk(2, 10, source_type=CLAIM),
        _chunk(3, 10, index=0),
    )
    assert _ids(ask_pic._passages(result, "en", _sel(cap=2, replace=True))) == [3]
    assert _ids(ask_pic._passages(result, "en", _sel(cap=2, skip=True))) == [1, 2]


def test_passage_text_is_capped():
    chunk = _chunk(1, 10)
    chunk.content = "x" * (ask_pic.MAX_PASSAGE_CHARS + 50)
    (passage,) = ask_pic._passages(_result(chunk), "en")
    assert len(passage.content) == ask_pic.MAX_PASSAGE_CHARS


def test_default_selection_is_the_measured_one():
    """The endpoint runs the configuration #695 measured (results in
    docs/portfolio/rag/results/2026-10-12-selection-695.md)."""
    default = ask_pic.DEFAULT_SELECTION
    assert default.max_passages == ask_pic.MAX_PASSAGES == 15
    assert default.per_article_cap == ask_pic.MAX_PASSAGES_PER_ARTICLE == 2
    assert default.language_dedup is True
    assert default.paragraph_replaces_claims is True


def test_worst_case_prompt_prices_every_passage():
    one = ask_pic.controls.estimate_input_tokens("x" * (ask_pic.MAX_PASSAGE_CHARS + 120))
    assert ask_pic._worst_prompt_tokens("q") >= ask_pic.MAX_PASSAGES * one


def test_search_groups_every_chunk(monkeypatch):
    """Titles and citations come from the groups, and the selection can reach
    past the first few articles, so every chunk's article must be grouped."""
    seen = {}

    def fake_search(db, query, **kwargs):
        seen.update(kwargs)
        return _result()

    monkeypatch.setattr(retrieval, "search", fake_search)
    ask_pic.search_passages(None, "q")
    assert seen["k"] >= 2 * retrieval.DEFAULT_CANDIDATES
    assert seen["rerank"] is True and seen["mode"] == "hybrid"
    assert seen["visibility"] == "public"


# --- eval wiring ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, ask_pic.DEFAULT_SELECTION),
        (
            {"max_passages": 8, "per_article_cap": 0, "language_dedup": False},
            ask_pic.PassageSelection(
                max_passages=8,
                per_article_cap=None,
                language_dedup=False,
                skip_covered_claims=ask_pic.DEFAULT_SELECTION.skip_covered_claims,
                paragraph_replaces_claims=ask_pic.DEFAULT_SELECTION.paragraph_replaces_claims,
            ),
        ),
    ],
)
def test_selection_from_args(overrides, expected):
    args = SimpleNamespace(
        max_passages=None,
        per_article_cap=None,
        language_dedup=None,
        skip_covered_claims=None,
        paragraph_replaces_claims=None,
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    assert eval_cli.selection_from_args(args) == expected


def test_twin_flags_parse():
    args = eval_cli.build_parser().parse_args(
        ["answers", "--no-twin-by-index", "--derive-twin-keys"]
    )
    assert eval_cli.selection_from_args(args).twin_by_index is False
    assert args.derive_twin_keys is True
    sel = eval_cli.build_parser().parse_args(["selection", "--derive-twin-keys"])
    assert sel.derive_twin_keys is True
    assert selection_eval.VARIANTS["rules15-replace-twin-key"].twin_by_index is False
    assert selection_eval.VARIANTS["rules15-replace"].twin_by_index is True
    # #713 measured the twin key with no gain in the answers eval: the endpoint
    # keeps pairing by chunk_index (docs/portfolio/rag/results/2026-10-13-twin-key-713.md).
    assert ask_pic.DEFAULT_SELECTION.twin_by_index is True


def test_selection_cli_flags_parse():
    args = eval_cli.build_parser().parse_args(
        ["answers", "--per-article-cap", "3", "--no-language-dedup", "--label", "x"]
    )
    sel = eval_cli.selection_from_args(args)
    assert sel.per_article_cap == 3 and sel.language_dedup is False
    assert args.label == "x"


def test_selection_eval_scores_each_variant():
    q = SimpleNamespace(
        question="When?",
        answerable=True,
        expected_article_ids=[11],
        key_facts=["1510"],
    )
    a = _chunk(1, 10, "en", index=0)
    b = _chunk(2, 10, "es", index=0)
    c = _chunk(3, 11, "en", index=0)
    c.content = "In 1510 ..."
    variants = {"two": _sel(n=2), "two-dedup": _sel(n=2, dedup=True)}
    out = selection_eval.evaluate_question(q, _result(a, b, c), "en", variants)
    assert out["two"]["passage_recall"] == 0.0
    assert out["two"]["key_facts_in_passages"] is False
    assert out["two"]["twin_pairs"] == 1
    assert out["two-dedup"]["passage_recall"] == 1.0
    assert out["two-dedup"]["key_facts_in_passages"] is True
    assert out["two-dedup"]["twin_pairs"] == 0


def test_chunk_ranks_find_the_fact_and_expected_chunks():
    q = SimpleNamespace(answerable=True, expected_article_ids=[11], key_facts=["1510", "Enciso"])
    a = _chunk(1, 10)
    b = _chunk(2, 11)
    b.content = "Enciso sailed"
    c = _chunk(3, 12)
    c.content = "In 1510 Enciso ..."
    c.rerank_score = 0.8

    ranks = selection_eval.chunk_ranks(q, _result(a, b, c))

    assert ranks == {"expected_chunk_rank": 2, "fact_chunk_rank": 3, "fact_chunk_rerank": 0.8}
    q.key_facts = ["1999"]
    assert selection_eval.chunk_ranks(q, _result(a, b, c))["fact_chunk_rank"] is None
    q.answerable = False
    assert selection_eval.chunk_ranks(q, _result(a, b, c)) == {}


def test_rank_summary_mrr():
    base = {"answerable": True, "type": "reader", "subtype": "who", "source": "reader"}
    records = [
        {**base, "fact_chunk_rank": 1, "expected_chunk_rank": 1},
        {**base, "fact_chunk_rank": 4, "expected_chunk_rank": 2},
        {**base, "fact_chunk_rank": None, "expected_chunk_rank": None},
    ]
    s = selection_eval.rank_summary(records)["all answerable"]
    assert s["n_facts"] == 3 and s["fact_chunk_found"] == 2
    assert s["fact_chunk_top1"] == 1 and s["fact_chunk_top5"] == 2
    assert s["fact_chunk_mrr"] == pytest.approx((1 + 0.25 + 0) / 3)
    assert s["expected_chunk_mrr"] == pytest.approx((1 + 0.5 + 0) / 3)


def test_derived_twin_keys_fill_hits_from_the_current_chunker(monkeypatch):
    """--derive-twin-keys (#713): re-chunk the hit's article and copy the key
    of the chunk with the same language and text; leave unmatched hits alone."""
    from app.services.knowledge.chunker import chunk_article
    from scripts.eval_rag import twin_keys

    body = "## A\n\nFirst paragraph.\n\n## B\n\nSecond paragraph."
    article = SimpleNamespace(title="T", content=body, title_es="T", content_es=body)
    db = SimpleNamespace(get=lambda _model, _id: article)
    wanted = chunk_article("T", body, "es")[1]
    hit = _chunk(1, 10, "es", index=1, key=None)
    hit.content = wanted.content
    stale = _chunk(2, 10, "en", index=0, key=None)  # text no longer in the article
    claim = _chunk(3, 10, source_type=CLAIM)
    result = _result(hit, stale, claim)

    calls = []

    def fake_search(db, question, extra_queries=None):
        calls.append(question)
        return result

    monkeypatch.setattr(ask_pic, "search_passages", fake_search)
    deriver = twin_keys.install()
    assert ask_pic.search_passages(db, "q") is result

    assert calls == ["q"]
    assert hit.meta[TWIN_KEY] == wanted.meta[TWIN_KEY]
    assert stale.meta is None and claim.meta is None
    assert (deriver.filled, deriver.unmatched) == (1, 1)


def test_bare_rerank_flag_parses():
    args = eval_cli.build_parser().parse_args(["selection", "--bare-rerank"])
    assert args.bare_rerank is True
