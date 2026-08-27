"""Integration tests pinning salesagent-z6nr.12 (#1291 B1) — the ONE inbound
RFC 9421 request-signature verifier middleware.

These are TDD-red: ``src.core.signing.request_verifier_middleware`` does not
exist yet, so this module fails at collection. That is legitimate red — what
matters is that each test below encodes the exact behavior the REFINED plan
(``bd show salesagent-z6nr.12`` → "## Refinement (atom salesagent-3u4e.21,
post-review)") creates. The refinement AMENDS the original Implementation Plan
and wins wherever the two differ.

What is graded here, and why each one exists
--------------------------------------------

**R-H1 — the composition rule (production-breaking if wrong).**
``required_for`` governs the signature requirement *relative to the caller's
credential path*, not absolutely. Pinned spec (AdCP 3.1.1 →
``docs/building/by-layer/L1/security.mdx`` in
``github.com/adcontextprotocol/adcp`` @ tag ``v3.1.1``):

  :1268  an **unauthenticated** request to a ``required_for`` operation MUST be
         rejected with ``request_signature_required``;
  :1269  an **unsigned but otherwise authenticated** request (valid bearer, no
         ``Signature-Input``) MUST NOT be rejected for the missing signature;
  :1271  a **malformed signature** blocks that fallback regardless;
  :1224  states it as a three-way AND ("…AND the caller presents no other
         credential the verifier accepts");
  :1289  names this exact failure — "a seller enabling ``required_for`` for
         operational monitoring would inadvertently 401 every bearer-authed
         buyer".

Salesagent is bearer-authenticated on every AdCP request, so the naive reading
401s essentially all production traffic the moment D1 populates
``required_for``. Compliance negative vector 001 does NOT catch this: its
request carries only ``Content-Type``, i.e. it is unauthenticated, and is
therefore consistent with BOTH readings. All three branches are pinned below.

**R-H2 / R-M5(b) — the verifier must sit OUTSIDE the body rewriter.**
``RestCompatMiddleware`` (``src/routes/rest_compat_middleware.py:67``) is a
``BaseHTTPMiddleware`` that, for POST ``/api/v1/{products,media-buys,
creatives/sync}``, sets ``request._body = normalized_bytes``; Starlette's
``_CachedRequest.wrapped_receive`` then hands those NORMALIZED bytes to
everything downstream. At the originally-planned placement the verifier would
verify bytes the signer never signed, and the collision lands on
``/api/v1/media-buys`` = ``create_media_buy``, the spend-committing operation
the spec pushes toward ``covers_content_digest: "required"``.
:class:`TestVerifierSitsOutsideBodyRewriter` grades exactly that and nothing
else: it FAILS at the original placement (digest computed over normalized
bytes) and PASSES at the corrected one (CORS → UnifiedAuth → verifier →
RestCompat → a2a → router).

**R-H3 — the ``none`` bucket costs nothing.** Two junk signature headers under
``supported: false`` must not buffer the body, must not run crypto. Asserted
observably (the SDK verifier is never invoked; the downstream handler still
receives the full body), not by reading the middleware source.

**R-L / three-way pre-check.** Both headers absent → composition rule; exactly
one present → ``_precheck_presence`` raises (``adcp/signing/verifier.py:389``);
both present → verify.

**Shadow-mode ladder.** ``supported_for`` / ``warn_for`` / ``required_for``
differ on the WIRE (200 vs 401), not merely in a counter — status AND counter
are asserted. (The refinement retires the research note's "invisible failure
mode" framing for warn precisely because the difference IS on the wire.)

**B4 — the configured counterparty registry (``salesagent-z6nr.15``).** The
second, config-sourced way a keyid resolves to key material, added because the
conformance runner sends no bearer and therefore produces no principal-derived
``agent_url`` to walk. It is a key-trust bypass unless two things hold, and the
last three classes in this module hold them: the registry is consulted ONLY when
that walk has no INPUT — never when the walk merely FAILED, which would let a
counterparty with a briefly unreachable brand.json be silently re-identified from
config — and the configuration is refused at ``SigningConfig`` construction under
every production signal this codebase deploys under. See the block comment above
:data:`_PRODUCTION_SIGNALS` for the spec grounding and the full argument.

Why these tests are not vacuous
-------------------------------

The seam is the tenant DECLARATION, never the middleware's decision function:
:func:`_declared_posture` substitutes what ``posture_for_tenant`` reads and
lets the REAL ``RequestSigningPosture.bucket_for`` precedence
(``required_for > warn_for > supported_for``) run. B1 shipped alone is INERT in
production — ``request_signing`` is still in ``_UNBACKED_BLOCKS``
(``src/core/schemas/capability_declarations.py:61``), so no tenant can declare
a posture through ``from_tenant`` yet. That is intended, and it is why D1
(``salesagent-z6nr.20``) carries the obligation to re-run this same ladder
through the real ``from_tenant`` path once the block is backed.

B1 shipped with ``UnresolvedOperationResolver``, which returned ``("", None)``
by design (plan step 2 — no partial hand-written map in B1), so the operation
these declarations bucketed was the empty string. B2
(``salesagent-z6nr.13``) swapped in the registry-derived resolver, and
:data:`LADDER_OPERATIONS` now carries the real AdCP operation names this ladder
invokes — which is what makes every assertion below non-vacuous.

Covers: salesagent-z6nr.12 (Core Invariant + Refinement R-H1, R-H2, R-H3,
R-L, and the shadow-mode ladder); salesagent-z6nr.15 (Core Invariant —
registry-as-fallback precedence and the production refusal).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from adcp.signing import (
    REQUEST_SIGNATURE_HEADER_MALFORMED,
    REQUEST_SIGNATURE_REQUIRED,
)
from adcp.signing.errors import (
    REQUEST_SIGNATURE_CAPABILITIES_UNREACHABLE,
    REQUEST_SIGNATURE_DIGEST_MISMATCH,
    REQUEST_SIGNATURE_KEY_UNKNOWN,
)
from adcp.signing.jwks import StaticJwksResolver

# The SDK's own parameter-length cap, READ rather than restated: a local literal could
# not fail when the SDK moved the cap, and a fixture that signs a keyid the verifier has
# started accepting stops reaching the step-2 raise it is named for.
from adcp.signing.verifier import _MAX_PARAM_LEN as _SDK_MAX_PARAM_LEN
from anyio.from_thread import start_blocking_portal
from pydantic import ValidationError

from src.core.config import SigningConfig
from tests.harness._base import BareIntegrationEnv
from tests.helpers.asgi_wire import send_wire_messages, truncated_body

# The B1 seams, the counter readers, the shared counterparty/tenant/surface
# constants and the request builders all live in tests/helpers/signing.py
# (salesagent-z6nr.14 step 2, review finding LOW-1): three integration modules and
# the B3 conformance run share them, and a name whose home is a test module gets
# imported ACROSS test modules, which is the duplication class the DRY invariant
# exists to stop. The seams are aliased to the private names this module already
# reads; everything promoted since is read under its own public name.
from tests.helpers.signing import (
    BODYLESS_ADCP_PATH,
    COUNTERPARTY_KID,
    FAILED_METRIC,
    LADDER_OPERATIONS,
    MALFORMED_SIGNATURE_HEADERS,
    REGISTRY_AGENT_URL,
    REGISTRY_JWKS_URI,
    REGISTRY_KEY_ORIGIN,
    REWRITTEN_ADCP_PATH,
    SIGNING_PRINCIPAL_ID,
    SIGNING_TENANT_ID,
    UNRESOLVABLE_AGENT_URL,
    UNSIGNED_METRIC,
    VERIFIED_METRIC,
    VERIFIER_ERROR,
    WIRE_ORIGIN,
    assert_counter_delta,
    bucketed_declaration,
    counterparty_key,
    keypair_for,
    narrowed_none,
    registry_entry,
    request_headers,
    seed_principal,
    signed_headers,
    signed_probe,
    tampered_signing_body,
    unsupported,
)
from tests.helpers.signing import (
    counter_samples as _counter_samples,
)
from tests.helpers.signing import (
    declared_posture as _declared_posture,
)
from tests.helpers.signing import (
    rejection_code as _rejection_code,
)
from tests.helpers.signing import (
    samples_with as _samples_with,
)
from tests.helpers.signing import (
    signing_config as _signing_config,
)
from tests.helpers.signing import (
    verifier_spy as _verifier_spy,
)

# --------------------------------------------------------------------------
# Contract the implement atom (salesagent-3u4e.15) must satisfy
# --------------------------------------------------------------------------
# These names are the red test's half of the TDD contract. The refined plan
# names the module and the class; the two module-level attributes below are
# the seams this file needs and are called out here so the implementer wires
# them deliberately rather than by accident:
#
#   src.core.signing.request_verifier_middleware
#     .RequestSignatureMiddleware      pure-ASGI class (plan step 4)
#     ._is_adcp_surface                the path allowlist (plan step 4b, R-M4).
#                                      Adapts the ASGI scope and delegates: the
#                                      allowlist and its segment-boundary rule
#                                      moved to src.core.signing.operations
#                                      (ADCP_SURFACE_PREFIXES, is_adcp_surface),
#                                      published through the facade, because the
#                                      resolver and the guard read them too
#                                      (S2 fault B, salesagent-n78j0.2)
#     .posture_for_tenant              imported from src.core.signing.posture
#                                      into the middleware namespace, so the
#                                      declaration is substitutable (see below)
#     .verify_request_signature        imported from adcp.signing — R-M3 says
#                                      call the SYNC entry point directly, not
#                                      through a Starlette Request duck type
#     .AGENT_RESOLUTION_CACHE          process-level {agent_url: AgentResolution}
#                                      registry (R-M2: cache the resolution
#                                      WHOLE — jwks + jwks_uri + key_origins —
#                                      so all four VerifyOptions fields can be
#                                      passed and the key-origin check does not
#                                      ship silently OFF)
#
# And for salesagent-z6nr.15 (B4), the names the last three classes require:
#
#   src.core.config.SigningConfig
#     .counterparty_registry           {keyid: entry} where an entry carries
#                                      agent_url, jwks_uri, key_origin and the
#                                      JWK set — the four values
#                                      tests.helpers.signing.registry_entry
#                                      builds and counterparty_key already seeds
#                                      into the cache. Explicit keyids only, the
#                                      same KEY-shape rule the override maps
#                                      enforce (share the helper; do NOT add
#                                      this field to
#                                      validate_overrides_name_explicit_keyids,
#                                      whose value check is numeric)
#     model_validator(mode="after")    refuses counterparty_registry,
#                                      per_keyid_cap_overrides and
#                                      replay_ttl_overrides under ANY of
#                                      _PRODUCTION_SIGNALS below. NOT
#                                      validate_configuration(), which
#                                      src/app.py's ASGI lifespan never calls
#
#   src.core.signing.request_verifier_middleware
#     _resolution_for                  takes the keyid PASSED IN by _handle_signed
#                                      (which has the headers; the resolver keeps
#                                      its one-input/one-cache contract) and
#                                      consults the registry only when agent_url
#                                      is falsy. Registry results are NOT written
#                                      to AGENT_RESOLUTION_CACHE — it is keyed by
#                                      agent_url, and a keyid would share that
#                                      namespace
#     the AgentResolution constructor  ONE shared builder in the signing layer,
#                                      which tests.helpers.signing.counterparty_key
#                                      is then repointed at. Its invariant:
#                                      key_origins stays consistent with jwks_uri,
#                                      so _jwks_resolver marks the resolver
#                                      brand_json and step-7 origin checking stays
#                                      engaged rather than vacuous

# --------------------------------------------------------------------------
# Declaration seam
# --------------------------------------------------------------------------


# ``unsupported()`` and ``narrowed_none()`` — the two halves of the ``none`` bucket —
# were defined here and are now imported from tests/helpers/signing.py
# (salesagent-nx8jp.9). They moved for the reason the import block above gives: a
# realization whose home is a test module cannot be reached by the harness at all
# (``test_architecture_no_cross_test_module_imports``), so
# ``declare_request_signing(bucket="narrowed_none")`` could only have been served by a
# THIRD copy. There were already two.


#: The bucket dimension of the malformed-signature rule (security.mdx :1226/:1271),
#: as FOUR rows rather than three.
#:
#: :1226 scopes the rule by the ABSENCE of a bucket — "Verifiers MUST NOT fall back to
#: bearer-only authentication when a malformed signature is present, **even for
#: operations not in required_for**" — so a test that pins one bucket grades a
#: bucket-independent rule at a single point and reports green for the three points it
#: never visited. Each row below names which point it is:
#:
#: * ``required``          — the point the un-parametrized test already stood on;
#: * ``warn``              — :1273 scopes ``warn_for`` to "signed-but-invalid"
#:   signatures, and a malformed HEADER has its own taxonomy row and its own code
#:   (:1373-1377) distinct from ``request_signature_invalid`` (:1388), so warn does
#:   NOT suppress it;
#: * ``narrowed-none``     — ``supported: true`` and the operation in no list. This is
#:   the population :1226's "even for operations not in required_for" names most
#:   directly;
#: * ``unsupported-none``  — ``supported: false``. NOT covered by the rule: the seller
#:   advertises that it verifies nothing, and the storyboard gates its negatives on
#:   ``supported: true``. Its 200 is the CONTROL, and it must stay 200 forever — a row
#:   that expected 401 here would be a permanent red demanding a conformance break.
_BUCKET_DIMENSION = pytest.mark.parametrize(
    ("declaration", "expected_status", "expected_code"),
    [
        pytest.param(
            bucketed_declaration("required", *LADDER_OPERATIONS),
            401,
            REQUEST_SIGNATURE_HEADER_MALFORMED,
            id="required",
        ),
        pytest.param(
            bucketed_declaration("warn", *LADDER_OPERATIONS),
            401,
            REQUEST_SIGNATURE_HEADER_MALFORMED,
            id="warn",
        ),
        pytest.param(narrowed_none(), 401, REQUEST_SIGNATURE_HEADER_MALFORMED, id="narrowed-none"),
        pytest.param(unsupported(), 200, None, id="unsupported-none"),
    ],
)


# --------------------------------------------------------------------------
# Counterparty key material
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def counterparty_keypair() -> tuple[Any, dict[str, Any]]:
    """A real Ed25519 request-signing keypair: (private_key, public JWKS)."""
    return keypair_for(COUNTERPARTY_KID)


# --------------------------------------------------------------------------
# Request construction
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# R-H1 — the composition rule
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestCompositionWithFallbackAuthenticators:
    """``required_for`` rejects the UNAUTHENTICATED branch only (security.mdx :1268-1271)."""

    def test_unsigned_but_bearer_authenticated_is_not_rejected(self, integration_db):
        """security.mdx :1269 — an unsigned request carrying a valid bearer that
        resolves to an accepted Principal MUST NOT be rejected for the missing
        signature, even when the operation is in ``required_for``.

        This is the branch that would 401 essentially all salesagent production
        traffic under the strict reading (:1289), and the branch compliance
        negative vector 001 cannot grade because its request is unauthenticated.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**bucketed_declaration("required", *LADDER_OPERATIONS)):
                response = client.get(BODYLESS_ADCP_PATH, headers=request_headers(token))

            assert _rejection_code(response) is None, (
                "an unsigned but bearer-authenticated request to a required_for "
                "operation must NOT be rejected for the missing signature "
                f"(security.mdx :1269); the verifier rejected with {_rejection_code(response)!r}"
            )
            assert response.status_code == 200, (
                f"expected the request to pass through to the route, got {response.status_code}: {response.text}"
            )

    def test_unsigned_and_unauthenticated_is_rejected(self, integration_db):
        """security.mdx :1268 + :1264 — a bearer token the verifier does not
        accept is NOT a valid credential, so the caller is unauthenticated and
        a ``required_for`` operation MUST reject with
        ``request_signature_required``.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**bucketed_declaration("required", *LADDER_OPERATIONS)):
                response = client.get(
                    BODYLESS_ADCP_PATH,
                    headers=request_headers("not-a-token-any-principal-holds"),
                )

            assert response.status_code == 401, (
                "an unsigned request presenting no credential the verifier accepts must be "
                f"rejected on a required_for operation (security.mdx :1268), got {response.status_code}"
            )
            assert _rejection_code(response) == REQUEST_SIGNATURE_REQUIRED, (
                "the rejection must carry the spec error code on "
                f'WWW-Authenticate: Signature error="{REQUEST_SIGNATURE_REQUIRED}"; '
                f"got {response.headers.get('WWW-Authenticate')!r}"
            )

    @_BUCKET_DIMENSION
    def test_malformed_signature_blocks_bearer_fallback(
        self, integration_db, declaration, expected_status, expected_code
    ):
        """security.mdx :1271 + :1226 — a present-but-malformed signature signals
        signer intent and MUST NOT downgrade silently to bearer. A valid bearer
        does not rescue it.

        "Regardless" is the whole claim, so it is graded across the whole bucket
        dimension rather than at ``required`` alone — see :data:`_BUCKET_DIMENSION`
        for what each row is and why the ``supported: false`` row answers 200.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**declaration):
                response = client.get(
                    BODYLESS_ADCP_PATH,
                    headers=request_headers(token, MALFORMED_SIGNATURE_HEADERS),
                )

            assert response.status_code == expected_status, (
                "a malformed signature blocks the bearer fallback regardless of the bucket "
                f"(security.mdx :1226/:1271); {declaration!r} must answer {expected_status}, "
                f"got {response.status_code}: {response.text[:300]}"
            )
            assert _rejection_code(response) == expected_code, (
                f"expected {expected_code!r} on the wire for {declaration!r}, "
                f"got {response.headers.get('WWW-Authenticate')!r}"
            )


