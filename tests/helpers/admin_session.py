"""Admin test-session helpers — the ONE module that states who an admin caller is.

Two clients need a session and neither needs a login route. Each has one writer, and both
writers REPLACE the session the client held:

* a Flask test client has its session dict written directly (:func:`state_flask_session`);
* an HTTP client against a running stack carries a session COOKIE, signed the way the
  server signs its own (:func:`state_http_session`).

What is stated is a payload: a super admin's (:func:`admin_session_payload`) or an ordinary
member's (:func:`member_session_payload`). :func:`admin_auth_session` and
:func:`authenticate_http_session` state the super admin's on their transport.
:func:`no_configured_super_admins` is the other half of stating a member: it keeps the
deployment's own configuration from promoting that member.

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
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest import mock

if TYPE_CHECKING:
    import requests
    from flask import Flask
    from flask.testing import FlaskClient

#: The secret the test stacks sign session cookies with. A deployment generates a random
#: one per process (``RuntimeSettings.flask_secret_key``), which is right for a deployment
#: and useless to a test on the other side of HTTP -- so the test stacks PIN it, in their
#: compose files, and this reads the same value. Not a credential: it signs sessions for a
#: throwaway stack, and a real deployment that sets it is choosing its own.
TEST_FLASK_SECRET_KEY = "adcp-test-stack-session-signing-key"


def admin_session_payload(tenant_id: str, *, auth_method: str | None = None) -> dict[str, Any]:
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


def member_session_payload(email: str, tenant_id: str) -> dict[str, Any]:
    """The session an OIDC login writes for a caller who is NOT a super admin.

    The keys ``callback`` writes under "Create session" in ``src/admin/blueprints/oidc.py``,
    with ``user`` a bare email as it is there. No ``test_user`` key, so
    ``require_tenant_access`` takes its real branch: the caller's ``User`` row in the
    requested tenant is the only thing that decides the outcome.
    """
    return {
        "user": email,
        "user_name": email,
        "tenant_id": tenant_id,
        "authenticated": True,
        "auth_method": "oidc",
    }


def admin_session_cookie(tenant_id: str, *, auth_method: str | None = None, secret_key: str | None = None) -> str:
    """A signed Flask session cookie for an authenticated super admin.

    Signed with Flask's own ``SecureCookieSessionInterface``, so the format follows Flask
    rather than a hand-rolled copy of it: if Flask changes how it serializes a session,
    this changes with it and the tests keep working.

    *secret_key* defaults to ``FLASK_SECRET_KEY`` from the environment, then to
    :data:`TEST_FLASK_SECRET_KEY` — the value the test compose stacks set.
    """
    return sign_session(admin_session_payload(tenant_id, auth_method=auth_method), secret_key=secret_key)


def sign_session(payload: dict[str, Any], *, secret_key: str | None = None) -> str:
    """*payload* as a Flask session cookie, signed the way the test stacks' server signs its own."""
    from flask import Flask
    from flask.sessions import SecureCookieSessionInterface

    app = Flask(__name__)
    app.secret_key = secret_key or os.environ.get("FLASK_SECRET_KEY") or TEST_FLASK_SECRET_KEY
    serializer = SecureCookieSessionInterface().get_signing_serializer(app)
    assert serializer is not None, "Flask refused to build a session serializer without a secret key"
    return serializer.dumps(payload)


def authenticate_http_session(
    session: requests.Session, base_url: str, tenant_id: str, *, auth_method: str | None = None
) -> requests.Session:
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
    state_http_session(session, admin_session_payload(tenant_id, auth_method=auth_method))
    return session


def state_http_session(session: requests.Session, payload: dict[str, Any]) -> None:
    """State *payload* as a ``requests.Session``'s admin session, replacing the one it held.

    Every ``session`` cookie goes first, the one the server wrote under its own domain
    included: left in the jar, it would sit beside the stated one and the request would
    carry two sessions.
    """
    _drop_session_cookies(session)
    session.cookies.set("session", sign_session(payload), path="/")


def _drop_session_cookies(session: requests.Session, *, stated_only: bool = False) -> None:
    """Remove the jar's ``session`` cookies: every one, or only the domainless stated one."""
    for cookie in list(session.cookies):
        if cookie.name == "session" and not (stated_only and cookie.domain):
            session.cookies.clear(cookie.domain, cookie.path, cookie.name)


def drop_stated_session_cookie(session: requests.Session) -> None:
    """Remove the stated cookie once the SERVER has written a session of its own.

    Call this between a form POST and the GET that renders its flash.

    A flash lives ONLY in the session the server writes: it deserializes the stated cookie,
    adds ``_flashes``, and writes the whole thing back in a ``Set-Cookie`` carrying a domain
    (Flask sets no ``Domain``, so the jar records the request host). The stated cookie is
    domainless -- it has to be, see :func:`authenticate_http_session` -- so without this the
    jar holds two cookies named ``session`` and sends the stated one, which has no
    ``_flashes``: the page renders with no message.

    Dropping the stated cookie rather than matching its domain to the server's is
    deliberate: a cookie whose domain is set is NOT sent to a DOTLESS host, and the
    in-network stacks are reached at ``proxy`` and ``localhost``. Domainless is the only
    form that reaches every host, so the duplicate is what gets fixed.

    The server's cookie carries everything the stated one did, so the session survives the
    drop.
    """
    _drop_session_cookies(session, stated_only=True)


def admin_test_app() -> Flask:
    """The admin Flask app a test drives in process: ONE composition, every harness.

    Composed from the settings object the process already holds (``get_settings()``), so a
    deployment fact a Given patched onto that object -- production, say -- is the one the
    app is composed and served under. Left to itself ``create_app`` calls ``load_settings()``,
    which builds a fresh object from the environment and replaces the current one, dropping
    any such patch.

    ``TESTING`` makes Flask propagate a view's exception instead of rendering a 500, so a
    harness sees the failure where it happened. ``WTF_CSRF_ENABLED`` is off so a form post
    needs no token.
    """
    from src.admin.app import create_app
    from src.core.config import get_settings

    app = create_app(settings=get_settings())
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


def state_flask_session(client: FlaskClient, payload: dict[str, Any]) -> None:
    """State *payload* as a Flask test client's session, replacing the one it held.

    The in-process twin of :func:`state_http_session`. Cleared first: a key left over from
    the session before (``test_user``, ``is_super_admin``) would make the caller someone the
    payload does not say it is.
    """
    with client.session_transaction() as session:
        session.clear()
        session.update(payload)


def admin_auth_session(client: FlaskClient, tenant_id: str, *, auth_method: str | None = None) -> None:
    """State a super-admin session on a Flask test client.

    Pass ``auth_method='oidc'`` to exercise routes that gate on SSO login
    (e.g. ``disable-setup-mode``).
    """
    state_flask_session(client, admin_session_payload(tenant_id, auth_method=auth_method))


@contextmanager
def no_configured_super_admins() -> Iterator[None]:
    """Empty the deployment's two super-admin lists for the block, so a stated member stays a member.

    ``is_super_admin`` waves through an address or a domain on either list, and a test's
    member lives at ``example.com``. Both are emptied on the live settings object, which the
    check reads on each request. The environment is loaded once, so removing the variables
    instead would only reach a process that had not built its settings yet.

    This governs the app in THIS process. A live stack's server reads its own settings.
    """
    from src.core.config import get_settings

    with mock.patch.multiple(get_settings().auth, super_admin_emails="", super_admin_domains=""):
        yield
