"""Tests for the weekly-series driver surface (#418).

Covers the kind -> calls mapping (exhaustively), leasing and lease expiry, the
dependency promotion that *is* the scheduler, the report protocol, and the
driver service token. Promotion gets the most attention on purpose: the issue
names it as the place where "a subtle bug stalls a whole week silently".
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import get_settings
from app.models.agent_task import AgentTask
from app.models.crawl_job import CrawlJob
from app.models.enums import AgentTaskKind, AgentTaskStatus, AgentTaskSubject
from app.models.fact_check import FactCheckClaim
from app.services.agent_task_driver import (
    AGENT_TASKS_PREFIX,
    KIND_CALLS,
    WAIT_CONDITIONS,
    _select_next_for_update,
    _wait_check,
    build_calls,
    claim_next_task,
    is_driver_call_allowed,
)
from sqlalchemy.orm import sessionmaker

BASE = "/api/v1/admin/dashboard/agent-tasks"
NEXT_URL = f"{BASE}/next"
GOAL = "weekly_series:2026-W31"
DRIVER_TOKEN = "test-driver-token"


@pytest.fixture
def driver_settings(monkeypatch):
    """Enable the driver token and pin the lease knobs for the whole test.

    ``get_settings`` is ``lru_cache``d, so patching attributes on the singleton
    is the supported override and monkeypatch restores them afterwards.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_driver_token", DRIVER_TOKEN)
    monkeypatch.setattr(settings, "agent_task_lease_minutes", 30)
    monkeypatch.setattr(settings, "agent_task_max_attempts", 3)
    return settings


@pytest.fixture
def driver_client(test_client, driver_settings):
    """A client authenticated with the driver service token (no admin fixture)."""
    test_client.headers["Authorization"] = f"Bearer {DRIVER_TOKEN}"
    return test_client


def make_task(
    test_db,
    kind=AgentTaskKind.GENERATE_TAGS,
    *,
    subject=AgentTaskSubject.ARTICLE,
    subject_id=1,
    status=AgentTaskStatus.READY,
    depends_on=None,
    position=10,
    payload=None,
    attempts=0,
    goal_key=GOAL,
    lease_expires_at=None,
    leased_by=None,
):
    task = AgentTask(
        goal_key=goal_key,
        kind=kind.value,
        subject_type=subject.value,
        subject_id=subject_id,
        status=status.value,
        depends_on=depends_on,
        position=position,
        payload=payload,
        attempts=attempts,
        lease_expires_at=lease_expires_at,
        leased_by=leased_by,
    )
    test_db.add(task)
    test_db.commit()
    test_db.refresh(task)
    return task


def spec_for(kind, subject_id=7, payload=None, subject=AgentTaskSubject.ARTICLE):
    """Render calls for an unsaved task row — build_calls needs no DB."""
    task = AgentTask(
        goal_key=GOAL,
        kind=kind.value,
        subject_type=subject.value,
        subject_id=subject_id,
        status=AgentTaskStatus.READY.value,
        position=10,
        payload=payload,
    )
    return build_calls(task)


# --- The mapping ------------------------------------------------------------


def test_kind_calls_covers_every_kind():
    """A new AgentTaskKind cannot ship without deciding what it calls."""
    assert set(KIND_CALLS) == {k.value for k in AgentTaskKind}


def test_every_rendered_path_is_absolute():
    for kind in AgentTaskKind:
        payload = {"target_publish_date": "2026-08-07", "target_date": "2026-08-08"}
        for call in spec_for(kind, payload=payload):
            assert call.path.startswith("/api/v1/"), f"{kind.value}: {call.path}"
            assert call.method in {"GET", "POST", "PATCH", "PUT", "DELETE"}


def test_every_rendered_call_is_driver_authorized():
    """A new kind cannot ship a call the driver token cannot execute (#444)."""
    payload = {
        "target_publish_date": "2026-08-07",
        "target_date": "2026-08-08",
        "week_start": "2026-08-03",
    }
    for kind in AgentTaskKind:
        for call in spec_for(kind, payload=payload):
            assert call.path.startswith(AGENT_TASKS_PREFIX) or is_driver_call_allowed(
                call.method, call.path
            ), f"{kind.value}: {call.method} {call.path} not driver-authorized"


def test_grammar_check_language_query():
    en = spec_for(AgentTaskKind.GRAMMAR_CHECK_EN, subject_id=12)
    es = spec_for(AgentTaskKind.GRAMMAR_CHECK_ES, subject_id=12)
    assert en[0].query == {"language": "en"}
    assert es[0].query == {"language": "es"}
    # The grammar-check endpoint lives on the /admin router, not /admin/dashboard.
    assert en[0].path == "/api/v1/admin/articles/12/grammar-check"


def test_es_accuracy_check_call():
    """#557: one POST on the /admin router, no query or body."""
    calls = spec_for(AgentTaskKind.ES_ACCURACY_CHECK, subject_id=12)
    assert len(calls) == 1
    assert calls[0].method == "POST"
    assert calls[0].path == "/api/v1/admin/articles/12/es-accuracy-check"
    assert calls[0].body is None
    assert is_driver_call_allowed("POST", calls[0].path)
    assert not is_driver_call_allowed("GET", calls[0].path)


def test_set_publish_date_sends_no_date():
    """#558: the server picks the slot when the step runs; a legacy row's
    planned target_publish_date is ignored."""
    for payload in (None, {"target_publish_date": "2026-08-07"}):
        calls = spec_for(AgentTaskKind.SET_PUBLISH_DATE, subject_id=5, payload=payload)
        assert calls[0].method == "PATCH"
        assert calls[0].path == "/api/v1/admin/dashboard/articles/5/publish"
        assert calls[0].body is None
        assert is_driver_call_allowed("PATCH", calls[0].path)


def test_schedule_ig_post_builds_datetime_from_payload():
    calls = spec_for(
        AgentTaskKind.SCHEDULE_IG_POST,
        subject_id=3,
        payload={"target_date": "2026-08-08"},
        subject=AgentTaskSubject.INSTAGRAM_POST,
    )
    assert calls[0].body == {"scheduled_at": "2026-08-08T17:00:00Z"}


def test_schedule_ig_post_without_target_date_sends_no_body():
    """#576: an article-promo row carries no date — the endpoint derives it
    from the article's published_at — and the call stays driver-authorized."""
    for payload in (None, {}):
        calls = spec_for(
            AgentTaskKind.SCHEDULE_IG_POST,
            subject_id=3,
            payload=payload,
            subject=AgentTaskSubject.INSTAGRAM_POST,
        )
        assert calls[0].method == "PATCH"
        assert calls[0].path == "/api/v1/admin/dashboard/instagram/posts/3/schedule"
        assert calls[0].body is None
        assert is_driver_call_allowed("PATCH", calls[0].path)


