"""Seed the storyboard conformance tenant, from the pinned bundle's own test kit.

Run by the ``storyboard`` tox env's ``commands_pre``, from the TESTS container, with
``DATABASE_URL`` pointed at the stack's database. Deliberately not part of
``init_database_ci``: that script runs inside the server image too, where the bundle is
absent, and this needs the bundle. Splitting them is what lets this refuse rather than
degrade.

WHY THIS TENANT EXISTS AT ALL. The agent card publishes a tenant's STORED host
(``canonical_agent_url``), and the e2e stack runs two agent fronts against one database.
The CI tenant declares no ``virtual_host``, so with its identity the card advertised
``ci-test.<SALES_AGENT_DOMAIN>`` — a name nothing on the compose network answers. A2A is
card-first: the runner fetched the card, followed that URL, and every check errored
``getaddrinfo ENOTFOUND`` — 0 passed, 64 failed, 25 of 72 storyboards executed, while MCP
kept 30/21/249 because it reads no card. One tenant per front keeps each card true about
the agent that served it, and ``virtual_host`` is unique, so the two cannot collide.

WHY THE INVENTORY IS READ, NOT WRITTEN HERE. 49 of the 52 pinned storyboards name
``test-kits/acme-outdoor.yaml`` as their ``prerequisites.test_kit``; it is the seller-side
fixture contract, and it is data in the bundle. A copy of it in this file would be wrong
the day the pin moves, and wrong SILENTLY: the rows would still seed, the storyboard would
still run, and only the match it grades would stop matching. So the domains come out of the
kit, and the version of the kit is the same pin the runner grades against —
``storyboard_spec.pinned_version`` is ``adcp.get_adcp_spec_version()`` (the installed
wheel's own answer) and ``adcp_home`` prefers the published, sha256-verified release bundle
the conformance job downloads. One artifact, both sides.

SCOPE: THIS TENANT ONLY. Nothing here touches the CI tenant, the isolation tenant or the
demo tenant. The kit describes one buyer against one seller, and its inventory is seeded
for the tenant the storyboard addresses and no other — a seller that carried
``acme-outdoor``'s properties everywhere would make every other suite's inventory
assertions depend on a conformance fixture.
"""

import sys
from pathlib import Path

# Add the project root directory to Python path to ensure imports work
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


#: The subdomain the storyboard tenant answers on, and the value the runner puts in
#: ``x-adcp-tenant``. Owned HERE and imported by
#: ``tests/storyboard/test_storyboard_conformance.py``, because this script is what makes
#: it true in the database: the tenant_id is a fresh uuid4 per seed, so the subdomain is
#: the only stable spelling and a second literal is a silent 401 the day one of them moves.
STORYBOARD_SUBDOMAIN = "storyboard"

#: The host the storyboard agent is ACTUALLY served on: ``adcp-server-storyboard``, behind
#: the shared tls-proxy whose ``map $ssl_server_name`` routes this SNI name to it
#: (config/nginx/nginx-tls-test.conf.template, docker-compose.e2e.yml). Covered by the
#: generated ``*.adcp.test`` wildcard certificate.
#: THE ORIGIN, PORT INCLUDED. This is the string the agent card publishes, and an A2A
#: client connects to what the card says: advertising ``https://storyboard.adcp.test/a2a``
#: for an agent listening on 8443 points every client at a closed port, which took the A2A
#: conformance axis from 27 passing checks to zero. The port comes off at the two reads
#: that want a hostname -- the tenant lookup and ``Tenant.primary_domain``, whose
#: ``publisher_domain`` pattern admits no colon (``config_loader.hostname_of``).
STORYBOARD_VIRTUAL_HOST = "storyboard.adcp.test:8443"

