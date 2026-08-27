"""Shared helpers for the signing test modules (#1291).

Two groups:

* **A2 key provisioning** (``salesagent-z6nr.8``) — build the tenant-scoped
  repository off the harness session, provision a key through production, and
  resolve a provider through production.
* **The B1 verifier seams** (``salesagent-z6nr.14`` step 2, review finding LOW-1)
  — the DECLARATION substitute, the counterparty-key seed, the verifier spy, the
  wire rejection-code reader and the Prometheus counter readers. These were
  defined inside ``tests/integration/test_request_signature_middleware.py`` and
  imported ACROSS test modules by two siblings (and now a third, the B3
  conformance run) — the duplication class the DRY invariant and
  ``check_code_duplication.py`` exist to stop. One definition, here.

Deliberately thin: these forward to production entry points and add nothing.
Anything that decides or asserts belongs in the test, not here.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal
from unittest.mock import patch

from adcp import get_adcp_spec_version

from tests.helpers.webhook_wire import CapturedWebhook

# An RFC 9421 signature base: the canonicalized component list joined by LF with
# ``"@signature-params"`` last. A SigningProvider signs this as a RAW MESSAGE —
# never as a pre-hashed digest — so passing it verbatim is what the profile means.
SIGNATURE_BASE = (
    b'"@method": POST\n'
    b'"@target-uri": https://seller.example/mcp/\n'
    b'"@signature-params": ("@method" "@target-uri");created=1767225600;'
    b'keyid="adcp-key";alg="ed25519"'
)

REQUEST_SIGNING = "request-signing"

# ---------------------------------------------------------------------------
# The counterparty, the tenant and the surfaces the signing suites share
# (promoted from tests/integration/test_request_signature_middleware.py —
# salesagent-z6nr.14 step 2)
# ---------------------------------------------------------------------------
#
# These were module constants of the B1 middleware suite that the B4 operations,
# A5 revocation and B3 conformance suites then imported ACROSS test modules (or
# re-declared verbatim). Both shapes are the same defect: a name whose home is a
# ``test_*.py`` module has no importable home at all. One definition, here.

#: The counterparty as the Principal row carries it. ``agent_url`` is the ONLY
#: legitimate source for a counterparty's identity (security.mdx forbids taking
#: it from a header, a body field or any other self-assertion), and the
#: middleware keys :data:`AGENT_RESOLUTION_CACHE` on exactly this value.
COUNTERPARTY_AGENT_URL = "https://buyer.example.com/a2a"

#: The origin the counterparty's ``brand.json`` is served from — what
#: ``expected_key_origins`` is checked against at verifier step 7.
COUNTERPARTY_KEY_ORIGIN = "https://buyer.example.com"

#: Where that brand.json points for request-signing keys.
COUNTERPARTY_JWKS_URI = "https://buyer.example.com/.well-known/jwks.json"

#: The ``keyid`` the counterparty signs under.
COUNTERPARTY_KID = "buyer-request-signing-1"

#: The counterparty as a REGISTERED entry of
#: :attr:`~src.core.config.SigningConfig.counterparty_registry` carries it (#1291 B4).
#: Deliberately different from :data:`COUNTERPARTY_AGENT_URL` and its origin: the two
#: resolution paths must be distinguishable at the assertion, so a test can say WHICH
#: one supplied the key rather than only that some key was found.
REGISTRY_AGENT_URL = "https://test-kit.example.com/a2a"
REGISTRY_KEY_ORIGIN = "https://test-kit.example.com"
REGISTRY_JWKS_URI = "https://test-kit.example.com/.well-known/jwks.json"

#: An ``agent_url`` whose brand.json walk FAILS, deterministically and with no network.
#:
#: The SDK resolves and validates the host SYNCHRONOUSLY before any socket is opened
#: (``adcp/signing/ip_pinned_transport.py`` ``resolve_and_validate_host`` via
#: ``build_async_ip_pinned_transport``), so a loopback authority is refused as a
#: reserved range and ``async_resolve_agent`` raises ``AgentResolverError
#: ("capabilities_unreachable")`` in microseconds. That is a REAL failure of the real
#: walk — not a patched resolver — which is what a test grading "the walk failed" needs
#: if it is not to be a mock asserting on itself. A public-looking hostname would
#: instead put a DNS lookup in the test's critical path.
UNRESOLVABLE_AGENT_URL = "http://127.0.0.1:1/a2a"

#: The origin the in-process ASGI client dials. ``httpx``'s ASGI transport defaults to
#: ``http://testserver``, and the signature covers ``@target-uri``, so a request signed
#: against any other origin fails on its merits before the behavior under test is reached.
WIRE_ORIGIN = "http://testserver"


def wire_origin(url: str) -> str:
    """The scheme+authority of *url* — what a signature's ``@target-uri`` covers.

    The PORT is the whole reason this is a function and not a slice: ``_verify_url``
    (``src/core/signing/request_verifier_middleware.py``) rebuilds the authority from
    the ``Host`` header the proxy forwards verbatim, so a caller that signs against a
    portless origin covers a different ``@target-uri`` than the verifier reconstructs
    and is refused as ``request_signature_invalid``. One definition — shared by the
    e2e suite (``tests/e2e/_signing_e2e.origin``) and the harness's e2e_rest dispatch
    — because a second copy that drops the port looks exactly like a verifier bug.
    """
    import httpx

    parsed = httpx.URL(url)
    return f"{parsed.scheme}://{parsed.netloc.decode()}"


#: The seller tenant and the buyer's principal within it. Shared so the three
#: in-process suites address the same rows. An env that SIGNS no longer takes its
#: identity from here — it signs as its OWN tenant/principal
#: (``tests.harness.signing_capability.attach_agent_url``, owner decision D3), and a
#: suite that wants these ids constructs its env with them.
SIGNING_TENANT_ID = "sig_tenant"
SIGNING_PRINCIPAL_ID = "sig_principal"

#: The seller's own host, DOTTED so ``canonical_agent_url`` derives ``https://`` for it.
#:
#: Load-bearing since #1291 D1: a stored ``request_signing`` declaration with any
#: non-empty bucket fires the pinned ``identity.brand_json_url`` ``required_when``, and
#: the pin fixes that pointer to ``^https://``. ``_get_protocol_for_domain``
#: deliberately derives ``http`` for localhost and single-label hosts, so on the default
#: integration host every declaration :func:`declared_posture` writes would be REFUSED —
#: and the suites would grade the refusal path while reading like they graded the
#: declared one. Requests still reach this tenant by the ``x-adcp-tenant`` header
#: (:func:`request_headers`), so the host is identity, not routing.
SIGNING_AGENT_HOST = "seller-signing.example.com"

#: An AdCP surface path with no request body — the cheapest place to grade the
#: header-presence branches. Auth-optional, so any 401 seen on it came from the
#: verifier and not from the route's own auth dependency.
BODYLESS_ADCP_PATH = "/api/v1/capabilities"

#: The body-rewriter collision site (R-H2). ``account_id`` is a DEPRECATED field
#: name: ``normalize_request_params`` translates ``account_id`` → ``account``
#: (``src/core/request_compat.py``), which sets ``translations_applied`` and makes
#: ``RestCompatMiddleware`` rewrite the body.
REWRITTEN_ADCP_PATH = "/api/v1/media-buys"

#: Metric names from B1 plan step 6. ``code`` collapses to ``"other"`` outside the
#: 27 spec strings; ``keyid`` is the real value only after step-7 resolution.
VERIFIED_METRIC = "adcp_request_signature_verified_total"
FAILED_METRIC = "adcp_request_signature_failed_total"

#: The third counter of the same family: ``reason="absent"`` (no signature headers)
#: or ``reason="ignored"`` (headers present, the posture buckets the operation as
#: ``none``, nothing verified). Named here beside its two siblings because the
#: ``ignored`` arm is the one observable that distinguishes "the middleware passed
#: this request through and counted it" from "the middleware stopped counting" —
#: the two look identical on the wire.
UNSIGNED_METRIC = "adcp_request_unsigned_total"

#: The AdCP operations the B1 shadow-mode ladder invokes, and which the A5
#: revocation suite declares alongside it so both bucket the same two names.
#:
#: Two names rather than one, because the ladder runs on two routes:
#: ``/api/v1/capabilities`` (both verbs) is ``get_adcp_capabilities`` and POST
#: ``/api/v1/media-buys`` is ``create_media_buy``. Each test exercises exactly one
#: of them and :func:`bucketed_declaration` puts BOTH in the bucket under test, so
#: every assertion grades the same thing against real operation names rather than
#: the empty string the pre-B2 resolver returned.
LADDER_OPERATIONS = ("get_adcp_capabilities", "create_media_buy")


def signing_key_repo(env: Any, tenant_id: str) -> Any:
    """Commit pending factory data and build a tenant-scoped SigningKeyRepository."""
    from src.core.database.repositories.signing_key import SigningKeyRepository

    return SigningKeyRepository(env.get_session(), tenant_id)


def provision_key(
    repo: Any,
    tenant_id: str,
    kid: str,
    *,
    alg: str = "ed25519",
    purpose: str = REQUEST_SIGNING,
) -> Any:
    """Mint a keypair through production and return the persisted SigningKey row.

    Returns the ROW, not the whole ``ProvisionedKey``: a ``db:`` mint hands back no
    PEM (the ciphertext is on the row), which is the only shape this helper is used
    for.
    """
    from src.core.signing.keys import provision_signing_key

    return provision_signing_key(
        repo,
        tenant_id=tenant_id,
        alg=alg,
        kid=kid,
        purpose=purpose,
    ).row


@contextmanager
def deployment_kek(monkeypatch: Any, name: str = "SALESAGENT_TEST_SIGNING_KEK") -> Iterator[None]:
    """Configure the one deployment-wide KEK for the duration of a test.

    ``db:`` minting REFUSES without it — there is no plaintext fallback — so every
    suite that provisions a key through production needs this. ``key_passphrase``
    is resolved from the environment on every use (deliberately uncached), but the
    ``AppConfig`` carrying ``key_passphrase_env`` is a process global, so the
    cached config is dropped too.
    """
    monkeypatch.setenv("ADCP_SIGNING_KEY_PASSPHRASE_ENV", name)
    monkeypatch.setenv(name, "correct-horse-battery-staple")
    monkeypatch.setattr("src.core.config._config", None, raising=False)
    yield


def get_trust_root_document(client: Any, path: str, tenant: Any, *, expect_status: int = 200) -> dict[str, Any]:
    """GET a trust-root document for *tenant*'s host, failing loudly on the wrong status.

    One home for the Host-scoped fetch so a missing route reports itself as a
    missing route rather than as a ``KeyError`` or a schema violation three
    assertions later — and so the A3 publication suite and the A-provisioning
    suite cannot drift into two fetches. It lives here rather than in either
    module because a module whose job is to BE a test must not also be a helper
    library (``tests/unit/test_architecture_no_cross_test_module_imports.py``).

    *expect_status* exists for the one document whose ABSENCE is a graded
    invariant: the revocation list is unpublishable while the tenant holds no
    active request-signing key, and that 404 is the fail-closed behavior a test
    must assert (``salesagent-z6nr.27`` step 8). It is a parameter rather than a
    second ad hoc fetch beside this one precisely because the whole reason this
    helper exists is that the suites must not drift into two fetches. The body is
    still returned decoded when there is one; a 404 body is ``{"detail": ...}``.
    """
    response = client.get(path, headers={"Host": tenant.virtual_host})
    assert response.status_code == expect_status, (
        f"GET {path} with Host {tenant.virtual_host!r} must return {expect_status}; got "
        f"{response.status_code} {response.text[:200]!r}"
    )
    return response.json()


def published_kids(entries: list[dict[str, Any]]) -> set[str]:
    """The ``kid`` set of a published JWK/signing-key list."""
    return {entry["kid"] for entry in entries}


def b64url_json(segment: str) -> dict[str, Any]:
    """Decode one base64url JWS segment as JSON.

    Uses the SDK's own ``b64url_decode`` (padding-tolerant) rather than a second
    repadding implementation — the same primitive production's
    ``sign_revocation_list`` uses on the encode side. Promoted from
    ``tests/integration/test_revocation_list_publication.py`` (#1291 mp53.6) so
    the e2e suite reads the same served JWS through the ONE decoder rather than a
    second hand-rolled one.
    """
    from adcp.signing.crypto import b64url_decode

    decoded = json.loads(b64url_decode(segment))
    assert isinstance(decoded, dict), f"JWS segment must decode to a JSON object; got {type(decoded).__name__}"
    return decoded


def jws_parts(document: dict[str, Any]) -> tuple[str, str, bytes]:
    """Assert the general-JSON JWS envelope and return (protected, payload, signature).

    Production emits GENERAL JSON serialization, which is what
    ``adcp.signing.jws.parse_general_json_jws`` — and therefore
    ``CachingRevocationChecker`` — reads. The envelope is checked here by
    EQUALITY on its member sets so a compact-JWS string, a multi-signature
    document, or an extra top-level member is a named failure rather than a
    ``KeyError`` further down. Promoted alongside :func:`b64url_json` (#1291
    mp53.6) — one envelope check, not one per suite.
    """
    from adcp.signing.crypto import b64url_decode

    assert set(document) == {"payload", "signatures"}, (
        "the served revocation list must be a JWS general JSON serialization with exactly "
        f"'payload' and 'signatures'; got {sorted(document)}"
    )
    signatures = document["signatures"]
    assert isinstance(signatures, list) and len(signatures) == 1, (
        "this profile is signed by ONE operator key, so signatures[] must hold exactly one "
        f"entry (parse_general_json_jws rejects more); got {signatures!r}"
    )
    entry = signatures[0]
    assert set(entry) == {"protected", "signature"}, (
        f"the signature entry carries exactly 'protected' and 'signature'; got {sorted(entry)}"
    )
    signature = b64url_decode(entry["signature"])
    return entry["protected"], document["payload"], signature


def resolve_provider(
    repo: Any,
    tenant_id: str,
    *,
    now: datetime,
    kid: str | None = None,
    purpose: str = REQUEST_SIGNING,
) -> Any:
    """Resolve a SigningProvider through production (row -> ref -> PEM -> tripwire)."""
    from src.core.signing.provider import _resolve_signing_provider

    return _resolve_signing_provider(repo, tenant_id=tenant_id, purpose=purpose, now=now, kid=kid)


# ---------------------------------------------------------------------------
# B1 verifier seams (promoted from tests/integration/test_request_signature_middleware.py)
# ---------------------------------------------------------------------------


@contextmanager
def declared_posture(*, tenant_id: str = SIGNING_TENANT_ID, **declaration: Any) -> Iterator[None]:
    """Store *declaration* as *tenant_id*'s REAL ``request_signing`` declaration.

    Takes the schema's own property names (``supported``,
    ``covers_content_digest``, ``required_for``, ``warn_for``, ``supported_for``,
    ``protocol_methods_*``) and writes them onto ``tenants.capability_declarations``
    through the repository, exactly as an operator would. Production then does all
    the rest for real: ``CapabilityDeclarations.from_tenant`` parses and
    relation-checks the document, ``posture_for_tenant`` reads it,
    ``bucket_for`` applies precedence, and ``to_verifier_capability`` projects it.

    #1291 D1 is what made this possible: ``request_signing`` left
    ``_UNBACKED_BLOCKS``, so ``request_signing_is_declarable()`` is True on its own
    and there is a DB path for the declaration to travel. Until then this helper
    substituted BOTH ``posture_for_tenant`` and that predicate, and B3's green
    therefore said only "GIVEN a posture, the checklist behaves" — nothing about
    where the posture came from, which is B3 design step 12's obligation 1 and is
    what this rewrite discharges. There is no ``patch`` left in it.

    ``identity.brand_json_url`` is written alongside the posture and is DERIVED from
    ``src.core.agent_identity``, never a literal: any non-empty bucket fires the
    pinned ``required_when`` trigger, and the capabilities read path additionally
    cross-checks a declared pointer against the served one. That is why
    :func:`tests.harness.signing_capability.attach_agent_url` gives the tenant a
    DOTTED ``virtual_host`` — on ``localhost`` the derived pointer is ``http://``
    and the pin's ``^https://`` would (correctly) refuse the whole declaration.

    Restores the previous stored value on exit, so a suite that declares different
    postures across tests does not inherit the last one.
    """
    previous = _declare(tenant_id, declaration)
    try:
        yield
    finally:
        _restore_declarations(tenant_id, previous)


def posture_declaration_document(tenant: Any, declaration: dict[str, Any]) -> dict[str, Any]:
    """The whole ``capability_declarations`` document a declared posture needs.

    ``identity.brand_json_url`` travels WITH the posture and is DERIVED from
    ``src.core.agent_identity``, never a literal: any non-empty bucket fires the
    pinned ``required_when`` trigger, and the capabilities read path cross-checks a
    declared pointer against the served one, so a posture written without it is
    refused whole — and the suite then grades the refusal path while reading like it
    graded the declared one.

    Stated here rather than inside :func:`_declare` because the e2e path cannot use
    that writer: a ``TenantConfigUoW`` write from the runner opens its own engine
    against the runner's ``DATABASE_URL`` and is empirically NOT visible to the live
    server's read (measured in ``test_request_signature_required_e2e``), so an e2e
    caller writes the same document through its live-DB session. One document shape,
    two writers — never two shapes.
    """
    from src.core.agent_identity import brand_json_url

    return {
        "request_signing": declaration,
        "identity": {"brand_json_url": brand_json_url(tenant)},
    }


def _declare(tenant_id: str, declaration: dict[str, Any]) -> dict[str, Any] | None:
    """Write the posture + derived identity onto the tenant; return what was there."""
    from src.core.database.repositories.uow import TenantConfigUoW

    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        tenant = uow.tenant_config.get_tenant()
        assert tenant is not None, (
            f"declared_posture needs tenant {tenant_id!r} to exist before a declaration can be stored "
            "on it — seed it (env.setup_default_data() / TenantFactory) first"
        )
        previous = tenant.capability_declarations
        tenant.capability_declarations = posture_declaration_document(tenant, declaration)
        return previous


def _restore_declarations(tenant_id: str, previous: dict[str, Any] | None) -> None:
    """Put the tenant's declaration document back the way the test found it."""
    from src.core.database.repositories.uow import TenantConfigUoW

    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        tenant = uow.tenant_config.get_tenant()
        if tenant is not None:
            tenant.capability_declarations = previous


