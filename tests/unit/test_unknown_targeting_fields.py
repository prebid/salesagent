"""Tests for unknown targeting field rejection.

Regression tests for salesagent-duu: ensures unknown buyer-submitted targeting
fields (typos, bogus fields) are caught via model_extra inspection rather than
silently accepted by Pydantic's extra='allow'.
"""

from src.core.schemas import Targeting


class TestModelExtraDetectsUnknownFields:
    """model_extra should contain only truly unknown fields."""

    def test_unknown_field_in_model_extra(self):
        t = Targeting(totally_bogus="hello", geo_countries=["US"])
        assert "totally_bogus" in t.model_extra

    def test_known_field_not_in_model_extra(self):
        """Known model fields must not appear in model_extra."""
        t = Targeting(geo_countries=["US"], device_type_any_of=["mobile"])
        assert t.model_extra == {}

    def test_managed_field_not_in_model_extra(self):
        """Managed-only fields are real model fields, not extra."""
        t = Targeting(axe_include_segment="foo", key_value_pairs={"k": "v"})
        assert t.model_extra == {}

    def test_v2_normalized_field_not_in_model_extra(self):
        """v2 field names consumed by normalizer should not leak to model_extra."""
        t = Targeting(geo_country_any_of=["CA"])
        assert t.model_extra == {}
        assert t.geo_countries is not None

    def test_multiple_unknown_fields(self):
        t = Targeting(bogus_one="a", bogus_two="b")
        assert "bogus_one" in t.model_extra
        assert "bogus_two" in t.model_extra


class TestValidateUnknownTargetingFields:
    """validate_unknown_targeting_fields should report model_extra keys."""

    def test_rejects_unknown_field(self):
        from src.services.targeting_capabilities import validate_unknown_targeting_fields

        t = Targeting(totally_bogus="hello", geo_countries=["US"])
        violations = validate_unknown_targeting_fields(t)
        assert len(violations) == 1
        assert "totally_bogus" in violations[0]

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

    def test_error_message_names_fields(self):
        from src.services.targeting_capabilities import validate_unknown_targeting_fields

        t = Targeting(bogus_one="a", bogus_two="b")
        violations = validate_unknown_targeting_fields(t)
        assert len(violations) == 2
        field_names = {v.split(" ")[0] for v in violations}
        assert field_names == {"bogus_one", "bogus_two"}

    def test_empty_targeting_no_violations(self):
        from src.services.targeting_capabilities import validate_unknown_targeting_fields

        t = Targeting()
        violations = validate_unknown_targeting_fields(t)
        assert violations == []
