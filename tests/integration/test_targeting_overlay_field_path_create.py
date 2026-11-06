"""Integration tests: targeting_overlay refusal field is a path, not a bare string. GH #2410.

AdCP 3.1.1 core/error.json `field`: "Field path associated with the error in JSONPath-lite
format (e.g., 'packages[0].targeting')." create_media_buy raised a bare "targeting_overlay"
on a geo include/exclude overlap -- not a path from the request root, and silent on which
package of a multi-package request was refused.

No storyboard asserts this field's value for the geo-overlap scenario (verified against
T-UC-002-ext-f-geo in BR-UC-002-create-media-buy.feature, which checks error_code, recovery,
details.geo_overlaps and the suggestion field, never errors[0].field), so this is a correction
to an ungraded value, not a change to a graded contract.
"""

import uuid

import pytest

from src.core.database.database_session import get_db_session
from src.core.exceptions import AdCPInvalidRequestError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import CreateMediaBuyRequest
from src.core.tools.media_buy_create import _create_media_buy_impl
from tests.helpers.adcp_factories import create_test_package_request
from tests.utils.database_helpers import add_targeting_test_product, future_iso_date_range, seed_targeting_test_tenant

pytestmark = pytest.mark.requires_db

TENANT_ID = "test_targeting_overlay_field_path"


@pytest.fixture
def targeting_tenant(integration_db):
    """One tenant with two display products — enough for a two-package request."""
    with get_db_session() as session:
        seed_targeting_test_tenant(
            session,
            tenant_id=TENANT_ID,
            tenant_name="Targeting Field Path Publisher",
            subdomain="targeting-field-path",
            access_token="test_token",
        )
        add_targeting_test_product(session, TENANT_ID, "prod_display_a")
        add_targeting_test_product(session, TENANT_ID, "prod_display_b")
        session.commit()

    yield TENANT_ID


def _make_identity() -> ResolvedIdentity:
    from tests.factories import PrincipalFactory

    return PrincipalFactory.make_identity(principal_id="test_adv", tenant_id=TENANT_ID)


async def test_geo_overlap_on_the_only_package_names_packages_zero(targeting_tenant):
    """A single violating package reports packages[0].targeting_overlay, not the bare field name."""
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
        account={"account_id": "acct_test"},
        brand={"domain": "testbrand.com"},
        packages=[
            create_test_package_request(
                product_id="prod_display_a",
                budget=5000.0,
                pricing_option_id="cpm_usd_fixed",
                targeting_overlay={"geo_countries": ["US"], "geo_countries_exclude": ["US"]},
            )
        ],
        start_time=start,
        end_time=end,
        idempotency_key=f"int-key-{uuid.uuid4().hex}",
    )

    with pytest.raises(AdCPInvalidRequestError) as excinfo:
        await _create_media_buy_impl(req=request, identity=_make_identity())

    assert excinfo.value.field == "packages[0].targeting_overlay"


async def test_geo_overlap_on_the_second_package_names_packages_one(targeting_tenant):
    """The violating package's OWN index reaches the buyer, not a hardcoded 0.

    The regression this guards: a fix that only prepends a constant "packages[0]." would
    pass the single-package test above and still be wrong for every later package.
    """
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
        account={"account_id": "acct_test"},
        brand={"domain": "testbrand.com"},
        packages=[
            create_test_package_request(
                product_id="prod_display_a",
                budget=5000.0,
                pricing_option_id="cpm_usd_fixed",
                targeting_overlay={"geo_countries": ["US"]},
            ),
            create_test_package_request(
                product_id="prod_display_b",
                budget=5000.0,
                pricing_option_id="cpm_usd_fixed",
                targeting_overlay={"geo_countries": ["CA"], "geo_countries_exclude": ["CA"]},
            ),
        ],
        start_time=start,
        end_time=end,
        idempotency_key=f"int-key-{uuid.uuid4().hex}",
    )

    with pytest.raises(AdCPInvalidRequestError) as excinfo:
        await _create_media_buy_impl(req=request, identity=_make_identity())

    assert excinfo.value.field == "packages[1].targeting_overlay"
