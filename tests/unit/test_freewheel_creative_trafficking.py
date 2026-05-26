"""Tests for FreeWheelAdapter.add_creative_assets + associate_creatives —
the live-mode creative trafficking wiring.

Live-verified against Talpa 2026-05-13: create_creative → create_creative_instance
→ delete cycle works. These tests pin the orchestration: which FW client
methods get called, with what arguments, in what order, and how the
adapter shapes the AssetStatus / result dicts back to AdCP.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.adapters.freewheel import FreeWheelAdapter
from src.adapters.freewheel.client import FreeWheelError
from src.core.schemas import AssetStatus


@pytest.fixture
def mock_principal():
    p = MagicMock()
    p.principal_id = "p1"
    p.get_adapter_id.return_value = "1356511"
    p.platform_mappings = {"freewheel": {"advertiser_id": "1356511"}}
    return p


def _adapter(mock_principal, dry_run: bool):
    return FreeWheelAdapter(config={"api_token": "t"}, principal=mock_principal, dry_run=dry_run, tenant_id="t1")


class TestAddCreativeAssetsDryRun:
    def test_dry_run_does_not_call_fw(self, mock_principal):
        adapter = _adapter(mock_principal, dry_run=True)
        statuses = adapter.add_creative_assets(
            "freewheel_io_777",
            [{"creative_id": "adcp_cr_1", "name": "Brand Spot", "package_assignments": ["pkg_a"]}],
            datetime.now(UTC),
        )
        assert len(statuses) == 1
        assert statuses[0].creative_id == "adcp_cr_1"
        assert statuses[0].status == "approved"
        # No client because dry_run defers construction
        assert adapter._client is None


class TestAddCreativeAssetsLive:
    def test_creates_one_creative_resource_per_asset(self, mock_principal):
        """Each AdCP asset POSTs one creative_resource and the returned
        AssetStatus carries the FW-assigned id so associate_creatives
        can use it downstream."""
        adapter = _adapter(mock_principal, dry_run=False)
        # The client is real; we replace creatives sub-client with a mock
        adapter._client = MagicMock()
        created_creative = MagicMock(id=335926557)
        adapter._client.creatives.create_creative.return_value = created_creative

        statuses = adapter.add_creative_assets(
            "freewheel_io_777",
            [
                {
                    "creative_id": "adcp_cr_1",
                    "name": "Brand Spot",
                    "url": "https://ads.example.com/vast.xml",
                    "format_id": {
                        "agent_url": "https://creative.adcontextprotocol.org",
                        "id": "video_vast",
                        "duration_ms": 30000,
                    },
                }
            ],
            datetime.now(UTC),
        )

        assert len(statuses) == 1
        assert isinstance(statuses[0], AssetStatus)
        # AssetStatus.creative_id carries the FW-assigned int id (as string)
        # so associate_creatives can call create_creative_instance(creative_id=...)
        assert statuses[0].creative_id == "335926557"
        assert statuses[0].status == "approved"

        adapter._client.creatives.create_creative.assert_called_once_with(
            name="Brand Spot",
            advertiser_ids=[1356511],
            base_ad_unit_id=None,
            external_id="adcp_cr_1",  # AdCP id stamped onto FW external_id for lineage
            renditions=[
                {
                    "uri": "https://ads.example.com/vast.xml",
                    "content_type": "application/xml",
                    "vast_rendition": True,
                    "https_compatibility": "compatible",
                }
            ],
            duration=30,
        )

    def test_returns_failed_when_fw_errors(self, mock_principal):
        adapter = _adapter(mock_principal, dry_run=False)
        adapter._client = MagicMock()
        adapter._client.creatives.create_creative.side_effect = FreeWheelError("validation: rendition required")

        statuses = adapter.add_creative_assets(
            "freewheel_io_777",
            [{"creative_id": "adcp_cr_1", "name": "Brand Spot", "url": "https://ads.example.com/vast.xml"}],
            datetime.now(UTC),
        )

        assert len(statuses) == 1
        assert statuses[0].status == "failed"
        # On failure we echo the AdCP creative_id back (no FW id was assigned)
        # so the caller still has a stable handle.
        assert statuses[0].creative_id == "adcp_cr_1"


class TestAssociateCreativesDryRun:
    def test_dry_run_logs_planned_calls(self, mock_principal):
        adapter = _adapter(mock_principal, dry_run=True)
        results = adapter.associate_creatives(line_item_ids=["90997225"], platform_creative_ids=["335926557"])
        assert len(results) == 1
        assert results[0] == {
            "line_item_id": "90997225",
            "creative_id": "335926557",
            "status": "success",
        }


class TestAssociateCreativesLive:
    def _wire_adapter(self, mock_principal, ad_unit_nodes_by_placement, monkeypatch):
        """Build a live-mode adapter with a mocked client + inventory
        repository. ``ad_unit_nodes_by_placement`` maps placement_id →
        list of ad_unit_node_id strings."""
        adapter = _adapter(mock_principal, dry_run=False)
        adapter._client = MagicMock()

        mock_repo = MagicMock()

        def list_by_type(entity_type, parent_id=None):
            if entity_type != "ad_unit_node":
                return []
            return [MagicMock(entity_id=node_id) for node_id in ad_unit_nodes_by_placement.get(parent_id, [])]

        mock_repo.list_by_type.side_effect = list_by_type
        from tests.helpers.freewheel_adapter_patches import patch_freewheel_db

        patch_freewheel_db(monkeypatch, mock_repo)
        return adapter

    def test_posts_one_creative_instance_per_ad_unit_node(self, mock_principal, monkeypatch):
        """Each placement has N ad_unit_nodes (pre-roll, mid-roll, post-roll
        etc.) — trafficking one creative against a placement means binding
        it to every ad_unit_node beneath it."""
        adapter = self._wire_adapter(
            mock_principal,
            ad_unit_nodes_by_placement={"90997225": ["90997227", "90997226"]},
            monkeypatch=monkeypatch,
        )
        adapter._client.creatives.create_creative_instance.side_effect = [
            {"id": 57369958, "ad_id": 90997227, "creative_id": 335926557, "placement_id": 90997225},
            {"id": 57369959, "ad_id": 90997226, "creative_id": 335926557, "placement_id": 90997225},
        ]

        results = adapter.associate_creatives(line_item_ids=["90997225"], platform_creative_ids=["335926557"])

        # One result per (placement, creative, ad_unit_node) — so 2 here
        assert len(results) == 2
        assert {r["ad_unit_node_id"] for r in results} == {"90997227", "90997226"}
        assert all(r["status"] == "success" for r in results)
        assert all(r["creative_instance_id"] in (57369958, 57369959) for r in results)

        # Verify the wire calls: ad_unit_node_id → ad_id, creative_id stays
        calls = adapter._client.creatives.create_creative_instance.call_args_list
        assert calls[0].kwargs == {"ad_unit_node_id": 90997227, "creative_id": 335926557}
        assert calls[1].kwargs == {"ad_unit_node_id": 90997226, "creative_id": 335926557}

    def test_skips_placement_with_no_cached_ad_unit_nodes(self, mock_principal, monkeypatch):
        """Operators must run inventory sync before they can traffic creatives —
        without ad_unit_node cache rows we have no ad_id to bind to. Skip
        with a clear message rather than fail silently."""
        adapter = self._wire_adapter(mock_principal, ad_unit_nodes_by_placement={}, monkeypatch=monkeypatch)
        results = adapter.associate_creatives(line_item_ids=["90997999"], platform_creative_ids=["335926557"])

        assert len(results) == 1
        assert results[0]["status"] == "skipped"
        assert "inventory sync" in results[0]["message"].lower()
        adapter._client.creatives.create_creative_instance.assert_not_called()

    def test_failed_binding_does_not_abort_remaining_bindings(self, mock_principal, monkeypatch):
        """One ad_unit_node bind failing must not block other bindings —
        operator gets partial-success results, not all-or-nothing."""
        adapter = self._wire_adapter(
            mock_principal,
            ad_unit_nodes_by_placement={"90997225": ["90997227", "90997226"]},
            monkeypatch=monkeypatch,
        )
        adapter._client.creatives.create_creative_instance.side_effect = [
            FreeWheelError("creative already bound"),
            {"id": 57369960, "ad_id": 90997226, "creative_id": 335926557, "placement_id": 90997225},
        ]

        results = adapter.associate_creatives(line_item_ids=["90997225"], platform_creative_ids=["335926557"])

        assert len(results) == 2
        statuses = {r["ad_unit_node_id"]: r["status"] for r in results}
        assert statuses == {"90997227": "failed", "90997226": "success"}
        failed = next(r for r in results if r["status"] == "failed")
        assert "creative already bound" in failed["message"]

    def test_multiple_placements_and_multiple_creatives_fan_out_correctly(self, mock_principal, monkeypatch):
        """Cartesian fan-out: 2 placements × 2 creatives × 1 node each = 4 binds."""
        adapter = self._wire_adapter(
            mock_principal,
            ad_unit_nodes_by_placement={"90997225": ["90997227"], "90997230": ["90997231"]},
            monkeypatch=monkeypatch,
        )
        adapter._client.creatives.create_creative_instance.return_value = {
            "id": 1,
            "ad_id": 0,
            "creative_id": 0,
            "placement_id": 0,
        }

        results = adapter.associate_creatives(
            line_item_ids=["90997225", "90997230"],
            platform_creative_ids=["335926557", "335926558"],
        )

        assert len(results) == 4
        assert adapter._client.creatives.create_creative_instance.call_count == 4
