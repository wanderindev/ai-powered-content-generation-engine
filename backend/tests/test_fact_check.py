"""Tests for the fact-check pipeline foundation + article grounding (#374/#375)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app.services.fact_check as fact_check_module
import pytest
from app.models.article import Article
from app.models.article_suggestion import ArticleSuggestion
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.models.enums import FactCheckSubject, FactCheckVerdict, FactClaimType
from app.models.fact_check import FactCheckClaim
from app.models.research import Research
from app.models.verified_claim import VerifiedClaim
from app.services.fact_check import (
    ExtractedClaim,
    _normalize,
    _run_fact_check_for_subject,
    build_suggestion_text,
    extract_claims,
    extract_for_deep_research,
    ground_article,
    ground_subject,
    grounding_verdict,
    match_claims_against_kb,
    parse_claim_lines,
    run_fact_check_job,
    upsert_verified_claims,
)
from app.services.llm import HAIKU_MODEL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

# --- Helpers ---

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


def _make_client(*messages):
    """Mock Anthropic client consuming `messages` in order across BOTH the
    create (short) and stream (long) seams create_message uses."""
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


def _msg(text: str):
    return MagicMock(content=[MagicMock(text=text)])


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    """Bypass admin token validation via dependency override (#54)."""
    from app.api.dependencies import (
        validate_admin_or_allowlisted_driver_token,
        validate_admin_token,
    )
    from app.main import app

    # dependency_overrides keys on the exact callable a router declares: the
    # dashboard/instagram/admin routers now declare the allowlisted-driver
    # variant (#444), other admin routers still the plain one. Override both.
    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    app.dependency_overrides[validate_admin_or_allowlisted_driver_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)
    app.dependency_overrides.pop(validate_admin_or_allowlisted_driver_token, None)


EXTRACTION_RESPONSE = """\
CLAIM: DATE | Panama Viejo was founded in 1519
CLAIM: PERSON | Pedrarias Davila founded Panama Viejo
CLAIM: EVENT | Henry Morgan attacked Panama Viejo in 1671"""

GROUNDING_RESPONSE = """\
VERDICT: 1 | SUPPORTED | -
VERDICT: 2 | CONTRADICTED | The research says the city was founded by a royal expedition, not Pedrarias Davila alone
VERDICT: 3 | ABSENT | -"""


def _create_article_chain(test_db, category_factory, *, content="Article text. " * 50):
    category = category_factory()
    suggestion = ArticleSuggestion(
        title="Test Suggestion",
        category_id=category.id,
        status="APPROVED",
        main_topic="Panama Viejo",
        sub_topics=["Founding", "Destruction"],
        point_of_view="narrative",
    )
    test_db.add(suggestion)
    test_db.flush()
    research = Research(
        suggestion_id=suggestion.id,
        content="Research content about Panama Viejo. " * 200,
        status="APPROVED",
    )
    test_db.add(research)
    test_db.flush()
    article = Article(
        research_id=research.id,
        category_id=category.id,
        title="Panama Viejo",
        outline="## Founding\n## Destruction",
        outline_status="APPROVED",
        status="PENDING",
        content=content,
    )
    test_db.add(article)
    test_db.flush()
    return article, research


# --- parse_claim_lines (#374) -------------------------------------------------


class TestParseClaimLines:
    def test_parses_well_formed_lines(self):
        claims = parse_claim_lines(EXTRACTION_RESPONSE)
        assert [c.claim_type for c in claims] == ["DATE", "PERSON", "EVENT"]
        assert claims[0].text == "Panama Viejo was founded in 1519"

    def test_skips_malformed_lines_and_no_claims(self):
        raw = "Here are the claims:\nNO CLAIMS\nCLAIM missing colon | x\n- bullet"
        assert parse_claim_lines(raw) == []

    def test_off_vocabulary_type_coerced_to_other(self):
        claims = parse_claim_lines("CLAIM: BANANA | Something factual")
        assert len(claims) == 1
        assert claims[0].claim_type == FactClaimType.OTHER.value


# --- extract_claims (#374) ------------------------------------------------------


class TestExtractClaims:
    @patch("app.services.llm.get_client")
    def test_short_text_single_call(self, mock_get_client):
        client = _make_client(_msg(EXTRACTION_RESPONSE))
        mock_get_client.return_value = client
        claims = extract_claims("Short text about Panama Viejo.")
        assert len(claims) == 3

    @patch("app.services.llm.get_client")
    def test_long_text_chunked_and_deduped(self, mock_get_client):
        # Two chunks return an overlapping claim (punctuation/case differ) —
        # the normalized dedup keeps one copy, first-seen order.
        client = _make_client(
            _msg("CLAIM: DATE | Panama Viejo was founded in 1519"),
            _msg(
                "CLAIM: DATE | Panama Viejo was founded in 1519!\n"
                "CLAIM: EVENT | Henry Morgan attacked Panama Viejo in 1671"
            ),
        )
        mock_get_client.return_value = client
        long_text = "word " * 2000  # > CHUNK_WORDS -> 2 chunks
        claims = extract_claims(long_text)
        assert [c.text for c in claims] == [
            "Panama Viejo was founded in 1519",
            "Henry Morgan attacked Panama Viejo in 1671",
        ]


# --- ground_article (#375) ------------------------------------------------------


class TestGroundArticle:
    @patch("app.services.llm.get_client")
    def test_maps_verdicts_and_persists(self, mock_get_client, test_db, category_factory):
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE)
        )
        article, research = _create_article_chain(test_db, category_factory)

        summary = ground_article(article, research, test_db)

        assert summary["total_claims"] == 3
        assert summary["verified"] == 1
        assert summary["inaccurate"] == 1
        # #555: an article claim its research does not cover is queued for
        # deep research, not parked as UNVERIFIABLE.
        assert summary["unverifiable"] == 0
        assert summary["needs_deep_research"] == 1

        rows = (
            test_db.query(FactCheckClaim)
            .filter(
                FactCheckClaim.subject_type == FactCheckSubject.ARTICLE.value,
                FactCheckClaim.subject_id == article.id,
            )
            .order_by(FactCheckClaim.id)
            .all()
        )
        assert [r.verdict for r in rows] == [
            FactCheckVerdict.VERIFIED.value,
            FactCheckVerdict.INACCURATE.value,
            FactCheckVerdict.NEEDS_DEEP_RESEARCH.value,
        ]
        # CONTRADICTED note lands in correction; "-" notes are dropped.
        assert rows[0].correction is None
        assert "royal expedition" in rows[1].correction
        assert all(r.checked_via == "GROUNDING" for r in rows)
        assert all(r.checked_at is not None for r in rows)

    @patch("app.services.llm.get_client")
    def test_missing_verdict_degrades_to_deep_research(
        self, mock_get_client, test_db, category_factory
    ):
        # Judge answers only claim 1 — the other two must NOT default to
        # verified. A missing line is ABSENT, which for an article (#555)
        # queues the claim for deep research.
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg("VERDICT: 1 | SUPPORTED | -")
        )
        article, research = _create_article_chain(test_db, category_factory)

        summary = ground_article(article, research, test_db)

        assert summary["verified"] == 1
        assert summary["needs_deep_research"] == 2
        assert summary["unverifiable"] == 0

    @patch("app.services.llm.get_client")
    def test_edu_material_absent_stays_unverifiable(
        self, mock_get_client, test_db, category_factory
    ):
        """EDU_MATERIAL shares ground_subject but keeps ABSENT -> UNVERIFIABLE."""
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE)
        )

        summary = ground_subject(
            FactCheckSubject.EDU_MATERIAL.value,
            987,
            "Guide text about Panama Viejo.",
            "Research content about Panama Viejo.",
            test_db,
            feature="test",
        )

        assert [r.verdict for r in summary["claims"]] == [
            FactCheckVerdict.VERIFIED.value,
            FactCheckVerdict.INACCURATE.value,
            FactCheckVerdict.UNVERIFIABLE.value,
        ]
        assert summary["needs_deep_research"] == 0

    @pytest.mark.parametrize(
        ("subject", "label", "expected"),
        [
            ("ARTICLE", "SUPPORTED", "VERIFIED"),
            ("ARTICLE", "CONTRADICTED", "INACCURATE"),
            ("ARTICLE", "ABSENT", "NEEDS_DEEP_RESEARCH"),
            ("ARTICLE", "BANANA", "UNVERIFIABLE"),
            ("EDU_MATERIAL", "SUPPORTED", "VERIFIED"),
            ("EDU_MATERIAL", "CONTRADICTED", "INACCURATE"),
            ("EDU_MATERIAL", "ABSENT", "UNVERIFIABLE"),
            ("EDU_MATERIAL", "BANANA", "UNVERIFIABLE"),
        ],
    )
    def test_grounding_verdict_mapping(self, subject, label, expected):
        assert grounding_verdict(subject, label) == expected

    @patch("app.services.llm.get_client")
    def test_rerun_replaces_previous_rows(self, mock_get_client, test_db, category_factory):
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE),
            _msg(GROUNDING_RESPONSE),
            _msg("CLAIM: DATE | Panama Viejo was founded in 1519"),
            _msg("VERDICT: 1 | SUPPORTED | -"),
        )
        article, research = _create_article_chain(test_db, category_factory)

        ground_article(article, research, test_db)
        summary = ground_article(article, research, test_db)

        assert summary["total_claims"] == 1
        count = (
            test_db.query(FactCheckClaim).filter(FactCheckClaim.subject_id == article.id).count()
        )
        assert count == 1

    # --- #575: a re-ground reuses deep-research verdicts and the KB -----------

    @staticmethod
    def _seed_article_claim(test_db, article_id, text, verdict, checked_via, **extra):
        row = FactCheckClaim(
            subject_type=FactCheckSubject.ARTICLE.value,
            subject_id=article_id,
            claim_text=text,
            claim_type="EVENT",
            verdict=verdict,
            checked_via=checked_via,
            **extra,
        )
        test_db.add(row)
        test_db.flush()
        return row

    @staticmethod
    def _article_rows(test_db, article_id):
        return (
            test_db.query(FactCheckClaim)
            .filter(
                FactCheckClaim.subject_type == FactCheckSubject.ARTICLE.value,
                FactCheckClaim.subject_id == article_id,
            )
            .order_by(FactCheckClaim.id)
            .all()
        )

    @patch("app.services.llm.get_client")
    def test_reground_keeps_deep_research_verdict(self, mock_get_client, test_db, category_factory):
        # Empty KB and an exact claim_key hit: no LLM call beyond extract+judge.
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE)
        )
        article, research = _create_article_chain(test_db, category_factory)
        resolved_at = datetime(2026, 9, 1, tzinfo=UTC)
        # Different case/punctuation, same normalised claim_key as claim 3.
        self._seed_article_claim(
            test_db,
            article.id,
            "Henry Morgan attacked Panama Viejo in 1671.",
            "DISPUTED",
            "DEEP_RESEARCH",
            correction="Sources disagree on the month",
            citation="https://example.org/morgan",
            checked_at=resolved_at,
        )
        # A stale grounding row is still replaced, not kept.
        self._seed_article_claim(test_db, article.id, "Some old claim", "VERIFIED", "GROUNDING")

        summary = ground_article(article, research, test_db)

        assert summary["total_claims"] == 3
        assert summary["needs_deep_research"] == 0
        rows = self._article_rows(test_db, article.id)
        assert len(rows) == 3
        kept = rows[2]
        assert kept.claim_text == "Henry Morgan attacked Panama Viejo in 1671"
        assert kept.verdict == FactCheckVerdict.DISPUTED.value
        assert kept.checked_via == "DEEP_RESEARCH"
        assert kept.checked_at == resolved_at
        assert kept.citation == "https://example.org/morgan"
        assert kept.correction == "Sources disagree on the month"
        # The judged claims keep their grounding verdicts.
        assert [r.checked_via for r in rows[:2]] == ["GROUNDING", "GROUNDING"]

    @patch("app.services.llm.get_client")
    def test_reground_does_not_keep_corrected_or_unresolved_rows(
        self, mock_get_client, test_db, category_factory
    ):
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE)
        )
        article, research = _create_article_chain(test_db, category_factory)
        # CORRECTED describes the edited content; re-asserted text is re-queued.
        self._seed_article_claim(
            test_db,
            article.id,
            "Henry Morgan attacked Panama Viejo in 1671",
            "CORRECTED",
            "DEEP_RESEARCH",
        )

        summary = ground_article(article, research, test_db)

        assert summary["needs_deep_research"] == 1
        row = self._article_rows(test_db, article.id)[2]
        assert row.verdict == FactCheckVerdict.NEEDS_DEEP_RESEARCH.value
        assert row.checked_via == "GROUNDING"

    @patch("app.services.llm.get_client")
    def test_absent_claim_inherits_kb_verdict(self, mock_get_client, test_db, category_factory):
        client = _make_client(_msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE))
        mock_get_client.return_value = client
        article, research = _create_article_chain(test_db, category_factory)
        _seed_kb(
            test_db,
            "Henry Morgan attacked Panama Viejo in 1671",
            verdict="INACCURATE",
            correction="It was 1671 but the city was Panama, not Panama Viejo by name",
            citation="https://kb.org/morgan",
        )
        # A SUPPORTED claim with a KB entry keeps its grounding verdict.
        _seed_kb(test_db, "Panama Viejo was founded in 1519", verdict="DISPUTED")

        summary = ground_article(article, research, test_db)

        assert summary["needs_deep_research"] == 0
        rows = self._article_rows(test_db, article.id)
        assert rows[0].verdict == FactCheckVerdict.VERIFIED.value
        assert rows[0].checked_via == "GROUNDING"
        assert rows[2].verdict == FactCheckVerdict.INACCURATE.value
        assert rows[2].checked_via == "KB_MATCH"
        assert rows[2].citation == "https://kb.org/morgan"
        assert "Panama Viejo by name" in rows[2].correction
        # Exact claim_key hit: only extract + judge reached the model.
        assert client.messages.create.call_count + client.messages.stream.call_count == 2

    # --- #586: the KB's Haiku pass inside grounding -----------------------------

    _FUZZY_MORGAN = "The pirate Henry Morgan attacked Panama Viejo in the year 1671"

    @patch("app.services.llm.get_client")
    def test_absent_claim_fuzzy_kb_match(self, mock_get_client, test_db, category_factory):
        article, research = _create_article_chain(test_db, category_factory)
        kb = _seed_kb(
            test_db,
            self._FUZZY_MORGAN,
            verdict="INACCURATE",
            correction="Morgan sacked the city in 1671",
            citation="https://kb.org/morgan-fuzzy",
        )
        client = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE), _msg(f"MATCH: 1 | {kb.id}")
        )
        mock_get_client.return_value = client

        summary = ground_article(article, research, test_db)

        assert summary["needs_deep_research"] == 0
        row = self._article_rows(test_db, article.id)[2]
        assert row.checked_via == "KB_MATCH"
        assert row.verdict == FactCheckVerdict.INACCURATE.value
        assert row.correction == "Morgan sacked the city in 1671"
        assert row.citation == "https://kb.org/morgan-fuzzy"
        # extract + judge + one Haiku KB confirmation.
        assert client.messages.create.call_count + client.messages.stream.call_count == 3

    @patch("app.services.llm.get_client")
    def test_kb_failure_degrades_to_queued(self, mock_get_client, test_db, category_factory):
        from app.services.llm import BudgetExceededError

        article, research = _create_article_chain(test_db, category_factory)
        _seed_kb(test_db, self._FUZZY_MORGAN, verdict="INACCURATE")
        # Extract and the judge stream (pop the queue in order); only the KB
        # match is a non-streamed create.
        client = _make_client(_msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE))
        client.messages.create.side_effect = [BudgetExceededError("budget")]
        mock_get_client.return_value = client

        with patch("app.services.fact_check.sentry_sdk.capture_exception") as capture:
            summary = ground_article(article, research, test_db)

        capture.assert_called_once()
        assert summary["needs_deep_research"] == 1
        row = self._article_rows(test_db, article.id)[2]
        assert row.verdict == FactCheckVerdict.NEEDS_DEEP_RESEARCH.value
        assert row.checked_via == "GROUNDING"

    @patch("app.services.llm.get_client")
    def test_reground_newest_prior_row_wins(self, mock_get_client, test_db, category_factory):
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE)
        )
        article, research = _create_article_chain(test_db, category_factory)
        # Two deep-research rows sharing claim 3's claim_key: the newer wins.
        self._seed_article_claim(
            test_db,
            article.id,
            "Henry Morgan attacked Panama Viejo in 1671",
            "VERIFIED",
            "DEEP_RESEARCH",
            citation="https://example.org/older",
        )
        self._seed_article_claim(
            test_db,
            article.id,
            "Henry Morgan attacked Panama Viejo, in 1671!",
            "DISPUTED",
            "DEEP_RESEARCH",
            citation="https://example.org/newer",
        )

        ground_article(article, research, test_db)

        row = self._article_rows(test_db, article.id)[2]
        assert row.verdict == FactCheckVerdict.DISPUTED.value
        assert row.citation == "https://example.org/newer"
        assert row.checked_via == "DEEP_RESEARCH"

    @staticmethod
    def _recording_client(events, *messages):
        """_make_client that also records each non-streamed create call."""
        client = _make_client(*messages)
        inner = client.messages.create.side_effect

        def _create(**kwargs):
            events.append(("create", kwargs["max_tokens"]))
            return inner(**kwargs)

        client.messages.create.side_effect = _create
        return client

    @patch("app.services.llm.get_client")
    def test_progress_runs_before_kb_haiku_call(self, mock_get_client, test_db, category_factory):
        """The caller's progress seam (which commits in the job runner) fires
        before the KB Haiku call, so the pool slot is free for it (#586)."""
        article, research = _create_article_chain(test_db, category_factory)
        kb = _seed_kb(test_db, self._FUZZY_MORGAN, verdict="INACCURATE")
        events: list = []
        mock_get_client.return_value = self._recording_client(
            events,
            _msg(EXTRACTION_RESPONSE),
            _msg(GROUNDING_RESPONSE),
            _msg(f"MATCH: 1 | {kb.id}"),
        )

        ground_article(
            article,
            research,
            test_db,
            progress_callback=lambda done, total, current: events.append(("progress", current)),
        )

        kb_progress = [
            i for i, e in enumerate(events) if e[0] == "progress" and "verified-claims KB" in e[1]
        ]
        kb_create = [i for i, e in enumerate(events) if e == ("create", 4000)]
        assert len(kb_progress) == 1
        assert len(kb_create) == 1
        assert kb_progress[0] < kb_create[0]

    @patch("app.services.llm.get_client")
    def test_exact_kb_match_reports_no_kb_progress(
        self, mock_get_client, test_db, category_factory
    ):
        article, research = _create_article_chain(test_db, category_factory)
        _seed_kb(test_db, "Henry Morgan attacked Panama Viejo in 1671", verdict="INACCURATE")
        events: list = []
        mock_get_client.return_value = self._recording_client(
            events, _msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE)
        )

        ground_article(
            article,
            research,
            test_db,
            progress_callback=lambda done, total, current: events.append(("progress", current)),
        )

        assert not any(e[0] == "progress" and "verified-claims KB" in e[1] for e in events)
        assert ("create", 1500) not in events
        assert self._article_rows(test_db, article.id)[2].checked_via == "KB_MATCH"

    @patch("app.services.llm.get_client")
    def test_edu_material_absent_ignores_kb(self, mock_get_client, test_db):
        """EDU_MATERIAL never queues deep research, so it never consults the KB."""
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE)
        )
        _seed_kb(test_db, "Henry Morgan attacked Panama Viejo in 1671")

        summary = ground_subject(
            FactCheckSubject.EDU_MATERIAL.value,
            988,
            "Guide text about Panama Viejo.",
            "Research content about Panama Viejo.",
            test_db,
            feature="test",
        )

        row = summary["claims"][2]
        assert row.verdict == FactCheckVerdict.UNVERIFIABLE.value
        assert row.checked_via == "GROUNDING"

    @patch("app.services.llm.get_client")
    def test_grounding_caches_research_prefix(self, mock_get_client, test_db, category_factory):
        # 26 claims > GROUNDING_BATCH_SIZE (25) -> two grounding batches.
        extraction_response = "\n".join(f"CLAIM: OTHER | Claim number {i}" for i in range(26))
        mock_get_client.return_value = _make_client(
            _msg(extraction_response),
            _msg("VERDICT: 1 | SUPPORTED | -"),
            _msg("VERDICT: 1 | SUPPORTED | -"),
        )
        article, research = _create_article_chain(test_db, category_factory)

        ground_article(article, research, test_db)

        client = mock_get_client.return_value
        # One streamed Haiku extraction, then the two streamed judge batches.
        assert client.messages.stream.call_count == 3
        judge_calls = [
            c for c in client.messages.stream.call_args_list if c.kwargs["model"] != HAIKU_MODEL
        ]
        assert len(judge_calls) == 2

        block0_texts = []
        for call in judge_calls:
            content = call.kwargs["messages"][0]["content"]
            assert len(content) == 2
            assert content[0]["type"] == "text"
            assert content[0]["cache_control"] == {"type": "ephemeral"}
            assert "cache_control" not in content[1]
            assert research.content in content[0]["text"]
            assert research.content not in content[1]["text"]
            assert "CLAIMS:" in content[1]["text"]
            block0_texts.append(content[0]["text"])

        # Stable prefix -> byte-identical across both batches.
        assert block0_texts[0] == block0_texts[1]