#: The credential the runner presents. Distinct from the CI token because a principal
#: belongs to exactly one tenant: the resolver looks a token up INSIDE the detected tenant,
#: so this token is only valid inside this tenant. ``tox.ini``'s STORYBOARD_AUTH_TOKEN
#: default carries a literal copy, as its own comment says, because an ini file cannot
#: import a Python constant — the two must move together.
STORYBOARD_TOKEN = "storyboard-test-token"


#: The test kit 49 of the 52 pinned storyboards name as their ``prerequisites.test_kit``,
#: relative to the bundle's ``compliance/`` root.
STORYBOARD_TEST_KIT = "test-kits/acme-outdoor.yaml"


def kit_property_domains() -> tuple[str, ...]:
    """The domains ``acme-outdoor.yaml`` says this seller's inventory must contain.

    From ``inventory_targets.matching_properties.expected_identifiers``.
    ``media_buy_seller/inventory_list_targeting`` resolves a PropertyListReference against
    the seller's own inventory and grades the match, so these have to be rows — but WHICH
    domains is the kit's decision.

    The kit's ``no_match_properties`` / ``no_match_collections`` siblings are deliberately
    NOT read: ``inventory_list_no_match`` grades that a list naming inventory this seller
    does not carry resolves to a truthful ZERO, so seeding those would break the scenario
    that needs them absent.

    RAISES when the bundle does not resolve. On this path that is a hard prerequisite, not
    a degradation: the suite's own ``_bundle_gate`` already FAILS rather than skips when the
    bundle is missing, so seeding silently without inventory would just move the same
    failure later and make it look like a conformance gap.
    """
    import yaml

    from scripts.audit import storyboard_spec

    version = storyboard_spec.pinned_version(project_root)
    kit_path = storyboard_spec.adcp_home(project_root, version) / "compliance" / STORYBOARD_TEST_KIT
    if not kit_path.is_file():
        raise FileNotFoundError(
            f"pinned test kit not found at {kit_path}. The storyboard tenant's inventory is READ from "
            f"the bundle, so there is nothing to seed without it — run .github/actions/_adcp-bundle."
        )

    kit = yaml.safe_load(kit_path.read_text(encoding="utf-8"))
    identifiers = ((kit.get("inventory_targets") or {}).get("matching_properties") or {}).get(
        "expected_identifiers"
    ) or []
    domains = tuple(i["value"] for i in identifiers if i.get("type") == "domain" and i.get("value"))
    if not domains:
        raise ValueError(
            f"{STORYBOARD_TEST_KIT} declares no matching_properties domain identifiers; "
            f"inventory_list_targeting cannot match anything this seller carries."
        )
    return domains