def test_instagram_publish_and_verify_are_not_driver_calls():
    """#576: the driver token can never publish or reach verify."""
    assert not is_driver_call_allowed(
        "POST", "/api/v1/admin/dashboard/instagram/posts/3/publish-now"
    )
    assert not is_driver_call_allowed("POST", "/api/v1/admin/dashboard/instagram/verify")


def test_plan_articles_carries_week_start_and_omits_it_when_legacy():
    """Payloads without goal_key (pre-#573) get the row's column (#587)."""
    with_anchor = spec_for(
        AgentTaskKind.PLAN_ARTICLES,
        subject_id=2,
        payload={"expands": "x", "week_start": "2026-08-01"},
        subject=AgentTaskSubject.RESEARCH,
    )
    assert with_anchor[0].body == {"week_start": "2026-08-01", "goal_key": GOAL}
    # Rows planned before #418 have no anchor: omit it rather than guess, but
    # still name the goal from the row's own goal_key column.
    legacy = spec_for(
        AgentTaskKind.PLAN_ARTICLES,
        subject_id=2,
        payload={"expands": "x"},
        subject=AgentTaskSubject.RESEARCH,
    )
    assert legacy[0].body == {"goal_key": GOAL}


def test_plan_articles_payload_goal_key_wins_over_the_column():
    """#587: the column is only a fallback; a payload goal_key is sent as is."""
    other = "daily_series:2026-07-30"
    calls = spec_for(
        AgentTaskKind.PLAN_ARTICLES,
        subject_id=2,
        payload={"expands": "x", "goal_key": other},
        subject=AgentTaskSubject.RESEARCH,
    )
    assert calls[0].body == {"goal_key": other}


def test_plan_articles_omits_an_odd_shaped_column_goal_key():
    """#587: a column plan-week would 422 on is left out (old behaviour)."""
    task = AgentTask(
        goal_key="legacy-odd",
        kind=AgentTaskKind.PLAN_ARTICLES.value,
        subject_type=AgentTaskSubject.RESEARCH.value,
        subject_id=2,
        status=AgentTaskStatus.READY.value,
        position=10,
        payload={"expands": "x"},
    )
    assert build_calls(task)[0].body is None


def test_build_calls_does_not_mutate_the_row_payload():
    """#587: the goal_key fallback goes into a copy, never the row's JSONB."""
    payload = {"expands": "x"}
    task = AgentTask(
        goal_key=GOAL,
        kind=AgentTaskKind.PLAN_ARTICLES.value,
        subject_type=AgentTaskSubject.RESEARCH.value,
        subject_id=2,
        status=AgentTaskStatus.READY.value,
        position=10,
        payload=payload,
    )
    assert build_calls(task)[0].body == {"goal_key": GOAL}
    assert task.payload == {"expands": "x"}
    assert payload == {"expands": "x"}


def test_plan_articles_carries_the_goal_key():
    """#573: the goal key rides along so plan-week cannot recompute another."""
    calls = spec_for(
        AgentTaskKind.PLAN_ARTICLES,
        subject_id=2,
        payload={"expands": "x", "week_start": "2026-08-01", "goal_key": GOAL},
        subject=AgentTaskSubject.RESEARCH,
    )
    assert calls[0].body == {"week_start": "2026-08-01", "goal_key": GOAL}


def test_feature_image_calls_the_gemini_endpoint():
    calls = spec_for(AgentTaskKind.GENERATE_FEATURE_IMAGE, subject_id=7)
    assert len(calls) == 1
    assert calls[0].method == "POST"
    assert calls[0].path == "/api/v1/admin/dashboard/articles/7/generate-image"


def test_two_step_kinds_render_both_calls():
    fact_check = spec_for(AgentTaskKind.RESEARCH_FACT_CHECK, subject_id=4)
    assert [c.path for c in fact_check] == [
        "/api/v1/admin/dashboard/research/4/fact-check",
        f"{BASE}/jobs/{{job_id}}",
    ]
    corrections = spec_for(AgentTaskKind.APPLY_ARTICLE_CORRECTIONS, subject_id=4)
    assert [c.path for c in corrections] == [
        "/api/v1/admin/dashboard/fact-check/ARTICLE/4/corrections/preview",
        "/api/v1/admin/dashboard/fact-check/ARTICLE/4/corrections/apply",
    ]
    write = spec_for(AgentTaskKind.WRITE_ARTICLE, subject_id=4)
    assert write[0].body == {"status": "APPROVED"}
    assert write[1].path == "/api/v1/admin/dashboard/articles/4/write"


# --- GET /next --------------------------------------------------------------


def test_next_claims_lowest_position_ready_task(driver_client, test_db):
    later = make_task(test_db, position=20, subject_id=2)
    first = make_task(test_db, position=10, subject_id=1)
    make_task(test_db, position=5, subject_id=3, status=AgentTaskStatus.PENDING)

    data = driver_client.get(NEXT_URL).json()

    assert data["task"]["id"] == first.id
    assert data["task"]["status"] == AgentTaskStatus.LEASED.value
    assert data["task"]["attempts"] == 1
    assert data["task"]["leased_by"] == "agent-driver"
    assert data["task"]["lease_expires_at"] is not None
    assert data["calls"][0]["path"].endswith("/generate-tags")

    test_db.expire_all()
    assert test_db.get(AgentTask, later.id).status == AgentTaskStatus.READY.value


def test_next_returns_204_when_nothing_ready(driver_client, test_db):
    make_task(test_db, status=AgentTaskStatus.PENDING)
    make_task(test_db, subject_id=2, status=AgentTaskStatus.DONE)
    assert driver_client.get(NEXT_URL).status_code == 204