# --- POST /articles/{id}/fact-check ------------------------------------------


class TestFactCheckEndpoint:
    @patch("app.api.dashboard.run_fact_check_job")
    def test_kickoff_returns_202(self, mock_run, test_client, test_db, category_factory):
        article, _ = _create_article_chain(test_db, category_factory)

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/fact-check",
            params={"token": "any"},
        )

        assert response.status_code == 202
        data = response.json()
        assert data["job_type"] == "FACT_CHECK"
        assert data["status"] == "PENDING"
        mock_run.assert_called_once_with(data["id"])
        job = test_db.get(CrawlJob, data["id"])
        assert job.params == {"subject_type": "ARTICLE", "subject_id": article.id}

    @patch("app.api.dashboard.run_fact_check_job")
    def test_duplicate_run_joins_the_inflight_job(
        self, mock_run, test_client, test_db, category_factory
    ):
        """A kickoff while a run is in flight returns the running job (202)
        instead of 409ing — no second job, no second background task. The agent
        driver races the write endpoint's auto-kicked check and must be able to
        poll the existing run to completion."""
        article, _ = _create_article_chain(test_db, category_factory)
        inflight = CrawlJob(
            job_type=CrawlJobType.FACT_CHECK.value,
            status=CrawlJobStatus.RUNNING.value,
            params={"subject_type": "ARTICLE", "subject_id": article.id},
        )
        test_db.add(inflight)
        test_db.commit()

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/fact-check",
            params={"token": "any"},
        )
        assert response.status_code == 202
        assert response.json()["id"] == inflight.id
        mock_run.assert_not_called()

    @patch("app.api.dashboard.run_fact_check_job")
    def test_other_subject_not_blocked(self, mock_run, test_client, test_db, category_factory):
        article, research = _create_article_chain(test_db, category_factory)
        test_db.add(
            CrawlJob(
                job_type=CrawlJobType.FACT_CHECK.value,
                status=CrawlJobStatus.RUNNING.value,
                params={"subject_type": "RESEARCH", "subject_id": research.id},
            )
        )
        test_db.commit()

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/fact-check",
            params={"token": "any"},
        )
        assert response.status_code == 202

    def test_article_not_found(self, test_client):
        response = test_client.post(
            "/api/v1/admin/dashboard/articles/999999/fact-check",
            params={"token": "any"},
        )
        assert response.status_code == 404

    def test_requires_content(self, test_client, test_db, category_factory):
        article, _ = _create_article_chain(test_db, category_factory, content="")

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/fact-check",
            params={"token": "any"},
        )
        assert response.status_code == 400
        assert "content" in response.json()["detail"].lower()


