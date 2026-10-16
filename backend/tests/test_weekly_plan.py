"""Tests for the weekly-series plan-week endpoint (#417).

The planner is an insert-only ensure-pass, so most tests follow the same shape
the #418 driver will: call plan-week, mark the tasks it planned as DONE, seed the
rows those tasks would have produced, then call plan-week again and assert the
graph expanded.
"""

import pytest
from app.models.agent_task import AgentTask
from app.models.enums import AgentTaskKind, AgentTaskStatus, AgentTaskSubject
from app.models.fact_check import FactCheckClaim
from app.models.instagram_post import InstagramPost

URL = "/api/v1/admin/dashboard/agent-tasks/plan-week"

# A Saturday — the intended planning day. ISO week 31 of 2026.
PLAN_SATURDAY = "2026-08-01"
GOAL_KEY = "weekly_series:2026-W31"


def by_key(payload):
    """Index a plan-week response's tasks by (kind, subject_type, subject_id)."""
    return {(t["kind"], t["subject_type"], t["subject_id"]): t for t in payload["tasks"]}


def kinds_for(payload, subject_type, subject_id):
    return {
        t["kind"]
        for t in payload["tasks"]
        if t["subject_type"] == subject_type and t["subject_id"] == subject_id
    }


def mark_done(test_db, kind):
    """Mark the goal's task of ``kind`` DONE, as the driver would."""
    task = test_db.query(AgentTask).filter(AgentTask.kind == kind.value).one()
    task.status = AgentTaskStatus.DONE.value
    test_db.commit()
    return task


def make_sabias_post(test_db, research_id, title):
    post = InstagramPost(
        post_type="SABIAS_QUE",
        research_id=research_id,
        title=title,
        fact="Un dato.",
    )
    test_db.add(post)
    test_db.commit()
    test_db.refresh(post)
    return post


def make_promo_post(test_db, article_id, title="Promo"):
    post = InstagramPost(post_type="ARTICLE_PROMO", article_id=article_id, title=title)
    test_db.add(post)
    test_db.commit()
    test_db.refresh(post)
    return post


@pytest.fixture(autouse=True)
def ig_posts_enabled(monkeypatch):
    """Most tests pin the IG-enabled graph; the flag-off tests flip it back.

    ``weekly_plan_ig_posts_enabled`` ships False (#552), but the bulk of this
    file asserts the full graph including the Instagram chains.
    """
    from app.core.config import get_settings

    # get_settings is lru_cached: patch the singleton, monkeypatch restores it.
    monkeypatch.setattr(get_settings(), "weekly_plan_ig_posts_enabled", True)


@pytest.fixture
def series(test_db, research_factory, category_factory, article_factory):
    """An APPROVED research plus a 3-article series (parent + 2 children)."""
    category = category_factory()
    research = research_factory()

    def build():
        parent = article_factory(
            title="Parent", category=category, research_id=research.id, status="PENDING"
        )
        children = [
            article_factory(
                title=f"Child {i}",
                category=category,
                research_id=research.id,
                series_parent=parent,
                series_order=i,
                status="PENDING",
            )
            for i in (1, 2)
        ]
        return parent, children

    return research, build


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


def test_plan_week_requires_admin_auth(test_client):
    response = test_client.post(URL, json={})
    assert response.status_code == 401


# --------------------------------------------------------------------------
# Wave 0
# --------------------------------------------------------------------------


def test_plan_week_builds_wave_zero(test_client, admin_token, research_factory):
    research = research_factory()

    response = test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    assert response.status_code == 200
    data = response.json()

    assert data["goal_key"] == GOAL_KEY
    assert data["research_id"] == research.id
    assert data["created"] == 4
    assert len(data["tasks"]) == 4

    tasks = by_key(data)
    subject = AgentTaskSubject.RESEARCH.value
    # #554: the research fact-check / corrections steps are gone from new plans.
    assert kinds_for(data, subject, research.id) == {
        AgentTaskKind.WAIT_RESEARCH_READY.value,
        AgentTaskKind.GENERATE_OUTLINE.value,
        AgentTaskKind.GENERATE_SABIAS_QUE.value,
        AgentTaskKind.PLAN_ARTICLES.value,
    }

    gate = tasks[(AgentTaskKind.WAIT_RESEARCH_READY.value, subject, research.id)]
    outline = tasks[(AgentTaskKind.GENERATE_OUTLINE.value, subject, research.id)]
    sabias = tasks[(AgentTaskKind.GENERATE_SABIAS_QUE.value, subject, research.id)]
    plan_articles = tasks[(AgentTaskKind.PLAN_ARTICLES.value, subject, research.id)]

    # The gate is the only root and is READY; everything downstream waits.
    assert gate["status"] == AgentTaskStatus.READY.value
    assert gate["depends_on"] is None
    assert outline["depends_on"] == [gate["id"]]
    assert outline["status"] == AgentTaskStatus.PENDING.value
    assert sabias["depends_on"] == [gate["id"]]
    assert sorted(plan_articles["depends_on"]) == sorted([outline["id"], sabias["id"]])
    assert plan_articles["payload"] == {
        "expands": "article chains + sabias-que post chains",
        "week_start": PLAN_SATURDAY,
        "goal_key": GOAL_KEY,
    }

    # Positions are a strictly increasing ordering aid.
    positions = [t["position"] for t in data["tasks"]]
    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)


def _seed_claims(test_db, subject_type, subject_id, verdicts, correction=None):
    for i, verdict in enumerate(verdicts):
        test_db.add(
            FactCheckClaim(
                subject_type=subject_type,
                subject_id=subject_id,
                claim_text=f"claim {i}",
                verdict=verdict,
                correction=correction if verdict == "INACCURATE" else None,
            )
        )
    test_db.commit()


@pytest.mark.parametrize(
    "verdicts",
    [
        [],  # no fact-check at all
        ["VERIFIED", "INACCURATE"],  # resolved, with something left to correct
        ["VERIFIED", "NEEDS_DEEP_RESEARCH"],  # attended loop still running
    ],
)
def test_plan_week_plans_the_gate_whatever_the_claims(
    test_client, admin_token, test_db, research_factory, verdicts
):
    """The planner does not pre-evaluate the gate: the driver's wait-check does."""
    research = research_factory()
    _seed_claims(test_db, "RESEARCH", research.id, verdicts, "It opened in 1914.")

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    subject = AgentTaskSubject.RESEARCH.value
    kinds = kinds_for(data, subject, research.id)
    assert AgentTaskKind.WAIT_RESEARCH_READY.value in kinds
    assert AgentTaskKind.RESEARCH_FACT_CHECK.value not in kinds
    assert AgentTaskKind.APPLY_RESEARCH_CORRECTIONS.value not in kinds
    gate = by_key(data)[(AgentTaskKind.WAIT_RESEARCH_READY.value, subject, research.id)]
    assert gate["status"] == AgentTaskStatus.READY.value


