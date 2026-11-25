"""A session is stated through one writer per transport, and stating one replaces the one held."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.harness.admin_client import AdminClient
from tests.helpers.admin_session import member_session_payload, tenant_user_session

_MEMBER = member_session_payload("member@example.com", "home")


@pytest.mark.parametrize(
    "state",
    [
        lambda client: client.state_session(_MEMBER),
        lambda client: client.authenticate("tenant_a"),
        lambda client: client.authenticate("tenant_a", tenant_user="member@example.com"),
    ],
    ids=["state_session", "authenticate", "authenticate_member"],
)
def test_stating_a_session_over_http_replaces_the_cookie_the_server_wrote(state: Callable[[AdminClient], None]) -> None:
    """A server-written ``session`` cookie carries a domain, so a domainless one would land beside it."""
    client = AdminClient("http://stack.test:8080")
    try:
        client._http.cookies.set("session", "written-by-the-server", domain="stack.test", path="/")

        state(client)

        assert [(cookie.name, cookie.domain) for cookie in client._http.cookies] == [("session", "")]
    finally:
        client.close()


@pytest.mark.parametrize(
    ("state", "stated"),
    [
        (lambda client: client.state_session(_MEMBER), _MEMBER),
        (
            lambda client: tenant_user_session(client._flask_client, "member@example.com"),
            member_session_payload("member@example.com", auth_method=None),
        ),
        (
            lambda client: client.authenticate("home", tenant_user="member@example.com"),
            member_session_payload("member@example.com", "home", auth_method=None),
        ),
        (
            lambda client: client.authenticate("home", tenant_user="member@example.com", auth_method="oidc"),
            _MEMBER,
        ),
    ],
    ids=["state_session", "tenant_user_session", "authenticate_member", "authenticate_oidc_member"],
)
def test_stating_a_session_in_process_replaces_the_session_the_client_held(
    state: Callable[[AdminClient], None], stated: dict[str, Any]
) -> None:
    """A member stated over a super-admin session keeps none of its keys, ``is_super_admin`` least of all."""
    client = AdminClient(None)
    try:
        client.authenticate("tenant_a")

        state(client)

        with client._flask_client.session_transaction() as session:
            assert dict(session) == stated
    finally:
        client.close()