def test_next_sweeps_expired_lease_and_reclaims(driver_client, test_db):
    stale = make_task(
        test_db,
        status=AgentTaskStatus.LEASED,
        attempts=1,
        leased_by="dead-driver",
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    data = driver_client.get(NEXT_URL).json()

    assert data["task"]["id"] == stale.id
    assert data["task"]["attempts"] == 2
    assert data["task"]["leased_by"] == "agent-driver"


def test_expired_lease_at_attempt_ceiling_fails(driver_client, test_db):
    stale = make_task(
        test_db,
        status=AgentTaskStatus.LEASED,
        attempts=3,
        leased_by="dead-driver",
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    assert driver_client.get(NEXT_URL).status_code == 204

    test_db.expire_all()
    swept = test_db.get(AgentTask, stale.id)
    assert swept.status == AgentTaskStatus.FAILED.value
    assert swept.error == "lease expired; attempts exhausted"
    assert swept.leased_by is None
    assert swept.lease_expires_at is None


# --- Promotion (the scheduler) ---------------------------------------------


def report(client, task_id, status, **body):
    return client.patch(f"{BASE}/{task_id}", json={"status": status, **body})


def test_done_promotes_single_dependent(driver_client, test_db):
    first = make_task(test_db, position=10)
    second = make_task(
        test_db,
        kind=AgentTaskKind.TRANSLATE,
        position=20,
        status=AgentTaskStatus.PENDING,
        depends_on=[first.id],
    )
    driver_client.get(NEXT_URL)

    data = report(driver_client, first.id, "DONE", result={"ok": True}).json()

    assert data["promoted"] == [second.id]
    assert data["task"]["status"] == AgentTaskStatus.DONE.value
    test_db.expire_all()
    assert test_db.get(AgentTask, second.id).status == AgentTaskStatus.READY.value
    assert test_db.get(AgentTask, first.id).result == {"ok": True}


def test_skipped_promotes_too(driver_client, test_db):
    """The planner creates SKIPPED rows on purpose — they must not block."""
    first = make_task(test_db, position=10)
    second = make_task(
        test_db,
        kind=AgentTaskKind.TRANSLATE,
        position=20,
        status=AgentTaskStatus.PENDING,
        depends_on=[first.id],
    )
    driver_client.get(NEXT_URL)

    data = report(driver_client, first.id, "SKIPPED").json()

    assert data["task"]["status"] == AgentTaskStatus.SKIPPED.value
    assert data["promoted"] == [second.id]


@pytest.mark.parametrize("second_outcome", ["DONE", "SKIPPED"])
def test_diamond_dependency_waits_for_all_parents(driver_client, test_db, second_outcome):
    left = make_task(test_db, position=10, subject_id=1)
    right = make_task(test_db, kind=AgentTaskKind.TRANSLATE, position=20, subject_id=1)
    joined = make_task(
        test_db,
        kind=AgentTaskKind.APPROVE_ARTICLE,
        position=30,
        subject_id=1,
        status=AgentTaskStatus.PENDING,
        depends_on=[left.id, right.id],
    )
    driver_client.get(NEXT_URL)

    # One parent done: the join still waits.
    assert report(driver_client, left.id, "DONE").json()["promoted"] == []
    test_db.expire_all()
    assert test_db.get(AgentTask, joined.id).status == AgentTaskStatus.PENDING.value

    driver_client.get(NEXT_URL)
    assert report(driver_client, right.id, second_outcome).json()["promoted"] == [joined.id]
    test_db.expire_all()
    assert test_db.get(AgentTask, joined.id).status == AgentTaskStatus.READY.value


def test_promotion_is_scoped_to_the_goal(driver_client, test_db):
    """A dependent in another goal is never touched (ids are goal-internal)."""
    first = make_task(test_db, position=10)
    other_goal = make_task(
        test_db,
        kind=AgentTaskKind.TRANSLATE,
        position=20,
        status=AgentTaskStatus.PENDING,
        depends_on=[first.id],
        goal_key="weekly_series:2026-W32",
    )
    driver_client.get(NEXT_URL)

    assert report(driver_client, first.id, "DONE").json()["promoted"] == []
    test_db.expire_all()
    assert test_db.get(AgentTask, other_goal.id).status == AgentTaskStatus.PENDING.value


# --- FAILED -----------------------------------------------------------------


def test_failed_under_ceiling_requeues(driver_client, test_db):
    task = make_task(test_db)
    dependent = make_task(
        test_db,
        kind=AgentTaskKind.TRANSLATE,
        position=20,
        status=AgentTaskStatus.PENDING,
        depends_on=[task.id],
    )
    driver_client.get(NEXT_URL)

    data = report(driver_client, task.id, "FAILED", error="boom").json()

    assert data["task"]["status"] == AgentTaskStatus.READY.value
    assert data["task"]["error"] == "boom"
    assert data["task"]["leased_by"] is None
    assert data["promoted"] == []
    test_db.expire_all()
    # Never cascade-fail: a human may fix the step by hand.
    assert test_db.get(AgentTask, dependent.id).status == AgentTaskStatus.PENDING.value


def test_failed_at_ceiling_stays_failed(driver_client, test_db):
    task = make_task(test_db, attempts=2)  # claim below makes it 3 == ceiling
    driver_client.get(NEXT_URL)

    data = report(driver_client, task.id, "FAILED", error="boom").json()

    assert data["task"]["attempts"] == 3
    assert data["task"]["status"] == AgentTaskStatus.FAILED.value


# --- Report protocol --------------------------------------------------------


def test_report_409_on_pending_task(driver_client, test_db):
    task = make_task(test_db, status=AgentTaskStatus.PENDING)
    response = report(driver_client, task.id, "DONE")
    assert response.status_code == 409
    assert "PENDING" in response.json()["detail"]


def test_report_409_on_terminal_task(driver_client, test_db):
    task = make_task(test_db, status=AgentTaskStatus.DONE)
    assert report(driver_client, task.id, "DONE").status_code == 409


def test_report_404_on_unknown_id(driver_client, test_db):
    assert report(driver_client, 999999, "DONE").status_code == 404


def test_late_report_on_swept_ready_task_succeeds(driver_client, test_db):
    """A lease expired mid-run, but the work really finished — still valid."""
    task = make_task(test_db, status=AgentTaskStatus.READY, attempts=1)
    data = report(driver_client, task.id, "DONE", result={"late": True}).json()
    assert data["task"]["status"] == AgentTaskStatus.DONE.value


def test_report_overwrites_result_and_error(driver_client, test_db):
    task = make_task(test_db)
    driver_client.get(NEXT_URL)
    report(driver_client, task.id, "FAILED", error="first", result={"a": 1})

    driver_client.get(NEXT_URL)  # re-claim (it went back to READY)
    data = report(driver_client, task.id, "DONE", result={"b": 2}).json()

    test_db.expire_all()
    stored = test_db.get(AgentTask, task.id)
    assert stored.result == {"b": 2}
    assert stored.error is None
    assert data["task"]["error"] is None


# --- Task actions (#420) ----------------------------------------------------


def act(client, task_id, action):
    return client.post(f"{BASE}/{task_id}/action", json={"action": action})


def test_retry_returns_a_failed_task_to_the_pool(driver_client, test_db):
    task = make_task(
        test_db,
        status=AgentTaskStatus.FAILED,
        attempts=2,
        leased_by="driver-a",
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    data = act(driver_client, task.id, "retry").json()

    assert data["task"]["status"] == AgentTaskStatus.READY.value
    assert data["task"]["attempts"] == 0
    assert data["task"]["leased_by"] is None
    assert data["task"]["lease_expires_at"] is None
    assert data["promoted"] == []
    # ...and the retried row is claimable again.
    assert driver_client.get(NEXT_URL).json()["task"]["id"] == task.id


def test_retry_resets_attempts_at_the_ceiling(driver_client, test_db):
    """Without the reset a retried task gets one attempt and no expiry grace."""
    task = make_task(test_db, status=AgentTaskStatus.FAILED, attempts=3)

    act(driver_client, task.id, "retry")

    test_db.expire_all()
    assert test_db.get(AgentTask, task.id).attempts == 0


def test_retry_keeps_the_error_as_history(driver_client, test_db):
    task = make_task(test_db, status=AgentTaskStatus.FAILED)
    task.error = "boom"
    test_db.commit()

    assert act(driver_client, task.id, "retry").json()["task"]["error"] == "boom"


@pytest.mark.parametrize(
    "status",
    [AgentTaskStatus.DONE, AgentTaskStatus.LEASED, AgentTaskStatus.PENDING],
)
def test_retry_409s_on_anything_but_failed(driver_client, test_db, status):
    """Without an earlier retry on record; READY is the retry's own target
    state and converges instead (#641)."""
    task = make_task(test_db, status=status)
    assert act(driver_client, task.id, "retry").status_code == 409


def test_skip_promotes_dependents(driver_client, test_db):
    first = make_task(test_db, position=10, status=AgentTaskStatus.PENDING)
    second = make_task(
        test_db,
        kind=AgentTaskKind.TRANSLATE,
        position=20,
        status=AgentTaskStatus.PENDING,
        depends_on=[first.id],
    )

    data = act(driver_client, first.id, "skip").json()

    assert data["task"]["status"] == AgentTaskStatus.SKIPPED.value
    assert data["promoted"] == [second.id]
    test_db.expire_all()
    assert test_db.get(AgentTask, second.id).status == AgentTaskStatus.READY.value


def test_skip_works_on_a_failed_task(driver_client, test_db):
    task = make_task(test_db, status=AgentTaskStatus.FAILED, attempts=3)

    data = act(driver_client, task.id, "skip").json()

    assert data["task"]["status"] == AgentTaskStatus.SKIPPED.value
    assert data["task"]["leased_by"] is None


@pytest.mark.parametrize("status", [AgentTaskStatus.LEASED, AgentTaskStatus.DONE])
def test_skip_409s_on_leased_and_terminal(driver_client, test_db, status):
    task = make_task(test_db, status=status)
    assert act(driver_client, task.id, "skip").status_code == 409


def test_action_404s_on_unknown_task(driver_client):
    assert act(driver_client, 999999, "retry").status_code == 404


def test_action_rejects_an_unknown_verb(driver_client, test_db):
    task = make_task(test_db, status=AgentTaskStatus.FAILED)
    assert act(driver_client, task.id, "nonsense").status_code == 422


# --- WAITING (#553) ----------------------------------------------------------
# These mechanism tests register GENERATE_TAGS as a stand-in wait kind with a
# switchable condition; the real gates (#554 onward) are tested further down.

WAIT_KIND = AgentTaskKind.GENERATE_TAGS


@pytest.fixture
def wait_kind(monkeypatch, driver_settings):
    """Make WAIT_KIND a wait kind; flip ``state["waiting_for"]`` to None to satisfy it."""
    state = {"waiting_for": "deep research in the pic-extension"}
    monkeypatch.setitem(WAIT_CONDITIONS, WAIT_KIND.value, lambda db, sid: state["waiting_for"])
    monkeypatch.setitem(KIND_CALLS, WAIT_KIND.value, _wait_check(WAIT_KIND))
    monkeypatch.setattr(driver_settings, "agent_task_wait_recheck_hours", 20)
    return state


def test_wait_kind_renders_the_wait_check_call(wait_kind):
    [call] = spec_for(WAIT_KIND, subject_id=42)
    assert call.method == "GET"
    assert call.path == f"{AGENT_TASKS_PREFIX}/wait-check/{WAIT_KIND.value}/42"


def test_wait_check_reports_unsatisfied_then_satisfied(driver_client, wait_kind):
    url = f"{BASE}/wait-check/{WAIT_KIND.value}/42"
    data = driver_client.get(url).json()
    assert data == {
        "kind": WAIT_KIND.value,
        "subject_id": 42,
        "satisfied": False,
        "waiting_for": "deep research in the pic-extension",
    }

    wait_kind["waiting_for"] = None
    data = driver_client.get(url).json()
    assert data["satisfied"] is True
    assert data["waiting_for"] is None


def test_wait_check_404s_on_a_non_wait_kind(driver_client):
    response = driver_client.get(f"{BASE}/wait-check/{AgentTaskKind.TRANSLATE.value}/1")
    assert response.status_code == 404


def test_report_waiting_does_not_burn_attempts(driver_client, test_db, wait_kind):
    task = make_task(test_db, kind=WAIT_KIND, attempts=2)  # claim makes it 3 == ceiling
    driver_client.get(NEXT_URL)

    before = datetime.now(UTC)
    data = report(driver_client, task.id, "WAITING", waiting_for="Grammarly EN").json()

    assert data["task"]["status"] == AgentTaskStatus.WAITING.value
    assert data["task"]["attempts"] == 2  # the claim's attempt is refunded
    assert data["task"]["waiting_for"] == "Grammarly EN"
    assert data["task"]["leased_by"] is None
    assert data["promoted"] == []
    next_check = datetime.fromisoformat(data["task"]["next_check_at"])
    assert before + timedelta(hours=19) < next_check < before + timedelta(hours=21)


def test_waiting_many_times_never_parks(driver_client, test_db, wait_kind):
    """A week of daily drives waiting on the operator must not exhaust attempts."""
    task = make_task(test_db, kind=WAIT_KIND)
    for _ in range(5):
        driver_client.get(NEXT_URL)
        report(driver_client, task.id, "WAITING", waiting_for="x")
        test_db.expire_all()
        row = test_db.get(AgentTask, task.id)
        row.next_check_at = datetime.now(UTC) - timedelta(minutes=1)  # next drive
        test_db.commit()

    test_db.expire_all()
    row = test_db.get(AgentTask, task.id)
    assert row.status == AgentTaskStatus.WAITING.value
    assert row.attempts == 0


def test_waiting_task_is_not_claimable_before_recheck(driver_client, test_db, wait_kind):
    task = make_task(test_db, kind=WAIT_KIND)
    driver_client.get(NEXT_URL)
    report(driver_client, task.id, "WAITING", waiting_for="x")

    # Same drive session: nothing else is READY, so the driver's loop ends.
    assert driver_client.get(NEXT_URL).status_code == 204


def test_waiting_task_is_claimable_after_recheck(driver_client, test_db, wait_kind):
    task = make_task(test_db, kind=WAIT_KIND)
    driver_client.get(NEXT_URL)
    report(driver_client, task.id, "WAITING", waiting_for="x")
    test_db.expire_all()
    row = test_db.get(AgentTask, task.id)
    row.next_check_at = datetime.now(UTC) - timedelta(minutes=1)
    test_db.commit()

    data = driver_client.get(NEXT_URL).json()

    assert data["task"]["id"] == task.id
    assert data["task"]["status"] == AgentTaskStatus.LEASED.value
    assert data["task"]["next_check_at"] is None
    assert data["calls"][0]["path"].endswith(f"/wait-check/{WAIT_KIND.value}/1")


def test_dependents_stay_pending_while_waiting_then_promote_on_done(
    driver_client, test_db, wait_kind
):
    gate = make_task(test_db, kind=WAIT_KIND, position=10)
    dependent = make_task(
        test_db,
        kind=AgentTaskKind.TRANSLATE,
        position=20,
        status=AgentTaskStatus.PENDING,
        depends_on=[gate.id],
    )
    driver_client.get(NEXT_URL)
    report(driver_client, gate.id, "WAITING", waiting_for="x")
    test_db.expire_all()
    assert test_db.get(AgentTask, dependent.id).status == AgentTaskStatus.PENDING.value

    # Next drive: the operator did the work, the condition holds.
    row = test_db.get(AgentTask, gate.id)
    row.next_check_at = datetime.now(UTC) - timedelta(minutes=1)
    test_db.commit()
    wait_kind["waiting_for"] = None
    assert driver_client.get(NEXT_URL).json()["task"]["id"] == gate.id

    data = report(driver_client, gate.id, "DONE").json()

    assert data["task"]["status"] == AgentTaskStatus.DONE.value
    assert data["task"]["waiting_for"] is None
    assert data["promoted"] == [dependent.id]


def test_report_waiting_400s_on_a_non_wait_kind(driver_client, test_db):
    task = make_task(test_db, kind=AgentTaskKind.TRANSLATE)
    driver_client.get(NEXT_URL)
    response = report(driver_client, task.id, "WAITING", waiting_for="x")
    assert response.status_code == 400
    test_db.expire_all()
    assert test_db.get(AgentTask, task.id).status == AgentTaskStatus.LEASED.value


def test_failed_still_works_for_a_wait_kind(driver_client, test_db, wait_kind):
    """The pre-#62 driver reports FAILED on a wait task — keep that path alive."""
    task = make_task(test_db, kind=WAIT_KIND)
    driver_client.get(NEXT_URL)
    data = report(driver_client, task.id, "FAILED", error="not ready").json()
    assert data["task"]["status"] == AgentTaskStatus.READY.value
    assert data["task"]["attempts"] == 1


def test_retry_on_waiting_rechecks_now(driver_client, test_db, wait_kind):
    task = make_task(test_db, kind=WAIT_KIND)
    driver_client.get(NEXT_URL)
    report(driver_client, task.id, "WAITING", waiting_for="x")

    data = act(driver_client, task.id, "retry").json()

    assert data["task"]["status"] == AgentTaskStatus.READY.value
    assert data["task"]["next_check_at"] is None
    assert data["task"]["waiting_for"] is None
    assert driver_client.get(NEXT_URL).json()["task"]["id"] == task.id


def test_skip_works_on_a_waiting_task(driver_client, test_db, wait_kind):
    gate = make_task(test_db, kind=WAIT_KIND, position=10)
    dependent = make_task(
        test_db,
        kind=AgentTaskKind.TRANSLATE,
        position=20,
        status=AgentTaskStatus.PENDING,
        depends_on=[gate.id],
    )
    driver_client.get(NEXT_URL)
    report(driver_client, gate.id, "WAITING", waiting_for="x")

    data = act(driver_client, gate.id, "skip").json()

    assert data["task"]["status"] == AgentTaskStatus.SKIPPED.value
    assert data["promoted"] == [dependent.id]


# --- Auth -------------------------------------------------------------------


def test_driver_token_passes_on_every_driver_endpoint(driver_client, test_db):
    make_task(test_db)
    assert driver_client.get(NEXT_URL).status_code == 200
    assert driver_client.get(BASE).status_code == 200
    # plan-week is intentionally in scope: 204 from next is the signal to call it,
    # and PLAN_ARTICLES maps to it. No research seeded -> the empty-state 404.
    assert driver_client.post(f"{BASE}/plan-week", json={}).status_code == 404


def test_wrong_bearer_token_is_401(test_client, driver_settings):
    test_client.headers["Authorization"] = "Bearer not-the-driver-token"
    assert test_client.get(NEXT_URL).status_code == 401


def test_driver_token_rejected_when_setting_is_empty(test_client, monkeypatch, admin_token):
    monkeypatch.setattr(get_settings(), "agent_driver_token", "")
    test_client.headers["Authorization"] = f"Bearer {DRIVER_TOKEN}"
    assert test_client.get(NEXT_URL).status_code == 401
    # ... and the admin token still works on the same router.
    test_client.headers["Authorization"] = f"Bearer {admin_token}"
    assert test_client.get(NEXT_URL).status_code == 204


def test_admin_token_still_works_with_driver_token_enabled(
    test_client, admin_token, driver_settings, test_db
):
    make_task(test_db)
    data = test_client.get(NEXT_URL).json()
    assert data["task"]["leased_by"] == "test-admin@example.com"


# --- Auth on the allowlisted call surface (#444) -----------------------------

# 404 (not 401) is the proof auth passed: every endpoint below looks its subject
# row up first, so a nonexistent id fails fast before any external work.


def test_driver_token_passes_on_the_dashboard_call_surface(driver_client):
    resp = driver_client.patch(
        "/api/v1/admin/dashboard/articles/999999/outline-status",
        json={"status": "APPROVED"},
    )
    assert resp.status_code == 404


def test_driver_token_passes_on_the_generic_article_status_route(driver_client):
    """APPROVE_ARTICLE lands on the generic /{entity}/{entity_id}/status route."""
    resp = driver_client.patch(
        "/api/v1/admin/dashboard/articles/999999/status", json={"status": "APPROVED"}
    )
    assert resp.status_code == 404


def test_driver_token_passes_on_the_instagram_call_surface(driver_client):
    resp = driver_client.patch(
        "/api/v1/admin/dashboard/instagram/posts/999999/status", json={"status": "APPROVED"}
    )
    assert resp.status_code == 404


def test_driver_token_passes_on_the_admin_router_call_surface(driver_client):
    resp = driver_client.post(
        "/api/v1/admin/articles/999999/grammar-check", params={"language": "en"}
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/api/v1/admin/dashboard/stats", None),
        ("get", "/api/v1/admin/dashboard/articles", None),
        # A real route, but not one the driver emits: method+path scoping, not prefix.
        ("delete", "/api/v1/admin/dashboard/instagram/posts/1", None),
        # The generic status route is shared with suggestions/research — fenced.
        ("patch", "/api/v1/admin/dashboard/suggestions/1/status", {"status": "APPROVED"}),
        # Method mismatch on an allowlisted path shape (the GET route exists).
        ("get", "/api/v1/admin/dashboard/articles/1", None),
    ],
)
def test_driver_token_is_401_off_its_call_surface(driver_client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert getattr(driver_client, method)(path, **kwargs).status_code == 401


def test_admin_token_unaffected_on_the_swapped_routers(test_client, admin_token, driver_settings):
    assert test_client.get("/api/v1/admin/dashboard/stats").status_code == 200


def test_driver_token_rejected_on_call_surface_when_setting_is_empty(test_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_driver_token", "")
    test_client.headers["Authorization"] = f"Bearer {DRIVER_TOKEN}"
    resp = test_client.patch(
        "/api/v1/admin/dashboard/articles/999999/outline-status",
        json={"status": "APPROVED"},
    )
    assert resp.status_code == 401


# --- List + jobs proxy ------------------------------------------------------


def test_list_filters_by_goal_key_and_orders(driver_client, test_db):
    make_task(test_db, position=20, subject_id=2)
    make_task(test_db, position=10, subject_id=1)
    make_task(test_db, position=10, subject_id=3, goal_key="weekly_series:2026-W32")

    all_tasks = driver_client.get(BASE).json()
    assert len(all_tasks) == 3
    # goal_key desc first, then position asc within a goal.
    assert all_tasks[0]["goal_key"] == "weekly_series:2026-W32"
    assert [t["position"] for t in all_tasks[1:]] == [10, 20]

    filtered = driver_client.get(BASE, params={"goal_key": GOAL}).json()
    assert len(filtered) == 2
    assert all(t["goal_key"] == GOAL for t in filtered)


def test_jobs_proxy_returns_job_and_404s(driver_client, test_db):
    job = CrawlJob(job_type="WIKIMEDIA", status="SUCCEEDED")
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    data = driver_client.get(f"{BASE}/jobs/{job.id}").json()
    assert data["id"] == job.id
    assert data["status"] == "SUCCEEDED"

    assert driver_client.get(f"{BASE}/jobs/999999").status_code == 404


# --- Concurrency ------------------------------------------------------------


def test_concurrent_claims_skip_locked(test_db, test_engine, driver_settings):
    """Two drivers never take the same row: the locked one is skipped."""
    first = make_task(test_db, position=10, subject_id=1)
    second = make_task(test_db, position=20, subject_id=2)

    Session = sessionmaker(bind=test_engine)
    session_a = Session()
    session_b = Session()
    try:
        locked = _select_next_for_update(session_a)  # holds the row lock, uncommitted
        assert locked.id == first.id

        claimed = claim_next_task(session_b, driver_settings, leased_by="driver-b")
        assert claimed.id == second.id
    finally:
        # Always release both, or teardown's TRUNCATE deadlocks.
        session_a.rollback()
        session_a.close()
        session_b.rollback()
        session_b.close()


# --- WAIT_RESEARCH_READY (#554) ----------------------------------------------

RESEARCH_READY = AgentTaskKind.WAIT_RESEARCH_READY


def _claim(test_db, subject_type, subject_id, verdict):
    test_db.add(
        FactCheckClaim(
            subject_type=subject_type,
            subject_id=subject_id,
            claim_text=f"claim {verdict}",
            verdict=verdict,
        )
    )
    test_db.commit()


def test_every_wait_kind_calls_the_wait_check():
    assert RESEARCH_READY.value in WAIT_CONDITIONS
    for kind in WAIT_CONDITIONS:
        [call] = spec_for(AgentTaskKind(kind), subject_id=3)
        assert call.method == "GET"
        assert call.path == f"{AGENT_TASKS_PREFIX}/wait-check/{kind}/3"


def test_research_ready_holds_with_no_claims(driver_client):
    """Approved research the operator chose not to fact-check is ready."""
    data = driver_client.get(f"{BASE}/wait-check/{RESEARCH_READY.value}/5").json()
    assert data["satisfied"] is True
    assert data["waiting_for"] is None


def test_research_ready_holds_once_every_claim_is_resolved(driver_client, test_db):
    for verdict in ("VERIFIED", "INACCURATE", "CORRECTED", "DISPUTED", "UNVERIFIABLE"):
        _claim(test_db, "RESEARCH", 5, verdict)
    data = driver_client.get(f"{BASE}/wait-check/{RESEARCH_READY.value}/5").json()
    assert data["satisfied"] is True


def test_research_ready_waits_on_unresolved_claims(driver_client, test_db):
    _claim(test_db, "RESEARCH", 5, "VERIFIED")
    _claim(test_db, "RESEARCH", 5, "PENDING")
    _claim(test_db, "RESEARCH", 5, "NEEDS_DEEP_RESEARCH")
    _claim(test_db, "RESEARCH", 5, "NEEDS_DEEP_RESEARCH")
    # Another subject's unresolved claims are irrelevant.
    _claim(test_db, "RESEARCH", 6, "PENDING")
    _claim(test_db, "ARTICLE", 5, "PENDING")

    data = driver_client.get(f"{BASE}/wait-check/{RESEARCH_READY.value}/5").json()
    assert data["satisfied"] is False
    assert data["waiting_for"] == (
        "fact-check of research 5 unresolved (2 NEEDS_DEEP_RESEARCH, 1 PENDING): "
        "deep research / corrections by the operator"
    )


def test_research_ready_gate_waits_then_releases_the_outline(driver_client, test_db):
    """End to end: WAITING keeps the outline PENDING; DONE promotes it."""
    gate = make_task(test_db, kind=RESEARCH_READY, subject=AgentTaskSubject.RESEARCH, subject_id=5)
    outline = make_task(
        test_db,
        kind=AgentTaskKind.GENERATE_OUTLINE,
        subject=AgentTaskSubject.RESEARCH,
        subject_id=5,
        status=AgentTaskStatus.PENDING,
        depends_on=[gate.id],
        position=20,
    )
    _claim(test_db, "RESEARCH", 5, "NEEDS_DEEP_RESEARCH")

    claimed = driver_client.get(NEXT_URL).json()
    assert claimed["task"]["id"] == gate.id
    check_path = claimed["calls"][0]["path"]
    check = driver_client.get(check_path).json()
    assert check["satisfied"] is False
    assert report(driver_client, gate.id, "WAITING", waiting_for=check["waiting_for"]).is_success
    test_db.refresh(outline)
    assert outline.status == AgentTaskStatus.PENDING.value

    claim = test_db.query(FactCheckClaim).filter(FactCheckClaim.subject_id == 5).one()
    claim.verdict = "VERIFIED"
    # Next drive: the recheck time has passed and the driver claims it again.
    test_db.get(AgentTask, gate.id).next_check_at = datetime.now(UTC) - timedelta(minutes=1)
    test_db.commit()
    assert driver_client.get(NEXT_URL).json()["task"]["id"] == gate.id
    assert driver_client.get(check_path).json()["satisfied"] is True
    assert report(driver_client, gate.id, "DONE").is_success
    test_db.refresh(outline)
    assert outline.status == AgentTaskStatus.READY.value


# --- WAIT_ARTICLE_CLAIMS_RESOLVED (#555) -------------------------------------

ARTICLE_GATE = AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED


def test_article_gate_is_a_wait_kind():
    assert ARTICLE_GATE.value in WAIT_CONDITIONS
    [call] = spec_for(ARTICLE_GATE, subject_id=8)
    assert (call.method, call.path) == (
        "GET",
        f"{AGENT_TASKS_PREFIX}/wait-check/{ARTICLE_GATE.value}/8",
    )


def test_article_gate_waits_on_unresolved_claims(driver_client, test_db):
    _claim(test_db, "ARTICLE", 8, "VERIFIED")
    _claim(test_db, "ARTICLE", 8, "NEEDS_DEEP_RESEARCH")
    # The research with the same id is a different subject.
    _claim(test_db, "RESEARCH", 9, "PENDING")
    _claim(test_db, "RESEARCH", 8, "PENDING")

    data = driver_client.get(f"{BASE}/wait-check/{ARTICLE_GATE.value}/8").json()
    assert data["satisfied"] is False
    assert data["waiting_for"] == (
        "fact-check of article 8 unresolved (1 NEEDS_DEEP_RESEARCH): "
        "deep research / corrections by the operator"
    )


def test_article_gate_holds_once_every_claim_is_resolved(driver_client, test_db):
    for verdict in ("VERIFIED", "INACCURATE", "CORRECTED", "DISPUTED", "UNVERIFIABLE"):
        _claim(test_db, "ARTICLE", 8, verdict)
    data = driver_client.get(f"{BASE}/wait-check/{ARTICLE_GATE.value}/8").json()
    assert data["satisfied"] is True


def test_article_gate_holds_corrections_until_deep_research_lands(
    driver_client, test_db, article_factory
):
    """End to end: the corrections step stays PENDING while the article has a
    NEEDS_DEEP_RESEARCH claim, and once deep research resolves it clean the
    preview the driver then calls is the #481 200-with-no-edits, not a 400."""
    article = article_factory()
    fact_check = make_task(
        test_db,
        kind=AgentTaskKind.ARTICLE_FACT_CHECK,
        subject=AgentTaskSubject.ARTICLE,
        subject_id=article.id,
        status=AgentTaskStatus.DONE,
    )
    gate = make_task(
        test_db,
        kind=ARTICLE_GATE,
        subject=AgentTaskSubject.ARTICLE,
        subject_id=article.id,
        depends_on=[fact_check.id],
        position=20,
    )
    corrections = make_task(
        test_db,
        kind=AgentTaskKind.APPLY_ARTICLE_CORRECTIONS,
        subject=AgentTaskSubject.ARTICLE,
        subject_id=article.id,
        status=AgentTaskStatus.PENDING,
        depends_on=[gate.id],
        position=30,
    )
    _claim(test_db, "ARTICLE", article.id, "VERIFIED")
    _claim(test_db, "ARTICLE", article.id, "NEEDS_DEEP_RESEARCH")

    claimed = driver_client.get(NEXT_URL).json()
    assert claimed["task"]["id"] == gate.id
    check_path = claimed["calls"][0]["path"]
    check = driver_client.get(check_path).json()
    assert check["satisfied"] is False
    assert report(driver_client, gate.id, "WAITING", waiting_for=check["waiting_for"]).is_success
    test_db.refresh(corrections)
    assert corrections.status == AgentTaskStatus.PENDING.value

    # The deep-research drain resolves the claim (here: confirmed true).
    pending = (
        test_db.query(FactCheckClaim).filter(FactCheckClaim.verdict == "NEEDS_DEEP_RESEARCH").one()
    )
    pending.verdict = "VERIFIED"
    test_db.get(AgentTask, gate.id).next_check_at = datetime.now(UTC) - timedelta(minutes=1)
    test_db.commit()

    assert driver_client.get(NEXT_URL).json()["task"]["id"] == gate.id
    assert driver_client.get(check_path).json()["satisfied"] is True
    assert report(driver_client, gate.id, "DONE").is_success
    test_db.refresh(corrections)
    assert corrections.status == AgentTaskStatus.READY.value

    claimed = driver_client.get(NEXT_URL).json()
    assert claimed["task"]["id"] == corrections.id
    preview = claimed["calls"][0]
    response = driver_client.post(preview["path"])
    assert response.status_code == 200
    assert response.json()["correctable_claims"] == 0
    assert response.json()["edits"] == []


# --- WAIT_GRAMMAR_CHECKED_EN / _ES (#556) --------------------------------------

GRAMMAR_WAITS = (
    (AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN, "en"),
    (AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES, "es"),
)


@pytest.mark.parametrize("kind,language", GRAMMAR_WAITS)
def test_grammar_waits_are_wait_kinds(kind, language):
    assert kind.value in WAIT_CONDITIONS
    [call] = spec_for(kind, subject_id=8)
    assert (call.method, call.path) == ("GET", f"{AGENT_TASKS_PREFIX}/wait-check/{kind.value}/8")


@pytest.mark.parametrize("kind,language", GRAMMAR_WAITS)
def test_grammar_wait_holds_only_on_its_own_stamp(
    driver_client, test_db, article_factory, kind, language
):
    article = article_factory()
    other = "es" if language == "en" else "en"
    # The other language's stamp does not satisfy this wait.
    setattr(article, f"grammar_checked_{other}_at", datetime.now(UTC))
    test_db.commit()

    data = driver_client.get(f"{BASE}/wait-check/{kind.value}/{article.id}").json()
    assert data["satisfied"] is False
    assert data["waiting_for"] == (
        f"Grammarly {language.upper()} pass on article {article.id} by the operator"
    )

    setattr(article, f"grammar_checked_{language}_at", datetime.now(UTC))
    test_db.commit()
    data = driver_client.get(f"{BASE}/wait-check/{kind.value}/{article.id}").json()
    assert data["satisfied"] is True


def test_grammar_wait_on_a_missing_article_keeps_waiting(driver_client):
    kind = AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN
    data = driver_client.get(f"{BASE}/wait-check/{kind.value}/999999").json()
    assert data["satisfied"] is False
    assert data["waiting_for"] == "article 999999 not found"


def test_grammar_wait_en_holds_tags_until_grammarly_stamps(driver_client, test_db, article_factory):
    """End to end: GENERATE_TAGS stays PENDING behind the EN wait until the
    operator's Grammarly pass stamps the article."""
    article = article_factory()
    corrections = make_task(
        test_db,
        kind=AgentTaskKind.APPLY_ARTICLE_CORRECTIONS,
        subject_id=article.id,
        status=AgentTaskStatus.DONE,
    )
    wait = make_task(
        test_db,
        kind=AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN,
        subject_id=article.id,
        depends_on=[corrections.id],
        position=20,
    )
    tags = make_task(
        test_db,
        kind=AgentTaskKind.GENERATE_TAGS,
        subject_id=article.id,
        status=AgentTaskStatus.PENDING,
        depends_on=[wait.id],
        position=30,
    )

    claimed = driver_client.get(NEXT_URL).json()
    assert claimed["task"]["id"] == wait.id
    check_path = claimed["calls"][0]["path"]
    check = driver_client.get(check_path).json()
    assert check["satisfied"] is False
    assert report(driver_client, wait.id, "WAITING", waiting_for=check["waiting_for"]).is_success
    test_db.refresh(tags)
    assert tags.status == AgentTaskStatus.PENDING.value

    # The operator runs Grammarly in the pic-extension; grammar-apply stamps.
    article.grammar_checked_en_at = datetime.now(UTC)
    test_db.get(AgentTask, wait.id).next_check_at = datetime.now(UTC) - timedelta(minutes=1)
    test_db.commit()

    assert driver_client.get(NEXT_URL).json()["task"]["id"] == wait.id
    assert driver_client.get(check_path).json()["satisfied"] is True
    assert report(driver_client, wait.id, "DONE").is_success
    test_db.refresh(tags)
    assert tags.status == AgentTaskStatus.READY.value


# --- ready_for_grammar_en / _es on the admin article list (#556) -------------

ARTICLES_URL = "/api/v1/admin/dashboard/articles"


def _readiness(client, article_id):
    [row] = [a for a in client.get(ARTICLES_URL).json() if a["id"] == article_id]
    return row["ready_for_grammar_en"], row["ready_for_grammar_es"]


def test_ready_for_grammar_without_a_wait_task_is_content_and_no_stamp(
    test_client, admin_token, test_db, article_factory
):
    """Articles the pipeline is not driving keep the extension's old rule."""
    article = article_factory()
    assert _readiness(test_client, article.id) == (True, False)  # no Spanish yet

    article.content_es = "Contenido"
    test_db.commit()
    assert _readiness(test_client, article.id) == (True, True)

    article.grammar_checked_en_at = datetime.now(UTC)
    test_db.commit()
    assert _readiness(test_client, article.id) == (False, True)


@pytest.mark.parametrize(
    "status,ready",
    [
        (AgentTaskStatus.PENDING, False),  # corrections / translation still ahead
        (AgentTaskStatus.READY, True),
        (AgentTaskStatus.WAITING, True),
        (AgentTaskStatus.LEASED, False),
        (AgentTaskStatus.DONE, False),
        (AgentTaskStatus.SKIPPED, False),
    ],
)
def test_ready_for_grammar_follows_the_wait_task(
    test_client, admin_token, test_db, article_factory, status, ready
):
    article = article_factory()
    article.content_es = "Contenido"
    test_db.commit()
    make_task(
        test_db,
        kind=AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN,
        subject_id=article.id,
        status=status,
    )
    # EN follows its wait; ES has no wait task, so it keeps the old rule.
    assert _readiness(test_client, article.id) == (ready, True)


def test_ready_for_grammar_is_false_once_stamped_even_if_the_wait_is_open(
    test_client, admin_token, test_db, article_factory
):
    """A READY wait the driver has not re-checked yet must not re-offer a
    stamped article."""
    article = article_factory()
    article.content_es = "Contenido"
    article.grammar_checked_es_at = datetime.now(UTC)
    test_db.commit()
    make_task(
        test_db,
        kind=AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES,
        subject_id=article.id,
        status=AgentTaskStatus.READY,
    )
    make_task(
        test_db,
        kind=AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN,
        subject_id=article.id,
        status=AgentTaskStatus.PENDING,
        position=20,
    )
    assert _readiness(test_client, article.id) == (False, False)


def test_ready_for_grammar_ignores_other_subjects_with_the_same_id(
    test_client, admin_token, test_db, article_factory
):
    article = article_factory()
    make_task(
        test_db,
        kind=AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN,
        subject=AgentTaskSubject.RESEARCH,
        subject_id=article.id,
        status=AgentTaskStatus.PENDING,
    )
    assert _readiness(test_client, article.id)[0] is True
