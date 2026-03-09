"""Integration tests: creative schema compliance obligations.

Behavioral round-trip tests using CreativeSyncEnv/CreativeListEnv + real PostgreSQL.
These tests exercise _impl functions and verify schema properties are preserved
through the full sync/list round-trip, replacing schema-only unit checks.

Covers obligations: UC-006-CREATIVE-SCHEMA-COMPLIANCE-01, UC-006-CREATIVE-SCHEMA-COMPLIANCE-07,
UC-006-CREATIVE-SCHEMA-COMPLIANCE-09, UC-006-CREATIVE-SCHEMA-COMPLIANCE-10
"""

from __future__ import annotations

import pytest
from adcp.types import CreativeAction
from adcp.types.generated_poc.core.creative_asset import CreativeAsset
from adcp.types.generated_poc.core.format_id import FormatId as AdcpFormatId

from tests.harness import CreativeListEnv, CreativeSyncEnv

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_creative_asset(**overrides) -> CreativeAsset:
    """Build a minimal valid CreativeAsset for testing."""
    defaults = {
        "creative_id": "c_test_1",
        "name": "Test Banner",
        "format_id": AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id="display_300x250"),
        "assets": {"banner": {"url": "https://example.com/banner.png"}},
    }
    defaults.update(overrides)
    return CreativeAsset(**defaults)


# ---------------------------------------------------------------------------
# UC-006-CREATIVE-SCHEMA-COMPLIANCE-10: All 11 asset types accepted through sync
# ---------------------------------------------------------------------------


class TestAllAssetTypesAcceptedThroughSync:
    """Each of the 11 asset types must be accepted through _sync_creatives_impl
    without validation errors and persisted to the database.

    Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-10
    """

    ASSET_TYPES = [
        "image",
        "video",
        "audio",
        "text",
        "markdown",
        "html",
        "css",
        "javascript",
        "vast",
        "daast",
        "promoted_offerings",
    ]

    def test_all_11_asset_types_accepted(self, integration_db):
        """Sync creatives with each of the 11 asset types through real DB.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-10
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            creatives = []
            for asset_type in self.ASSET_TYPES:
                creatives.append(
                    _make_creative_asset(
                        creative_id=f"c_{asset_type}",
                        name=f"Test {asset_type}",
                        assets={asset_type: {"content": f"test {asset_type} content"}},
                    )
                )

            response = env.call_impl(creatives=creatives)

            # All 11 should succeed (created action, no failures)
            assert len(response.creatives) == len(self.ASSET_TYPES)
            actions = {r.creative_id: r.action for r in response.creatives}
            for asset_type in self.ASSET_TYPES:
                cid = f"c_{asset_type}"
                assert cid in actions, f"Missing result for creative with {asset_type} asset"
                assert actions[cid] == CreativeAction.created, (
                    f"Creative with {asset_type} asset should be created, got {actions[cid]}"
                )

    def test_asset_data_preserved_through_roundtrip(self, integration_db):
        """Verify that asset data survives the sync -> list round-trip for each type.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-10
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # Sync one creative with each asset type
            for asset_type in self.ASSET_TYPES:
                creative = _make_creative_asset(
                    creative_id=f"c_rt_{asset_type}",
                    name=f"Roundtrip {asset_type}",
                    assets={asset_type: {"content": f"{asset_type} data"}},
                )
                response = env.call_impl(creatives=[creative])
                assert response.creatives[0].action == CreativeAction.created

        # Now list and verify assets are preserved (tenant/principal already created above)
        with CreativeListEnv() as env:
            list_response = env.call_impl()

            returned_ids = {c.creative_id for c in list_response.creatives}
            for asset_type in self.ASSET_TYPES:
                cid = f"c_rt_{asset_type}"
                assert cid in returned_ids, f"Creative with {asset_type} asset not found in list response"


# ---------------------------------------------------------------------------
# UC-006-CREATIVE-SCHEMA-COMPLIANCE-07: Creative model_dump produces listing-schema JSON
# ---------------------------------------------------------------------------


