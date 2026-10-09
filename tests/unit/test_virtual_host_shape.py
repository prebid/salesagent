"""``validate_virtual_host`` is the ONE definition of the shape a tenant's host may take.

A pure function, so a unit test is the right level: input in, folded host or a ValueError
out, no database and no transport.

Why it is graded at all. Three entry points delegate here -- the ORM validator, the admin
settings form and the tenant management API -- and the point of one definition is that they
cannot disagree, which only holds if the definition itself is pinned.

EVERY refusal message is authored here. The management API answers 400 with ``str(exc)``,
so a ValueError escaping from ``urlsplit`` would put urllib's own text in a response body;
``test_every_refusal_message_is_authored`` is what keeps that from coming back.
"""

from __future__ import annotations

import pytest

from src.core.http_utils import validate_virtual_host

#: Accepted, mapped to what the column stores. A port is part of the value because the card
#: publishes this string verbatim and a client dials what the card says.
ACCEPTED = {
    "seller.example.com": "seller.example.com",
    "seller.example.com:8443": "seller.example.com:8443",
    "ad-sales2.seller-one.example.com": "ad-sales2.seller-one.example.com",
    "xn--bcher-kva.example": "xn--bcher-kva.example",
    "HOST.Example.COM": "host.example.com",
    "  host.example.com  ": "host.example.com",
    "[2001:db8::1]": "[2001:db8::1]",
    "[2001:db8::1]:8443": "[2001:db8::1]:8443",
    "localhost": "localhost",
    "proxy:8000": "proxy:8000",
}

#: Refused because the admin plane cannot serve them: Werkzeug answers a ``Host`` like these
#: with an empty ``request.host``. Graded against Werkzeug itself below.
UNSERVABLE = [
    ("seller_one.example.com", "underscore in the name"),
    ("bücher.example", "non-ASCII name"),
    ("host!.example.com", "punctuation in the name"),
    ("host.example.com:0", "port zero"),
    ("host.example.com:08443", "port with a leading zero"),
]

#: Refused, each with the reason it is not a host a request can name.
REFUSED = UNSERVABLE + [
    ("", "blank"),
    ("   ", "whitespace only"),
    ("https://evil.com", "carries a scheme"),
    ("http://host.example.com", "carries a scheme"),
    ("evil.com/path", "carries a path"),
    ("host.example.com/", "carries a path"),
    ("a b.com", "contains a space, so no Host header can name it"),
    ("user@evil.com", "carries userinfo"),
    ("host.example.com:notaport", "non-numeric port"),
    ("host.example.com:", "trailing colon naming no port"),
    ("[::1]:", "trailing colon naming no port"),
    ("[::1", "unterminated IPv6 bracket"),
    ("host.example.com?q=1", "carries a query"),
    ("host.example.com#f", "carries a fragment"),
]


@pytest.mark.parametrize(("submitted", "stored"), sorted(ACCEPTED.items()))
def test_an_accepted_host_is_folded_to_what_the_column_stores(submitted: str, stored: str) -> None:
    assert validate_virtual_host(submitted) == stored


@pytest.mark.parametrize(("submitted", "why"), REFUSED, ids=[why for _, why in REFUSED])
def test_a_host_a_request_cannot_name_is_refused(submitted: str, why: str) -> None:
    with pytest.raises(ValueError):
        validate_virtual_host(submitted)


def test_none_is_refused_like_a_blank() -> None:
    """The column is NOT NULL, so a reader never sees one -- the validator refuses it too."""
    with pytest.raises(ValueError):
        validate_virtual_host(None)


@pytest.mark.parametrize(("submitted", "why"), REFUSED, ids=[why for _, why in REFUSED])
def test_every_refusal_message_is_authored(submitted: str, why: str) -> None:
    """No refusal carries a message this repo did not write.

    ``tenant_management_api.create_tenant`` answers 400 with ``str(exc)``, so the message is
    wire-visible. ``urlsplit`` raises its own ValueError for an unterminated IPv6 bracket
    ("Invalid IPv6 URL"); before it was caught, that string reached the response body, which
    is the exposure CodeQL flags on that line. Naming ``virtual_host`` is the cheap check
    that the message came from here.
    """
    with pytest.raises(ValueError) as caught:
        validate_virtual_host(submitted)
    assert "virtual_host" in str(caught.value), (
        f"the refusal for {submitted!r} carries a message this repo did not author: "
        f"{str(caught.value)!r}. The management API puts it in a 400 body."
    )


def test_the_admin_plane_serves_every_accepted_host_and_no_unservable_one() -> None:
    """The character and port rule is Werkzeug's, so Werkzeug grades it.

    Werkzeug (3.1.7 and later) answers a ``Host`` whose name is not letters, digits, ``-`` and
    ``.`` or a bracketed IPv6 literal, or whose port is outside 1-65535, with an empty
    ``request.host``. Every ``redirect(request.url)`` and ``url_for(..., _external=True)`` the
    admin UI builds then names no host, OAuth callbacks included. So an accepted host must come
    back from Werkzeug intact, and each name refused as unservable must be one it refuses too;
    a Werkzeug upgrade that moves the rule fails here rather than in a tenant's admin pages.
    """
    from flask import Flask, request

    app = Flask(__name__)

    def host_seen_by_the_admin_plane(host: str) -> str:
        with app.test_request_context("/", headers={"Host": host}):
            return request.host

    for stored in ACCEPTED.values():
        assert host_seen_by_the_admin_plane(stored) == stored, f"the admin plane cannot serve accepted {stored!r}"
    for submitted, why in UNSERVABLE:
        assert host_seen_by_the_admin_plane(submitted) == "", (
            f"{submitted!r} ({why}) is servable, so refusing it is wrong"
        )


def test_folding_is_idempotent() -> None:
    """What the column stores validates to itself, so a re-save cannot drift."""
    for stored in ACCEPTED.values():
        assert validate_virtual_host(stored) == stored


def test_the_stored_name_is_the_stdlib_hostname_of_the_stored_origin() -> None:
    """``Tenant.virtual_host_name`` is derived, and ``hostname_of`` is what derives it.

    The unique key routing resolves by is that column, so it has to hold the host's name and
    nothing else. Deriving it in the index instead would mean splitting the origin in SQL,
    which is wrong for a bracketed IPv6 literal -- graded end to end by
    ``tests/integration/test_tenant_lookup_repository.py::...::test_two_ipv6_tenants_do_not_collide``.

    No database here: the validator is a plain method, so assigning the column on an
    unattached instance exercises the derivation on its own.
    """
    from src.core.database.models import Tenant
    from src.core.http_utils import hostname_of

    for origin in ("host.example.com", "host.example.com:8443", "[2001:db8::1]:8443", "HOST.Example.COM"):
        tenant = Tenant(tenant_id="t", virtual_host=origin)
        assert tenant.virtual_host_name == hostname_of(tenant.virtual_host), (
            f"{origin!r} stored name {tenant.virtual_host_name!r}, but its host is {hostname_of(tenant.virtual_host)!r}"
        )

    # The case the SQL form gets wrong: the bracketed literal keeps its address.
    assert Tenant(tenant_id="t", virtual_host="[2001:db8::1]:8443").virtual_host_name == "2001:db8::1"
