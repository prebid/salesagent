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
== true`` on ``get_adcp_capabilities``; :func:`declared_posture` substitutes
``posture_for_tenant``, so nothing here says whether a tenant can STORE a
``request_signing`` block or whether it reaches the wire. Both obligations are D1's
(``salesagent-z6nr.20``).

Why the URLs are transplanted (D-B3-1, settled)
-----------------------------------------------
``ADCP_SURFACE_PREFIXES`` gates the entire middleware on ``/mcp``, ``/a2a``,
``/api/v1``; the vectors address ``/adcp/<operation>``. Verbatim, all 40 would sail
past unverified and "pass" having graded nothing. The mechanical rule and its
per-vector consequences live in :mod:`tests.helpers.signing_vectors`; the plan's
completeness and the rule's application are guarded in
``tests/unit/test_signing_conformance_plan.py``.

Anti-vacuity, which is this ticket's entire failure mode
---------------------------------------------------------
* A POSITIVE vector is NOT graded by "non-4xx" — that is equally true of a
  middleware that skipped the path, of an exempted request, and of an inert posture.
  It is graded by an OBSERVED ``VerifiedSigner`` carrying the vector's keyid plus a
  ``verified_total{keyid}`` delta of exactly 1. (Literal ``non-4xx`` is L3's: vector
  bodies are not valid ``/api/v1`` models under ``extra="forbid"``, so a transplanted
  positive yields a downstream 422 — a 4xx that is not a signature rejection.)
* A NEGATIVE vector is NOT graded by ``status_code == 401`` — auth middleware
  rejecting first satisfies that. It is graded by the exact
  ``WWW-Authenticate: Signature error="<code>"`` bytes.
* Every case additionally asserts the RESOLVED OPERATION, so a future route-table
  change cannot silently flip a vector into bucket ``none`` — the silent-unverified
  shape this epic has hit at three separate layers.
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
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from adcp.signing.canonical import build_signature_base, parse_signature_input_header