# --- write_article auto-trigger (#375) ----------------------------------------


ARTICLE_CONTENT = "The Panama Canal is a great engineering achievement. " * 20
EXCERPT_TEXT = "An excerpt."
SUMMARY_TEXT = "A summary."


class TestWriteArticleFactCheckTrigger:
    def _create_writable_article(self, test_db, category_factory):
        article, research = _create_article_chain(test_db, category_factory, content=None)
        return article

    @patch("app.api.dashboard.run_fact_check_job")
    @patch("app.services.llm.get_client")
    def test_write_enqueues_grounding_job(
        self, mock_get_client, mock_run, test_client, test_db, category_factory
    ):
        mock_get_client.return_value = _make_client(
            _msg(ARTICLE_CONTENT), _msg(EXCERPT_TEXT), _msg(SUMMARY_TEXT)
        )
        article = self._create_writable_article(test_db, category_factory)

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/write",
            params={"token": "any"},
        )

        assert response.status_code == 200
        job = (
            test_db.query(CrawlJob).filter(CrawlJob.job_type == CrawlJobType.FACT_CHECK.value).one()
        )
        assert job.params == {"subject_type": "ARTICLE", "subject_id": article.id}
        mock_run.assert_called_once_with(job.id)

    @patch("app.services.knowledge.indexer.index_article")
    @patch("app.api.dashboard.run_fact_check_job")
    @patch("app.services.llm.get_client")
    def test_write_kicks_off_fact_check_before_reindex(
        self, mock_get_client, mock_run, mock_index, test_client, test_db, category_factory
    ):
        # BackgroundTasks run in order, so grounding must not queue behind the
        # embedding reindex (#649).
        order: list[str] = []
        mock_run.side_effect = lambda *a, **k: order.append("fact_check")
        mock_index.side_effect = lambda *a, **k: order.append("reindex")
        mock_get_client.return_value = _make_client(
            _msg(ARTICLE_CONTENT), _msg(EXCERPT_TEXT), _msg(SUMMARY_TEXT)
        )
        article = self._create_writable_article(test_db, category_factory)

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/write",
            params={"token": "any"},
        )

        assert response.status_code == 200
        assert order == ["fact_check", "reindex"]

    @patch("app.api.dashboard._kickoff_fact_check", side_effect=RuntimeError("boom"))
    @patch("app.services.llm.get_client")
    def test_enqueue_failure_is_non_fatal(
        self, mock_get_client, _mock_kickoff, test_client, test_db, category_factory
    ):
        mock_get_client.return_value = _make_client(
            _msg(ARTICLE_CONTENT), _msg(EXCERPT_TEXT), _msg(SUMMARY_TEXT)
        )
        article = self._create_writable_article(test_db, category_factory)

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/write",
            params={"token": "any"},
        )

        # The write succeeds and the content is kept even though enqueue blew up.
        assert response.status_code == 200
        test_db.expire_all()
        refreshed = test_db.query(Article).filter(Article.id == article.id).one()
        assert refreshed.content


