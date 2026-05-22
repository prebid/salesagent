"""Integration tests: property_targeting_allowed enforcement at create/update.

AdCP 3.0.6 spec (core/targeting.json:191) — sellers SHOULD reject property_list
targeting against products with property_targeting_allowed=false. Two paths:
- create_media_buy: validation block inside the UoW where product_map is built
- update_media_buy: validation guards the targeting_overlay write

Covers: UC-002-MAIN-14b
Covers: UC-003-MAIN-14
"""

import pytest

from src.core.database.database_session import get_db_session
from src.core.exceptions import AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    CollectionListReference,
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    UpdateMediaBuyRequest,
)
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories import PrincipalFactory
from tests.helpers.adcp_factories import create_test_package_request
from tests.utils.database_helpers import (
    add_targeting_test_product,
    future_iso_date_range,
    seed_media_buy_with_package,
    seed_targeting_test_tenant,
)

pytestmark = pytest.mark.requires_db

TENANT_ID = "test_property_targeting_allowed"


def _make_identity() -> ResolvedIdentity:
    return PrincipalFactory.make_identity(
        principal_id="test_adv",
        tenant_id=TENANT_ID,
        protocol="mcp",
        dry_run=True,
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
        session.commit()

    yield TENANT_ID


# ---------------------------------------------------------------------------
# create_media_buy enforcement
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
async def test_create_rejects_property_list_when_product_disallows(property_targeting_tenant):
    """Product with property_targeting_allowed=False rejects property_list targeting on create.

    The validation block raises AdCPValidationError so the transport boundary translates
    to the spec-compliant two-layer envelope. The previous raw ValueError shape was caught
    by an inner (ValueError, PermissionError) catchall and re-emitted via Pattern A
    (Error(code=...) construction in _impl) — anti-pattern that the error-emission
    architecture work eliminates. After PR #1306 / PR #1307 land, this raise propagates
    cleanly through the narrowed except AdCPError boundary.
    """
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
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
    )

    with pytest.raises(AdCPValidationError) as excinfo:
        await _create_media_buy_impl(req=request, identity=_make_identity())

    exc = excinfo.value
    assert "prod_no_property_targeting" in exc.message
    assert "property_targeting_allowed" in exc.message
    assert exc.error_code == "VALIDATION_ERROR"
    assert exc.field == "packages[].targeting_overlay.property_list"
    assert exc.details is not None
    assert "violations" in exc.details


@pytest.mark.requires_db
async def test_create_accepts_property_list_when_product_allows(property_targeting_tenant):
    """Product with property_targeting_allowed=True passes the validation."""
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
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
    )

    response, _ = await _create_media_buy_impl(req=request, identity=_make_identity())

    # The validation rule must not fire for an allowing product. Separate
    # assertion gates the success branch — without it the compound
    # ``isinstance(...) or all(...)`` short-circuits on success and runs zero
    # checks, leaving the happy-path proof vacuous. If the response IS an
    # error variant, accept any failure cause that isn't the property_targeting
    # rule itself (test stays decoupled from unrelated downstream errors).
    assert not isinstance(
        response, CreateMediaBuyError
    ), f"Expected success but got CreateMediaBuyError: {[err.message for err in (response.errors or [])]}"
    assert all("property_targeting_allowed" not in err.message for err in (response.errors or []))


@pytest.mark.requires_db
async def test_create_accepts_collection_list_without_property_list(property_targeting_tenant):
    """collection_list alone never triggers the property_list check."""
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
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
    )

    response, _ = await _create_media_buy_impl(req=request, identity=_make_identity())

    # Mirror the line-157 split for the sister test — the compound
    # ``isinstance(...) or all(...)`` short-circuits on success, leaving the
    # happy-path proof vacuous. Separate ``not isinstance`` gates the success
    # branch with a real check; the follow-up ``all(...)`` ensures the
    # property_list rule still doesn't fire if an unrelated error did appear.
    assert not isinstance(
        response, CreateMediaBuyError
    ), f"Expected success but got CreateMediaBuyError: {[err.message for err in (response.errors or [])]}"
    assert all("property_targeting_allowed" not in err.message for err in (response.errors or []))


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
    assert exc.field == "packages[].targeting_overlay.property_list"
    assert "property_targeting_allowed" in exc.message
    assert exc.details is not None and "violations" in exc.details


@pytest.mark.requires_db
def test_update_accepts_collection_list_only(property_targeting_tenant):
    """collection_list-only update never triggers property_list rejection."""
    media_buy_id = _seed_media_buy(TENANT_ID, "prod_no_property_targeting", media_buy_id="mb_collection_only")

    request = UpdateMediaBuyRequest(
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
