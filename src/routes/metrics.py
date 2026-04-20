"""``/metrics`` endpoint — Prometheus text exposition format.

Leaf route landed at L0 per the permitted-modifications carve-out in
``L0-implementation-plan-v2.md §7.2 RATIFIED``. The endpoint exists so
the L1 bake-window dashboards have a target to scrape before real
metrics land at L1+ (L6 adds ``logfire`` instrumentation per the
post-L2 items in ``CLAUDE.md``).

Wiring: ``src/app.py`` calls ``app.include_router(metrics_router)``
alongside the existing health/api_v1 routes. This is a leaf route,
NOT an admin router include — the latter is explicitly forbidden at
L0 per the "do not modify src/app.py" sentence in execution-plan.md:145.

Response shape:

- Status 200.
- Content-Type ``text/plain; version=0.0.4`` — Prometheus text
  exposition format version token. Scrapers parse this to pick the
  grammar.
- Body: the live Prometheus registry rendered via
  ``src.core.metrics.get_metrics_text()`` — same source Flask's
  ``core_bp.metrics`` (``src/admin/routers/core.py``) delegates to, so
  the FastAPI route returns byte-equivalent output to the existing
  Flask ``/metrics`` endpoint. This preserves L0's zero-visible-change
  thesis for Prometheus scrapers.

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-19``.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.responses import Response

from src.core.metrics import get_metrics_text

PROMETHEUS_CONTENT_TYPE: str = "text/plain; version=0.0.4; charset=utf-8"


router = APIRouter(tags=["observability"], include_in_schema=False)


@router.get("/metrics", name="metrics")
def metrics() -> Response:
    """Return a Prometheus text-format ``200`` from the live registry.

    Delegates to ``src.core.metrics.get_metrics_text()`` so FastAPI's
    ``/metrics`` returns the same bytes as Flask's ``core_bp.metrics``
    — the REGISTRY is a process-global ``prometheus_client`` default,
    so both transports observe the same counters, histograms, and
    gauges.

    The handler stays synchronous at L0: ``get_metrics_text()`` runs
    ``prometheus_client.generate_latest`` which is CPU-bound
    serialization over an in-memory registry (no I/O).
    """
    return Response(
        content=get_metrics_text(),
        media_type=PROMETHEUS_CONTENT_TYPE,
        status_code=200,
    )


__all__ = ["PROMETHEUS_CONTENT_TYPE", "router"]
