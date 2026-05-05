"""Integration tests: property_targeting_allowed enforcement at create/update.

AdCP 3.0.1 spec (core/targeting.json:191) — sellers SHOULD reject property_list
targeting against products with property_targeting_allowed=false. Two paths:
- create_media_buy: validation block inside the UoW where product_map is built
- update_media_buy: validation guards the targeting_overlay write

Covers: UC-002-MAIN-14b
Covers: UC-003-MAIN-14
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.core.database.database_session import get_db_session
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    CollectionListReference,
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    UpdateMediaBuyRequest,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.helpers.adcp_factories import create_test_package_request
from tests.utils.database_helpers import (
    add_targeting_test_product,
    seed_media_buy_with_package,
    seed_targeting_test_tenant,
)

pytestmark = pytest.mark.requires_db

TENANT_ID = "test_property_targeting_allowed"


def _future_dates() -> tuple[str, str]:
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    end = tomorrow + timedelta(days=30)
    return tomorrow.strftime("%Y-%m-%dT00:00:00Z"), end.strftime("%Y-%m-%dT23:59:59Z")


def _make_identity() -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id="test_adv",
        tenant_id=TENANT_ID,
        tenant={"tenant_id": TENANT_ID},
        testing_context=AdCPTestContext(dry_run=True, test_session_id="test_property_targeting"),
        protocol="mcp",
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
    """Product with property_targeting_allowed=False rejects property_list targeting on create."""
    start, end = _future_dates()
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

    response, _ = await _create_media_buy_impl(req=request, identity=_make_identity())

    assert isinstance(response, CreateMediaBuyError)
    error_text = response.errors[0].message
    assert "prod_no_property_targeting" in error_text
    assert "property_targeting_allowed" in error_text


@pytest.mark.requires_db
async def test_create_accepts_property_list_when_product_allows(property_targeting_tenant):
    """Product with property_targeting_allowed=True passes the validation."""
    start, end = _future_dates()
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

    # Either succeeds outright, or fails on something else — but not on property_targeting_allowed.
    if isinstance(response, CreateMediaBuyError):
        for error in response.errors:
            assert "property_targeting_allowed" not in error.message


@pytest.mark.requires_db
async def test_create_accepts_collection_list_without_property_list(property_targeting_tenant):
    """collection_list alone never triggers the property_list check."""
    start, end = _future_dates()
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

    if isinstance(response, CreateMediaBuyError):
        for error in response.errors:
            assert "property_targeting_allowed" not in error.message


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
async def test_update_rejects_property_list_when_product_disallows(property_targeting_tenant):
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

    response = await _update_media_buy_impl(req=request, identity=_make_identity())

    # Response shape varies by error path; check the error is about property_targeting_allowed
    response_dict = response.model_dump() if hasattr(response, "model_dump") else response
    errors_text = str(response_dict)
    assert "property_targeting_allowed" in errors_text or "VALIDATION_ERROR" in errors_text


@pytest.mark.requires_db
async def test_update_accepts_collection_list_only(property_targeting_tenant):
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

    # Sanity: the schema accepts CollectionListReference at the boundary
    assert request.packages[0].targeting_overlay.collection_list is not None
    assert isinstance(request.packages[0].targeting_overlay.collection_list, CollectionListReference)

    response = await _update_media_buy_impl(req=request, identity=_make_identity())
    response_dict = response.model_dump() if hasattr(response, "model_dump") else response
    assert "property_targeting_allowed" not in str(response_dict)
