"""Tests for unknown targeting field rejection.

Regression tests for : ensures unknown buyer-submitted targeting
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

    # REMOVED: test_managed_field_accepted. It constructed
    # ``Targeting(axe_include_segment="foo", key_value_pairs={"k": "v"})``; there is no
    # seller-managed key/value field any more (src/core/schemas/_base.py names the reason:
    # pinned core/targeting.json declares none, and Field(exclude=True) kept the one that
    # lived there off the wire, off persistence and out of the idempotency hash alike, so
    # it could never round-trip). With that argument gone the test asserted only that a
    # declared field is accepted, which test_known_field_accepted above already grades.

    def test_v2_flat_field_rejected(self):
        """A v2 FLAT geo field is rejected — there is no normalizer to consume it.

        adcp 3.1.1 core/targeting.json declares ``geo_countries`` (ISO 3166-1 alpha-2
        array) and no flat ``geo_country_any_of``, and ``Targeting`` reshapes nothing on
        the way in. This test asserted the opposite while the normalizer existed; it is
        inverted rather than deleted because the rejection is the contract, and a
        re-added normalizer would silently reopen a spelling the pin does not define.
        """
        with pytest.raises(Exception, match="Extra inputs are not permitted"):
            Targeting(geo_country_any_of=["CA"])

    def test_multiple_unknown_fields_rejected(self):
        with pytest.raises(Exception, match="Extra inputs are not permitted"):
            Targeting(bogus_one="a", bogus_two="b")


class TestValidateUnknownTargetingFields:
    """Unknown targeting fields are rejected by PYDANTIC, not by business logic.

    ``Targeting`` resolves ``extra`` through ``get_pydantic_extra_mode()``: ``forbid`` in
    dev/CI (rejected at construction, as the tests above assert) and ``ignore`` in
    production (silently dropped). A business-logic ``model_extra`` scan therefore could
    never fire, and was deleted in salesagent-3dawm.9.
    """
