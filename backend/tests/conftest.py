"""
Pytest configuration and shared fixtures.

Uses testcontainers to spin up a real PostgreSQL database for realistic testing.
"""

import os

# Force the test environment BEFORE importing app.main, whose module-level
# Sentry init is gated on ENVIRONMENT != "test" (see app/main.py). Without this,
# a local/CI shell that happens to export SENTRY_DSN would ship every test
# fixture failure to production Sentry tagged environment=production. Set
# unconditionally (not setdefault) so a stray ENVIRONMENT=dev/production in the
# shell can't defeat the gate; ENVIRONMENT is only ever read by that gate.
os.environ["ENVIRONMENT"] = "test"
# Point the SMTP relay at an unresolvable host (RFC 2606 reserves .invalid) so
# a send that somehow gets past the ``smtp_outbox`` guard below fails DNS
# instead of reaching the IP-allowlisted Workspace relay, which would deliver
# it as @panamaincontext.com (#563). Unconditional, like ENVIRONMENT.
os.environ["SMTP_RELAY_HOST"] = "smtp.invalid"
# No test may call Voyage for real (#624): the indexer runs as a background task
# behind every article write, so a key in the shell would embed test fixtures.
# Tests that exercise the seam set a fake key with monkeypatch.
os.environ["VOYAGE_API_KEY"] = ""

import smtplib  # noqa: E402
import warnings  # noqa: E402

import docker.errors  # noqa: E402
import pytest  # noqa: E402
import requests  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.search_sql import SEARCH_PREREQUISITES_SQL  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402


class _RecordingSMTP:
    """Stand-in for ``smtplib.SMTP``: records each message, never opens a socket."""

    def __init__(self, outbox, host="", port=0, timeout=None, **kwargs):
        self._outbox = outbox
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self, *args, **kwargs):
        return (250, b"ok")

    def starttls(self, *args, **kwargs):
        return (220, b"ok")

    def send_message(self, msg, *args, **kwargs):
        self._outbox.append(msg)
        return {}

    def quit(self):
        return (221, b"bye")


@pytest.fixture(autouse=True)
def smtp_outbox(monkeypatch):
    """Global SMTP guard (#563): no test can send real email.

    The backend's only transport is ``email_sender.send_email`` over
    ``smtplib.SMTP`` to an IP-allowlisted relay that needs no credentials, so
    an unguarded test delivered real mail from any allowlisted machine (a
    managed-agents sandbox did on 2026-10-05). Replacing ``smtplib.SMTP`` on
    the module covers every caller. Request this fixture by name to assert on
    what was sent: it is the list of ``email.message.Message`` objects. A test
    that patches ``smtplib.SMTP`` or ``send_email`` itself (test_email_sender,
    test_notifications_dispatch) still wins, since its patch is applied later.
    ``send_email`` additionally refuses outright if it ever sees the real class
    under ENVIRONMENT=test (see ``RealSendBlockedError``).
    """
    outbox: list = []
    monkeypatch.setattr(
        smtplib, "SMTP", lambda *args, **kwargs: _RecordingSMTP(outbox, *args, **kwargs)
    )
    return outbox


# PostgreSQL container for testing
@pytest.fixture(scope="session")
def postgres_container():
    """Start a PostgreSQL container for the test session.

    ``PIC_TEST_PG_IMAGE`` overrides the image. CI sets it to ``pic-test-pg:17``,
    built from ``docker/test-db/Dockerfile`` (the ECR mirror of the official
    image plus pgvector), because runners hit Docker Hub's pull rate limit on
    ``pgvector/pgvector``.
    """
    image = os.environ.get("PIC_TEST_PG_IMAGE") or "pgvector/pgvector:pg17"
    postgres = PostgresContainer(image)
    # Explicit start/stop instead of ``with`` (#735): ``__exit__`` lets a
    # Docker timeout on the force-remove fail an all-green session. A start
    # failure still raises (it happens before the ``try``); only the stop is
    # made non-fatal.
    postgres.start()
    try:
        yield postgres
    finally:
        _stop_test_container(postgres)


# Docker errors a teardown may swallow: the API read timing out (60 s default)
# or the daemon refusing/failing the remove. Anything else still raises.
_TEARDOWN_DOCKER_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    docker.errors.APIError,
)


