"""Tests for unknown targeting field handling.

Targeting uses extra='allow' so unknown buyer-submitted fields (typos, bogus
names) land in model_extra.  The business-logic validator
(validate_unknown_targeting_fields) inspects model_extra and raises
INVALID_REQUEST.  model_dump() strips extra fields from serialized output.
"""

from src.core.schemas import Targeting


class TestExtraAllowCaptures:
    """extra='allow' should capture unknown fields in model_extra."""

    def test_unknown_field_captured_in_model_extra(self):
        t = Targeting(totally_bogus="hello", geo_countries=["US"])
        assert "totally_bogus" in (t.model_extra or {})

    def test_known_field_accepted(self):
        """Known model fields must be accepted; model_extra empty for known fields."""
        t = Targeting(geo_countries=["US"], device_type_any_of=["mobile"])
        assert t.geo_countries is not None
        assert not t.model_extra  # Empty dict or None

    def test_managed_field_accepted(self):
        """Managed-only fields are real model fields, not in model_extra."""
        t = Targeting(axe_include_segment="foo", key_value_pairs={"k": "v"})
        assert t.axe_include_segment == "foo"
        assert not t.model_extra

    def test_v2_normalized_field_accepted(self):
        """v2 field names consumed by normalizer should not land in model_extra."""
        t = Targeting(geo_country_any_of=["CA"])
        assert t.geo_countries is not None
        assert not t.model_extra

    def test_multiple_unknown_fields_captured(self):
        t = Targeting(bogus_one="a", bogus_two="b")
        assert "bogus_one" in (t.model_extra or {})
        assert "bogus_two" in (t.model_extra or {})

    def test_model_dump_strips_extra_fields(self):
        """Extra fields captured in model_extra should NOT appear in model_dump()."""
        t = Targeting(geo_countries=["US"], weather_targeting="sunny")
        dumped = t.model_dump()
        assert "weather_targeting" not in dumped
        assert "geo_countries" in dumped


class TestValidateUnknownTargetingFields:
    """validate_unknown_targeting_fields should report model_extra keys.

    With extra='allow', unknown fields land in model_extra.  The validator
    reports them as violations.
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

    def test_detects_unknown_fields(self):
        """Unknown fields in model_extra should be reported as violations."""
        from src.services.targeting_capabilities import validate_unknown_targeting_fields

        t = Targeting(weather_targeting="sunny", bogus="value")
        violations = validate_unknown_targeting_fields(t)
        assert len(violations) == 2
        assert any("weather_targeting" in v for v in violations)
        assert any("bogus" in v for v in violations)