# --- CHECK-constraint enforcement (#24 convention) -----------------------------


class TestFactCheckConstraints:
    def _claim(self, **overrides):
        base = {
            "subject_type": FactCheckSubject.ARTICLE.value,
            "subject_id": 1,
            "claim_text": "x",
            "claim_type": FactClaimType.DATE.value,
        }
        base.update(overrides)
        return FactCheckClaim(**base)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"subject_type": "PODCAST"},
            {"claim_type": "VIBES"},
            {"verdict": "PROBABLY_FINE"},
            {"checked_via": "ASKED_A_FRIEND"},
        ],
        ids=["subject_type", "claim_type", "verdict", "checked_via"],
    )
    def test_invalid_value_rejected(self, test_db, overrides):
        test_db.add(self._claim(**overrides))
        with pytest.raises(IntegrityError):
            test_db.flush()

    def test_valid_row_accepted(self, test_db):
        test_db.add(self._claim(verdict=FactCheckVerdict.VERIFIED.value, checked_via="GROUNDING"))
        test_db.flush()  # must not raise


class TestBuildSuggestionText:
    def test_includes_all_factual_fields(self):
        suggestion = SimpleNamespace(
            title="Panama Viejo",
            main_topic="The founding of Panama Viejo",
            sub_topics=["Founding: 1519", "Destruction: 1671"],
            point_of_view="narrative",
        )
        text = build_suggestion_text(suggestion)
        assert "Panama Viejo" in text
        assert "Founding: 1519" in text
        assert "narrative" in text


