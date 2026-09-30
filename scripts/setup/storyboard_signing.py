"""Provision the storyboard agent to satisfy the signed-requests test-kit contract.

The contract is ``dist/compliance/{version}/test-kits/signed-requests-runner.yaml``. It
names five agent-side preconditions, and this module is the ONE owner of all five — the
three that are deployment settings (read by the server process from its environment) and
the two that are tenant state (written to the database).

Both halves are here rather than beside their consumers because they are one fact seen
twice: the counterparty this agent trusts. Split across a compose file and a seed script,
the ``agent_url`` in the registry and the ``agent_url`` on the principal drift, and the
symptom is a 401 on every positive vector with nothing naming the cause —
``_signature_credential`` looks the signer up by exactly that string
(``src/core/resolved_identity.py``) and an anonymous caller on ``create_media_buy`` is
``AUTH_MISSING``, which the challenge responder lifts to the same 401 a signature refusal
produces.

What the contract requires, and where each lands
------------------------------------------------
1. ``request_signing.required_for`` covering the graded operations -> the tenant's
   ``capability_declarations`` (:func:`declarations`).
2. A counterparty JWKS holding ``test-ed25519-2026`` and ``test-es256-2026`` with
   ``adcp_use: "request-signing"``, trusted as a registered test counterparty ->
   ``ADCP_SIGNING_COUNTERPARTY_REGISTRY`` (:func:`counterparty_registry`), plus the
   principal that JWKS resolves to (:func:`seed`).
3. ``test-revoked-2026`` pre-revoked before the negative phase ->
   ``ADCP_SIGNING_REVOKED_KEYIDS``.
4. A replay TTL of at least ``min_replay_ttl_seconds`` -> ``ADCP_SIGNING_REPLAY_TTL_OVERRIDES``.
5. The per-keyid replay cache at its configured cap for vector 020 ->
   ``ADCP_SIGNING_PER_KEYID_CAP_OVERRIDES``.

Why the registry and not the brand.json walk
--------------------------------------------
``_resolution_for`` consults ``counterparty_registry`` only when there is no
``agent_url`` to walk from, and ``agent_url`` comes from the principal the BEARER
resolved. The conformance runner sends no bearer on a vector probe — the signature is the
credential — so the walk has no input and the registry is the only path. That is the case
the registry was built for; its docstring says so.

Why this deployment must not signal production
-----------------------------------------------
``SigningSettings`` refuses a non-empty ``counterparty_registry`` (and both override maps)
under any production signal, and it is right to: the private keys of the conformance
corpus are PUBLISHED in ``keys.json``, so trusting those keyids in production would let
anyone at all sign as a registered counterparty. The storyboard agent is a grading
deployment that wants production's forward-compatible REQUEST BOUNDARY and nothing else
about production, so it declares that one axis explicitly
(``ADCP_PYDANTIC_EXTRA_MODE``) instead of claiming to be production. See the service
definition in ``docker-compose.e2e.yml``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.core.database.models import Account

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.setup.init_database_ci import CI_TEST_SUBDOMAIN, CI_TEST_TOKEN  # noqa: E402

#: The Host the conformance runner dials, and therefore the ``virtual_host`` the storyboard
#: tenant must answer to.
#:
#: A VECTOR PROBE CARRIES NO ROUTING HEADER. The runner's ``-H x-adcp-tenant=...`` reaches
#: the MCP ``initialize`` handshake and the ordinary storyboard steps, but a signed vector
#: is built from the vector's own headers plus an ``Mcp-Session-Id``
#: (``@adcp/sdk`` ``probe.mjs`` ``attachMcpSessionHeader``) — so ``_detect_tenant`` has only
#: the Host to go on. Without a tenant there is no posture, ``posture_for_tenant(None)``
#: falls back to the agent-level default whose buckets are all empty, and every negative
#: vector is SPEC-CORRECTLY answered 200.
#:
#: ``tests/storyboard/test_storyboard_conformance.py`` builds its agent URLs from this, so
#: the value the runner dials and the value the database answers to are one string.
STORYBOARD_VIRTUAL_HOST = "storyboard.adcp.test:8443"

#: The conformance runner AS A COUNTERPARTY: the ``agent_url`` its keys resolve at.
#:
#: A name, not a destination. Nothing dials it — a registry entry short-circuits the
#: three-hop walk and carries its JWKS inline (``build_registry_resolution``) — but it is
#: what ``get_principal_by_agent_url`` looks the established signer up by, and the origin
#: the key location is derived from.
#:
#: Under ``.test``, which RFC 6761 §6.2 reserves precisely so it can never resolve.
COUNTERPARTY_AGENT_URL = "https://runner.adcp-conformance.test/a2a"

#: The principal the runner's verified signature establishes. It holds a token because the
#: schema requires one; nothing ever presents it, and it is deliberately NOT the CI token —
#: a second principal answering to ``ci-test-token`` would make the token lookup ambiguous.
COUNTERPARTY_PRINCIPAL_ID = "storyboard-conformance-runner"
COUNTERPARTY_PRINCIPAL_TOKEN = "storyboard-conformance-runner-token-not-presented"

#: The operation every graded vector but two addresses, and therefore the one operation
#: ``required_for`` has to name.
#:
#: The corpus is uniform about this: 26 of the 28 negative vectors declare
#: ``required_for: ["create_media_buy"]``. The two that do not are ``negative/027``
#: (``required_for: []`` — it grades the webhook-credential escalation, which fires
#: REGARDLESS of the bucket) and ``negative/028``, whose ``verifier_capability`` declares a
#: protocol-method bucket this agent refuses to store at all, so the runner skips it as a
#: capability-profile mismatch (docs/design/request-signing-subset.md). So a single-entry
#: bucket grades everything a larger one would, and every operation added beyond it is a
#: promise to buyers that nothing here checks.
REQUIRED_FOR = ("create_media_buy",)

#: ``covers_content_digest``. The corpus asks for all three values and an agent has ONE:
#: ``negative/007`` needs ``required`` (a signature omitting content-digest must be
#: refused), ``negative/018`` needs ``forbidden`` (one covering it must be refused), and
#: every other vector is written against ``either``.
#:
#: ``either`` is the value that grades the most and mis-grades nothing, and the arithmetic
#: was re-measured against the runner's own gate (``request-signing/grader.mjs``
#: ``contentDigestDeclarationMismatch`` + ``contentDigestStructuralMismatch`` at
#: @adcp/sdk 14.0.0-rc.42) rather than inferred from what each vector declares:
#:
#: * under ``either`` the runner skips 007 and 018 (their expected codes are the two
#:   content-digest POLICY refusals) and grades 010, 023 and ``positive/002``;
#: * ``required`` would NOT add 007 back — the structural rule skips a vector whose
#:   Signature-Input omits content-digest, which is 007's whole shape — while removing
#:   ELEVEN of the twelve positives, every one that signs without a digest;
#: * ``forbidden`` would drop ``positive/002``, which covers one.
#:
#: So ``either`` is not a compromise between the vectors; it is the only value under which
#: the happy path is graded at all. A property of the corpus, not a gap in this agent —
#: reported with the fixture PR.
COVERS_CONTENT_DIGEST = "either"

#: Test-kit ``stateful_vector_contract.revocation.pre_revoked_keyid``.
REVOKED_KEYID = "test-revoked-2026"

#: Test-kit ``stateful_vector_contract.rate_abuse.grading_target_per_keyid_cap_requests``.
#: The runner sends this many requests and expects the NEXT one refused. It is a GRADING
#: target and explicitly not a production recommendation — ``SigningSettings`` refuses it
#: as a global cap and accepts it only for a named counterparty, which is what this is.
GRADING_PER_KEYID_CAP = 100

#: The replay-row lifetime for the runner's keyids, in seconds.
#:
#: Bounded on BOTH sides by the test-kit, and the window between them is narrow:
#:
#: * above ``min_replay_ttl_seconds: 10`` (itself above ``max_interval_seconds: 5``), or
#:   the first of ``negative/016``'s two submissions evicts before the second arrives and
#:   the vector passes SPURIOUSLY — both accepted, no rejection observed;
#: * above ``window_seconds: 60``, or ``negative/020``'s rows drain before the 101st
#:   request and the cap is never reached;
#: * well BELOW what the vectors' own 300s signature window would otherwise leave behind
#:   (~360s once ``remember()`` raises the claim to ``expires - now + skew``), because
#:   020 drives one keyid to its cap of 100 and those rows would then trip step 9a for
#:   every LATER vector — including 016's first submission, which must be accepted.
#:
#: ``src/core/signing/replay_store.py`` carries the same derivation from the store's side.
GRADING_REPLAY_TTL_SECONDS = 70

#: Where the runner's keypairs are published. The VENDORED snapshot, not the downloaded
#: bundle: this module is read by ``scripts/test-stack.sh`` at compose time, before
#: anything has materialized a bundle, and the vendored tree is committed and sha256-pinned
#: by ``MANIFEST.json`` — so it is both always present and not a second copy of the keys.
_KEYS_JSON = (
    _PROJECT_ROOT / "tests" / "fixtures" / "adcp_conformance_vectors" / "3.1.1" / "request-signing" / "keys.json"
)

#: Members of a published JWK that are not part of the public key.
_PRIVATE_MEMBERS = ("_private_d_for_test_only", "$comment")


def counterparty_jwks() -> dict[str, Any]:
    """The runner's published keys, as a JWKS a verifier may trust.

    Every key in ``keys.json`` is included, and that is deliberate rather than lax: three
    of the four are there so a vector can be refused on its MERITS instead of on a missing
    key. ``negative/009`` presents ``test-gov-2026`` and must reach step 8 to be refused
    for its ``adcp_use``; ``negative/017`` presents ``test-revoked-2026`` and must reach
    step 9 to be refused as revoked. Omit either and both are refused at step 7 with
    ``request_signature_key_unknown`` — a rejection that grades as a FAIL because it is the
    wrong code, arrived at without ever running the check the vector is about.

    ``unknown-key-9999`` is in no JWKS anywhere, which is what keeps ``negative/008``
    honest.

    Private material is stripped. It is public spec data and the corpus says so, but a
    private key installed in a verifier's TRUST STORE is the wrong shape regardless of who
    else can read it.
    """
    published = json.loads(_KEYS_JSON.read_text())
    return {
        "keys": [
            {name: value for name, value in key.items() if name not in _PRIVATE_MEMBERS}
            for key in published["keys"]
            if "kid" in key
        ]
    }


def counterparty_registry() -> dict[str, dict[str, Any]]:
    """``ADCP_SIGNING_COUNTERPARTY_REGISTRY`` — every runner keyid, one counterparty.

    Keyed per keyid because that is the only handle a bearer-less request offers, and the
    setting refuses anything that looks like a pattern. All four entries name the SAME
    counterparty and carry the same JWKS: they are one agent with four published keys, and
    the SDK selects within the JWKS by ``kid``.
    """
    jwks = counterparty_jwks()
    entry = {"agent_url": COUNTERPARTY_AGENT_URL, "jwks": jwks}
    return {key["kid"]: dict(entry) for key in jwks["keys"]}


def signing_env() -> dict[str, str]:
    """The storyboard agent's signing environment, as ``NAME -> value``.

    Read by ``scripts/test-stack.sh``, which exports these before ``docker compose up`` so
    the service definition can interpolate them. Generated rather than written into the
    compose file because three of the four values are DERIVED — from ``keys.json`` and from
    the test-kit's own numbers — and a literal copy in YAML is a silent 401 the day the
    corpus is re-vendored.
    """
    registry = counterparty_registry()
    compact: dict[str, Any] = {"separators": (",", ":")}
    return {
        "ADCP_SIGNING_COUNTERPARTY_REGISTRY": json.dumps(registry, **compact),
        "ADCP_SIGNING_REVOKED_KEYIDS": REVOKED_KEYID,
        # Every registered keyid, not just the two the runner signs positives with: the cap
        # and the TTL bound how long ANY of them keeps replay rows, and a keyid left on the
        # production floor would hold rows for ~360s and trip step 9a for a later vector.
        "ADCP_SIGNING_PER_KEYID_CAP_OVERRIDES": json.dumps(dict.fromkeys(registry, GRADING_PER_KEYID_CAP), **compact),
        "ADCP_SIGNING_REPLAY_TTL_OVERRIDES": json.dumps(dict.fromkeys(registry, GRADING_REPLAY_TTL_SECONDS), **compact),
    }


def declarations(brand_json_url: str) -> dict[str, Any]:
    """The storyboard tenant's ``capability_declarations``.

    ``identity.brand_json_url`` is obliged by the posture, not chosen: a ``required_for``
    naming any operation triggers the pinned schema's ``required_when``, and the capability
    read path additionally byte-matches the declared value against the one this agent
    actually serves. So it is passed in, derived by the caller from the tenant row, rather
    than written here as a second literal of the same origin.
    """
    return {
        "request_signing": {
            "supported": True,
            "covers_content_digest": COVERS_CONTENT_DIGEST,
            "required_for": list(REQUIRED_FOR),
        },
        "identity": {"brand_json_url": brand_json_url},
    }


#: The account the universal ``webhook_emission`` storyboard transacts against, by
#: NATURAL KEY. Its ``sample_request`` sends ``account: {brand: {domain}, operator}``
#: and no ``account_id`` — the reference the pinned schema calls a natural key — and
#: ``account_lookup._by_natural_key`` RESOLVES that against existing rows rather than
#: provisioning one. So the row has to exist before the storyboard runs, or every
#: ``trigger_*`` step fails ACCOUNT_NOT_FOUND before a single webhook is emitted.
#:
#: Derived by scanning the pinned compliance bundle for every such pair, not hand-written.
#: Two are deliberately ABSENT and must stay absent: ``nonexistent-brand-xyz.example`` (the
#: fixture that grades not-found) and the ``<runner-supplied ...>`` placeholder in the
#: single-side-trust runner contract.
STORYBOARD_ACCOUNT_KEYS: tuple[tuple[str, str], ...] = (
    ("acmeoutdoor.example", "pinnacle-agency.example"),
    ("amsterdam-steakhouse.example", "pinnacle-agency.example"),
    ("novamotors.example", "pinnacle-agency.example"),
    ("otherbrand.example", "other-operator.example"),
    ("pagination-acme-1.example", "pagination-pinnacle.example"),
    ("pagination-acme-2.example", "pagination-pinnacle.example"),
    ("pagination-acme-3.example", "pagination-pinnacle.example"),
    ("test.example", "test.example"),
)

#: BOTH sandbox values. The reference carries the flag and the resolver filters on it
#: exactly -- ``_scope_natural_key`` applies ``Account.sandbox == sandbox`` whenever the
#: reference names one -- so a row seeded ``False`` is INVISIBLE to a request asking for
#: ``True``. Measured on the wire: "natural-key miss ... sandbox=True" against a
#: ``sandbox=False`` row. The two never collide: an omitted flag matches ``NULL OR False``
#: only, so it can never see the sandbox row.
STORYBOARD_SANDBOX_MODES: tuple[bool, ...] = (False, True)


def _brand_domain_of(account: Account) -> str | None:
    """The brand domain on a row, whether the column deserialized to a model or a dict."""
    brand = account.brand
    if brand is None:
        return None
    return brand.get("domain") if isinstance(brand, dict) else getattr(brand, "domain", None)


def _storyboard_account_id(brand_domain: str, sandbox: bool) -> str:
    """Stable, readable account id per (brand, sandbox). Unique by construction."""
    slug = brand_domain.replace(".", "-")
    return f"sb-{slug}-sandbox" if sandbox else f"sb-{slug}"


def seed() -> None:
    """Make the storyboard tenant answer to the runner's Host, posture and counterparty.

    Idempotent, and run from ``[testenv:storyboard]``'s ``commands_pre`` — AFTER the suites
    that share this database, which ``depends`` orders ahead of it. That ordering is load
    bearing: ``required_for`` changes what an UNAUTHENTICATED ``create_media_buy`` is
    answered (``request_signature_required`` rather than ``AUTH_MISSING``), and
    ``virtual_host`` changes the origin this tenant publishes its trust root at. Neither
    should reach a suite that did not ask for it.
    """
    from sqlalchemy import select

    from src.core.agent_identity import brand_json_url
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Tenant
    from src.core.database.repositories.principal import PrincipalRepository

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(subdomain=CI_TEST_SUBDOMAIN)).first()
        if tenant is None:
            raise SystemExit(
                f"No tenant with subdomain {CI_TEST_SUBDOMAIN!r}. Run scripts.setup.init_database_ci first."
            )

        tenant.virtual_host = STORYBOARD_VIRTUAL_HOST
        session.flush()  # so brand_json_url() derives from the host just written
        tenant.capability_declarations = declarations(brand_json_url(tenant))
        print(f"Storyboard tenant {tenant.tenant_id} answers to {STORYBOARD_VIRTUAL_HOST}")
        print(f"   posture: required_for={list(REQUIRED_FOR)} covers_content_digest={COVERS_CONTENT_DIGEST}")

        principals = PrincipalRepository(session, tenant.tenant_id)
        counterparty = principals.find_by_agent_url(COUNTERPARTY_AGENT_URL)
        if counterparty is None:
            principals.create_with_token(
                COUNTERPARTY_PRINCIPAL_TOKEN,
                principal_id=COUNTERPARTY_PRINCIPAL_ID,
                name="Storyboard conformance runner",
                platform_mappings={"mock": {"advertiser_id": "storyboard-conformance-runner"}},
                agent_url=COUNTERPARTY_AGENT_URL,
            )
            print(f"   counterparty principal created at {COUNTERPARTY_AGENT_URL}")
        else:
            print(f"   counterparty principal already at {COUNTERPARTY_AGENT_URL}")

        _seed_webhook_storyboard_account(session, tenant.tenant_id)
        session.commit()


def _seed_webhook_storyboard_account(session: Session, tenant_id: str) -> None:
    """The account ``webhook_emission`` transacts against, plus the grant that exposes it.

    BOTH halves, and the grant is the half that is easy to miss: account resolution is
    scoped to the calling agent's accessible set (#1417), so a row without an
    ``AgentAccountAccess`` join fails ACCOUNT_NOT_FOUND *identically* to no row at all.
    Seeding one without the other looks like it worked and changes nothing.

    Idempotent on both halves — this script is re-run against a live stack.
    """
    from sqlalchemy import select

    from src.core.credentials import hash_token
    from src.core.database.models import Account, AgentAccountAccess
    from src.core.database.repositories.principal_lookup import find_principal_by_token_hash

    # The principal the RUNNER authenticates as. The grant has to name that principal,
    # not the counterparty one above: the counterparty is who the runner's SIGNATURE
    # establishes, while the account is read on the bearer's behalf.
    principal = find_principal_by_token_hash(session, hash_token(CI_TEST_TOKEN))
    if principal is None:
        raise SystemExit(
            f"No principal for the runner's bearer in tenant {tenant_id!r}. Run scripts.setup.init_database_ci first."
        )

    seeded: list[str] = []
    for brand_domain, operator in STORYBOARD_ACCOUNT_KEYS:
        for sandbox in STORYBOARD_SANDBOX_MODES:
            # Idempotent on the NATURAL KEY, not the id. The key is what the resolver
            # matches and what the database constrains as unique, so a row already holding
            # it satisfies the precondition whatever it is called -- and inserting a
            # second row under our own id would violate that constraint rather than being
            # a harmless no-op.
            existing = session.scalars(
                select(Account).filter_by(tenant_id=tenant_id, operator=operator, sandbox=sandbox)
            ).all()
            match = next((a for a in existing if (a.brand or {}) and _brand_domain_of(a) == brand_domain), None)
            if match is not None:
                seeded.append(match.account_id)
                continue
            account_id = _storyboard_account_id(brand_domain, sandbox)
            seeded.append(account_id)
            session.add(
                Account(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    name=f"Storyboard conformance account ({brand_domain}, sandbox={sandbox})",
                    status="active",
                    operator=operator,
                    brand={"domain": brand_domain},
                    sandbox=sandbox,
                )
            )
            session.flush()
    print(f"   storyboard accounts present: {len(seeded)}")

    # BOTH principals the runner can resolve as, because which one carries the request
    # depends on how it was authenticated. This tenant declares ``required_for`` signing,
    # so a SIGNED call resolves to the principal the signature establishes
    # (``COUNTERPARTY_PRINCIPAL_ID`` — the ``agent_url`` the verifier walks to), while an
    # unsigned call resolves to the one the BEARER names. Account resolution is scoped to
    # the RESOLVED principal's grants (#1417), so granting only the bearer leaves every
    # signed request answering ACCOUNT_NOT_FOUND — indistinguishably from no account row
    # at all, which is exactly how this went undiagnosed through one full run.
    for principal_id in sorted({principal.principal_id, COUNTERPARTY_PRINCIPAL_ID}):
        for account_id in seeded:
            grant = session.scalars(
                select(AgentAccountAccess).filter_by(
                    tenant_id=tenant_id, principal_id=principal_id, account_id=account_id
                )
            ).first()
            if grant is None:
                session.add(AgentAccountAccess(tenant_id=tenant_id, principal_id=principal_id, account_id=account_id))
                session.flush()
                print(f"   account access granted to {principal_id} on {account_id}")

    _seed_vector_product(session, tenant_id)
    _mint_tenant_signing_key(session, tenant_id)
    _assert_account_resolves(session, tenant_id, grantees=sorted({principal.principal_id, COUNTERPARTY_PRINCIPAL_ID}))


def _seed_vector_product(session: Session, tenant_id: str) -> None:
    """The product our CORRECTED request-signing bodies name, so their creates resolve.

    adcp#7567/#7583: the pinned vectors ship `create_media_buy` bodies no seller can parse,
    so `corrected_vectors.py` rewrites them from this repo's own conformant payload —
    which is built by `tests/factories/request.py`'s `PackageRequestFactory`, whose
    defaults are `product_id="prod-1"` and `pricing_option_id="cpm_usd_fixed"`.

    Nothing seeded those. Measured on the box: `[GET_PRODUCTS] Got 2 products` (the real
    catalog answers), then thirteen creates requesting `prod-1`, and thirteen
    `product miss: requested=['prod-1'] missing=['prod-1']`. The vectors were correct and
    the catalog was correct; they simply named different products.

    Read off the factory rather than restated, so a factory default that moves cannot
    leave this seeding the old id — the same rule the account seeding follows for the
    natural key it reads off the request builder.
    """
    from sqlalchemy import select

    from src.core.database.models import PricingOption, Product
    from tests.factories.product import DEFAULT_PRICING_OPTION_ID, ProductFactory
    from tests.factories.request import PackageRequestFactory

    product_id = PackageRequestFactory.product_id
    # `format_ids` is object-shaped ({agent_url, id}) and a DB trigger enforces it. Read
    # the factory's own default rather than restating an id: #1418 already moved this one
    # once, from "display_300x250" to the catalog's "display_300x250_image".
    format_ids = ProductFactory.format_ids.function()
    existing = session.scalars(select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)).first()
    if existing is not None:
        # Re-apply the capabilities rather than returning: a row left by an earlier
        # revision of this function would otherwise keep that revision's capabilities
        # forever, and the run would grade a product nobody in this tree described.
        existing.property_targeting_allowed = True
        session.flush()
        print(f"   vector product refreshed: {product_id}")
        return
    session.add(
        Product(
            tenant_id=tenant_id,
            product_id=product_id,
            name="Storyboard signed-vector product",
            description="Named by the corrected request-signing create_media_buy bodies.",
            format_ids=format_ids,
            targeting_template={},
            delivery_type="non_guaranteed",
            property_tags=["all_inventory"],
            # `media_buy_seller/inventory_list_targeting` buys this product with a
            # property_list overlay, and a product that forbids it is refused before the
            # storyboard's own subject is reached. Nothing about this product is a
            # restriction under test -- it exists so the signed vectors name something.
            property_targeting_allowed=True,
        )
    )
    session.flush()
    session.add(
        PricingOption.create(
            tenant_id=tenant_id,
            product_id=product_id,
            # The mock adapter simulates an ad server's capacity and refuses a CPM package
            # over 1,000,000 impressions (src/adapters/mock_ad_server.py). Impressions are
            # budget/rate*1000, so the RATE is what decides whether a storyboard-sized
            # budget fits: at 10.0 the corpus's 25,000 budget asked for 2,500,000 and was
            # refused on `packages[0].impressions` -- a fixture ceiling, not a contract.
            # 50.0 carries a budget up to 50,000 inside the same cap.
            rate=50.0,
            pricing_model="cpm",
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            pricing_option_id=DEFAULT_PRICING_OPTION_ID,
        )
    )
    session.flush()
    print(f"   vector product seeded: {product_id} ({DEFAULT_PRICING_OPTION_ID})")


def _mint_tenant_signing_key(session: Session, tenant_id: str) -> None:
    """Give the storyboard tenant a published signing key, through PRODUCTION's minter.

    ``webhook_emission`` requires the agent to publish "a JWKS at the ``jwks_uri`` on its
    ``brand.json`` ``agents[]`` entry containing a webhook-valid signing key"
    (webhook-emission.yaml). A tenant with no key publishes an EMPTY key set and the check
    fails for a fixture reason rather than a conformance one.

    ``request-signing`` is the only purpose this agent mints, deliberately:
    ``webhook-signing`` is deprecated pending removal (security.mdx "adcp_use", adcp#5555)
    and webhooks are signed with a request-signing key. The storyboard accepts either —
    it asks for ``adcp_use in {request-signing, webhook-signing}`` — so the narrower,
    spec-current choice satisfies it.

    Through ``provision_signing_key`` rather than a factory: minting is the behaviour under
    test elsewhere in this suite, and a fixture that mints differently from production would
    publish material production cannot sign with.
    """
    from datetime import UTC, datetime

    from src.core.database.repositories.signing_key import SigningKeyRepository
    from src.core.exceptions import AdCPSalesAgentError
    from src.core.signing.keys import provision_signing_key

    repo = SigningKeyRepository(session, tenant_id)
    if repo.active_at(now=datetime.now(UTC)) is not None:
        print("   tenant signing key already present")
        return
    try:
        minted_kid = provision_signing_key(repo, tenant_id=tenant_id, alg="ed25519")
    except AdCPSalesAgentError as exc:
        # NON-FATAL, deliberately, and this is the difference between an enhancement and a
        # regression. `db:` minting refuses without a deployment KEK — correctly, since
        # there is no plaintext fallback — and a deployment that configures none is a
        # legitimate one to grade. Failing the seeder there takes the ENTIRE storyboard
        # suite down (tox aborts commands_pre, no reports are written, and the run is
        # ungraded) to fix ONE check. Skipping costs only
        # `assert_webhook_signing_key_present`, which is exactly the check the key exists
        # to satisfy, and leaves every other storyboard measured.
        print(f"   tenant signing key NOT minted ({type(exc).__name__}); JWKS stays empty")
        return
    session.flush()
    print(f"   tenant signing key minted: {minted_kid}")


def _assert_account_resolves(session: Session, tenant_id: str, *, grantees: list[str]) -> None:
    """Read the seeded account back THROUGH THE RESOLVER, and fail loudly if it does not.

    Seeding that writes rows nobody can resolve is indistinguishable from not seeding: the
    server answers ACCOUNT_NOT_FOUND either way, and the storyboard reports it as a
    conformance failure rather than a fixture failure. This turns "seeded" from an
    assumption into a measurement, taken against the same repository method the request
    path uses and in the same database this script just wrote to.

    Stated as a precondition rather than a test because it guards a fixture: if it trips,
    every account-bearing storyboard is about to fail for a reason that has nothing to do
    with the agent's conformance, and the run is worthless. Better to stop here, where the
    message can name the cause.
    """
    from src.core.database.repositories.account import AccountRepository

    repo = AccountRepository(session, tenant_id)
    for principal_id in grantees:
        for brand_domain, operator in STORYBOARD_ACCOUNT_KEYS:
            for sandbox in STORYBOARD_SANDBOX_MODES:
                if repo.list_by_natural_key(
                    operator=operator,
                    brand_domain=brand_domain,
                    sandbox=sandbox,
                    principal_id=principal_id,
                ):
                    continue
                raise SystemExit(
                    f"Seeded the storyboard accounts, but {brand_domain}/{operator} "
                    f"sandbox={sandbox} does NOT resolve for principal {principal_id!r} in "
                    f"tenant {tenant_id!r}. Every storyboard naming it would fail "
                    f"ACCOUNT_NOT_FOUND as a fixture defect wearing the costume of a "
                    f"conformance failure."
                )
    print(
        f"   all {len(STORYBOARD_ACCOUNT_KEYS)} account keys x {len(STORYBOARD_SANDBOX_MODES)} "
        f"sandbox modes resolve for {len(grantees)} principal(s)"
    )


def main() -> None:
    """``seed`` by default; ``--env`` prints the settings for ``scripts/test-stack.sh``."""
    if "--env" in sys.argv[1:]:
        for name, value in signing_env().items():
            print(f"{name}={value}")
        return
    seed()


if __name__ == "__main__":
    main()
