"""Signals page wiring for the GAM adapter."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.admin.blueprints.tenant_signals import _load_gam_signal_rows


def _stub_inventory_row(
    *,
    inventory_type: str,
    inventory_id: str,
    name: str,
    inventory_metadata: dict,
) -> MagicMock:
    row = MagicMock()
    row.inventory_type = inventory_type
    row.inventory_id = inventory_id
    row.name = name
    row.inventory_metadata = inventory_metadata
    return row


class TestLoadGamSignalRows:
    def test_only_preloads_values_for_keys_with_existing_mappings(self, monkeypatch):
        mapped_key = _stub_inventory_row(
            inventory_type="custom_targeting_key",
            inventory_id="100",
            name="genre",
            inventory_metadata={"display_name": "Genre", "type": "PREDEFINED"},
        )
        unmapped_key = _stub_inventory_row(
            inventory_type="custom_targeting_key",
            inventory_id="200",
            name="section",
            inventory_metadata={"display_name": "Section", "type": "PREDEFINED"},
        )
        freeform_key = _stub_inventory_row(
            inventory_type="custom_targeting_key",
            inventory_id="300",
            name="query",
            inventory_metadata={"display_name": "Query", "type": "FREEFORM"},
        )
        mapped_value = _stub_inventory_row(
            inventory_type="custom_targeting_value",
            inventory_id="v1",
            name="sports",
            inventory_metadata={"display_name": "Sports", "custom_targeting_key_id": "100"},
        )
        unmapped_value = _stub_inventory_row(
            inventory_type="custom_targeting_value",
            inventory_id="v2",
            name="news",
            inventory_metadata={"display_name": "News", "custom_targeting_key_id": "100"},
        )
        signal = SimpleNamespace(signal_id="sig_sports", name="Sports fans", tags=["sports"])
        repo = MagicMock()
        repo.list_inventory.side_effect = lambda inv_type: {
            "audience_segment": [],
            "custom_targeting_key": [mapped_key, unmapped_key, freeform_key],
        }[inv_type]
        repo.list_values_for_keys.return_value = {"100": [unmapped_value, mapped_value]}
        monkeypatch.setattr("src.admin.blueprints.tenant_signals.GAMSyncRepository", lambda *_a, **_kw: repo)

        _segments, keys = _load_gam_signal_rows(
            session=MagicMock(),
            tenant_id="t1",
            segment_index={},
            kv_index={("100", "v1"): signal},
            mapped_payload=lambda s: {"signal_id": s.signal_id, "name": s.name, "tags": s.tags},
        )

        repo.list_values_for_keys.assert_called_once_with({"100"})
        assert [k["id"] for k in keys] == ["100", "200", "300"]
        assert keys[0]["lazy_load_values"] is False
        assert keys[0]["mapped_count"] == 1
        assert keys[0]["total_values"] == 2
        assert [v["mapped"] for v in keys[0]["values"]] == [
            None,
            {"signal_id": "sig_sports", "name": "Sports fans", "tags": ["sports"]},
        ]
        assert keys[1]["values"] == []
        assert keys[1]["lazy_load_values"] is True
        assert keys[2]["values"] == []
        assert keys[2]["lazy_load_values"] is False
