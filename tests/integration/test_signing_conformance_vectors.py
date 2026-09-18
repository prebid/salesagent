"""L2 — the 40 AdCP request-signing conformance vectors, through the REAL app.

#1291 B3 (``salesagent-z6nr.14``), design steps 5-9. The authoritative in-repo
grading of the verifier: everything before this ticket is plumbing, this is the
evidence.

Grading level L2 of three — read this before reading any assertion below
-----------------------------------------------------------------------
* **L1** (``tests/unit/test_signing_conformance_signature_base.py`` +
  ``..._canonicalization.py``) — the signature BASE and the 31 canonicalization
  cases, over the vectors' ORIGINAL untouched URLs, against spec-authored bytes.
* **L2 (here)** — the 15-check ORDERING, the wire envelope and the error codes.
* **L3** — B4 (``salesagent-z6nr.15``), black-box, the vectors' own URL space,
  nothing re-signed, ``positive -> non-4xx`` graded literally.

**At L2 the harness signs a base produced by ``adcp.signing.canonical
.build_signature_base`` — the same canonicalizer ``verify_request_signature`` uses
— so signer and verifier agree BY CONSTRUCTION whatever they compute. That is
ACCEPTED AND DELIBERATE at L2, because canonicalization is already graded at L1 over
untouched URLs against spec-authored bytes. L2 asserts the checklist, not the
canonicalizer.** Do not "fix" the re-signing, and do not report L2 green as
canonicalization conformance.

**B3 is CHECKLIST conformance, not STORYBOARD conformance.** ``signed-requests
.yaml``'s first phase (``capability_discovery``) asserts ``request_signing.supported
== true`` on ``get_adcp_capabilities``. :func:`declared_posture` now writes the REAL
declaration onto ``tenants.capability_declarations`` through the repository and
patches nothing, so "can a tenant STORE a ``request_signing`` block" is answered here
by construction — but whether that block reaches the capabilities WIRE is still not,
and remains D1's (``salesagent-z6nr.20``).

Why the URLs — and, since #1721, the BODIES — are transplanted (D-B3-1, settled)
--------------------------------------------------------------------------------
The vectors address ``/adcp/<operation>``, which names no row in
:data:`src.core.tools.registry.TOOLS`. Verbatim, all 40 would 404 in the router and
"pass" having graded nothing.

Their BODIES are on the same footing now, for a reason that did not exist while the
verifier was a middleware: ``src.core.tools._boundary.serve`` computes
``validated_request(...)`` BEFORE ``invoke_tool(...)``, and ``invoke_tool`` is what
resolves the identity and therefore what reaches the verifier. So a body
``create_media_buy`` refuses is answered ``INVALID_REQUEST`` (400) with the checklist
never run — the same silent non-arrival a verbatim URL produces, and measured as such:
every routed vector reported 400 before :func:`~tests.helpers.signing_vectors
.transplant_body` existed.

Both mechanical rules, what each preserves, and their per-vector consequences live in
:mod:`tests.helpers.signing_vectors`; the plan's completeness and the URL rule's
application are guarded in ``tests/unit/test_signing_conformance_plan.py``, and the
body rule by :func:`test_the_transplant_edits_only_what_the_dispatched_operation_forces`
below — which validates every routed row's wire body against the registry's own DTO, so
a row cannot go back to grading a 400.

Where the verifier runs now, and the five rows that cannot reach it
-------------------------------------------------------------------
The verifier is no longer an ASGI middleware with its own surface prefixes: it is a
call :func:`src.core.resolved_identity._resolve_identity` makes, after
``invoke_tool`` resolved ``TOOLS[tool_name]``. The operation is therefore the registry
key the boundary dispatched on, there is no URL→operation resolver and no fail-closed
``none`` bucket — and a wire URL naming no route 404s in the router without ever
reaching the resolver.

Five rows are on that side of the line (``VectorPlan.route_named is False``):
``positive/006``/``008``/``009``/``010`` (the pathological paths) and the verbatim
``negative/028`` (``POST /mcp``, the JSON-RPC protocol-method namespace, which the
a2a-sdk and FastMCP session machinery answer without calling the boundary at all).
:func:`test_unrouted_rows_never_reach_the_verifier` drives the four positives and
asserts exactly that, so the coverage boundary is a measured fact rather than an
omission. What they would have graded is graded elsewhere: their canonicalization at
L1 by ``test_signing_conformance_signature_base.py`` and
``test_signing_conformance_canonicalization.py``, ``010``'s raw-path ``@target-uri``
derivation by ``test_architecture_signed_target_uri_raw_path.py``, and ``028``'s
namespace rule by ``tests/unit/test_request_signing_namespace_split.py``.
``protocol_methods_required_for`` ENFORCEMENT is ungraded here and is a recorded gap
in :mod:`src.core.signing.verifier`'s own docstring.

Where the composition rule's first branch is decided
-----------------------------------------------------
``negative/001`` and ``negative/027`` are the unsigned rows with no credential the
seller accepts, and the spec's first branch says both earn
``request_signature_required``. ``verify_inbound_signature`` implements that branch
correctly and is graded directly by
``tests/unit/test_request_signature_composition_rule.py`` — but it only answers if it
is REACHED, and this module is the only thing that grades that end to end. It caught
the merge doing exactly the opposite: ``_resolve_identity`` refused an absent credential
(AUTH_MISSING) and a presented-but-unresolvable one (AUTH_INVALID) BEFORE it read the
signature, so both rows reported ``Bearer error="invalid_token"`` and the checklist
never ran. The verifier call is ahead of both refusals now, and the AUTH_INVALID one
still fires on the bearer the verifier could not rescue — see the step-4b comment in
``src/core/resolved_identity.py``. If either row reports a challenge other than
``Signature error="request_signature_required"`` again, that is where the regression is,
not in this driver.

Anti-vacuity, which is this ticket's entire failure mode
---------------------------------------------------------
* A POSITIVE vector is NOT graded by "non-4xx" — that is equally true of a request
  that never reached the resolver, of an exempted request, and of an inert posture.
  It is graded by an OBSERVED ``VerifiedSigner`` carrying the vector's keyid plus a
  ``verified_total{keyid}`` delta of exactly 1. (Literal ``non-4xx`` stays L3's: a
  transplanted positive dispatches ``create_media_buy`` against fixture data it names
  no product row for, so what it earns DOWNSTREAM of the verifier is not this
  module's claim and is not asserted here.)
* A NEGATIVE vector is NOT graded by ``status_code == 401`` — the resolver refusing
  the BEARER first (AUTH_MISSING / AUTH_INVALID) renders through the same
  ``AuthChallengeResponder`` and the same 401. It is graded by the exact
  ``WWW-Authenticate: Signature error="<code>"`` bytes, which only a signature
  refusal carries.
* Every case additionally asserts the RESOLVED OPERATION, and
  :func:`test_every_row_names_the_route_the_plan_claims` pins every row's
  ``route_named``/``expected_operation`` against the LIVE route table, so a future
  registry change cannot silently move a vector to the unrouted side — the
  silent-unverified shape this epic has hit at three separate layers.
* ``negative/016`` asserts submission #1 was ACCEPTED and #2 rejected. Preloading the
  replay cache would make the acceptance unobservable, which is the exact false-green
  the runner kit warns about.

Replay isolation — exact-pair deletes, NEVER a truncate
--------------------------------------------------------
``adcp_replay`` has no tenant dimension by design and every vector shares keyid
``test-ed25519-2026``, so "truncate for the vector's keyid" is a GLOBAL wipe: it
would erase the claims of ``test_request_signature_{middleware,operations,
revocation}.py`` running on other xdist workers, and ``negative/020``'s at-cap rows
would make their signed requests reject with ``request_signature_rate_abuse``.
Measured disjointness is what makes exact-pair deletes safe: none of those three
modules uses ``test-ed25519-2026`` — they mint their own keys.

Spec grounding: AdCP 3.1.1 (``adcp==6.6.0``), ``adcontextprotocol/adcp@v3.1.1``:
``docs/building/by-layer/L1/security.mdx`` §"Verifier checklist
(requests)" (15 checks, in order, short-circuiting) and §"Composition with fallback
authenticators"; ``docs/reference/url-canonicalization.mdx``. Graded by
``dist/compliance/3.1.1/universal/signed-requests.yaml``; ``016``/``017``/``020``
additionally gated on ``dist/compliance/3.1.1/test-kits/signed-requests-runner.yaml``.

Covers: salesagent-z6nr.14 (Core Invariant, L2).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from adcp.signing.canonical import build_signature_base, parse_signature_input_header
from adcp.signing.digest import compute_content_digest_sha256, content_digest_matches

from tests.factories import PrincipalFactory, TenantFactory
from tests.factories.principal import plaintext_token_for
from tests.harness._base import BareIntegrationEnv
from tests.helpers.app_state import preserved_global_app_state
from tests.helpers.asgi_wire import WireResponse, build_scope, send_wire_request
from tests.helpers.signing import (
    COUNTERPARTY_AGENT_URL,
    COUNTERPARTY_KEY_ORIGIN,
    FAILED_METRIC,
    VERIFIED_METRIC,
    VERIFIER_RESULT,
    counterparty_key,
    declared_posture,
    rejection_code,
    samples_with,
    signing_config,
    verifier_spy,
)
from tests.helpers.signing_vectors import (
    TRANSPLANT,
    TRANSPLANT_OPERATION,
    VERIFIED_LABEL,
    Credential,
    HarnessState,
    Outcome,
    VectorPlan,
    load_signing_keys,
    load_signing_vectors,
    recomputes_digest,
    transplant_body,
)

# The four signing integration modules contend for one deployment-wide
# ``adcp_replay`` table. The marker is INERT today — ``tox.ini`` runs
# ``--dist loadfile``, which already keeps a module on one worker — and becomes
# load-bearing the moment the suite moves to ``loadgroup``. What makes the four safe
# TODAY is keyid disjointness plus exact-pair deletes. Any future module adopting a
# ``test-*-2026`` keyid must join this group.
pytestmark = [pytest.mark.requires_db, pytest.mark.xdist_group("adcp_replay")]

#: This module's OWN tenant and principal, deliberately not the shared
#: ``SIGNING_TENANT_ID``/``SIGNING_PRINCIPAL_ID``: the vector corpus claims
#: nonces in the deployment-wide ``adcp_replay`` table, and addressing distinct
#: rows is part of what keeps this run disjoint from the three in-process suites
#: (see the ``xdist_group`` note above). The counterparty it signs AS is the
#: shared one — same ``agent_url``, same key origin, same jwks_uri.
_TENANT_ID = "b3_conformance_tenant"
_AGENT_HOST = "b3-conformance-seller.example.com"
_PRINCIPAL_ID = "b3_conformance_principal"

#: Above the kit's ``min_replay_ttl_seconds: 10`` and ``max_interval_seconds: 5``,
#: and above the vectors' 60s signature window, so ``016``'s two submissions cannot
#: both be accepted because the first entry expired between them.
_REPLAY_TTL_SECONDS = 70.0

#: ``negative/020``'s cap, scoped through the PRODUCTION config knob so it is
#: process-local and cannot reach another xdist worker.
_RATE_ABUSE_CAP = 3

_ED25519_KEYID = "test-ed25519-2026"


def _counter(metric: str, **labels: str) -> float:
    """The total of *metric* across every sample whose labels include *labels*.

    A label SUBSET rather than an exact key: ``request_signature_failed_total`` also
    carries ``keyid`` (always the unresolved placeholder — a failure by definition may
    not have resolved one), so pinning the full label tuple would make the assertion
    fail on a label addition rather than on a behavior change.
    """
    return sum(samples_with(metric, **labels).values())


_VECTORS = load_signing_vectors()
_NEGATIVES = sorted(vid for vid, plan in TRANSPLANT.items() if plan.outcome is Outcome.REJECTED)
_POSITIVES = sorted(vid for vid, plan in TRANSPLANT.items() if plan.outcome is Outcome.ACCEPTED)

#: The rows whose wire URL names no ``TOOLS[...].rest`` binding, so the router 404s them
#: and ``_resolve_identity`` — and therefore the verifier — never runs. NOT dropped:
#: :func:`test_unrouted_rows_never_reach_the_verifier` drives the four positives among
#: them and asserts the non-arrival, and every row's membership here is pinned against
#: the live route table by :func:`test_every_row_names_the_route_the_plan_claims`. See
#: the module docstring for where each one's obligation is graded instead.
_UNROUTED = sorted(vid for vid, plan in TRANSPLANT.items() if not plan.route_named)
_DRIVEN_POSITIVES = [vid for vid in _POSITIVES if TRANSPLANT[vid].route_named]
_DRIVEN_NEGATIVES = [vid for vid in _NEGATIVES if TRANSPLANT[vid].route_named]
#: 016 owns its own two-submission sequence — see :func:`test_replayed_nonce_vector`.
_SINGLE_SHOT_NEGATIVES = [
    vid for vid in _DRIVEN_NEGATIVES if TRANSPLANT[vid].harness_state is not HarnessState.REPLAY_PAIR
]
#: Derived, not spelled: the census below asserts the partition, so the id the
#: two-submission test drives must come from the same table the partition reads.
_REPLAY_PAIR_VECTOR = next(
    vid for vid in _DRIVEN_NEGATIVES if TRANSPLANT[vid].harness_state is HarnessState.REPLAY_PAIR
)


# ---------------------------------------------------------------------------
# Key material: the runner keypairs, used ONLY to re-sign transplanted URLs
# ---------------------------------------------------------------------------


def _b64url(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def _private_key(kid: str) -> Any:
    """Build a private key from ``keys.json``'s ``_private_d_for_test_only``.

    Public spec data by the vector README's own statement. It exists for exactly this
    (re-signing a transplanted URL) and must never reach this agent's own signing
    provider (``settings.signing`` / :mod:`src.core.signing.provider`).
    """
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519

    jwk = next(key for key in load_signing_keys()["keys"] if key["kid"] == kid)
    secret = _b64url(jwk["_private_d_for_test_only"])
    if jwk["kty"] == "OKP":
        return ed25519.Ed25519PrivateKey.from_private_bytes(secret)
    return ec.derive_private_key(int.from_bytes(secret, "big"), ec.SECP256R1())


def _sign_base(kid: str, base: str) -> str:
    """The RFC 9421 ``Signature`` value for *base*, as ``sig1=:<b64>:``."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    key = _private_key(kid)
    message = base.encode()
    if isinstance(key, ec.EllipticCurvePrivateKey):
        der = key.sign(message, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")  # RFC 9421: raw r||s, not DER
    else:
        raw = key.sign(message)
    return f"{VERIFIED_LABEL}=:{base64.b64encode(raw).decode()}:"


# ---------------------------------------------------------------------------
# Wire request construction
# ---------------------------------------------------------------------------


def _signature_params(vector: dict[str, Any]) -> dict[str, Any]:
    """The ``sig1`` label's RFC 8941 params (``keyid``, ``nonce``, ``created``, ...)."""
    header = {key.lower(): value for key, value in vector["request"]["headers"].items()}.get("signature-input")
    if not header:
        return {}
    try:
        return dict(parse_signature_input_header(header)[VERIFIED_LABEL].params)
    except Exception:  # a deliberately malformed header (011, 021, 024) has no params
        return {}


def _wire_headers(vector_id: str, token: str | None) -> list[tuple[str, str]]:
    """The header LIST that goes on the wire, in order, repeats preserved.

    A list rather than a dict because ``negative/021``/``022``/``023`` grade a strict
    pre-parse gate whose threat form is a REPEATED header line, and a dict collapses
    those (last-wins) before anything can check.

    ``Host`` is synthesised from the wire URL (the vectors ship none — a real client
    always sends one, and ``_verify_url`` reads ``@authority`` from it).
    ``x-adcp-tenant`` names the tenant for the IPv6 and IDN authorities that name
    none. NEITHER is a covered component in ANY vector, so neither can disturb a
    signature; the same is true of ``Authorization``, which is why the credential can
    be added without re-signing anything on its account.
    """
    plan = TRANSPLANT[vector_id]
    headers: list[tuple[str, str]] = [
        ("host", urlsplit(plan.wire_url).netloc),
        ("x-adcp-tenant", _TENANT_ID),
    ]
    if token is not None:
        headers.append(("Authorization", f"Bearer {token}"))
    headers.extend((name, value) for name, value in _VECTORS[vector_id]["request"]["headers"].items())
    return headers


def _redigested_headers(vector_id: str, headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Recompute ONLY the ``Content-Digest`` value, for the rows :func:`recomputes_digest` names.

    Runs BEFORE :func:`_resigned_headers`, because ``content-digest`` is a covered
    component wherever it is present and the base has to be built over the value actually
    sent. Every other row's digest — the falsified one, the duplicate-algorithm one, the
    one on a row that keeps its placeholder signature — goes on the wire untouched.
    """
    if not recomputes_digest(vector_id):
        return headers
    digest = compute_content_digest_sha256(transplant_body(vector_id))
    return [(name, digest if name.lower() == "content-digest" else value) for name, value in headers]


def _resigned_headers(vector_id: str, headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Recompute ONLY the ``Signature`` value for the transplanted URL.

    ``Signature-Input`` carries component NAMES and params, never the URL, so it stays
    byte-identical — which is what preserves every negative's mutation (wrong tag,
    missing expires/nonce, unquoted param, duplicate label, bad alg, over-long window)
    and every vector's ``keyid``/``nonce``.
    """
    plan = TRANSPLANT[vector_id]
    if plan.resigned is None:
        return headers
    lowered = {name.lower(): value for name, value in headers}
    parsed = parse_signature_input_header(lowered["signature-input"])
    base = build_signature_base(
        _VECTORS[vector_id]["request"]["method"], plan.wire_url, lowered, parsed[VERIFIED_LABEL]
    )
    return [
        (name, _sign_base(plan.resigned, base) if name.lower() == "signature" else value) for name, value in headers
    ]


def _wire_request(vector_id: str, token: str | None) -> tuple[list[tuple[str, str]], bytes]:
    """The exact headers and body this vector puts on the wire. ONE builder.

    Digest first, then signature: the signature covers the digest wherever the vector
    covers it, so recomputing them the other way round signs a value that is not sent.
    """
    body = transplant_body(vector_id)
    headers = _redigested_headers(vector_id, _wire_headers(vector_id, token))
    return _resigned_headers(vector_id, headers), body


def _send(app: Any, portal: Any, vector_id: str, token: str | None) -> WireResponse:
    headers, body = _wire_request(vector_id, token)
    return send_wire_request(
        app,
        portal,
        method=_VECTORS[vector_id]["request"]["method"],
        url=TRANSPLANT[vector_id].wire_url,
        headers=headers,
        body=body,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def conformance_app() -> Iterator[tuple[Any, Any]]:
    """``src.app.app`` with its lifespan STARTED, plus the portal to drive it on.

    ``TestClient`` is entered ONLY for the lifespan and its event-loop portal —
    never to send: an httpx driver voids eight vectors (see
    :mod:`tests.helpers.asgi_wire`).

    Starting the lifespan for real mutates process-global state on the ``src.app.app``
    SINGLETON that the shutdown hook does not undo — chiefly the route table, which
    ``_install_admin_mounts()`` re-shapes with a catch-all ``Mount("")``. Under
    ``--dist loadfile`` the next file on this worker inherits that shape, which is how
    this fixture silently broke the trust-root suite's discovery of the endpoint paths
    the app serves (``salesagent-66a1``). :func:`preserved_global_app_state` puts the
    globals back; see its module docstring for the measured routing flips.
    """
    from starlette.testclient import TestClient

    from src.app import app

    with preserved_global_app_state(), TestClient(app) as client:
        yield app, client.portal


def _seed_tenant() -> Any:
    """The SELLER, which exists whether or not this vector's caller authenticated.

    Seeded for every vector since #1291 D1, because the posture is now a REAL stored
    declaration on this row rather than a substituted reader — the three vectors whose
    caller carries no principal token (``negative/001``, ``/027``, ``/028``) still need a
    seller to have declared one.

    ``virtual_host`` is DOTTED so ``canonical_agent_url`` derives ``https://``: a
    non-empty bucket obliges an ``identity.brand_json_url`` that the pin fixes to
    ``^https://``, so on a single-label host the declaration would be refused and every
    vector would grade the refusal path instead of the checklist.
    """
    return TenantFactory(tenant_id=_TENANT_ID, subdomain="seller", virtual_host=_AGENT_HOST)


def _seed_principal(tenant: Any) -> str:
    """The counterparty's Principal, carrying the ``agent_url`` key resolution reads.

    ``_resolve_identity`` resolves this row from the Bearer value and hands
    :func:`~src.core.signing.verifier.verify_inbound_signature` the ``Principal``;
    ``principal.agent_url`` is the ONLY admitted source of the key set (security.mdx
    forbids a header or body field naming it). Without it every signed vector
    short-circuits at step 7 with ``request_signature_key_unknown``.

    The token is DERIVED, not read back: the row stores ``sha256(token)`` and no
    plaintext, so ``plaintext_token_for`` is what both the factory and this caller
    compute — the same derivation ``tests/utils/database_helpers.py`` and the harness
    use, never a second one written here.
    """
    PrincipalFactory(tenant=tenant, principal_id=_PRINCIPAL_ID, agent_url=COUNTERPARTY_AGENT_URL)
    return plaintext_token_for(_PRINCIPAL_ID)


def _jwks_for(vector: dict[str, Any]) -> dict[str, Any]:
    """The counterparty JWKS this vector's key resolution must see.

    ``jwks_override`` wins where a vector ships one (``negative/025``'s alg/crv
    mismatch); otherwise the referenced kids are taken from ``keys.json`` with the
    private material stripped — a verifier must never be handed private key bytes.
    """
    if "jwks_override" in vector:
        return vector["jwks_override"]
    wanted = set(vector.get("jwks_ref", []))
    return {
        "keys": [
            {name: value for name, value in key.items() if name != "_private_d_for_test_only"}
            for key in load_signing_keys()["keys"]
            if key["kid"] in wanted
        ]
    }


def _replay_pairs(vector: dict[str, Any]) -> list[tuple[str, str]]:
    params = _signature_params(vector)
    keyid, nonce = params.get("keyid"), params.get("nonce")
    return [(str(keyid), str(nonce))] if keyid and nonce else []


def _forget(env: BareIntegrationEnv, pairs: list[tuple[str, str]]) -> None:
    """Delete EXACTLY these ``(keyid, nonce)`` rows. Never a truncate.

    Through ``ReplayNonceRepository.forget``, the delete this run is what added to the
    repository: it shipped ``claim``/``extend``/``at_or_above_cap``/``reap`` and no way
    to drop a pair. Raw SQL in the fixture is not an option
    (``test_architecture_repository_pattern.py``), so the repository grew the method.
    """
    from src.core.database.repositories.replay_nonce import ReplayNonceRepository

    repository = ReplayNonceRepository(env.get_session())
    for keyid, nonce in pairs:
        repository.forget(keyid, nonce)


def _claim(env: BareIntegrationEnv, pairs: list[tuple[str, str]]) -> None:
    """Claim these exact ``(keyid, nonce)`` rows through the production repository."""
    from src.core.database.repositories.replay_nonce import ReplayNonceRepository

    repository = ReplayNonceRepository(env.get_session())
    for keyid, nonce in pairs:
        assert not repository.claim(keyid, nonce, _REPLAY_TTL_SECONDS), (
            f"cap-filler nonce {nonce!r} was already live for {keyid!r} — the fixture would be "
            "measuring a leftover row rather than the cap it just established"
        )


@contextmanager
def _frozen_verifier_clock(reference_now: float) -> Iterator[None]:
    """Make the verifier read the vector's own ``reference_now`` as the wall clock.

    ``_run_verifier`` passes ``now=time.time()`` (no injection seam), and every vector
    is signed for 2026-04-18T14:00:00Z — the PAST — so without this every one of the
    40 fails step 5 as ``request_signature_window_invalid``.

    MEASURED CORRECTION to the B3 design, which specified ``freezegun``: freezegun
    freezes time only for the thread that entered it, and the boundary runs
    ``_resolve_identity`` — and therefore ``verify_inbound_signature`` — off the loop on
    a worker thread, so the verifier reads REAL time while everything else, including
    the log timestamps, shows the frozen clock. Verified against freezegun 1.5.5 with
    ``ignore=[]`` and with ``threading`` removed from the ignore list: the worker thread
    gets real time in every configuration.

    So the ``time`` MODULE REFERENCE in :mod:`src.core.signing.verifier`'s namespace is
    substituted instead — the module that now owns the checklist call, where the ASGI
    middleware used to. Narrower than freezing the process clock, which is the point:
    the DB's ``func.now()`` expiry math, the session clock and logging all keep real
    time, and the vectors' own bytes (``created``/``expires``/``nonce``) are preserved
    exactly — rewriting them would force a re-sign of vectors that must not be
    re-signed. ``time.time`` is the only attribute that module reads off ``time``, so a
    namespace carrying just that one is the whole substitution.
    """
    import types

    from src.core.signing import verifier as mw

    with patch.object(mw, "time", types.SimpleNamespace(time=lambda: float(reference_now))):
        yield


@contextmanager
def _config_for(plan: VectorPlan) -> Iterator[None]:
    """Establish the case's pre-state through PRODUCTION config seams only.

    ``017``'s revocation and ``020``'s cap are CONFIG on this deployment, not
    white-box injection: ``SigningSettings.revoked_keyids``'s own docstring says "Set to
    ``test-revoked-2026`` on the conformance-grading deployment", and
    ``per_keyid_cap_overrides`` says "test-kit counterparties -> 100".

    Through :func:`tests.helpers.signing.signing_config`, which substitutes the whole
    ``SigningSettings`` the verifier reads per request off ``get_settings().signing``
    and REFUSES a field name the model does not declare — so a renamed knob fails here
    loudly instead of yielding the default settings under an assertion that reads as
    though it had overridden one.
    """
    with signing_config(
        revoked_keyids="test-revoked-2026" if plan.harness_state is HarnessState.REVOKED_KID else "",
        per_keyid_cap_overrides=(
            {_ED25519_KEYID: _RATE_ABUSE_CAP} if plan.harness_state is HarnessState.CAP_OVERRIDE else {}
        ),
        replay_ttl_overrides={_ED25519_KEYID: _REPLAY_TTL_SECONDS},
    ):
        yield


@contextmanager
def _vector_case(vector_id: str) -> Iterator[tuple[BareIntegrationEnv, str | None, list[dict[str, Any]]]]:
    """Everything one vector needs, torn down by exact ``(keyid, nonce)`` delete."""
    plan = TRANSPLANT[vector_id]
    vector = _VECTORS[vector_id]
    pairs = _replay_pairs(vector)

    if plan.harness_state is HarnessState.CAP_OVERRIDE:
        # ``negative/020``: fill the per-keyid replay cache TO the (overridden) cap with
        # CASE-UNIQUE nonces, which the vector explicitly blesses ("populating the cache
        # with N placeholder entries"). The keyid stays ``test-ed25519-2026`` — renaming
        # it would mutate a graded ``Signature-Input`` byte and need the JWKS reseeded
        # under a new kid. These pairs are deleted by exact match afterwards, and the
        # cap itself is a process-local config override, so neither can leak to a
        # sibling module on another worker.
        pairs = pairs + [(_ED25519_KEYID, f"b3-cap-{index:02d}") for index in range(_RATE_ABUSE_CAP)]

    with BareIntegrationEnv(tenant_id=_TENANT_ID, principal_id=_PRINCIPAL_ID) as env:
        tenant = _seed_tenant()
        token = _seed_principal(tenant) if plan.credential is Credential.PRINCIPAL_TOKEN else None
        _forget(env, pairs)
        if plan.harness_state is HarnessState.CAP_OVERRIDE:
            _claim(env, pairs[-_RATE_ABUSE_CAP:])
        try:
            with (
                declared_posture(tenant_id=_TENANT_ID, **vector["verifier_capability"]),
                counterparty_key(_jwks_for(vector)),
                _config_for(plan),
                _frozen_verifier_clock(vector["reference_now"]),
                verifier_spy() as calls,
            ):
                yield env, token, calls
        finally:
            _forget(env, pairs)


def _observed_operation(calls: list[dict[str, Any]], response: WireResponse) -> str:
    """The operation the RESOLVER actually named, off the verifier's own kwargs."""
    assert calls, (
        "the verifier was never invoked, so no operation was resolved — the request did not "
        f"reach the checklist at all (status {response.status_code})"
    )
    # ``verify_request_signature(method=, url=, headers=, body=, options=)`` — the
    # resolved operation travels INSIDE ``VerifyOptions``, not as a top-level kwarg.
    # Reading a non-existent kwarg would return "" for every case and make this
    # assertion vacuous on exactly the rows it exists to protect.
    return str(calls[-1]["options"].operation)


# ---------------------------------------------------------------------------
# Negative vectors: the exact wire code, the counter, the resolved operation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vector_id", _SINGLE_SHOT_NEGATIVES)
def test_negative_vector_is_rejected_with_the_spec_code(vector_id, integration_db, conformance_app) -> None:
    """401 carrying ``WWW-Authenticate: Signature error="<expected_outcome.error_code>"``.

    ``failed_step`` is deliberately NOT asserted — the storyboard calls it
    informational; the CODE is the graded artifact.
    """
    app, portal = conformance_app
    plan = TRANSPLANT[vector_id]

    with _vector_case(vector_id) as (_env, token, calls):
        before = _counter(FAILED_METRIC, code=plan.expected_code, operation=plan.expected_operation)
        response = _send(app, portal, vector_id, token)

        assert response.status_code == 401, (
            f"{vector_id}: expected a 401 signature rejection, got {response.status_code}. body={response.body[:200]!r}"
        )
        assert rejection_code(response) == plan.expected_code, (
            f"{vector_id}: wire challenge is {response.get('WWW-Authenticate')!r}, expected "
            f'Signature error="{plan.expected_code}". The header bytes are the graded artifact — '
            "a bare 401 is satisfied by the resolver refusing the bearer first."
        )
        after = _counter(FAILED_METRIC, code=plan.expected_code, operation=plan.expected_operation)
        assert after - before == 1.0, (
            f"{vector_id}: adcp_request_signature_failed_total"
            f'{{code="{plan.expected_code}",operation="{plan.expected_operation}"}} moved by '
            f"{after - before}, expected exactly 1"
        )
        if calls:
            assert _observed_operation(calls, response) == plan.expected_operation


def _route_name_for(vector_id: str) -> str:
    """The registry key the LIVE route table dispatches this vector's wire request on.

    Matched the way Starlette matches, over the real ``/api/v1`` router, on the scope
    :func:`tests.helpers.asgi_wire.build_scope` builds for the same URL — so the path
    this resolves is byte-for-byte the one the request will carry, percent-encoding and
    dot segments included, rather than a second normalisation written here.

    Every route is added as ``name=<registry key>`` (``src/routes/api_v1.py``), so the
    matched route's name IS ``SignatureSubject.operation``: there is no URL→operation
    table left to disagree with the router. ``""`` means no route matched, which is a
    404 the resolver never sees.
    """
    from starlette.routing import Match

    from src.routes.api_v1 import router

    plan = TRANSPLANT[vector_id]
    scope = build_scope(_VECTORS[vector_id]["request"]["method"], plan.wire_url, [])
    scope["path_params"] = {}
    for route in router.routes:
        match, _child = route.matches(scope)
        if match is Match.FULL:
            return str(route.name)
    return ""


@pytest.mark.parametrize("vector_id", sorted(TRANSPLANT))
def test_every_row_names_the_route_the_plan_claims(vector_id) -> None:
    """The plan's ``route_named`` / ``expected_operation`` match the REAL route table.

    All 40 rows, not just the ``/api/v1`` ones: ``negative/028``'s ``POST /mcp`` is in
    the table's scope precisely BECAUSE it names no registry route — that is what puts
    it on the unrouted side and leaves ``protocol_methods_required_for`` ungraded here.

    The pathological-path rows (``/./``, ``%e2%98%83``, ``%7E%2D%5F%2E``, ``%2F``) name
    no route either. Pinning that against the live route table is what stops a registry
    change from silently moving a row across the line — in either direction. A row
    quietly becoming unrouted stops being verified while still "passing" its expected
    outcome; a row quietly becoming routed starts grading an operation nobody chose.
    """
    plan = TRANSPLANT[vector_id]
    named = _route_name_for(vector_id)

    assert bool(named) == plan.route_named, (
        f"{vector_id}: plan says route_named={plan.route_named} but the live /api/v1 route table "
        f"resolved {named!r} for {urlsplit(plan.wire_url).path!r}"
    )
    assert named == plan.expected_operation, (
        f"{vector_id}: the route table dispatches {named!r}, but the plan grades the metric labels "
        f"and the verifier's own kwargs against {plan.expected_operation!r}"
    )


# ---------------------------------------------------------------------------
# Positive vectors: an OBSERVED VerifiedSigner, not "non-4xx"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vector_id", _DRIVEN_POSITIVES)
def test_positive_vector_is_verified_by_the_real_verifier(vector_id, integration_db, conformance_app) -> None:
    """No ``Signature`` challenge, a ``VerifiedSigner`` with the vector's keyid, +1 counter.

    Strictly stronger than "non-4xx", which a request that never reached the resolver
    satisfies. The literal ``non-4xx`` criterion stays L3/B4's: the transplanted body
    dispatches ``create_media_buy`` for real, against fixture data that names no product
    row, so the status this earns downstream of the verifier is not what this module
    claims and is deliberately not asserted.
    """
    app, portal = conformance_app
    plan = TRANSPLANT[vector_id]
    keyid = str(_signature_params(_VECTORS[vector_id])["keyid"])

    with _vector_case(vector_id) as (_env, token, calls):
        before = _counter(VERIFIED_METRIC, keyid=keyid, operation=plan.expected_operation)
        response = _send(app, portal, vector_id, token)

        assert rejection_code(response) is None, (
            f"{vector_id}: the verifier REJECTED a positive vector with "
            f"{rejection_code(response)!r} (status {response.status_code})"
        )
        assert calls, f"{vector_id}: the verifier never ran — a positive that grades nothing"
        assert _observed_operation(calls, response) == plan.expected_operation

        signer = calls[-1].get(VERIFIER_RESULT)
        assert signer is not None, (
            f"{vector_id}: the verifier ran but RAISED — no VerifiedSigner was returned, so the "
            "signature was not accepted"
        )
        assert signer.key_id == keyid, (
            f"{vector_id}: the verifier returned a VerifiedSigner for {signer.key_id!r}, but the "
            f"vector signed with {keyid!r}"
        )

        after = _counter(VERIFIED_METRIC, keyid=keyid, operation=plan.expected_operation)
        assert after - before == 1.0, (
            f"{vector_id}: adcp_request_signature_verified_total"
            f'{{keyid="{keyid}",operation="{plan.expected_operation}"}} moved by {after - before}, '
            "expected exactly 1 — the counter is the observable that the verifier ACCEPTED, "
            "not merely that it ran"
        )


# ---------------------------------------------------------------------------
# The stateful vector that needs its own sequence
# ---------------------------------------------------------------------------


def test_replayed_nonce_vector(integration_db, conformance_app) -> None:
    """``negative/016``: submission #1 ACCEPTED, submission #2 rejected as replayed.

    The kit's ``black_box_behavior: repeat_request``. 016's request is byte-identical
    to ``positive/001``, so it is SENT TWICE rather than run against a preloaded
    cache. Preloading makes the acceptance unobservable — and a replay TTL shorter
    than the interval would let BOTH be accepted with no rejection observed, which is
    the false-green the kit warns about. Asserting #1 ACCEPTED is what closes it.
    """
    app, portal = conformance_app
    vector_id = _REPLAY_PAIR_VECTOR
    assert vector_id == "negative/016-replayed-nonce", vector_id
    plan = TRANSPLANT[vector_id]
    keyid = str(_signature_params(_VECTORS[vector_id])["keyid"])

    with _vector_case(vector_id) as (_env, token, calls):
        before = _counter(VERIFIED_METRIC, keyid=keyid, operation=plan.expected_operation)

        first = _send(app, portal, vector_id, token)
        assert rejection_code(first) is None, (
            f"submission #1 was rejected with {rejection_code(first)!r}; 016 grades nothing unless "
            "the first submission is ACCEPTED"
        )
        assert _counter(VERIFIED_METRIC, keyid=keyid, operation=plan.expected_operation) - before == 1.0, (
            "submission #1 did not increment verified_total — it was not accepted by the verifier, "
            "so the replay rejection below would prove nothing"
        )

        second = _send(app, portal, vector_id, token)
        assert second.status_code == 401
        assert rejection_code(second) == plan.expected_code, (
            f"submission #2 returned {rejection_code(second)!r}, expected {plan.expected_code!r}"
        )
        assert calls


# ---------------------------------------------------------------------------
# Coverage of the run itself
# ---------------------------------------------------------------------------


def test_all_forty_vectors_are_accounted_for() -> None:
    """12 positive + 28 negative, each driven by exactly one test above. 0 skipped.

    The census is what stops a row from going quiet. Every vector is in exactly one of
    three disjoint groups, and each group names the test that drives it:
    :func:`test_positive_vector_is_verified_by_the_real_verifier`,
    :func:`test_negative_vector_is_rejected_with_the_spec_code` (plus
    :func:`test_replayed_nonce_vector` for ``016``), and
    :func:`test_unrouted_rows_never_reach_the_verifier`. The counts are LITERAL so that
    a row moving between groups has to be moved here too, with a reason.
    """
    assert len(_POSITIVES) == 12, _POSITIVES
    assert len(_NEGATIVES) == 28, _NEGATIVES
    assert set(_POSITIVES) | set(_NEGATIVES) == set(_VECTORS)

    assert _UNROUTED == [
        "negative/028-unsigned-protocol-method-required",
        "positive/006-dot-segment-path",
        "positive/008-percent-encoded-path",
        "positive/009-percent-encoded-unreserved-decoded",
        "positive/010-percent-encoded-slash-preserved",
    ], f"the set of rows that cannot reach the resolver changed: {_UNROUTED}"

    assert len(_DRIVEN_POSITIVES) == 8, _DRIVEN_POSITIVES
    assert len(_SINGLE_SHOT_NEGATIVES) == 26, "016 runs in its own two-submission test"
    driven = set(_DRIVEN_POSITIVES) | set(_SINGLE_SHOT_NEGATIVES) | {_REPLAY_PAIR_VECTOR} | set(_UNROUTED)
    assert driven == set(_VECTORS), f"vectors driven by no test at all: {sorted(set(_VECTORS) - driven)}"


@pytest.mark.parametrize("vector_id", [vid for vid in _UNROUTED if TRANSPLANT[vid].outcome is Outcome.ACCEPTED])
def test_unrouted_rows_never_reach_the_verifier(vector_id, integration_db, conformance_app) -> None:
    """The four pathological-path positives 404 in the router, unverified. MEASURED.

    This is the coverage BOUNDARY, asserted rather than assumed. ``SignatureSubject
    .operation`` is the registry key ``invoke_tool`` dispatched on, so a URL that names
    no route never reaches ``_resolve_identity`` and the checklist never runs on it —
    which means these four URLs' canonicalization cannot be graded end to end here, and
    is graded at L1 instead (module docstring).

    Two assertions, because either alone is satisfied by the wrong thing: "the verifier
    never ran" alone would also hold if the response were a signature rejection produced
    somewhere else, and "no signature challenge" alone would also hold if the request
    HAD been verified and accepted. Together they say the request was never a signature
    decision at all.

    ``negative/028`` is excluded: ``POST /mcp`` is answered by FastMCP's session
    machinery rather than by the router, so its non-arrival is not a 404 and driving it
    raw would grade that machinery instead of this boundary. Its membership in
    :data:`_UNROUTED` is pinned by :func:`test_every_row_names_the_route_the_plan_claims`.
    """
    app, portal = conformance_app

    with _vector_case(vector_id) as (_env, token, calls):
        response = _send(app, portal, vector_id, token)

        assert not calls, (
            f"{vector_id}: the verifier RAN on a URL the route table names no operation for "
            f"(status {response.status_code}) — the plan's route_named=False is stale, and the "
            "row should be driven as a positive instead of recorded as unreachable"
        )
        assert rejection_code(response) is None, (
            f"{vector_id}: got a signature challenge {rejection_code(response)!r} from a request "
            "that never reached the resolver"
        )


def test_unknown_keyid_vector_resolves_the_counterparty_before_failing(integration_db, conformance_app) -> None:
    """``negative/008`` must earn ``request_signature_key_unknown`` at step 7.

    Without this the vector passes for the WRONG reason: with no ``agent_url`` on the
    principal the resolver resolved, ``verify_inbound_signature`` builds an empty
    ``StaticJwksResolver`` and EVERY signed vector short-circuits with the same code.
    The rejection is only graded if the counterparty WAS resolvable and the keyid
    ``not-a-real-kid`` is what failed.
    """
    app, portal = conformance_app
    vector_id = "negative/008-unknown-keyid"

    with _vector_case(vector_id) as (_env, token, calls):
        response = _send(app, portal, vector_id, token)
        assert rejection_code(response) == "request_signature_key_unknown"
        assert calls, "the verifier never ran"
        options = calls[-1]["options"]
        assert options.agent_url == COUNTERPARTY_AGENT_URL, (
            "the counterparty was NOT resolved, so this rejection is on a missing principal, not "
            f"on the keyid: agent_url={options.agent_url!r}"
        )
        assert options.expected_key_origins == {"request_signing": COUNTERPARTY_KEY_ORIGIN}, (
            f"expected_key_origins is {options.expected_key_origins!r} — the step-7 key-origin "
            "check shipped OFF, so the rejection cannot be attributed to the keyid"
        )


def test_the_transplant_edits_only_what_the_dispatched_operation_forces() -> None:
    """``Signature`` and, for ``positive/002`` alone, ``Content-Digest``. Nothing else.

    ``Signature-Input`` and ``Content-Type`` go on the wire exactly as the vector ships
    them for every row, and so does ``Content-Digest`` except where
    :func:`~tests.helpers.signing_vectors.recomputes_digest` names the row — mutating any
    of them would edit what the vector grades (its digest MISMATCH, its forbidden covered
    component, its malformed param).

    The BODY is the one thing #1721 forces off verbatim, and this pins both halves of
    that. A row that names no route keeps its bytes, because it 404s before any DTO sees
    them. A row that names one must send a payload ``create_media_buy`` ACCEPTS: that is
    the entire reason the body is transplanted, and it is asserted against the registry's
    own DTO rather than assumed — a body the DTO refuses is a 400 from
    ``validated_request`` and the checklist never runs, which is the silent-unverified
    shape this module exists to refuse.
    """
    from src.core.tools.registry import TOOLS

    for vector_id, plan in TRANSPLANT.items():
        original = _VECTORS[vector_id]["request"]["headers"]
        headers, body = _wire_request(vector_id, "tok")
        sent = dict(headers)
        for header in ("Signature-Input", "Content-Type"):
            if header in original:
                assert sent[header] == original[header], f"{vector_id}: {header} was mutated by the harness"
        if "Content-Digest" in original and not recomputes_digest(vector_id):
            assert sent["Content-Digest"] == original["Content-Digest"], (
                f"{vector_id}: Content-Digest was recomputed but the vector's own digest is the "
                "graded mutation — a falsified digest must stay falsified"
            )
        if plan.resigned is None and "Signature" in original:
            assert sent["Signature"] == original["Signature"], (
                f"{vector_id}: Signature was recomputed but the plan says verbatim — for 017 and 020 "
                "the placeholder signature IS the step-ordering canary"
            )

        if not plan.route_named:
            assert body == _VECTORS[vector_id]["request"].get("body", "").encode(), (
                f"{vector_id}: names no route, so its body reaches no DTO and must go verbatim"
            )
            continue
        TOOLS[TRANSPLANT_OPERATION].dto.model_validate(json.loads(body))
        if recomputes_digest(vector_id):
            assert content_digest_matches(sent["Content-Digest"], body), (
                f"{vector_id}: the wire Content-Digest does not describe the wire body, so a "
                "positive that must PASS the step-11 digest check would fail it"
            )
