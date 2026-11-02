"""Integration test: execute_approved_media_buy must persist platform_line_item_ids.

Bug: (GitHub #1037)
Root cause: execute_approved_media_buy calls adapter, gets platform_line_item_ids
back on the adapter's result, but never persists them to MediaPackage.package_config.
The auto-approval path in _create_media_buy_impl DOES persist them.

WHY THESE SIX MOCK-HEAVY CASES ARE KEPT. They are the ONLY verification of this branch.
Measured in run innet_150926_1232: a phrase search across all 52 files in
``tests/bdd/features/`` for the seller-approves-then-the-adapter-executes outcome, and for
line-item-id persistence, returns ZERO scenarios -- and create_media_buy measures ~11% of
its BDD rows live (165 passed of 1394). There is no wire to grade this on: the stimulus is
an ADMIN action and the outcome is internal persistence (``package_config
["platform_line_item_id"]`` per package), which is not a buyer-facing response shape any
scenario covers. So the mocks are not laziness here; delete these and the manual-approval
branch has nothing watching it.

The stimulus is an ``AdapterCreateResult`` -- what ``create_media_buy`` returns
(``src/adapters/base.py``, commit ecfdd7771) and what production reads
(``response.platform_line_item_ids``, media_buy_create.py). Each case below used to
stage a ``CreateMediaBuySuccess`` and hang the mapping off it under a PRIVATE name via
``object.__setattr__``, because the buyer's wire model has no field for a seller-internal
ad-server id. That is the state the carrier split removed: the mapping is a declared
field on the adapter's result, so it is passed as a keyword and nothing reaches past the
model to plant an attribute.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, MagicMock, patch

import pytest

from src.adapters.base import AdapterCreateResult
from src.core.database.database_session import get_db_session, get_engine
from src.core.database.models import MediaPackage as DBMediaPackage
from tests.helpers.gam_client import gam_line_item, stub_gam_client_manager
from tests.helpers.media_buy_approval import run_approval

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def pending_media_buy_with_package(integration_db):
    """Create a media buy in pending_approval status with a package using factories."""
    from sqlalchemy.orm import Session as SASession

    from tests.factories import (
        ALL_FACTORIES,
        AccountFactory,
        AgentAccountAccessFactory,
        MediaBuyFactory,
        MediaPackageFactory,
        PricingOptionFactory,
        PrincipalFactory,
        ProductFactory,
        PropertyTagFactory,
        TenantFactory,
    )
    from tests.factories.account import DEFAULT_TEST_ACCOUNT_ID

    engine = get_engine()
    session = SASession(bind=engine)
    try:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = session

        tenant = TenantFactory(tenant_id="test_tenant")
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id="test_principal",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        # The approval path rebuilds the caller's identity from the stored row
        # (``identity_of`` -> ``find_account``), so the buy's account must exist AND the
        # principal must have access to it. MediaBuyFactory names DEFAULT_TEST_ACCOUNT_ID.
        _account = AccountFactory(tenant=tenant, account_id=DEFAULT_TEST_ACCOUNT_ID)
        AgentAccountAccessFactory(tenant=tenant, principal=principal, account=_account)
        product = ProductFactory(
            tenant=tenant,
            product_id="guaranteed_display",
            name="Guaranteed Display Ads",
            delivery_type="guaranteed",
        )
        PricingOptionFactory(
            product=product,
            pricing_model="cpm",
            rate=15.0,
            currency="USD",
            is_fixed=True,
        )

        now = datetime.now(UTC)
        start = now + timedelta(days=1)
        end = now + timedelta(days=8)

        mb = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_approval_test",
            order_name="Approval Test Order",
            advertiser_name="Test Advertiser",
            currency="USD",
            start_date=start.date(),
            end_date=end.date(),
            start_time=start,
            end_time=end,
            status="pending_approval",
            raw_request={
                "brand": {"domain": "testbrand.com"},
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "packages": [
                    {
                        "product_id": "guaranteed_display",
                        "pricing_option_id": "po_1",
                        "budget": 5000.0,
                    }
                ],
            },
        )

        MediaPackageFactory(
            media_buy=mb,
            package_id="pkg_001",
            package_config={
                "product_id": "guaranteed_display",
                "name": "Guaranteed Display Ads",
                "budget": 5000.0,
                "pricing_model": "cpm",
            },
        )

        yield {
            "media_buy_id": "mb_approval_test",
            "tenant_id": tenant.tenant_id,
            "package_id": "pkg_001",
        }
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
        session.close()


@pytest.fixture
def pending_media_buy_with_two_packages(integration_db):
    """Create a media buy in pending_approval status with two packages."""
    from sqlalchemy.orm import Session as SASession

    from tests.factories import (
        ALL_FACTORIES,
        AccountFactory,
        AgentAccountAccessFactory,
        MediaBuyFactory,
        MediaPackageFactory,
        PricingOptionFactory,
        PrincipalFactory,
        ProductFactory,
        PropertyTagFactory,
        TenantFactory,
    )
    from tests.factories.account import DEFAULT_TEST_ACCOUNT_ID

    engine = get_engine()
    session = SASession(bind=engine)
    try:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = session

        tenant = TenantFactory(tenant_id="test_tenant_multi")
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id="test_principal_multi",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        # See pending_media_buy_with_package: the approval path resolves the buy's
        # account and checks this principal's access to it.
        _account = AccountFactory(tenant=tenant, account_id=DEFAULT_TEST_ACCOUNT_ID)
        AgentAccountAccessFactory(tenant=tenant, principal=principal, account=_account)
        product = ProductFactory(
            tenant=tenant,
            product_id="guaranteed_display",
            name="Guaranteed Display Ads",
            delivery_type="guaranteed",
        )
        PricingOptionFactory(product=product, pricing_model="cpm", rate=15.0, currency="USD", is_fixed=True)

        now = datetime.now(UTC)
        start = now + timedelta(days=1)
        end = now + timedelta(days=8)

        mb = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_multi_pkg_test",
            order_name="Multi Package Order",
            advertiser_name="Test Advertiser",
            currency="USD",
            start_date=start.date(),
            end_date=end.date(),
            start_time=start,
            end_time=end,
            status="pending_approval",
            raw_request={
                "brand": {"domain": "testbrand.com"},
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "packages": [
                    {
                        "product_id": "guaranteed_display",
                        "pricing_option_id": "po_1",
                        "budget": 3000.0,
                    },
                    {
                        "product_id": "guaranteed_display",
                        "pricing_option_id": "po_1",
                        "budget": 2000.0,
                    },
                ],
            },
        )

        MediaPackageFactory(
            media_buy=mb,
            package_id="pkg_A",
            package_config={"product_id": "guaranteed_display", "name": "Package A", "budget": 3000.0},
        )
        MediaPackageFactory(
            media_buy=mb,
            package_id="pkg_B",
            package_config={"product_id": "guaranteed_display", "name": "Package B", "budget": 2000.0},
        )

        yield {
            "media_buy_id": "mb_multi_pkg_test",
            "tenant_id": tenant.tenant_id,
            "package_ids": ["pkg_A", "pkg_B"],
        }
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
        session.close()


def _run_execute_approved(media_buy_id, tenant_id, adapter_response):
    """Helper to run execute_approved_media_buy with mocked adapter."""
    with (
        patch(
            "src.core.tools.media_buy_create._execute_adapter_media_buy_creation",
            return_value=adapter_response,
        ),
        patch("src.core.tools.media_buy_create._validate_creatives_before_adapter_call"),
        patch(
            "src.core.helpers.adapter_helpers.get_adapter",
            return_value=type("MockAdapter", (), {"orders_manager": None})(),
        ),
    ):
        return run_approval(media_buy_id, tenant_id)


class TestExecuteApprovedPlatformIds:
    """execute_approved_media_buy must persist platform_line_item_ids to package_config."""

    def test_platform_line_item_ids_persisted_after_approval(self, pending_media_buy_with_package):
        """After adapter execution via manual approval, platform_line_item_id
        must be written to MediaPackage.package_config for each package.

        This is the regression test for (GitHub #1037).
        """
        media_buy_id = pending_media_buy_with_package["media_buy_id"]
        tenant_id = pending_media_buy_with_package["tenant_id"]
        package_id = pending_media_buy_with_package["package_id"]

        # This is how GAM/Broadstreet adapters return the mapping.
        adapter_response = AdapterCreateResult(
            media_buy_id=media_buy_id,
            packages=[],
            platform_line_item_ids={package_id: "GAM_LINE_ITEM_12345"},
        )

        with (
            patch(
                "src.core.tools.media_buy_create._execute_adapter_media_buy_creation",
                return_value=adapter_response,
            ),
            patch("src.core.tools.media_buy_create._validate_creatives_before_adapter_call"),
            patch(
                "src.core.helpers.adapter_helpers.get_adapter",
                return_value=type("MockAdapter", (), {"orders_manager": None})(),
            ),
        ):
            result = run_approval(media_buy_id, tenant_id)

        assert result.ok, f"execute_approved_media_buy failed: {result.error_msg}"

        # THE KEY ASSERTION: platform_line_item_id must be in package_config
        from sqlalchemy import select

        with get_db_session() as session:
            pkg = session.scalars(
                select(DBMediaPackage).filter_by(
                    media_buy_id=media_buy_id,
                    package_id=package_id,
                )
            ).first()

            assert pkg is not None, f"Package {package_id} not found"
            assert "platform_line_item_id" in pkg.package_config, (
                f"platform_line_item_id NOT persisted in package_config. Got keys: {list(pkg.package_config.keys())}"
            )
            assert pkg.package_config["platform_line_item_id"] == "GAM_LINE_ITEM_12345", (
                f"Wrong platform_line_item_id value: {pkg.package_config.get('platform_line_item_id')}"
            )


class TestExecuteApprovedPlatformIdsEdgeCases:
    """Edge case tests for platform_line_item_ids persistence."""

    def test_multiple_packages_all_persisted(self, pending_media_buy_with_two_packages):
        """Multiple packages in one media buy — each gets its own platform_line_item_id."""
        data = pending_media_buy_with_two_packages
        media_buy_id = data["media_buy_id"]
        tenant_id = data["tenant_id"]

        adapter_response = AdapterCreateResult(
            media_buy_id=media_buy_id,
            packages=[],
            platform_line_item_ids={"pkg_A": "LINE_ITEM_A", "pkg_B": "LINE_ITEM_B"},
        )

        result = _run_execute_approved(media_buy_id, tenant_id, adapter_response)
        assert result.ok, f"execute_approved_media_buy failed: {result.error_msg}"

        from sqlalchemy import select

        with get_db_session() as session:
            for pkg_id, expected_lid in [("pkg_A", "LINE_ITEM_A"), ("pkg_B", "LINE_ITEM_B")]:
                pkg = session.scalars(
                    select(DBMediaPackage).filter_by(media_buy_id=media_buy_id, package_id=pkg_id)
                ).first()
                assert pkg is not None, f"Package {pkg_id} not found"
                assert pkg.package_config.get("platform_line_item_id") == expected_lid, (
                    f"{pkg_id}: expected {expected_lid}, got {pkg.package_config.get('platform_line_item_id')}"
                )

    def test_package_not_found_in_db_does_not_crash(self, pending_media_buy_with_package):
        """platform_line_item_ids references a package_id not in DB — logs warning, doesn't crash."""
        data = pending_media_buy_with_package
        media_buy_id = data["media_buy_id"]
        tenant_id = data["tenant_id"]

        adapter_response = AdapterCreateResult(
            media_buy_id=media_buy_id,
            packages=[],
            platform_line_item_ids={"nonexistent_pkg": "LINE_ITEM_999"},
        )

        result = _run_execute_approved(media_buy_id, tenant_id, adapter_response)
        assert result.ok, f"Should succeed even if package not found: {result.error_msg}"

    def test_empty_platform_line_item_ids_dict(self, pending_media_buy_with_package):
        """Empty _platform_line_item_ids dict — no writes, no crash."""
        data = pending_media_buy_with_package
        media_buy_id = data["media_buy_id"]
        tenant_id = data["tenant_id"]

        adapter_response = AdapterCreateResult(
            media_buy_id=media_buy_id,
            packages=[],
            platform_line_item_ids={},
        )

        result = _run_execute_approved(media_buy_id, tenant_id, adapter_response)
        assert result.ok, f"Should succeed with empty dict: {result.error_msg}"

        # Package config should be unchanged (no platform_line_item_id added)
        from sqlalchemy import select

        with get_db_session() as session:
            pkg = session.scalars(
                select(DBMediaPackage).filter_by(media_buy_id=media_buy_id, package_id=data["package_id"])
            ).first()
            assert pkg is not None
            assert "platform_line_item_id" not in pkg.package_config

    def test_omitted_platform_line_item_ids(self, pending_media_buy_with_package):
        """An adapter that maps no line items omits the field — it defaults to {}, no crash.

        Formerly "Response has no _platform_line_item_ids attr — getattr default {}".
        The attribute cannot be absent any more: it is a declared field with
        ``default_factory=dict`` on ``AdapterCreateResult``, and production reads it
        directly rather than through a ``getattr`` default. What is still worth grading is
        the OMISSION at the construction site, which is what an adapter with no mapping
        does.
        """
        data = pending_media_buy_with_package
        media_buy_id = data["media_buy_id"]
        tenant_id = data["tenant_id"]

        adapter_response = AdapterCreateResult(
            media_buy_id=media_buy_id,
            packages=[],
        )

        result = _run_execute_approved(media_buy_id, tenant_id, adapter_response)
        assert result.ok, f"Should succeed with no mapping: {result.error_msg}"

        from sqlalchemy import select

        with get_db_session() as session:
            pkg = session.scalars(
                select(DBMediaPackage).filter_by(media_buy_id=media_buy_id, package_id=data["package_id"])
            ).first()
            assert pkg is not None
            assert "platform_line_item_id" not in pkg.package_config


@pytest.fixture
def pending_media_buy_with_approved_creative(pending_media_buy_with_package):
    """Extend the single-package pending buy with one approved creative assigned to it.

    Thin extension of ``pending_media_buy_with_package`` (reuses its buy/package setup —
    no duplication) that adds an approved creative + assignment. The creative starts with
    NO concept and NO ``platform_creative_id`` — the writeback under test is what fills
    them. Factories persist with ``commit``, so the rows are visible to the separate
    session ``execute_approved_media_buy`` opens.
    """
    from sqlalchemy import select

    from src.core.database.models import MediaBuy as DBMediaBuy
    from src.core.database.models import Principal as DBPrincipal
    from src.core.database.models import Tenant as DBTenant
    from tests.factories import CreativeAssignmentFactory, CreativeFactory, MediaBuyFactory

    data = pending_media_buy_with_package
    # The parent fixture bound every factory to its (committed) session; reuse it so the
    # creative/assignment land in the same DB the parent's buy/package did.
    session = MediaBuyFactory._meta.sqlalchemy_session

    tenant = session.scalars(select(DBTenant).filter_by(tenant_id=data["tenant_id"])).first()
    principal = session.scalars(
        select(DBPrincipal).filter_by(tenant_id=data["tenant_id"], principal_id="test_principal")
    ).first()
    mb = session.scalars(
        select(DBMediaBuy).filter_by(tenant_id=data["tenant_id"], media_buy_id=data["media_buy_id"])
    ).first()

    creative = CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id="cre_concept_enrichment",
        status="approved",
        format="display_300x250",
        data={"assets": {"banner": {"url": "https://example.com/ad.jpg", "width": 300, "height": 250}}},
    )
    CreativeAssignmentFactory(creative=creative, media_buy=mb, package_id=data["package_id"])

    return {**data, "principal_id": principal.principal_id, "creative_id": creative.creative_id}