def _stop_test_container(container):
    """Stop and remove a test container; a Docker failure warns, never raises.

    The test Postgres is disposable: on CI the runner is thrown away with it
    (Ryuk is disabled there), and locally Ryuk reaps an orphan when the session
    ends. So a slow ``remove(force=True)`` after ~2,900 passing tests must not
    turn the run red (#735) — it surfaces in the pytest warnings summary.
    """
    try:
        container.stop()
    except _TEARDOWN_DOCKER_ERRORS as exc:
        warnings.warn(
            f"Ignoring a Docker error while removing the test Postgres container "
            f"(it is disposable): {type(exc).__name__}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )


@pytest.fixture(scope="session")
def test_engine(postgres_container):
    """Create SQLAlchemy engine connected to test database."""
    engine = create_engine(
        postgres_container.get_connection_url(),
        pool_pre_ping=True,
    )
    # DB objects the models depend on but create_all cannot make: the
    # extensions and IMMUTABLE wrappers behind the media search_vector
    # generated columns (#590). Same SQL as migration 0041. Plus pgvector
    # (#621, migration 0043) — why the container is the pgvector image.
    with engine.begin() as conn:
        for stmt in SEARCH_PREREQUISITES_SQL:
            conn.execute(text(stmt))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
    # Create all tables
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


def _reset_database(engine):
    """Truncate every table and reset identity sequences for inter-test isolation.

    A single ``TRUNCATE a, b, c ... RESTART IDENTITY CASCADE`` empties all tables
    in one statement (CASCADE resolves FK ordering) and resets every
    auto-increment sequence to its start value.
    """
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="function")
def test_db(test_engine):
    """
    Create a fresh database session for each test.

    The session commits for real (it is bound to the engine, not pinned inside a
    single never-committed outer transaction), and the database is reset with
    ``TRUNCATE ... RESTART IDENTITY CASCADE`` at teardown. This design fixes three
    coupled defects of the old single-transaction + rollback fixture:

    - #205: a service-layer ``db.rollback()`` no longer wipes the test's seeded
      rows — it only discards the service's own uncommitted work, because the
      test's seed was really committed.
    - #233: auto-increment ids no longer drift across tests (sequences are
      non-transactional, so the old rollback never reset them); ``RESTART
      IDENTITY`` makes every test start from a clean sequence, removing
      test-order-dependent flakiness.
    - #140: ``now()`` advances between the test's separate committed
      transactions, so ``onupdate=func.now()`` triggers are observable.
    """
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
    )
    session = TestingSessionLocal()

    try:
        yield session
    finally:
        session.close()
        _reset_database(test_engine)


@pytest.fixture(scope="function")
def test_client(test_db):
    """
    Create a FastAPI test client with the test database session.

    Overrides the get_db dependency to use the test session.
    """

    def override_get_db():
        try:
            yield test_db
        finally:
            pass  # Session cleanup handled by test_db fixture

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def edu_job_session(test_engine):
    """Point the edu job runner's own session factory at the test database.

    The long edu endpoints return 202 and run ``run_edu_job`` as a background
    task (#346), which ``TestClient`` executes after the response; the runner
    opens ``SessionLocal()`` itself rather than using the request's session.
    """
    from unittest.mock import patch

    from app.services import edu_jobs

    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with patch.object(edu_jobs, "SessionLocal", factory):
        yield


@pytest.fixture
def narration_job_session(test_engine):
    """Point the article narration job runner's session factory at the test DB.

    ``generate-audio`` returns 202 and runs ``run_article_narration_job`` as a
    background task (#547) — same reason as ``edu_job_session``.
    """
    from unittest.mock import patch

    from app.services import article_narration_job

    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with patch.object(article_narration_job, "SessionLocal", factory):
        yield


