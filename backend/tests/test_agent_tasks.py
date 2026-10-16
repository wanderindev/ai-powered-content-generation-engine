"""Tests for the agent_tasks model (#416, weekly-series epic #422).

Pure model tests against the testcontainers Postgres: defaults, JSONB/ARRAY
round-trips, the three CHECK constraints, the idempotency unique constraint, and
model ↔ migration enum agreement.
"""

import pytest
from app.models.agent_task import (
    AgentTask,
    AgentTaskKind,
    AgentTaskStatus,
    AgentTaskSubject,
)
from sqlalchemy.exc import IntegrityError


def _task(**overrides) -> AgentTask:
    """A minimal valid task row, with overrides for the field under test."""
    fields = {
        "goal_key": "weekly_series:2026-W31",
        "kind": AgentTaskKind.GENERATE_OUTLINE.value,
        "subject_type": AgentTaskSubject.RESEARCH.value,
        "subject_id": 1,
    }
    fields.update(overrides)
    return AgentTask(**fields)


class TestAgentTaskDefaults:
    def test_minimal_row_gets_server_defaults(self, test_db):
        task = _task()
        test_db.add(task)
        test_db.flush()
        test_db.refresh(task)

        assert task.id is not None
        assert task.status == AgentTaskStatus.PENDING.value
        assert task.attempts == 0
        assert task.position == 0
        assert task.created_at is not None
        assert task.updated_at is not None

    def test_optional_columns_default_to_null(self, test_db):
        task = _task()
        test_db.add(task)
        test_db.flush()
        test_db.refresh(task)

        assert task.depends_on is None
        assert task.payload is None
        assert task.result is None
        assert task.lease_expires_at is None
        assert task.leased_by is None
        assert task.error is None


class TestAgentTaskJsonbAndArray:
    def test_payload_result_and_depends_on_round_trip(self, test_db):
        task = _task(
            payload={"target_publish_date": "2026-07-31"},
            result={"ok": True},
            depends_on=[1, 2],
        )
        test_db.add(task)
        test_db.flush()
        test_db.expire(task)

        assert task.payload == {"target_publish_date": "2026-07-31"}
        assert task.result == {"ok": True}
        assert task.depends_on == [1, 2]

    def test_empty_dependency_array_persists(self, test_db):
        """NULL and [] both mean "no dependencies"; [] must survive a round-trip."""
        task = _task(depends_on=[])
        test_db.add(task)
        test_db.flush()
        test_db.expire(task)

        assert task.depends_on == []


class TestAgentTaskConstraints:
    def test_kind_check_rejects_unknown_value(self, test_db):
        test_db.add(_task(kind="NOT_A_KIND"))
        with pytest.raises(IntegrityError):
            test_db.flush()
        test_db.rollback()

    def test_status_check_rejects_unknown_value(self, test_db):
        test_db.add(_task(status="RUNNING"))
        with pytest.raises(IntegrityError):
            test_db.flush()
        test_db.rollback()

    def test_subject_type_check_rejects_unknown_value(self, test_db):
        test_db.add(_task(subject_type="suggestion"))
        with pytest.raises(IntegrityError):
            test_db.flush()
        test_db.rollback()


class TestAgentTaskIdempotency:
    def test_duplicate_goal_kind_subject_is_rejected(self, test_db):
        test_db.add(_task())
        test_db.flush()

        test_db.add(_task())
        with pytest.raises(IntegrityError):
            test_db.flush()
        test_db.rollback()

    def test_same_kind_on_a_different_subject_is_allowed(self, test_db):
        test_db.add(_task(subject_id=1))
        test_db.flush()
        test_db.add(_task(subject_id=2))
        test_db.flush()

        assert test_db.query(AgentTask).count() == 2

    def test_same_task_in_a_different_goal_is_allowed(self, test_db):
        test_db.add(_task(goal_key="weekly_series:2026-W31"))
        test_db.flush()
        test_db.add(_task(goal_key="weekly_series:2026-W32"))
        test_db.flush()

        assert test_db.query(AgentTask).count() == 2


def _load_migration(filename: str):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigrationAgreement:
    def test_migration_0025_matches_enums(self):
        """The 0025 migration's CHECK values agree with the model enums.

        Status and kind only historically: 0035 later widened the status
        vocabulary (#553) and 0036 the kind vocabulary (#554).
        """
        module = _load_migration("0025_agent_tasks.py")

        kinds = _load_migration("0036_research_ready_gate.py")
        assert set(module._KIND_VALUES) == set(kinds._OLD_VALUES)
        assert set(module._SUBJECT_TYPE_VALUES) == set(AgentTaskSubject.values())
        widened = _load_migration("0035_agent_task_waiting.py")
        assert set(module._STATUS_VALUES) == set(widened._OLD_VALUES)

    def test_migration_0035_added_waiting(self):
        """0035 historically added exactly WAITING to the status vocabulary."""
        module = _load_migration("0035_agent_task_waiting.py")
        assert set(module._NEW_VALUES) - set(module._OLD_VALUES) == {"WAITING"}

    def test_migration_0036_added_the_research_ready_gate(self):
        """0036 historically added exactly WAIT_RESEARCH_READY to the kinds."""
        module = _load_migration("0036_research_ready_gate.py")
        assert set(module._NEW_VALUES) - set(module._OLD_VALUES) == {"WAIT_RESEARCH_READY"}

    def test_migration_0037_added_the_article_claims_gate(self):
        """0037 historically added exactly WAIT_ARTICLE_CLAIMS_RESOLVED to the kinds."""
        module = _load_migration("0037_article_claims_gate.py")
        previous = _load_migration("0036_research_ready_gate.py")
        assert set(module._OLD_VALUES) == set(previous._NEW_VALUES)
        assert set(module._NEW_VALUES) - set(module._OLD_VALUES) == {"WAIT_ARTICLE_CLAIMS_RESOLVED"}

    def test_migration_0038_added_the_grammar_waits(self):
        """0038 historically added exactly the two grammar wait kinds (#556)."""
        module = _load_migration("0038_grammar_waits.py")
        previous = _load_migration("0037_article_claims_gate.py")
        assert set(module._OLD_VALUES) == set(previous._NEW_VALUES)
        assert set(module._NEW_VALUES) - set(module._OLD_VALUES) == {
            "WAIT_GRAMMAR_CHECKED_EN",
            "WAIT_GRAMMAR_CHECKED_ES",
        }

    def test_migration_0039_added_the_es_accuracy_check(self):
        """0039 historically added exactly ES_ACCURACY_CHECK to the kinds and
        ES_ACCURACY to the grammar-fix sources (#557)."""
        module = _load_migration("0039_es_accuracy_check.py")
        previous = _load_migration("0038_grammar_waits.py")
        assert set(module._OLD_VALUES) == set(previous._NEW_VALUES)
        assert set(module._NEW_VALUES) - set(module._OLD_VALUES) == {"ES_ACCURACY_CHECK"}
        sources = _load_migration("0032_grammar_fix_source.py")
        assert set(module._OLD_SOURCES) == set(sources._SOURCES)
        assert set(module._NEW_SOURCES) - set(module._OLD_SOURCES) == {"ES_ACCURACY"}
