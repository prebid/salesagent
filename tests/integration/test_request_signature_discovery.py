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
``# brand.json discovery chain (ADCP #3690)`` and ``# identity.key_origins consistency
check (ADCP #3690)`` blocks. The step numbering is ``security.mdx`` @ v3.1.1
"Verifier checklist (requests)".

Covers: salesagent-hksr (the ordering invariant, and the discovery-code family reachable
through our OWN resolution path).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from adcp.signing.errors import (
    REQUEST_SIGNATURE_CAPABILITIES_UNREACHABLE,
    REQUEST_SIGNATURE_HEADER_MALFORMED,
    REQUEST_SIGNATURE_KEY_ORIGIN_MISSING,
)

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
    counterparty_key,
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
# 2. A second discovery branch, reached through our own resolution path
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestKeyOriginMissingIsGraded:
    """A resolution advertising no ``key_origins`` must be REFUSED, not silently trusted."""

    def test_a_resolution_without_key_origins_is_rejected_rather_than_skipping_the_check(
        self, integration_db, counterparty_keypair
    ):
        """The ``or {}`` in ``verifier._run_verifier``'s ``VerifyOptions``, graded on the wire.

        ``VerifyOptions(expected_key_origins=None)`` does not mean "no declaration" to the
        SDK — it means "the adopter did not thread the map through", and
        ``_maybe_check_key_origin`` responds by emitting a ``UserWarning`` and RETURNING.
        The spec's step-7 key-origin consistency check — the defense against the
        shared-tenancy spoof where an attacker's brand.json lists a victim's ``jwks_uri``
        while the victim's capabilities advertise a different origin — is then silently
        OFF, and the request is ACCEPTED. The SDK says so in the warning itself: "pass an
        empty dict if the operator advertises no map and you want the missing-declaration
        rejection to fire."

        So the counterparty here resolves fine, its signature verifies on its merits, and
        the ONLY thing wrong is that its capabilities document declared no
        ``identity.key_origins`` map. Correct behavior is a 401
        ``request_signature_key_origin_missing``. The failure mode this guards is a 200
        with a warning in a log nobody reads, which is a silent-acceptance bug and the
        reason this is graded on the WIRE rather than on ``VerifyOptions`` alone.

        Built with ``model_copy`` off the resolution the production constructor produced,
        so everything except the one field under test is exactly what production builds.
        (Dropping ``key_origin`` from the registry entry instead would not reach this:
        ``build_registry_resolution`` requires the key and raises ``KeyError``.)
        """
        from src.core.signing import verifier

        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=COUNTERPARTY_AGENT_URL)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with _cold_discovery_state(), counterparty_key(jwks):
                verifier.AGENT_RESOLUTION_CACHE[COUNTERPARTY_AGENT_URL] = verifier.AGENT_RESOLUTION_CACHE[
                    COUNTERPARTY_AGENT_URL
                ].model_copy(update={"key_origins": None})

                with (
                    declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
                    verifier_spy() as calls,
                ):
                    response = client.post(CAPABILITIES_ADCP_PATH, content=body, headers=headers)

        assert rejection_code(response) == REQUEST_SIGNATURE_KEY_ORIGIN_MISSING, (
            "a counterparty whose capabilities document declares no identity.key_origins map "
            "must be refused with request_signature_key_origin_missing. Passing None to "
            "VerifyOptions makes the SDK WARN and skip the spec's step-7 consistency check, so "
            "the request is accepted with the shared-tenancy defense silently off; the map must "
            f"be passed as an empty dict instead. Got status {response.status_code} with "
            f"WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}"
        )
        assert len(calls) == 1, f"the signed POST must reach the SDK verifier exactly once; it ran {len(calls)}x"
        assert calls[0]["options"].expected_key_origins == {}, (
            "the absent map must reach VerifyOptions as an EMPTY DICT, which is what makes the "
            "check run and reject; None is what makes it skip with a warning. Got "
            f"{calls[0]['options'].expected_key_origins!r}"
        )
