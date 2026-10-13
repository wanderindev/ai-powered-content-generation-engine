"""Tests for the unified status/type CHECK constraints (issue #24).

Three layers of protection:

1. **Wiring/drift** — every constrained column declares a ``ck_<table>_<column>``
   CHECK whose allowed values exactly match its Python enum(s) in
   ``app.models.enums``. This is the single source of truth.
2. **Migration agreement** — the ``0006`` migration's snapshot of value sets
   matches the live model metadata, so prod and the ORM can't drift.
3. **Enforcement** — inserting an out-of-vocabulary value raises
   ``IntegrityError`` (the test DB is built from this same metadata, so the
   CHECKs are real).
"""

import importlib.util
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.core.database import Base
from app.models import (
    ContactSubmission,
    Educator,
    EduMaterial,
    EduResearch,
    EduSuggestion,
    MagicLink,
    MediaCandidate,
    Order,
)
from app.models.enums import (
    BookingStatus,
    CandidateSource,
    CandidateStatus,
    ContactInterest,
    ContentStatus,
    EducatorStatus,
    Language,
    MagicLinkAction,
    MaterialType,
    OrderStatus,
    PaymentMethod,
    TimeSlot,
    TransportDirection,
)
from sqlalchemy import CheckConstraint
from sqlalchemy.exc import IntegrityError

# (table, column) -> enum(s) whose union defines the allowed values.
EXPECTED = {
    ("articles", "outline_status"): (ContentStatus,),
    ("bookings", "status"): (BookingStatus,),
    ("bookings", "time_slot"): (TimeSlot,),
    ("bookings", "transport_direction"): (TransportDirection,),
    ("booking_status_log", "previous_status"): (BookingStatus, OrderStatus),
    ("booking_status_log", "new_status"): (BookingStatus, OrderStatus),
    ("contact_submissions", "interest"): (ContactInterest,),
    ("edu_materials", "material_type"): (MaterialType,),
    ("educators", "status"): (EducatorStatus,),
    ("educators", "language"): (Language,),
    ("magic_links", "action"): (MagicLinkAction,),
    ("media_candidates", "status"): (CandidateStatus,),
    ("media_candidates", "source"): (CandidateSource,),
    ("orders", "status"): (OrderStatus,),
    ("orders", "payment_method"): (PaymentMethod,),
    ("orders", "customer_language"): (Language,),
}


def _expected_values(enums):
    """Ordered de-duplicated union of the enums' values."""
    out = []
    for enum_cls in enums:
        for value in enum_cls.values():
            if value not in out:
                out.append(value)
    return out


def _model_allowed(table, column):
    """Allowed values declared by the model's ck_<table>_<column> constraint."""
    name = f"ck_{table}_{column}"
    tbl = Base.metadata.tables[table]
    for constraint in tbl.constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == name:
            return set(re.findall(r"'([^']*)'", str(constraint.sqltext)))
    raise AssertionError(f"model {table} is missing CHECK constraint {name}")


@pytest.mark.parametrize(("table", "column"), list(EXPECTED), ids=lambda v: v)
def test_model_check_matches_enum(table, column):
    """Each model CHECK exists and lists exactly its enum(s) values."""
    assert _model_allowed(table, column) == set(_expected_values(EXPECTED[(table, column)]))


def _load_migration_constraints():
    """Import the _CONSTRAINTS table from the 0006 migration by file path."""
    path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "0006_status_check_constraints.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0006", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {(t, c): list(v) for t, c, v in module._CONSTRAINTS}


def test_migration_matches_models():
    """The 0006 migration's value sets agree with the models / enums."""
    migration = _load_migration_constraints()
    assert set(migration) == set(EXPECTED), "migration and model columns diverged"
    for key, enums in EXPECTED.items():
        assert set(migration[key]) == set(_expected_values(enums)), f"{key} value set diverged"


# --- Enforcement: out-of-vocabulary inserts must be rejected by the DB --------
# Builders for standalone tables (no required FK rows) so each case is one insert.
def _bad_order_status():
    return Order(
        reference="ORD-BAD1",
        customer_name="A",
        customer_email="a@b.com",
        customer_phone="1",
        subtotal=1,
        grand_total=1,
        status="NOT_A_REAL_STATUS",
    )


def _bad_order_payment_method():
    return Order(
        reference="ORD-BAD2",
        customer_name="A",
        customer_email="a@b.com",
        customer_phone="1",
        subtotal=1,
        grand_total=1,
        payment_method="BITCOIN",
    )


def _bad_order_language():
    return Order(
        reference="ORD-BAD3",
        customer_name="A",
        customer_email="a@b.com",
        customer_phone="1",
        subtotal=1,
        grand_total=1,
        customer_language="fr",
    )


def _bad_educator_status():
    return Educator(email="e1@x.com", status="ACTIVE")


def _bad_educator_language():
    return Educator(email="e2@x.com", language="fr")