# --------------------------------------------------------------------------
# The other direction of the same predicate — a NON-step-1 malformation
# --------------------------------------------------------------------------

#: A ``keyid`` one byte past ``adcp/signing/verifier.py``'s ``_MAX_PARAM_LEN`` (256).
_OVERLONG_KEYID = "k" * (_SDK_MAX_PARAM_LEN + 1)


@pytest.mark.requires_db
class TestWarnStillSuppressesANonStepOneMalformation:
    """``request_signature_header_malformed`` is ELEVEN raise sites across FIVE steps,
    and only step 1 is the spec's pre-check.

    :data:`_BUCKET_DIMENSION` grades the direction in which the malformed rule can be
    under-applied — warn swallowing a step-1 header malformation. This class grades the
    direction in which it can be OVER-applied: a predicate widened from
    ``(code, step == 1)`` to the bare code would also route steps 2, 5, 6 and 8 to a
    rejection, and those are checklist failures on a WELL-FORMED header, which
    security.mdx :1273 keeps inside ``warn_for``'s "signed-but-invalid" scope.

    Measured against ``adcp==6.6.0``: step 2 is ``verifier.py`` :245/:251/:407/:414, of
    which :245 is the over-long ``keyid`` this row drives. Everything ahead of it —
    ``Signature-Input`` parse, label lookup, ``Signature`` parse, params-present, tag,
    alg, window, components — passes on its merits, because the request is signed for
    real with the counterparty's key over the real wire bytes.
    """

    def test_an_overlong_keyid_fails_at_step_2_and_still_completes_under_warn(
        self, integration_db, counterparty_keypair
    ):
        """Four assertions, and the first two are what make the last two mean anything.

        "It completed" on its own is equally true of a request that never failed at all,
        and equally true of a step-7 ``key_unknown`` — both answer 200 under warn. So
        the ``(code, step)`` PAIR is witnessed off the exception the SDK actually raised
        (``VERIFIER_ERROR``), not off ``adcp_request_signature_failed_total``, whose
        ``code`` label cannot distinguish step 2 from step 1: they carry the SAME code.

        Then completion is witnessed on the wire AND in the handler: ``200`` plus the
        ``context`` the route echoes back, which only a request that reached the route
        with its body intact can produce.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()
            # A genuinely signed POST whose ONLY defect is the length of its keyid.
            headers, body = signed_probe(
                private_key,
                token,
                key_id=_OVERLONG_KEYID,
                request_id="overlong-keyid-completes-under-warn",
            )

            with (
                _declared_posture(**bucketed_declaration("warn", *LADDER_OPERATIONS)),
                counterparty_key(jwks),
                _verifier_spy() as calls,
            ):
                response = client.post(BODYLESS_ADCP_PATH, content=body, headers=headers)

            assert len(calls) == 1, (
                "the warn bucket must still run the SDK checklist exactly once — warn is "
                f"'call, catch, log, continue', not 'skip'; it ran {len(calls)} time(s)"
            )
            raised = calls[0].get(VERIFIER_ERROR)
            assert (getattr(raised, "code", None), getattr(raised, "step", None)) == (
                REQUEST_SIGNATURE_HEADER_MALFORMED,
                2,
            ), (
                "this request must fail at CHECKLIST STEP 2 (verifier.py:245, keyid exceeds "
                f"{_SDK_MAX_PARAM_LEN} bytes), which is the non-pre-check half of "
                f"{REQUEST_SIGNATURE_HEADER_MALFORMED!r}. The verifier raised {raised!r}. A "
                "different step means this row is no longer grading what its name says, and "
                "the widened-predicate mutation it exists to catch would go unnoticed"
            )
            assert response.status_code == 200, (
                "a step-2 malformation is a checklist failure on a well-formed header, which "
                "security.mdx :1273 leaves inside warn_for's 'signed-but-invalid' scope. "
                f"Got {response.status_code}: {response.text[:300]}. A 401 here is a predicate "
                "widened from (code, step == 1) to the bare code"
            )
            assert response.json()["context"] == json.loads(body)["context"], (
                "'completes' means the DOWNSTREAM HANDLER ran over the request, not merely "
                "that no 401 was sent: the route echoes `context` verbatim, so this is the "
                f"receipt. Got {response.json().get('context')!r}"
            )


# --------------------------------------------------------------------------
# Three-way pre-check (R-L)
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestHeaderPresencePrecheck:
    """Both absent → composition rule; exactly one → malformed; both → verify."""

    @_BUCKET_DIMENSION
    @pytest.mark.parametrize(
        "present",
        ["Signature-Input", "Signature"],
        ids=["only-signature-input", "only-signature"],
    )
    def test_exactly_one_signature_header_is_malformed(
        self, integration_db, present, declaration, expected_status, expected_code
    ):
        """``_precheck_presence`` raises on BOTH one-sided branches
        (``adcp/signing/verifier.py:389``): "Signature and Signature-Input must
        both be present". A valid bearer does not rescue a half-present
        signature — same malformed rule as :1271.

        Same rule, so the same bucket dimension (:data:`_BUCKET_DIMENSION`): the
        half-present shape is a step-1 header malformation exactly like the
        two-junk-headers shape above, and it can no more be suppressed by a bucket
        than that one can. This test previously pinned ``supported`` — a fifth point
        that the parametrization replaces with the four the rule is scoped by.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**declaration):
                response = client.get(
                    BODYLESS_ADCP_PATH,
                    headers=request_headers(token, {present: MALFORMED_SIGNATURE_HEADERS[present]}),
                )

            assert _rejection_code(response) == expected_code, (
                f"a request carrying only {present!r} under {declaration!r} must be answered "
                f"with {expected_code!r}; got status {response.status_code}, "
                f"WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}"
            )
            assert response.status_code == expected_status, (
                f"a request carrying only {present!r} under {declaration!r} must answer "
                f"{expected_status} on the wire; got {response.status_code}: {response.text[:300]}"
            )

    def test_both_headers_present_enters_the_checklist(self, integration_db):
        """Both present → the SDK verifier runs, on a ``supported_for`` operation."""
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)), _verifier_spy() as calls:
                client.get(
                    BODYLESS_ADCP_PATH,
                    headers=request_headers(token, MALFORMED_SIGNATURE_HEADERS),
                )

            assert len(calls) == 1, (
                "a request carrying BOTH signature headers on a supported_for operation "
                f"must enter the SDK verifier checklist exactly once; it was called {len(calls)} times"
            )


