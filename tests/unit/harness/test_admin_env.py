"""Unit tests for ``IntegrationEnv.get_admin_client()`` (L0-22).

Scope: the harness extension that lets L1+ integration tests exercise the
empty ``build_admin_router()`` + ``SessionMiddleware`` scaffold via a
FastAPI ``TestClient``.

Contracts (derived from ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md``
§L0-22 and ``flask-to-fastapi-execution-details.md`` §Wave 0 A.11, A.12, C-risk
"``get_admin_client()`` TestClient triggers ``src.app`` middleware"):

1. ``get_admin_client()`` returns a functional ``TestClient``; constructing
   it does not error even though the admin router is empty at L0.
2. A GET to a non-existent admin route returns ``404`` — the empty router has
   no routes; this is the expected L0 behavior.
3. ``dependency_overrides`` set via ``env.override_dependency(...)`` context
   manager are visible inside the request handler and are restored on
   teardown (Agent B Risk #13 isolation).
4. A session cookie set via ``authenticated=True`` / ``session_payload=...``
   round-trips: two sequential requests see the same session state.
5. Pre-populated session payload appears in ``request.session`` inside a
   dependency-overridden handler — proves the SessionMiddleware is wired.

These tests are unit-level: the isolated ``FastAPI()`` inside
``get_admin_client()`` has no DB handlers at L0, so we never call
``_commit_factory_data()`` paths. The guarding use of ``IntegrationEnv``
(rather than ``BaseTestEnv``) is validated via the two short
``integration_db`` patches already proven by ``test_harness_base.py``.
"""

from unittest.mock import MagicMock, patch


def _mock_integration_deps():
    """Return patches that neutralize IntegrationEnv DB setup for unit tests.

    IntegrationEnv.__enter__ binds factory_boy sessions to the real engine;
    we short-circuit that so these unit tests can run without ``integration_db``.
    """
    return [
        patch("src.core.database.database_session.get_engine", return_value=MagicMock()),
        patch("tests.factories.ALL_FACTORIES", []),
    ]


class TestGetAdminClientReturnsTestClient:
    def test_returns_functional_test_client(self):
        """get_admin_client() builds a TestClient without error (empty router OK)."""
        from starlette.testclient import TestClient

        from tests.harness._base import IntegrationEnv

        env = IntegrationEnv(tenant_id="t1", principal_id="p1")
        patches = _mock_integration_deps()
        for p in patches:
            p.start()
        try:
            with env:
                client = env.get_admin_client()
                assert isinstance(client, TestClient)
        finally:
            for p in reversed(patches):
                p.stop()

    def test_nonexistent_admin_route_returns_404(self):
        """Empty router at L0: any admin path is a 404 (not 500)."""
        from tests.harness._base import IntegrationEnv

        env = IntegrationEnv(tenant_id="t1", principal_id="p1")
        patches = _mock_integration_deps()
        for p in patches:
            p.start()
        try:
            with env:
                client = env.get_admin_client()
                resp = client.get("/admin/nonexistent-route")
                assert resp.status_code == 404
        finally:
            for p in reversed(patches):
                p.stop()


