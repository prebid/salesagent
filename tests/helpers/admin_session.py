"""Admin test-session helpers — the ONE way a test becomes an authenticated admin.

Two clients need a session and neither needs a login route:

* a Flask test client writes the session dict directly (:func:`admin_auth_session`);
* an HTTP client against a running stack carries a session COOKIE, signed the way the
  server signs its own (:func:`admin_session_cookie`).

Both replaced ``POST /test/auth`` — a composed-in login route that accepted a default
password and minted exactly this session. That route made the app under test a different
app from the deployed one (it was composed only under ``ADCP_AUTH_TEST_MODE``), which is
CLAUDE.md pattern 11 one frame up, at composition; and it made a suite's verdict depend on
whether the flag happened to be set, which is how two template tests came to pass on the CI
box and fail on a laptop. A test that needs an admin session now STATES one, and the
deployment has one fewer password path.

Nine older blueprint test modules still carry their own private ``_auth_session`` copy
(pre-existing duplication debt); new modules must import from here.
"""

from __future__ import annotations

import os
from typing import Any

#: The secret the test stacks sign session cookies with. A deployment generates a random
#: one per process (``RuntimeSettings.flask_secret_key``), which is right for a deployment
#: and useless to a test on the other side of HTTP -- so the test stacks PIN it, in their
#: compose files, and this reads the same value. Not a credential: it signs sessions for a
#: throwaway stack, and a real deployment that sets it is choosing its own.
TEST_FLASK_SECRET_KEY = "adcp-test-stack-session-signing-key"


def _session_payload(tenant_id: str, *, auth_method: str | None = None) -> dict[str, Any]:
    """The session an authenticated super admin carries. ONE definition, both transports."""
    payload: dict[str, Any] = {
        "authenticated": True,
        "user": {"email": "test@example.com", "is_super_admin": True},
        "email": "test@example.com",
        "tenant_id": tenant_id,
        "test_user": "test@example.com",
        "test_user_role": "super_admin",
        "test_user_name": "Test User",
        "test_tenant_id": tenant_id,
        "is_super_admin": True,
        "role": "super_admin",
        # is_super_admin() short-circuits on this pair, so recognising the caller costs no
        # database round trip — which is what lets a unit test use the real decorator.
        "admin_email": "test@example.com",
    }
    if auth_method is not None:
        payload["auth_method"] = auth_method
    return payload


def admin_session_cookie(tenant_id: str, *, auth_method: str | None = None, secret_key: str | None = None) -> str:
    """A signed Flask session cookie for an authenticated super admin.

    Signed with Flask's own ``SecureCookieSessionInterface``, so the format follows Flask
    rather than a hand-rolled copy of it: if Flask changes how it serializes a session,
    this changes with it and the tests keep working.

    *secret_key* defaults to ``FLASK_SECRET_KEY`` from the environment, then to
    :data:`TEST_FLASK_SECRET_KEY` — the value the test compose stacks set.
    """
    from flask import Flask
    from flask.sessions import SecureCookieSessionInterface

    app = Flask(__name__)
    app.secret_key = secret_key or os.environ.get("FLASK_SECRET_KEY") or TEST_FLASK_SECRET_KEY
    serializer = SecureCookieSessionInterface().get_signing_serializer(app)
    assert serializer is not None, "Flask refused to build a session serializer without a secret key"
    return serializer.dumps(_session_payload(tenant_id, auth_method=auth_method))


def authenticate_http_session(session: Any, base_url: str, tenant_id: str, *, auth_method: str | None = None) -> Any:
    """Give a ``requests.Session`` an authenticated admin cookie, for any host.

    The HTTP-side twin of :func:`admin_auth_session`. Returns the session, so a caller can
    build and authenticate in one expression.

    NO DOMAIN IS SET, deliberately. ``http.cookiejar`` refuses to return a cookie whose
    domain was SPECIFIED and contains no dot, so pinning it to ``localhost`` -- or to a
    compose service name like ``proxy`` -- produces a cookie that is stored and then never
    sent, and the request arrives anonymous. Measured: with an explicit domain the admin
    page answered 302 and ``Cookie`` was absent from the request; without one it answered
    200. Leaving the domain unset lets the jar attach it to whatever host is asked, which
    is what every caller wants and the only form that works for a dotless host.

    *base_url* is still taken, because a caller naming the session's target reads better
    than one that does not, and because a future scheme- or host-specific rule belongs here.
    """
    session.cookies.set("session", admin_session_cookie(tenant_id, auth_method=auth_method), path="/")
    return session


def admin_auth_session(client: Any, tenant_id: str, *, auth_method: str | None = None) -> None:
    """Populate a super-admin test-mode session on a Flask test client.

    Pass ``auth_method='oidc'`` to exercise routes that gate on SSO login
    (e.g. ``disable-setup-mode``).
    """
    with client.session_transaction() as sess:
        sess.update(_session_payload(tenant_id, auth_method=auth_method))
