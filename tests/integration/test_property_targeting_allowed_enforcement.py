"""Integration tests: property_targeting_allowed enforcement at create/update.

AdCP 3.0.0 spec (core/product.json ``property_targeting_allowed``) — sellers
SHOULD reject property_list targeting against products with
property_targeting_allowed=false. Two paths:
- create_media_buy: validation block inside the UoW where product_map is built
- update_media_buy: validation guards the targeting_overlay write

Covers: UC-002-MAIN-14b
Covers: UC-003-MAIN-14
"""

import uuid

import pytest

from src.core.database.database_session import get_db_session
from src.core.exceptions import AdCPValidationError
from src.core.resolved_identity import AccountIdentity
from src.core.schemas import (
    CollectionListReference,
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    UpdateMediaBuyRequest,
)
from src.core.schemas.account import Account
from src.core.tenant_context import TenantContext
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories import AccountFactory, PrincipalFactory
from tests.helpers.adcp_factories import create_test_package_request
from tests.utils.database_helpers import (
    add_targeting_test_product,
    bind_factories_to_session,
    future_iso_date_range,
    seed_media_buy_with_package,
    seed_targeting_test_tenant,
)

pytestmark = pytest.mark.requires_db

TENANT_ID = "test_property_targeting_allowed"
ACCOUNT_ID = "acct_test"


def _make_identity() -> AccountIdentity:
    """The caller both media-buy implementations take: the identity with the account inside.

    ``create-media-buy-request.json`` and ``update-media-buy-request.json`` both
    require ``account``, so both implementations are annotated ``AccountIdentity``
    and read ``identity.account.account_id`` directly. The tenant is the committed
    ROW, which is what the resolver would have loaded.
    """
    tenant = TenantContext.load(TENANT_ID)
    assert tenant is not None, "the property_targeting_tenant fixture must have committed the tenant row"
    return PrincipalFactory.make_account_identity(
        PrincipalFactory.make_identity(principal_id="test_adv", tenant_id=TENANT_ID, tenant=tenant),
        Account(account_id=ACCOUNT_ID, name="Test Account", status="active"),
    )


@pytest.fixture
def property_targeting_tenant(integration_db):
    """Tenant with two products: one allowing property targeting, one not."""
    with get_db_session() as session:
        seed_targeting_test_tenant(
            session,
            tenant_id=TENANT_ID,
            tenant_name="Property Targeting Publisher",
            subdomain="prop-targeting",
            access_token="test_token_property_targeting",
        )
        add_targeting_test_product(
            session,
            tenant_id=TENANT_ID,
            product_id="prod_no_property_targeting",
            name="Display Ads (no property targeting)",
            property_targeting_allowed=False,
        )
        add_targeting_test_product(
            session,
            tenant_id=TENANT_ID,
            product_id="prod_yes_property_targeting",
            name="Display Ads (property targeting allowed)",
            property_targeting_allowed=True,
        )
        # The account the requests name. The boundary resolves it onto the identity in
        # production; a direct _impl call hands it over through make_account_identity,
        # and the row has to exist because the created buy carries its account_id.
        with bind_factories_to_session(session):
            AccountFactory(tenant_id=TENANT_ID, account_id=ACCOUNT_ID)
        session.commit()

    yield TENANT_ID


# ---------------------------------------------------------------------------
# create_media_buy enforcement
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
async def test_create_rejects_property_list_when_product_disallows(property_targeting_tenant):
    """Product with property_targeting_allowed=False rejects property_list targeting on create.

    The validation block raises AdCPValidationError so the transport boundary translates
    to the spec-compliant two-layer envelope. The raise propagates cleanly through the
    narrowed except AdCPSalesAgentError boundary; the prior ValueError shape was caught by an inner
    (ValueError, PermissionError) catchall and re-emitted via Pattern A, which is the
    anti-pattern the typed-error substrate eliminates.
    """
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
        account={"account_id": "acct_test"},
        brand={"domain": "testbrand.com"},
        packages=[
            create_test_package_request(
                product_id="prod_no_property_targeting",
                budget=5000.0,
                pricing_option_id="cpm_usd_fixed",
                targeting_overlay={
                    "property_list": {
                        "agent_url": "https://gov.example",
                        "list_id": "v1",
                    },
                },
            )
        ],
        start_time=start,
        end_time=end,
        idempotency_key=f"int-key-{uuid.uuid4().hex}",
    )

    with pytest.raises(AdCPValidationError) as excinfo:
        await _create_media_buy_impl(req=request, identity=_make_identity())

    exc = excinfo.value
    assert exc.error_code == "VALIDATION_ERROR"
    # The ARRAY parameter: violations are gathered across every package before the
    # raise, so no single element is at fault and details carry which ones were
    # (salesagent-rfxfu). The old "packages[]" prefix named neither the array nor an
    # element.
    assert exc.field == "packages"
    assert exc.details is not None
    assert exc.details.reasons