class TestDependencyOverrideIsolation:
    def test_override_dependency_is_visible_in_handler(self):
        """override_dependency() context manager injects the override into the handler."""
        from fastapi import Depends

        from tests.harness._base import IntegrationEnv

        def _probe_dep() -> str:
            return "real"

        env = IntegrationEnv(tenant_id="t1", principal_id="p1")
        patches = _mock_integration_deps()
        for p in patches:
            p.start()
        try:
            with env:
                client = env.get_admin_client()
                # Register a probe route on the app so we can observe the override
                app = env.admin_app

                @app.get("/admin/__probe", name="admin_probe")
                def _probe(val: str = Depends(_probe_dep)) -> dict[str, str]:
                    return {"val": val}

                with env.override_dependency(_probe_dep, lambda: "overridden"):
                    resp = client.get("/admin/__probe")
                    assert resp.status_code == 200
                    assert resp.json() == {"val": "overridden"}

                # After the context manager exits, the override is gone
                resp_after = client.get("/admin/__probe")
                assert resp_after.json() == {"val": "real"}
        finally:
            for p in reversed(patches):
                p.stop()

    def test_dependency_overrides_cleared_on_env_teardown(self):
        """No dep-override leakage across sequential env contexts (Agent B Risk #13)."""
        from tests.harness._base import IntegrationEnv

        def _dep() -> str:
            return "A"

        patches = _mock_integration_deps()
        for p in patches:
            p.start()
        try:
            # First env: install an override via the canonical context manager
            env1 = IntegrationEnv(tenant_id="t1", principal_id="p1")
            with env1:
                env1.get_admin_client()
                with env1.override_dependency(_dep, lambda: "X"):
                    assert _dep in env1.admin_app.dependency_overrides

            # The app is per-env, so a second env gets a fresh app — but
            # the invariant we care about is: env1's teardown must have
            # emptied its own app.dependency_overrides.
            assert env1.admin_app.dependency_overrides == {}

            # Second env: no lingering overrides from env1
            env2 = IntegrationEnv(tenant_id="t1", principal_id="p1")
            with env2:
                env2.get_admin_client()
                assert _dep not in env2.admin_app.dependency_overrides
                # env1 and env2 have separate admin_app instances
                assert env2.admin_app is not env1.admin_app
        finally:
            for p in reversed(patches):
                p.stop()


class TestSessionCookieRoundtrip:
    def test_session_cookie_roundtrips_across_requests(self):
        """SessionMiddleware: setting session["key"] once is readable on the next request."""
        from fastapi import Request

        from tests.harness._base import IntegrationEnv

        patches = _mock_integration_deps()
        for p in patches:
            p.start()
        try:
            env = IntegrationEnv(tenant_id="t1", principal_id="p1")
            with env:
                client = env.get_admin_client()
                app = env.admin_app

                @app.get("/admin/__set-session", name="admin_set_session")
                def _set(request: Request) -> dict[str, str]:
                    request.session["marker"] = "hello"
                    return {"set": "ok"}

                @app.get("/admin/__read-session", name="admin_read_session")
                def _read(request: Request) -> dict[str, str | None]:
                    return {"marker": request.session.get("marker")}

                r1 = client.get("/admin/__set-session")
                assert r1.status_code == 200, r1.text
                r2 = client.get("/admin/__read-session")
                assert r2.status_code == 200, r2.text
                assert r2.json() == {"marker": "hello"}
        finally:
            for p in reversed(patches):
                p.stop()

    def test_session_payload_kwarg_pre_populates_session(self):
        """``session_payload=...`` seeds the session so the first request sees it."""
        from fastapi import Request

        from tests.harness._base import IntegrationEnv

        patches = _mock_integration_deps()
        for p in patches:
            p.start()
        try:
            env = IntegrationEnv(tenant_id="t1", principal_id="p1")
            with env:
                client = env.get_admin_client(session_payload={"user_email": "admin@t1"})
                app = env.admin_app

                @app.get("/admin/__who", name="admin_who")
                def _who(request: Request) -> dict[str, str | None]:
                    return {"email": request.session.get("user_email")}

                resp = client.get("/admin/__who")
                assert resp.status_code == 200
                assert resp.json() == {"email": "admin@t1"}
        finally:
            for p in reversed(patches):
                p.stop()

    def test_authenticated_kwarg_populates_minimal_admin_session(self):
        """``authenticated=True`` seeds a session with canonical admin keys."""
        from fastapi import Request

        from tests.harness._base import IntegrationEnv

        patches = _mock_integration_deps()
        for p in patches:
            p.start()
        try:
            env = IntegrationEnv(tenant_id="t1", principal_id="p1")
            with env:
                client = env.get_admin_client(authenticated=True)
                app = env.admin_app

                @app.get("/admin/__session-dump", name="admin_session_dump")
                def _dump(request: Request) -> dict:
                    return dict(request.session)

                resp = client.get("/admin/__session-dump")
                assert resp.status_code == 200
                payload = resp.json()
                # Minimal admin session contract: at least a user_email + tenant_id marker
                assert "user_email" in payload
                assert payload.get("tenant_id") == "t1"
        finally:
            for p in reversed(patches):
                p.stop()