@pytest.fixture
def admin_token(test_db, test_client):
    """Create a valid admin magic-link token and authenticate ``test_client`` with it.

    Admin auth is **header-only** (epic #263 removed the ``?token=`` query
    fallback), so this fixture also sets ``Authorization: Bearer <token>`` as a
    default header on the function-scoped ``test_client``. Existing tests that
    still append ``?token={admin_token}`` to the URL keep passing — the query
    param is simply ignored now; the header is what authenticates. Returns the
    raw token for tests that need the value (e.g. to build a Bearer header
    explicitly or seed related rows).
    """
    from datetime import UTC, datetime, timedelta

    from app.models import AdminEmail, MagicLink, MagicLinkAction

    email = "test-admin@example.com"
    token = "test-admin-token-fixture"
    # validate_admin_token now joins the magic link against admin_emails and
    # requires an active allowlist entry (#56), so the fixture must seed one.
    test_db.add(AdminEmail(email=email, is_active=True))
    link = MagicLink(
        token=token,
        admin_email=email,
        action=MagicLinkAction.ADMIN_ACCESS.value,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    test_db.add(link)
    test_db.commit()
    test_client.headers["Authorization"] = f"Bearer {token}"
    return token


# Factory fixtures for creating test data
@pytest.fixture
def taxonomy_factory(test_db):
    """Factory for creating test taxonomies."""
    from app.models.category import Taxonomy

    def create(name="Test Taxonomy", description="Test taxonomy description"):
        taxonomy = Taxonomy(name=name, description=description)
        test_db.add(taxonomy)
        test_db.commit()
        test_db.refresh(taxonomy)
        return taxonomy

    return create


@pytest.fixture
def category_factory(test_db, taxonomy_factory):
    """Factory for creating test categories."""
    from app.models.category import Category

    def create(name="Test Category", taxonomy=None, description="Test category description"):
        if taxonomy is None:
            taxonomy = taxonomy_factory()
        category = Category(
            name=name,
            taxonomy_id=taxonomy.id,
            description=description,
        )
        test_db.add(category)
        test_db.commit()
        test_db.refresh(category)
        return category

    return create


@pytest.fixture
def suggestion_factory(test_db, category_factory):
    """Factory for creating test article suggestions."""
    from app.models.article_suggestion import ArticleSuggestion

    def create(
        title="Test Suggestion",
        category=None,
        status="APPROVED",
        main_topic="Test topic",
        sub_topics=None,
        point_of_view="narrative",
    ):
        if category is None:
            category = category_factory()
        suggestion = ArticleSuggestion(
            title=title,
            category_id=category.id,
            status=status,
            main_topic=main_topic,
            sub_topics=sub_topics or ["Sub-topic 1", "Sub-topic 2"],
            point_of_view=point_of_view,
        )
        test_db.add(suggestion)
        test_db.commit()
        test_db.refresh(suggestion)
        return suggestion

    return create


@pytest.fixture
def research_factory(test_db, suggestion_factory):
    """Factory for creating test research records."""
    from app.models.research import Research

    def create(
        suggestion=None,
        content="Test research content " * 500,
        status="APPROVED",
    ):
        if suggestion is None:
            suggestion = suggestion_factory()
        research = Research(
            suggestion_id=suggestion.id,
            content=content,
            status=status,
        )
        test_db.add(research)
        test_db.commit()
        test_db.refresh(research)
        return research

    return create


@pytest.fixture
def article_factory(test_db, category_factory, suggestion_factory, research_factory):
    """Factory for creating test articles."""
    from datetime import UTC, datetime, timedelta

    from app.models.article import Article

    # Sentinel so callers can explicitly pass published_at=None to make a
    # not-yet-published article (the list endpoint filters those out).
    UNSET = object()

    def create(
        title="Test Article",
        title_es=None,
        content="Test content",
        excerpt="Test excerpt",
        status="APPROVED",
        category=None,
        series_parent=None,
        series_order=None,
        research_id=None,
        outline=None,
        published_at=UNSET,
    ):
        if category is None:
            category = category_factory()
        if research_id is None:
            # research_id FKs research.id, so it must reference a real row.
            # Reuse the article's own category for the research's suggestion so
            # we don't spawn an extra category that would pollute the
            # category-listing tests.
            suggestion = suggestion_factory(category=category)
            research_id = research_factory(suggestion=suggestion).id
        if published_at is UNSET:
            # Subtract 1 second so we're safely before Postgres `now()` which
            # is fixed at transaction start (the list endpoint filters on
            # `published_at <= now()`).
            published_at = datetime.now(UTC) - timedelta(seconds=1)
        article = Article(
            title=title,
            title_es=title_es,
            content=content,
            excerpt=excerpt,
            status=status,
            category_id=category.id,
            series_parent_id=series_parent.id if series_parent else None,
            series_order=series_order,
            research_id=research_id,
            outline=outline,
            published_at=published_at,
        )
        test_db.add(article)
        test_db.commit()
        test_db.refresh(article)
        return article

    return create
