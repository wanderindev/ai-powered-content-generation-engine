"""The newest migration behind each drop-and-recreate CHECK matches its enum (#572).

The test DB is built from model metadata (``Base.metadata.create_all``), so a
typo in a CHECK-vocabulary migration never shows up under pytest — only in
prod, when the kickoff that inserts the new value 500s. The per-migration tests
(``test_migration_0025_matches_enums`` and friends) are deliberately
*historical*: each pins what one migration did, so none of them notices when the
**latest** rebuild of a constraint disagrees with the enum.

This module is the present-tense check. For every constraint in
``CONSTRAINT_ENUMS`` it walks the Alembic revision chain from the head down,
finds the newest revision whose ``upgrade()`` creates that constraint, and
asserts the value set it creates equals the enum's ``values()``. Nothing here
names a migration file, so a new migration needs no test edit; a new
drop-and-recreate constraint needs one line in ``CONSTRAINT_ENUMS``.

Discovery convention (the only one the test relies on): a revision touches a
constraint when its source contains the constraint name as a literal string,
and it declares the vocabulary as a ``'<value>'`` list inside the CHECK
condition. The value set is read by running ``upgrade()`` against a recording
stand-in for ``alembic.op``, so it does not matter whether a migration calls the
tuple ``_NEW_VALUES``, ``_NEW_SOURCES`` or ``_TYPE_VALUES``, nor whether it uses
``op.create_check_constraint`` or a ``sa.CheckConstraint`` inside
``op.create_table``.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.models.enums import (
    AgentTaskAction,
    AgentTaskKind,
    AgentTaskStatus,
    ConsistencyResolution,
    ConsistencySource,
    ConsistencyVerdict,
    CrawlJobType,
    GrammarFixSource,
    KnowledgeSourceType,
    McpApprovalAction,
    McpApprovalStatus,
    McpCallOutcome,
    McpScope,
)
from sqlalchemy import CheckConstraint

# Constraint name -> the enum whose values() it must allow, exactly.
CONSTRAINT_ENUMS = {
    "ck_agent_tasks_kind": AgentTaskKind,
    "ck_grammar_check_fixes_source": GrammarFixSource,
    "ck_crawl_jobs_job_type": CrawlJobType,
    "ck_knowledge_chunks_source_type": KnowledgeSourceType,
    "ck_consistency_findings_verdict": ConsistencyVerdict,
    "ck_consistency_findings_other_source_type": ConsistencySource,
    "ck_consistency_findings_resolution": ConsistencyResolution,
    "ck_mcp_tokens_scopes": McpScope,
    "ck_mcp_tool_calls_outcome": McpCallOutcome,
    "ck_mcp_tool_calls_required_scope": McpScope,
    "ck_agent_task_actions_action": AgentTaskAction,
    "ck_agent_task_actions_previous_status": AgentTaskStatus,
    "ck_mcp_approval_requests_action": McpApprovalAction,
    "ck_mcp_approval_requests_status": McpApprovalStatus,
}

_ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"


def _script_directory() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    return ScriptDirectory.from_config(config)


class _RecordingOp:
    """Stand-in for ``alembic.op`` that records every CHECK an upgrade creates.

    Any other ``op`` call (drop_constraint, execute, add_column, ...) is
    accepted and ignored.
    """

    def __init__(self):
        self.created: dict[str, str] = {}

    def create_check_constraint(self, name, table_name, condition, *args, **kwargs):
        self.created[name] = str(condition)

    def create_table(self, table_name, *columns, **kwargs):
        for item in columns:
            if isinstance(item, CheckConstraint) and isinstance(item.name, str):
                self.created[item.name] = str(item.sqltext)

    def __getattr__(self, name):
        return MagicMock(name=f"op.{name}")


def _created_checks(script) -> dict[str, str]:
    """Run a revision's ``upgrade()`` against the recorder; return name -> condition."""
    module = script.module
    recorder = _RecordingOp()
    original = module.op
    module.op = recorder
    try:
        module.upgrade()
    finally:
        module.op = original
    return recorder.created


def _newest_vocabulary(constraint: str) -> tuple[str, set[str]]:
    """Return (revision id, allowed values) of the newest migration creating ``constraint``."""
    scripts = _script_directory()
    assert len(scripts.get_heads()) == 1, "expected a single Alembic head"
    # walk_revisions() yields head first, then down the down_revision chain.
    for script in scripts.walk_revisions():
        if constraint not in Path(script.path).read_text():
            continue
        created = _created_checks(script)
        assert constraint in created, (
            f"{script.revision} is the newest migration naming {constraint} but its "
            "upgrade() does not create it; if the CHECK was dropped for good, remove it "
            "from CONSTRAINT_ENUMS"
        )
        values = re.findall(r"'([^']*)'", created[constraint])
        assert len(values) == len(set(values)), f"{script.revision} lists a value twice"
        return script.revision, set(values)
    raise AssertionError(f"no migration creates {constraint}")


@pytest.mark.parametrize("constraint", list(CONSTRAINT_ENUMS))
def test_newest_migration_vocabulary_matches_enum(constraint):
    """The latest rebuild of each CHECK allows exactly its enum's values."""
    revision, migration_values = _newest_vocabulary(constraint)
    enum_values = set(CONSTRAINT_ENUMS[constraint].values())

    assert migration_values == enum_values, (
        f"{revision} rebuilds {constraint} with a vocabulary that differs from "
        f"{CONSTRAINT_ENUMS[constraint].__name__}: "
        f"missing from migration {sorted(enum_values - migration_values)}, "
        f"not in enum {sorted(migration_values - enum_values)}"
    )
