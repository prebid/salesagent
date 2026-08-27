"""The discovery-code family and Tier-3 brand authorization (#1291, salesagent-hksr).

TDD-RED. None of what this module grades exists in
``src.core.signing.request_verifier_middleware`` yet: there is no
``_map_agent_resolver_error``, no ``_FailedDiscoveryJwksResolver``, no
``_check_brand_authorization``, no ``_map_brand_authorization_reason`` and no
``_BRAND_AUTHZ_RESOLVER_CACHE``, and line 903 still reads
``resolution.key_origins if resolution is not None else None`` where it must read
``resolution.key_origins or {}``.

What today's middleware does with a failed discovery, and why that is wrong
--------------------------------------------------------------------------
``_resolution_for`` catches ``AgentResolverError`` and returns whatever is cached
— usually ``None`` — so EVERY discovery failure collapses into one wire answer:
``request_signature_key_unknown`` at step 7. AdCP 3.1.1 gives the brand.json walk
its own rejection codes precisely so a caller can tell a retryable transport
failure (``*_unreachable``) apart from a misconfiguration (``*_missing`` /
``*_malformed`` / ``*_mismatch``) — the distinction the SDK's own
``adcp/signing/errors.py`` block comment states in those words. Collapsing all of
them onto ``key_unknown`` tells a counterparty with a briefly unreachable
capabilities endpoint that its KEY is wrong, which is both the wrong diagnosis and
the wrong retry advice.

The ordering invariant this whole ticket exists to protect
----------------------------------------------------------
Mapping the failure is the easy half. The hard half is WHERE it is raised.
``_handle_signed`` awaits ``_resolution_for`` BEFORE it calls the SDK checklist, so
an implementation that raises the mapped code at resolution time makes a discovery
failure OUTRANK checklist steps 1-6 — a request with a malformed ``Signature-Input``
from an unreachable counterparty would answer ``capabilities_unreachable`` instead
of ``request_signature_header_malformed``. That reorders a graded artifact: the
conformance suite fixes which step each negative vector fails at, and step 1 is not
negotiable against a step-7 concern.

The design's answer is to DEFER — hand the checklist a JWKS resolver that raises
the mapped code from inside its ``__call__``, so the discovery failure surfaces at
step 7 where key resolution actually happens and every earlier step keeps winning.
:class:`TestDiscoveryFailureDefersToTheChecklist` is the canary for exactly that,
and it is the single most important test in this module: it is the difference
between "correct" and "shipped a reordering".

Spec grounding
--------------
AdCP 3.1.1 via ``adcp==6.6.0``. Codes:
``adcp/signing/errors.py`` — the ``# brand.json discovery chain (ADCP #3690)`` and
``# identity.key_origins consistency check (ADCP #3690)`` blocks. Tier-3 binding:
``adcp/signing/brand_authz.py`` module docstring (listed in ``agents[]``, then
eTLD+1 or ``house.authorized_operators[]``), which cites #3690's security profile.
The step numbering is ``security.mdx`` @ v3.1.1 "Verifier checklist (requests)".

Covers: salesagent-hksr (ordering invariant, the discovery-code family reachable
through our OWN resolution path, Tier-3 brand authorization, and B4's registry
fallback staying exempt from it).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from adcp import get_adcp_spec_version
from adcp.signing.errors import (
    REQUEST_SIGNATURE_AGENT_NOT_IN_BRAND_JSON,
    REQUEST_SIGNATURE_BRAND_ORIGIN_MISMATCH,
    REQUEST_SIGNATURE_CAPABILITIES_UNREACHABLE,
    REQUEST_SIGNATURE_HEADER_MALFORMED,
    REQUEST_SIGNATURE_KEY_ORIGIN_MISSING,
)

from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import (
    BODYLESS_ADCP_PATH,
    COUNTERPARTY_AGENT_URL,
    COUNTERPARTY_KEY_ORIGIN,
    COUNTERPARTY_KID,
    LADDER_OPERATIONS,
    MALFORMED_SIGNATURE_HEADERS,
    REGISTRY_AGENT_URL,
    SIGNING_PRINCIPAL_ID,
    SIGNING_TENANT_ID,
    UNRESOLVABLE_AGENT_URL,
    brand_authz_resolver,
    bucketed_declaration,
    counterparty_key,
    declared_posture,
    keypair_for,
    registry_entry,
    rejection_code,
    request_headers,
    seed_principal,
    seeded_cache_entry,
    signed_probe,
    signing_config,
    verifier_spy,
)

# --------------------------------------------------------------------------
# Contract the implement atom must satisfy
# --------------------------------------------------------------------------
# The names below are this red test's half of the TDD contract.
#
#   _BRAND_AUTHZ_RESOLVER_CACHE   process-level cache of BrandAuthorizationResolver,
#                                 KEYED BY brand_json_url (design item 5 — multiple
#                                 agents can share one brand.json). If that key ever
#                                 changes, :func:`_brand_authorization` is the ONE
#                                 place to update, in the same commit.
#   _check_brand_authorization    the Tier-3 gate, run AFTER _run_verifier succeeds
#                                 and only for a brand-json-WALKED counterparty.
#
# THE REGISTRY-EXEMPTION MECHANISM (design item 7 asks this atom to name it, and
# there is a trap in the obvious answer)
# ----------------------------------------------------------------------------------
# The exemption MUST be decided by WHICH BRANCH of ``_resolution_for`` produced the
# resolution — the ``if not agent_url:`` registry-fallback branch versus the
# cache/walk branch — and NOT by any property of the AgentResolution object.
#
# Specifically, "was it built by ``build_registry_resolution``?" is the wrong test,
# even though it looks like the natural one. ``tests/helpers/signing.py``'s
# ``counterparty_key()`` seeds a WALKED counterparty's cache entry through that very
# constructor, deliberately and by its own docstring (":331-337 — so this fixture and
# the registry fallback can never drift into two different resolution shapes"). Keying
# the exemption off the constructor would therefore exempt every counterparty this
# suite seeds, and TestBrandAuthorizationIsEnforced would go quiet — three tests
# grading nothing while reading as if they graded Tier 3.
#
# A marker attribute on the resolution is also unavailable: AgentResolution is a
# pydantic model with ``model_config = ConfigDict(extra="forbid")``.
#
# So: carry the provenance out of ``_resolution_for`` alongside the resolution — e.g.
# a frozen ``_CounterpartyResolution(resolution, source: Literal["walk", "registry"])``,
# matching the ``_RequestContext`` / ``_BufferedBody`` style already in the module —
# and call ``_check_brand_authorization`` only for ``source == "walk"``.
#
# This module already discriminates the two: tests 3-5 seed via ``counterparty_key()``
# AND give the principal an agent_url (the WALK branch), so Tier 3 must RUN on them;
# test 6 gives the principal no agent_url (the REGISTRY branch) with the Tier-3
# resolver armed to refuse, so Tier 3 must NOT run. A constructor-based exemption
# fails tests 3 and 5; a "Tier 3 always runs" implementation fails test 6.
#
# The rest is read from the pinned SDK and is not an assumption:
# AgentResolverError.code -> the request_signature_* discovery codes
# (adcp/signing/errors.py), and BrandAuthorizationReason -> the same family
# (adcp/signing/brand_authz.py).


@contextmanager
def _cold_discovery_state() -> Iterator[None]:
    """Run with an empty resolution cache AND an empty failure cooldown.

    Both dicts are PROCESS-level module state, and both are load-bearing here for
    the same reason: ``_resolution_for`` short-circuits on a cached resolution and,
    separately, on a recent failure — ``now - _RESOLUTION_FAILURES[agent_url] <
    agent_resolution_refetch_cooldown_seconds`` returns the cache WITHOUT
    re-attempting the walk. That cooldown is deliberate production behavior (it
    stops one broken counterparty starting a three-hop walk per request) and is not
    what these tests grade, but a test sending two requests from the same
    counterparty would otherwise grade the FIRST request's walk and the SECOND
    request's cooldown, i.e. two different inputs wearing one name.

    Cleared on the way out as well as in, because the constants here are shared with
    ``tests/integration/test_request_signature_middleware.py`` and pytest-randomly
    reorders modules.
    """
    from src.core.signing import request_verifier_middleware as mw

    def _clear() -> None:
        mw.AGENT_RESOLUTION_CACHE.clear()
        mw._RESOLUTION_FAILURES.clear()

    _clear()
    try:
        yield
    finally:
        _clear()


def _brand_json(*listed_agent_urls: str) -> dict[str, Any]:
    """A brand.json listing exactly *listed_agent_urls* as buying agents.

    Minimal on purpose: the Tier-3 binding reads ``agents[]`` (top level,
    ``house.agents``, ``brands[].agents``) and ``authorized_operators[]``, and
    nothing else on the document changes the decision. The match is BYTE-EQUAL by
    spec mandate (#3690: "no canonicalization at this step"), so an entry that
    differs only by a trailing slash is a legitimately different agent — which is
    why the not-listed case below passes a deliberately different URL rather than a
    mangled one.
    """
    return {
        # DERIVED from the pin, not the literal "v1" this carried (#1757): production
        # serves .../schemas/3.1.1/brand.json, so a v1 literal here meant the fixture and
        # the deployment DISAGREED ABOUT THE SPEC VERSION ON THE WIRE — invisible to the
        # key-set pin, which only grades that "$schema" is present.
        "$schema": f"https://adcontextprotocol.org/schemas/{get_adcp_spec_version()}/brand.json",
        "name": "Buyer Example",
        "agents": [{"type": "buying", "url": url} for url in listed_agent_urls],
    }


#: Where ``build_registry_resolution`` points the shared counterparty's brand.json —
#: ``f"{key_origin}/.well-known/brand.json"``. Derived from the same constant the
#: resolution is built from rather than written out again, so the seeded Tier-3
#: resolver and the seeded Tier-2 resolution can never drift onto two documents.
COUNTERPARTY_BRAND_JSON_URL = f"{COUNTERPARTY_KEY_ORIGIN}/.well-known/brand.json"

#: A counterparty whose agent host does NOT share an eTLD+1 with the brand its
#: brand.json belongs to — the multi-tenant SaaS operator shape (WPP / GroupM acting
#: for a brand client). Listing it in ``agents[]`` is necessary but NOT sufficient:
#: without an ``house.authorized_operators[]`` entry covering it, the binding fails,
#: which is the ``binding_failed`` reason and the trust-pivot case
#: ``request_signature_brand_origin_mismatch`` names. A different registrable domain
#: (``example.net``, not a subdomain of ``example.com``) is what makes that so.
OPERATOR_AGENT_URL = "https://operator.example.net/a2a"


@contextmanager
def _brand_authorization(brand_json: dict[str, Any]) -> Iterator[None]:
    """Seed the Tier-3 resolver for the shared counterparty and restore on exit.

    Overrides whatever :func:`counterparty_key` already seeded (its own default
    "authorized" resolver) with *brand_json* for the duration of the ``with``
    block — the resolver itself is :func:`brand_authz_resolver`, the SAME
    production-real, mocked-transport builder ``counterparty_key`` uses, so this
    module and the fixture it composes with can never drift onto two different
    resolver shapes.
    """
    from src.core.signing import request_verifier_middleware as mw

    with seeded_cache_entry(
        mw._BRAND_AUTHZ_RESOLVER_CACHE,
        COUNTERPARTY_BRAND_JSON_URL,
        brand_authz_resolver(COUNTERPARTY_BRAND_JSON_URL, brand_json),
    ):
        yield


@pytest.fixture(scope="module")
def counterparty_keypair() -> tuple[Any, dict[str, Any]]:
    """A real Ed25519 request-signing keypair: (private_key, public JWKS)."""
    return keypair_for(COUNTERPARTY_KID)


# --------------------------------------------------------------------------
# 1. The ordering canary
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDiscoveryFailureDefersToTheChecklist:
    """A discovery failure is a STEP 7 answer and must never outrank an earlier step.

    The canary for the design's raise-inside-``__call__`` decision. Both requests
    below come from the SAME counterparty with the SAME unreachable ``agent_url`` and
    differ in exactly one thing — whether the signature headers parse — so the only
    thing that can explain two different codes is WHERE the discovery failure is
    raised.
    """

    def test_a_malformed_signature_outranks_an_unresolvable_counterparty(self, integration_db, counterparty_keypair):
        """Malformed headers -> step 1; a well-formed signature -> the discovery code.

        The walk fails for real, with no network and no patched resolver: the SDK
        resolves and validates the authority synchronously before opening a socket
        (``adcp/signing/ip_pinned_transport.py``), so :data:`UNRESOLVABLE_AGENT_URL`'s
        loopback authority is refused as a reserved range and ``async_resolve_agent``
        raises ``AgentResolverError("capabilities_unreachable")`` in microseconds.

        Both halves are asserted in one test on purpose. The ordering claim alone is
        vacuously TRUE on a tree that emits no discovery code at all — which is
        exactly today's tree — so a canary that asserted only the malformed case
        would go green through the entire implementation and catch nothing. The
        second rung is what makes the first one mean something.
        """
        private_key, _jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=UNRESOLVABLE_AGENT_URL)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)):
                with _cold_discovery_state():
                    malformed = client.get(
                        BODYLESS_ADCP_PATH, headers=request_headers(token, MALFORMED_SIGNATURE_HEADERS)
                    )
                with _cold_discovery_state():
                    well_formed = client.post(BODYLESS_ADCP_PATH, content=body, headers=headers)

        malformed_code = rejection_code(malformed)
        well_formed_code = rejection_code(well_formed)

        assert malformed_code == REQUEST_SIGNATURE_HEADER_MALFORMED, (
            "a malformed Signature-Input must be refused at checklist step 1 even when the "
            "counterparty is unresolvable. The discovery failure is a step-7 answer, so it "
            "must be DEFERRED into key resolution (a JWKS resolver that raises from its own "
            "__call__) rather than raised where _resolution_for is awaited — which is BEFORE "
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
        """The ``or {}`` at ``request_verifier_middleware.py:903``, graded on the wire.

        ``VerifyOptions(expected_key_origins=None)`` does not mean "no declaration" to
        the SDK — it means "the adopter did not thread the map through", and
        ``_maybe_check_key_origin`` responds by emitting a ``UserWarning`` and
        RETURNING. The spec's step-7 key-origin consistency check — the defense
        against the shared-tenancy spoof where an attacker's brand.json lists a
        victim's ``jwks_uri`` while the victim's capabilities advertise a different
        origin — is then silently OFF, and the request is ACCEPTED. The SDK says so
        in the warning itself: "pass an empty dict if the operator advertises no map
        and you want the missing-declaration rejection to fire."

        So the counterparty here resolves fine, its signature verifies on its merits,
        and the ONLY thing wrong is that its capabilities document declared no
        ``identity.key_origins`` map. Correct behavior is a 401
        ``request_signature_key_origin_missing``. Today it is a 200 with a warning in
        a log nobody reads, which is a silent-acceptance bug and the reason this is
        graded on the WIRE rather than on ``VerifyOptions``.

        Built with ``model_copy`` off the resolution the production constructor
        produced, so everything except the one field under test is exactly what
        production builds. (Dropping ``key_origin`` from the registry entry instead
        would not reach this: ``build_registry_resolution`` requires the key and
        raises ``KeyError``.)
        """
        from src.core.signing import request_verifier_middleware as mw

        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=COUNTERPARTY_AGENT_URL)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with _cold_discovery_state(), counterparty_key(jwks):
                mw.AGENT_RESOLUTION_CACHE[COUNTERPARTY_AGENT_URL] = mw.AGENT_RESOLUTION_CACHE[
                    COUNTERPARTY_AGENT_URL
                ].model_copy(update={"key_origins": None})

                with (
                    declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
                    verifier_spy() as calls,
                ):
                    response = client.post(BODYLESS_ADCP_PATH, content=body, headers=headers)

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


# --------------------------------------------------------------------------
# 3. Tier-3 — brand authorization
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestBrandAuthorizationIsEnforced:
    """A cryptographically valid signature is not authority to act FOR A BRAND.

    Tier 1 answers "what key signed this"; Tier 2 answers "whose key is it"; Tier 3
    answers "is that agent authorized to act for this brand". A counterparty that
    holds a real key and signs correctly, but whose brand has not listed it in
    ``agents[]``, must be refused — otherwise any agent with a published JWKS can act
    for any brand whose brand.json it can find.
    """

    def test_an_agent_absent_from_brand_json_is_refused(self, integration_db, counterparty_keypair):
        """The signature verifies; the brand never listed the agent; the request is refused.

        Everything about the signature is legitimate — real Ed25519, real key, real
        resolution — so the ONLY reason this can be refused is the Tier-3 binding.
        The brand.json served lists a DIFFERENT buying agent at the same origin, which
        is the honest shape of the failure: the operator runs agents, just not this
        one. It is not a mangled copy of the counterparty's own URL, because the match
        is byte-equal by spec mandate and a near-miss would grade the SDK's string
        comparison rather than our wiring of it.

        ``agent_not_listed`` is the SDK's finer reason; the spec folds it (with
        ``agent_type_mismatch``) into ``request_signature_agent_not_in_brand_json``,
        and the wire is where that folding must be graded.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=COUNTERPARTY_AGENT_URL)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with (
                _cold_discovery_state(),
                counterparty_key(jwks),
                _brand_authorization(_brand_json(f"{COUNTERPARTY_KEY_ORIGIN}/some-other-buying-agent")),
                declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
            ):
                response = client.post(BODYLESS_ADCP_PATH, content=body, headers=headers)

        assert rejection_code(response) == REQUEST_SIGNATURE_AGENT_NOT_IN_BRAND_JSON, (
            "an agent the brand has not listed in brand.json agents[] must be refused with "
            "request_signature_agent_not_in_brand_json even though its signature verified: a "
            "valid key proves WHO signed, never that the signer may act for the brand. Got "
            f"status {response.status_code} with WWW-Authenticate="
            f"{response.headers.get('WWW-Authenticate')!r}"
        )

    def test_a_listed_agent_bound_to_the_brand_still_verifies(self, integration_db, counterparty_keypair):
        """The control: Tier 3 must refuse the unlisted agent ONLY.

        Without it, a ``_check_brand_authorization`` that refused everything would
        satisfy the assertion above while breaking every legitimate counterparty —
        and nothing else in this module would notice, because every other test here
        is a negative.

        The counterparty is listed AND shares an eTLD+1 with the brand domain
        (``buyer.example.com`` both sides), which is the ``etld1_match`` branch: the
        common first-party case the binding is written around.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=COUNTERPARTY_AGENT_URL)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with (
                _cold_discovery_state(),
                counterparty_key(jwks),
                _brand_authorization(_brand_json(COUNTERPARTY_AGENT_URL)),
                declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
            ):
                response = client.post(BODYLESS_ADCP_PATH, content=body, headers=headers)

        assert rejection_code(response) is None, (
            "a counterparty listed in its brand's agents[] and sharing the brand's eTLD+1 is "
            "authorized; the Tier-3 check must let it through. The verifier rejected with "
            f"{rejection_code(response)!r}"
        )
        assert response.status_code == 200, (
            f"expected the verified, brand-authorized request to reach the route, got "
            f"{response.status_code}: {response.text[:300]}"
        )

    def test_a_listed_agent_that_fails_the_host_binding_is_refused(self, integration_db, counterparty_keypair):
        """Listed in ``agents[]`` is necessary, not sufficient — the trust-pivot case.

        The agent IS listed, so ``agent_not_in_brand_json`` does not apply. It simply
        does not belong to the brand: ``operator.example.net`` shares no eTLD+1 with
        ``buyer.example.com`` and the brand.json declares no
        ``authorized_operators[]`` entry covering it. The SDK's reason is
        ``binding_failed``; the spec code for it is
        ``request_signature_brand_origin_mismatch``.

        This is the arm that separates "the brand listed this URL" from "the brand
        delegated to this operator", and it is the only Tier-3 arm reachable purely by
        moving the agent's HOST — which is why the counterparty is seeded with a
        different ``agent_url`` while its ``key_origin`` (and therefore its
        ``brand_json_url``, and therefore the brand domain) stays put. Tiers 1 and 2
        are untouched: the same key resolves and the same signature verifies.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=OPERATOR_AGENT_URL)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with (
                _cold_discovery_state(),
                counterparty_key(jwks, agent_url=OPERATOR_AGENT_URL),
                _brand_authorization(_brand_json(OPERATOR_AGENT_URL)),
                declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
            ):
                response = client.post(BODYLESS_ADCP_PATH, content=body, headers=headers)

        assert rejection_code(response) == REQUEST_SIGNATURE_BRAND_ORIGIN_MISMATCH, (
            "an agent listed in agents[] but bound to neither the brand's own registrable "
            "domain nor an authorized operator must be refused with "
            "request_signature_brand_origin_mismatch. Being listed is not delegation — "
            "otherwise anyone who gets a URL into a brand.json can act for that brand from "
            f"any host they control. Got status {response.status_code} with WWW-Authenticate="
            f"{response.headers.get('WWW-Authenticate')!r}"
        )


