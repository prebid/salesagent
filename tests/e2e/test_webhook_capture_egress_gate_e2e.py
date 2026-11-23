"""E2E: the egress gate is still ARMED inside the stack that the receiver now passes.

salesagent-mp53.9's acceptance is that the e2e webhook-capture receiver is
reachable "with production's SSRF and reserved-TLD gates PASSING ON THEIR OWN
TERMS and NOTHING PATCHED". That sentence is only worth something if its
negation is observable — a stack that reached the receiver by quietly disarming
the gate would satisfy every OTHER test in this suite identically. This module
is the falsifiability the architect review made a condition of the approach
(``ny68.4`` Q1): from INSIDE the stack, the gate still refuses what it is
supposed to refuse.

The mechanism being graded, stated so a later reader does not mistake it for a
bypass: the compose network moves onto a NON-PRIVATE, per-stack-allocated subnet
(candidate base ``192.88.99.0/24``, sliced per stack). Nothing in ``src/``
changes; no env hatch is introduced (pinned separately by
``tests/unit/test_architecture_no_private_destinations.py``). The gate's
documented terms are exhaustively address arithmetic —
``EgressPolicy._blocked_address``'s ``is_loopback``/``is_link_local``/
``is_private`` flag half plus its supplement ranges — and the destination is
meant to genuinely satisfy them. Per AdCP 3.1.1
(``v3.1.1:docs/creative/canonical-formats.mdx``) the forbidden set is RFC1918 /
loopback / link-local / CGNAT plus RFC 6761 special-use NAMES; today's 172.16/12
default bridge is explicitly forbidden.

**Which gate, after GH #1802.** ``src/core/security/url_validator.py`` is gone —
its SSRF half was deleted, and the RFC 2606/6761 reserved-TLD family that
briefly survived it moved with the rest — so ``src/core/security/egress/policy.py``
now spells BOTH the address policy (as two verdicts) and
:func:`~src.core.security.egress.policy.is_reserved_tld_host`. The two address
verdicts are NOT interchangeable here and this module deliberately uses both:

* :meth:`EgressPolicy.check_registration` — DNS-free, and it reads NO
  environment. That is what the refusal arms need. The dial verdict is opened by
  ``ADCP_OUTBOUND_ALLOW_PRIVATE``, which ``docker-compose.e2e.yml`` sets to
  ``"true"`` on the ``tests`` service for the runner's own loopback fixtures —
  measured: under that hatch the dial verdict ACCEPTS ``10.0.0.7`` and
  ``127.0.0.1``, so grading the refusals through it would assert nothing in the
  one stack this module exists to falsify.
* :func:`outbound_http.validate_url` (the dial verdict, DNS-full, IP-pinning) —
  what the capture origin has to survive, because "resolves to an address the
  gate accepts" is a claim only DNS can settle.

Neither returns ``(bool, str)``: both RAISE, and the dial refusal is
deliberately opaque (AdCP 3.1.1 ``building/by-layer/L1/security.mdx`` point 6 —
a refusal never echoes the resolved address back), so there is no reason string
to assert on. HTTPS is unconditional since GH #1757, so the old
``require_https=`` argument has no successor and needs none.

**What this module proves.** 127.0.0.1, a literal RFC1918 address, link-local and
the cloud-metadata address are ALL still refused from inside the stack by the
real egress policy; the capture hostname is not a reserved-TLD name; and it
resolves INTO this stack's own declared subnet rather than out to public DNS
(the bounded DNS-leak risk recorded under OWNER DECISION 4 — ``adcp-e2e.dev`` is
an unregistered name and real DNS is never consulted).

**KNOWN CONFLICT, in-network only (GH #1802 × salesagent-mp53.9).** #1802 added
``192.88.99.0/24`` to ``EgressPolicy._SUPPLEMENT_NETWORKS`` (6to4 relay anycast,
RFC 7526) and refuses it under EVERY posture — the hatch explicitly cannot open
it. That is the exact pool ``scripts/dev/alloc-e2e-subnet.sh`` slices this
stack's network out of, so the two accept-case tests below now fail in-network:
the stack squats a range the merged gate forbids. The premise is what broke, not
the assertions, so they are left asserting acceptance rather than relaxed to
match — moving the pool (or reconciling the supplement entry) is owned by
``alloc-e2e-subnet.sh`` / ``docker-compose.e2e.yml`` / ``policy.py``, not here.

**What it does NOT prove, stated rather than implied.** With the network on a
non-private subnet, other in-stack services (``postgres:5432``, ``tests:8080``)
also become addresses the gate accepts. This stack therefore does NOT grade "a
webhook aimed at another service on my own network is refused". That loss is
bounded to the one subnet slice this stack owns — unlike an ``ALLOW_PRIVATE``
hatch, which would open 127.0.0.1, 169.254.169.254, host.docker.internal and all
of RFC1918 everywhere, in any deployment that set it.
"""

