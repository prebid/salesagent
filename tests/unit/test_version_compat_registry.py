"""Tests for version compat transform registry.

Validates that:
- apply_version_compat() exists and applies registered transforms
- get_products transform is registered and works correctly
- Unknown tools pass through unchanged
- V3+ clients skip transforms entirely
- Transform registration works

beads: salesagent-b61l.14
"""


# ---------------------------------------------------------------------------
# Registry API Tests
# ---------------------------------------------------------------------------


class TestVersionCompatRegistry:
    """Verify the version compat registry exists and works."""

    def test_apply_version_compat_exists(self):
        """apply_version_compat function must exist."""
        from src.core.version_compat import apply_version_compat

        assert callable(apply_version_compat)

    def test_get_products_transform_registered(self):
        """get_products must have a registered v2 compat transform."""
        from src.core.version_compat import apply_version_compat

        response = {
            "products": [
                {
                    "product_id": "p1",
                    "pricing_options": [{"pricing_model": "cpm", "fixed_price": 5.0}],
                }
            ]
        }
        result = apply_version_compat("get_products", response, "2.0.0")
        # Transform should add is_fixed and rate fields
        po = result["products"][0]["pricing_options"][0]
        assert po["is_fixed"] is True
        assert po["rate"] == 5.0

    def test_v3_clients_skip_transform(self):
        """V3+ clients should get clean response without compat fields."""
        from src.core.version_compat import apply_version_compat

        response = {
            "products": [
                {
                    "product_id": "p1",
                    "pricing_options": [{"pricing_model": "cpm", "fixed_price": 5.0}],
                }
            ]
        }
        result = apply_version_compat("get_products", response, "3.0.0")
        po = result["products"][0]["pricing_options"][0]
        assert "is_fixed" not in po
        assert "rate" not in po

    def test_unknown_tool_passes_through(self):
        """Unknown tool names should return response unchanged."""
        from src.core.version_compat import apply_version_compat

        response = {"data": "unchanged"}
        result = apply_version_compat("nonexistent_tool", response, "2.0.0")
        assert result == {"data": "unchanged"}

    def test_none_version_applies_compat(self):
        """None adcp_version should apply compat (safe default)."""
        from src.core.version_compat import apply_version_compat

        response = {
            "products": [
                {
                    "product_id": "p1",
                    "pricing_options": [{"pricing_model": "cpm", "fixed_price": 3.0}],
                }
            ]
        }
        result = apply_version_compat("get_products", response, None)
        po = result["products"][0]["pricing_options"][0]
        assert po["is_fixed"] is True
        assert po["rate"] == 3.0
