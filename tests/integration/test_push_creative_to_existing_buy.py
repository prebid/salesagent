"""The retroactive creative push PERSISTS its adapter enrichment.

``push_creative_to_existing_buy`` (#1038) is the third GAM push path: a creative that was
in pending_review when its buy was approved gets pushed later, on its own approval. Its
last act is a WRITE — ``uow.creatives.update_data(creative, merged_data)`` — and that write
is the whole point of the function: without it the creative carries no
``platform_creative_id``, so the next approval pushes it to the ad server a second time
(the function's own re-approval guard reads that field).

WHY THIS TEST EXISTS. That write used to run against a session that had already been
closed out from under its unit of work. ``identity_of`` was called from INSIDE the
``AdminCreativeUoW`` block, and because ``get_db_session()`` yields the THREAD-SCOPED
session with no nesting refcount, its exit ran ``session.close(); scoped.remove()`` on the
same ``Session`` object the outer unit held — GH #1644, the P1 class every other entry in
the nested-unit-of-work guard's allowlist is filed against. The resolution now happens
before the unit opens.

The structural guard (``test_architecture_nested_unit_of_work.py``) is an AST scan: it can
see that the call moved, and it cannot see whether the write LANDS. That is what this
grades, by reading the row back through a unit of work the test opens afterwards and
asserting the adapter's
enrichment is on it.

The adapter is the one seam stubbed. Everything else is real: real rows, real repository,
real unit of work, real identity resolution — the resolution being precisely what broke the
session, so a test that stubbed it would grade nothing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.schemas import AssetStatus
from src.core.tools.media_buy_create import push_creative_to_existing_buy
from tests.helpers.media_buy_approval import uploadable_creative

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: The ad server's own grouping id, echoed back on the push result. A value no input to the
#: function carries, so reading it off the persisted row can only pass if production
#: carried it from the adapter through the merge into the write.
PLATFORM_CONCEPT_ID = "gam-order-90210"


def _seed_live_buy_with_assigned_creative():
    """A creative approved but not yet pushed, assigned to a package on a LIVE buy.

    Returns (tenant_id, creative_id, media_buy_id, principal_id) -- the principal because
    the buyer-path repository read used for the assertion is principal-scoped.
    """
    from tests.factories import (
        CreativeAssignmentFactory,
        CreativeFactory,
        MediaBuyFactory,
        MediaPackageFactory,
        PrincipalFactory,
        TenantFactory,
    )

    tenant = TenantFactory(ad_server="mock")
    principal = PrincipalFactory(tenant=tenant)
    # ``uploadable_creative`` supplies the format + assets that get through
    # ``_build_adapter_asset_from_creative``; the factory defaults do not.
    creative = uploadable_creative(
        CreativeFactory,
        tenant=tenant,
        principal=principal,
        status="approved",
    )
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    # platform_order_id is what the function refuses to push without: it is the live ad
    # server order the creative is being attached to.
    MediaPackageFactory(
        media_buy=media_buy,
        package_id="pkg_live",
        package_config={"product_id": "prod_001", "platform_order_id": "gam_order_777"},
    )
    CreativeAssignmentFactory(creative=creative, media_buy=media_buy, package_id="pkg_live")
    return tenant.tenant_id, creative.creative_id, media_buy.media_buy_id, principal.principal_id


def _adapter_reporting(creative_id: str) -> MagicMock:
    """An adapter whose creatives_manager accepts the asset and reports enrichment back."""
    adapter = MagicMock()
    adapter.creatives_manager.add_creative_assets.return_value = [
        AssetStatus(
            creative_id=creative_id,
            status="active",
            concept_id=PLATFORM_CONCEPT_ID,
            concept_name="Q4 Brand Push",
            concept_source="gam_order",
        )
    ]
    return adapter


def _persisted_creative_data(tenant_id: str, creative_id: str, principal_id: str) -> dict:
    """The creative's ``data`` blob, read back through its own unit of work.

    A read through a unit the test opens AFTER the push returned is the point: the defect
    this grades left the write on a closed session, so anything asserted against the
    in-memory object the function touched could pass while the row was never updated. The
    unit opens its own session, which is what makes this an independent read, and it goes
    through ``CreativeRepository`` rather than a raw ``get_db_session()`` — the shape
    ``test_architecture_repository_pattern`` requires of a new test.

    Proven independent rather than assumed: with the nested ``identity_of`` call
    reintroduced, this read still reports the un-enriched row and the test still fails.
    """
    from src.core.database.repositories.uow import CreativeUoW

    with CreativeUoW(tenant_id) as uow:
        assert uow.creatives is not None
        row = uow.creatives.get_by_id(creative_id, principal_id)
        assert row is not None, "the seeded creative row is missing"
        return dict(row.data or {})


def test_push_persists_the_adapter_enrichment(integration_db, factory_session):
    """The enrichment the adapter reported is on the ROW after the push returns."""
    tenant_id, creative_id, media_buy_id, principal_id = _seed_live_buy_with_assigned_creative()

    with patch(
        "src.core.tools.media_buy_create.get_adapter",
        return_value=_adapter_reporting(creative_id),
    ):
        ok, err = push_creative_to_existing_buy(
            creative_id=creative_id,
            media_buy_id=media_buy_id,
            tenant_id=tenant_id,
        )

    assert (ok, err) == (True, None), f"push failed: {err}"

    data = _persisted_creative_data(tenant_id, creative_id, principal_id)
    assert data.get("concept_id") == PLATFORM_CONCEPT_ID, (
        "the adapter's concept enrichment did not reach the row — the update_data write "
        f"was lost. Persisted data: {data}"
    )
    assert data.get("concept_source") == "gam_order"
    assert data.get("platform_creative_id") == creative_id, (
        "platform_creative_id is what the function's own re-approval guard reads; without "
        "it a second approval pushes this creative to the ad server again"
    )