def _seed_subject_rows(test_db, subject_id, *rows):
    """Persist (claim_text, verdict, checked_via) triples for a suggestion."""
    persisted = []
    for text, verdict, checked_via in rows:
        row = FactCheckClaim(
            subject_type=FactCheckSubject.SUGGESTION.value,
            subject_id=subject_id,
            claim_text=text,
            claim_type="DATE",
            verdict=verdict,
            checked_via=checked_via,
            citation="https://old.org" if checked_via else None,
        )
        test_db.add(row)
        persisted.append(row)
    test_db.flush()
    return persisted


class TestSubjectFactCheckEndpoints:
    @patch("app.api.dashboard.run_fact_check_job")
    def test_suggestion_kickoff_returns_202(self, mock_run, test_client, test_db, category_factory):
        article, research = _create_article_chain(test_db, category_factory)
        suggestion_id = research.suggestion_id

        response = test_client.post(
            f"/api/v1/admin/dashboard/suggestions/{suggestion_id}/fact-check",
            params={"token": "any"},
        )

        assert response.status_code == 202
        data = response.json()
        assert data["job_type"] == "FACT_CHECK"
        mock_run.assert_called_once_with(data["id"])
        job = test_db.get(CrawlJob, data["id"])
        # Extract-only is the only external tier (#389); no mode is stored (#751).
        assert job.params == {"subject_type": "SUGGESTION", "subject_id": suggestion_id}

    def test_suggestion_not_found(self, test_client):
        response = test_client.post(
            "/api/v1/admin/dashboard/suggestions/999999/fact-check",
            params={"token": "any"},
        )
        assert response.status_code == 404

    @patch("app.api.dashboard.run_fact_check_job")
    def test_research_kickoff_returns_202(self, mock_run, test_client, test_db, category_factory):
        article, research = _create_article_chain(test_db, category_factory)

        response = test_client.post(
            f"/api/v1/admin/dashboard/research/{research.id}/fact-check",
            params={"token": "any"},
        )

        assert response.status_code == 202
        data = response.json()
        job = test_db.get(CrawlJob, data["id"])
        assert job.params == {"subject_type": "RESEARCH", "subject_id": research.id}

    def test_research_not_found(self, test_client):
        response = test_client.post(
            "/api/v1/admin/dashboard/research/999999/fact-check",
            params={"token": "any"},
        )
        assert response.status_code == 404

    def test_research_requires_content(self, test_client, test_db, category_factory):
        article, research = _create_article_chain(test_db, category_factory)
        research.content = "   "
        test_db.flush()

        response = test_client.post(
            f"/api/v1/admin/dashboard/research/{research.id}/fact-check",
            params={"token": "any"},
        )
        assert response.status_code == 400

    @patch("app.api.dashboard.run_fact_check_job")
    @pytest.mark.parametrize("mode", ["web_search", "deep_research", "bogus"])
    def test_stray_mode_param_is_ignored(
        self, mock_run, mode, test_client, test_db, category_factory
    ):
        # #751 removed the mode query param; FastAPI ignores unknown params, so
        # an old client sending ?mode=... still gets the extract-only tier.
        article, research = _create_article_chain(test_db, category_factory)

        response = test_client.post(
            f"/api/v1/admin/dashboard/research/{research.id}/fact-check",
            params={"token": "any", "mode": mode},
        )

        assert response.status_code == 202
        job = test_db.get(CrawlJob, response.json()["id"])
        assert "mode" not in job.params


