"""Tests for unknown targeting field rejection.

Regression tests for salesagent-duu: ensures unknown buyer-submitted targeting
fields (typos, bogus fields) are rejected. With extra='forbid' in dev mode,
unknown fields are caught at construction time via ValidationError.
"""

import pytest

from src.core.schemas import Targeting


class TestForbidRejectsUnknownFields:
    """extra='forbid' should reject unknown fields at construction time."""

    def test_unknown_field_rejected(self):
        with pytest.raises(Exception, match="Extra inputs are not permitted"):
            Targeting(totally_bogus="hello", geo_countries=["US"])

    def test_known_field_accepted(self):
        """Known model fields must be accepted, model_extra stays None (extra='forbid')."""
        t = Targeting(geo_countries=["US"], device_type_any_of=["mobile"])
        assert t.geo_countries is not None
        assert t.model_extra is None

    def test_managed_field_accepted(self):
        """Managed-only fields are real model fields, accepted normally."""
        t = Targeting(axe_include_segment="foo", key_value_pairs={"k": "v"})
        assert t.axe_include_segment == "foo"
        assert t.model_extra is None

    def test_v2_normalized_field_accepted(self):
        """v2 field names consumed by normalizer should not cause rejection."""
        t = Targeting(geo_country_any_of=["CA"])
        assert t.geo_countries is not None
        assert t.model_extra is None

    def test_multiple_unknown_fields_rejected(self):
        with pytest.raises(Exception, match="Extra inputs are not permitted"):
            Targeting(bogus_one="a", bogus_two="b")


class TestValidateUnknownTargetingFields:
    """validate_unknown_targeting_fields should report model_extra keys.

    With extra='forbid', unknown fields are rejected at parse time, so
    model_extra is always empty/None. These tests verify the validator
    handles both modes correctly.
    """

    def test_accepts_all_known_fields(self):
        from src.services.targeting_capabilities import validate_unknown_targeting_fields

        t = Targeting(geo_countries=["US"], device_type_any_of=["mobile"])
        violations = validate_unknown_targeting_fields(t)
        assert violations == []

    def test_accepts_managed_fields(self):
        """Managed fields are known model fields — they should NOT be flagged here.
        (They are caught separately by validate_overlay_targeting's access checks.)"""
        from src.services.targeting_capabilities import validate_unknown_targeting_fields

        t = Targeting(key_value_pairs={"k": "v"}, axe_include_segment="seg")
        violations = validate_unknown_targeting_fields(t)
        assert violations == []

    def test_accepts_v2_normalized_fields(self):
        """v2 fields converted by normalizer should not be flagged."""
        from src.services.targeting_capabilities import validate_unknown_targeting_fields

        t = Targeting(geo_country_any_of=["US"])
        violations = validate_unknown_targeting_fields(t)
        assert violations == []

    def test_empty_targeting_no_violations(self):
        from src.services.targeting_capabilities import validate_unknown_targeting_fields

        t = Targeting()
        violations = validate_unknown_targeting_fields(t)
        assert violations == []
