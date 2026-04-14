"""Cross-transport differential property for create_media_buy.

Generates one valid request, dispatches through IMPL and A2A transports,
asserts byte-for-byte response parity (modulo volatile id/timestamp fields).

Scope (v1):
    IMPL  -- direct _create_media_buy_impl
    A2A   -- create_media_buy_raw (flat-param wrapper)

Deliberately excluded from v1, with reasons:

    MCP -- discovered on first run: the FastMCP pipeline re-resolves identity
    from HTTP headers, and headers do not carry ``testing_context``
    (dry_run / test_session_id / mock_time). So an MCP call with dry_run=True
    at the env level still hits ``validate_setup_complete`` at _impl, while
    the IMPL call (identity injected directly) correctly skips it. That is
    a legitimate parity bug in the MCP auth chain -- it propagates principal
    and tenant, but drops testing_context. See salesagent-81u4 notes.

    REST -- CreateMediaBuyBody (src/routes/api_v1.py:73-83) is a known-lossy
    subset of the request schema, missing targeting_overlay, creatives,
    push_notification_config, context, ext. Until the body model is
    completed, REST parity is guaranteed to diverge on any rich payload.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.core.schemas import CreateMediaBuyRequest
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


def _future_datetime() -> st.SearchStrategy[datetime]:
    return st.builds(
        lambda off: datetime.now(UTC) + timedelta(seconds=off),
        st.integers(min_value=3600, max_value=30 * 86400),
    )


buyer_ref_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=4,
    max_size=20,
).map(lambda s: f"xt-{s}-{uuid.uuid4().hex[:6]}")

brand_domain_strategy = st.sampled_from(
    ["example.com", "testbrand.com", "publisher.io", "advertiser.org"]
)

budget_strategy = st.decimals(
    min_value=Decimal("100.00"),
    max_value=Decimal("9000.00"),  # below mock-adapter inventory cap
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def parity_payload(draw: st.DrawFn) -> dict:
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
        rate=Decimal("10.00"),
    )
    env.commit_catalog()


# Fields to strip before cross-transport comparison. These vary by call
# regardless of input (uuid4, timestamps, random ids) and would cause
# false parity failures.
_VOLATILE_KEYS = frozenset(
    {
        "media_buy_id",
        "package_id",  # format: pkg_{product_id}_{random_hex}_{index}
        "created_at",
        "updated_at",
        "workflow_step_id",
        "context_id",
    }
)


def _normalize(d: dict) -> dict:
    """Remove volatile keys from a flat result dict, and recurse into packages."""
    out = {k: v for k, v in d.items() if k not in _VOLATILE_KEYS}
    if isinstance(out.get("packages"), list):
        out["packages"] = [
            {k: v for k, v in pkg.items() if k not in _VOLATILE_KEYS}
            if isinstance(pkg, dict)
            else pkg
            for pkg in out["packages"]
        ]
    return out


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
@given(payload=parity_payload())
def test_impl_and_a2a_agree(integration_db, payload: dict) -> None:
    """For any valid payload, IMPL and A2A transports produce byte-for-byte
    identical responses (modulo volatile keys).

    Divergence output shows which keys differ and where.
    """
    tenant_id = f"xt-t-{uuid.uuid4().hex[:8]}"
    principal_id = f"xt-p-{uuid.uuid4().hex[:6]}"

    with MediaBuyCreateEnv(
        tenant_id=tenant_id, principal_id=principal_id, dry_run=True
    ) as env:
        _setup_catalog(env)

        # Each transport builds its own request instance. Reusing one across
        # transports is unsafe -- the wrappers mutate req.packages in-flight
        # (creative_ids injection at line ~1909).
        impl_req = CreateMediaBuyRequest(**payload)
        a2a_req = CreateMediaBuyRequest(**payload)

        impl_dict = _normalize(env.call_impl(req=impl_req).model_dump(mode="json"))
        a2a_dict = _normalize(env.call_a2a_as_dict(req=a2a_req))

        assert impl_dict == a2a_dict, (
            f"IMPL vs A2A divergence:\n"
            f"  IMPL only: {set(impl_dict) - set(a2a_dict)}\n"
            f"  A2A only:  {set(a2a_dict) - set(impl_dict)}\n"
            f"  shared but different: "
            f"{[k for k in set(impl_dict) & set(a2a_dict) if impl_dict[k] != a2a_dict[k]]}\n"
            f"IMPL: {impl_dict}\nA2A:  {a2a_dict}"
        )