from __future__ import annotations

import ipaddress
import os
import socket

import pytest

from src.core.exceptions import AdCPBlockedUrlError
from src.core.security.egress.policy import EgressPolicy, is_reserved_tld_host
from src.core.security.outbound_http import OperatorEndpoint, OutboundRequestBlocked, validate_url
from tests.e2e.conftest import e2e_in_network

#: The success-leg receiver (OWNER DECISION 4, salesagent-mp53.9). Kept as a
#: literal here rather than imported from the compose wiring: this module's whole
#: job is to check the wiring from the outside, and a name read out of the thing
#: under test cannot disagree with it.
CAPTURE_HOSTNAME = "webhooks.adcp-e2e.dev"

#: The per-stack subnet the compose network is allocated. Set by the stack
#: launcher alongside the compose ``${E2E_NETWORK_SUBNET}`` substitution.
SUBNET_ENV_VAR = "E2E_NETWORK_SUBNET"

#: Destinations that MUST stay refused however the stack's own network is
#: addressed. The RFC1918 literal is deliberately a 172.16/12 address — the range
#: the compose default bridge used to hand out, i.e. the one an accidental revert
#: to the old wiring would make reachable again.
STILL_REFUSED_URLS = (
    "https://127.0.0.1:8443/webhook",
    "https://172.17.0.2:8443/webhook",
    "https://10.0.0.7:8443/webhook",
    "https://192.168.1.10:8443/webhook",
    "https://169.254.169.254/latest/meta-data/",
    "https://host.docker.internal:8443/webhook",
)


requires_in_network = pytest.mark.skipif(
    not e2e_in_network(),
    reason="in-network only: a compose alias is not resolvable from the host path (set ADCP_TEST_HOST)",
)


def stack_subnet() -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    """This stack's declared network, from ``E2E_NETWORK_SUBNET``.

    Raises rather than skipping when unset: in-network, an absent value means the
    per-stack subnet allocation is not wired, which is precisely the failure this
    module exists to catch. Degrading to a skip would report that as success.
    """
    raw = os.getenv(SUBNET_ENV_VAR)
    if not raw:
        raise AssertionError(
            f"{SUBNET_ENV_VAR} is unset in-network — the e2e compose network has no per-stack "
            "subnet allocation, so every origin still resolves to a private default-bridge address"
        )
    return ipaddress.ip_network(raw, strict=False)


class TestTheGateIsStillArmed:
    """Refusals that must survive the network move — the negation of the acceptance."""

    @pytest.mark.parametrize("url", STILL_REFUSED_URLS)
    def test_private_and_metadata_destinations_are_still_refused(self, url: str) -> None:
        """The real gate refuses loopback, RFC1918, link-local and metadata destinations.

        Not a restatement of the unit-level SSRF tests: those run in a process
        with no compose network. This one runs where the receiver is actually
        reachable, which is the only place "we reached it without disarming the
        gate" can be told apart from "we reached it by disarming the gate".

        Graded through the REGISTRATION verdict, for two reasons the dial verdict
        cannot satisfy here (both measured, see the module docstring): it reads no
        ``ADCP_OUTBOUND_ALLOW_PRIVATE``, which this stack sets on the runner and
        which would otherwise turn four of these six refusals into acceptances;
        and it is DNS-free, so ``host.docker.internal`` is refused by name off the
        seam's own blocklist instead of by whatever a live resolver happens to
        answer. Same ``_blocked_address`` predicate either way — this is the
        posture-independent half of it, which is precisely what "still armed"
        means.
        """
        with pytest.raises(AdCPBlockedUrlError):
            EgressPolicy.check_registration(url)


