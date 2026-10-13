"""Tests for the fact-check pipeline foundation + article grounding (#374/#375)."""

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
    fact_check_subject,
    ground_article,
    match_claims_against_kb,
    parse_claim_lines,
    run_fact_check_job,
    upsert_verified_claims,
    verify_claims_via_search,
)
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
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


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
        assert summary["unverifiable"] == 1

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
            FactCheckVerdict.UNVERIFIABLE.value,
        ]
        # CONTRADICTED note lands in correction; "-" notes are dropped.
        assert rows[0].correction is None
        assert "royal expedition" in rows[1].correction
        assert all(r.checked_via == "GROUNDING" for r in rows)
        assert all(r.checked_at is not None for r in rows)

    @patch("app.services.llm.get_client")
    def test_missing_verdict_degrades_to_unverifiable(
        self, mock_get_client, test_db, category_factory
    ):
        # Judge answers only claim 1 — the other two must NOT default to verified.
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg("VERDICT: 1 | SUPPORTED | -")
        )
        article, research = _create_article_chain(test_db, category_factory)

        summary = ground_article(article, research, test_db)

        assert summary["verified"] == 1
        assert summary["unverifiable"] == 2

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
    def test_duplicate_run_rejected(self, mock_run, test_client, test_db, category_factory):
        article, _ = _create_article_chain(test_db, category_factory)
        test_db.add(
            CrawlJob(
                job_type=CrawlJobType.FACT_CHECK.value,
                status=CrawlJobStatus.RUNNING.value,
                params={"subject_type": "ARTICLE", "subject_id": article.id},
            )
        )
        test_db.commit()

        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{article.id}/fact-check",
            params={"token": "any"},
        )
        assert response.status_code == 409
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


# --- Web-search verification tier (#376) ---------------------------------------


SEARCH_RESPONSE = """\
VERDICT: 1 | VERIFIED | - | https://en.wikipedia.org/wiki/Panama_Viejo
VERDICT: 2 | INACCURATE | The city was founded by a royal expedition led by Pedrarias | https://example.org/history
VERDICT: 3 | DISPUTED | Sources disagree on the exact date of the attack | https://a.org, https://b.org"""


def _three_claims():
    return [
        ExtractedClaim("Panama Viejo was founded in 1519", "DATE"),
        ExtractedClaim("Pedrarias Davila founded Panama Viejo", "PERSON"),
        ExtractedClaim("Henry Morgan attacked Panama Viejo in 1671", "EVENT"),
    ]