# --------------------------------------------------------------------------
# R-H3 — the `none` bucket costs nothing
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestNoneBucketCostsNothing:
    """What the ``none`` bucket costs, stated for BOTH of its halves.

    The class previously claimed one general property — "posture is resolved BEFORE
    buffering; ``none`` passes through untouched" — while holding a single test whose
    fixture is ``supported: false``. That is the epic's own root R4 inside our own
    instruments: a claim about a two-member population, evidenced from one member. The
    two halves do not cost the same thing and must not be described as if they did.

    * ``supported: false`` (:meth:`test_junk_signature_headers_under_unsupported_posture_run_no_crypto`)
      — the seller advertises that it verifies nothing, so nothing is verified: the SDK
      verifier is NEVER CALLED and the request passes through untouched. This half is
      what R-H3 is about, and it is what keeps two junk headers from being an
      unauthenticated CPU+DB amplifier.
    * ``supported: true``, operation in no list
      (:meth:`test_a_narrowed_none_bucket_enters_the_sdk_with_nothing_to_call_out_to`)
      — the seller DOES verify, and the malformed-signature rule (security.mdx :1226)
      binds it "even for operations not in ``required_for``". So this half does enter
      the SDK. What it must not do is pay for a counterparty resolution, a replay store
      or a revocation checker for a verdict the bucket will not act on.

    Stated as one contrast because it is one: the sibling proves the SDK is never
    called; the narrowed row proves it is called with nothing to call out to.
    """

    def test_junk_signature_headers_under_unsupported_posture_run_no_crypto(self, integration_db):
        """R-H3 — two junk headers under ``supported: false`` must not buffer the
        body and must not run crypto: otherwise attaching two headers is an
        unauthenticated CPU+DB amplifier whose result is then DISCARDED, because
        the matrix says ``none`` → 200.

        Asserted observably (the SDK verifier is never invoked, and the
        downstream handler still receives the complete body from an undrained
        receive channel), not by reading the middleware source.
        """
        body = {"context": {"request_id": "none-bucket-passthrough"}}
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**unsupported()), _verifier_spy() as calls:
                response = client.post(
                    BODYLESS_ADCP_PATH,
                    json=body,
                    headers=request_headers(token, MALFORMED_SIGNATURE_HEADERS),
                )

            assert calls == [], (
                "the none bucket must not run crypto — verify_request_signature was "
                f"invoked {len(calls)} time(s) for a request the matrix passes through"
            )
            assert response.status_code == 200, (
                f"the none bucket must pass through untouched, got {response.status_code}: {response.text}"
            )
            assert response.json()["context"] == body["context"], (
                "the downstream handler must still receive the complete request body — "
                "a receive channel drained by the verifier would truncate it; got "
                f"{response.json().get('context')!r}"
            )

    def test_a_narrowed_none_bucket_enters_the_sdk_with_nothing_to_call_out_to(self, integration_db):
        """The OTHER half of ``none``: ``supported: true``, operation in no list.

        Byte-identical request to the sibling above, one declaration change, and the
        opposite call count — the sibling proves the SDK is never called, and this row
        proves it IS called, with nothing to call out to. ``len(calls) == 1`` rather
        than ``calls == []`` is the point: security.mdx :1226 binds the malformed rule
        "even for operations not in ``required_for``", so the checklist has to be
        entered before the bucket can decline to act on its verdict.

        The two ENFORCING assertions are ``replay_store`` and ``revocation_checker``.
        They hold only because this path builds its ``VerifyOptions`` deliberately:
        ``_run_verifier`` constructs ``PostgresReplayStore`` unconditionally and
        ``checker_for`` never returns ``None``, so reinstating the ordinary dependencies
        for the ``none`` bucket reddens both. That is the executable half of the
        coupling between "the none bucket is pre-checked" and "the none bucket stays
        cheap": remove the second and these two fail.

        The remaining two are SHAPE DOCUMENTATION and claim NO mutation-catching power,
        stated so no reader credits them with any. ``_jwks_resolver(None,
        agent_url=None)`` falls through both of its branches and returns a plain empty
        ``StaticJwksResolver``, and ``_resolve_request_context`` makes ``agent_url``
        structurally ``None`` when no resolution was performed — so both survive
        reinstating a real resolver for this bucket. They record the seam's shape; they
        do not guard it.

        KNOWN BOUND (design §J): these assertions pin the seam's SHAPE, not its COST.
        They prove the SDK was handed nothing to call out to. They do NOT prove that no
        I/O occurred — ``_detect_tenant`` opens a session on every AdCP request whatever
        this bucket does. Proving absence of I/O is its own work with its own instrument.
        """
        body = {"context": {"request_id": "narrowed-none-enters-the-sdk"}}
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**narrowed_none()), _verifier_spy() as calls:
                client.post(
                    BODYLESS_ADCP_PATH,
                    json=body,
                    headers=request_headers(token, MALFORMED_SIGNATURE_HEADERS),
                )

            assert len(calls) == 1, (
                "a supported: true seller must run the checklist for a malformed signature "
                "even on an operation it declared in no bucket (security.mdx :1226); the SDK "
                f"verifier ran {len(calls)} time(s). Zero means the none bucket still returns "
                "ahead of the pre-check, i.e. the bearer fallback is still available to a "
                "present-but-broken signature"
            )
            options = calls[0]["options"]
            assert options.replay_store is None, (
                "the none bucket must not pay for a replay store: nothing it decides can be "
                "replayed, because nothing it decides is acted on. _run_verifier builds "
                "PostgresReplayStore unconditionally, so a non-None value here means the "
                f"ordinary dependencies were reinstated for this bucket; got {options.replay_store!r}"
            )
            assert options.revocation_checker is None, (
                "the none bucket must not pay for a revocation checker, whose step-9 hook can "
                "fetch a counterparty's revocation list. checker_for never returns None, so a "
                f"non-None value here means the same reinstatement; got {options.revocation_checker!r}"
            )
            # Shape documentation from here down — see the docstring. These two SURVIVE the
            # mutation the two assertions above catch, and are recorded as such.
            assert type(options.jwks_resolver) is StaticJwksResolver, (
                "shape: the resolver handed to the checklist is a bare StaticJwksResolver, not "
                "a brand_json-declaring one built from a counterparty walk; got "
                f"{type(options.jwks_resolver).__name__}"
            )
            assert options.agent_url is None, (
                f"shape: no counterparty was resolved, so the checklist is told about none; got {options.agent_url!r}"
            )

    def test_the_same_request_under_a_supported_posture_does_run_crypto(self, integration_db):
        """The contrast that makes the cheapness claim non-vacuous: byte-identical
        request, one declaration change, and now the checklist runs and rejects.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)), _verifier_spy() as calls:
                response = client.post(
                    BODYLESS_ADCP_PATH,
                    json={"context": {"request_id": "supported-bucket"}},
                    headers=request_headers(token, MALFORMED_SIGNATURE_HEADERS),
                )

            assert len(calls) == 1, (
                "under supported_for the SDK verifier must run for the same request the "
                f"none bucket skips; it ran {len(calls)} time(s)"
            )
            assert _rejection_code(response) == REQUEST_SIGNATURE_HEADER_MALFORMED, (
                f"expected {REQUEST_SIGNATURE_HEADER_MALFORMED!r} on the wire, "
                f"got status {response.status_code}, WWW-Authenticate="
                f"{response.headers.get('WWW-Authenticate')!r}"
            )


# --------------------------------------------------------------------------
# The narrowed-`none` bucket's METRIC, and its remaining wire exits
# --------------------------------------------------------------------------

#: The AdCP operation ``/api/v1/capabilities`` resolves to, on both verbs
#: (``tests.helpers.signing.LADDER_OPERATIONS`` names it alongside
#: ``create_media_buy``). Spelled out here because these rows read the counter's
#: ``operation`` LABEL, which is the thing being asserted rather than a fixture knob.
_BODYLESS_OPERATION = "get_adcp_capabilities"


@contextmanager
def _assert_ignored_recorded(operation: str, expected: int = 1) -> Iterator[None]:
    """``adcp_request_unsigned_total{operation, reason="ignored"}`` moved by *expected*.

    Read on the LABELLED series rather than on the metric total, because "the series
    kept counting" is the whole claim: a bare total moves for the ``absent`` arm too,
    and an operation label collapsed to ``other`` would leave the total right and the
    signal gone.

    ``expected=0`` is a first-class case here — it is how a row says the pass-through
    arm was NOT the one taken.
    """
    before = sum(_samples_with(UNSIGNED_METRIC, operation=operation, reason="ignored").values())
    yield
    after = sum(_samples_with(UNSIGNED_METRIC, operation=operation, reason="ignored").values())
    assert after == before + expected, (
        f"{UNSIGNED_METRIC}{{operation={operation!r}, reason='ignored'}} must move by "
        f"{expected} across this block; it went {before} -> {after}. This series is what "
        "tells 'the none bucket passed a request through' apart from 'traffic stopped'"
    )


@pytest.mark.requires_db
class TestTheNarrowedNoneBucketRecordsTheRightOutcome:
    """Two failures, two different counters — and the difference is checklist step 1.

    Once the ``none`` bucket is pre-checked it can fail for two structurally different
    reasons, and recording them the same way destroys the signal in both directions:

    * **step 7, ``key_unknown`` — ENGINEERED.** This bucket hands the checklist an empty
      key set BY CONSTRUCTION (design §C: no counterparty resolution, so
      ``StaticJwksResolver({})``), and ``adcp/signing/verifier.py:259`` raises
      ``request_signature_key_unknown`` the moment it is consulted. §C2: there is no
      success exit before that point, so EVERY well-formed narrowed-``none`` signature
      lands here. Counting those as ``signature_failed`` would put a self-inflicted
      failure in the same series as a real key-resolution failure, at the rate of the
      seller's whole un-narrowed traffic — so the failure is suppressed and the
      pass-through is counted as what it is, ``ignored``.
    * **step 1, ``header_malformed`` — GENUINE.** Nothing about this bucket engineered
      it: the caller sent a broken header, and this is precisely the request
      security.mdx :1226 stops from falling back to bearer. It is rejected, and because
      the rejection returns before the pass-through arm, a suppression written as the
      bare "the bucket is not none" would record NOTHING AT ALL for it — a 401 that no
      counter anywhere can see.

    Both limbs of that condition are graded here, one row each. Neither is observable
    from the other: the first says a counter did NOT move and the second says the same
    counter DID, for the same bucket.
    """

    def test_the_engineered_step_7_failure_is_not_counted_as_a_signature_failure(
        self, integration_db, counterparty_keypair
    ):
        """A well-formed, cryptographically real signature under narrowed ``none``.

        The counterparty's key is seeded into the resolution cache, so a ``supported``
        sibling of this exact request verifies (that is what
        :class:`TestVerifierSitsOutsideBodyRewriter` already shows). The failure below
        is therefore attributable to the bucket's empty resolver and to nothing else,
        which is what makes "do not count it" the right rule rather than a lost failure.

        The ``(code, step)`` pair is witnessed off the exception the SDK raised. Without
        it, ``FAILED_METRIC`` not moving is equally true of a middleware that never ran
        the checklist at all — which is exactly what happens today — so the suppression
        would read as graded while nothing had been graded.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with (
                assert_counter_delta(
                    FAILED_METRIC,
                    0,
                    why="the empty resolver is this bucket's own doing; counting its step-7 "
                    "key_unknown would make self-inflicted failures indistinguishable from real "
                    "key-resolution failures in the same series",
                ),
                _assert_ignored_recorded(_BODYLESS_OPERATION),
                _declared_posture(**narrowed_none()),
                counterparty_key(jwks),
                _verifier_spy() as calls,
            ):
                response = client.post(BODYLESS_ADCP_PATH, content=body, headers=headers)

            assert len(calls) == 1, (
                f"the narrowed none bucket must enter the checklist exactly once; it ran {len(calls)} time(s)"
            )
            raised = calls[0].get(VERIFIER_ERROR)
            assert (getattr(raised, "code", None), getattr(raised, "step", None)) == (
                REQUEST_SIGNATURE_KEY_UNKNOWN,
                7,
            ), (
                "this row's whole premise is that the narrowed none bucket fails at STEP 7 "
                "with an empty key set, engineered by the bucket itself. The verifier raised "
                f"{raised!r}. Anything else means the suppression below is being graded "
                "against a failure it was not written for"
            )
            assert response.status_code == 200, (
                "an engineered failure is not a reason to reject: the bucket ignores the "
                f"signature and the request passes through. Got {response.status_code}: {response.text[:300]}"
            )

    def test_a_genuine_step_1_malformation_is_still_counted_as_a_signature_failure(self, integration_db):
        """The ``is_precheck`` limb, and the one a bare "not none" suppression eats.

        Same bucket, same seller, a caller-caused failure instead of an engineered one.
        It is rejected (:1226), and the rejection MUST be counted — the reject arm
        returns before the pass-through arm, so a request that records neither
        ``signature_failed`` nor ``ignored`` 401s with no counter anywhere naming it.

        Both counters are asserted, in opposite directions, because either alone is
        satisfiable by the wrong implementation: ``ignored`` alone by one that never
        rejects, ``failed`` alone by one that counts the rejection AND the pass-through.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            # FAILED_METRIC is the INNERMOST of the two counter guards on purpose: context
            # managers unwind last-entered-first, and this row's primary obligation is the
            # one that should name itself first when it fails.
            with (
                _assert_ignored_recorded(_BODYLESS_OPERATION, expected=0),
                assert_counter_delta(
                    FAILED_METRIC,
                    1,
                    why="a genuine step-1 header malformation under narrowed none is rejected, "
                    "and a rejection nothing counts is a 401 with no operational signal at all",
                ),
                _declared_posture(**narrowed_none()),
            ):
                response = client.post(
                    BODYLESS_ADCP_PATH,
                    json={"context": {"request_id": "narrowed-none-step-1"}},
                    headers=request_headers(token, MALFORMED_SIGNATURE_HEADERS),
                )

            assert _rejection_code(response) == REQUEST_SIGNATURE_HEADER_MALFORMED, (
                f"expected {REQUEST_SIGNATURE_HEADER_MALFORMED!r} on the wire, got status "
                f"{response.status_code} with WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}"
            )
            assert _samples_with(
                FAILED_METRIC, code=REQUEST_SIGNATURE_HEADER_MALFORMED, operation=_BODYLESS_OPERATION
            ), (
                "the counted failure must carry this request's own code and operation, not a "
                f"neighbouring one; samples were {sorted(_counter_samples(FAILED_METRIC))}"
            )


@pytest.mark.requires_db
class TestANarrowedNoneRequestThatNeverFinishesArriving:
    """A client that disconnects mid-body still passes through, and is still counted.

    The fourth of the ``none`` bucket's exits, and the one no client library can reach:
    Starlette's ``TestClient`` builds its receive channel from a complete byte string
    and always answers with the whole body, so ``_buffer_body``'s
    ``not buffered.complete`` branch is unreachable from httpx. Driven here off the raw
    ASGI boundary instead (:func:`tests.helpers.asgi_wire.truncated_body`).

    There is nothing to VERIFY — the bytes a signature would cover never arrived — so
    the middleware hands what did arrive, and then the disconnect, downstream and lets
    the app unwind. What must not happen is that the ``ignored`` series stops moving on
    this exit while it keeps moving on the other pass-through: a metric that disappears
    for one shape of traffic reads as that traffic stopping.
    """

    def test_a_mid_body_disconnect_passes_through_and_is_still_counted_as_ignored(self, integration_db):
        """Raw ASGI: one chunk promising more, then ``http.disconnect``.

        The 400 is the RECEIPT, not an incidental status: it is FastAPI's own
        body-parse verdict on the truncated JSON, which only a request that reached the
        ROUTE can produce. A verifier that answered this request itself would send 401
        (with a ``WWW-Authenticate`` challenge) or 413, and neither can be confused
        with a route-level parse failure.
        """
        from src.app import app

        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            env.get_rest_client()  # installs the auth dependency overrides this app needs
            headers = list(
                request_headers(token, {"Content-Type": "application/json", **MALFORMED_SIGNATURE_HEADERS}).items()
            )

            with (
                _assert_ignored_recorded(_BODYLESS_OPERATION),
                _declared_posture(**narrowed_none()),
                start_blocking_portal("asyncio") as portal,
            ):
                response = send_wire_messages(
                    app,
                    portal,
                    method="POST",
                    url=f"{WIRE_ORIGIN}{BODYLESS_ADCP_PATH}",
                    headers=headers,
                    messages=truncated_body(b'{"context": {"request_id": "narrowed-none-disc'),
                )

            assert _rejection_code(response) is None, (
                "a body that never finished arriving is not a malformed signature: there is "
                "nothing to verify and nothing to reject. The verifier answered "
                f"{response.status_code} with WWW-Authenticate={response.get('WWW-Authenticate')!r}"
            )
            assert response.status_code == 400, (
                "the truncated request must reach the ROUTE, whose own body parse then fails "
                f"on it — that 400 is the pass-through receipt. Got {response.status_code}: "
                f"{response.body[:200]!r}. A 413 would mean the over-cap branch answered "
                "instead, on a body far under the cap"
            )


# --------------------------------------------------------------------------
# Shadow-mode ladder
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestShadowModeLadder:
    """``supported_for`` / ``warn_for`` / ``required_for`` differ on the WIRE.

    ``VerifierCapability`` (``adcp/signing/verifier.py:88``) carries only 4 of
    ``request_signing``'s 8 properties and only 2 of its 6 operation buckets, so
    ``warn_for`` is SILENTLY DROPPED if it is passed to the SDK and expected to
    do something. Warn must therefore be implemented by us: call the verifier,
    catch ``SignatureVerificationError``, emit the metric and CONTINUE. Each
    test below asserts BOTH the status and the counter, so the test fails if
    warn degrades to plain ``supported_for`` (status) and fails if the metric is
    dropped (counter).

    The signature used is WELL-FORMED and cryptographically real — signed with
    the counterparty's actual key, then the body is mutated in flight
    (:func:`~tests.helpers.signing.tampered_signing_body`, this class's own
    realization since promoted and generalized to any body, salesagent-nx8jp.9), so
    the verifier reaches ``request_signature_digest_mismatch`` on its merits rather
    than short-circuiting at the header parse. The harness reaches the same
    realization as ``call_via(..., signed="tampered")``, over the same helper, so a
    scenario and this ladder cannot drift on what "tampered" means.
    """

    @staticmethod
    def _tampered_signed_request(private_key: Any, token: str) -> tuple[dict[str, str], bytes]:
        """Headers signed over one body, plus the DIFFERENT body actually sent.

        The MUTATION is :func:`~tests.helpers.signing.tampered_signing_body`'s; what
        stays here is only this suite's own request document and surface. Sending the
        UNMUTATED bytes (and signing the mutated ones) rather than the reverse keeps
        the sent body a valid AdCP document, so nothing downstream of the verifier can
        reject it for a reason other than the digest.
        """
        sent_body = json.dumps({"context": {"request_id": "as-sent"}}).encode()
        headers = signed_headers(
            private_key,
            token,
            method="POST",
            path=BODYLESS_ADCP_PATH,
            body=tampered_signing_body(sent_body),
            extra={"Content-Type": "application/json"},
        )
        return headers, sent_body

    @pytest.mark.parametrize(
        ("bucket", "expected_status"),
        [("supported", 401), ("warn", 200), ("required", 401)],
    )
    def test_invalid_signature_outcome_differs_per_bucket(
        self, integration_db, counterparty_keypair, bucket, expected_status
    ):
        """A signed-but-invalid request: ``warn_for`` logs and continues (200),
        ``supported_for`` and ``required_for`` reject (401). Every bucket
        increments ``adcp_request_signature_failed_total`` with the spec code.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()
            headers, sent_body = self._tampered_signed_request(private_key, token)

            with (
                assert_counter_delta(
                    FAILED_METRIC,
                    1,
                    why=f"bucket {bucket!r} must count the failure exactly once — it is the promotion "
                    "evidence the shadow-mode ladder runs on",
                ),
                _declared_posture(**bucketed_declaration(bucket, *LADDER_OPERATIONS)),
                counterparty_key(jwks),
            ):
                response = client.post(BODYLESS_ADCP_PATH, content=sent_body, headers=headers)

            assert response.status_code == expected_status, (
                f"bucket {bucket!r} must answer {expected_status} on the wire for a "
                f"signed-but-invalid request, got {response.status_code}: {response.text}"
            )
            assert _samples_with(FAILED_METRIC, code=REQUEST_SIGNATURE_DIGEST_MISMATCH), (
                f"the failure must be labelled with the spec code "
                f"{REQUEST_SIGNATURE_DIGEST_MISMATCH!r}; samples were "
                f"{sorted(_counter_samples(FAILED_METRIC))}"
            )

    def test_warn_does_not_degrade_to_supported(self, integration_db, counterparty_keypair):
        """The two-bucket contrast stated as one assertion: byte-identical
        request, ``warn_for`` vs ``supported_for``, different wire answers. If
        warn degrades to supported both are 401 and this fails.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()
            headers, sent_body = self._tampered_signed_request(private_key, token)

            with counterparty_key(jwks):
                with _declared_posture(**bucketed_declaration("warn", *LADDER_OPERATIONS)):
                    warn_response = client.post(BODYLESS_ADCP_PATH, content=sent_body, headers=headers)
                with _declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)):
                    supported_response = client.post(BODYLESS_ADCP_PATH, content=sent_body, headers=headers)

            assert (warn_response.status_code, supported_response.status_code) == (200, 401), (
                "warn_for and supported_for must differ on the WIRE for the same "
                f"signed-but-invalid request; got warn={warn_response.status_code}, "
                f"supported={supported_response.status_code}"
            )


# --------------------------------------------------------------------------
# R-H2 / R-M5(b) — the verifier sits outside the body rewriter
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestVerifierSitsOutsideBodyRewriter:
    """The verifier must see the WIRE bytes, not ``RestCompatMiddleware``'s rewrite.

    This class grades the middleware ORDER and nothing else. Execution must be
    CORS → UnifiedAuth → verifier → RestCompat → a2a → router (R-H2): the
    verifier stays outside BOTH body rewriters while ``auth_context`` is still
    populated before it. At the ORIGINAL placement (verifier inside RestCompat)
    ``request._body`` has already been replaced with the normalized JSON and
    the signature over ``content-digest`` fails with
    ``request_signature_digest_mismatch`` — on ``create_media_buy``, the
    spend-committing operation.
    """

    @staticmethod
    def _signed_deprecated_field_request(private_key: Any, token: str) -> tuple[dict[str, str], bytes]:
        """A POST whose body uses the DEPRECATED ``account_id`` field name.

        ``normalize_request_params`` translates ``account_id`` → ``account``
        (``src/core/request_compat.py``), so ``translations_applied`` is
        non-empty and ``RestCompatMiddleware`` rewrites the body — which is
        precisely the condition that makes wire bytes and downstream bytes
        differ.
        """
        wire_body = json.dumps(
            {
                "account_id": "acct-deprecated-name",
                "packages": [],
                "start_time": "2026-08-01T00:00:00Z",
                "end_time": "2026-08-31T00:00:00Z",
            }
        ).encode()
        headers = signed_headers(
            private_key,
            token,
            method="POST",
            path=REWRITTEN_ADCP_PATH,
            body=wire_body,
            extra={"Content-Type": "application/json"},
        )
        return headers, wire_body

    def test_signature_over_wire_bytes_verifies_on_a_body_rewritten_route(self, integration_db, counterparty_keypair):
        """R-M5(b) — the whole point of the ordering change, graded directly.

        Three assertions, each with its own diagnosis:
        1. the verifier was handed the WIRE bytes (they still carry the
           deprecated field name);
        2. it did not reject — the real SDK checklist ran over those bytes and
           the ``content-digest`` matched;
        3. the URL it verified against is the as-received one.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()
            headers, wire_body = self._signed_deprecated_field_request(private_key, token)

            with (
                _declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
                counterparty_key(jwks),
                _verifier_spy() as calls,
            ):
                response = client.post(REWRITTEN_ADCP_PATH, content=wire_body, headers=headers)

            assert len(calls) == 1, (
                "the signed POST must reach the SDK verifier exactly once; it was called "
                f"{len(calls)} time(s). Zero calls means the middleware aborted before "
                "verify (check AGENT_RESOLUTION_CACHE seeding, R-M2)"
            )
            assert calls[0]["body"] == wire_body, (
                "the verifier must be handed the WIRE bytes. It received "
                f"{calls[0]['body']!r} instead of {wire_body!r} — that is "
                "RestCompatMiddleware's normalized rewrite, i.e. the verifier is "
                "registered INSIDE the body rewriter (R-H2)"
            )
            assert _rejection_code(response) != REQUEST_SIGNATURE_DIGEST_MISMATCH, (
                "the signature covers content-digest over the wire bytes and must "
                "verify; a digest mismatch means the verifier hashed bytes the signer "
                "never signed (R-H2)"
            )
            assert _rejection_code(response) is None, (
                f"the verifier must not reject this request; it answered {response.status_code} "
                f"with WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}"
            )
            assert calls[0]["url"] == f"http://testserver{REWRITTEN_ADCP_PATH}", (
                "the verify URL must be derived from the as-received Host "
                f"(security.mdx step 10), got {calls[0]['url']!r}"
            )

    def test_verified_signature_increments_the_verified_counter(self, integration_db, counterparty_keypair):
        """Plan step 6 — B1 is the only layer that sees the outcome before it is
        swallowed or turned into a 401, so the success side of the ladder's
        promotion evidence is emitted here too.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()
            headers, wire_body = self._signed_deprecated_field_request(private_key, token)

            with (
                assert_counter_delta(
                    VERIFIED_METRIC, 1, why="a successfully verified signature must be counted exactly once"
                ),
                _declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
                counterparty_key(jwks),
            ):
                client.post(REWRITTEN_ADCP_PATH, content=wire_body, headers=headers)

    def test_rest_compat_still_normalizes_the_deprecated_field(self, integration_db):
        """Companion guard on the re-registration: moving RestCompatMiddleware
        must not disable it. The deprecated ``account_id`` must still be
        translated before the route's Pydantic body model sees it, so no error
        the route emits names ``account_id``.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**unsupported()):
                response = client.post(
                    REWRITTEN_ADCP_PATH,
                    json={
                        "account_id": "acct-deprecated-name",
                        "packages": [],
                        "start_time": "2026-08-01T00:00:00Z",
                        "end_time": "2026-08-31T00:00:00Z",
                    },
                    headers=request_headers(token),
                )

            assert "account_id" not in response.text, (
                "RestCompatMiddleware must still translate account_id -> account before "
                f"the route model validates; the response still names it: {response.text[:400]}"
            )


