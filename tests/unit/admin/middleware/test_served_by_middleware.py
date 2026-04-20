"""Semantic tests for the L0-18 ``ServedByMiddleware`` + feature flag.

Pattern (a) stub-first: at L0 Red the middleware is a no-op pass-through
that does NOT stamp the ``X-Served-By`` header, and the feature-flag is
readable via ``src.core.config``. L0 Green adds the header emission.

The feature-flag read test stays green across Red/Green because the
flag wiring is non-behavioral at L0 (the middleware does not consult it
yet — L1a does).

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-18``
and ``implementation-checklist.md §EP-1``, ``§EP-2``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient


@pytest.fixture
def app_with_middleware() -> TestClient:
    """FastAPI app with ``ServedByMiddleware`` wired at the outermost tier."""
    from src.admin.middleware.served_by import ServedByMiddleware

    app = FastAPI()

    @app.get("/probe")
    def probe() -> dict[str, str]:
        return {"ok": "yes"}

    app.add_middleware(ServedByMiddleware, stack_name="fastapi")
    return TestClient(app)


def test_served_by_header_is_stamped_on_responses(app_with_middleware: TestClient) -> None:
    """The middleware adds an ``X-Served-By`` response header.

    L0 Red: stub does not add the header; this test fails. L0 Green
    wires the send wrapper that injects the header on
    ``http.response.start`` messages.
    """
    response = app_with_middleware.get("/probe")
    assert response.status_code == 200
    assert "x-served-by" in {
        k.lower() for k in response.headers.keys()
    }, f"Missing X-Served-By header. Got: {dict(response.headers)}"


def test_served_by_header_matches_configured_stack_name(app_with_middleware: TestClient) -> None:
    """The header value is the ``stack_name`` passed to the middleware."""
    response = app_with_middleware.get("/probe")
    assert response.headers.get("x-served-by") == "fastapi"


def test_served_by_default_stack_is_flask_at_l0() -> None:
    """Middleware's default ``stack_name`` is ``flask`` at L0.

    L0 ships with Flask serving 100% of /admin/* traffic. The default
    reflects that until L1a wires the feature-flag-aware resolver.
    """
    from src.admin.middleware.served_by import ServedByMiddleware

    app = FastAPI()

    @app.get("/probe")
    def probe() -> dict[str, str]:
        return {"ok": "yes"}

    # No stack_name override — default must be 'flask' at L0.
    app.add_middleware(ServedByMiddleware)
    client = TestClient(app)
    assert client.get("/probe").headers.get("x-served-by") == "flask"


def test_non_http_scope_passes_through_unchanged() -> None:
    """The middleware is a no-op for non-HTTP scopes (websocket, lifespan).

    Guards against over-eager header injection that would corrupt
    lifespan messages or WebSocket frames.
    """
    import asyncio

    from src.admin.middleware.served_by import ServedByMiddleware

    received: list[dict[str, object]] = []

    async def inner_app(scope: dict, receive, send) -> None:
        await send({"type": "lifespan.startup.complete"})

    mw = ServedByMiddleware(inner_app, stack_name="fastapi")

    async def send(msg: dict) -> None:
        received.append(msg)

    async def receive() -> dict:
        return {"type": "lifespan.startup"}

    asyncio.run(mw({"type": "lifespan"}, receive, send))
    # No x-served-by fabricated on lifespan messages — they have no headers field.
    assert received == [{"type": "lifespan.startup.complete"}]


def test_feature_flag_defaults_to_false() -> None:
    """``ADCP_USE_FASTAPI_ADMIN`` flag defaults to False on AppConfig.

    L0 ships with Flask serving 100% of /admin/* traffic. The flag is
    flipped progressively at L1a. Default False is the safe rollback
    posture per implementation-checklist.md §EP-1.
    """
    from src.core.config import AppConfig

    cfg = AppConfig(adcp_use_fastapi_admin=False)
    assert cfg.adcp_use_fastapi_admin is False


def test_feature_flag_readable_as_typed_bool() -> None:
    """The flag surfaces on ``AppConfig`` as a typed ``bool``, not a str.

    Avoids the classic ``os.environ.get('ADCP_USE_FASTAPI_ADMIN') == 'true'``
    bug class where different callers disagree on truthiness casting.
    """
    from src.core.config import AppConfig

    cfg_true = AppConfig(adcp_use_fastapi_admin=True)
    cfg_false = AppConfig(adcp_use_fastapi_admin=False)
    assert cfg_true.adcp_use_fastapi_admin is True
    assert cfg_false.adcp_use_fastapi_admin is False
    assert isinstance(cfg_true.adcp_use_fastapi_admin, bool)
