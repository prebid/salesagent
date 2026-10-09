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

import string
from functools import cache

import pytest
from flask import Flask, request, url_for
from werkzeug.exceptions import BadHost

from src.core.http_utils import _admin_plane_can_serve, validate_virtual_host

#: Accepted, mapped to what the column stores. A port is part of the value because the card
#: publishes this string verbatim and a client dials what the card says.
ACCEPTED = {
    "seller.example.com": "seller.example.com",
    "seller.example.com:8443": "seller.example.com:8443",
    "ad-sales2.seller-one.example.com": "ad-sales2.seller-one.example.com",
    "xn--bcher-kva.example": "xn--bcher-kva.example",
    "xn--n3h.com": "xn--n3h.com",
    "xn--fsq.com": "xn--fsq.com",
    "127.0.0.1:8080": "127.0.0.1:8080",
    "HOST.Example.COM": "host.example.com",
    "  host.example.com  ": "host.example.com",
    "\texample.com\r": "example.com",
    "[2001:db8::1]": "[2001:db8::1]",
    "[2001:db8::1]:8443": "[2001:db8::1]:8443",
    "localhost": "localhost",
    "proxy:8000": "proxy:8000",
}

#: Refused because the admin plane cannot build URLs for them: Werkzeug answers some with an
#: empty ``request.host``, and URL building fails for the rest. Graded against Flask below.
UNSERVABLE = [
    ("seller_one.example.com", "underscore in the name"),
    ("bücher.example", "non-ASCII name"),
    ("host!.example.com", "punctuation in the name"),
    ("a~b.example", "tilde in the name"),
    ("host.example.com:0", "port zero"),
    ("host.example.com:08443", "port with a leading zero"),
    ("a..b.example.com", "empty label"),
    (".starts-with-dot.example.com", "leading dot"),
    ("x" * 64 + ".example.com", "label over 63 characters"),
    ("xn--zz.example", "xn-- label that is not punycode"),
    ("[v1.fe]", "IPvFuture literal"),
]

#: Refused, each with the reason it is not a host a request can name.
REFUSED = UNSERVABLE + [
    ("", "blank"),
    ("   ", "whitespace only"),
    ("https://evil.com", "carries a scheme"),
    ("http://host.example.com", "carries a scheme"),
    ("ftp://files.example.com", "carries a scheme"),
    ("evil.com/path", "carries a path"),
    ("host.example.com/", "carries a path"),
    ("a b.com", "contains a space, so no Host header can name it"),
    ("user@evil.com", "carries userinfo"),
    ("host.example.com:notaport", "non-numeric port"),
    ("host.example.com:65536", "port above 65535"),
    ("::1", "unbracketed IPv6 literal"),
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


@pytest.mark.parametrize(("submitted", "why"), UNSERVABLE, ids=[why for _, why in UNSERVABLE])
def test_an_unservable_host_is_refused_by_the_admin_plane_rule(submitted: str, why: str) -> None:
    """Pinned to the rule that refuses it, so an earlier check cannot take the case over unnoticed."""
    with pytest.raises(ValueError, match="is not a host the admin UI can serve"):
        validate_virtual_host(submitted)


@cache
def _admin_app() -> Flask:
    app = Flask(__name__)
    app.add_url_rule("/callback", "callback", lambda: "")
    return app


def _admin_plane_builds_urls_for(host: str) -> bool:
    """Whether Flask, sent ``Host: host``, routes the request and builds absolute URLs for it.

    That is what the admin UI needs from a tenant's host: ``redirect(request.url)`` and
    ``url_for(..., _external=True)``. Werkzeug answers three ways a host can fail: an empty
    ``request.host`` (outside its grammar), a BadHost routing error (an empty or over-long
    label), or a UnicodeError when the URL is built (an ``xn--`` label that is not punycode).
    """
    with _admin_app().test_request_context("/", headers={"Host": host}):
        if not request.host or isinstance(request.routing_exception, BadHost):
            return False
        try:
            request.url  # noqa: B018 -- building it is the check
            url_for("callback", _external=True)
        except UnicodeError:
            return False
        return True


_PORTS = ["1", "80", "8443", "65535", "65536", "0", "00", "01", "08443"]
#: Generated, so the rule is compared with Flask well beyond the tables above: every printable
#: ASCII character inside a label, label boundaries, punycode, IPv6 literals and port forms.
#: Non-ASCII stays within latin-1, the only names a WSGI ``Host`` header can carry raw.
CORPUS = sorted(
    {f"a{ch}b.example" for ch in string.printable if not ch.isspace()}
    | {"aüb.example", "aßb.example", "café.example"}
    | {"host.", "host..", ".", "..", "a..b.example", ".lead.example", "-lead.example", "trail-.example"}
    | {"x" * 63 + ".example", "x" * 64 + ".example", ".".join(["a" * 63] * 4) + ".example"}
    | {"xn--bcher-kva.example", "xn--n3h.com", "xn--zz.example", "xn--a.example", "xn--.example"}
    | {"a.xn--zz.example", "a.b.xn--a", "example." + "x" * 64, "a." + "x" * 64 + ".example"}
    | {"[::1]", "[2001:db8::1]", "[::ffff:1.2.3.4]", "[v1.fe]", "[2001:db8::g]", "[fe80::1%25eth0]"}
    | {f"host.example.com:{port}" for port in _PORTS}
    | {f"[::1]:{port}" for port in _PORTS}
    | set(ACCEPTED.values())
    | {submitted for submitted, _ in UNSERVABLE}
)


@pytest.mark.parametrize("candidate", CORPUS)
def test_the_admin_plane_rule_matches_flask(candidate: str) -> None:
    """``_admin_plane_can_serve`` says yes exactly when Flask can serve the host.

    Checked against Flask itself, not a copy of Werkzeug's grammar, so a Werkzeug or Flask
    upgrade that moves what the admin plane can serve fails here instead of in a tenant's
    admin pages.
    """
    host = candidate.lower()
    assert _admin_plane_can_serve(host) == _admin_plane_builds_urls_for(host)


@pytest.mark.parametrize("candidate", CORPUS)
def test_every_accepted_host_is_one_the_admin_plane_can_serve(candidate: str) -> None:
    """One direction only: the validator may refuse a servable host for a reason of its own."""
    try:
        stored = validate_virtual_host(candidate)
    except ValueError:
        return
    assert _admin_plane_builds_urls_for(stored), (
        f"{candidate!r} is accepted as {stored!r}, but the admin plane cannot build URLs for it"
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
