"""The scheme predicate — the one decision that makes a trust root reachable.

salesagent-tgzb design step 8. ``_get_protocol_for_domain`` is the SINGLE place
that decides whether this agent publishes ``http://`` or ``https://`` for a host
(``src/core/agent_identity.py`` calls it at :45 and :94, and nothing else in the
tree calls it at all). Until this module existed it had **zero direct coverage**,
so the constraint salesagent-tgzb turns on — *the production predicate must not
be weakened to make the test stack serve https* — was enforced by nothing.

This module guards EXISTING behaviour; it drives no new production code. It is a
unit module on purpose: the https branch is invisible from any outer surface for
hosts like ``seller.example.com`` or ``x.adcp.test``, because no stack in this
repo answers there. The predicate is a pure function of one string, with no I/O.
"""

from __future__ import annotations

import pytest

from src.core.domain_config import _get_protocol_for_domain, _is_localhost, _is_single_label_host

# Hosts that CANNOT present a publicly-trusted certificate, so advertising https
# for them publishes an origin nothing can reach. Two shapes, one reason.
CANNOT_SERVE_HTTPS = [
    "localhost",  # the literal loopback name
    "localhost:8080",  # ... with the port the dev server uses
    "127.0.0.1",  # the loopback address
    "127.0.0.1:8443",  # ... even on a TLS-looking port: still not a public FQDN
    "proxy",  # docker-compose service name (the in-network e2e stack)
    "proxy:8000",
    "server-gw0",  # per-worker bdd_e2e container name
    "adcp-innet-1234-server-gw3:8080",  # ... fully qualified by project prefix
    "adcp-server",  # hyphens do not make a label dotted
]

# Hosts that CAN, because they are dotted names a CA could issue for.
CAN_SERVE_HTTPS = [
    "agent.localhost",  # the boundary — see the dedicated test below
    "agent.localhost:8443",
    "x.adcp.test",
    "proxy.adcp.test:8443",
    "adcp-innet-1234-tls-gw3.adcp.test:8443",  # the bdd_e2e TLS sidecar's name
    "seller.example.com",
    "seller-trustroot.sales-agent.example.com",
]


@pytest.mark.parametrize("host", CANNOT_SERVE_HTTPS)
def test_hosts_that_cannot_serve_https_are_published_as_http(host):
    """Loopback and single-label service names get ``http``.

    Commit 29252b060's rationale, restated as an assertion: a host with no
    publicly-trusted certificate that advertises https publishes a trust root
    nothing can reach. Flipping any of these to https is the exact weakening
    salesagent-tgzb must not perform.
    """
    assert _get_protocol_for_domain(host) == "http", (
        f"{host!r} cannot present a publicly-trusted certificate — publishing https "
        f"for it advertises a trust root no counterparty can reach"
    )


@pytest.mark.parametrize("host", CAN_SERVE_HTTPS)
def test_dotted_hosts_that_are_not_loopback_are_published_as_https(host):
    """A dotted, non-loopback name is the https branch.

    This is the branch salesagent-tgzb makes SERVABLE: the test stack earns
    https by presenting a certificate for a dotted name, never by being told to.
    """
    assert _get_protocol_for_domain(host) == "https", (
        f"{host!r} is a dotted non-loopback name — the scheme we publish for it must be https, "
        f"or a tenant served over TLS would advertise an http origin"
    )


def test_agent_localhost_is_https_because_the_loopback_set_is_an_exact_literal_match():
    """The boundary the whole TLS mechanism rests on.

    ``agent.localhost`` resolves to 127.0.0.1 (RFC 6761 §6.3) with no DNS zone
    and no ``/etc/hosts`` edit, yet it takes the **https** branch — because
    ``_is_localhost`` compares the port-stripped host against the exact literal
    set ``("localhost", "127.0.0.1")`` rather than testing a suffix, and because
    the name has a dot. Loosening either helper to "endswith localhost" or
    "contains localhost" silently deletes the only locally-servable https host
    the test stacks have.
    """
    assert _is_localhost("agent.localhost:8443") is False, (
        "_is_localhost must be an EXACT match on the port-stripped host; a suffix or "
        "substring match would classify agent.localhost as loopback and collapse the https branch"
    )
    assert _is_single_label_host("agent.localhost:8443") is False, (
        "agent.localhost has a dot, so it is not a single-label host"
    )
    assert _get_protocol_for_domain("agent.localhost:8443") == "https"


@pytest.mark.parametrize(
    "host, expected_localhost, expected_single_label",
    [
        ("localhost:8080", True, True),  # port stripped before BOTH comparisons
        ("127.0.0.1:8443", True, False),  # dotted, but an exact loopback literal
        ("proxy:8000", False, True),
        ("proxy.adcp.test:8443", False, False),
    ],
)
def test_the_port_is_stripped_before_either_branch_is_evaluated(host, expected_localhost, expected_single_label):
    """A port must never change the scheme.

    ``virtual_host`` carries a port (``get_tenant_by_virtual_host`` matches the
    Host header including it), so every value this predicate sees in the test
    stacks is ``name:port``. A predicate that forgot to strip it would classify
    ``localhost:8080`` as dotted — and publish https for loopback.
    """
    assert _is_localhost(host) is expected_localhost
    assert _is_single_label_host(host) is expected_single_label


@pytest.mark.parametrize("host", [None, ""])
def test_an_absent_host_is_not_treated_as_loopback_or_single_label(host):
    """Pinning the caller-guarded input, so a refactor cannot change it silently.

    ``canonical_agent_url`` only calls the predicate under ``if host:``, so no
    production path reaches these values. Both helpers return False for them,
    which means the predicate answers ``https`` — recorded here as the current
    contract, not endorsed as a good default. If a future caller drops the guard,
    this test is what makes the resulting behaviour visible instead of surprising.
    """
    assert _is_localhost(host) is False
    assert _is_single_label_host(host) is False
    assert _get_protocol_for_domain(host) == "https"


@pytest.mark.parametrize(
    "flag",
    [
        "ADCP_FORCE_HTTPS",
        "FORCE_HTTPS",
        "ADCP_AGENT_SCHEME",
        "ADCP_PROTOCOL",
        "SALES_AGENT_PROTOCOL",
        "ADCP_TLS",
        "ADCP_TEST_TLS",
        "E2E_TLS_BASE_URL",
    ],
)
@pytest.mark.parametrize("value", ["1", "true", "https"])
def test_no_environment_flag_can_force_https_for_a_host_that_cannot_serve_it(monkeypatch, flag, value):
    """salesagent-tgzb's acceptance line, made falsifiable.

    "No config flag exists that forces https for a host that cannot serve it."
    The temptation this ticket creates is to add exactly such a flag so the
    existing single-label test hosts look https-capable without serving TLS.
    These are the names such a flag would plausibly take; the scheme for
    ``proxy:8000`` must be ``http`` with every one of them set.
    """
    monkeypatch.setenv(flag, value)
    assert _get_protocol_for_domain("proxy:8000") == "http", (
        f"{flag}={value!r} changed the scheme for a single-label host — the predicate must derive "
        f"the scheme from the host alone, never from deployment configuration"
    )
    assert _get_protocol_for_domain("localhost:8080") == "http"