# --- Verified-claims knowledge base (#378) ---------------------------------------


def _three_claims():
    return [
        ExtractedClaim("Panama Viejo was founded in 1519", "DATE"),
        ExtractedClaim("Pedrarias Davila founded Panama Viejo", "PERSON"),
        ExtractedClaim("Henry Morgan attacked Panama Viejo in 1671", "EVENT"),
    ]


def _seed_kb(test_db, text, *, verdict="VERIFIED", correction=None, citation="https://kb.org"):
    # source_method WEB_SEARCH on purpose: prod's historical WEB_SEARCH KB rows
    # (the tier was removed in #751) must keep loading and matching via KB_MATCH.
    row = VerifiedClaim(
        claim_key=_normalize(text),
        claim_text=text,
        claim_type="DATE",
        verdict=verdict,
        correction=correction,
        citation=citation,
        source_method="WEB_SEARCH",
    )
    test_db.add(row)
    test_db.flush()
    return row


class TestKbMatching:
    def test_empty_kb_matches_nothing_without_llm(self, test_db):
        # No client patched at all — any LLM call would raise.
        assert match_claims_against_kb(_three_claims(), test_db) == {}

    @patch("app.services.llm.get_client")
    def test_exact_normalized_match_skips_llm(self, mock_get_client, test_db):
        kb = _seed_kb(test_db, "Panama Viejo was FOUNDED in 1519!")
        client = _make_client()  # empty queue — an LLM call would pop and raise
        mock_get_client.return_value = client

        claims = [ExtractedClaim("Panama Viejo was founded in 1519", "DATE")]
        matches = match_claims_against_kb(claims, test_db)

        assert matches == {0: kb}
        assert client.messages.create.call_count + client.messages.stream.call_count == 0

    @patch("app.services.llm.get_client")
    def test_fuzzy_match_confirmed_by_haiku(self, mock_get_client, test_db):
        kb = _seed_kb(test_db, "The city of Panama Viejo was founded in the year 1519")
        client = _make_client(_msg(f"MATCH: 1 | {kb.id}"))
        mock_get_client.return_value = client

        claims = [ExtractedClaim("Panama Viejo was founded in 1519", "DATE")]
        matches = match_claims_against_kb(claims, test_db)

        assert matches == {0: kb}

    @patch("app.services.llm.get_client")
    def test_haiku_none_falls_through(self, mock_get_client, test_db):
        _seed_kb(test_db, "The city of Panama Viejo was founded in the year 1519")
        mock_get_client.return_value = _make_client(_msg("MATCH: 1 | NONE"))

        claims = [ExtractedClaim("Panama Viejo was founded in 1520", "DATE")]
        assert match_claims_against_kb(claims, test_db) == {}

    @patch("app.services.llm.get_client")
    def test_hallucinated_kb_id_rejected(self, mock_get_client, test_db):
        kb = _seed_kb(test_db, "The city of Panama Viejo was founded in the year 1519")
        # Haiku answers with an id that is NOT among the claim's candidates.
        mock_get_client.return_value = _make_client(_msg(f"MATCH: 1 | {kb.id + 999}"))

        claims = [ExtractedClaim("Panama Viejo was founded in 1519", "DATE")]
        assert match_claims_against_kb(claims, test_db) == {}

    def test_unresolved_kb_rows_are_not_match_targets(self, test_db):
        _seed_kb(test_db, "Panama Viejo was founded in 1519", verdict="NEEDS_DEEP_RESEARCH")

        claims = [ExtractedClaim("Panama Viejo was founded in 1519", "DATE")]
        # Row filtered out -> empty KB path, no LLM needed.
        assert match_claims_against_kb(claims, test_db) == {}


class TestUpsertVerifiedClaims:
    def _row(self, text, verdict, checked_via="WEB_SEARCH", citation="https://x.org"):
        return FactCheckClaim(
            subject_type="SUGGESTION",
            subject_id=1,
            claim_text=text,
            claim_type="DATE",
            verdict=verdict,
            citation=citation,
            checked_via=checked_via,
        )

    def test_upserts_resolved_external_verdicts_only(self, test_db):
        rows = [
            self._row("Claim A is true", "VERIFIED"),
            self._row("Claim B is wrong", "INACCURATE"),
            self._row("Claim C is contested", "DISPUTED", checked_via="DEEP_RESEARCH"),
            self._row("Claim D unknown", "NEEDS_DEEP_RESEARCH"),
            self._row("Claim E from KB", "VERIFIED", checked_via="KB_MATCH"),
            self._row("Claim F grounded", "VERIFIED", checked_via="GROUNDING"),
        ]
        assert upsert_verified_claims(rows, test_db) == 3
        assert test_db.query(VerifiedClaim).count() == 3

    def test_dedupes_by_normalized_key_latest_wins(self, test_db):
        upsert_verified_claims([self._row("Panama Viejo was founded in 1519", "VERIFIED")], test_db)
        upsert_verified_claims(
            [self._row("panama viejo was FOUNDED in 1519!!", "DISPUTED")], test_db
        )

        rows = test_db.query(VerifiedClaim).all()
        assert len(rows) == 1
        assert rows[0].verdict == "DISPUTED"