def test_plan_week_keeps_legacy_research_steps_of_an_in_flight_goal(
    test_client, admin_token, test_db, research_factory
):
    """A goal planned before #554 keeps its fact-check/corrections rows and gets
    no gate: the outline already chains off the corrections row (insert-only)."""
    research = research_factory()
    subject = AgentTaskSubject.RESEARCH.value

    def add(kind, status, position, depends_on=None):
        task = AgentTask(
            goal_key=GOAL_KEY,
            kind=kind.value,
            subject_type=subject,
            subject_id=research.id,
            status=status.value,
            depends_on=depends_on,
            position=position,
        )
        test_db.add(task)
        test_db.flush()
        return task

    fact_check = add(AgentTaskKind.RESEARCH_FACT_CHECK, AgentTaskStatus.DONE, 10)
    corrections = add(
        AgentTaskKind.APPLY_RESEARCH_CORRECTIONS, AgentTaskStatus.READY, 20, [fact_check.id]
    )
    add(AgentTaskKind.GENERATE_OUTLINE, AgentTaskStatus.PENDING, 30, [corrections.id])
    test_db.commit()

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)
    assert AgentTaskKind.WAIT_RESEARCH_READY.value not in kinds_for(data, subject, research.id)
    outline = tasks[(AgentTaskKind.GENERATE_OUTLINE.value, subject, research.id)]
    assert outline["depends_on"] == [corrections.id]
    # The rows the re-plan adds chain off the legacy corrections row too.
    sabias = tasks[(AgentTaskKind.GENERATE_SABIAS_QUE.value, subject, research.id)]
    assert sabias["depends_on"] == [corrections.id]
    assert data["created"] == 2  # sabías-qué + PLAN_ARTICLES


def test_plan_week_never_skips_article_corrections_behind_the_gate(
    test_client, admin_token, test_db, series
):
    """#555 retired the #481 plan-time skip for articles: a SKIPPED corrections
    row would satisfy the grammar wait at once and route around the gate. A
    resolved-clean article costs one wait-check and one empty preview instead."""
    research, build_series = series
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    mark_done(test_db, AgentTaskKind.GENERATE_SABIAS_QUE)
    parent, children = build_series()
    _seed_claims(test_db, "ARTICLE", parent.id, ["VERIFIED", "VERIFIED"])

    tasks = by_key(test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json())
    article_subject = AgentTaskSubject.ARTICLE.value
    key = AgentTaskKind.APPLY_ARTICLE_CORRECTIONS.value
    gate_key = AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED.value
    for article in (parent, *children):
        corrections = tasks[(key, article_subject, article.id)]
        gate = tasks[(gate_key, article_subject, article.id)]
        assert corrections["status"] == AgentTaskStatus.PENDING.value
        assert corrections["depends_on"] == [gate["id"]]


def test_plan_week_keeps_article_corrections_of_an_in_flight_chain(
    test_client, admin_token, test_db, series
):
    """A chain planned before #555 keeps fact-check -> corrections and gets no
    gate: the corrections row already depends on the fact-check (insert-only),
    so a gate planned now would gate nothing."""
    research, build_series = series
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    mark_done(test_db, AgentTaskKind.GENERATE_SABIAS_QUE)
    parent, children = build_series()
    article_subject = AgentTaskSubject.ARTICLE.value

    # Seed the parent's legacy chain head as the old planner wrote it.
    def add(kind, status, position, depends_on=None):
        task = AgentTask(
            goal_key=GOAL_KEY,
            kind=kind.value,
            subject_type=article_subject,
            subject_id=parent.id,
            status=status.value,
            depends_on=depends_on,
            position=position,
        )
        test_db.add(task)
        test_db.flush()
        return task

    write = add(AgentTaskKind.WRITE_ARTICLE, AgentTaskStatus.DONE, 100)
    fact_check = add(AgentTaskKind.ARTICLE_FACT_CHECK, AgentTaskStatus.DONE, 110, [write.id])
    corrections = add(
        AgentTaskKind.APPLY_ARTICLE_CORRECTIONS, AgentTaskStatus.READY, 120, [fact_check.id]
    )
    test_db.commit()

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)
    gate_key = AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED.value
    assert gate_key not in kinds_for(data, article_subject, parent.id)
    legacy = tasks[(AgentTaskKind.APPLY_ARTICLE_CORRECTIONS.value, article_subject, parent.id)]
    assert legacy["depends_on"] == [fact_check.id]
    # The rest of the legacy chain chains off its corrections row.
    grammar = tasks[(AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN.value, article_subject, parent.id)]
    assert grammar["depends_on"] == [corrections.id]
    # Siblings planned now get the gate.
    for child in children:
        assert gate_key in kinds_for(data, article_subject, child.id)


def test_plan_week_keeps_legacy_grammar_steps_of_an_in_flight_chain(
    test_client, admin_token, test_db, series
):
    """A chain planned before #556 keeps its GRAMMAR_CHECK_EN / _ES rows and
    gets no grammar wait: the next step already depends on the legacy row
    (insert-only), so a wait planned now would gate nothing."""
    research, build_series = series
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    mark_done(test_db, AgentTaskKind.GENERATE_SABIAS_QUE)
    parent, children = build_series()
    article_subject = AgentTaskSubject.ARTICLE.value

    # The parent's legacy chain, as the pre-#556 planner wrote it (grammar
    # steps SKIPPED behind the retired weekly_plan_grammar_checks_enabled flag).
    chain = [
        (AgentTaskKind.WRITE_ARTICLE, AgentTaskStatus.DONE),
        (AgentTaskKind.ARTICLE_FACT_CHECK, AgentTaskStatus.DONE),
        (AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED, AgentTaskStatus.DONE),
        (AgentTaskKind.APPLY_ARTICLE_CORRECTIONS, AgentTaskStatus.DONE),
        (AgentTaskKind.GRAMMAR_CHECK_EN, AgentTaskStatus.SKIPPED),
        (AgentTaskKind.GENERATE_TAGS, AgentTaskStatus.READY),
        (AgentTaskKind.TRANSLATE, AgentTaskStatus.PENDING),
        (AgentTaskKind.GRAMMAR_CHECK_ES, AgentTaskStatus.SKIPPED),
        (AgentTaskKind.GENERATE_FEATURE_IMAGE, AgentTaskStatus.PENDING),
    ]
    rows = {}
    previous = None
    for position, (kind, status) in enumerate(chain, start=100):
        task = AgentTask(
            goal_key=GOAL_KEY,
            kind=kind.value,
            subject_type=article_subject,
            subject_id=parent.id,
            status=status.value,
            depends_on=[previous.id] if previous else None,
            position=position,
        )
        test_db.add(task)
        test_db.flush()
        rows[kind] = previous = task
    test_db.commit()

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)
    parent_kinds = kinds_for(data, article_subject, parent.id)
    assert AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN.value not in parent_kinds
    assert AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES.value not in parent_kinds
    # #557: the legacy ES grammar row already chains off TRANSLATE.
    assert AgentTaskKind.ES_ACCURACY_CHECK.value not in parent_kinds
    # Pass 2 still chains off the legacy chain's tail.
    sections = tasks[(AgentTaskKind.GENERATE_SERIES_SECTIONS.value, article_subject, parent.id)]
    assert sections["depends_on"][0] == rows[AgentTaskKind.GENERATE_FEATURE_IMAGE].id
    # #558: the fan-in takes the legacy GRAMMAR_CHECK_ES as the parent's ES
    # step, and keeps its TRANSLATE because that SKIPPED row satisfies at once.
    assert rows[AgentTaskKind.GRAMMAR_CHECK_ES].id in sections["depends_on"]
    assert rows[AgentTaskKind.TRANSLATE].id in sections["depends_on"]
    for child in children:
        wait_es = tasks[(AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES.value, article_subject, child.id)]
        assert wait_es["id"] in sections["depends_on"]
    # Siblings planned now get the waits, not the LLM steps.
    for child in children:
        kinds = kinds_for(data, article_subject, child.id)
        assert AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN.value in kinds
        assert AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES.value in kinds
        assert AgentTaskKind.GRAMMAR_CHECK_EN.value not in kinds