class TestExecuteApprovedEnrichesSellerConcept:
    """execute_approved_media_buy — the manual-approval GAM push path — enriches
    trafficked creatives with the seller-side concept + platform id (#1506), and the
    persisted concept is findable by the #1407 list_creatives concept_ids filter.

    Covers Chris's review of #1509:
      • Finding 1 — this is the third GAM push path, previously unenriched.
      • Finding 2 — the real producer → real writeback → DB → real reader chain: the
        concept-bearing AssetStatus is produced by the REAL dry-run GAM adapter, folded
        in by the REAL execute_approved_media_buy writeback, persisted, and read back
        through the REAL CreativeRepository concept_ids filter. A key-name drift on
        either the producer or the reader reddens here.
    """

    def _produce_gam_status(self, gam_order_id: str, creative_id: str, tenant_id: str):
        """Emit the AssetStatus the REAL dry-run GAM producer returns for a pushed
        creative — so a change to the GAM concept keys/values reddens this test too."""
        from src.adapters.google_ad_manager import GoogleAdManager
        from src.core.schemas import Principal

        # The SCHEMA Principal, which carries no token at all — ``with_token`` is a
        # classmethod on the ORM ``Principal`` row, not on this model.
        gam_principal = Principal(
            principal_id="test_principal",
            name="Test Principal",
            platform_mappings={"google_ad_manager": {"advertiser_id": "123"}},
        )
        config = {
            "network_code": "123456",
            "service_account_key_file": "/path/to/key.json",
            "trafficker_id": "trafficker_123",
        }
        # The adapter reaches GAM on construction, so the client manager is a stand-in
        # serving the one line item this order has. ``_init_client``, which this used to
        # patch, is a legacy delegate the constructor no longer calls.
        with patch("src.adapters.google_ad_manager.GAMClientManager") as client_manager:
            client_manager.return_value = stub_gam_client_manager(
                line_items=[gam_line_item("test_package", sizes=((970, 250),))]
            )
            gam = GoogleAdManager(
                config=config,
                principal=gam_principal,
                network_code=config["network_code"],
                advertiser_id="123",
                trafficker_id=config["trafficker_id"],
                tenant_id=tenant_id,
            )
        # The order's line item accepts the asset's 970x250 (read off its format), so the
        # producer's size-vs-placeholder check passes. This asset only drives the producer
        # to emit the concept-bearing status — it is independent of the DB package the
        # writeback (step 2) uses.
        gam_asset = {
            "creative_id": creative_id,
            "name": "HTML5 Banner",
            "format": "display_970x250",
            "media_url": "https://example.com/creative.html",
            "click_url": "https://example.com/landing",
            "package_assignments": ["test_package"],
        }
        with patch.object(gam, "_validate_creative_for_gam", return_value=[]):
            statuses = gam.add_creative_assets(gam_order_id, [gam_asset], datetime.now(UTC))
        assert statuses[0].status == "approved"
        assert statuses[0].concept_id == f"gam-order-{gam_order_id}"  # pins the produced key/value
        return statuses

    def test_manual_approval_enriches_concept_and_is_filterable(self, pending_media_buy_with_approved_creative):
        """Approving a pending buy from the admin path enriches its creatives and the
        concept is retrievable via the list_creatives concept_ids filter."""
        from sqlalchemy import select

        from src.core.database.models import Creative as DBCreative
        from src.core.database.repositories.creative import CreativeRepository

        fixture = pending_media_buy_with_approved_creative
        media_buy_id = fixture["media_buy_id"]
        tenant_id = fixture["tenant_id"]
        principal_id = fixture["principal_id"]
        creative_id = fixture["creative_id"]
        gam_order_id = "555"
        expected_concept = f"gam-order-{gam_order_id}"

        # (1) REAL dry-run GAM producer generates the concept-bearing status...
        produced = self._produce_gam_status(gam_order_id, creative_id, tenant_id)

        # (2) ...fed through the REAL execute_approved_media_buy writeback. The adapter
        # response carries the GAM order id; the adapter itself is a stand-in whose
        # creatives_manager returns the real producer's output.
        adapter_response = AdapterCreateResult(media_buy_id=gam_order_id, packages=[])
        mock_adapter = MagicMock()
        mock_adapter.creatives_manager.add_creative_assets.return_value = produced
        mock_adapter.orders_manager.approve_order.return_value = True
        valid_asset = {
            "creative_id": creative_id,
            "package_assignments": [{"package_id": fixture["package_id"], "weight": 100}],
            "width": 300,
            "height": 250,
            "url": "https://example.com/ad.jpg",
            "click_url": None,
            "asset_type": "image",
            "name": "Test Creative",
        }
        with (
            patch(
                "src.core.tools.media_buy_create._execute_adapter_media_buy_creation",
                return_value=adapter_response,
            ),
            patch("src.core.tools.media_buy_create._validate_creatives_before_adapter_call"),
            patch(
                "src.core.tools.media_buy_create._build_adapter_asset_from_creative",
                return_value=(valid_asset, None),
            ),
            patch("src.core.helpers.adapter_helpers.get_adapter", return_value=mock_adapter),
        ):
            result = run_approval(media_buy_id, tenant_id)

        assert result.ok, f"execute_approved_media_buy failed: {result.error_msg}"
        mock_adapter.creatives_manager.add_creative_assets.assert_called_once_with(gam_order_id, ANY, ANY)

        # (3) The concept is PERSISTED to the creative's data blob (the Finding 1 fix —
        # without the writeback wiring, these keys are absent).
        with get_db_session() as session:
            creative = session.scalars(
                select(DBCreative).filter_by(tenant_id=tenant_id, creative_id=creative_id)
            ).first()
            assert creative is not None
            assert creative.data.get("concept_id") == expected_concept
            assert creative.data.get("concept_name") == f"GAM Order {gam_order_id}"
            assert creative.data.get("concept_source") == "gam_order"
            # The platform_creative_id half is filled on the same path.
            assert creative.data.get("platform_creative_id") == creative_id
            # Fill-only-when-absent preserved the pre-existing assets blob.
            assert creative.data.get("assets") is not None

        # (4) The persisted concept is findable through the REAL #1407 reader filter.
        with get_db_session() as session:
            repo = CreativeRepository(session, tenant_id)
            result = repo.get_by_principal(principal_id, concept_ids=[expected_concept])
            returned = {c.creative_id for c in result.creatives}
            assert creative_id in returned, (
                f"list_creatives concept_ids filter did not return the enriched creative; got {returned!r}"
            )
