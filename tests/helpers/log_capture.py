"""Log-capture handler for asserting on production log output in tests.

Attach directly to the producing module's logger instead of relying on
pytest's ``caplog`` (which captures at the root logger and silently loses
records when suite-level code reconfigures root handlers or disables
propagation on an ancestor logger).

Usage:
    with capture_logs("src.core.tools.creatives.listing") as captured:
        ...exercise production...
    assert any("expected message" in r for r in captured.records)
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager


class LogCaptureHandler(logging.Handler):
    """Captures formatted log records into a list for assertion in tests."""

    def __init__(self, level: int = logging.WARNING) -> None:
        super().__init__(level=level)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


@contextmanager
def capture_logs(logger_name: str, *, level: int = logging.WARNING) -> Iterator[LogCaptureHandler]:
    """Capture records from ``logger_name`` for the duration of the block.

    Attaching and detaching is the whole ceremony, and getting the ``finally``
    wrong leaks a handler into every later test in the session — which is why it
    lives here once rather than being hand-rolled per call site.
    """
    handler = LogCaptureHandler(level=level)
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