def test_plan_week_adds_no_accuracy_check_to_an_in_flight_grammar_wait_chain(
    test_client, admin_token, test_db, series
):
    """A chain planned before #557 (its WAIT_GRAMMAR_CHECKED_ES already depends
    on TRANSLATE) gets no ES_ACCURACY_CHECK: insert-only, so a check planned now
    would run in parallel with the wait instead of before it. Siblings planned
    now get it."""
    research, build_series = series
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    mark_done(test_db, AgentTaskKind.GENERATE_SABIAS_QUE)
    parent, children = build_series()
    article_subject = AgentTaskSubject.ARTICLE.value

    chain = [
        (AgentTaskKind.WRITE_ARTICLE, AgentTaskStatus.DONE),
        (AgentTaskKind.ARTICLE_FACT_CHECK, AgentTaskStatus.DONE),
        (AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED, AgentTaskStatus.DONE),
        (AgentTaskKind.APPLY_ARTICLE_CORRECTIONS, AgentTaskStatus.DONE),
        (AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN, AgentTaskStatus.DONE),
        (AgentTaskKind.GENERATE_TAGS, AgentTaskStatus.DONE),
        (AgentTaskKind.TRANSLATE, AgentTaskStatus.DONE),
        (AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES, AgentTaskStatus.READY),
        (AgentTaskKind.GENERATE_FEATURE_IMAGE, AgentTaskStatus.PENDING),
    ]
    rows = {}
    previous = None
    for position, (kind, status) in enumerate(chain, start=100):
        task = AgentTask(
            goal_key=GOAL_KEY,
            kind=kind.value,
            subject_type=article_subject,
            subject_id=parent.id,
            status=status.value,
            depends_on=[previous.id] if previous else None,
            position=position,
        )
        test_db.add(task)
        test_db.flush()
        rows[kind] = previous = task
    test_db.commit()

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)
    assert AgentTaskKind.ES_ACCURACY_CHECK.value not in kinds_for(data, article_subject, parent.id)
    wait_es = tasks[(AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES.value, article_subject, parent.id)]
    assert wait_es["depends_on"] == [rows[AgentTaskKind.TRANSLATE].id]
    for child in children:
        assert AgentTaskKind.ES_ACCURACY_CHECK.value in kinds_for(data, article_subject, child.id)

    # And a re-plan stays stable: nothing new for anyone.
    again = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    assert again["created"] == 0


def test_plan_week_second_call_creates_nothing(test_client, admin_token, research_factory):
    research_factory()

    first = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    second = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()

    assert first["created"] == 4
    assert second["created"] == 0
    assert len(second["tasks"]) == len(first["tasks"])
    assert [t["id"] for t in second["tasks"]] == [t["id"] for t in first["tasks"]]


# --------------------------------------------------------------------------
# Expansion (the fan-out)
# --------------------------------------------------------------------------


