"""The host, header and path facts of an HTTP request, read in one place.

The host a request names is its ``Host``. ``requested_host`` is that, and it is the only
host input for deciding which tenant a request is for — a caller asks for the FACT rather
than naming a header, which is what keeps one answer to "which host is this request for"
across the boundary resolver, the admin blueprints, the routes and the routing module.

Whatever a proxy in front of this app does to produce that ``Host`` is the edge's business
and has no spelling here.

The path a request names is its ASGI ``path`` with any mount prefix stripped.
``path_from_asgi_scope`` is that, for the same reason: every routing predicate that must
agree with the dispatcher asks for the fact instead of restating the rule.

A signature base reads neither of these facts: ``@target-uri`` has to cover the bytes the
client dialled, so ``src.core.signing.capture`` reads the wire itself — the host line and
the unstripped, still-encoded path — and deliberately does not come here.

This module holds no state and imports nothing from the application, so every one of those
callers can import it.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

#: The ``Host`` the admin plane can serve, in Werkzeug's own grammar (3.1.7 and later): a name
#: of letters, digits, ``-`` and ``.``, or a bracketed IPv6 literal, then an optional port with
#: no leading zero. Werkzeug answers any other ``Host`` with an empty ``request.host``.
#: ``tests/unit/test_virtual_host_shape.py`` grades this against Werkzeug itself.
_SERVABLE_HOST = re.compile(r"(?:[a-z0-9.-]+|\[[a-f0-9]*:[a-f0-9.:]+\])(?::[1-9][0-9]{0,4})?")


class HeaderSource(Protocol):
    """Anything that can list its headers.

    A plain ``dict``, Flask/werkzeug's ``Headers`` and Starlette's ``Headers`` are all
    passed to these functions, and only the first is a ``Mapping`` — werkzeug's is a
    multi-dict that does not register as one. Listing the pairs is all any function here
    needs, so that is what the parameter asks for.
    """

    def items(self) -> Iterable[tuple[str, Any]]: ...


def get_header_case_insensitive(headers: HeaderSource, header_name: str) -> str | None:
    """Get a header value with case-insensitive lookup.

    HTTP headers are case-insensitive per RFC 7230, but Python dicts are
    case-sensitive. This helper performs case-insensitive header lookup.

    Args:
        headers: Dictionary of headers
        header_name: Header name to look up (compared case-insensitively)

    Returns:
        Header value if found, None otherwise
    """
    if not headers:
        return None

    header_name_lower = header_name.lower()
    for key, value in headers.items():
        if key.lower() == header_name_lower:
            return value
    return None


def requested_host(headers: HeaderSource) -> str | None:
    """The host this request is for: the ``Host``, and nothing else.

    Callers ask for the FACT, not for a header name, which is why this exists at all as a
    one-line function — a reader phrasing it as its own header read is a copy that can
    disagree with this one. What a proxy in front of this app did to produce that ``Host``
    is the edge's business and has no spelling here.
    """
    return get_header_case_insensitive(headers, "Host")


def validate_virtual_host(value: str | None) -> str:
    """The host a tenant is served at, folded to lowercase — or a ValueError saying why not.

    ONE definition of the shape, so the ORM validator, the admin form and the management
    API cannot disagree about it. The card publishes this string verbatim, so a value that
    is not a bare host is a value nothing can dial.

    The parsing is ``urlsplit``'s, never string surgery: it decides where a netloc ends,
    what a path is, where userinfo stops and whether a port is a number. Two rules are
    spelled out here because ``urlsplit`` accepts what no request can use. Whitespace:
    ``urlsplit`` parses ``a b.com`` happily and no ``Host`` header can carry a space
    (RFC 3986 §3.2.2). And the characters the admin plane refuses: ``urlsplit`` takes
    ``seller_one.example.com``, but Werkzeug serves that ``Host`` with an empty
    ``request.host``, so every redirect and OAuth callback URL the admin UI builds for the
    tenant names no host. A row holding either is unreachable, which is the same defect
    class as a fabricated host.

    Accepts a bare ``host`` and ``host:port``, including a bracketed IPv6 literal, because
    the card publishes this string verbatim and a client dials what the card says.
    """
    if value is None or not value.strip():
        raise ValueError("virtual_host is required: a tenant declares the host it is served at")
    host = value.strip().lower()
    if any(ch.isspace() for ch in host):
        raise ValueError(f"virtual_host {value!r} contains whitespace, so no Host header can name it")
    # EVERY ValueError out of here carries an authored message. ``urlsplit`` raises its own
    # for an unterminated IPv6 bracket ("Invalid IPv6 URL"), and the management API answers
    # 400 with ``str(exc)`` -- so an uncaught one puts urllib's text in a response body,
    # which is the exposure CodeQL flags on that line. Re-raised like the port below.
    try:
        parts = urlsplit(f"//{host}")
    except ValueError as exc:
        raise ValueError(f"virtual_host {value!r} is not a host this seller can be served at") from exc
    if parts.path or parts.query or parts.fragment:
        raise ValueError(
            f"virtual_host {value!r} is not a bare host: it carries a scheme or a path. "
            "Store the host a request names, e.g. 'seller.example.com' or 'seller.example.com:8443'"
        )
    if parts.netloc != host or "@" in parts.netloc:
        raise ValueError(f"virtual_host {value!r} is not a bare host[:port]")
    # A trailing colon parses as "no port" rather than as an error, so `host:` and `[::1]:`
    # would store and then publish `https://host:/a2a` on the card. The card is dialled
    # verbatim, so a host that cannot be dialled is the defect this function exists to catch.
    if host.endswith(":"):
        raise ValueError(f"virtual_host {value!r} ends with a colon but names no port")
    try:
        parts.port  # noqa: B018 — raises for a non-numeric port
    except ValueError as exc:
        raise ValueError(f"virtual_host {value!r} has a non-numeric port") from exc
    if not parts.hostname:
        raise ValueError(f"virtual_host {value!r} names no host")
    if not _SERVABLE_HOST.fullmatch(host):
        raise ValueError(
            f"virtual_host {value!r} is not a host the admin UI can serve: use letters, digits, '-' and '.' "
            "(no '_'; an international name in its xn-- form) or a bracketed IPv6 literal, "
            "with a port from 1 to 65535"
        )
    return host


def hostname_of(host: str) -> str:
    """*host* without its port.

    A ``Host`` header carries a port whenever the origin is not on the scheme's default
    (``storyboard.adcp.test:8443``), and both proxies forward it intact. ``virtual_host``
    stores the ORIGIN a tenant is served at, port included, because that is the string the
    agent card has to publish -- a card advertising ``https://storyboard.adcp.test/a2a``
    for an agent listening on 8443 sends every A2A client to a closed port, which is
    exactly what took the A2A conformance axis from 27 passing checks to zero.

    So the port is dropped where a HOSTNAME is what the reader needs, and nowhere else.
    Two readers need one: the tenant routing lookups in ``TenantLookupRepository``, which
    compare host to host so a request naming either form resolves; and
    ``Tenant.primary_domain``, which feeds ``publisher_properties[].publisher_domain`` --
    a field AdCP constrains to ``^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[...])*$``, admitting no
    colon. Feeding the port into that pattern failed every product of such a tenant and
    answered INTERNAL_ERROR for the whole catalogue, which is the defect that first named
    these two jobs.

    Projecting a stored origin onto a hostname is not the defensive re-validation the
    architecture forbids: the column's contents are trusted exactly as stored, and what
    happens here is that one reader wants a different part of the same fact.

    The answer is LOWERCASE -- ``urlsplit(...).hostname`` folds case -- and both callers
    depend on that: ``_same_host`` compares this against a case-folded ``virtual_host``
    column, and ``publisher_domain``'s pinned pattern (``^[a-z0-9]...``) admits no
    uppercase at all. Only this side folding is what took every tenant-routing reader dark
    for a host STORED with uppercase (PR #2191), so the column is folded too.
    ``tests/unit/test_request_host_headers.py`` pins the fold rather than leaving it
    inherited.
    """
    return urlsplit(f"//{host}").hostname or host


def path_from_asgi_scope(scope: Mapping[str, Any]) -> str:
    """The request path with any ASGI ``root_path`` mount prefix stripped.

    A sub-mounted app sees ``path`` still carrying the mount prefix, so anything
    matching a path against a route table or a surface allowlist has to strip it
    first. Two copies of that rule is two chances to disagree about the empty-path
    edge, so every routing predicate that must agree with the dispatcher calls
    this one.

    Deliberately the OPPOSITE of the path that feeds a signature base: ``@target-uri``
    covers the bytes the client dialed, mount prefix and percent-encoding intact
    (see ``src.core.signing.capture``). Do not collapse the two.
    """
    path = str(scope.get("path", ""))
    root_path = str(scope.get("root_path") or "")
    if root_path and path.startswith(root_path):
        path = path[len(root_path) :] or "/"
    return path
