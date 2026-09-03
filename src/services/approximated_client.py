"""Approximated vendor client (TLS/domain-routing proxy).

Every other operator-configured vendor this repo talks to (Kevel, Triton,
Xandr, GAM) routes through ``src/adapters/`` or a service, never a Flask
blueprint. Approximated was the one exception: new code built fresh inside
``src/admin/blueprints/settings.py``. Moved here so the layer choice matches
every other vendor, and so the vendor-specific status-code interpretation
(404 meaning "not registered", 409 meaning "already registered") lives beside
the client that produces those statuses instead of inline in the blueprint.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from src.core.database.models import Tenant
from src.core.security.egress.destination import VendorConstant
from src.core.security.outbound_http import OutboundError, OutboundResult, send

# The Approximated vhost API host, in ONE place. It was repeated at all four
# call sites, which made the ticket's own "drive it at a local origin" gate
# unreachable. A VendorConstant, never an env read: this is a credential-bearing
# destination that must not become silently redirectable (GH #1802).
APPROXIMATED_BASE_URL = VendorConstant(url="https://cloud.approximated.app")


def _api(method: str, path: str, api_key: str, *, json_body: JsonValue | None = None) -> OutboundResult:
    """One Approximated call through the egress seam.

    max_attempts=1: these are vhost mutations and a status read, none of which
    retried before — turning one failed vhost create into three is exactly the
    drift this migration must not introduce.
    """
    return send(
        f"{APPROXIMATED_BASE_URL.url}{path}",
        method=method,
        headers={"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
        json=json_body,
        timeout=10.0,
        max_attempts=1,
    )


class DomainNotOwned(Exception):
    """The acting tenant does not own the domain the request named.

    Raised by :func:`tenant_owns_domain`, which is the only producer of the
    :class:`OwnedDomain` every domain-bearing operation below requires.
    """


@dataclass(frozen=True, slots=True)
class OwnedDomain:
    """A domain PROVEN to belong to the tenant acting on it.

    The ownership rule used to live inside one of the three Flask handlers that
    dial Approximated, so the other two omitted it -- and did, for as long as
    all three have existed. Copying the ``if`` into the siblings would have made
    the fourth route omit it in turn.

    This type is what stops that, and the property it buys is precise: a route
    cannot reach a dial without PRODUCING an ``OwnedDomain``, and the sanctioned
    producer is :func:`tenant_owns_domain`. Omission -- the actual defect, and
    the one that held for as long as these routes have existed -- becomes
    impossible: there is no argument to pass. What remains possible is a
    deliberate forgery, ``OwnedDomain(domain)`` written by hand, because this is
    an ordinary frozen dataclass and the Flask handlers are untyped
    (``check_untyped_defs = False``), so mypy does not grade them. That is a
    different failure mode: it is one grep (``OwnedDomain(`` outside this
    module) and it is a lie a reviewer can see, not a step someone forgot.
    """

    domain: str


def tenant_owns_domain(tenant: Tenant, domain: str | None) -> OwnedDomain:
    """Prove *tenant* owns *domain*, or refuse.

    Takes the Tenant, not a bare ``virtual_host`` string, on purpose: with two
    strings a caller can satisfy the gate with ``tenant_owns_domain(domain,
    domain)`` and the proof means nothing. Read ``tenant.virtual_host`` INSIDE
    the caller's session block -- a detached instance raises here, not at the
    dial.
    """
    if not isinstance(domain, str) or not domain or tenant.virtual_host != domain:
        raise DomainNotOwned("Domain must match tenant's virtual_host")
    return OwnedDomain(domain=domain)


@dataclass(frozen=True, slots=True)
class DomainStatus:
    """The outcome of checking a domain's Approximated registration.

    ``registered=False`` is a meaningful, non-error answer (Approximated's
    404), not a failure — every other field is only present when registered.
    """

    registered: bool
    status: str | None = None
    tls_enabled: bool = False
    ssl_active: bool = False
    target_address: str | None = None


def get_domain_status(owned: OwnedDomain, api_key: str) -> DomainStatus:
    """Check whether ``owned.domain`` is registered with Approximated.

    Raises ``OutboundError`` for anything other than a 404 -- the caller
    handles that as a generic upstream failure, not a domain outcome.
    """
    try:
        response = _api("GET", f"/api/vhosts/by/incoming/{owned.domain}", api_key)
    except OutboundError as exc:
        if exc.http_status == 404:
            return DomainStatus(registered=False)
        raise

    response_data = response.json()
    # Approximated API wraps data in a 'data' key.
    domain_data = response_data.get("data", response_data)
    return DomainStatus(
        registered=True,
        status=domain_data.get("status"),
        tls_enabled=domain_data.get("has_ssl", False),
        ssl_active=(domain_data.get("status") or "").startswith("ACTIVE_SSL"),
        target_address=domain_data.get("target_address"),
    )


@dataclass(frozen=True, slots=True)
class RegisterResult:
    """The outcome of registering a domain with Approximated."""

    already_registered: bool


def register_domain(owned: OwnedDomain, backend_url: str, api_key: str) -> RegisterResult:
    """Register ``owned.domain`` with Approximated, pointing it at ``backend_url``.

    Raises ``OutboundError`` for anything other than a 409 -- "already
    registered" is the one vendor status this operation treats as success,
    not a failure to translate generically.
    """
    try:
        _api(
            "POST",
            "/api/vhosts",
            api_key,
            json_body={"incoming_address": owned.domain, "target_address": backend_url},
        )
    except OutboundError as exc:
        if exc.http_status == 409:
            return RegisterResult(already_registered=True)
        raise
    return RegisterResult(already_registered=False)


@dataclass(frozen=True, slots=True)
class UnregisterResult:
    """The outcome of unregistering a domain from Approximated."""

    already_unregistered: bool


def unregister_domain(owned: OwnedDomain, api_key: str) -> UnregisterResult:
    """Unregister ``owned.domain`` from Approximated.

    Raises ``OutboundError`` for anything other than a 404 -- "already
    unregistered" is the one vendor status this operation treats as success.
    """
    try:
        _api("DELETE", f"/api/vhosts/by/incoming/{owned.domain}", api_key)
    except OutboundError as exc:
        if exc.http_status == 404:
            return UnregisterResult(already_unregistered=True)
        raise
    return UnregisterResult(already_unregistered=False)


@dataclass(frozen=True, slots=True)
class DnsToken:
    """The outcome of requesting an Approximated DNS widget token.

    A stricter variant of this module's outcome-dataclass pattern
    (``DomainStatus``/``RegisterResult``/``UnregisterResult`` are plain
    ``@dataclass``): ``token`` is the one field this call site ever reads off
    the vendor response, typed instead of left as an open dict a caller reads
    with ``.get()``.
    """

    token: str | None


def get_dns_token(api_key: str) -> DnsToken:
    """Request an Approximated DNS widget token.

    Every status this operation can receive is a genuine failure -- there is
    no vendor-status outcome to translate, so callers read ``OutboundError``
    directly (e.g. ``exc.http_status`` to propagate an upstream 401/403).
    """
    response = _api("GET", "/api/dns/token", api_key)
    return DnsToken(token=response.json().get("token"))