def test_plan_week_expands_after_outline_and_drafts(test_client, admin_token, test_db, series):
    research, build_series = series
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})

    # Driver ran the outline + the sabías-que generator.
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    mark_done(test_db, AgentTaskKind.GENERATE_SABIAS_QUE)
    parent, children = build_series()
    posts = [make_sabias_post(test_db, research.id, f"Fact {i}") for i in (1, 2, 3)]

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)
    article_subject = AgentTaskSubject.ARTICLE.value
    post_subject = AgentTaskSubject.INSTAGRAM_POST.value

    # 3 articles x 13 chain steps + promo + second PLAN_ARTICLES + 2 IG chains.
    assert data["created"] == 3 * 13 + 2 + 2 * 3

    for article in [parent, *children]:
        assert kinds_for(data, article_subject, article.id) >= {
            AgentTaskKind.WRITE_ARTICLE.value,
            AgentTaskKind.SET_PUBLISH_DATE.value,
        }
        write = tasks[(AgentTaskKind.WRITE_ARTICLE.value, article_subject, article.id)]
        outline = tasks[
            (AgentTaskKind.GENERATE_OUTLINE.value, AgentTaskSubject.RESEARCH.value, research.id)
        ]
        # Outline is DONE, so the chain head is immediately claimable.
        assert write["depends_on"] == [outline["id"]]
        assert write["status"] == AgentTaskStatus.READY.value

        chain = tasks[(AgentTaskKind.ARTICLE_FACT_CHECK.value, article_subject, article.id)]
        assert chain["depends_on"] == [write["id"]]
        assert chain["status"] == AgentTaskStatus.PENDING.value

        # #555: corrections wait until the article's claims are resolved.
        gate = tasks[
            (AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED.value, article_subject, article.id)
        ]
        assert gate["depends_on"] == [chain["id"]]
        assert gate["status"] == AgentTaskStatus.PENDING.value
        corrections = tasks[
            (AgentTaskKind.APPLY_ARTICLE_CORRECTIONS.value, article_subject, article.id)
        ]
        assert corrections["depends_on"] == [gate["id"]]
        assert corrections["status"] == AgentTaskStatus.PENDING.value

        # #556: the operator's Grammarly pass is a wait — EN after the
        # corrections (and before translation), ES after TRANSLATE. The LLM
        # grammar steps are no longer planned.
        kinds = kinds_for(data, article_subject, article.id)
        assert AgentTaskKind.GRAMMAR_CHECK_EN.value not in kinds
        assert AgentTaskKind.GRAMMAR_CHECK_ES.value not in kinds
        wait_en = tasks[(AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN.value, article_subject, article.id)]
        assert wait_en["depends_on"] == [corrections["id"]]
        assert wait_en["status"] == AgentTaskStatus.PENDING.value
        tags = tasks[(AgentTaskKind.GENERATE_TAGS.value, article_subject, article.id)]
        assert tags["depends_on"] == [wait_en["id"]]
        translate = tasks[(AgentTaskKind.TRANSLATE.value, article_subject, article.id)]
        # #557: the Spanish accuracy check runs between TRANSLATE and the ES
        # grammar wait, so Grammarly polishes Spanish that already says what
        # the English says.
        accuracy = tasks[(AgentTaskKind.ES_ACCURACY_CHECK.value, article_subject, article.id)]
        assert accuracy["depends_on"] == [translate["id"]]
        assert accuracy["status"] == AgentTaskStatus.PENDING.value
        wait_es = tasks[(AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES.value, article_subject, article.id)]
        assert wait_es["depends_on"] == [accuracy["id"]]
        assert wait_es["status"] == AgentTaskStatus.PENDING.value

        # The flag is off, so feature images are planned SKIPPED...
        feature = tasks[(AgentTaskKind.GENERATE_FEATURE_IMAGE.value, article_subject, article.id)]
        assert feature["status"] == AgentTaskStatus.SKIPPED.value
        assert feature["payload"]["reason"].startswith("feature-image generation disabled")
        # ... but a series does get its sections. It regenerates the WHOLE
        # series at once, so beyond its own chain it waits for EVERY article's
        # translation — running it while a sibling is still unwritten burned
        # retry attempts on the 2026-09-02 drive — and (#558) for EVERY
        # article's ES Grammarly wait, the last step that changes the text.
        sections = tasks[
            (AgentTaskKind.GENERATE_SERIES_SECTIONS.value, article_subject, article.id)
        ]
        translate_ids = [
            tasks[(AgentTaskKind.TRANSLATE.value, article_subject, a.id)]["id"]
            for a in [parent, *children]
        ]
        wait_es_ids = [
            tasks[(AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES.value, article_subject, a.id)]["id"]
            for a in [parent, *children]
        ]
        assert sections["depends_on"] == [feature["id"], *translate_ids, *wait_es_ids]
        assert feature["depends_on"] == [wait_es["id"]]
        assert sections["status"] == AgentTaskStatus.PENDING.value

    # Promo + second fan-out point hang off the PARENT's approval.
    approve_parent = tasks[(AgentTaskKind.APPROVE_ARTICLE.value, article_subject, parent.id)]
    promo = tasks[(AgentTaskKind.GENERATE_ARTICLE_PROMO.value, article_subject, parent.id)]
    plan_promo = tasks[(AgentTaskKind.PLAN_ARTICLES.value, article_subject, parent.id)]
    assert promo["depends_on"] == [approve_parent["id"]]
    assert plan_promo["depends_on"] == [promo["id"]]
    assert plan_promo["payload"] == {
        "expands": "article-promo post chain",
        "week_start": PLAN_SATURDAY,
        "goal_key": GOAL_KEY,
    }

    # Exactly two of the three drafts are scheduled; the third is untouched.
    sabias = tasks[
        (AgentTaskKind.GENERATE_SABIAS_QUE.value, AgentTaskSubject.RESEARCH.value, research.id)
    ]
    for post in posts[:2]:
        approve = tasks[(AgentTaskKind.APPROVE_IG_POST.value, post_subject, post.id)]
        render = tasks[(AgentTaskKind.RENDER_IG_POST.value, post_subject, post.id)]
        schedule = tasks[(AgentTaskKind.SCHEDULE_IG_POST.value, post_subject, post.id)]
        assert approve["depends_on"] == [sabias["id"]]
        assert approve["status"] == AgentTaskStatus.READY.value
        assert render["depends_on"] == [approve["id"]]
        assert schedule["depends_on"] == [render["id"]]
    assert kinds_for(data, post_subject, posts[2].id) == set()

    # And the expanded graph is itself stable.
    again = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    assert again["created"] == 0
    assert len(again["tasks"]) == len(data["tasks"])


def test_plan_week_standalone_article_skips_series_sections(
    test_client, admin_token, test_db, research_factory, article_factory
):
    research = research_factory()
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    article = article_factory(title="Solo", research_id=research.id, status="PENDING")

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    sections = by_key(data)[
        (
            AgentTaskKind.GENERATE_SERIES_SECTIONS.value,
            AgentTaskSubject.ARTICLE.value,
            article.id,
        )
    ]
    assert sections["status"] == AgentTaskStatus.SKIPPED.value
    assert sections["payload"] == {"reason": "not a series"}


def test_plan_week_standalone_article_approval_waits_for_its_chain(
    test_client, admin_token, test_db, research_factory, article_factory
):
    """#702: a skipped series-sections row must not let approval jump the chain.

    SKIPPED satisfies a dependency, so an APPROVE_ARTICLE depending only on the
    planned-SKIPPED GENERATE_SERIES_SECTIONS used to start READY — ahead of
    WRITE_ARTICLE and every check after it.
    """
    from app.core.config import get_settings
    from app.services.agent_task_driver import report_task

    research = research_factory()
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    article = article_factory(title="Solo", research_id=research.id, status="PENDING")

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)

    def task(kind):
        return tasks[(kind.value, AgentTaskSubject.ARTICLE.value, article.id)]

    sections = task(AgentTaskKind.GENERATE_SERIES_SECTIONS)
    feature = task(AgentTaskKind.GENERATE_FEATURE_IMAGE)
    wait_es = task(AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES)
    approve = task(AgentTaskKind.APPROVE_ARTICLE)
    publish = task(AgentTaskKind.SET_PUBLISH_DATE)
    assert sections["status"] == AgentTaskStatus.SKIPPED.value
    assert feature["status"] == AgentTaskStatus.SKIPPED.value
    # Through both skipped rows to the last real step of the chain.
    assert approve["depends_on"] == [sections["id"], feature["id"], wait_es["id"]]
    assert approve["status"] == AgentTaskStatus.PENDING.value
    assert publish["depends_on"] == [approve["id"]]
    assert publish["status"] == AgentTaskStatus.PENDING.value

    # The driver's promotion honours the same order: finishing WRITE_ARTICLE
    # promotes the fact-check, not the approval.
    settings = get_settings()
    write = task(AgentTaskKind.WRITE_ARTICLE)
    assert write["status"] == AgentTaskStatus.READY.value
    _, promoted = report_task(
        test_db, settings, write["id"], AgentTaskStatus.DONE.value, None, None
    )
    assert promoted == [task(AgentTaskKind.ARTICLE_FACT_CHECK)["id"]]

    # Only the ES grammar wait finishing unblocks the approval.
    row = test_db.get(AgentTask, wait_es["id"])
    row.status = AgentTaskStatus.READY.value
    test_db.commit()
    _, promoted = report_task(
        test_db, settings, wait_es["id"], AgentTaskStatus.DONE.value, None, None
    )
    assert promoted == [approve["id"]]


