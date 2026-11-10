"""The asset dict production builds is the asset dict an adapter reads.

WHY THIS EXISTS. ``AdServerAdapter.add_creative_assets`` declares
``assets: list[dict[str, Any]]`` (``src/adapters/base.py``), so the producer of that dict
and its consumers cannot disagree at the type level — and they disagreed for a year. The
one producer, ``_build_adapter_asset_from_creative``, writes ``creative_id``; GAM's
creatives manager reads ``creative_id``; the mock ad server read ``id``, a key no producer
writes. Every create_media_buy that uploaded a creative through the real mock adapter
raised ``KeyError: 'id'``, which the create path's catch-all turned into SERVICE_UNAVAILABLE
with no field and no details — a transient-recovery answer to a request that could never
succeed (salesagent-jzzaz; the masking is salesagent-q28ss).

No in-process BDD transport could see it: the harness replaces ``get_adapter`` with a
MagicMock, so ``add_creative_assets`` was a mock call that returned a truthy mock. It showed
only on ``e2e_rest``, where the live server patches nothing and the real adapter runs.

WHAT THIS GRADES, and why it is not a key literal. The asset comes from production's own
builder and reaches the adapter unmodified, and the assertion reads back the creative the
adapter NAMES. So it fails for any future drift on either side of the dict, in either
direction, without naming the key it is defending.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.adapters.base import AdapterCreateRequest
from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import FormatId, MediaPackage, Principal
from src.core.tools.media_buy_create import _build_adapter_asset_from_creative
from tests.helpers.media_buy_approval import uploadable_creative

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: The package the asset is assigned to. One name, used by the buy and by the assignment,
#: because an adapter that cannot find the package drops the asset for that reason instead.
PACKAGE_ID = "pkg_asset_contract"


def _live_buy(adapter: MockAdServer) -> str:
    """A buy registered through the adapter's OWN create path.

    Seeding ``adapter._media_buys`` directly would be shorter and is what an older test in
    this suite does, but then the upload runs against a store no production call wrote —
    and ``_add_creative_assets_immediate`` refuses an unknown buy, which is the one thing
    this test must not be grading.
    """
    start = datetime.now(UTC)
    result = adapter.create_media_buy(
        AdapterCreateRequest(brand={"domain": "example.com"}, total_budget=Decimal("5000.00")),
        [
            MediaPackage(
                package_id=PACKAGE_ID,
                name="Asset contract",
                delivery_type="guaranteed",
                cpm=5.0,
                impressions=10000,
                format_ids=[FormatId(agent_url="https://creative.test", id="display_300x250")],
                product_id="prod_asset_contract",
            )
        ],
        start,
        start + timedelta(days=30),
    )
    return result.media_buy_id


def test_the_adapter_names_the_creative_the_builder_named(bound_factory_session):
    """An asset built by production and uploaded to the real mock adapter comes back named."""
    from tests.factories import CreativeFactory, PrincipalFactory, TenantFactory

    tenant = TenantFactory()
    principal_row = PrincipalFactory(tenant=tenant)
    creative = uploadable_creative(CreativeFactory, tenant=tenant, principal=principal_row, approved=True)
    bound_factory_session.commit()

    asset, build_err = _build_adapter_asset_from_creative(
        creative,
        [{"package_id": PACKAGE_ID, "weight": 100}],
        tenant_id=tenant.tenant_id,
    )
    assert build_err is None, build_err
    assert asset is not None

    adapter = MockAdServer(
        config={},
        principal=Principal(
            principal_id=principal_row.principal_id,
            name=principal_row.name,
            platform_mappings={"mock": {"account_id": "acct_asset_contract"}},
        ),
        tenant_id=tenant.tenant_id,
    )

    statuses = adapter.add_creative_assets(_live_buy(adapter), [asset], datetime.now(UTC))

    assert [status.creative_id for status in statuses] == [creative.creative_id]