class TestTheCaptureOriginPassesOnItsOwnTerms:
    """The receiver clears both gates because it genuinely satisfies them."""

    def test_capture_hostname_is_not_a_reserved_tld_name(self) -> None:
        """``.dev`` is a normal delegable gTLD, not an RFC 6761 special-use name.

        This is the FIRST gate the proof-of-control path applies
        (``notification_proof_service`` checks ``is_reserved_tld_host`` before it
        resolves anything), and it is why the refusal leg keeps using a ``.test``
        name: both legs stay gradeable, from opposite sides of the same check.
        """
        assert is_reserved_tld_host(CAPTURE_HOSTNAME) is False
        assert is_reserved_tld_host("webhooks.adcp.test") is True

    @requires_in_network
    def test_capture_hostname_resolves_into_this_stacks_own_subnet(self) -> None:
        """The alias resolves inside the compose network — never out to public DNS.

        ``adcp-e2e.dev`` is unregistered; a third party could register it later.
        That has no functional impact precisely BECAUSE the name is answered by
        the compose network's embedded DNS, and this assertion is what keeps that
        true instead of merely assumed.
        """
        subnet = stack_subnet()

        try:
            resolved = ipaddress.ip_address(socket.gethostbyname(CAPTURE_HOSTNAME))
        except socket.gaierror as exc:
            raise AssertionError(
                f"{CAPTURE_HOSTNAME} does not resolve in-network ({exc}) — the compose alias on "
                "the tls-proxy service is missing, so nothing answers for the capture origin"
            ) from exc

        assert resolved in subnet, (
            f"{CAPTURE_HOSTNAME} resolved to {resolved}, which is OUTSIDE this stack's "
            f"subnet {subnet} — the name escaped to a resolver outside the compose network"
        )

    @requires_in_network
    def test_this_stacks_subnet_is_one_the_gate_accepts(self) -> None:
        """The declared subnet is outside every range the gate blocks — the whole premise.

        The registration verdict again, and here the hatch-free property is what
        keeps the test from being vacuous: the dial verdict runs with
        ``allow_private=True`` in this stack, so it would accept a private subnet
        too and report success for a stack whose premise had collapsed.
        """
        subnet = stack_subnet()
        url = f"https://{subnet.network_address + 1}:8443/webhook"

        try:
            EgressPolicy.check_registration(url)
        except AdCPBlockedUrlError as exc:
            raise AssertionError(
                f"an address in this stack's own subnet {subnet} is refused by the egress policy. "
                "If the subnet is under 192.88.99.0/24, this is the GH #1802 conflict recorded in "
                "the module docstring: that range is now in EgressPolicy._SUPPLEMENT_NETWORKS and is "
                "refused under every posture. The pool in scripts/dev/alloc-e2e-subnet.sh has to move"
            ) from exc

    @requires_in_network
    def test_the_capture_origin_passes_the_unpatched_gate(self) -> None:
        """The delivery URL the receiver hands out survives the real gate, HTTPS required.

        The DIAL verdict here, unlike the two arms above: this is the only claim
        that needs DNS, because "the origin lands on an address the gate accepts"
        is not decidable without resolving it, and resolving it is what the
        delivery path itself does. HTTPS is no longer an argument — GH #1757 made
        it unconditional — so the old ``require_https=True`` is carried by the
        ``https://`` in the URL and by the seam's own scheme check.

        In-network only, and the resolution is answered by the compose network's
        embedded DNS: that is the same real lookup this module's sibling
        ``socket.gethostbyname`` assertion makes, and the reason ``adcp-e2e.dev``
        being unregistered has no functional consequence.
        """
        url = f"https://{CAPTURE_HOSTNAME}:8443/webhook/probe"

        try:
            validate_url(url, provenance=OperatorEndpoint(name="the e2e webhook capture receiver"))
        except OutboundRequestBlocked as exc:
            raise AssertionError(
                f"{url} was refused by the unpatched gate. The refusal is opaque by design "
                "(AdCP 3.1.1 L1 security point 6), so check the seam's log line for the cause; if the "
                "origin resolves under 192.88.99.0/24 it is the GH #1802 supplement-range conflict "
                "recorded in the module docstring"
            ) from exc