def test_plan_week_expands_promo_chain(test_client, admin_token, test_db, series):
    research, build_series = series
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    mark_done(test_db, AgentTaskKind.GENERATE_SABIAS_QUE)
    parent, _ = build_series()
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})

    # Driver ran the promo generator, producing the draft.
    mark_done(test_db, AgentTaskKind.GENERATE_ARTICLE_PROMO)
    promo_post = make_promo_post(test_db, parent.id)

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    assert data["created"] == 3
    tasks = by_key(data)
    post_subject = AgentTaskSubject.INSTAGRAM_POST.value
    approve = tasks[(AgentTaskKind.APPROVE_IG_POST.value, post_subject, promo_post.id)]
    render = tasks[(AgentTaskKind.RENDER_IG_POST.value, post_subject, promo_post.id)]
    schedule = tasks[(AgentTaskKind.SCHEDULE_IG_POST.value, post_subject, promo_post.id)]
    publish = tasks[
        (AgentTaskKind.SET_PUBLISH_DATE.value, AgentTaskSubject.ARTICLE.value, parent.id)
    ]
    assert approve["status"] == AgentTaskStatus.READY.value
    # #576: no planned date — the schedule endpoint derives the promo's time
    # from the article's real publish date, so the step also waits for it.
    assert schedule["payload"] is None
    assert sorted(schedule["depends_on"]) == sorted([render["id"], publish["id"]])


# --------------------------------------------------------------------------
# Instagram posts disabled (#552)
# --------------------------------------------------------------------------

IG_DISABLED_REASON = "instagram posts disabled (weekly_plan_ig_posts_enabled)"


def disable_ig_posts(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "weekly_plan_ig_posts_enabled", False)


def test_ig_posts_flag_defaults_off():
    from app.core.config import Settings

    assert Settings.model_fields["weekly_plan_ig_posts_enabled"].default is False


def test_ig_posts_disabled_skips_generators_and_week_completes(
    test_client, admin_token, test_db, series, monkeypatch
):
    disable_ig_posts(monkeypatch)
    research, build_series = series
    research_subject = AgentTaskSubject.RESEARCH.value
    article_subject = AgentTaskSubject.ARTICLE.value

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)
    sabias = tasks[(AgentTaskKind.GENERATE_SABIAS_QUE.value, research_subject, research.id)]
    assert sabias["status"] == AgentTaskStatus.SKIPPED.value
    assert sabias["payload"] == {"reason": IG_DISABLED_REASON}

    # The outline runs; the research fan-out depends only on it once the
    # (skipped) sabías-que step is satisfied.
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    parent, children = build_series()
    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)

    promo = tasks[(AgentTaskKind.GENERATE_ARTICLE_PROMO.value, article_subject, parent.id)]
    assert promo["status"] == AgentTaskStatus.SKIPPED.value
    assert promo["payload"] == {"reason": IG_DISABLED_REASON}
    # The promo fan-out depends on the skipped promo and, through it (#702), on
    # what the promo waited for — the parent's approval — so it does not run
    # ahead of the article chain.
    approve = tasks[(AgentTaskKind.APPROVE_ARTICLE.value, article_subject, parent.id)]
    assert promo["depends_on"] == [approve["id"]]
    plan_promo = tasks[(AgentTaskKind.PLAN_ARTICLES.value, article_subject, parent.id)]
    assert plan_promo["depends_on"] == [promo["id"], approve["id"]]
    assert plan_promo["status"] == AgentTaskStatus.PENDING.value

    # No drafts were generated, so no Instagram post chain is ever planned.
    assert not any(
        t["subject_type"] == AgentTaskSubject.INSTAGRAM_POST.value for t in data["tasks"]
    )
    # The article chains themselves are untouched by the flag.
    write = tasks[(AgentTaskKind.WRITE_ARTICLE.value, article_subject, parent.id)]
    assert write["status"] == AgentTaskStatus.READY.value

    # The promo fan-out re-running plan-week adds nothing: the week can drain.
    again = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    assert again["created"] == 0


def test_ig_posts_disabled_skips_chains_of_existing_drafts(
    test_client, admin_token, test_db, series, monkeypatch
):
    """Drafts made before the flag (or by hand) get their chain planned SKIPPED."""
    disable_ig_posts(monkeypatch)
    research, build_series = series
    posts = [make_sabias_post(test_db, research.id, f"Fact {i}") for i in (1, 2, 3)]
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    parent, _ = build_series()
    promo_post = make_promo_post(test_db, parent.id)

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)
    post_subject = AgentTaskSubject.INSTAGRAM_POST.value
    for post in (*posts[:2], promo_post):
        for kind in (
            AgentTaskKind.APPROVE_IG_POST,
            AgentTaskKind.RENDER_IG_POST,
            AgentTaskKind.SCHEDULE_IG_POST,
        ):
            task = tasks[(kind.value, post_subject, post.id)]
            assert task["status"] == AgentTaskStatus.SKIPPED.value
            assert task["payload"]["reason"] == IG_DISABLED_REASON
    assert kinds_for(data, post_subject, posts[2].id) == set()
    # Nothing in the goal is left waiting on an Instagram step.
    assert not any(
        t["status"] == AgentTaskStatus.PENDING.value and t["subject_type"] == post_subject
        for t in data["tasks"]
    )


def test_ig_posts_enabled_plans_generators_runnable(test_client, admin_token, test_db, series):
    """Flag on (autouse fixture): today's behaviour — generators are real steps."""
    research, build_series = series
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    parent, _ = build_series()
    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)
    sabias = tasks[
        (AgentTaskKind.GENERATE_SABIAS_QUE.value, AgentTaskSubject.RESEARCH.value, research.id)
    ]
    promo = tasks[
        (AgentTaskKind.GENERATE_ARTICLE_PROMO.value, AgentTaskSubject.ARTICLE.value, parent.id)
    ]
    assert sabias["status"] == AgentTaskStatus.PENDING.value
    assert promo["status"] == AgentTaskStatus.PENDING.value
    assert sabias["payload"] is None
    assert promo["payload"] is None


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


