"""Logging configuration installed by ``app.main`` (issue #424)."""

import logging

from app.main import _configure_logging


def test_app_namespace_is_info_after_importing_main():
    """The acceptance criterion from issue #424.

    ``tests/conftest.py`` imports ``app.main`` at collection time, so the
    module-level ``_configure_logging`` call has already run by the time this
    executes. The ``app``-level assertion is real (``setLevel`` always
    applies); the root assertion is a regression guard against someone
    switching the root to INFO, which would flood the logs with third-party
    chatter.
    """
    import app.main  # noqa: F401  (explicit — do not rely on conftest's import)

    assert logging.getLogger("app").getEffectiveLevel() == logging.INFO
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING


def test_configure_logging_installs_root_handler_at_warning():
    """Non-vacuous: run the configurator against a pristine root logger.

    Under pytest the root logger already carries the plugin's capture
    handlers, and ``logging.basicConfig`` is a no-op when
    ``root.handlers`` is non-empty — so the handler/root-level half of the
    configuration can only be observed by isolating it here.
    """
    root = logging.getLogger()
    app_logger = logging.getLogger("app")
    saved_handlers = root.handlers[:]
    saved_root_level = root.level
    saved_app_level = app_logger.level
    try:
        root.handlers = []
        root.setLevel(logging.NOTSET)
        app_logger.setLevel(logging.NOTSET)

        _configure_logging("INFO")

        assert root.handlers, "basicConfig should have installed a root handler"
        assert root.level == logging.WARNING
        assert app_logger.getEffectiveLevel() == logging.INFO
        # third-party namespaces must stay quiet
        assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_root_level)
        app_logger.setLevel(saved_app_level)