class TestFactCheckReadEndpoints:
    def _seed_claims(self, test_db):
        test_db.add_all(
            [
                FactCheckClaim(
                    subject_type="SUGGESTION",
                    subject_id=1,
                    claim_text="a",
                    claim_type="DATE",
                    verdict="VERIFIED",
                ),
                FactCheckClaim(
                    subject_type="SUGGESTION",
                    subject_id=1,
                    claim_text="b",
                    claim_type="DATE",
                    verdict="INACCURATE",
                    correction="fixed",
                ),
                FactCheckClaim(
                    subject_type="ARTICLE",
                    subject_id=9,
                    claim_text="c",
                    claim_type="EVENT",
                    verdict="UNVERIFIABLE",
                ),
            ]
        )
        test_db.flush()

    def test_summary_aggregates_per_subject(self, test_client, test_db):
        self._seed_claims(test_db)

        response = test_client.get(
            "/api/v1/admin/dashboard/fact-check/summary", params={"token": "any"}
        )

        assert response.status_code == 200
        items = {(i["subject_type"], i["subject_id"]): i["counts"] for i in response.json()}
        assert items[("SUGGESTION", 1)] == {"VERIFIED": 1, "INACCURATE": 1}
        assert items[("ARTICLE", 9)] == {"UNVERIFIABLE": 1}

    def test_detail_returns_claims(self, test_client, test_db):
        self._seed_claims(test_db)

        response = test_client.get(
            "/api/v1/admin/dashboard/fact-check/suggestion/1", params={"token": "any"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["subject_type"] == "SUGGESTION"
        assert data["total_claims"] == 2
        inaccurate = [c for c in data["claims"] if c["verdict"] == "INACCURATE"]
        assert inaccurate[0]["correction"] == "fixed"

    def test_detail_rejects_bad_subject_type(self, test_client):
        response = test_client.get(
            "/api/v1/admin/dashboard/fact-check/PODCAST/1", params={"token": "any"}
        )
        assert response.status_code == 400

    def test_detail_empty_subject(self, test_client):
        response = test_client.get(
            "/api/v1/admin/dashboard/fact-check/ARTICLE/12345", params={"token": "any"}
        )
        assert response.status_code == 200
        assert response.json()["total_claims"] == 0


class TestVerifiedClaimConstraints:
    def _kb_row(self, **overrides):
        base = {
            "claim_key": _normalize(overrides.get("claim_text", "some claim")),
            "claim_text": "some claim",
            "claim_type": "DATE",
            "verdict": "VERIFIED",
        }
        base.update(overrides)
        return VerifiedClaim(**base)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"claim_type": "VIBES"},
            {"verdict": "PROBABLY_FINE"},
            {"source_method": "ASKED_A_FRIEND"},
        ],
        ids=["claim_type", "verdict", "source_method"],
    )
    def test_invalid_value_rejected(self, test_db, overrides):
        test_db.add(self._kb_row(**overrides))
        with pytest.raises(IntegrityError):
            test_db.flush()

    def test_duplicate_claim_key_rejected(self, test_db):
        test_db.add(self._kb_row())
        test_db.flush()
        test_db.add(self._kb_row())
        with pytest.raises(IntegrityError):
            test_db.flush()


# --- Background fact-check job runner (#384) -------------------------------------


class TestRunFactCheckJob:
    @pytest.fixture
    def patched_session(self, test_engine):
        """Point the runner's own session factory at the test database."""
        factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
        with patch.object(fact_check_module, "SessionLocal", factory):
            yield

    def _job(self, test_db, subject_type, subject_id, mode=None):
        params = {"subject_type": subject_type, "subject_id": subject_id}
        if mode is not None:
            params["mode"] = mode
        job = CrawlJob(
            job_type=CrawlJobType.FACT_CHECK.value,
            params=params,
        )
        test_db.add(job)
        test_db.commit()
        return job.id

    @patch("app.services.llm.get_client")
    def test_suggestion_job_success(
        self, mock_get_client, test_db, category_factory, patched_session
    ):
        # Extract-only external tier: extraction only, every claim queued for
        # deep research.
        mock_get_client.return_value = _make_client(_msg(EXTRACTION_RESPONSE))
        article, research = _create_article_chain(test_db, category_factory)
        suggestion_id = research.suggestion_id
        test_db.commit()  # the runner uses its own session — make the chain visible
        job_id = self._job(test_db, "SUGGESTION", suggestion_id)

        run_fact_check_job(job_id)

        test_db.expire_all()
        job = test_db.get(CrawlJob, job_id)
        assert job.status == CrawlJobStatus.SUCCEEDED.value
        assert job.stats["total_claims"] == 3
        assert job.stats["counts"] == {"NEEDS_DEEP_RESEARCH": 3}
        assert job.units_done == 3
        assert job.units_total == 3
        rows = (
            test_db.query(FactCheckClaim)
            .filter(
                FactCheckClaim.subject_type == "SUGGESTION",
                FactCheckClaim.subject_id == suggestion_id,
            )
            .all()
        )
        assert len(rows) == 3

    @patch("app.services.llm.get_client")
    def test_article_grounding_job_success(
        self, mock_get_client, test_db, category_factory, patched_session
    ):
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE)
        )
        article, _ = _create_article_chain(test_db, category_factory)
        article_id = article.id
        test_db.commit()
        job_id = self._job(test_db, "ARTICLE", article_id)

        run_fact_check_job(job_id)

        test_db.expire_all()
        job = test_db.get(CrawlJob, job_id)
        assert job.status == CrawlJobStatus.SUCCEEDED.value
        assert job.stats["counts"] == {"VERIFIED": 1, "INACCURATE": 1, "NEEDS_DEEP_RESEARCH": 1}
        rows = (
            test_db.query(FactCheckClaim)
            .filter(
                FactCheckClaim.subject_type == "ARTICLE",
                FactCheckClaim.subject_id == article_id,
            )
            .all()
        )
        assert all(r.checked_via == "GROUNDING" for r in rows)

    @patch("app.services.llm.get_client")
    def test_article_grounding_kb_failure_still_succeeds(
        self, mock_get_client, test_db, category_factory, patched_session
    ):
        """#586: a failed KB Haiku call leaves the claim queued; the job
        does not fail after extract + judge were paid for."""
        article, _ = _create_article_chain(test_db, category_factory)
        article_id = article.id
        _seed_kb(
            test_db,
            "The pirate Henry Morgan attacked Panama Viejo in the year 1671",
            verdict="INACCURATE",
        )
        test_db.commit()
        # Extract and the judge stream (pop the queue in order); only the KB
        # match is a non-streamed create.
        client = _make_client(_msg(EXTRACTION_RESPONSE), _msg(GROUNDING_RESPONSE))
        client.messages.create.side_effect = [RuntimeError("haiku down")]
        mock_get_client.return_value = client
        job_id = self._job(test_db, "ARTICLE", article_id)

        with patch("app.services.fact_check.sentry_sdk.capture_exception") as capture:
            run_fact_check_job(job_id)

        capture.assert_called_once()
        test_db.expire_all()
        job = test_db.get(CrawlJob, job_id)
        assert job.status == CrawlJobStatus.SUCCEEDED.value
        assert job.stats["counts"] == {"VERIFIED": 1, "INACCURATE": 1, "NEEDS_DEEP_RESEARCH": 1}
        queued = (
            test_db.query(FactCheckClaim)
            .filter(
                FactCheckClaim.subject_type == "ARTICLE",
                FactCheckClaim.subject_id == article_id,
                FactCheckClaim.verdict == "NEEDS_DEEP_RESEARCH",
            )
            .one()
        )
        assert queued.checked_via == "GROUNDING"

    def test_missing_subject_records_failure(self, test_db, patched_session):
        job_id = self._job(test_db, "SUGGESTION", 999999)

        run_fact_check_job(job_id)

        test_db.expire_all()
        job = test_db.get(CrawlJob, job_id)
        assert job.status == CrawlJobStatus.FAILED.value
        assert "not found" in job.error
        assert job.finished_at is not None