def test_plan_week_target_dates(test_client, admin_token, test_db, series):
    research, build_series = series
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    mark_done(test_db, AgentTaskKind.GENERATE_SABIAS_QUE)
    parent, children = build_series()
    posts = [make_sabias_post(test_db, research.id, f"Fact {i}") for i in (1, 2, 3)]

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)

    # #558: no planned publish date — the endpoint picks it when the step runs.
    for article in [parent, *children]:
        publish = tasks[
            (AgentTaskKind.SET_PUBLISH_DATE.value, AgentTaskSubject.ARTICLE.value, article.id)
        ]
        assert publish["payload"] is None

    post_subject = AgentTaskSubject.INSTAGRAM_POST.value
    assert tasks[(AgentTaskKind.SCHEDULE_IG_POST.value, post_subject, posts[0].id)]["payload"] == {
        "target_date": "2026-08-04"  # Tuesday
    }
    assert tasks[(AgentTaskKind.SCHEDULE_IG_POST.value, post_subject, posts[1].id)]["payload"] == {
        "target_date": "2026-08-05"  # Wednesday
    }


def test_daily_cadence_keys_by_date_and_compresses_targets(
    test_client, admin_token, test_db, series, monkeypatch
):
    """weekly_plan_daily_cadence=true: one goal per DAY (daily_series:YYYY-MM-DD),
    sabías-qué day+1/day+2 (the promo follows the publish date, #576) — the backlog-burn
    cadence (reach 1903 by November)."""
    from app.core.config import get_settings

    # get_settings is lru_cached: patch the singleton, monkeypatch restores it.
    monkeypatch.setattr(get_settings(), "weekly_plan_daily_cadence", True)
    research, build_series = series

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    assert data["goal_key"] == "daily_series:2026-08-01"

    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    mark_done(test_db, AgentTaskKind.GENERATE_SABIAS_QUE)
    parent, children = build_series()
    posts = [make_sabias_post(test_db, research.id, f"Fact {i}") for i in (1, 2, 3)]

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    tasks = by_key(data)

    for article in [parent, *children]:
        publish = tasks[
            (AgentTaskKind.SET_PUBLISH_DATE.value, AgentTaskSubject.ARTICLE.value, article.id)
        ]
        assert publish["payload"] is None

    post_subject = AgentTaskSubject.INSTAGRAM_POST.value
    assert tasks[(AgentTaskKind.SCHEDULE_IG_POST.value, post_subject, posts[0].id)]["payload"] == {
        "target_date": "2026-08-02"
    }
    assert tasks[(AgentTaskKind.SCHEDULE_IG_POST.value, post_subject, posts[1].id)]["payload"] == {
        "target_date": "2026-08-03"
    }


def test_daily_cadence_promo_has_no_planned_date(
    test_client, admin_token, test_db, series, monkeypatch
):
    from app.core.config import get_settings

    # get_settings is lru_cached: patch the singleton, monkeypatch restores it.
    monkeypatch.setattr(get_settings(), "weekly_plan_daily_cadence", True)
    research, build_series = series
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    parent, _ = build_series()
    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    mark_done(test_db, AgentTaskKind.GENERATE_ARTICLE_PROMO)
    promo_post = make_promo_post(test_db, parent.id)

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    schedule = by_key(data)[
        (
            AgentTaskKind.SCHEDULE_IG_POST.value,
            AgentTaskSubject.INSTAGRAM_POST.value,
            promo_post.id,
        )
    ]
    # #576: derived from published_at at schedule time on either cadence.
    assert schedule["payload"] is None


def test_plan_week_defaults_week_start_to_today(test_client, admin_token, research_factory):
    from datetime import UTC, datetime

    research_factory()
    data = test_client.post(URL, json={}).json()

    iso = datetime.now(UTC).date().isocalendar()
    assert data["goal_key"] == f"weekly_series:{iso.year}-W{iso.week:02d}"


# --------------------------------------------------------------------------
# Research resolution and errors
# --------------------------------------------------------------------------


def test_plan_week_404_when_no_research_available(test_client, admin_token):
    response = test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    assert response.status_code == 404
    assert "No research available" in response.json()["detail"]


def test_plan_week_404_for_unknown_research(test_client, admin_token, research_factory):
    research_factory()
    response = test_client.post(URL, json={"research_id": 9999, "week_start": PLAN_SATURDAY})
    assert response.status_code == 404


def test_plan_week_400_when_research_not_approved(test_client, admin_token, research_factory):
    research = research_factory(status="PENDING")
    response = test_client.post(URL, json={"research_id": research.id, "week_start": PLAN_SATURDAY})
    assert response.status_code == 400
    assert "APPROVED" in response.json()["detail"]


def test_plan_week_409_when_override_conflicts_with_goal(
    test_client, admin_token, research_factory
):
    planned = research_factory()
    other = research_factory()

    test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    response = test_client.post(URL, json={"research_id": other.id, "week_start": PLAN_SATURDAY})

    assert response.status_code == 409
    assert str(planned.id) in response.json()["detail"]


def test_plan_week_override_replans_research_with_articles(
    test_client, admin_token, test_db, research_factory, article_factory
):
    """The explicit-research_id recovery path: wave 0 + expansion in one call."""
    research = research_factory()
    article = article_factory(title="Existing", research_id=research.id, status="PENDING")
    make_sabias_post(test_db, research.id, "Fact")

    data = test_client.post(
        URL, json={"research_id": research.id, "week_start": PLAN_SATURDAY}
    ).json()
    tasks = by_key(data)
    research_subject = AgentTaskSubject.RESEARCH.value

    outline = tasks[(AgentTaskKind.GENERATE_OUTLINE.value, research_subject, research.id)]
    sabias = tasks[(AgentTaskKind.GENERATE_SABIAS_QUE.value, research_subject, research.id)]
    assert outline["status"] == AgentTaskStatus.SKIPPED.value
    assert outline["payload"] == {"reason": "articles already exist"}
    assert sabias["status"] == AgentTaskStatus.SKIPPED.value
    assert sabias["payload"] == {"reason": "posts already exist"}

    # SKIPPED satisfies a dependency, but a skipped step passes through to what
    # it waited for (#702): the fan-out and the writing still wait on the
    # research gate, exactly as they would behind a real outline.
    gate = tasks[(AgentTaskKind.WAIT_RESEARCH_READY.value, research_subject, research.id)]
    plan_articles = tasks[(AgentTaskKind.PLAN_ARTICLES.value, research_subject, research.id)]
    assert plan_articles["depends_on"] == [outline["id"], sabias["id"], gate["id"]]
    assert plan_articles["status"] == AgentTaskStatus.PENDING.value
    write = tasks[(AgentTaskKind.WRITE_ARTICLE.value, AgentTaskSubject.ARTICLE.value, article.id)]
    assert write["depends_on"] == [outline["id"], gate["id"]]
    assert write["status"] == AgentTaskStatus.PENDING.value


