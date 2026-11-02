"""The discovery-code family, graded on the wire (#1291, salesagent-hksr).

What a failed counterparty walk must NOT collapse into
------------------------------------------------------
The naive shape is for ``_resolution_for`` to catch ``AgentResolverError`` and return
whatever is cached — usually ``None`` — so EVERY discovery failure lands on one wire
answer: ``request_signature_key_unknown`` at step 7. AdCP 3.1.1 gives the brand.json
walk its own rejection codes precisely so a caller can tell a retryable transport
failure (``*_unreachable``) apart from a misconfiguration (``*_missing`` /
``*_malformed`` / ``*_mismatch``) — the distinction the SDK's own
``adcp/signing/errors.py`` block comment states in those words. Collapsing all of them
onto ``key_unknown`` tells a counterparty with a briefly unreachable capabilities
endpoint that its KEY is wrong, which is both the wrong diagnosis and the wrong retry
advice.

The ordering invariant this whole ticket exists to protect
----------------------------------------------------------
Mapping the failure is the easy half. The hard half is WHERE it is raised.
:func:`~src.core.signing.verifier._verify_signed` calls
:func:`~src.core.signing.verifier._resolution_for` BEFORE it calls the SDK checklist, so
an implementation that raises the mapped code at resolution time makes a discovery
failure OUTRANK checklist steps 1-6 — a request with a malformed ``Signature-Input``
from an unreachable counterparty would answer ``capabilities_unreachable`` instead of
``request_signature_header_malformed``. That reorders a graded artifact: the conformance
suite fixes which step each negative vector fails at, and step 1 is not negotiable
against a step-7 concern.

The design's answer is to DEFER — hand the checklist a JWKS resolver
(:class:`~src.core.signing.verifier._FailedDiscoveryJwksResolver`) that raises the mapped
code from inside its ``__call__``, so the discovery failure surfaces at step 7 where key
resolution actually happens and every earlier step keeps winning.
:class:`TestDiscoveryFailureDefersToTheChecklist` is the canary for exactly that, and it
is the single most important test in this module: it is the difference between "correct"
and "shipped a reordering".

Tier-3 brand authorization is NOT graded here
---------------------------------------------
This module used to carry four more tests over the Tier-3 binding ("is this agent
authorized to act FOR THIS BRAND", ``request_signature_agent_not_in_brand_json`` /
``request_signature_brand_origin_mismatch``). They graded
``request_verifier_middleware._check_brand_authorization`` and seeded a
``_BRAND_AUTHZ_RESOLVER_CACHE``, and NEITHER NAME EXISTS on this architecture:
:func:`~src.core.signing.verifier._resolution_for` delegates the entire walk to the SDK's
``resolve_agent`` and holds no authorization seam of its own, so there is no production
object to configure and no decision to grade. They are removed rather than rewritten
against a stand-in, because a Tier-3 test that seeds a cache nothing reads passes
unconditionally while reading as though it proved a brand binding — worse than the
absence it would be hiding. The one arm still reachable is
:func:`~src.core.signing.verifier._map_brand_json_resolver_error`, which maps the SDK's
``agent_not_found`` onto ``request_signature_agent_not_in_brand_json`` as part of the
DISCOVERY chain; that is a different obligation (the walk found no entry) from the Tier-3
one (the entry exists and the brand did not delegate to it), and it is ungraded today.

Spec grounding
--------------
AdCP 3.1.1 via ``adcp==6.6.0``. Codes: ``adcp/signing/errors.py`` — the
``# brand.json discovery chain (ADCP #3690)`` block. The step numbering is ``security.mdx``
@ v3.1.1 "Verifier checklist (requests)", whose step 6 is where this seller's one key
location comes from (``docs/design/request-signing-subset.md``).

Covers: salesagent-hksr (the ordering invariant, and the discovery-code family reachable
through our OWN resolution path).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from adcp.signing.agent_resolver import AgentResolution
from adcp.signing.errors import (
    REQUEST_SIGNATURE_CAPABILITIES_UNREACHABLE,
    REQUEST_SIGNATURE_HEADER_MALFORMED,
)

from src.core.signing import verifier
from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import (
    CAPABILITIES_ADCP_PATH,
    COUNTERPARTY_AGENT_URL,
    COUNTERPARTY_KID,
    LADDER_OPERATIONS,
    MALFORMED_SIGNATURE_HEADERS,
    SIGNING_PRINCIPAL_ID,
    SIGNING_TENANT_ID,
    UNRESOLVABLE_AGENT_URL,
    bucketed_declaration,
    declared_posture,
    keypair_for,
    rejection_code,
    request_headers,
    seed_principal,
    signed_probe,
    verifier_spy,
)


@contextmanager
def _cold_discovery_state() -> Iterator[None]:
    """Run with an empty resolution cache AND an empty failure cooldown.

    Both dicts are PROCESS-level module state on
    :mod:`src.core.signing.verifier`, and both are load-bearing here for the same
    reason: ``_resolution_for`` short-circuits on a cached resolution and, separately,
    on a recent failure — ``now - _RESOLUTION_FAILURES[agent_url].at <
    agent_resolution_refetch_cooldown_seconds`` returns the cache WITHOUT re-attempting
    the walk. That cooldown is deliberate production behavior (it stops one broken
    counterparty starting a three-hop walk per request) and is not what these tests
    grade, but a test sending two requests from the same counterparty would otherwise
    grade the FIRST request's walk and the SECOND request's cooldown, i.e. two different
    inputs wearing one name.

    Cleared on the way out as well as in, because the constants here are shared with
    ``tests/integration/test_request_signature_middleware.py`` and pytest-randomly
    reorders modules.
    """
    from src.core.signing import verifier

    def _clear() -> None:
        verifier.AGENT_RESOLUTION_CACHE.clear()
        verifier._RESOLUTION_FAILURES.clear()

    _clear()
    try:
        yield
    finally:
        _clear()


#: The counterparty's own origin, which is the ONE place its keys may live.
_WELL_KNOWN_ORIGIN = "https://buyer.example.com"

#: A location the spec's step 6 permits and this seller does not: same brand, same keys,
#: a different origin. A ``keys.`` subdomain rather than a different path, because the rule
#: is about the ORIGIN the keys were fetched from.
_KEYS_ELSEWHERE = "https://keys.buyer.example.com"


def _resolution_at(jwks: dict[str, Any], origin: str) -> AgentResolution:
    """What ``resolve_agent`` returns for a counterparty publishing *jwks* at *origin*.

    Everything except the location is held constant, so the two tests below differ in one
    value: the keyset is the real one, the brand.json is the same document, and the agent is
    :data:`COUNTERPARTY_AGENT_URL` either way.
    """
    jwks_uri = f"{origin}{verifier.WELL_KNOWN_JWKS_PATH}"
    return AgentResolution(
        agent_url=COUNTERPARTY_AGENT_URL,
        brand_json_url=f"{_WELL_KNOWN_ORIGIN}/.well-known/brand.json",
        agent_entry={"type": "buying", "url": COUNTERPARTY_AGENT_URL, "jwks_uri": jwks_uri},
        jwks_uri=jwks_uri,
        jwks=jwks,
        fetched_at=0.0,
    )


@pytest.fixture(scope="module")
def counterparty_keypair() -> tuple[Any, dict[str, Any]]:
    """A real Ed25519 request-signing keypair: (private_key, public JWKS).

    Keyed by the SAME :data:`COUNTERPARTY_KID` :func:`signed_probe` signs under by
    default, so the key this fixture publishes and the key the probe names can never
    drift into two different kids — which would make every test here fail at step 7 for
    a reason that has nothing to do with what it grades.
    """
    return keypair_for(COUNTERPARTY_KID)


# --------------------------------------------------------------------------
# 1. The ordering canary
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDiscoveryFailureDefersToTheChecklist:
    """A discovery failure is a STEP 7 answer and must never outrank an earlier step.

    The canary for the design's raise-inside-``__call__`` decision. Both requests below
    come from the SAME counterparty with the SAME unreachable ``agent_url`` and differ in
    exactly one thing — whether the signature headers parse — so the only thing that can
    explain two different codes is WHERE the discovery failure is raised.
    """

    def test_a_malformed_signature_outranks_an_unresolvable_counterparty(self, integration_db, counterparty_keypair):
        """Malformed headers -> step 1; a well-formed signature -> the discovery code.

        The walk fails for real, with no network and no patched resolver: the SDK
        resolves and validates the authority synchronously before opening a socket
        (``adcp/signing/ip_pinned_transport.py``), so :data:`UNRESOLVABLE_AGENT_URL`'s
        loopback authority is refused as a reserved range and ``resolve_agent`` raises
        ``AgentResolverError("capabilities_unreachable")`` in microseconds.

        Both halves are asserted in one test on purpose. The ordering claim alone is
        vacuously TRUE on a tree that emits no discovery code at all, so a canary that
        asserted only the malformed case would go green through a regression that undid
        the whole mapping and catch nothing. The second rung is what makes the first one
        mean something.
        """
        private_key, _jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=UNRESOLVABLE_AGENT_URL)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)):
                with _cold_discovery_state():
                    # POST, like its well-formed twin below: this test's own docstring says
                    # the two requests differ in exactly one thing, and while this leg was a
                    # GET they differed in two. #1721 deleted the GET route, so the malformed
                    # probe was answered 405 by the router and never reached the verifier at
                    # all -- ``rejection_code`` read None, which is not a wrong code but no
                    # verdict, and the step-1-outranks-step-7 claim was graded vacuously.
                    malformed = client.post(
                        CAPABILITIES_ADCP_PATH,
                        content=body,
                        headers=request_headers(token, MALFORMED_SIGNATURE_HEADERS),
                    )
                with _cold_discovery_state():
                    well_formed = client.post(CAPABILITIES_ADCP_PATH, content=body, headers=headers)

        malformed_code = rejection_code(malformed)
        well_formed_code = rejection_code(well_formed)

        assert malformed_code == REQUEST_SIGNATURE_HEADER_MALFORMED, (
            "a malformed Signature-Input must be refused at checklist step 1 even when the "
            "counterparty is unresolvable. The discovery failure is a step-7 answer, so it "
            "must be DEFERRED into key resolution (a JWKS resolver that raises from its own "
            "__call__) rather than raised where _resolution_for is called — which is BEFORE "
            f"the checklist runs at all. Got {malformed_code!r}"
        )
        assert well_formed_code == REQUEST_SIGNATURE_CAPABILITIES_UNREACHABLE, (
            "a well-formed signature from a counterparty whose capabilities hop cannot be "
            "reached must be refused with the discovery code the pinned spec gives that "
            "failure, not folded into request_signature_key_unknown — the counterparty's key "
            "is not what is wrong, and key_unknown is the wrong retry advice. Got "
            f"{well_formed_code!r}"
        )
        assert malformed_code != well_formed_code, (
            "the two requests differ only in whether the signature headers parse, so a single "
            "code for both means the discovery failure swallowed the checklist"
        )


# --------------------------------------------------------------------------
# 2. The one key location, enforced on the resolution path
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestKeysAwayFromTheWellKnownLocationAreRefused:
    """A counterparty that publishes its keys elsewhere is REFUSED, not verified.

    This seller reads a counterparty's keys from ``<agent origin>/.well-known/jwks.json``
    and nowhere else (``docs/design/request-signing-subset.md``). security.mdx @ v3.1.1
    step 6 makes that the default and permits ``agents[].jwks_uri`` to name somewhere else;
    declining the second half is what removes the need for the key-origin consistency check
    the spec couples two cache TTLs to protect.

    Which makes the gate load-bearing rather than cosmetic: with no origin pin, a
    counterparty-chosen key location would be admitted on the counterparty's word alone.
    """

    def test_a_valid_signature_over_a_key_published_elsewhere_is_still_refused(
        self, integration_db, counterparty_keypair
    ):
        """The ONLY thing wrong is WHERE the keys live, and that alone is a 401.

        The resolution carries the counterparty's REAL public JWKS, so every later checklist
        step would pass: the signature verifies against exactly this key. What it does not
        carry is the one location this seller reads — the keys sit on a ``keys.`` subdomain,
        which is a different origin — so the resolution is never admitted and the request is
        answered ``request_signature_jwks_untrusted``, naming the trust decision rather than
        blaming the key.

        ``resolve_agent`` is the patch target, and it is the legitimate one: it dials three
        HTTP hops at a counterparty that deliberately cannot resolve (RFC 6761 ``.test``).
        Everything downstream of it — the gate, the failure cooldown, the deferred step-7
        resolver, the wire rendering — is production.
        """
        from adcp.signing.errors import REQUEST_SIGNATURE_JWKS_UNTRUSTED

        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=COUNTERPARTY_AGENT_URL)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with (
                _cold_discovery_state(),
                declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
                patch.object(verifier, "resolve_agent", return_value=_resolution_at(jwks, _KEYS_ELSEWHERE)),
            ):
                response = client.post(CAPABILITIES_ADCP_PATH, content=body, headers=headers)

        assert rejection_code(response) == REQUEST_SIGNATURE_JWKS_UNTRUSTED, (
            "a counterparty whose brand.json points its JWKS away from "
            f"<agent origin>{verifier.WELL_KNOWN_JWKS_PATH} must be refused with "
            "request_signature_jwks_untrusted. The keyset here is the real one and the signature "
            "over it is valid, so anything else means the location was taken on the counterparty's "
            f"word. Got status {response.status_code} with "
            f"WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}"
        )

    def test_the_well_known_location_at_that_same_origin_is_accepted(self, integration_db, counterparty_keypair):
        """The control: one variable apart, and it verifies.

        Same counterparty, same keyset, same signature — the JWKS is at the well-known path
        of the agent's own origin. Without this row the test above is equally explained by a
        seller that refuses every walked counterparty, and the gate would be graded by
        nothing.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=COUNTERPARTY_AGENT_URL)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with (
                _cold_discovery_state(),
                declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
                patch.object(verifier, "resolve_agent", return_value=_resolution_at(jwks, _WELL_KNOWN_ORIGIN)),
                verifier_spy() as calls,
            ):
                response = client.post(CAPABILITIES_ADCP_PATH, content=body, headers=headers)

        assert rejection_code(response) is None, (
            "the same signature over the same keyset, published at the one location this seller "
            f"reads, must be accepted; got {rejection_code(response)!r} (status {response.status_code})"
        )
        assert len(calls) == 1, f"the signed POST must reach the SDK verifier exactly once; it ran {len(calls)}x"
        assert calls[0]["options"].expected_key_origins is None, (
            "no key-origin map may reach VerifyOptions: with one possible key location there is no "
            "origin to pin, and passing a map would put a counterparty document back in the "
            f"decision. Got {calls[0]['options'].expected_key_origins!r}"
        )
