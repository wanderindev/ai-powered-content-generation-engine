"""Tests for the Grammarly meaning check (#518): verdict parsing, targeted
reverts, review rows, and the grammar-apply wiring with a mocked model."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.models.grammar_check import GrammarCheckFix
from app.models.grammar_review import GrammarReviewDiff
from app.services import grammar_verdicts as gv
from app.services.grammar_verdicts import (
    ChangedPair,
    Verdict,
    changed_pairs,
    parse_verdict_lines,
    run_meaning_check,
)
from app.services.series_sections import split_series_sections

ON = SimpleNamespace(grammar_meaning_check_enabled=True, anthropic_api_key="sk-test")
OFF = SimpleNamespace(grammar_meaning_check_enabled=False, anthropic_api_key="sk-test")
NO_KEY = SimpleNamespace(grammar_meaning_check_enabled=True, anthropic_api_key="")

OLD = (
    "## Intro\n\n"
    "On the peninsula, engineers would concentrate their works on a single front, the *frente de tierra*.\n\n"
    "In practice the situado arrived late.\n\n"
    "Nature added a second gift, and it is a distinctly Panamanian one."
)
NEW = (
    "## Intro\n\n"
    "On the peninsula, engineers concentrated their works on a single front: the *frente de tierra*.\n\n"
    "In practice, the situado arrived late.\n\n"
    "Nature added a second gift, distinctly Panamanian."
)


def _message(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


# --- pure helpers ----------------------------------------------------------


def test_changed_pairs_align_by_position():
    pairs = changed_pairs(OLD, NEW)
    assert [p.index for p in pairs] == [1, 2, 3]
    assert pairs[0].before.startswith("On the peninsula, engineers would")
    assert pairs[0].after.startswith("On the peninsula, engineers concentrated")


def test_parse_keep_revert_review_and_drop_malformed():
    raw = (
        "[1] KEEP\n"
        "[2] REVERT ||| FIND: engineers concentrated ||| REPLACE: engineers would concentrate ||| REASON: tense\n"
        "[3] REVIEW ||| REASON: unsure\n"
        "[4] REVERT ||| FIND: x ||| REASON: no replace\n"  # degenerate -> dropped
        "[9] KEEP\n"  # out of range -> dropped
        "chatter line\n"
        "[2] REVERT ||| FIND: front: ||| REPLACE: front, ||| REASON: punctuation\n"
    )
    verdicts = parse_verdict_lines(raw, pair_count=4)
    assert [(v.position, v.kind) for v in verdicts] == [
        (0, "KEEP"),
        (1, "REVERT"),
        (2, "REVIEW"),
        (1, "REVERT"),
    ]
    assert verdicts[1].find == "engineers concentrated"
    assert verdicts[1].replace == "engineers would concentrate"
    assert verdicts[1].reason == "tense"


def test_apply_verdicts_reverts_exactly_once_and_files_the_rest():
    pairs = changed_pairs(OLD, NEW)
    verdicts = [
        Verdict(0, "REVERT", "tense", "engineers concentrated", "engineers would concentrate"),
        Verdict(0, "REVERT", "colon", "front: the", "front, the"),
        Verdict(1, "KEEP"),
        Verdict(
            2, "REVERT", "voice", "second gift, distinctly", "second gift, and it is a distinctly"
        ),
    ]
    result = gv._apply_verdicts(NEW, pairs, verdicts)
    assert (
        "engineers would concentrate their works on a single front, the *frente de tierra*."
        in result.body
    )
    assert "In practice, the situado arrived late." in result.body  # KEEP
    assert [p.index for p, _ in result.reverted] == [1, 3]
    assert result.review == []


def test_missing_verdict_and_failed_revert_go_to_review():
    pairs = changed_pairs(OLD, NEW)
    verdicts = [Verdict(0, "REVERT", "r", "not in the text", "whatever")]
    result = gv._apply_verdicts(NEW, pairs, verdicts)
    assert result.body == NEW
    reasons = {p.index: r for p, r in result.review}
    assert reasons[1].startswith("revert could not be applied (snippet not found in content)")
    assert reasons[2] == gv.NO_VERDICT_REASON and reasons[3] == gv.NO_VERDICT_REASON


def test_disabled_or_keyless_never_calls_the_model():
    with (
        patch.object(gv, "create_message") as create,
        patch.object(gv, "get_settings", return_value=OFF),
    ):
        assert run_meaning_check(OLD, NEW).called is False
    with (
        patch.object(gv, "create_message") as create,
        patch.object(gv, "get_settings", return_value=NO_KEY),
    ):
        assert run_meaning_check(OLD, NEW).called is False
    create.assert_not_called()
    with (
        patch.object(gv, "create_message") as create,
        patch.object(gv, "get_settings", return_value=ON),
    ):
        assert run_meaning_check(OLD, OLD).called is False  # nothing changed
    create.assert_not_called()


def test_model_failure_files_every_pair_for_review():
    with (
        patch.object(gv, "create_message", side_effect=RuntimeError("boom")),
        patch.object(gv, "get_settings", return_value=ON),
        patch.object(gv.sentry_sdk, "capture_exception") as capture,
    ):
        result = run_meaning_check(OLD, NEW)
    assert result.called is True and result.body == NEW
    assert len(result.review) == 3 and all("meaning check failed" in r for _, r in result.review)
    capture.assert_called_once()


def test_run_uses_sonnet_low_effort():
    create = MagicMock(return_value=_message("[1] KEEP\n[2] KEEP\n[3] KEEP"))
    with (
        patch.object(gv, "create_message", create),
        patch.object(gv, "get_settings", return_value=ON),
    ):
        result = run_meaning_check(OLD, NEW)
    assert result.called and result.body == NEW and not result.review
    kwargs = create.call_args.kwargs
    assert kwargs["feature"] == "grammar_verdicts"
    assert kwargs["model"] == gv.SONNET_MODEL
    assert kwargs["output_config"] == {"effort": "low"}
    assert "[1] BEFORE:" in kwargs["messages"][0]["content"]


# --- endpoint wiring ------------------------------------------------------

BODY = (
    "## Introduction\n\n"
    "In the last article we left Panama City in ashes. The **old capital** never recovered.\n\n"
    "Nature added a second gift, and it is a distinctly Panamanian one."
)


@pytest.fixture
def article(test_db, article_factory):
    row = article_factory(title="Walls", content=BODY)
    test_db.commit()
    test_db.refresh(row)
    return row


def _source(test_client, article_id):
    resp = test_client.get(
        f"/api/v1/admin/dashboard/articles/{article_id}/grammar-source?language=en"
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _apply(test_client, article_id, payload):
    return test_client.post(
        f"/api/v1/admin/dashboard/articles/{article_id}/grammar-apply?language=en", json=payload
    )


class TestGrammarApplyMeaningCheck:
    def test_reverts_before_writing_and_marks_the_item(
        self, test_client, admin_token, test_db, article
    ):
        src = _source(test_client, article.id)
        corrected = (
            src["body_html"]
            .replace("In the last article we left", "In the last article, we left")
            .replace(
                "Nature added a second gift, and it is a distinctly Panamanian one.",
                "Nature added a second gift, distinctly Panamanian.",
            )
        )
        verdict_text = (
            "[1] KEEP\n"
            "[2] REVERT ||| FIND: second gift, distinctly Panamanian. ||| "
            "REPLACE: second gift, and it is a distinctly Panamanian one. ||| REASON: flattened voice"
        )
        create = MagicMock(return_value=_message(verdict_text))
        with (
            patch.object(gv, "create_message", create),
            patch.object(gv, "get_settings", return_value=ON),
        ):
            resp = _apply(
                test_client,
                article.id,
                {
                    "body_html": corrected,
                    "fingerprint": src["fingerprint"],
                    "accepted": [
                        {
                            "kind": "Add a comma",
                            "before": "In the last article we left",
                            "after": "In the last article, we left",
                        },
                        {
                            "kind": "Improve your text",
                            "before": "Nature added a second gift, and it is a distinctly Panamanian one.",
                            "after": "Nature added a second gift, distinctly Panamanian.",
                        },
                    ],
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["written"] is True and data["meaning_checked"] is True
        assert data["reverted_count"] == 1 and data["review_count"] == 0

        test_db.refresh(article)
        _, body, _ = split_series_sections(article.content)
        assert "In the last article, we left" in body  # kept
        assert "and it is a distinctly Panamanian one." in body  # reverted
        assert "**old capital**" in body

        rows = (
            test_db.query(GrammarCheckFix)
            .filter_by(article_id=article.id)
            .order_by(GrammarCheckFix.id)
            .all()
        )
        assert [r.applied for r in rows] == [True, False]
        assert rows[1].skipped_reason == "reverted by meaning check: flattened voice"
        assert test_db.query(GrammarReviewDiff).filter_by(article_id=article.id).count() == 0

    def test_review_rows_and_listing_and_resolve(self, test_client, admin_token, test_db, article):
        src = _source(test_client, article.id)
        corrected = src["body_html"].replace(
            "In the last article we left", "In the last article, we left"
        )
        create = MagicMock(return_value=_message("[1] REVIEW ||| REASON: cannot tell"))
        with (
            patch.object(gv, "create_message", create),
            patch.object(gv, "get_settings", return_value=ON),
        ):
            resp = _apply(
                test_client,
                article.id,
                {
                    "body_html": corrected,
                    "fingerprint": src["fingerprint"],
                    "accepted": [
                        {
                            "before": "In the last article we left",
                            "after": "In the last article, we left",
                        }
                    ],
                },
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["review_count"] == 1
        test_db.refresh(article)
        assert "In the last article, we left" in article.content  # still written

        listed = test_client.get(
            f"/api/v1/admin/dashboard/articles/{article.id}/grammar-review"
        ).json()
        assert listed["pending_count"] == 1
        diff = listed["diffs"][0]
        assert diff["reason"] == "cannot tell" and diff["block_index"] == 1
        assert diff["before_text"].startswith("In the last article we left")

        grid = test_client.get("/api/v1/admin/dashboard/articles").json()
        assert next(a for a in grid if a["id"] == article.id)["grammar_review_count"] == 1

        done = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/grammar-review/{diff['id']}/resolve"
        )
        assert done.status_code == 200 and done.json()["resolved_at"] is not None
        assert (
            test_client.get(f"/api/v1/admin/dashboard/articles/{article.id}/grammar-review").json()[
                "pending_count"
            ]
            == 0
        )
        assert (
            test_client.post(
                f"/api/v1/admin/dashboard/articles/{article.id}/grammar-review/{diff['id']}/resolve"
            ).status_code
            == 200
        )
        assert (
            test_client.post(
                f"/api/v1/admin/dashboard/articles/{article.id + 999}/grammar-review/{diff['id']}/resolve"
            ).status_code
            == 404
        )

    def test_dry_run_never_calls_the_model(self, test_client, admin_token, article):
        src = _source(test_client, article.id)
        corrected = src["body_html"].replace(
            "In the last article we left", "In the last article, we left"
        )
        create = MagicMock()
        with (
            patch.object(gv, "create_message", create),
            patch.object(gv, "get_settings", return_value=ON),
        ):
            resp = _apply(
                test_client,
                article.id,
                {
                    "body_html": corrected,
                    "fingerprint": src["fingerprint"],
                    "dry_run": True,
                    "accepted": [{"before": "a", "after": "b"}],
                },
            )
        assert resp.status_code == 200 and resp.json()["meaning_checked"] is False
        create.assert_not_called()


def test_prompt_keeps_intensifier_edits_and_names_claim_shifts():
    """2026-09-27 calibration: "emphasis" read as rhetorical intensity made the
    check revert ~10 of 27 sound edits; the rules now say what to keep and name
    the three claim shifts a looser prompt missed."""
    from app.services.grammar_verdicts import _VERDICT_PROMPT

    keep_rule = next(line for line in _VERDICT_PROMPT.splitlines() if line.startswith("- KEEP"))
    revert_rule = next(
        line for line in _VERDICT_PROMPT.splitlines() if line.startswith("- REVERT only")
    )
    assert "intensifiers" in keep_rule
    assert "emphasis" not in revert_rule
    assert '"would build"' in revert_rule  # plan -> fact
    assert '"was not allowed to" -> "could not"' in revert_rule  # rule -> inability
    assert "idiom" in revert_rule
    assert "ungrammatical" in _VERDICT_PROMPT
