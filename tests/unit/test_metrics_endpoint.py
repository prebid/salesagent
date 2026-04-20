"""Semantic tests for the L0-19 ``/metrics`` endpoint.

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
- Body is valid Prometheus text (``# HELP`` + ``# TYPE`` comments
  plus metric lines) rendered from the live
  ``prometheus_client`` registry by
  ``src.core.metrics.get_metrics_text()``. This keeps the FastAPI
  ``/metrics`` byte-equivalent to Flask's ``core_bp.metrics`` at
  ``src/admin/routers/core.py`` — both transports observe the same
  registry, so Prometheus scrapers see the same metrics regardless
  of which route handles them.

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
    """The body is valid Prometheus text format from the live registry.

    The route delegates to ``src.core.metrics.get_metrics_text()``
    which serializes the real ``prometheus_client`` REGISTRY — so the
    body carries ``# HELP`` + ``# TYPE`` header comments and at least
    one ``metric_name{...} value`` line. This test guards the shape,
    not specific metrics, so it stays stable as metrics are added.
    """
    response = client.get("/metrics")
    body = response.text
    lines = body.splitlines()
    help_lines = [ln for ln in lines if ln.startswith("# HELP")]
    type_lines = [ln for ln in lines if ln.startswith("# TYPE")]
    metric_lines = [ln for ln in lines if ln and not ln.startswith("#")]
    assert help_lines, f"No '# HELP' comment in body — get_metrics_text() output missing registry metadata: {body!r}"
    assert type_lines, f"No '# TYPE' comment in body — get_metrics_text() output missing type annotations: {body!r}"
    assert metric_lines, f"No Prometheus metric lines in body: {body!r}"


def test_metrics_body_delegates_to_core_metrics(client: TestClient) -> None:
    """FastAPI ``/metrics`` returns the same bytes as ``get_metrics_text()``.

    The Flask ``core_bp.metrics`` handler at
    ``src/admin/routers/core.py`` also delegates to
    ``src.core.metrics.get_metrics_text()``. Both routes observe the
    same process-global ``prometheus_client`` REGISTRY, so a scraper
    hitting either endpoint should see byte-equivalent output.

    This guards the L0 "zero visible change" thesis: Prometheus
    scrapers that were wired to Flask's ``/metrics`` must still see
    the real metrics when the FastAPI route fields the request.
    """
    from src.core.metrics import get_metrics_text

    response = client.get("/metrics")
    assert response.text == get_metrics_text()


def test_metrics_body_includes_registered_counter(client: TestClient) -> None:
    """A well-known metric name from ``src.core.metrics`` appears in the body.

    ``ai_review_total`` is declared at import time in
    ``src/core/metrics.py``; its ``# HELP`` + ``# TYPE`` headers are
    rendered by ``get_metrics_text()`` even before any samples are
    recorded. Asserting on a real metric name proves the route is
    serializing the live registry, not a placeholder body.
    """
    # Import ensures the metric is registered in the process-global
    # REGISTRY even if no other code path has imported src.core.metrics.
    import src.core.metrics  # noqa: F401

    response = client.get("/metrics")
    body = response.text
    assert (
        "ai_review_total" in body
    ), f"Expected well-known metric 'ai_review_total' from src.core.metrics in /metrics body; got:\n{body[:500]}"