class TestVerifyClaimsViaSearch:
    @patch("app.services.llm.get_client")
    def test_maps_verdicts_and_citations(self, mock_get_client):
        mock_get_client.return_value = _make_client(_msg(SEARCH_RESPONSE))

        results = verify_claims_via_search(_three_claims())

        assert [r[0] for r in results] == ["VERIFIED", "INACCURATE", "DISPUTED"]
        assert results[0][2] == "https://en.wikipedia.org/wiki/Panama_Viejo"
        assert "royal expedition" in results[1][1]

    @patch("app.services.llm.get_client")
    def test_missing_verdict_punts_to_deep_research(self, mock_get_client):
        # Only claim 1 answered — the rest must punt, never default to verified.
        mock_get_client.return_value = _make_client(
            _msg("VERDICT: 1 | VERIFIED | - | https://x.org")
        )

        results = verify_claims_via_search(_three_claims())

        assert results[0][0] == "VERIFIED"
        assert results[1][0] == "NEEDS_DEEP_RESEARCH"
        assert results[2][0] == "NEEDS_DEEP_RESEARCH"

    @patch("app.services.llm.get_client")
    def test_uncited_definitive_verdict_downgraded(self, mock_get_client):
        # VERIFIED/INACCURATE without a source URL must not survive.
        mock_get_client.return_value = _make_client(
            _msg(
                "VERDICT: 1 | VERIFIED | - | -\n"
                "VERDICT: 2 | INACCURATE | wrong founder | -\n"
                "VERDICT: 3 | DISPUTED | positions differ | -"
            )
        )

        results = verify_claims_via_search(_three_claims())

        assert results[0][0] == "NEEDS_DEEP_RESEARCH"
        assert results[1][0] == "NEEDS_DEEP_RESEARCH"
        # DISPUTED does not require a citation to survive.
        assert results[2][0] == "DISPUTED"

    @patch("app.services.llm.get_client")
    def test_batches_claims(self, mock_get_client):
        # 10 claims > SEARCH_BATCH_SIZE (8) -> two search calls.
        client = _make_client(
            _msg("VERDICT: 1 | VERIFIED | - | https://x.org"),
            _msg("VERDICT: 1 | VERIFIED | - | https://y.org"),
        )
        mock_get_client.return_value = client

        claims = [ExtractedClaim(f"Claim number {i}", "OTHER") for i in range(10)]
        results = verify_claims_via_search(claims)

        calls = client.messages.create.call_count + client.messages.stream.call_count
        assert calls == 2
        # First claim of each batch verified; everything else punts.
        assert results[0][0] == "VERIFIED"
        assert results[8][0] == "VERIFIED"
        assert results[1][0] == "NEEDS_DEEP_RESEARCH"

    @patch("app.services.llm.get_client")
    def test_search_call_cost_controls(self, mock_get_client):
        # Cost trims (post-#384): low effort, 8 searches/call, grouping prompt.
        client = _make_client(_msg(SEARCH_RESPONSE))
        mock_get_client.return_value = client

        verify_claims_via_search(_three_claims())

        kwargs = client.messages.stream.call_args.kwargs
        assert kwargs["output_config"] == {"effort": "low"}
        assert kwargs["tools"][0]["max_uses"] == 8
        assert "single broader query" in kwargs["messages"][0]["content"]

    @patch("app.services.llm.get_client")
    def test_pause_turn_is_resumed(self, mock_get_client):
        paused = _msg("searching...")
        paused.stop_reason = "pause_turn"
        final = _msg("VERDICT: 1 | VERIFIED | - | https://x.org")
        final.stop_reason = "end_turn"
        client = _make_client(paused, final)
        mock_get_client.return_value = client

        results = verify_claims_via_search([_three_claims()[0]])

        calls = client.messages.create.call_count + client.messages.stream.call_count
        assert calls == 2
        assert results[0][0] == "VERIFIED"


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


class TestFactCheckSubject:
    @patch("app.services.llm.get_client")
    def test_persists_web_search_rows(self, mock_get_client, test_db):
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(SEARCH_RESPONSE)
        )

        summary = fact_check_subject(
            FactCheckSubject.SUGGESTION.value, 42, "Some suggestion text", test_db
        )

        assert summary["total_claims"] == 3
        assert summary["counts"] == {"VERIFIED": 1, "INACCURATE": 1, "DISPUTED": 1}
        rows = (
            test_db.query(FactCheckClaim)
            .filter(
                FactCheckClaim.subject_type == FactCheckSubject.SUGGESTION.value,
                FactCheckClaim.subject_id == 42,
            )
            .order_by(FactCheckClaim.id)
            .all()
        )
        assert [r.verdict for r in rows] == ["VERIFIED", "INACCURATE", "DISPUTED"]
        assert all(r.checked_via == "WEB_SEARCH" for r in rows)
        assert rows[0].citation == "https://en.wikipedia.org/wiki/Panama_Viejo"
        assert rows[0].correction is None  # "-" notes are dropped

    @patch("app.services.fact_check.MAX_SEARCH_CLAIMS_PER_RUN", 2)
    @patch("app.services.llm.get_client")
    def test_overflow_claims_persist_as_pending(self, mock_get_client, test_db):
        # 3 extracted claims, cap of 2 -> third row stays PENDING, unchecked.
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE),
            _msg(
                "VERDICT: 1 | VERIFIED | - | https://x.org\n"
                "VERDICT: 2 | VERIFIED | - | https://y.org"
            ),
        )

        summary = fact_check_subject(
            FactCheckSubject.RESEARCH.value, 7, "Some research text", test_db
        )

        assert summary["total_claims"] == 3
        assert summary["counts"] == {"VERIFIED": 2, "PENDING": 1}
        pending = [c for c in summary["claims"] if c.verdict == "PENDING"]
        assert len(pending) == 1
        assert pending[0].checked_via is None

    @patch("app.services.llm.get_client")
    def test_rerun_replaces_previous_rows(self, mock_get_client, test_db):
        one_claim = "CLAIM: DATE | Panama Viejo was founded in 1519"
        one_verdict = "VERDICT: 1 | VERIFIED | - | https://x.org"
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE),
            _msg(SEARCH_RESPONSE),
            _msg(one_claim),
            _msg(one_verdict),
        )

        fact_check_subject(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)
        summary = fact_check_subject(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)

        assert summary["total_claims"] == 1
        count = test_db.query(FactCheckClaim).filter(FactCheckClaim.subject_id == 42).count()
        assert count == 1


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