from tests.factories import PrincipalFactory, TenantFactory
from tests.harness._base import BareIntegrationEnv
from tests.helpers.app_state import preserved_global_app_state
from tests.helpers.asgi_wire import WireResponse, send_wire_request
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
    verifier_spy,
)
from tests.helpers.signing_vectors import (
    TRANSPLANT,
    VERIFIED_LABEL,
    Credential,
    HarnessState,
    Outcome,
    VectorPlan,
    load_signing_keys,
    load_signing_vectors,
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
#: 016 owns its own two-submission sequence — see :func:`test_replayed_nonce_vector`.
_SINGLE_SHOT_NEGATIVES = [vid for vid in _NEGATIVES if TRANSPLANT[vid].harness_state is not HarnessState.REPLAY_PAIR]


# ---------------------------------------------------------------------------
# Key material: the runner keypairs, used ONLY to re-sign transplanted URLs
# ---------------------------------------------------------------------------


def _b64url(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def _private_key(kid: str) -> Any:
    """Build a private key from ``keys.json``'s ``_private_d_for_test_only``.

    Public spec data by the vector README's own statement. It exists for exactly this
    (re-signing a transplanted URL) and must never reach ``SigningConfig.provider``.
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


def _send(app: Any, portal: Any, vector_id: str, token: str | None) -> WireResponse:
    plan = TRANSPLANT[vector_id]
    vector = _VECTORS[vector_id]
    headers = _resigned_headers(vector_id, _wire_headers(vector_id, token))
    return send_wire_request(
        app,
        portal,
        method=vector["request"]["method"],
        url=plan.wire_url,
        headers=headers,
        body=vector["request"].get("body", "").encode(),
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


def _seed_principal(tenant: Any, with_agent_url: bool = True) -> str:
    principal = PrincipalFactory(
        tenant=tenant,
        principal_id=_PRINCIPAL_ID,
        agent_url=COUNTERPARTY_AGENT_URL if with_agent_url else None,
    )
    return principal.access_token


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

    ``ReplayNonceRepository.forget`` is a PRODUCTION GAP this run exposes: the
    repository ships ``claim``/``extend``/``at_or_above_cap``/``reap`` and no delete.
    Raw SQL in the fixture is not an option (``test_architecture_repository_pattern
    .py``), so the repository grows the method.
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
    freezes time only for the thread that entered it, and ``_run_verifier`` runs on an
    ``asyncio.to_thread`` worker, so the verifier reads REAL time while everything else
    — including the log timestamps — shows the frozen clock. Verified against freezegun
    1.5.5 with ``ignore=[]`` and with ``threading`` removed from the ignore list: the
    worker thread gets real time in every configuration.

    So the ``time`` MODULE REFERENCE in the middleware's namespace is substituted
    instead. Narrower than freezing the process clock, which is the point: the DB's
    ``func.now()`` expiry math, the session clock and logging all keep real time, and
    the vectors' own bytes (``created``/``expires``/``nonce``) are preserved exactly —
    rewriting them would force a re-sign of vectors that must not be re-signed.
    """
    import types

    from src.core.signing import request_verifier_middleware as mw

    with patch.object(mw, "time", types.SimpleNamespace(time=lambda: float(reference_now))):
        yield


@contextmanager
def _config_for(plan: VectorPlan) -> Iterator[None]:
    """Establish the case's pre-state through PRODUCTION config seams only.

    ``017``'s revocation and ``020``'s cap are CONFIG on this deployment, not
    white-box injection: ``SigningConfig.revoked_keyids``'s own docstring says "Set to
    ``test-revoked-2026`` on the conformance-grading deployment", and
    ``per_keyid_cap_overrides`` says "test-kit counterparties -> 100".
    """
    from src.core.config import get_config

    signing = get_config().signing
    revoked = "test-revoked-2026" if plan.harness_state is HarnessState.REVOKED_KID else ""
    caps = {"test-ed25519-2026": _RATE_ABUSE_CAP} if plan.harness_state is HarnessState.CAP_OVERRIDE else {}
    with (
        patch.object(signing, "revoked_keyids", revoked),
        patch.object(signing, "per_keyid_cap_overrides", caps),
        patch.object(signing, "replay_ttl_overrides", {"test-ed25519-2026": _REPLAY_TTL_SECONDS}),
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
            "a bare 401 is satisfied by auth middleware rejecting first."
        )
        after = _counter(FAILED_METRIC, code=plan.expected_code, operation=plan.expected_operation)
        assert after - before == 1.0, (
            f"{vector_id}: adcp_request_signature_failed_total"
            f'{{code="{plan.expected_code}",operation="{plan.expected_operation}"}} moved by '
            f"{after - before}, expected exactly 1"
        )
        if calls:
            assert _observed_operation(calls, response) == plan.expected_operation


_REST_ROWS = [vid for vid in sorted(TRANSPLANT) if urlsplit(TRANSPLANT[vid].wire_url).path.startswith("/api/v1")]


@pytest.mark.parametrize("vector_id", _REST_ROWS)
def test_rest_row_names_the_route_the_plan_claims(vector_id) -> None:
    """The plan's ``route_named`` / ``expected_operation`` match the REAL route table.

    The pathological-path rows (``/./``, ``%e2%98%83``, ``%7E%2D%5F%2E``, ``%2F``) name
    NO route, so verification happens by ``_fail_closed_bucket`` with ``operation == ""``
    and the ``{operation,keyid}`` counter labels carry an empty operation. Pinning that
    against the live route table is what stops a future route change from silently
    flipping a vector into bucket ``none`` — where nothing is verified and the case
    still looks like the outcome it expected.

    ``negative/028`` is excluded here on purpose: ``POST /mcp`` names the PROTOCOL-METHOD
    namespace (``tasks/cancel``), not the operation namespace, so the REST route table is
    not its resolver.
    """
    from src.core.signing.operations import operation_for_rest_route

    plan = TRANSPLANT[vector_id]
    path = urlsplit(plan.wire_url).path
    named = operation_for_rest_route(_VECTORS[vector_id]["request"]["method"], path)

    assert bool(named) == plan.route_named, (
        f"{vector_id}: plan says route_named={plan.route_named} but operation_for_rest_route "
        f"returned {named!r} for {path!r}"
    )
    assert named == plan.expected_operation


# ---------------------------------------------------------------------------
# Positive vectors: an OBSERVED VerifiedSigner, not "non-4xx"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vector_id", _POSITIVES)
def test_positive_vector_is_verified_by_the_real_verifier(vector_id, integration_db, conformance_app) -> None:
    """No ``Signature`` challenge, a ``VerifiedSigner`` with the vector's keyid, +1 counter.

    Strictly stronger than "non-4xx", which a middleware that skipped the path
    satisfies. The literal ``non-4xx`` criterion is L3/B4's: these bodies are not
    valid ``/api/v1`` request models under ``extra="forbid"``, so a transplanted
    positive yields a downstream 422 that is NOT a signature rejection.
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
    vector_id = "negative/016-replayed-nonce"
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


def test_all_forty_vectors_are_executed() -> None:
    """12 positive + 28 negative, every one of them driven above. 0 skipped."""
    assert len(_POSITIVES) == 12, _POSITIVES
    assert len(_NEGATIVES) == 28, _NEGATIVES
    assert len(_SINGLE_SHOT_NEGATIVES) == 27, "016 runs in its own two-submission test"
    assert set(_POSITIVES) | set(_NEGATIVES) == set(_VECTORS)


def test_unknown_keyid_vector_resolves_the_counterparty_before_failing(integration_db, conformance_app) -> None:
    """``negative/008`` must earn ``request_signature_key_unknown`` at step 7.

    Without this the vector passes for the WRONG reason: with no accepted credential
    the middleware hands the verifier an empty ``StaticJwksResolver``, and EVERY
    signed vector short-circuits with the same code. The rejection is only graded if
    the counterparty WAS resolvable and the keyid ``not-a-real-kid`` is what failed.
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


def test_vector_bodies_are_sent_byte_verbatim() -> None:
    """The transplant recomputes ONLY the ``Signature`` value.

    ``Signature-Input``, ``Content-Digest``, ``Content-Type`` and the body bytes go on
    the wire exactly as the vector ships them — mutating any of them would edit what
    the vector grades (its digest mismatch, its forbidden covered component, its
    malformed param).
    """
    for vector_id, plan in TRANSPLANT.items():
        original = _VECTORS[vector_id]["request"]["headers"]
        sent = dict(_resigned_headers(vector_id, _wire_headers(vector_id, "tok")))
        for header in ("Signature-Input", "Content-Digest", "Content-Type"):
            if header in original:
                assert sent[header] == original[header], f"{vector_id}: {header} was mutated by the harness"
        if plan.resigned is None and "Signature" in original:
            assert sent["Signature"] == original["Signature"], (
                f"{vector_id}: Signature was recomputed but the plan says verbatim — for 017 and 020 "
                "the placeholder signature IS the step-ordering canary"
            )
