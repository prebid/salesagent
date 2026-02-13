"""Unit tests for format resolver override logic.

salesagent-c4s: format_resolver uses model_dump() dict roundtrip to merge
platform_config overrides, but model_dump() drops exclude=True fields
(like platform_config), causing the base format's platform_config to be
silently lost during merging.

Note: Must use src.core.schemas.Format (which has exclude=True on platform_config),
not the adcp library Format (which does not).
"""

from unittest.mock import patch

from src.core.schemas import Format
from tests.helpers.adcp_factories import create_test_format_id


def _make_format(format_id_str: str = "display_300x250", name: str = "Test", **kwargs) -> Format:
    """Create a Format using the internal schema (with exclude=True on platform_config)."""
    fid = create_test_format_id(format_id_str)
    assets = [{"item_type": "individual", "asset_id": "primary", "asset_type": "image", "required": True}]
    return Format(format_id=fid, name=name, type="display", assets=assets, **kwargs)


class TestProductFormatOverrideMerge:
    """Test that _get_product_format_override preserves base platform_config."""

    def test_base_platform_config_preserved_during_override(self):
        """Base format's platform_config must survive override merging.

        This is the core bug: model_dump() drops exclude=True fields,
        so base_platform_config was always {} and only override values survived.
        """
        base_format = _make_format(
            "display_300x250",
            name="Medium Rectangle",
            platform_config={"gam": {"width": 300, "height": 250}},
        )

        # Verify our test setup: model_dump drops platform_config
        assert "platform_config" not in base_format.model_dump(), (
            "Test setup error: platform_config should be excluded from model_dump()"
        )
        assert base_format.platform_config == {"gam": {"width": 300, "height": 250}}, (
            "Test setup error: platform_config should be accessible on the model"
        )

        format_overrides = {
            "display_300x250": {
                "platform_config": {
                    "kevel": {"zone_id": 99},
                }
            }
        }

        with (
            patch("src.core.format_resolver.get_db_session") as mock_db,
            patch(
                "src.core.format_resolver.get_format",
                return_value=base_format,
            ),
        ):
            mock_session = mock_db.return_value.__enter__.return_value
            mock_result = mock_session.execute.return_value
            mock_result.fetchone.return_value = ({"format_overrides": format_overrides},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("tenant1", "prod1", "display_300x250")

        assert result is not None
        # Base GAM config must be preserved
        assert result.platform_config is not None, "platform_config was lost entirely"
        assert "gam" in result.platform_config, "Base format's platform_config was lost during override merge"
        assert result.platform_config["gam"] == {"width": 300, "height": 250}
        # Override config must also be present
        assert "kevel" in result.platform_config
        assert result.platform_config["kevel"] == {"zone_id": 99}

    def test_override_merges_into_existing_platform(self):
        """When override targets same platform as base, values merge with override precedence."""
        base_format = _make_format(
            "display_300x250",
            name="Medium Rectangle",
            platform_config={
                "gam": {"width": 300, "height": 250, "ad_unit_id": "original"},
            },
        )

        format_overrides = {
            "display_300x250": {
                "platform_config": {
                    "gam": {"creative_template_id": 12345, "width": 1},
                }
            }
        }

        with (
            patch("src.core.format_resolver.get_db_session") as mock_db,
            patch(
                "src.core.format_resolver.get_format",
                return_value=base_format,
            ),
        ):
            mock_session = mock_db.return_value.__enter__.return_value
            mock_result = mock_session.execute.return_value
            mock_result.fetchone.return_value = ({"format_overrides": format_overrides},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("tenant1", "prod1", "display_300x250")

        assert result is not None
        assert result.platform_config is not None
        gam_config = result.platform_config["gam"]
        # Base values preserved
        assert gam_config["height"] == 250
        assert gam_config["ad_unit_id"] == "original"
        # Override values applied
        assert gam_config["creative_template_id"] == 12345
        # Override takes precedence for conflicts
        assert gam_config["width"] == 1

    def test_no_platform_config_override_preserves_base(self):
        """When override has no platform_config key, base format is returned unchanged."""
        base_format = _make_format(
            "display_300x250",
            name="Medium Rectangle",
            platform_config={"gam": {"width": 300}},
        )

        format_overrides = {
            "display_300x250": {
                "some_other_key": "value",
            }
        }

        with (
            patch("src.core.format_resolver.get_db_session") as mock_db,
            patch(
                "src.core.format_resolver.get_format",
                return_value=base_format,
            ),
        ):
            mock_session = mock_db.return_value.__enter__.return_value
            mock_result = mock_session.execute.return_value
            mock_result.fetchone.return_value = ({"format_overrides": format_overrides},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("tenant1", "prod1", "display_300x250")

        assert result is not None
        # platform_config should be preserved from base
        assert result.platform_config == {"gam": {"width": 300}}

    def test_base_with_none_platform_config(self):
        """When base format has no platform_config, override still applies."""
        base_format = _make_format(
            "display_300x250",
            name="Medium Rectangle",
            # No platform_config — defaults to None
        )

        format_overrides = {
            "display_300x250": {
                "platform_config": {
                    "gam": {"creative_template_id": 99999},
                }
            }
        }

        with (
            patch("src.core.format_resolver.get_db_session") as mock_db,
            patch(
                "src.core.format_resolver.get_format",
                return_value=base_format,
            ),
        ):
            mock_session = mock_db.return_value.__enter__.return_value
            mock_result = mock_session.execute.return_value
            mock_result.fetchone.return_value = ({"format_overrides": format_overrides},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("tenant1", "prod1", "display_300x250")

        assert result is not None
        assert result.platform_config == {"gam": {"creative_template_id": 99999}}