# --------------------------------------------------------------------------
# Series in flight (#559)
# --------------------------------------------------------------------------


def seed_goal(test_db, goal_key, *statuses):
    """Seed a goal with one APPROVE_ARTICLE row per status (no FK on subjects)."""
    for i, status in enumerate(statuses):
        test_db.add(
            AgentTask(
                goal_key=goal_key,
                kind=AgentTaskKind.APPROVE_ARTICLE.value,
                subject_type=AgentTaskSubject.ARTICLE.value,
                subject_id=9000 + i,
                status=status.value,
                position=10 * (i + 1),
            )
        )
    test_db.commit()


@pytest.fixture
def daily(monkeypatch):
    from app.core.config import get_settings

    # get_settings is lru_cached: patch the singleton, monkeypatch restores it.
    monkeypatch.setattr(get_settings(), "weekly_plan_daily_cadence", True)
    monkeypatch.setattr(get_settings(), "weekly_plan_max_in_flight", 2)


def test_max_in_flight_defaults_to_two():
    from app.core.config import Settings

    assert Settings.model_fields["weekly_plan_max_in_flight"].default == 2


def test_new_goal_at_capacity_answers_200_and_plans_nothing(
    test_client, admin_token, test_db, research_factory, daily
):
    research_factory()
    seed_goal(test_db, "daily_series:2026-07-30", AgentTaskStatus.DONE, AgentTaskStatus.FAILED)
    seed_goal(test_db, "weekly_series:2026-W30", AgentTaskStatus.WAITING)

    response = test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    assert response.status_code == 200
    data = response.json()
    assert data["at_capacity"] is True
    assert data["goal_key"] is None
    assert data["research_id"] is None
    assert data["created"] == 0
    assert data["tasks"] == []
    assert data["max_in_flight"] == 2
    assert data["in_flight_goals"] == ["daily_series:2026-07-30", "weekly_series:2026-W30"]
    assert "At capacity" in data["detail"]
    planned = test_db.query(AgentTask).filter(AgentTask.goal_key == "daily_series:2026-08-01")
    assert planned.count() == 0


def test_at_capacity_wins_over_no_research(test_client, admin_token, test_db, daily):
    """Nothing to plan AND no slot: the driver gets the normal end, not the 404."""
    seed_goal(test_db, "daily_series:2026-07-30", AgentTaskStatus.PENDING)
    seed_goal(test_db, "daily_series:2026-07-31", AgentTaskStatus.READY)

    response = test_client.post(URL, json={"week_start": PLAN_SATURDAY})
    assert response.status_code == 200
    assert response.json()["at_capacity"] is True


def test_finished_goals_do_not_hold_a_slot(
    test_client, admin_token, test_db, research_factory, daily
):
    research = research_factory()
    seed_goal(test_db, "daily_series:2026-07-29", AgentTaskStatus.DONE, AgentTaskStatus.SKIPPED)
    seed_goal(test_db, "daily_series:2026-07-30", AgentTaskStatus.SKIPPED)
    seed_goal(test_db, "daily_series:2026-07-31", AgentTaskStatus.LEASED)

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    assert data["at_capacity"] is False
    assert data["goal_key"] == "daily_series:2026-08-01"
    assert data["research_id"] == research.id
    assert data["created"] > 0
    assert data["in_flight_goals"] == []


def test_the_new_goal_itself_takes_a_slot(test_client, admin_token, research_factory, daily):
    """Two daily calls start two goals; the third day's call is at capacity."""
    for _ in range(3):
        research_factory()

    first = test_client.post(URL, json={"week_start": "2026-08-01"}).json()
    second = test_client.post(URL, json={"week_start": "2026-08-02"}).json()
    third = test_client.post(URL, json={"week_start": "2026-08-03"}).json()

    assert first["goal_key"] == "daily_series:2026-08-01"
    assert second["goal_key"] == "daily_series:2026-08-02"
    assert third["at_capacity"] is True
    assert third["in_flight_goals"] == ["daily_series:2026-08-01", "daily_series:2026-08-02"]


def test_expanding_an_existing_goal_is_never_capped(
    test_client, admin_token, test_db, series, daily
):
    """The PLAN_ARTICLES call re-sends the goal's week_start: it must expand
    even while the cap is reached (the goal itself holds one of the slots)."""
    research, build_series = series
    first = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    assert first["goal_key"] == "daily_series:2026-08-01"
    seed_goal(test_db, "daily_series:2026-07-31", AgentTaskStatus.FAILED)

    mark_done(test_db, AgentTaskKind.WAIT_RESEARCH_READY)
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    mark_done(test_db, AgentTaskKind.GENERATE_SABIAS_QUE)
    parent, children = build_series()

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    assert data["at_capacity"] is False
    assert data["goal_key"] == "daily_series:2026-08-01"
    assert data["created"] > 0
    assert AgentTaskKind.WRITE_ARTICLE.value in kinds_for(
        data, AgentTaskSubject.ARTICLE.value, parent.id
    )


def test_explicit_research_id_is_never_capped(
    test_client, admin_token, test_db, research_factory, daily
):
    research = research_factory()
    seed_goal(test_db, "daily_series:2026-07-30", AgentTaskStatus.FAILED)
    seed_goal(test_db, "daily_series:2026-07-31", AgentTaskStatus.PENDING)

    data = test_client.post(
        URL, json={"research_id": research.id, "week_start": PLAN_SATURDAY}
    ).json()
    assert data["at_capacity"] is False
    assert data["goal_key"] == "daily_series:2026-08-01"
    assert data["research_id"] == research.id
    assert data["created"] > 0


def test_cap_zero_disables_it(
    test_client, admin_token, test_db, research_factory, daily, monkeypatch
):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "weekly_plan_max_in_flight", 0)
    research_factory()
    for day in ("2026-07-28", "2026-07-29", "2026-07-30"):
        seed_goal(test_db, f"daily_series:{day}", AgentTaskStatus.FAILED)

    data = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    assert data["at_capacity"] is False
    assert data["goal_key"] == "daily_series:2026-08-01"


# --- #573: PLAN_ARTICLES carries its goal key --------------------------------


def _goal_with_articles(test_client, test_db, series):
    """Plan the weekly goal at PLAN_SATURDAY and let it reach its expansion."""
    first = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    assert first["goal_key"] == GOAL_KEY
    mark_done(test_db, AgentTaskKind.WAIT_RESEARCH_READY)
    mark_done(test_db, AgentTaskKind.GENERATE_OUTLINE)
    mark_done(test_db, AgentTaskKind.GENERATE_SABIAS_QUE)
    _research, build_series = series
    parent, _children = build_series()
    return parent


