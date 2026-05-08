"""Tests for the GAM WSDL-shape helpers in `src.adapters.gam.managers.orders`.

Covers the three payload-shape bugs reported in tescoboy issues #146, #147,
and #148 — all surfaced as opaque `KeyError` from the googleads SOAP
serializer when emitted shapes diverged from the GAM WSDL:

- #146: `customTargeting` must be a `CustomCriteriaSet`, not a flat dict
- #147: tz-aware datetimes must be converted to the target zone before
  extracting wall-clock fields, otherwise the emitted instant shifts
- #148: `InventoryTargeting` uses `targetedPlacementIds: string[]`, not a
  list of `{placementId: ...}` objects under the non-existent
  `targetedPlacements` key
"""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.adapters.gam.managers.orders import (
    _build_custom_criteria_set,
    _gam_datetime,
)


class TestGamDatetime:
    """`_gam_datetime` converts to the target tz before extracting fields."""

    def test_utc_aware_converts_to_eastern(self):
        dt = datetime(2026, 5, 7, 15, 22, 0, tzinfo=UTC)
        result = _gam_datetime(dt, "America/New_York")
        assert result == {
            "date": {"year": 2026, "month": 5, "day": 7},
            "hour": 11,  # 15 UTC = 11 EDT in May
            "minute": 22,
            "second": 0,
        }

    def test_pacific_aware_converts_to_eastern(self):
        dt = datetime(2026, 5, 7, 8, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        result = _gam_datetime(dt, "America/New_York")
        assert result["hour"] == 11  # 08 PDT = 11 EDT
        assert result["date"] == {"year": 2026, "month": 5, "day": 7}

    def test_eastern_aware_passthrough_in_eastern(self):
        dt = datetime(2026, 5, 7, 11, 22, 0, tzinfo=ZoneInfo("America/New_York"))
        result = _gam_datetime(dt, "America/New_York")
        assert result["hour"] == 11
        assert result["minute"] == 22

    def test_naive_datetime_passes_through_unchanged(self):
        dt = datetime(2026, 5, 7, 15, 22, 33)
        result = _gam_datetime(dt, "America/New_York")
        assert result == {
            "date": {"year": 2026, "month": 5, "day": 7},
            "hour": 15,
            "minute": 22,
            "second": 33,
        }

    def test_day_rollover_across_zone(self):
        # 03:00 UTC on May 7 = 23:00 May 6 in New York (EDT, UTC-4)
        dt = datetime(2026, 5, 7, 3, 0, 0, tzinfo=UTC)
        result = _gam_datetime(dt, "America/New_York")
        assert result["date"] == {"year": 2026, "month": 5, "day": 6}
        assert result["hour"] == 23


class TestBuildCustomCriteriaSet:
    """`_build_custom_criteria_set` produces a GAM CustomCriteriaSet."""

    def test_single_key_single_value(self):
        result = _build_custom_criteria_set({"17395695": {"values": ["451343125357"], "operator": "IS"}})
        assert result == {
            "logicalOperator": "AND",
            "children": [
                {
                    "xsi_type": "CustomCriteria",
                    "keyId": "17395695",
                    "valueIds": ["451343125357"],
                    "operator": "IS",
                }
            ],
        }

    def test_two_keys_produce_two_children(self):
        result = _build_custom_criteria_set(
            {
                "k1": {"values": ["v1a", "v1b"], "operator": "IS"},
                "k2": {"values": ["v2"], "operator": "IS_NOT"},
            }
        )
        assert result["logicalOperator"] == "AND"
        assert len(result["children"]) == 2
        keys = {c["keyId"] for c in result["children"]}
        assert keys == {"k1", "k2"}

    def test_default_operator_is_is(self):
        result = _build_custom_criteria_set({"k1": {"values": ["v1"]}})
        assert result["children"][0]["operator"] == "IS"

    def test_numeric_inputs_cast_to_string(self):
        result = _build_custom_criteria_set({17395695: {"values": [451343125357]}})
        child = result["children"][0]
        assert child["keyId"] == "17395695"
        assert child["valueIds"] == ["451343125357"]

    def test_empty_values_skipped(self):
        result = _build_custom_criteria_set({"k1": {"values": []}})
        assert result is None

    def test_missing_values_skipped(self):
        result = _build_custom_criteria_set({"k1": {"operator": "IS"}})
        assert result is None

    def test_non_dict_spec_skipped(self):
        result = _build_custom_criteria_set({"k1": "not a dict"})
        assert result is None

    def test_empty_input_returns_none(self):
        assert _build_custom_criteria_set({}) is None


class TestNoLegacyShapeReferences:
    """Ensure the dead WSDL-mismatched literals stay out of `src/adapters/`.

    These literals caused real production failures (issues #146 and #148) and
    survived for months because nothing checked the wire shape against the
    WSDL. This guard catches a regression at commit time.
    """

    @pytest.mark.parametrize(
        "needle",
        [
            '"targetedPlacements"',  # #148 — non-existent field
            "'targetedPlacements'",
        ],
    )
    def test_targeted_placements_not_referenced(self, needle):
        repo_root = Path(__file__).resolve().parents[2]
        adapters = repo_root / "src" / "adapters"
        offenders = [str(p.relative_to(repo_root)) for p in adapters.rglob("*.py") if needle in p.read_text()]
        assert not offenders, (
            f"`{needle}` found in: {offenders}. The correct field is "
            "`targetedPlacementIds: string[]` on `InventoryTargeting`."
        )