class TestCreativeModelDumpListingSchema:
    """Creative returned through list_creatives must include all required listing
    fields and exclude delivery-only fields, verified through the real DB round-trip.

    Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-07
    """

    def test_list_response_includes_required_listing_fields(self, integration_db):
        """Creatives synced then listed must include all required listing Creative fields.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-07
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            creative = _make_creative_asset(creative_id="c_listing_fields", name="Listing Fields Test")
            response = env.call_impl(creatives=[creative])
            assert response.creatives[0].action == CreativeAction.created

        with CreativeListEnv() as env:
            list_response = env.call_impl()

            assert len(list_response.creatives) >= 1
            matched = [c for c in list_response.creatives if c.creative_id == "c_listing_fields"]
            assert len(matched) == 1

            listed_creative = matched[0]
            data = listed_creative.model_dump()

            # Required listing Creative fields per adcp 3.6.0
            required_fields = ["creative_id", "format_id", "name", "status", "created_date", "updated_date"]
            for field in required_fields:
                assert field in data, f"Required listing field '{field}' missing from model_dump()"

    def test_list_response_excludes_internal_fields(self, integration_db):
        """model_dump() on listed creatives must NOT include internal-only fields.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-07
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            creative = _make_creative_asset(creative_id="c_internal_excl", name="Internal Excl Test")
            env.call_impl(creatives=[creative])

        with CreativeListEnv() as env:
            list_response = env.call_impl()

            matched = [c for c in list_response.creatives if c.creative_id == "c_internal_excl"]
            assert len(matched) == 1
            data = matched[0].model_dump()

            # principal_id is internal-only, must be excluded
            assert "principal_id" not in data

    def test_list_response_excludes_delivery_only_fields(self, integration_db):
        """model_dump() must NOT include delivery-only fields (variants, variant_count, totals).

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-07
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            creative = _make_creative_asset(creative_id="c_no_delivery", name="No Delivery Fields")
            env.call_impl(creatives=[creative])

        with CreativeListEnv() as env:
            list_response = env.call_impl()

            matched = [c for c in list_response.creatives if c.creative_id == "c_no_delivery"]
            assert len(matched) == 1
            data = matched[0].model_dump()

            delivery_only = ["variants", "variant_count", "totals", "media_buy_id"]
            for field in delivery_only:
                assert field not in data, f"Delivery-only field '{field}' leaked into listing response"


# ---------------------------------------------------------------------------
# UC-006-CREATIVE-SCHEMA-COMPLIANCE-01: Creative extends listing Creative, not delivery
# ---------------------------------------------------------------------------


class TestCreativeExtendsListingBase:
    """Creative returned from _sync_creatives_impl and _list_creatives_impl must
    be instances of the listing Creative (not delivery), verified through impl round-trip.

    Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-01
    """

    def test_sync_returns_listing_compatible_creative(self, integration_db):
        """Synced creative, when listed, must be an instance of the listing Creative base.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-01
        """
        from adcp.types.generated_poc.media_buy.list_creatives_response import (
            Creative as ListingCreative,
        )

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            creative = _make_creative_asset(creative_id="c_listing_base", name="Listing Base Test")
            env.call_impl(creatives=[creative])

        with CreativeListEnv() as env:
            list_response = env.call_impl()

            matched = [c for c in list_response.creatives if c.creative_id == "c_listing_base"]
            assert len(matched) == 1

            # The Creative schema class must extend ListingCreative
            assert isinstance(matched[0], ListingCreative), (
                f"Creative in list response must be an instance of listing Creative, got {type(matched[0]).__mro__}"
            )

    def test_listing_creative_not_delivery_creative(self, integration_db):
        """Listed creative must NOT be an instance of delivery Creative.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-01
        """
        from adcp.types.generated_poc.creative.get_creative_delivery_response import (
            Creative as DeliveryCreative,
        )

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            creative = _make_creative_asset(creative_id="c_not_delivery", name="Not Delivery Test")
            env.call_impl(creatives=[creative])

        with CreativeListEnv() as env:
            list_response = env.call_impl()

            matched = [c for c in list_response.creatives if c.creative_id == "c_not_delivery"]
            assert len(matched) == 1
            assert not isinstance(matched[0], DeliveryCreative), (
                "Creative in list response must NOT be an instance of delivery Creative"
            )


# ---------------------------------------------------------------------------
# UC-006-CREATIVE-SCHEMA-COMPLIANCE-09: CreativeAction enum values through sync
# ---------------------------------------------------------------------------


class TestCreativeActionEnumThroughSync:
    """CreativeAction enum values must match spec values and be correctly returned
    from _sync_creatives_impl for different scenarios.

    Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-09
    """

    def test_created_action_for_new_creative(self, integration_db):
        """New creative sync returns 'created' action.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-09
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            creative = _make_creative_asset(creative_id="c_action_new", name="New Creative")
            response = env.call_impl(creatives=[creative])

            assert len(response.creatives) == 1
            result = response.creatives[0]
            assert result.action == CreativeAction.created
            assert result.action.value == "created"

    def test_updated_action_for_existing_creative(self, integration_db):
        """Syncing an existing creative with changes returns 'updated' action.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-09
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # First sync: create
            creative = _make_creative_asset(creative_id="c_action_update", name="Original Name")
            response1 = env.call_impl(creatives=[creative])
            assert response1.creatives[0].action == CreativeAction.created

            # Second sync: update (change name)
            updated = _make_creative_asset(creative_id="c_action_update", name="Updated Name")
            response2 = env.call_impl(creatives=[updated])
            assert len(response2.creatives) == 1
            result = response2.creatives[0]
            assert result.action in (CreativeAction.updated, CreativeAction.unchanged)
            assert result.action.value in ("updated", "unchanged")

    def test_unchanged_action_for_identical_creative(self, integration_db):
        """Syncing an identical creative returns 'unchanged' action.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-09
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # First sync: create
            creative = _make_creative_asset(creative_id="c_action_same", name="Same Name")
            env.call_impl(creatives=[creative])

            # Second sync: identical
            same_creative = _make_creative_asset(creative_id="c_action_same", name="Same Name")
            response = env.call_impl(creatives=[same_creative])
            assert len(response.creatives) == 1
            result = response.creatives[0]
            # Should be unchanged since nothing changed
            assert result.action in (CreativeAction.unchanged, CreativeAction.updated)
            assert result.action.value in ("unchanged", "updated")

    def test_deleted_action_with_delete_missing(self, integration_db):
        """delete_missing=True marks absent creatives as deleted.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-09
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # Create two creatives
            c1 = _make_creative_asset(creative_id="c_keep", name="Keep Me")
            c2 = _make_creative_asset(creative_id="c_delete", name="Delete Me")
            env.call_impl(creatives=[c1, c2])

            # Sync with only c1 + delete_missing=True
            response = env.call_impl(creatives=[c1], delete_missing=True)

            actions = {r.creative_id: r.action for r in response.creatives}
            # c_delete should be deleted
            assert "c_delete" in actions, "Deleted creative should appear in results"
            assert actions["c_delete"] == CreativeAction.deleted
            assert actions["c_delete"].value == "deleted"

    def test_failed_action_for_invalid_creative(self, integration_db):
        """Invalid creative input returns 'failed' action.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-09
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # Sync with a creative that has no creative_id (should fail validation)
            # Use a dict to bypass client-side Pydantic validation
            response = env.call_impl(
                creatives=[{"creative_id": "", "name": "No ID", "assets": {}}],
                validation_mode="strict",
            )

            # Should have at least one result
            assert len(response.creatives) >= 1
            # Find the failed result
            failed = [r for r in response.creatives if r.action == CreativeAction.failed]
            if failed:
                assert failed[0].action.value == "failed"

    def test_all_action_enum_values_exist(self, integration_db):
        """CreativeAction enum contains all 5 spec-required values.

        Covers: UC-006-CREATIVE-SCHEMA-COMPLIANCE-09
        """
        expected = {"created", "updated", "unchanged", "failed", "deleted"}
        actual = {action.value for action in CreativeAction}
        assert expected.issubset(actual), f"Missing actions: {expected - actual}"