def test_explicit_goal_key_expands_a_legacy_row_without_week_start(
    test_client, admin_token, test_db, series, monkeypatch
):
    """A pre-#418 row has no week_start: the recomputed key is today's week,
    which misses the goal and (at the cap) answers at_capacity. The explicit
    goal key expands the real goal instead."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "weekly_plan_daily_cadence", False)
    monkeypatch.setattr(get_settings(), "weekly_plan_max_in_flight", 2)
    parent = _goal_with_articles(test_client, test_db, series)
    seed_goal(test_db, "weekly_series:2026-W30", AgentTaskStatus.FAILED)

    # Today's behaviour without the key: a different (new) goal, refused.
    missed = test_client.post(URL, json={}).json()
    assert missed["at_capacity"] is True

    data = test_client.post(URL, json={"goal_key": GOAL_KEY}).json()
    assert data["at_capacity"] is False
    assert data["goal_key"] == GOAL_KEY
    assert data["created"] > 0
    assert AgentTaskKind.WRITE_ARTICLE.value in kinds_for(
        data, AgentTaskSubject.ARTICLE.value, parent.id
    )


def test_explicit_goal_key_survives_a_cadence_flip(
    test_client, admin_token, test_db, series, monkeypatch
):
    """Weekly goal planned, then weekly_plan_daily_cadence flipped on: the same
    week_start now computes daily_series:..., but the goal key wins."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "weekly_plan_daily_cadence", False)
    monkeypatch.setattr(get_settings(), "weekly_plan_max_in_flight", 2)
    parent = _goal_with_articles(test_client, test_db, series)
    monkeypatch.setattr(get_settings(), "weekly_plan_daily_cadence", True)
    # The flipped cadence's key would be a new goal: fill the second slot.
    seed_goal(test_db, "daily_series:2026-07-30", AgentTaskStatus.FAILED)
    missed = test_client.post(URL, json={"week_start": PLAN_SATURDAY}).json()
    assert missed["at_capacity"] is True

    body = {"week_start": PLAN_SATURDAY, "goal_key": GOAL_KEY}
    data = test_client.post(URL, json=body).json()
    assert data["at_capacity"] is False
    assert data["goal_key"] == GOAL_KEY
    assert data["created"] > 0
    assert AgentTaskKind.WRITE_ARTICLE.value in kinds_for(
        data, AgentTaskSubject.ARTICLE.value, parent.id
    )
    # Replay converges: the second identical call inserts nothing.
    again = test_client.post(URL, json=body).json()
    assert again["created"] == 0
    assert len(again["tasks"]) == len(data["tasks"])


def _flip_to_daily_after_weekly_goal(test_client, test_db, series, monkeypatch):
    """Weekly goal at its expansion, three sabías posts, then cadence flipped
    on with the second in-flight slot taken."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "weekly_plan_daily_cadence", False)
    monkeypatch.setattr(get_settings(), "weekly_plan_max_in_flight", 2)
    parent = _goal_with_articles(test_client, test_db, series)
    research, _ = series
    posts = [make_sabias_post(test_db, research.id, f"Fact {i}") for i in (1, 2, 3)]
    monkeypatch.setattr(get_settings(), "weekly_plan_daily_cadence", True)
    seed_goal(test_db, "daily_series:2026-07-30", AgentTaskStatus.FAILED)
    return parent, posts


def _assert_weekly_sabias_dates(data, posts):
    tasks = by_key(data)
    post_subject = AgentTaskSubject.INSTAGRAM_POST.value
    kind = AgentTaskKind.SCHEDULE_IG_POST.value
    assert tasks[(kind, post_subject, posts[0].id)]["payload"] == {"target_date": "2026-08-04"}
    assert tasks[(kind, post_subject, posts[1].id)]["payload"] == {"target_date": "2026-08-05"}


def test_legacy_row_without_payload_goal_key_expands_its_own_goal_after_a_flip(
    test_client, admin_token, test_db, series, monkeypatch
):
    """#587: a PLAN_ARTICLES row planned before #573 has no payload goal_key.
    The driver now sends the row's own goal_key column, so after a cadence
    flip it still expands its own (weekly) goal, with weekly IG dates."""
    from app.services.agent_task_driver import build_calls

    parent, posts = _flip_to_daily_after_weekly_goal(test_client, test_db, series, monkeypatch)
    row = (
        test_db.query(AgentTask)
        .filter(
            AgentTask.goal_key == GOAL_KEY,
            AgentTask.kind == AgentTaskKind.PLAN_ARTICLES.value,
            AgentTask.subject_type == AgentTaskSubject.RESEARCH.value,
        )
        .one()
    )
    # Make it legacy (JSONB needs whole-dict reassignment to persist).
    row.payload = {
        "expands": "article chains + sabias-que post chains",
        "week_start": PLAN_SATURDAY,
    }
    test_db.commit()

    calls = build_calls(row)
    assert calls[0].path == URL
    assert calls[0].body == {"week_start": PLAN_SATURDAY, "goal_key": GOAL_KEY}

    data = test_client.post(calls[0].path, json=calls[0].body).json()
    assert data["at_capacity"] is False
    assert data["goal_key"] == GOAL_KEY
    assert data["created"] > 0
    assert AgentTaskKind.WRITE_ARTICLE.value in kinds_for(
        data, AgentTaskSubject.ARTICLE.value, parent.id
    )
    # Change 2: the weekly goal keeps its Tue/Wed dates despite the flip.
    _assert_weekly_sabias_dates(data, posts)


def test_sabias_dates_follow_the_goal_key_not_the_live_cadence(
    test_client, admin_token, test_db, series, monkeypatch
):
    """#587: an explicit weekly goal key expanded after the cadence was flipped
    to daily still gets the weekly Tuesday/Wednesday sabías-qué dates."""
    _parent, posts = _flip_to_daily_after_weekly_goal(test_client, test_db, series, monkeypatch)
    body = {"week_start": PLAN_SATURDAY, "goal_key": GOAL_KEY}
    data = test_client.post(URL, json=body).json()
    assert data["goal_key"] == GOAL_KEY
    _assert_weekly_sabias_dates(data, posts)


def test_explicit_goal_key_never_starts_a_goal(test_client, admin_token, test_db, research_factory):
    """A well-formed key with no rows is a 404, not a fresh goal past the cap."""
    research_factory()
    response = test_client.post(URL, json={"goal_key": "weekly_series:2026-W40"})
    assert response.status_code == 404
    assert test_db.query(AgentTask).count() == 0


@pytest.mark.parametrize(
    "bad",
    ["anything", "weekly_series:2026-W3", "daily_series:2026-8-01", "weekly_series:2026-W31 "],
)
def test_explicit_goal_key_shape_is_validated(test_client, admin_token, test_db, bad):
    response = test_client.post(URL, json={"goal_key": bad})
    assert response.status_code == 422
    assert test_db.query(AgentTask).count() == 0
