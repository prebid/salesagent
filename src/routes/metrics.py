"""``/metrics`` endpoint scaffold — Prometheus text exposition format.

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
- Body: a single ``# HELP``+``# TYPE`` header and the ``adcp_up``
  gauge with value ``1``. Placeholder until L1+ adds real metrics.

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-19``.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.responses import Response

PROMETHEUS_CONTENT_TYPE: str = "text/plain; version=0.0.4; charset=utf-8"

_PLACEHOLDER_BODY: str = (
    "# HELP adcp_up Liveness gauge — 1 if the process is serving.\n" "# TYPE adcp_up gauge\n" "adcp_up 1\n"
)


router = APIRouter(tags=["observability"], include_in_schema=False)


@router.get("/metrics", name="metrics")
def metrics() -> Response:
    """Return a Prometheus text-format ``200`` with a liveness gauge.

    The handler is intentionally synchronous — there is no I/O at L0
    (the body is a module-level constant). L1+ expands the body but
    must stay synchronous while ``_PLACEHOLDER_BODY`` is the contract.
    """
    return Response(
        content=_PLACEHOLDER_BODY,
        media_type=PROMETHEUS_CONTENT_TYPE,
        status_code=200,
    )


__all__ = ["PROMETHEUS_CONTENT_TYPE", "router"]
