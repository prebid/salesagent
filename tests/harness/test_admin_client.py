"""``AdminClient`` states a session in one place, and stating one replaces the one held."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tests.harness.admin_client import AdminClient
from tests.helpers.admin_session import member_session_payload


@pytest.mark.parametrize(
    "state",
    [
        lambda client: client.state_session(member_session_payload("member@example.com", "home")),
        lambda client: client.authenticate("tenant_a"),
    ],
    ids=["state_session", "authenticate"],
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