class TestResumePendingFactCheck:
    @patch("app.services.llm.get_client")
    def test_resume_verifies_only_pending_rows_in_place(self, mock_get_client, test_db):
        resolved, pending_a, pending_b = _seed_subject_rows(
            test_db,
            42,
            ("Already verified claim", "VERIFIED", "WEB_SEARCH"),
            ("Pending claim about the canal", "PENDING", None),
            ("Pending claim about the railroad", "PENDING", None),
        )
        # ONE search call, NO extraction call — resume skips it entirely.
        client = _make_client(
            _msg(
                "VERDICT: 1 | VERIFIED | - | https://new.org\n"
                "VERDICT: 2 | INACCURATE | actually 1855 | https://rr.org"
            )
        )
        mock_get_client.return_value = client

        summary = fact_check_subject(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)

        calls = client.messages.create.call_count + client.messages.stream.call_count
        assert calls == 1
        # Rows updated in place — same ids, no delete/re-insert.
        assert pending_a.verdict == "VERIFIED"
        assert pending_a.citation == "https://new.org"
        assert pending_a.checked_via == "WEB_SEARCH"
        assert pending_b.verdict == "INACCURATE"
        assert pending_b.correction == "actually 1855"
        # The already-resolved row is untouched.
        assert resolved.citation == "https://old.org"
        # Summary reflects the WHOLE subject, not just the resumed rows.
        assert summary["total_claims"] == 3
        assert summary["counts"] == {"VERIFIED": 2, "INACCURATE": 1}
        assert test_db.query(FactCheckClaim).filter(FactCheckClaim.subject_id == 42).count() == 3

    def test_resume_inherits_from_kb_without_search(self, test_db):
        # Exact-key KB hit resolves the only PENDING row — zero LLM calls
        # (no client patched at all; any call would raise).
        kb = _seed_kb(test_db, "Panama Viejo was founded in 1519", citation="https://kb.org/pv")
        (pending,) = _seed_subject_rows(
            test_db, 42, ("Panama Viejo was founded in 1519", "PENDING", None)
        )

        summary = fact_check_subject(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)

        assert pending.verdict == "VERIFIED"
        assert pending.checked_via == "KB_MATCH"
        assert pending.citation == kb.citation
        assert summary["counts"] == {"VERIFIED": 1}

    @patch("app.services.fact_check.MAX_SEARCH_CLAIMS_PER_RUN", 1)
    @patch("app.services.llm.get_client")
    def test_resume_respects_per_run_cap(self, mock_get_client, test_db):
        pending_a, pending_b = _seed_subject_rows(
            test_db,
            42,
            ("First pending claim", "PENDING", None),
            ("Second pending claim", "PENDING", None),
        )
        mock_get_client.return_value = _make_client(
            _msg("VERDICT: 1 | VERIFIED | - | https://x.org")
        )

        summary = fact_check_subject(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)

        assert pending_a.verdict == "VERIFIED"
        assert pending_b.verdict == "PENDING"  # overflow survives for the next resume
        assert summary["counts"] == {"VERIFIED": 1, "PENDING": 1}

    @patch("app.services.llm.get_client")
    def test_resume_feeds_search_verdicts_to_kb(self, mock_get_client, test_db):
        _seed_subject_rows(test_db, 42, ("A pending claim", "PENDING", None))
        mock_get_client.return_value = _make_client(
            _msg("VERDICT: 1 | VERIFIED | - | https://x.org")
        )

        fact_check_subject(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)

        assert test_db.query(VerifiedClaim).count() == 1

    @patch("app.services.llm.get_client")
    def test_needs_deep_research_rows_do_not_trigger_resume(self, mock_get_client, test_db):
        # Only PENDING resumes — NEEDS_DEEP_RESEARCH belongs to the manual
        # tier, so a re-run with none pending re-extracts wholesale.
        _seed_subject_rows(
            test_db, 42, ("Old deep-research claim", "NEEDS_DEEP_RESEARCH", "WEB_SEARCH")
        )
        mock_get_client.return_value = _make_client(
            _msg("CLAIM: DATE | Panama Viejo was founded in 1519"),
            _msg("VERDICT: 1 | VERIFIED | - | https://x.org"),
        )

        summary = fact_check_subject(FactCheckSubject.SUGGESTION.value, 42, "text", test_db)

        # Wholesale replacement: the old row is gone, one fresh row remains.
        assert summary["total_claims"] == 1
        assert summary["counts"] == {"VERIFIED": 1}
        assert test_db.query(FactCheckClaim).filter(FactCheckClaim.subject_id == 42).count() == 1


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
        # deep_research (extract-only) is the default mode (#389).
        assert job.params == {
            "subject_type": "SUGGESTION",
            "subject_id": suggestion_id,
            "mode": "deep_research",
        }

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
        assert job.params == {
            "subject_type": "RESEARCH",
            "subject_id": research.id,
            "mode": "deep_research",
        }

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
    def test_web_search_mode_stored_in_params(
        self, mock_run, test_client, test_db, category_factory
    ):
        # The API-only escape hatch (#389): mode=web_search rides in job params.
        article, research = _create_article_chain(test_db, category_factory)

        response = test_client.post(
            f"/api/v1/admin/dashboard/research/{research.id}/fact-check",
            params={"token": "any", "mode": "web_search"},
        )

        assert response.status_code == 202
        job = test_db.get(CrawlJob, response.json()["id"])
        assert job.params["mode"] == "web_search"

    def test_invalid_mode_rejected(self, test_client, test_db, category_factory):
        article, research = _create_article_chain(test_db, category_factory)

        response = test_client.post(
            f"/api/v1/admin/dashboard/research/{research.id}/fact-check",
            params={"token": "any", "mode": "bogus"},
        )
        assert response.status_code == 422


