"""Test v2 compat version gating.

V2 compat fields (is_fixed, rate, price_guidance.floor) should only be added
for pre-3.0 clients. Clients declaring adcp_version >= 3.0 should receive
clean V3 responses without backward-compat fields.
"""

from src.core.product_conversion import needs_v2_compat


class TestNeedsV2Compat:
    """Test the version gating helper."""

    def test_none_version_needs_compat(self):
        """None version (unknown client) should get v2 compat for safety."""
        assert needs_v2_compat(None) is True

    def test_v1_needs_compat(self):
        """V1.x clients need v2 compat fields."""
        assert needs_v2_compat("1.0.0") is True

    def test_v2_needs_compat(self):
        """V2.x clients need v2 compat fields."""
        assert needs_v2_compat("2.2.0") is True
        assert needs_v2_compat("2.5.0") is True

    def test_v3_does_not_need_compat(self):
        """V3.0+ clients should NOT get v2 compat fields."""
        assert needs_v2_compat("3.0.0") is False

    def test_v3_minor_does_not_need_compat(self):
        """V3.x clients should NOT get v2 compat fields."""
        assert needs_v2_compat("3.1.0") is False
        assert needs_v2_compat("3.5.0") is False

    def test_future_v4_does_not_need_compat(self):
        """Future versions should NOT get v2 compat fields."""
        assert needs_v2_compat("4.0.0") is False

    def test_malformed_version_defaults_to_compat(self):
        """Malformed version strings should default to applying compat (safe default)."""
        assert needs_v2_compat("not-a-version") is True
        assert needs_v2_compat("") is True