# --------------------------------------------------------------------------
# B4 — the configured counterparty registry (salesagent-z6nr.15)
# --------------------------------------------------------------------------
#
# TDD-red for salesagent-z6nr.15. None of what these three classes address exists
# yet: ``SigningConfig`` has no ``counterparty_registry`` field, ``_resolution_for``
# has no fallback branch, and no production signal refuses a test-kit configuration.
#
# WHY the registry exists at all: the @adcp/sdk conformance runner sends NO bearer
# (verified across the vendored vector set — only negative/027 carries an
# Authorization header, and that one is deliberately unsigned). Our verifier derives
# the counterparty from the AUTHENTICATED principal, so with no bearer there is no
# ``Principal.agent_url``, no brand.json to walk, and every positive vector reaches
# ``request_signature_key_unknown`` at step 7. AdCP 3.1.1 ``security.mdx`` :1090
# gives the fallback its cover — "Discovery MAY come from prior onboarding, MAY come
# from a registry cache" — and :1236 requires only that the keyid resolve to a
# specific ``agents[]`` entry, which a config-seeded resolution does.
#
# WHY it is dangerous, and what these tests hold in place: a keyid -> counterparty
# map is a key-trust bypass the moment it can either (1) outrank a real
# counterparty's onboarding record, or (2) be configured in production. So the
# invariant has two halves and both are graded here — the registry is a FALLBACK
# consulted solely when the principal-derived walk has no INPUT (never when that
# walk merely FAILED), and the configuration is refused at ``SigningConfig``
# construction under every production signal this codebase deploys under.