# --- Verified-claims knowledge base (#378) ---------------------------------------


def _seed_kb(test_db, text, *, verdict="VERIFIED", correction=None, citation="https://kb.org"):
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


class TestFactCheckSubjectWithKb:
    @patch("app.services.llm.get_client")
    def test_kb_matches_inherit_verdict_and_skip_search(self, mock_get_client, test_db):
        kb = _seed_kb(
            test_db,
            "Panama Viejo was founded in 1519",
            verdict="VERIFIED",
            citation="https://kb.org/pv",
        )
        # Calls: extraction, KB fuzzy confirm (claims 2+3 share words with the
        # KB row), then ONE search batch covering only the two unmatched claims.
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE),
            _msg("MATCH: 1 | NONE\nMATCH: 2 | NONE"),
            _msg(
                "VERDICT: 1 | VERIFIED | - | https://a.org\n"
                "VERDICT: 2 | VERIFIED | - | https://b.org"
            ),
        )

        summary = fact_check_subject("SUGGESTION", 42, "text", test_db)

        assert summary["total_claims"] == 3
        assert summary["counts"] == {"VERIFIED": 3}
        kb_rows = [c for c in summary["claims"] if c.checked_via == "KB_MATCH"]
        assert len(kb_rows) == 1
        assert kb_rows[0].citation == kb.citation
        searched = [c for c in summary["claims"] if c.checked_via == "WEB_SEARCH"]
        assert len(searched) == 2

    @patch("app.services.llm.get_client")
    def test_search_verdicts_feed_the_kb(self, mock_get_client, test_db):
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(SEARCH_RESPONSE)
        )

        fact_check_subject("SUGGESTION", 42, "text", test_db)

        # VERIFIED + INACCURATE + DISPUTED all land in the KB.
        assert test_db.query(VerifiedClaim).count() == 3


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
        # No mode in params (also the pre-#389 job shape) → deep_research
        # default: extraction only, every claim queued for deep research.
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

    @patch("app.services.fact_check.MAX_SEARCH_CLAIMS_PER_RUN", 1)
    @patch("app.services.llm.get_client")
    def test_job_runs_uncapped(self, mock_get_client, test_db, category_factory, patched_session):
        # The sync-era per-run cap must not apply to jobs: all 3 claims are
        # searched (in one batch) and nothing is left PENDING.
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(SEARCH_RESPONSE)
        )
        article, research = _create_article_chain(test_db, category_factory)
        suggestion_id = research.suggestion_id
        test_db.commit()
        job_id = self._job(test_db, "SUGGESTION", suggestion_id, mode="web_search")

        run_fact_check_job(job_id)

        test_db.expire_all()
        job = test_db.get(CrawlJob, job_id)
        assert job.status == CrawlJobStatus.SUCCEEDED.value
        assert "PENDING" not in job.stats["counts"]
        assert job.stats["total_claims"] == 3

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
        assert job.stats["counts"] == {"VERIFIED": 1, "INACCURATE": 1, "UNVERIFIABLE": 1}
        rows = (
            test_db.query(FactCheckClaim)
            .filter(
                FactCheckClaim.subject_type == "ARTICLE",
                FactCheckClaim.subject_id == article_id,
            )
            .all()
        )
        assert all(r.checked_via == "GROUNDING" for r in rows)

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
        # Half-checked subject (a capped earlier web-search run). No client
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


