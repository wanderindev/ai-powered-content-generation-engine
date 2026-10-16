"""The test-Postgres teardown must not fail a green session (#735)."""

import docker.errors
import pytest
import requests

from tests.conftest import _stop_test_container


class _StubContainer:
    def __init__(self, exc=None):
        self.exc = exc
        self.stopped = False

    def stop(self):
        self.stopped = True
        if self.exc is not None:
            raise self.exc


def test_clean_stop_does_not_warn(recwarn):
    container = _StubContainer()
    _stop_test_container(container)
    assert container.stopped
    assert not [w for w in recwarn if issubclass(w.category, RuntimeWarning)]


@pytest.mark.parametrize(
    "exc",
    [
        requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)"),
        requests.exceptions.ConnectionError("daemon gone"),
        docker.errors.APIError("removal of container is already in progress"),
    ],
)
def test_docker_error_on_stop_warns_instead_of_raising(exc):
    with pytest.warns(RuntimeWarning, match="test Postgres container"):
        _stop_test_container(_StubContainer(exc))


def test_unrelated_error_on_stop_still_raises():
    with pytest.raises(ValueError):
        _stop_test_container(_StubContainer(ValueError("bug")))
