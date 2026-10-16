"""The request session is released before the request's background tasks (#649).

FastAPI >= 0.118 runs a yield dependency's ``finally`` only after the response's
BackgroundTasks have finished, so ``get_db`` queues a guarded close as the first
background task. These tests drive the REAL ``get_db`` (the ``test_client``
fixture overrides it) through a bare ``FastAPI()`` app.
"""

import logging

from app.core import database
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


def _point_at_test_db(monkeypatch, test_engine):
    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr(database, "SessionLocal", factory)


def test_get_db_releases_connection_before_background_tasks(test_engine, monkeypatch):
    _point_at_test_db(monkeypatch, test_engine)
    seen: dict = {}
    app = FastAPI()

    @app.get("/probe")
    def probe(background_tasks: BackgroundTasks, db: Session = Depends(database.get_db)):
        db.execute(text("SELECT 1"))
        assert db.in_transaction()

        def record():
            seen["in_transaction"] = db.in_transaction()
            seen["checkedout"] = test_engine.pool.checkedout()

        background_tasks.add_task(record)
        return {"ok": True}

    baseline = test_engine.pool.checkedout()
    with TestClient(app) as client:
        response = client.get("/probe")

    assert response.status_code == 200
    assert seen == {"in_transaction": False, "checkedout": baseline}


def test_get_db_release_runs_before_endpoint_tasks_and_session_still_closes(
    test_engine, monkeypatch
):
    _point_at_test_db(monkeypatch, test_engine)
    seen: dict = {}

    def auth_like(db: Session = Depends(database.get_db)):
        # Mirrors the router-level admin auth dependency's SELECT.
        db.execute(text("SELECT 1"))

    router = APIRouter(dependencies=[Depends(auth_like)])

    @router.get("/probe")
    def probe(background_tasks: BackgroundTasks, db: Session = Depends(database.get_db)):
        db.execute(text("SELECT 1"))

        def record():
            seen["in_transaction"] = db.in_transaction()

        background_tasks.add_task(record)
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)

    baseline = test_engine.pool.checkedout()
    with TestClient(app) as client:
        response = client.get("/probe")

    assert response.status_code == 200
    assert seen["in_transaction"] is False
    assert test_engine.pool.checkedout() == baseline


def test_release_failure_does_not_drop_later_background_tasks(monkeypatch, caplog):
    class FlakyCloseSession:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            if self.close_calls == 1:
                raise RuntimeError("close failed")

    monkeypatch.setattr(database, "SessionLocal", FlakyCloseSession)
    flag: dict = {}
    app = FastAPI()

    @app.get("/probe")
    def probe(background_tasks: BackgroundTasks, db=Depends(database.get_db)):
        background_tasks.add_task(lambda: flag.setdefault("ran", True))
        return {"ok": True}

    with caplog.at_level(logging.ERROR, logger="app.core.database"):
        with TestClient(app) as client:
            response = client.get("/probe")

    assert response.status_code == 200
    assert flag.get("ran") is True
    assert any(
        r.name == "app.core.database"
        and r.levelno == logging.ERROR
        and "Failed to release the request DB session" in r.getMessage()
        for r in caplog.records
    )