def _bad_candidate_status():
    return MediaCandidate(
        source=CandidateSource.WIKIMEDIA.value,
        external_id="x",
        external_url="http://x",
        title="t",
        status="MAYBE",
    )


def _bad_candidate_source():
    return MediaCandidate(
        source="FLICKR",
        external_id="x",
        external_url="http://x",
        title="t",
    )


def _bad_magic_link_action():
    return MagicLink(
        token="tok-bad",
        action="DELETE_EVERYTHING",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _bad_material_type():
    return EduMaterial(material_type="hologram", title="t")


def _bad_contact_interest():
    return ContactSubmission(
        interest="aliens",
        first_name="A",
        last_name="B",
        email="a@b.com",
    )


ENFORCEMENT_CASES = {
    "orders.status": _bad_order_status,
    "orders.payment_method": _bad_order_payment_method,
    "orders.customer_language": _bad_order_language,
    "educators.status": _bad_educator_status,
    "educators.language": _bad_educator_language,
    "media_candidates.status": _bad_candidate_status,
    "media_candidates.source": _bad_candidate_source,
    "magic_links.action": _bad_magic_link_action,
    "edu_materials.material_type": _bad_material_type,
    "contact_submissions.interest": _bad_contact_interest,
}


@pytest.mark.parametrize("case", list(ENFORCEMENT_CASES), ids=list(ENFORCEMENT_CASES))
def test_invalid_value_rejected(test_db, case):
    """A value outside the enum vocabulary triggers an IntegrityError."""
    test_db.add(ENFORCEMENT_CASES[case]())
    with pytest.raises(IntegrityError):
        test_db.flush()


@pytest.mark.parametrize("case", list(ENFORCEMENT_CASES), ids=list(ENFORCEMENT_CASES))
def test_valid_value_accepted(test_db, case):
    """Sanity: a representative valid row of each kind still inserts cleanly."""
    valid = {
        "orders.status": lambda: Order(
            reference="ORD-OK",
            customer_name="A",
            customer_email="a@b.com",
            customer_phone="1",
            subtotal=1,
            grand_total=1,
            status=OrderStatus.PAID.value,
        ),
        "educators.status": lambda: Educator(
            email="ok@x.com", status=EducatorStatus.CONFIRMED.value
        ),
        "media_candidates.status": lambda: MediaCandidate(
            source=CandidateSource.LOC.value,
            external_id="ok",
            external_url="http://ok",
            title="t",
            status=CandidateStatus.APPROVED.value,
        ),
        "magic_links.action": lambda: MagicLink(
            token="tok-ok",
            action=MagicLinkAction.VIEW_ORDER.value,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        "edu_materials.material_type": lambda: EduMaterial(
            material_type=MaterialType.SLIDES.value,
            title="t",
            research_id=_seed_edu_research(test_db),
        ),
        "contact_submissions.interest": lambda: ContactSubmission(
            interest=ContactInterest.TOURS.value,
            first_name="A",
            last_name="B",
            email="a@b.com",
        ),
    }
    if case not in valid:
        pytest.skip("covered by another case's table")
    test_db.add(valid[case]())
    test_db.flush()  # must not raise


# --- EduMaterial discriminated-union & natural-key constraints (#95 / #97) ----
def _seed_edu_research(test_db) -> int:
    """Create a minimal approved edu_research row and return its id."""
    suggestion = EduSuggestion(title="t")
    test_db.add(suggestion)
    test_db.flush()
    research = EduResearch(suggestion_id=suggestion.id, content="x")
    test_db.add(research)
    test_db.flush()
    return research.id


def test_edu_material_rejects_both_research_fks(test_db):
    """#95: a row with BOTH research FKs set violates the one-FK CHECK."""
    research_id = _seed_edu_research(test_db)
    test_db.add(
        EduMaterial(
            material_type=MaterialType.GUIDE.value,
            title="t",
            research_id=research_id,
            blog_research_id=1,
        )
    )
    with pytest.raises(IntegrityError):
        test_db.flush()


def test_edu_material_rejects_neither_research_fk(test_db):
    """#95: a row with NEITHER research FK set violates the one-FK CHECK."""
    test_db.add(EduMaterial(material_type=MaterialType.GUIDE.value, title="t"))
    with pytest.raises(IntegrityError):
        test_db.flush()


def test_edu_material_rejects_duplicate_natural_key(test_db):
    """#97: two guides for the same (research_id, material_type, grade_band) collide."""
    research_id = _seed_edu_research(test_db)
    test_db.add(
        EduMaterial(
            research_id=research_id,
            material_type=MaterialType.GUIDE.value,
            grade_band="3rd-6th",
            title="first",
        )
    )
    test_db.flush()
    test_db.add(
        EduMaterial(
            research_id=research_id,
            material_type=MaterialType.GUIDE.value,
            grade_band="3rd-6th",
            title="dup",
        )
    )
    with pytest.raises(IntegrityError):
        test_db.flush()
