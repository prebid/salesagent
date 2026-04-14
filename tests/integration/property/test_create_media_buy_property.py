"""Property-based integration tests for _create_media_buy_impl.

Anchored to the codebase's architectural contract (CLAUDE.md Pattern #5,
structural guard ``test_no_toolerror_in_impl.py``, and BR-UC-002 Gherkin):

    _impl functions RAISE AdCPError subclasses on validation failures.
    Transport wrappers catch and translate to transport-specific error
    structures. _impl never returns an error envelope.

This wasn't our first cut. An earlier version of this test asserted the
inverse -- that _impl returns ``CreateMediaBuyError`` in a result envelope
-- and passed because the test author misread the contract. The property
was wrong; the "bug" it "found" was phantom. The lesson:

    Property tests codify the test author's claim about the contract.
    If you misread the contract, your property is wrong and "all
    passing" is false confidence. Always anchor properties to spec
    (Gherkin, structural guards, documented patterns) -- not to the
    code under test.

The two properties below are split by input class (valid / invalid) so
each assertion is sharp and specific:

    P1 (valid inputs)  : impl returns CreateMediaBuySuccess; python- and
                         JSON-mode roundtrips preserve equality; buyer_ref
                         is preserved end-to-end.
    P2 (invalid inputs): impl raises AdCPValidationError with structured
                         details["error_code"] == "ADAPTER_VALIDATION_FAILED".
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.core.exceptions import AdCPValidationError
from src.core.schemas import (
    CreateMediaBuyRequest,
    CreateMediaBuySuccess,
)
from tests.factories import (
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    PropertyTagFactory,
    TenantFactory,
)
from tests.harness.media_buy_create import MediaBuyCreateEnv

pytestmark = [pytest.mark.requires_db, pytest.mark.integration]


PRODUCT_ID = "prod_display"
PRICING_OPTION_ID = "cpm_usd_fixed"

# Mock adapter inventory cap: 1,000,000 impressions. At $10 CPM, that's
# exactly $10,000 budget. Anything above that triggers the adapter's
# PERCENTAGE_UNITS_BOUGHT_TOO_HIGH rejection path -- which raises
# AdCPValidationError at media_buy_create.py:2844.
_CPM_RATE = Decimal("10.00")
_INVENTORY_CAP_DOLLARS = Decimal("10000.00")


def _future_datetime() -> st.SearchStrategy[datetime]:
    return st.builds(
        lambda off: datetime.now(UTC) + timedelta(seconds=off),
        st.integers(min_value=3600, max_value=30 * 86400),
    )


buyer_ref_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=4,
    max_size=20,
).map(lambda s: f"buyer-{s}-{uuid.uuid4().hex[:8]}")

brand_domain_strategy = st.sampled_from(
    ["example.com", "testbrand.com", "publisher.io", "advertiser.org"]
)

# Strictly below the inventory cap -- these inputs MUST succeed.
valid_budget_strategy = st.decimals(
    min_value=Decimal("100.00"),
    max_value=Decimal("9000.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Strictly above the inventory cap -- these inputs MUST raise.
overflow_budget_strategy = st.decimals(
    min_value=Decimal("10000.01"),  # literal off-by-one boundary
    max_value=Decimal("50000.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _payload_with(draw: st.DrawFn, budget_strategy) -> dict:
    start = draw(_future_datetime())
    end = start + timedelta(days=draw(st.integers(min_value=1, max_value=30)))
    return {
        "buyer_ref": draw(buyer_ref_strategy),
        "brand": {"domain": draw(brand_domain_strategy)},
        "start_time": start,
        "end_time": end,
        "packages": [
            {
                "product_id": PRODUCT_ID,
                "buyer_ref": f"pkg-{uuid.uuid4().hex[:8]}",
                "budget": float(draw(budget_strategy)),
                "pricing_option_id": PRICING_OPTION_ID,
            }
        ],
    }


def _setup_catalog(env: MediaBuyCreateEnv) -> None:
    tenant = TenantFactory(tenant_id=env._tenant_id)
    PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
    PrincipalFactory(
        tenant=tenant,
        principal_id=env._principal_id,
        platform_mappings={"mock": {"advertiser_id": "mock_adv_1"}},
    )
    product = ProductFactory(
        tenant=tenant, product_id=PRODUCT_ID, property_tags=["all_inventory"]
    )
    PricingOptionFactory(
        product=product,
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
        rate=_CPM_RATE,
    )
    env.commit_catalog()


_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)


# --------------------------------------------------------------------------- #
# P1: valid inputs -> CreateMediaBuySuccess with roundtrip properties
# --------------------------------------------------------------------------- #


@_SETTINGS
@given(payload=_payload_with(valid_budget_strategy))
def test_valid_payload_returns_success_with_roundtrip(
    integration_db, payload: dict
) -> None:
    """Within adapter inventory cap, _impl returns CreateMediaBuySuccess
    and the response satisfies python/JSON roundtrip equality plus
    buyer_ref preservation."""
    tenant_id = f"prop-t-{uuid.uuid4().hex[:8]}"
    principal_id = f"prop-p-{uuid.uuid4().hex[:6]}"

    with MediaBuyCreateEnv(
        tenant_id=tenant_id, principal_id=principal_id, dry_run=True
    ) as env:
        _setup_catalog(env)

        request = CreateMediaBuyRequest(**payload)
        result = env.call_impl(req=request)
        response = result.response

        assert isinstance(response, CreateMediaBuySuccess), (
            f"Valid input should produce CreateMediaBuySuccess, got "
            f"{type(response).__name__}: {response!r}"
        )

        # Python-mode roundtrip
        dumped = response.model_dump()
        rebuilt = CreateMediaBuySuccess(**dumped)
        assert rebuilt.model_dump() == dumped, "Python-mode roundtrip diverged"

        # JSON-mode roundtrip (Decimal/datetime serialization)
        json_dumped = response.model_dump(mode="json")
        rebuilt_json = CreateMediaBuySuccess(**json.loads(json.dumps(json_dumped)))
        assert rebuilt_json.model_dump(mode="json") == json_dumped, (
            "JSON-mode roundtrip diverged"
        )

        # buyer_ref preserved end-to-end
        assert response.buyer_ref == payload["buyer_ref"], (
            f"buyer_ref drift: sent {payload['buyer_ref']!r}, "
            f"got {response.buyer_ref!r}"
        )


# --------------------------------------------------------------------------- #
# P2: invalid inputs -> raises AdCPValidationError with structured details
# --------------------------------------------------------------------------- #


@_SETTINGS
@given(payload=_payload_with(overflow_budget_strategy))
def test_inventory_overflow_raises_validation_error(
    integration_db, payload: dict
) -> None:
    """Above adapter inventory cap, _impl raises AdCPValidationError
    carrying structured details["error_code"] == "ADAPTER_VALIDATION_FAILED"
    (matching the convention at media_buy_create.py:2844 and the
    enforcement pattern of test_no_toolerror_in_impl.py)."""
    tenant_id = f"prop-t-{uuid.uuid4().hex[:8]}"
    principal_id = f"prop-p-{uuid.uuid4().hex[:6]}"

    with MediaBuyCreateEnv(
        tenant_id=tenant_id, principal_id=principal_id, dry_run=True
    ) as env:
        _setup_catalog(env)

        request = CreateMediaBuyRequest(**payload)
        with pytest.raises(AdCPValidationError) as exc_info:
            env.call_impl(req=request)

        exc = exc_info.value
        # Structured error code per the raise at line 2844.
        assert getattr(exc, "details", None), (
            f"AdCPValidationError must carry structured details; got {exc!r}"
        )
        assert exc.details.get("error_code") == "ADAPTER_VALIDATION_FAILED", (
            f"Unexpected error_code in details: {exc.details!r}"
        )
        # Message carries the underlying reason from the adapter.
        assert "PERCENTAGE_UNITS_BOUGHT_TOO_HIGH" in str(exc), (
            f"Expected adapter-originated reason in message; got: {exc!s}"
        )