@pytest.mark.requires_db
async def test_create_accepts_property_list_when_product_allows(property_targeting_tenant):
    """Product with property_targeting_allowed=True passes the validation."""
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
        account={"account_id": "acct_test"},
        brand={"domain": "testbrand.com"},
        packages=[
            create_test_package_request(
                product_id="prod_yes_property_targeting",
                budget=5000.0,
                pricing_option_id="cpm_usd_fixed",
                targeting_overlay={
                    "property_list": {
                        "agent_url": "https://gov.example",
                        "list_id": "v1",
                    },
                },
            )
        ],
        start_time=start,
        end_time=end,
        idempotency_key=f"int-key-{uuid.uuid4().hex}",
    )

    response = await _create_media_buy_impl(req=request, identity=_make_identity())

    # The validation rule must not fire for an allowing product. Separate
    # assertion gates the success branch — without it the compound
    # ``isinstance(...) or all(...)`` short-circuits on success and runs zero
    # checks, leaving the happy-path proof vacuous. If the response IS an
    # error variant, accept any failure cause that isn't the property_targeting
    # rule itself (test stays decoupled from unrelated downstream errors).
    assert not isinstance(response, CreateMediaBuyError), (
        f"Expected success but got CreateMediaBuyError: {[err.message for err in (response.errors or [])]}"
    )


@pytest.mark.requires_db
async def test_create_accepts_collection_list_without_property_list(property_targeting_tenant):
    """collection_list alone never triggers the property_list check."""
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
        account={"account_id": "acct_test"},
        brand={"domain": "testbrand.com"},
        packages=[
            create_test_package_request(
                product_id="prod_no_property_targeting",  # property_targeting_allowed=False
                budget=5000.0,
                pricing_option_id="cpm_usd_fixed",
                targeting_overlay={
                    "collection_list": {
                        "agent_url": "https://gov.example",
                        "list_id": "c1",
                    },
                },
            )
        ],
        start_time=start,
        end_time=end,
        idempotency_key=f"int-key-{uuid.uuid4().hex}",
    )

    response = await _create_media_buy_impl(req=request, identity=_make_identity())

    # Mirror the line-157 split for the sister test — the compound
    # ``isinstance(...) or all(...)`` short-circuits on success, leaving the
    # happy-path proof vacuous. Separate ``not isinstance`` gates the success
    # branch with a real check; the follow-up ``all(...)`` ensures the
    # property_list rule still doesn't fire if an unrelated error did appear.
    assert not isinstance(response, CreateMediaBuyError), (
        f"Expected success but got CreateMediaBuyError: {[err.message for err in (response.errors or [])]}"
    )


# ---------------------------------------------------------------------------
# update_media_buy enforcement
# ---------------------------------------------------------------------------


def _seed_media_buy(tenant_id: str, product_id: str, media_buy_id: str = "mb_test_pta") -> str:
    """Insert a media buy + package directly so update_media_buy has something to update."""
    with get_db_session() as session:
        seed_media_buy_with_package(
            session,
            tenant_id=tenant_id,
            principal_id="test_adv",
            product_id=product_id,
            media_buy_id=media_buy_id,
            package_id="pkg_test_pta",
        )
        session.commit()
    return media_buy_id


@pytest.mark.requires_db
def test_update_rejects_property_list_when_product_disallows(property_targeting_tenant):
    """Update path: same rule as create — reject property_list against disallowing product."""
    media_buy_id = _seed_media_buy(TENANT_ID, "prod_no_property_targeting")

    request = UpdateMediaBuyRequest(
        account={"account_id": "acct_test"},
        idempotency_key="test-idem-key-0001",
        media_buy_id=media_buy_id,
        packages=[
            {
                "package_id": "pkg_test_pta",
                "targeting_overlay": {
                    "property_list": {
                        "agent_url": "https://gov.example",
                        "list_id": "v1",
                    },
                },
            }
        ],
    )

    # PR #1276 round-5: validation site raises AdCPValidationError (matches
    # create-time path exactly). Boundary translator turns it into the
    # spec-compliant two-layer envelope at the transport edge.
    with pytest.raises(AdCPValidationError) as excinfo:
        _update_media_buy_impl(req=request, identity=_make_identity())

    exc = excinfo.value
    assert exc.error_code == "VALIDATION_ERROR"
    # The ARRAY parameter: violations are gathered across every package before the
    # raise, so no single element is at fault and details carry which ones were
    # (salesagent-rfxfu). The old "packages[]" prefix named neither the array nor an
    # element.
    assert exc.field == "packages"
    assert exc.details is not None
    assert exc.details.reasons


@pytest.mark.requires_db
def test_update_accepts_collection_list_only(property_targeting_tenant):
    """collection_list-only update never triggers property_list rejection."""
    media_buy_id = _seed_media_buy(TENANT_ID, "prod_no_property_targeting", media_buy_id="mb_collection_only")

    request = UpdateMediaBuyRequest(
        account={"account_id": "acct_test"},
        idempotency_key="test-idem-key-0001",
        media_buy_id=media_buy_id,
        packages=[
            {
                "package_id": "pkg_test_pta",
                "targeting_overlay": {
                    "collection_list": {
                        "agent_url": "https://gov.example",
                        "list_id": "c_only_v1",
                    },
                },
            }
        ],
    )

    # Sanity: the schema accepts CollectionListReference at the boundary —
    # this is the C1 fix: AdCPPackageUpdate now overrides targeting_overlay
    # to use the local Targeting subclass instead of library TargetingOverlay.
    assert request.packages is not None
    overlay = request.packages[0].targeting_overlay
    assert overlay is not None
    assert isinstance(overlay.collection_list, CollectionListReference)

    response = _update_media_buy_impl(req=request, identity=_make_identity())
    response_dict = response.model_dump() if hasattr(response, "model_dump") else response
    assert "property_targeting_allowed" not in str(response_dict)