# --- Extract-only deep-research-first mode (#389) --------------------------------


class TestExtractForDeepResearch:
    @patch("app.services.llm.get_client")
    def test_persists_needs_deep_research_rows(self, mock_get_client, test_db):
        # Empty KB → extraction is the ONLY LLM call; nothing is web-searched.
        client = _make_client(_msg(EXTRACTION_RESPONSE))
        mock_get_client.return_value = client

        summary = extract_for_deep_research(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)

        calls = client.messages.create.call_count + client.messages.stream.call_count
        assert calls == 1
        assert summary["total_claims"] == 3
        assert summary["counts"] == {"NEEDS_DEEP_RESEARCH": 3}
        rows = test_db.query(FactCheckClaim).filter(FactCheckClaim.subject_id == 42).all()
        assert all(r.verdict == "NEEDS_DEEP_RESEARCH" and r.checked_via is None for r in rows)

    @patch("app.services.llm.get_client")
    def test_kb_matches_inherit_verdict(self, mock_get_client, test_db):
        kb = _seed_kb(test_db, "Panama Viejo was founded in 1519", citation="https://kb.org/pv")
        mock_get_client.return_value = _make_client(
            _msg("CLAIM: DATE | Panama Viejo was founded in 1519")
        )

        summary = extract_for_deep_research(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)

        (row,) = summary["claims"]
        assert row.verdict == "VERIFIED"
        assert row.checked_via == "KB_MATCH"
        assert row.citation == kb.citation
        assert summary["counts"] == {"VERIFIED": 1}

    @patch("app.services.llm.get_client")
    def test_wholesale_replace_on_clean_rerun(self, mock_get_client, test_db):
        # No PENDING rows → a re-run re-extracts and replaces the rows wholesale.
        _seed_subject_rows(test_db, 42, ("Old resolved claim", "VERIFIED", "WEB_SEARCH"))
        mock_get_client.return_value = _make_client(_msg(EXTRACTION_RESPONSE))

        summary = extract_for_deep_research(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)

        assert summary["total_claims"] == 3
        texts = {
            r.claim_text
            for r in test_db.query(FactCheckClaim).filter(FactCheckClaim.subject_id == 42)
        }
        assert "Old resolved claim" not in texts

    def test_pending_flip_in_place_preserves_resolved(self, test_db):
        # Half-checked subject (PENDING rows left by the removed web-search
        # tier, #751). No client
        # patched at all — no re-extraction, and the empty KB costs no LLM call.
        resolved, pending_a, pending_b = _seed_subject_rows(
            test_db,
            42,
            ("Already verified claim", "VERIFIED", "WEB_SEARCH"),
            ("Pending claim about the canal", "PENDING", None),
            ("Pending claim about the railroad", "PENDING", None),
        )

        summary = extract_for_deep_research(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)

        # PENDING rows flipped in place — same rows, no delete/re-insert.
        assert pending_a.verdict == "NEEDS_DEEP_RESEARCH"
        assert pending_b.verdict == "NEEDS_DEEP_RESEARCH"
        assert pending_a.checked_via is None
        assert resolved.verdict == "VERIFIED"
        assert resolved.citation == "https://old.org"
        assert summary["total_claims"] == 3
        assert summary["counts"] == {"VERIFIED": 1, "NEEDS_DEEP_RESEARCH": 2}
        assert test_db.query(FactCheckClaim).filter(FactCheckClaim.subject_id == 42).count() == 3

    def test_pending_flip_inherits_from_kb(self, test_db):
        kb = _seed_kb(test_db, "Panama Viejo was founded in 1519", citation="https://kb.org/pv")
        pending_match, pending_other = _seed_subject_rows(
            test_db,
            42,
            ("Panama Viejo was founded in 1519", "PENDING", None),
            ("Some unrelated open question", "PENDING", None),
        )

        extract_for_deep_research(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)

        assert pending_match.verdict == "VERIFIED"
        assert pending_match.checked_via == "KB_MATCH"
        assert pending_match.citation == kb.citation
        assert pending_other.verdict == "NEEDS_DEEP_RESEARCH"


class TestLegacyModeParam:
    """Job rows created before #751 may carry ``mode`` in params; it is ignored."""

    @pytest.mark.parametrize("mode", [None, "deep_research", "web_search"])
    @patch("app.services.llm.get_client")
    def test_external_subject_always_runs_extract_only(
        self, mock_get_client, mode, test_db, category_factory
    ):
        client = _make_client(_msg(EXTRACTION_RESPONSE))
        mock_get_client.return_value = client
        article, research = _create_article_chain(test_db, category_factory)
        params = {"subject_type": "SUGGESTION", "subject_id": research.suggestion_id}
        if mode is not None:
            params["mode"] = mode

        stats = _run_fact_check_for_subject(test_db, SimpleNamespace(params=params), None)

        # Extraction is the only LLM call; nothing is web-searched.
        calls = client.messages.create.call_count + client.messages.stream.call_count
        assert calls == 1
        assert stats["counts"] == {"NEEDS_DEEP_RESEARCH": 3}
