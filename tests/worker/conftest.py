import sys

import pytest


@pytest.fixture(autouse=True)
def _fresh_handler_imports():
    """Force a fresh import of worker handler modules for each test.

    Handler modules register themselves into `runner.JOB_HANDLERS` as an
    import-time side effect (see `app/worker/handlers/email.py`). Python
    caches imports in `sys.modules`, so once any test in this session has
    imported `app.worker.handlers.email` (e.g. `test_email_handler.py`), a
    later `import app.worker.handlers.email` inside `register()`
    (`app/worker/__main__.py`) becomes a no-op and does not re-register the
    handler - which breaks `test_register_wires_email_handler` purely from
    test collection order (alphabetically, `test_email_handler.py` runs
    before `test_entrypoint.py`). Evicting the module before/after each test
    keeps the behaviour independent of collection order. Same story for
    `app.worker.handlers.scheduled` (imported directly by
    `test_scheduled_handlers.py`).
    """
    sys.modules.pop("app.worker.handlers.email", None)
    sys.modules.pop("app.worker.handlers.scheduled", None)
    sys.modules.pop("app.worker.handlers.ai", None)
    sys.modules.pop("app.worker.handlers.plan", None)
    yield
    sys.modules.pop("app.worker.handlers.email", None)
    sys.modules.pop("app.worker.handlers.scheduled", None)
    sys.modules.pop("app.worker.handlers.ai", None)
    sys.modules.pop("app.worker.handlers.plan", None)
