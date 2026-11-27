"""Editing form-owned mapping fields preserves adapter configuration."""

from copy import deepcopy

import pytest

from src.admin.blueprints.principals import _platform_mappings_from_form


@pytest.mark.parametrize(
    "platform,form,settings,expected_id",
    [
        (
            "mock",
            {"enable_mock": "1"},
            {"hitl_config": {"enabled": True, "mode": "async"}},
            "mock_buyer",
        ),
        (
            "google_ad_manager",
            {"gam_advertiser_id": "123"},
            {"network_code": "456"},
            "123",
        ),
    ],
)
def test_edit_preserves_adapter_settings(platform, form, settings, expected_id):
    stored = {platform: {"advertiser_id": "old", "enabled": False, **settings}}
    before = deepcopy(stored)
    mappings, refusal = _platform_mappings_from_form("buyer", form, stored_mappings=stored)

    assert refusal is None
    assert mappings[platform] == {**settings, "advertiser_id": expected_id, "enabled": True}
    assert stored == before


def test_create_starts_without_stored_mapping():
    mappings, refusal = _platform_mappings_from_form("buyer", {"enable_mock": "1"})

    assert refusal is None
    assert mappings == {"mock": {"advertiser_id": "mock_buyer", "enabled": True}}
