"""Semantic tests for the L0-19 ``/metrics`` endpoint scaffold.

Pattern (a) with absence-of-route as the Red semantic failure: before
Green lands, ``GET /metrics`` returns 404 because the router is not
yet included in ``src/app.py``. Green lands ``src/routes/metrics.py``
AND adds ``app.include_router(metrics_router)`` in ``src/app.py``
(permitted leaf-route modification per v2 §7.2 RATIFIED).

Response contract:

- Status 200.
- Content-Type ``text/plain; version=0.0.4`` — the Prometheus text
  exposition format version token. Scrapers parse this header to
  decide which grammar to use.
- Body is valid Prometheus text (comment lines + metric lines). At L0
  the body is a placeholder: a single ``# HELP`` comment and a single
  ``adcp_up`` gauge = 1. L1+ extends with real metrics.

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-19``.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient bound to the real ``src.app.app``.

    The metrics endpoint is leaf-included at the root app level per
    §7.2 — importing ``app`` exercises the include_router wiring.
    """
    from src.app import app

    return TestClient(app)


def test_metrics_endpoint_returns_200(client: TestClient) -> None:
    """``GET /metrics`` returns 200 OK."""
    response = client.get("/metrics")
    assert response.status_code == 200, (
        f"Expected 200 from /metrics; got {response.status_code}. "
        "The metrics router is likely not included in src/app.py yet."
    )


def test_metrics_content_type_is_prometheus_text(client: TestClient) -> None:
    """The ``Content-Type`` header carries the Prometheus text version.

    Prometheus scrapers treat the ``version=0.0.4`` token as the
    grammar discriminator. Returning ``text/plain`` without the
    version parameter breaks modern scrapers.
    """
    response = client.get("/metrics")
    ct = response.headers.get("content-type", "")
    assert "text/plain" in ct, ct
    assert "version=0.0.4" in ct, ct


def test_metrics_body_has_prometheus_format(client: TestClient) -> None:
    """The body is valid Prometheus text format.

    At L0 the body is a placeholder gauge — only the shape is checked:
    at least one ``# HELP`` or ``# TYPE`` comment followed by a
    metric line. L1+ extends with real metrics; this test guards the
    contract, not the specific metrics.
    """
    response = client.get("/metrics")
    body = response.text
    lines = body.splitlines()
    # Must have at least one comment AND at least one non-comment line.
    comments = [ln for ln in lines if ln.startswith("#")]
    metric_lines = [ln for ln in lines if ln and not ln.startswith("#")]
    assert comments, f"No Prometheus comment lines in body: {body!r}"
    assert metric_lines, f"No Prometheus metric lines in body: {body!r}"