class _AlwaysAuthorizedBrandResolver:
    """A Tier-3 double that authorizes any agent unconditionally (#1291 hksr).

    For suites that repoint a counterparty's ``brand_json_url`` at a host chosen
    for an UNRELATED reason — e.g. A5 revocation-issuer testing, which reuses the
    field to steer where the revocation list is fetched from, sometimes to a
    deliberately SSRF-blocked or unresolvable host that is not a real registrable
    domain. The real :func:`brand_authz_resolver` would refuse those via the SDK's
    own ``registrable_domain`` validation regardless of mocking the fetch, which
    is not what those suites grade. Reserved for suites that are not themselves
    testing Tier-3 binding logic; use :func:`brand_authz_resolver` for those.
    """

    async def check(
        self,
        *,
        agent_url: str,
        brand_domain: str,
        agent_type: Any = None,
        brand_id: str | None = None,
    ) -> Any:
        from adcp.signing.brand_authz import BrandAuthorizationResult

        return BrandAuthorizationResult(True, reason="etld1_match", matched_agent_url=agent_url)


def always_authorized_brand_resolver() -> Any:
    """Build a Tier-3 double that authorizes unconditionally. See :class:`_AlwaysAuthorizedBrandResolver`."""
    return _AlwaysAuthorizedBrandResolver()