def kit_account_references() -> tuple[tuple[str, str, bool], ...]:
    """The (brand_domain, operator, sandbox) accounts the storyboards will ASK FOR.

    Read from the bundle, for the same reason the inventory is: a hand-written copy is
    wrong the day the pin moves, and wrong silently. This one was worse than that -- it was
    wrong on the day it was written (``acme-outdoor.example`` for a kit whose brand domain
    is ``acmeoutdoor.example``, and the brand domain again where the operator goes), and the
    symptom was 251 storyboard steps answering ACCOUNT_NOT_FOUND about products they never
    reached.

    THE DERIVATION. Every step's ``sample_request.account`` is the exact reference the
    runner will send. A reference is ours to seed when its brand domain is one a test kit
    DECLARES (``brand.house.domain``) -- that is what "this seller implements the kit"
    means. ``otherbrand.example`` belongs to no kit, so it is not seeded and stays
    ACCOUNT_NOT_FOUND, which is what the isolation steps that send it grade.

    SANDBOX IS PART OF THE KEY, not a flag. ``AccountReference.sandbox`` defaults to false
    and ``_scope_natural_key`` matches NULL-or-false for it, so a brand asked for both ways
    needs TWO accounts -- which is exactly why the bundle ships ``acme-outdoor.yaml``
    (sandbox) and ``acme-outdoor-live.yaml`` (live) under one brand domain.

    A bare ``{"sandbox": true}`` reference is skipped by construction: ``AccountReference``
    requires brand and operator, so such a payload is not an account reference at the pin
    and has no natural key to seed.
    """
    import yaml

    from scripts.audit import storyboard_spec

    version = storyboard_spec.pinned_version(project_root)
    compliance = storyboard_spec.adcp_home(project_root, version) / "compliance"
    if not compliance.is_dir():
        raise FileNotFoundError(
            f"pinned compliance bundle not found at {compliance}. The storyboard tenant's accounts are READ "
            f"from it, so there is nothing to seed without it — run .github/actions/_adcp-bundle."
        )

    kit_domains = set()
    for kit_path in sorted((compliance / "test-kits").glob("*.yaml")):
        kit = yaml.safe_load(kit_path.read_text(encoding="utf-8")) or {}
        domain = ((kit.get("brand") or {}).get("house") or {}).get("domain")
        if domain:
            kit_domains.add(domain)

    references: set[tuple[str, str, bool]] = set()
    for path in sorted((compliance / "domains").rglob("*.yaml")):
        try:
            board = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(board, dict) or "phases" not in board:
            continue
        for phase in board.get("phases") or []:
            for step in phase.get("steps") or []:
                account = (step.get("sample_request") or {}).get("account") or {}
                domain = (account.get("brand") or {}).get("domain")
                operator = account.get("operator")
                if domain in kit_domains and operator:
                    references.add((domain, operator, bool(account.get("sandbox"))))

    if not references:
        raise ValueError(
            "no storyboard step names an account whose brand a test kit declares; "
            "every account-bearing step would answer ACCOUNT_NOT_FOUND."
        )
    return tuple(sorted(references))


def account_id_for(brand_domain: str, sandbox: bool) -> str:
    """A stable id for a derived account. The natural key is what resolves it; this is the
    handle the grant and the row need, so it only has to be deterministic."""
    return f"{brand_domain.replace('.', '-')}-{'sandbox' if sandbox else 'live'}"