# --------------------------------------------------------------------------
# 4. B4's registry fallback must survive the new Tier-3 check
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestRegistryFallbackIsExemptFromBrandAuthorization:
    """A registry-resolved counterparty has no brand.json walk to authorize against.

    B4's registry exists because the conformance runner sends no bearer, so there is
    no ``Principal.agent_url`` and no walk. Tier 3 asks "did the BRAND list this
    agent", and that question has no answer for a counterparty whose trust came from
    configuration rather than from a brand's own document — running the check anyway
    would refuse every registered counterparty and silently un-ship B4.
    """

    def test_a_registry_resolved_request_verifies_with_the_brand_check_armed(
        self, integration_db, counterparty_keypair
    ):
        """The Tier-3 resolver is armed and would REFUSE; the registry path must not consult it.

        The brand.json seeded here lists nobody, so any Tier-3 check that ran on this
        request would refuse it. The request must still verify — which is what
        distinguishes "Tier 3 is correctly scoped to brand-json-walked counterparties"
        from "Tier 3 happens to be off".

        ``options.agent_url`` is asserted so the 200 cannot be explained by a stray
        ``AGENT_RESOLUTION_CACHE`` entry: it must be the REGISTRY's resolution, named
        by its own agent_url.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=None)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with (
                _cold_discovery_state(),
                _brand_authorization(_brand_json()),
                declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
                signing_config(counterparty_registry={COUNTERPARTY_KID: registry_entry(jwks)}),
                verifier_spy() as calls,
            ):
                response = client.post(BODYLESS_ADCP_PATH, content=body, headers=headers)

        assert rejection_code(response) is None, (
            "a registry-resolved counterparty has no brand.json walk to authorize against, so "
            "the Tier-3 check must not fire on it. Running it anyway refuses every registered "
            f"counterparty and un-ships B4; the verifier rejected with {rejection_code(response)!r}"
        )
        assert response.status_code == 200, (
            f"expected the registry-resolved request to reach the route, got {response.status_code}: "
            f"{response.text[:300]}"
        )
        assert len(calls) == 1, f"the signed POST must reach the SDK verifier exactly once; it ran {len(calls)}x"
        assert calls[0]["options"].agent_url == REGISTRY_AGENT_URL, (
            "the 200 must be explained by the REGISTRY's resolution, not by a leftover cache "
            f"entry; got {calls[0]['options'].agent_url!r}"
        )
