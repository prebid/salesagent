"""Unit tests for GAM AXE segment targeting translation.

Tests that axe_include_segment and axe_exclude_segment fields (AdCP pre-3.0;
deprecated in 3.0.x in favor of TMP provider fields) are correctly translated
to GAM custom targeting key-value pairs using three separate custom targeting
keys per AdCP spec.

The targeting manager is handed the seller's targeting config directly, so no
database is read, and value names resolve through the GAM stand-in.
"""

import pytest

from src.adapters.gam.managers.targeting import GAMTargetingManager
from src.core.exceptions import AdCPCapabilityNotSupportedError
from src.core.schemas import Targeting
from tests.helpers.gam_client import gam_criteria_set, gam_criterion, stub_gam_client_manager

#: The seller's targeting config with all three AXE keys set, each mapped to its GAM key ID.
ALL_AXE_KEYS_CONFIGURED = {
    "axe_include_key": "audience_include",
    "axe_exclude_key": "audience_exclude",
    "axe_macro_key": "audience_macro",
    "custom_targeting_keys": {"audience_include": "12345", "audience_exclude": "67890", "audience_macro": "11111"},
}

#: The seller's targeting config with no AXE key set and no key synced from GAM.
NO_AXE_KEYS_CONFIGURED = {
    "axe_include_key": None,
    "axe_exclude_key": None,
    "axe_macro_key": None,
    "custom_targeting_keys": {},
}

#: Segment and key-value names the GAM stand-in holds, by (key_id, name), and their value IDs.
INCLUDE_SEGMENT, EXCLUDE_SEGMENT, FOOTBALL = 901, 902, 903
GAM_VALUES = {(12345, "x8dj3k"): INCLUDE_SEGMENT, (67890, "y9kl4m"): EXCLUDE_SEGMENT, (22222, "football"): FOOTBALL}


def _targeting_built(targeting_config: dict, targeting_overlay: Targeting) -> dict:
    """The GAM targeting the manager builds for ``targeting_overlay`` under ``targeting_config``."""
    gam_client = stub_gam_client_manager(custom_targeting_values=GAM_VALUES).get_client()
    manager = GAMTargetingManager("tenant_123", gam_client=gam_client, targeting_config=targeting_config)
    return manager.build_targeting(targeting_overlay)


def test_axe_include_segment_translates_to_custom_targeting():
    """axe_include_segment targets the configured include key (12345)."""
    sent = _targeting_built(ALL_AXE_KEYS_CONFIGURED, Targeting(geo_countries=["US"], axe_include_segment="x8dj3k"))

    assert sent["customTargeting"] == gam_criteria_set(
        "OR", [gam_criteria_set("AND", [gam_criterion(12345, [INCLUDE_SEGMENT])])]
    )


def test_axe_exclude_segment_translates_to_negative_custom_targeting():
    """axe_exclude_segment excludes on the configured exclude key (67890)."""
    sent = _targeting_built(ALL_AXE_KEYS_CONFIGURED, Targeting(geo_countries=["US"], axe_exclude_segment="y9kl4m"))

    assert sent["customTargeting"] == gam_criteria_set(
        "OR", [gam_criteria_set("AND", [gam_criterion(67890, [EXCLUDE_SEGMENT], "IS_NOT")])]
    )


def test_axe_segments_both_include_and_exclude():
    """Both segments are ANDed in one branch, each on its own key."""
    sent = _targeting_built(
        ALL_AXE_KEYS_CONFIGURED,
        Targeting(geo_countries=["US"], axe_include_segment="x8dj3k", axe_exclude_segment="y9kl4m"),
    )

    assert sent["customTargeting"] == gam_criteria_set(
        "OR",
        [
            gam_criteria_set(
                "AND", [gam_criterion(12345, [INCLUDE_SEGMENT]), gam_criterion(67890, [EXCLUDE_SEGMENT], "IS_NOT")]
            )
        ],
    )


def test_axe_segments_combine_with_other_custom_targeting():
    """A segment is ANDed with the buyer's own GAM key-values in the same branch."""
    sent = _targeting_built(
        ALL_AXE_KEYS_CONFIGURED,
        Targeting(
            geo_countries=["US"],
            custom={"gam": {"key_values": {"22222": "football"}}},
            axe_include_segment="x8dj3k",
        ),
    )

    assert sent["customTargeting"] == gam_criteria_set(
        "OR",
        [gam_criteria_set("AND", [gam_criterion(22222, [FOOTBALL]), gam_criterion(12345, [INCLUDE_SEGMENT])])],
    )


def test_axe_segments_optional():
    """Without a segment there is geo targeting and no custom targeting."""
    sent = _targeting_built(ALL_AXE_KEYS_CONFIGURED, Targeting(geo_countries=["US"]))

    assert "geoTargeting" in sent
    assert "customTargeting" not in sent


@pytest.mark.parametrize(
    "overlay",
    [
        pytest.param(Targeting(geo_countries=["US"], axe_include_segment="x8dj3k"), id="include"),
        pytest.param(Targeting(geo_countries=["US"], axe_exclude_segment="y9kl4m"), id="exclude"),
    ],
)
def test_axe_segment_fails_if_its_key_is_not_configured(overlay):
    """A segment whose AXE key the seller has not configured is a capability the seller lacks."""
    with pytest.raises(AdCPCapabilityNotSupportedError):
        _targeting_built(NO_AXE_KEYS_CONFIGURED, overlay)