class TestFactCheckModeDispatch:
    def _job(self, subject_id, mode=None):
        params = {"subject_type": "SUGGESTION", "subject_id": subject_id}
        if mode is not None:
            params["mode"] = mode
        return SimpleNamespace(params=params)

    @patch("app.services.llm.get_client")
    def test_default_mode_is_deep_research(self, mock_get_client, test_db, category_factory):
        # No mode in params (pre-#389 jobs included) → extract-only, no search.
        client = _make_client(_msg(EXTRACTION_RESPONSE))
        mock_get_client.return_value = client
        article, research = _create_article_chain(test_db, category_factory)

        stats = _run_fact_check_for_subject(test_db, self._job(research.suggestion_id), None)

        calls = client.messages.create.call_count + client.messages.stream.call_count
        assert calls == 1
        assert stats["counts"] == {"NEEDS_DEEP_RESEARCH": 3}

    @patch("app.services.llm.get_client")
    def test_web_search_mode_still_runs_search_tier(
        self, mock_get_client, test_db, category_factory
    ):
        mock_get_client.return_value = _make_client(
            _msg(EXTRACTION_RESPONSE), _msg(SEARCH_RESPONSE)
        )
        article, research = _create_article_chain(test_db, category_factory)

        stats = _run_fact_check_for_subject(
            test_db, self._job(research.suggestion_id, mode="web_search"), None
        )

        assert stats["counts"] == {"VERIFIED": 1, "INACCURATE": 1, "DISPUTED": 1}
        rows = (
            test_db.query(FactCheckClaim)
            .filter(
                FactCheckClaim.subject_type == "SUGGESTION",
                FactCheckClaim.subject_id == research.suggestion_id,
            )
            .all()
        )
        assert all(r.checked_via == "WEB_SEARCH" for r in rows)

    def test_unknown_mode_raises(self, test_db, category_factory):
        article, research = _create_article_chain(test_db, category_factory)

        with pytest.raises(ValueError, match="Unknown fact-check mode"):
            _run_fact_check_for_subject(
                test_db, self._job(research.suggestion_id, mode="bogus"), None
            )