#: The signals this codebase already treats as "this is production", each read at a
#: different site: ``ENVIRONMENT`` by ``src.core.config.is_production``, ``PRODUCTION``
#: and ``ENVIRONMENT`` together by ``src.admin.utils.helpers.is_admin_production``, and
#: ``FLY_APP_NAME`` (or ``PRODUCTION``) by ``scripts/run_server.py``. Named here rather
#: than imported from the production predicate on purpose: a test that asks the guard
#: which signals it honors cannot notice a signal the guard forgot.
_PRODUCTION_SIGNALS = {
    "ENVIRONMENT": "production",
    "PRODUCTION": "true",
    "FLY_APP_NAME": "salesagent-prod",
}

#: Every relaxation this ticket's configuration introduces. Each is refused under each
#: signal INDEPENDENTLY — one field slipping past the guard is a full bypass, since the
#: registry alone is enough to make a keyid sufficient for trust.
_TEST_KIT_RELAXATIONS = {
    "counterparty_registry": lambda jwks: {COUNTERPARTY_KID: registry_entry(jwks)},
    "per_keyid_cap_overrides": lambda jwks: {COUNTERPARTY_KID: 100},
    "replay_ttl_overrides": lambda jwks: {COUNTERPARTY_KID: 70.0},
}


@pytest.mark.requires_db
class TestRegistryResolvesACounterpartyWithNoAgentUrl:
    """With no ``agent_url`` to walk, the registry is what makes the keyid resolve."""

    def test_a_registered_keyid_verifies_when_the_principal_has_no_agent_url(
        self, integration_db, counterparty_keypair
    ):
        """The grading case, end to end: a signed request whose principal carries no
        ``agent_url`` (exactly what a bearer-less conformance runner produces once its
        token maps to a principal, and what ``_resolve_request_context`` already logs a
        warning for) must verify against the JWKS configured for its keyid.

        Three assertions, three different failures:
        1. the request is not rejected — the registry produced a usable key at all;
        2. the resolution handed to the verifier is the REGISTRY's, named by its own
           ``agent_url``. Without this the test would also pass on a stray
           ``AGENT_RESOLUTION_CACHE`` entry left by another suite;
        3. ``expected_key_origins`` is populated and the resolver declares
           ``brand_json``. The SDK engages the spec's step-7 key-origin consistency
           check ONLY for a resolver that declares its source, so a registry entry that
           dropped ``key_origin`` would ship the check silently OFF — trusting a key
           served from anywhere — while every other assertion here stayed green.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=None)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with (
                _declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
                _signing_config(counterparty_registry={COUNTERPARTY_KID: registry_entry(jwks)}),
                _verifier_spy() as calls,
            ):
                response = client.post(BODYLESS_ADCP_PATH, content=body, headers=headers)

            assert _rejection_code(response) is None, (
                "a signed request from a REGISTERED keyid must verify even though its "
                "principal carries no agent_url — that is the whole reason the registry "
                f"exists; the verifier rejected with {_rejection_code(response)!r}"
            )
            assert response.status_code == 200, (
                f"expected the verified request to reach the route, got {response.status_code}: {response.text[:300]}"
            )
            assert len(calls) == 1, f"the signed POST must reach the SDK verifier exactly once; it ran {len(calls)}x"
            options = calls[0]["options"]
            assert options.agent_url == REGISTRY_AGENT_URL, (
                "the resolution passed to the verifier must be the one built from the "
                f"registry entry, named by its own agent_url; got {options.agent_url!r}"
            )
            assert options.expected_key_origins == {"request_signing": REGISTRY_KEY_ORIGIN}, (
                "a registry-built resolution must carry key_origins consistent with its "
                "jwks_uri, or step-7 key-origin checking is vacuous for every registered "
                f"counterparty; got {options.expected_key_origins!r}"
            )
            assert getattr(options.jwks_resolver, "jwks_source", None) == "brand_json", (
                "the resolver must declare brand_json, which is what turns the step-7 "
                "origin check ON; a plain StaticJwksResolver is treated as a "
                "publisher-pinned tuple and skips it with a warning"
            )
            assert getattr(options.jwks_resolver, "jwks_uri", None) == REGISTRY_JWKS_URI, (
                f"the declared jwks_uri must be the registered one; got {getattr(options.jwks_resolver, 'jwks_uri', None)!r}"
            )


@pytest.mark.requires_db
class TestRegistryIsAFallbackNeverAnOverride:
    """A principal-derived ``agent_url`` wins, including when its walk FAILS.

    The Core Invariant's load-bearing half. "Consult the registry when the resolution
    is empty" and "consult the registry when there is no agent_url to walk" are the
    same sentence on the happy path and opposite behaviors the moment a real
    counterparty's brand.json is briefly unreachable — at which point the first
    reading silently swaps a real counterparty's onboarded identity for whatever the
    config says about its keyid. A counterparty that could get a keyid into the
    registry could then impersonate any onboarded principal signing under it.
    """

    def test_a_failed_brand_json_walk_does_not_fall_back_to_the_registry(self, integration_db, counterparty_keypair):
        """Same registry entry that verifies the request in the class above; the only
        change is that the principal HAS an ``agent_url`` and its walk fails.

        The walk fails for real, not by substitution: the SDK resolves and validates
        the authority synchronously before opening a socket, so a loopback
        ``agent_url`` raises ``AgentResolverError`` inside the real
        ``_resolution_for``, which returns ``None`` — the identical input the fallback
        branch sees in the passing case. The two tests therefore differ in exactly one
        thing, WHY the resolution is empty, which is precisely the distinction the
        implementation must make.

        Correct behavior is a 401 rejection carrying the counterparty walk's OWN
        discovery-family code (#1291 hksr assigns ``capabilities_unreachable`` its own
        ``request_signature_capabilities_unreachable`` wire code instead of collapsing
        every walk failure onto the generic ``key_unknown``) — an unreachable
        counterparty is a failure to resolve, not a licence to trust a different key.
        """
        private_key, jwks = counterparty_keypair
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env, agent_url=UNRESOLVABLE_AGENT_URL)
            client = env.get_rest_client()
            headers, body = signed_probe(private_key, token)

            with (
                _declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
                _signing_config(counterparty_registry={COUNTERPARTY_KID: registry_entry(jwks)}),
                _verifier_spy() as calls,
            ):
                response = client.post(BODYLESS_ADCP_PATH, content=body, headers=headers)

            assert len(calls) == 1, f"the signed POST must reach the SDK verifier exactly once; it ran {len(calls)}x"
            assert calls[0]["options"].agent_url != REGISTRY_AGENT_URL, (
                "the registry resolved a counterparty whose principal DOES carry an "
                "agent_url. The registry is a fallback for a walk with no INPUT, never "
                "an override for a walk that FAILED — a counterparty with a briefly "
                "unreachable brand.json would otherwise be silently re-identified from "
                "config, which is a key-trust bypass"
            )
            assert calls[0]["options"].agent_url is None, (
                "with the principal's walk failed and the registry correctly not "
                "consulted, the verifier must be handed no resolution at all; got "
                f"{calls[0]['options'].agent_url!r}"
            )
            assert _rejection_code(response) == REQUEST_SIGNATURE_CAPABILITIES_UNREACHABLE, (
                "an unresolvable counterparty must reach step 7 on its merits and be "
                "rejected with the walk failure's own discovery code, not the generic "
                f"key_unknown; got status {response.status_code} with "
                f"WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}"
            )


class TestTestKitConfigurationIsRefusedInProduction:
    """Every test-kit relaxation is refused at ``SigningConfig`` construction.

    Placement is deliberate and has two halves. It is on ``SigningConfig`` rather than
    in ``validate_configuration()`` because that function is reachable only through
    ``initialize_application()`` (``scripts/run_server.py``, ``src/admin/server.py``);
    ``src/app.py``'s ASGI lifespan never calls it, so any deployment pointing gunicorn
    or uvicorn at ``src.app:app`` — the default shape on most platforms — would boot
    the verifier and the registry with the guard never executing. A
    ``model_validator(mode="after")`` fires on every ``AppConfig()`` construction, so
    every process that can reach ``get_config()`` is covered.

    And it lives in this module, though it needs no database, because the thing it
    guards is the fallback resolution path the two classes above grade. Splitting them
    would hide that the refusal is the ONLY thing standing between "a keyid alone is
    sufficient to be trusted as a counterparty" and production.
    """

    @staticmethod
    def _clear_production_signals(monkeypatch: Any) -> None:
        for name in _PRODUCTION_SIGNALS:
            monkeypatch.delenv(name, raising=False)

    @pytest.mark.parametrize("relaxation", sorted(_TEST_KIT_RELAXATIONS))
    @pytest.mark.parametrize("signal", sorted(_PRODUCTION_SIGNALS))
    def test_each_relaxation_is_refused_under_each_production_signal(
        self, monkeypatch, counterparty_keypair, signal, relaxation
    ):
        """One construction, run twice: permitted with no signal set, refused with one.

        Pairing the two halves in a single test is what makes the refusal non-vacuous.
        A bare ``pytest.raises(ValidationError)`` would be satisfied by a config that
        rejects the value for ANY reason — an unknown field, a bad shape — and would
        therefore go green before the guard exists at all. Here the same value is
        proven acceptable microseconds earlier, so the only difference the assertion
        can be reading is the environment.

        Each signal is set alone, with the other two cleared: a guard that ANDs them,
        or that reads only ``ENVIRONMENT`` (``is_production()``'s bug — bypassable by
        the ``PRODUCTION=true`` deployment style), passes a test that sets all three.
        """
        _, jwks = counterparty_keypair
        value = _TEST_KIT_RELAXATIONS[relaxation](jwks)

        self._clear_production_signals(monkeypatch)
        permitted = SigningConfig(**{relaxation: value})
        assert getattr(permitted, relaxation) != {}, (
            f"{relaxation} must be settable outside production — it is how the "
            "conformance-grading deployment is configured at all"
        )

        monkeypatch.setenv(signal, _PRODUCTION_SIGNALS[signal])
        with pytest.raises(ValidationError):
            SigningConfig(**{relaxation: value})

    @pytest.mark.parametrize("signal", sorted(_PRODUCTION_SIGNALS))
    def test_a_production_deployment_without_test_kit_configuration_still_boots(self, monkeypatch, signal):
        """The control on the guard's blast radius: it refuses the RELAXATIONS, not
        production itself. A predicate that refused any signing config under a
        production signal would take every production deployment down, and the
        parametrized test above cannot tell the two apart.
        """
        self._clear_production_signals(monkeypatch)
        monkeypatch.setenv(signal, _PRODUCTION_SIGNALS[signal])

        config = SigningConfig()

        assert config.counterparty_registry == {}, (
            "a production deployment must construct with an EMPTY registry, not refuse "
            f"to construct; got {config.counterparty_registry!r}"
        )
        assert config.per_keyid_cap == 1_000_000, (
            "the spec floor stays the production default; the guard must not disturb it"
        )