def seed_storyboard_tenant() -> str:
    """Create (or converge) the storyboard tenant and everything it needs. Returns its id."""
    import uuid
    from datetime import UTC, datetime

    from adcp.types import BrandReference
    from sqlalchemy import select

    from scripts.setup.seed_products import seed_product
    from src.core.credentials import hash_token
    from src.core.database.database_session import get_db_session
    from src.core.database.models import (
        Account,
        AgentAccountAccess,
        AuthorizedProperty,
        CurrencyLimit,
        Product,
        PropertyTag,
        Tenant,
    )
    from src.core.database.repositories.principal import PrincipalRepository
    from src.core.database.repositories.principal_lookup import find_principal_by_token_hash

    # Resolved BEFORE any write: a missing bundle is a refusal, and refusing after seeding
    # a tenant would leave the database half-configured for the next run to inherit.
    domains = kit_property_domains()
    accounts = kit_account_references()

    print("=" * 60)
    print(f"Seeding storyboard conformance tenant ({STORYBOARD_SUBDOMAIN}) at {STORYBOARD_VIRTUAL_HOST}")
    print(f"Inventory from the pinned bundle's {STORYBOARD_TEST_KIT}: {len(domains)} properties")
    print(f"Accounts derived from the bundle's storyboards: {len(accounts)}")
    print("=" * 60)

    with get_db_session() as session:
        stmt = select(Tenant).filter_by(subdomain=STORYBOARD_SUBDOMAIN)
        existing = session.scalars(stmt).first()

        if existing:
            tenant_id = existing.tenant_id
            # Re-assert the field this tenant exists FOR, so a row seeded before it was
            # added converges instead of silently keeping the old identity.
            if existing.virtual_host != STORYBOARD_VIRTUAL_HOST:
                existing.virtual_host = STORYBOARD_VIRTUAL_HOST
                session.flush()
                print(f"  ✓ Re-asserted virtual_host: {STORYBOARD_VIRTUAL_HOST}")
            print(f"  ✓ Tenant already exists (ID: {tenant_id})")
        else:
            tenant_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            session.add(
                Tenant(
                    tenant_id=tenant_id,
                    name="Storyboard Conformance Tenant",
                    subdomain=STORYBOARD_SUBDOMAIN,
                    # The whole reason this tenant exists: tenant detection matches Host
                    # exactly, and canonical_agent_url publishes this string on the card.
                    virtual_host=STORYBOARD_VIRTUAL_HOST,
                    billing_plan="test",
                    ad_server="mock",
                    enable_axe_signals=False,
                    is_active=True,
                    authorized_emails=["storyboard@example.com"],
                    authorized_domains=None,
                    policy_settings=None,
                    signals_agent_config=None,
                    ai_policy=None,
                    auto_approve_format_ids=["display_300x250", "display_728x90", "video_30s"],
                    human_review_required=False,
                    auth_setup_mode=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            try:
                session.commit()
                print(f"  ✓ Created tenant (ID: {tenant_id})")
            except Exception as e:
                session.rollback()
                print(f"  ⚠️  Tenant race condition: {e}")
                existing = session.scalars(stmt).first()
                if not existing:
                    raise ValueError("Failed to create or find the storyboard tenant") from e
                tenant_id = existing.tenant_id

        # Its own principal: a token resolves only INSIDE its tenant.
        existing_principal = find_principal_by_token_hash(session, hash_token(STORYBOARD_TOKEN))
        if not existing_principal:
            principal_id = str(uuid.uuid4())
            PrincipalRepository(session, tenant_id).create_with_token(
                STORYBOARD_TOKEN,
                principal_id=principal_id,
                name="Storyboard Conformance Principal",
                platform_mappings={"mock": {"advertiser_id": "storyboard-advertiser"}},
            )
            try:
                session.commit()
                print(f"  ✓ Created principal (ID: {principal_id})")
            except Exception as e:
                session.rollback()
                print(f"  ⚠️  Principal race condition: {e}")
                principal_id = find_principal_by_token_hash(session, hash_token(STORYBOARD_TOKEN)).principal_id
        else:
            principal_id = existing_principal.principal_id
            print(f"  ✓ Principal already exists (ID: {principal_id})")

        # THE ACCOUNTS THE RUNNER WILL ASK FOR, and a grant for each. Resolution is
        # access-scoped, so an account row alone answers AUTHORIZATION_ERROR — the grant is
        # what makes it resolvable.
        for brand_domain, operator, sandbox in accounts:
            account_id = account_id_for(brand_domain, sandbox)
            if not session.scalars(select(Account).filter_by(tenant_id=tenant_id, account_id=account_id)).first():
                session.add(
                    Account(
                        tenant_id=tenant_id,
                        account_id=account_id,
                        name=f"{brand_domain} via {operator}",
                        status="active",
                        operator=operator,
                        brand=BrandReference(domain=brand_domain),
                        sandbox=sandbox,
                    )
                )
                # Composite FKs with no ORM relationship: the unit of work has nothing to
                # order the INSERTs by, so the parent must be on the database first.
                session.flush()

            if not session.scalars(
                select(AgentAccountAccess).filter_by(
                    tenant_id=tenant_id, principal_id=principal_id, account_id=account_id
                )
            ).first():
                session.add(AgentAccountAccess(tenant_id=tenant_id, principal_id=principal_id, account_id=account_id))
        print(f"  ✓ {len(accounts)} account(s) + grants: {', '.join(account_id_for(b, s) for b, _, s in accounts)}")

        now = datetime.now(UTC)
        # Budget validation refuses a currency with no limit row, and
        # media_buy_seller/pricing_currency_filter reads a multi-currency catalog.
        for currency_code in ("USD", "EUR"):
            if not session.scalars(
                select(CurrencyLimit).filter_by(tenant_id=tenant_id, currency_code=currency_code)
            ).first():
                session.add(
                    CurrencyLimit(
                        tenant_id=tenant_id,
                        currency_code=currency_code,
                        min_package_budget=500.0,
                        max_daily_package_spend=50000.0,
                    )
                )

        if not session.scalars(select(PropertyTag).filter_by(tenant_id=tenant_id, tag_id="all_inventory")).first():
            session.add(
                PropertyTag(
                    tag_id="all_inventory",
                    tenant_id=tenant_id,
                    name="All Inventory",
                    description="Default tag for the storyboard tenant",
                    created_at=now,
                    updated_at=now,
                )
            )

        for domain in domains:
            property_id = domain.replace(".", "_")
            if not session.scalars(
                select(AuthorizedProperty).filter_by(tenant_id=tenant_id, property_id=property_id)
            ).first():
                session.add(
                    AuthorizedProperty(
                        tenant_id=tenant_id,
                        property_id=property_id,
                        property_type="website",
                        name=domain,
                        identifiers=[{"type": "domain", "value": domain}],
                        publisher_domain=domain,
                        verification_status="verified",
                    )
                )

        try:
            session.commit()
            print(f"  ✓ 2 currency limits, property tag, {len(domains)} authorized properties: {', '.join(domains)}")
        except Exception as e:
            session.rollback()
            print(f"  ⚠️  Prerequisites race condition: {e}")

        # A CATALOGUE. Measured, not assumed: moving the runner from the CI tenant to this
        # one with an empty catalogue lost four checks that had been passing —
        # error_compliance::nonexistent_product, error_compliance::reversed_dates_error,
        # governance_conditions::get_products_brief and refine_products::get_products_brief
        # — and left inventory_list_targeting failing, because get_products_brief is the
        # FIRST step of those storyboards and everything downstream depends on it. The CI
        # tenant's two products had been carrying every product-dependent storyboard
        # invisibly; a tenant of this suite's own has to carry them itself.
        #
        # The test kit declares no products: per-storyboard catalogue state is what
        # comply_test_controller seeds (#1834), and half the 249 skipped checks wait on it.
        # These two are the generic catalogue a buy flow needs to get off the ground, the
        # same shape the CI tenant carries.
        products = [
            {
                "product_id": "storyboard_display_premium",
                "name": "Premium Display Advertising",
                "description": "High-impact display ads across premium content",
                "formats": [
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
                ],
                "targeting_template": {"geo": ["US"], "device_type": "any"},
                "delivery_type": "guaranteed",
                "pricing": {"model": "cpm", "rate": 15.0, "is_fixed": True},
            },
            {
                "product_id": "storyboard_video_premium",
                "name": "Premium Video Advertising",
                "description": "Pre-roll video ads with guaranteed completion rates",
                "formats": [
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_30s"},
                ],
                "targeting_template": {"geo": ["US"], "device_type": "any"},
                "delivery_type": "guaranteed",
                "pricing": {"model": "cpm", "rate": 25.0, "is_fixed": True},
            },
        ]
        for p in products:
            if seed_product(session, tenant_id, p):
                print(f"  ✓ Created product: {p['name']}")
            else:
                print(f"  ℹ️  Product already exists: {p['name']}")

        session.commit()
        seeded = session.scalars(select(Product).filter_by(tenant_id=tenant_id)).all()
        # Loudly, because an empty catalogue is exactly the failure this block exists to
        # prevent and it shows up four storyboards later as a conformance gap.
        if not seeded:
            raise ValueError("storyboard tenant has no products; every get_products storyboard will fail")
        print(f"  ✓ Catalogue: {len(seeded)} products")

    print("✅ Storyboard tenant ready")
    return tenant_id


if __name__ == "__main__":
    try:
        seed_storyboard_tenant()
    except Exception as e:
        print(f"Error seeding the storyboard tenant: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