def brand_authz_resolver(brand_json_url: str, brand_json: dict[str, Any]) -> Any:
    """The SDK's REAL ``BrandJsonAuthorizationResolver`` over an in-process fetch.

    Real in every way that decides the outcome (#1291 hksr, Tier 3): the resolver
    does its own fetch, body cap, parse, ``agents[]`` walk, byte-equal URL match,
    eTLD+1 binding and ``authorized_operators[]`` delegation. Only the socket is
    replaced, by the SDK's OWN documented ``_client_factory`` seam — the same seam
    ``tests/e2e/_signing_e2e.py``'s ``seeded_capabilities_factory`` uses for hop 1.
    Substituting the resolver itself would make an assertion against it a mock
    asserting on itself; substituting the transport leaves every line of the
    binding logic under test.
    """
    from adcp.signing.brand_authz import BrandJsonAuthorizationResolver

    return BrandJsonAuthorizationResolver(brand_json_url, _client_factory=json_seeded_client_factory(brand_json))


async def walk_discovery_to_jwks(client: Any, identity: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The buyer's discovery chain, walked from the SERVED documents only.

    ``identity`` (from the served capabilities) -> ``brand_json_url`` -> brand.json
    -> the ``agents[]`` entry whose url BYTE-EQUALS the agent card's interface url
    -> ``jwks_uri`` -> JWKS. Every hop reads its next address out of the previous
    document; the JWKS URL is never written down by the caller, which is the whole
    point — a test that names it proves nothing about discovery.

    Step 7 runs through the SDK's own ``check_key_origin_consistency`` rather than
    being re-implemented: it is the check a real verifier performs, and its absence
    is what "delete the key_origins emission -> red" bites on.

    Returns ``(jwks, resolved_jwks_uri)``. Callers keep their OWN assertions about
    what the JWKS should contain — the two original copies differed there (one
    demands exactly one key, the other grades the document member by member), and
    fusing those would be the mistake DRY is invoked to prevent. What is shared is
    the WALK (salesagent-z6nr.36).
    """
    from adcp.signing import check_key_origin_consistency
    from adcp.signing.errors import SignatureVerificationError

    advertised_origin = (identity.get("key_origins") or {}).get("request_signing")
    assert advertised_origin, (
        "once the tenant owns a publishable key, the served document must advertise "
        f"identity.key_origins.request_signing, or no counterparty can locate our keys. Served: {identity!r}"
    )

    brand_response = await client.get(identity["brand_json_url"])
    assert brand_response.status_code == 200, (
        f"the trust root the served document points at must resolve over TLS at "
        f"{identity['brand_json_url']!r}; got HTTP {brand_response.status_code}"
    )
    brand = brand_response.json()

    card = (await client.get("/.well-known/agent-card.json")).json()
    agent_url = card["supportedInterfaces"][0]["url"]
    matching = [entry for entry in brand["agents"] if entry.get("type") == "sales" and entry["url"] == agent_url]
    assert len(matching) == 1, (
        "the served brand.json must carry exactly one sales agents[] entry whose url byte-equals the agent "
        f"card's interface URL {agent_url!r} — that is the match the discovery algorithm performs; served "
        f"{[entry['url'] for entry in brand['agents']]}"
    )
    resolved_jwks_uri = matching[0]["jwks_uri"]

    try:
        check_key_origin_consistency(
            jwks_uri=resolved_jwks_uri, key_origins=identity.get("key_origins"), purpose="request_signing"
        )
    except SignatureVerificationError as exc:
        raise AssertionError(
            "the JWKS the served trust root resolves to must satisfy the verifier's key-origin consistency "
            f"check against the served identity.key_origins; declared {advertised_origin!r}, resolved "
            f"{resolved_jwks_uri!r} — {exc}"
        ) from exc

    jwks_response = await client.get(resolved_jwks_uri)
    assert jwks_response.status_code == 200, (
        f"the advertised JWKS must resolve over TLS at {resolved_jwks_uri!r}; got HTTP {jwks_response.status_code}"
    )
    return jwks_response.json(), resolved_jwks_uri


def json_seeded_client_factory(body: dict[str, Any]):
    """An ``httpx.AsyncClient`` factory that answers EVERY request with *body*.

    The SDK resolvers take a ``_client_factory``/``factory`` seam and use the
    result as an async context manager, so a ``MockTransport`` client is a drop-in
    with no monkeypatching of the resolver itself — which matters, because
    substituting the resolver would make an assertion against it a mock asserting
    on itself, while substituting the TRANSPORT leaves every line of the binding
    logic under test.

    One implementation for both the integration and e2e sides: these were
    byte-identical closures in two files (salesagent-og9k.10).
    """
    import httpx

    def factory(_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=body)))

    return factory


def authorizing_brand_json(agent_url: str) -> dict[str, Any]:
    """A minimal brand.json listing *agent_url* as a buying agent — the ordinary case.

    What :func:`counterparty_key` seeds by default: a legitimate counterparty whose
    brand actually lists it, so every signing suite that predates Tier 3 (#1291
    hksr) and does not care about brand authorization keeps passing without
    learning about it. Tests that DO grade Tier 3 override the same cache entry
    (see ``tests/integration/test_request_signature_discovery.py``'s
    ``_brand_authorization``).
    """
    return {
        # DERIVED from the pin, not the literal "v1" this carried (#1757): production
        # serves .../schemas/3.1.1/brand.json, so a v1 literal here meant the fixture and
        # the deployment DISAGREED ABOUT THE SPEC VERSION ON THE WIRE — invisible to the
        # key-set pin, which only grades that "$schema" is present.
        "$schema": f"https://adcontextprotocol.org/schemas/{get_adcp_spec_version()}/brand.json",
        "name": "Test Brand",
        "agents": [{"type": "buying", "url": agent_url}],
    }


@contextmanager
def seeded_cache_entry(cache: dict[str, Any], key: str, value: Any) -> Iterator[None]:
    """Set ``cache[key] = value``, restoring whatever was there before on exit.

    The one shape behind every Tier-3 (#1291 hksr) resolver-cache seed across the
    signing suites — save what was there, overwrite, restore-or-pop on the way
    out — used by :func:`counterparty_key` here, ``_counterparty_at``
    (``tests/integration/test_request_signature_revocation.py``) and
    ``_brand_authorization`` (``tests/integration/test_request_signature_discovery.py``).
    """
    previous = cache.get(key)
    cache[key] = value
    try:
        yield
    finally:
        if previous is None:
            cache.pop(key, None)
        else:
            cache[key] = previous


@contextmanager
def counterparty_key(
    jwks: dict[str, Any],
    *,
    agent_url: str = COUNTERPARTY_AGENT_URL,
    jwks_uri: str = COUNTERPARTY_JWKS_URI,
    key_origin: str = COUNTERPARTY_KEY_ORIGIN,
) -> Iterator[None]:
    """Seed the whole ``AgentResolution`` for *agent_url* into the middleware cache.

    The three keyword arguments default to the shared counterparty
    (:data:`COUNTERPARTY_AGENT_URL` and friends) that every signing suite signs
    as; pass them only when a test needs a SECOND counterparty, which is the
    thing the defaults make visible at the call site.

    The middleware keys its resolver registry on the counterparty's ``agent_url``
    (read from the Principal row — security.mdx forbids taking it from a header, a
    body field or any self-assertion), and the cached object must carry ``jwks``
    AND ``jwks_uri`` AND ``key_origins`` so ``expected_key_origins`` reaches
    ``VerifyOptions`` and the step-7 key-origin check stays ON. Seeding the
    resolution is what lets these tests run the REAL verifier against real keys
    without a live counterparty — nothing about the outcome is faked.

    Built via ``build_registry_resolution`` (#1291 B4) — the SAME production
    constructor the configured counterparty registry uses — rather than a
    second inline ``AgentResolution(...)`` here, so this fixture and the
    registry fallback can never drift into two different resolution shapes.

    A resolution seeded here lands in the SAME ``AGENT_RESOLUTION_CACHE`` a real
    brand.json walk populates, so ``_resolution_for`` tags it ``source="walk"``
    and Tier 3 (#1291 hksr) runs against it exactly as it would in production.
    This also seeds a Tier-3 resolver that authorizes *agent_url* by default —
    the ordinary case, a counterparty its own brand actually lists — so every
    caller of this fixture that is not itself grading Tier 3 keeps passing
    without knowing it exists.
    """
    from src.core.signing import request_verifier_middleware as mw

    resolution = mw.build_registry_resolution(
        {"agent_url": agent_url, "jwks_uri": jwks_uri, "key_origin": key_origin, "jwks": jwks}
    )
    mw.AGENT_RESOLUTION_CACHE[agent_url] = resolution
    brand_json_url = resolution.brand_json_url
    try:
        with seeded_cache_entry(
            mw._BRAND_AUTHZ_RESOLVER_CACHE,
            brand_json_url,
            brand_authz_resolver(brand_json_url, authorizing_brand_json(agent_url)),
        ):
            yield
    finally:
        mw.AGENT_RESOLUTION_CACHE.pop(agent_url, None)


def registry_entry(
    jwks: dict[str, Any],
    *,
    agent_url: str = REGISTRY_AGENT_URL,
    jwks_uri: str = REGISTRY_JWKS_URI,
    key_origin: str = REGISTRY_KEY_ORIGIN,
) -> dict[str, Any]:
    """One entry of the configured counterparty registry (#1291 B4).

    The same four values :func:`counterparty_key` seeds into the cache, because the
    registry's whole job is to produce the SAME ``AgentResolution`` shape from config
    instead of from the brand.json walk. Keeping one vocabulary for both is what lets
    a test swap the resolution SOURCE while changing nothing else — and what makes it
    visible if the two shapes ever drift apart.
    """
    return {"agent_url": agent_url, "jwks_uri": jwks_uri, "key_origin": key_origin, "jwks": jwks}


@contextmanager
def signing_config(**overrides: Any) -> Iterator[Any]:
    """Substitute the agent-level ``SigningConfig``, keeping every other field.

    The middleware reads ``get_config().signing`` per request
    (``request_verifier_middleware.py``) off the process-global singleton, so
    replacing that attribute is what reaches ``_run_verifier``. Built by
    CONSTRUCTION rather than ``model_copy``, so an override naming a field that
    does not exist fails loudly instead of attaching a stray attribute — which is
    also what makes a red test for a not-yet-existing field fail for its own reason.
    """
    from src.core.config import SigningConfig, get_config

    config = get_config()
    replaced = SigningConfig(**{**config.signing.model_dump(), **overrides})
    with patch.object(config, "signing", replaced):
        yield replaced


#: Reserved key under which :func:`verifier_spy` records what the real verifier
#: RETURNED. Not a kwarg name — chosen so it cannot collide with one.
VERIFIER_RESULT = "__verifier_result__"

#: The negative-path twin of :data:`VERIFIER_RESULT`: the ``SignatureVerificationError``
#: the real verifier RAISED, recorded and then re-raised unchanged.
#:
#: Needed because ``(code, step)`` is a PAIR and only the code reaches the metric.
#: ``request_signature_header_malformed`` is raised at five different checklist steps
#: (``adcp/signing/verifier.py`` :197/:203/:213/:390 at step 1, :245/:251/:407/:414 at
#: step 2, :230 at 5, :317 at 6, :325 at 8), and the whole malformed-signature rule
#: turns on step 1 versus the rest. A test that reads only
#: ``samples_with(FAILED_METRIC, code=...)`` therefore cannot tell the step-1 pre-check
#: family from a step-2 checklist failure on a well-formed header, and a row claiming
#: to grade one of them would silently be graded by the other.
VERIFIER_ERROR = "__verifier_error__"


@contextmanager
def verifier_spy() -> Iterator[list[dict[str, Any]]]:
    """Record every ``verify_request_signature`` call, delegating to the real one.

    Pure observation: the SDK verifier still runs and still decides. Recording the
    kwargs is what proves WHICH bytes were verified — and what makes a positive
    conformance vector non-vacuous, since "non-4xx" is equally true of a middleware
    that skipped the path entirely.

    The patched attribute is called inside a worker thread (``asyncio.to_thread``);
    ``list.append`` is atomic under the GIL, so the recording list is safe as-is.
    """
    from src.core.signing import request_verifier_middleware as mw

    calls: list[dict[str, Any]] = []
    real = mw.verify_request_signature

    def _recording(**kwargs: Any) -> Any:
        record = dict(kwargs)
        calls.append(record)
        try:
            result = real(**kwargs)
        except BaseException as exc:  # noqa: BLE001 - recorded and re-raised unchanged
            # The RAISED exception under a reserved key. Recorded for EVERY exception
            # type, not just ``SignatureVerificationError``: narrowing here would make
            # a wrong-exception-type regression look like "the verifier never raised".
            record[VERIFIER_ERROR] = exc
            raise
        # The RESULT under a reserved key, so a caller can assert the returned
        # ``VerifiedSigner.key_id`` — the only positive-path observable that
        # distinguishes "the verifier accepted this signature" from "the middleware
        # never looked". Absent when the verifier raised, which is itself the signal.
        record[VERIFIER_RESULT] = result
        return result

    with patch.object(mw, "verify_request_signature", _recording):
        yield calls


def rejection_code(response: Any) -> str | None:
    """The verifier's error code OFF THE WIRE, or None if it did not reject.

    The 401 carries ``WWW-Authenticate: Signature error="<code>"`` — the SDK's
    ``unauthorized_response_headers`` and the only wire signal that distinguishes a
    verifier rejection from a route-level 401. Asserting ``status_code == 401``
    alone is satisfied by auth middleware rejecting first, which is why every
    negative case reads this instead.

    Accepts anything with ``.status_code`` and ``.headers`` — an httpx response or
    a :class:`tests.helpers.asgi_wire.WireResponse`.
    """
    if response.status_code != 401:
        return None
    challenge = response.headers.get("WWW-Authenticate") or response.headers.get("www-authenticate") or ""
    if not challenge.startswith("Signature "):
        return None
    _, _, remainder = challenge.partition('error="')
    return remainder.rstrip('"') or None


@lru_cache(maxsize=1)
def request_signature_codes() -> frozenset[str]:
    """Every request-family code the verifier can name in a challenge.

    Read off the SAME source production reads — ``REQUEST_TO_WEBHOOK_CODE``, the
    SDK's own request->webhook translation table, re-exported by
    ``src.core.signing_contract`` — never re-listed here: a local copy of the
    vocabulary cannot fail when the vocabulary changes, and a grader holding its
    own copy of the thing under test is the defect this epic already found once.
    ``src/core/metrics.py:126`` derives ``SIGNATURE_ERROR_CODES`` from that table
    for the reason its own comment gives, and this is that reason applied to the
    harness.

    A PREFIX SCAN OF ``adcp.signing.errors`` IS SUCH A COPY, and it was what stood
    here. It is a SECOND derivation of the same vocabulary, and it returns a
    SMALLER answer: the verifier also emits ``request_target_uri_malformed``
    through ``reject_malformed_target``
    (``src/core/signing/request_verifier_middleware.py``), and that name carries no
    ``REQUEST_SIGNATURE_`` prefix, so the scan cannot see it. Measured, the table's
    key set is EXACTLY the scan's 27 codes plus that one. The consequence was not
    cosmetic: ``TransportResult.assert_signature_challenge``
    (``tests/harness/transport.py``) refuses an unknown code UP FRONT, before it
    reads the wire at all, so the harness vetoed a code production sends and no
    scenario could grade that refusal.

    The webhook family (``webhook_signature_*``) is still deliberately excluded —
    it is the same shape on a different surface, and a request-signature assertion
    passing for a webhook code would grade the wrong direction. The table's KEYS
    are the request family; its values are the webhook translations.
    """
    from src.core.signing_contract import REQUEST_TO_WEBHOOK_CODE

    return frozenset(REQUEST_TO_WEBHOOK_CODE)


#: One Prometheus text-format sample line: ``name{a="1",b="2"} 3.0`` (labels optional).
_SCRAPED_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?[ \t]+(?P<value>[^ \t]+)[ \t]*$"
)

#: One ``name="value"`` pair inside a sample's label set, with backslash escapes intact.
_SCRAPED_LABEL_RE = re.compile(r'(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:[^"\\]|\\.)*)"')

#: The three escapes the Prometheus text exposition format defines for a label value.
_SCRAPED_UNESCAPES = (("\\\\", "\\"), ('\\"', '"'), ("\\n", "\n"))


def scraped_counter_samples(text: str, sample_name: str, **labels: str) -> dict[tuple[tuple[str, str], ...], float]:
    """Samples named *sample_name* in SCRAPED Prometheus text, filtered to a label superset.

    The cross-container analogue of :func:`samples_with`, and deliberately NOT folded
    into it. :func:`counter_samples` collects ``prometheus_client.REGISTRY`` inside THIS
    process; this parses the ``text/plain`` exposition a test scraped over TLS from
    another container's ``GET /metrics``. Same vocabulary, genuinely different operation
    — the CLAUDE.md DRY carve-out — and an e2e module cannot reach the in-process
    registry at all: the counter it needs was incremented in the server container.

    One definition rather than a parse open-coded in the e2e module, for the same reason
    :func:`rejection_code` exists: a reader that mis-handles the label escaping or the
    ``# HELP``/``# TYPE`` lines reports a delta of zero, which looks exactly like "the
    mechanism did not run" — the failure this whole grading exists to tell apart.

    Returns the same shape as :func:`counter_samples` (label tuple -> value) so a caller
    can sum, compare or diff two scrape windows without learning a second vocabulary.
    """
    wanted = labels.items()
    out: dict[tuple[tuple[str, str], ...], float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SCRAPED_SAMPLE_RE.match(line)
        if match is None or match.group("name") != sample_name:
            continue
        sample_labels = {
            pair.group("name"): _unescape_label_value(pair.group("value"))
            for pair in _SCRAPED_LABEL_RE.finditer(match.group("labels") or "")
        }
        if not wanted <= sample_labels.items():
            continue
        out[tuple(sorted(sample_labels.items()))] = float(match.group("value"))
    return out


#: Flask's admin app is mounted at ``/`` as well as ``/admin`` (``src/app.py``), in the
#: same process as the ASGI middleware, and the route carries no ``@require_auth``.
METRICS_PATH = "/metrics"


def scraped_verified_count(base_url: str, key_id: str, *, when: str = "now") -> float:
    """This key's ``adcp_request_signature_verified_total`` total, scraped off a LIVE stack.

    The out-of-process oracle for "the verifier ran and ACCEPTED this signature", and
    the only one available across a container boundary: the in-process legs read this
    same counter off the registry they share with the middleware, which the live
    server's verifier — running in another container — does not touch.
    ``record_signature_verified`` has ONE call site in ``src/``
    (``request_verifier_middleware``), on the branch reached only after the verifier
    returns AND Tier 3 passes, so a non-zero total for a per-capability ``keyid``
    (``signing_capability.unique_run_id``) is a claim about THIS env's requests rather
    than about the server's cumulative session.

    The two guards live in :func:`assert_metrics_scrape` and the fetch in
    :func:`scraped_metrics_text`: an unreachable endpoint and an empty exposition both FAIL
    there rather than reaching this sum as a vacuous 0.

    One definition of the SCRAPE GUARDS, shared with the e2e accepted-leg reader through
    :func:`assert_metrics_scrape`. The fetch and the label filter are deliberately NOT
    shared: that reader goes through a TLS front with an async client, and it sums the
    ``{operation, keyid}`` series rather than this function's ``{keyid}``. Stating which
    half is shared matters — an earlier version of this docstring claimed the whole
    function had one definition while a second one existed.
    """
    return sum(
        scraped_counter_samples(scraped_metrics_text(base_url, when=when), VERIFIED_METRIC, keyid=key_id).values()
    )


def assert_metrics_scrape(*, status_code: int, text: str, when: str) -> str:
    """*text* is a real Prometheus scrape, or a loud failure naming *when*.

    THE ONE DEFINITION of what makes a scrape valid. Both guards are load-bearing rather
    than defensive: a non-200 and an empty exposition each yield "no samples", which reads
    exactly like "the mechanism did not run" — the one thing this grading exists to tell
    apart. They FAIL, naming when the scrape was taken, instead of returning a vacuous 0.

    Callers differ in HOW they fetch — sync ``httpx.get`` from the runner, async through a
    TLS front inside the compose network — and that difference is real. What they must not
    differ on is what counts as a scrape, so the predicate lives here and the fetch does
    not. Keyword-only because ``(200, text)`` and ``(text, 200)`` are both plausible at a
    call site and nothing type-checks this file: ``make quality-ci`` runs mypy over ``src/``
    alone. There is no ``path`` parameter for the same reason — both fetchers append
    :data:`METRICS_PATH`, so a caller that could NAME a path would be able to name one
    nothing fetched, and the message would describe a request that never happened.
    """
    assert status_code == 200, (
        f"the {when} metrics scrape must reach the Prometheus endpoint; GET {METRICS_PATH} returned HTTP "
        f"{status_code}. A 404 means the route is shadowed or a tenant virtual_host swallowed it. "
        f"STOP and fix the scrape rather than falling back to the other assertions — they do not "
        f"grade the same thing. Body: {text[:300]!r}"
    )
    assert "# HELP" in text, (
        f"the {when} metrics scrape returned HTTP 200 but no Prometheus exposition text, so a zero "
        f"count would mean nothing. Body: {text[:300]!r}"
    )
    return text


def scraped_metrics_text(base_url: str, *, when: str = "now") -> str:
    """The live stack's Prometheus exposition text, fetched from the runner.

    The sync fetch. :func:`scraped_metrics_text_async` is the sibling for callers already
    holding an ``httpx.AsyncClient`` wired through the TLS front; both delegate their
    validity check to :func:`assert_metrics_scrape`, which is the one thing they must agree
    on.
    """
    import httpx

    response = httpx.get(f"{base_url}{METRICS_PATH}", timeout=30)
    return assert_metrics_scrape(status_code=response.status_code, text=response.text, when=when)


async def scraped_metrics_text_async(client: Any, *, when: str = "now") -> str:
    """The same text, fetched through a caller's already-wired async client.

    Exists because the e2e accepted-leg test must reach ``/metrics`` through the TLS front
    with a ``Host`` matching a tenant ``virtual_host`` — a route that is not proven until
    it runs, and one the sync fetch above cannot take.
    """
    response = await client.get(METRICS_PATH)
    return assert_metrics_scrape(status_code=response.status_code, text=response.text, when=when)


def _unescape_label_value(value: str) -> str:
    """Undo the Prometheus text format's label-value escaping."""
    for escaped, literal in _SCRAPED_UNESCAPES:
        value = value.replace(escaped, literal)
    return value


def counter_samples(sample_name: str) -> dict[tuple[tuple[str, str], ...], float]:
    """All Prometheus samples named *sample_name*, keyed by their label set."""
    from prometheus_client import REGISTRY

    out: dict[tuple[tuple[str, str], ...], float] = {}
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == sample_name:
                out[tuple(sorted(sample.labels.items()))] = sample.value
    return out


@contextmanager
def assert_counter_delta(metric: str, expected: int, *, why: str = "") -> Iterator[None]:
    """Assert *metric* moved by exactly *expected* across the block.

    The before/after counter-delta idiom was open-coded at six sites across three
    signing test modules, each re-deriving the same two reads and the same
    message (salesagent-z6nr.40's DRY finding). One helper so a seventh cannot
    drift: the reads are always the same distance apart, and the failure message
    always names the metric and both values.

    ``expected=0`` is a first-class case, not a degenerate one — "production did
    NOT count this" is exactly as much of an observable as "it counted once", and
    is what distinguishes a mechanism that declined to engage from one that never
    ran.
    """
    before = counter_total(metric)
    yield
    after = counter_total(metric)
    detail = f" {why}" if why else ""
    assert after == before + expected, (
        f"{metric} must move by {expected} across this block; it went {before} -> {after}.{detail}"
    )


def counter_total(sample_name: str) -> float:
    return sum(counter_samples(sample_name).values())


def samples_with(sample_name: str, **labels: str) -> dict[tuple[tuple[str, str], ...], float]:
    """Samples of *sample_name* whose labels are a superset of *labels*."""
    wanted = labels.items()
    return {key: value for key, value in counter_samples(sample_name).items() if wanted <= dict(key).items()}


# ---------------------------------------------------------------------------
# Declaration and request construction (promoted alongside the constants above)
# ---------------------------------------------------------------------------


def unsupported() -> dict[str, Any]:
    """The default posture: the ``none`` bucket for every operation.

    Promoted out of ``tests/integration/test_request_signature_middleware.py``
    (salesagent-nx8jp.9) so ``BaseTestEnv.declare_request_signing(bucket=...)``
    can reach it: ``tests/unit/test_architecture_no_cross_test_module_imports.py``
    makes a ``test_*.py`` module unimportable, so a realization stranded in one is
    reachable only by copying it — which is how ``_NARROWED_NONE`` came to exist
    twice. Same promotion :func:`signed_probe` went through, for the same reason.
    """
    return {"supported": False}


def narrowed_none() -> dict[str, Any]:
    """``supported: true``, narrowed so the surface under test lands in ``none``.

    The OTHER half of the ``none`` bucket, and the one every caller exists to
    separate from :func:`unsupported`. Both bucket the operation under test as
    ``none``, and ``bucket_for`` cannot tell them apart afterwards — but the
    declarations differ on ``supported``, which is the field the spec's pre-check
    obligation is scoped by: the signed-requests storyboard gates all 28 negative
    vectors on ``request_signing.supported: true`` alone, so a seller advertising
    ``supported: false`` is outside the rule while a seller advertising
    ``supported: true`` is inside it however narrowly it declared its buckets.

    Naming ``create_media_buy`` (a real operation on another route) rather than
    leaving ``supported_for`` absent is what does the narrowing: a NULL
    ``supported_for`` means "verify wherever signatures appear"
    (``src/core/signing/posture.py`` ``_bucket_for``), i.e. ``supported``, not
    ``none``. Callers whose surface under test IS ``create_media_buy`` must narrow
    to some other real operation instead of reusing this one.
    """
    return bucketed_declaration("supported", "create_media_buy")


def bucketed_declaration(bucket: str, *operations: str) -> dict[str, Any]:
    """A ``request_signing`` declaration putting *operations* in exactly one bucket.

    ``required_for`` entries must also appear in ``supported_for``
    (``get-adcp-capabilities-response.json`` x-adcp-validation: "an operation
    can't be required without being supported"), so the required declaration lists
    both — which is also what makes the precedence rule
    ``required_for > warn_for > supported_for`` load-bearing rather than
    decorative.

    Naming the operations EXPLICITLY (rather than leaving a bucket unnarrowed) is
    what puts every other operation in the ``none`` bucket, and so what keeps the
    controls in the suites that use this meaningful.
    """
    declaration: dict[str, Any] = {"supported": True, "supported_for": list(operations)}
    if bucket == "required":
        declaration["required_for"] = list(operations)
    elif bucket == "warn":
        declaration["warn_for"] = list(operations)
    elif bucket != "supported":
        raise ValueError(f"unknown bucket {bucket!r}")
    return declaration


def keypair_for(kid: str) -> tuple[Any, dict[str, Any]]:
    """Fresh Ed25519 request-signing material for *kid*: (private_key, public JWKS)."""
    from adcp.signing import generate_signing_keypair, load_private_key_pem

    pem, public_jwk = generate_signing_keypair(alg="ed25519", kid=kid, purpose=REQUEST_SIGNING)
    return load_private_key_pem(pem), {"keys": [public_jwk]}


def seed_principal(env: Any, *, agent_url: str | None = COUNTERPARTY_AGENT_URL) -> str:
    """Create the shared tenant + a Principal carrying *agent_url*; return its token.

    ``Principal.agent_url`` (nullable ``String(500)``) is the onboarding record,
    and the only legitimate source for the counterparty's agent URL. Pass
    ``agent_url=None`` to grade what the verifier does when onboarding never
    recorded one.

    *env* is the live :class:`~tests.harness._base.BareIntegrationEnv`: it is not
    read here, but the factories below write through the session that entering the
    env bound to them, so taking it as an argument is what pins the ordering.

    Distinct from :func:`tests.harness.signing_capability.attach_agent_url`, and
    both are needed. That one is for an env that SIGNS: it reuses the env's OWN
    tenant/principal (owner decision D3) so the signer's identity is the env's.
    This one seeds the shared unsigned-suite rows named by
    :data:`SIGNING_TENANT_ID` / :data:`SIGNING_PRINCIPAL_ID`, which the four
    ``tests/integration/test_request_signature_*`` / ``test_signing_conformance_*``
    modules address directly and which no env creates for them.
    """
    from tests.factories import PrincipalFactory, TenantFactory

    tenant = TenantFactory(tenant_id=SIGNING_TENANT_ID, virtual_host=SIGNING_AGENT_HOST)
    principal = PrincipalFactory(
        tenant=tenant,
        principal_id=SIGNING_PRINCIPAL_ID,
        agent_url=agent_url,
    )
    return principal.access_token


#: Both signature headers present, neither parseable — the malformed-signature
#: shape. security.mdx :1226/:1271 make this the case that blocks the bearer
#: fallback regardless of bucket, and it is checklist STEP 1, which is what makes
#: it the right probe for any test asserting that an EARLIER step outranks a later
#: one. ``_strict_header_precheck`` deliberately does not pre-empt an unparseable
#: ``Signature-Input`` (``negative/011``/``024`` are the SDK's to code), so this
#: shape reaches the SDK and is refused there.
MALFORMED_SIGNATURE_HEADERS = {
    "Signature-Input": "sig1=this-is-not-an-rfc8941-inner-list",
    "Signature": "sig1=:AAAA:",
}

#: What a harness leg can put on the wire in place of a correct signature.
#:
#: ``False`` sends no signature headers at all and ``True`` sends a real RFC 9421
#: signature over the exact bytes sent. The two strings are FAILURE realizations, and
#: they are two rather than one because the verifier's own phase boundary is drawn
#: between them (``_handle_rejection``, ``request_verifier_middleware``):
#:
#: * ``"malformed"`` — :data:`MALFORMED_SIGNATURE_HEADERS`, a step-1 PRE-CHECK failure
#:   that rejects in EVERY bucket, warn included (security.mdx :1226/:1271);
#: * ``"tampered"`` — a cryptographically real signature over a DIFFERENT rendering of
#:   the same request, so the verifier reaches ``request_signature_digest_mismatch`` on
#:   its merits inside the checklist, where ``warn_for`` does suppress it (:1273).
#:
#: Collapsing the two would make the (code, step) predicate lane .1 turns on
#: unrepresentable from a scenario.
SIGNATURE_REALIZATIONS: tuple[Any, ...] = (False, True, "malformed", "tampered")

#: The type every seam that carries a realization is annotated with.
SignatureRealization = bool | Literal["malformed", "tampered"]


def realization(signed: Any) -> SignatureRealization:
    """*signed*, refused unless it is one of :data:`SIGNATURE_REALIZATIONS`.

    A typo'd ``signed="malfromed"`` must RAISE, not silently sign correctly.
    Without this the widening from ``bool`` is a trapdoor: every consumer branches
    on the exact strings and falls through to the correct-signature arm for
    anything else, so the scenario would send a well-formed signature, be
    accepted, and grade the acceptance as if it had graded a refusal. That is the
    same failure the epic fixed at the settings boundary for
    ``counterparty_agent_type`` — an unrecognized value reaching a default arm
    instead of a raise.

    Identity comparison for the booleans on purpose: ``1`` and ``0`` are equal to
    ``True``/``False`` but are not realizations, and a caller that produced an int
    where a realization belongs has a bug worth surfacing.
    """
    if signed is True or signed is False or signed in ("malformed", "tampered"):
        return signed
    raise ValueError(
        f"signed={signed!r} is not a signature realization. Pass one of "
        f"{SIGNATURE_REALIZATIONS!r}: False (no signature headers), True (a real signature over "
        "the exact bytes sent), 'malformed' (unparseable headers — a step-1 pre-check failure), "
        "or 'tampered' (a real signature over a different body — a checklist digest mismatch). "
        "Refusing rather than falling through to a correct signature, which would make a "
        "scenario grading a refusal pass on an acceptance."
    )


def tampered_signing_body(raw: bytes) -> bytes:
    """The body to SIGN so that sending *raw* is a checklist digest mismatch.

    Generalized out of ``TestShadowModeLadder._tampered_signed_request``
    (salesagent-nx8jp.9), which hardcoded one request document. The caller signs
    over what this returns and sends its OWN bytes, so ``Signature-Input`` and
    ``Signature`` stay WELL-FORMED and the signature is cryptographically real:
    the verifier gets past step 1 on its merits and fails at
    ``request_signature_digest_mismatch`` inside the checklist. That is what makes
    it a checklist failure rather than a header rejection — the distinction the
    (code, step) predicate in ``_handle_rejection`` turns on, and the reason
    ``warn_for`` suppresses this one and not ``"malformed"``.

    A TRAILING SPACE rather than an edited field: ``content-digest`` covers the raw
    bytes, so any byte-level difference produces the mismatch, and appending one
    keeps the mutation independent of what the caller's document happens to
    contain (the hardcoded version could only tamper with a body it had written
    itself).

    PER-TRANSPORT LIMIT, stated rather than worked around: a BODYLESS request
    (``body=None`` — the e2e ``GET /api/v1/capabilities`` leg) has nothing to
    mutate, so this refuses instead of returning something that would sign and
    send identical bytes and be ACCEPTED. A tampered realization is not
    expressible on a bodyless surface; dispatch it on a body-carrying one.
    """
    if not raw:
        raise ValueError(
            "signed='tampered' needs a request BODY to mutate: the tamper is a "
            "content-digest mismatch, and a bodyless request (body=None — e.g. the e2e "
            "bodyless GET on /api/v1/capabilities) has no bytes to differ in. Signing and "
            "sending identical zero bytes would be ACCEPTED, so a scenario grading a "
            "digest mismatch would pass on an acceptance. Dispatch the tampered "
            "realization on a body-carrying surface, or use signed='malformed', which "
            "needs no body."
        )
    return raw + b" "


def request_headers(token: str | None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Wire headers: tenant hint + optional bearer + whatever the test adds."""
    headers = {"x-adcp-tenant": SIGNING_TENANT_ID}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    return headers


def sign_wire_request(
    private_key: Any,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    key_id: str = COUNTERPARTY_KID,
) -> dict[str, str]:
    """Sign *body* over the WIRE bytes, covering ``content-digest``."""
    from adcp.signing import sign_request

    signed = sign_request(
        method=method,
        url=url,
        headers=headers,
        body=body,
        private_key=private_key,
        key_id=key_id,
        alg="ed25519",
        cover_content_digest=True,
    )
    return signed.as_dict()


def signed_headers(
    private_key: Any,
    token: str | None,
    *,
    method: str,
    path: str,
    body: bytes = b"",
    extra: dict[str, str] | None = None,
    key_id: str = COUNTERPARTY_KID,
    origin: str = WIRE_ORIGIN,
) -> dict[str, str]:
    """The full header set for one signed request to *path* at *origin*.

    Merges the wire headers a signed request carries anyway (tenant hint, bearer,
    whatever the case needs) with the signature over exactly those headers and *body*
    — the ordering the signature depends on, since the base covers ``content-digest``
    and the headers the signer chose to sign. Every signing suite built this merge by
    hand, and a copy that signs a URL or a header set slightly different from the one
    it then sends fails as ``request_signature_invalid``, i.e. looks like a verifier
    bug. One construction, so that cannot happen in only one of them.

    Signed FRESH per call: ``sign_request`` mints a new nonce each time, so two
    requests in one test do not collide in A4's replay store.

    *origin* defaults to :data:`WIRE_ORIGIN`, the in-process ASGI client's origin, so
    every existing caller signs byte-identical bytes. An e2e caller passes the REAL TLS
    origin **including the port**: ``_verify_url``
    (``src/core/signing/request_verifier_middleware.py``) rebuilds the authority from
    the ``Host`` header nginx forwards verbatim and the scheme from
    ``X-Forwarded-Proto``, so a signature over a portless authority covers a different
    ``@target-uri`` than the one the verifier reconstructs and is refused as
    ``request_signature_invalid`` — a fixture bug wearing a verifier bug's clothes.
    Widened here rather than by inlining :func:`sign_wire_request` at the e2e call site
    for the reason this function's own docstring gives: a second copy of the merge that
    signs a slightly different URL looks like a verifier bug.
    """
    base = request_headers(token, extra)
    return {
        **base,
        **sign_wire_request(
            private_key,
            method=method,
            url=f"{origin}{path}",
            headers=base,
            body=body,
            key_id=key_id,
        ),
    }


def signed_probe(
    private_key: Any,
    token: str,
    *,
    key_id: str = COUNTERPARTY_KID,
    request_id: str = "registry-probe",
) -> tuple[dict[str, str], bytes]:
    """A well-formed signed POST to the bodyless AdCP surface, and its wire bytes.

    Real Ed25519 over the real wire bytes under :data:`COUNTERPARTY_KID`, so every
    checklist step up to key resolution passes on its merits. That is what makes the
    outcome attributable to WHICH resolution path supplied the key — or to which
    discovery failure was mapped — rather than to anything about the signature itself.

    Promoted out of ``tests/integration/test_request_signature_middleware.py`` when
    the discovery-code suite needed the identical probe: a name whose home is a
    ``test_*.py`` module has no importable home at all
    (``tests/unit/test_architecture_no_cross_test_module_imports.py``), and a second
    inline copy is the duplication class the DRY invariant exists to stop. Both
    suites must send the SAME bytes, or "the signature verified on its merits" means
    something different in each of them.

    *key_id* is the ONE knob a caller grading a checklist step BEFORE key resolution
    needs: an over-long value reaches ``adcp/signing/verifier.py:245`` at step 2 with
    everything ahead of it — parse, label, params, tag, alg, window, components —
    still passing on its merits. *request_id* names the probe in the echoed body so a
    caller can read its own receipt back off the response.
    """
    body = json.dumps({"context": {"request_id": request_id}}).encode()
    headers = signed_headers(
        private_key,
        token,
        method="POST",
        path=BODYLESS_ADCP_PATH,
        body=body,
        extra={"Content-Type": "application/json"},
        key_id=key_id,
    )
    return headers, body


def verify_as_conformant_receiver(signed: CapturedWebhook, jwks: dict[str, Any]) -> Any:
    """Run the SDK's verifier over *signed* the way the PINNED spec defines it.

    *signed* is one outbound POST as the receiving socket saw it — the ``url`` a
    receiver reconstructs from its own wire, the headers it received, the bytes it
    read. Three graders need exactly this call: the C2 proof-of-control challenge
    (in-process, ``tests/integration/test_notification_proof_challenge.py``), the
    E2E-3 delivery webhook (``tests/e2e/test_webhook_signature_e2e.py``), and the
    BR-UC-004 9421 sibling scenario (``tests/bdd/steps/domain/uc004_delivery.py``,
    #1291 z6nr.31). One definition, here — a verifier that drifted between them would
    let one surface be graded more weakly than the other.

    ``verify_webhook_signature`` is the SDK's webhook entry point and it is the right
    machinery — the whole checklist, the tag pin, the required components, the digest
    policy, the alg allowlist, the step-6 component precheck, and the request->webhook
    error retag. It is called here through the request verifier plus the SAME two
    private helpers ``verify_webhook_signature`` itself calls
    (``adcp.signing.webhook_verifier._precheck_webhook_has_required_components``,
    ``._retag_to_webhook``) rather than a hand-copied reimplementation, because the SDK
    diverges from the pin on exactly ONE value and ``WebhookVerifyOptions`` gives no way
    to override it:

    security.mdx @ v3.1.1 :1426 — *"Webhooks are signed with the agent's ``adcp_use:
    "request-signing"`` key; there is no separate webhook key purpose. Domain separation
    between requests and webhooks is carried by the signature ``tag`` … not by the key
    purpose."* Its required-JWK table pins ``adcp_use`` to ``"request-signing"``, :955
    repeats it ("webhooks do not need their own purpose"), and :1438 makes
    ``"webhook-signing"`` DEPRECATED — verifiers "MUST still accept it for backward
    compatibility", while "new signers SHOULD publish and sign with ``"request-signing"``
    keys only". Step 8 (:1478) and the taxonomy row (:1560) both say the accept-set is
    exactly ``{"request-signing", "webhook-signing"}`` — never a single value in either
    direction.

    The SDK inverts that: ``verify_webhook_signature`` builds its options with
    ``expected_adcp_use=ADCP_USE_WEBHOOK``, so it accepts ONLY the deprecated value and
    REJECTS the one the spec mandates for new signers
    (``webhook_signature_key_purpose_invalid``). Filed upstream:
    https://github.com/adcontextprotocol/adcp-client-python/issues/1018 (FIXME(#1291)).
    The divergence itself is also pinned locally by
    ``TestChallengeIsSignedAndVerifiable.test_the_sdk_webhook_verifier_diverges_from_the_pin``
    so it becomes a loud failure the moment the SDK is fixed and this substitution can go;
    the accept-SET breadth graded here (``tests/unit/test_conformant_receiver_key_purpose.py``)
    is a SEPARATE, longer-lived obligation that outlives that fix — the spec's requirement,
    not a note about the SDK's bug.

    Verifying with the accept-set is therefore what a CONFORMANT receiver does: attempt
    ``"request-signing"`` first (:1426's canonical value); on a refusal caused SPECIFICALLY
    by the ``adcp_use`` mismatch, retry ONCE with ``"webhook-signing"``. Any other step-8
    refusal (``use``, ``key_ops``, or algorithm) is reported as itself, never blurred into
    a purpose complaint by a retry that re-runs the whole check under a second purpose —
    the SDK's ``_check_key_purpose`` gives no code finer than
    ``request_signature_key_purpose_invalid`` for any of its four conditions, so the
    ``"adcp_use"`` substring in the exception's own message is the only signal available to
    tell them apart.

    Everything except the widened accept-set is copied from the SDK's own construction, so
    this cannot drift into a weaker check than the SDK performs.

    Nothing here reaches into ``src``: the decision is made by SDK code over the wire
    bytes and a JWKS document, which is what lets an e2e caller claim its VERIFY path
    holds no production import.
    """
    from adcp.signing.constants import ADCP_USE_REQUEST, ADCP_USE_WEBHOOK
    from adcp.signing.errors import REQUEST_SIGNATURE_KEY_PURPOSE_INVALID, SignatureVerificationError
    from adcp.signing.jwks import StaticJwksResolver
    from adcp.signing.verifier import VerifierCapability, VerifyOptions, verify_request_signature
    from adcp.signing.webhook_signer import WEBHOOK_TAG as _TAG
    from adcp.signing.webhook_verifier import _precheck_webhook_has_required_components, _retag_to_webhook

    headers = dict(signed.headers)
    _precheck_webhook_has_required_components(headers)

    # FIXME(#1291): the retry below exists only because adcp-client-python's webhook
    # verifier pins expected_adcp_use to the deprecated "webhook-signing" value and
    # gives no way to widen it (WebhookVerifyOptions has no expected_adcp_use field).
    # Delete this retry and call verify_webhook_signature directly once
    # https://github.com/adcontextprotocol/adcp-client-python/issues/1018 lands.
    def _attempt(expected_adcp_use: str) -> Any:
        return verify_request_signature(
            method="POST",
            url=signed.url,
            headers=headers,
            body=signed.content,
            options=VerifyOptions(
                now=time.time(),
                capability=VerifierCapability(
                    supported=True, covers_content_digest="required", required_for=frozenset({"webhook"})
                ),
                operation="webhook",
                # The SDK's own resolver over OUR published document: a JwksResolver maps a
                # keyid to ONE JWK, so handing it the whole JWKS wrapper would have the
                # verifier read `use` off the envelope and fail for a reason that is the
                # test's, not production's.
                jwks_resolver=StaticJwksResolver(jwks),
                expected_tag=_TAG,
                expected_adcp_use=expected_adcp_use,
            ),
        )

    try:
        return _attempt(ADCP_USE_REQUEST)
    except SignatureVerificationError as exc:
        if exc.code != REQUEST_SIGNATURE_KEY_PURPOSE_INVALID or "adcp_use" not in str(exc):
            raise _retag_to_webhook(exc) from exc
        try:
            return _attempt(ADCP_USE_WEBHOOK)
        except SignatureVerificationError as retried:
            raise _retag_to_webhook(retried) from retried


def just_after_provisioning() -> datetime:
    """An instant safely inside the activity window of a key provisioned just now.

    ``provision_signing_key`` stamps ``not_before`` from the wall clock, so a
    fixed literal instant would sit outside the window and make window-sensitive
    resolution fail for reasons that have nothing to do with the behavior tested.
    """
    return datetime.now(UTC) + timedelta(minutes=1)
