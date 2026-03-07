"""Unit tests for format resolver override logic.

salesagent-c4s: format_resolver uses model_dump() dict roundtrip to merge
platform_config overrides, but model_dump() drops exclude=True fields
(like platform_config), causing the base format's platform_config to be
silently lost during merging.

Note: Must use src.core.schemas.Format (which has exclude=True on platform_config),
not the adcp library Format (which does not).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


class TestGetFormat:
    """Test get_format() top-level resolution paths."""

    def test_product_override_path(self):
        """get_format returns product override when product_id and tenant_id provided."""
        override_format = _make_format("display_300x250", name="Override")

        with patch(
            "src.core.format_resolver._get_product_format_override",
            return_value=override_format,
        ):
            from src.core.format_resolver import get_format

            result = get_format("display_300x250", tenant_id="t1", product_id="p1")

        assert result.name == "Override"

    def test_search_all_agents_when_no_agent_url(self):
        """get_format searches all agents when no agent_url provided."""
        # Use MagicMock because fmt.format_id == format_id compares FormatId to str
        found_format = MagicMock()
        found_format.format_id = "display_300x250"
        found_format.name = "Found"

        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[found_format])

        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry),
            patch("src.core.format_resolver._get_product_format_override", return_value=None),
        ):
            from src.core.format_resolver import get_format

            result = get_format("display_300x250", tenant_id="t1", product_id="p1")

        assert result.name == "Found"

    def test_not_found_raises_valueerror(self):
        """get_format raises ValueError with context when format not found."""
        mock_registry = MagicMock()
        mock_registry.get_format = AsyncMock(return_value=None)

        with patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry):
            from src.core.format_resolver import get_format

            with pytest.raises(ValueError, match="Unknown format_id 'nonexistent'.*agent.*tenant"):
                get_format(
                    "nonexistent",
                    agent_url="https://creative.example.com",
                    tenant_id="t1",
                )

    def test_not_found_without_context_has_minimal_message(self):
        """ValueError message omits agent/tenant when not provided."""
        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=[])

        with patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry):
            from src.core.format_resolver import get_format

            with pytest.raises(ValueError, match="^Unknown format_id 'missing'$"):
                get_format("missing")


class TestProductFormatOverrideEdgeCases:
    """Test _get_product_format_override edge cases not covered by merge tests."""

    def test_format_not_in_overrides_returns_none(self):
        """Returns None when product has overrides but not for requested format_id."""
        with patch("src.core.format_resolver.get_db_session") as mock_db:
            mock_session = mock_db.return_value.__enter__.return_value
            mock_result = mock_session.execute.return_value
            mock_result.fetchone.return_value = ({"format_overrides": {"video_preroll": {"platform_config": {}}}},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("t1", "p1", "display_300x250")

        assert result is None

    def test_no_overrides_key_returns_none(self):
        """Returns None when implementation_config has no format_overrides key."""
        with patch("src.core.format_resolver.get_db_session") as mock_db:
            mock_session = mock_db.return_value.__enter__.return_value
            mock_result = mock_session.execute.return_value
            mock_result.fetchone.return_value = ({"some_other_config": True},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("t1", "p1", "display_300x250")

        assert result is None

    def test_base_format_lookup_fails_returns_none(self):
        """Returns None when base format lookup raises ValueError."""
        with (
            patch("src.core.format_resolver.get_db_session") as mock_db,
            patch(
                "src.core.format_resolver.get_format",
                side_effect=ValueError("not found"),
            ),
        ):
            mock_session = mock_db.return_value.__enter__.return_value
            mock_result = mock_session.execute.return_value
            mock_result.fetchone.return_value = (
                {"format_overrides": {"display_300x250": {"platform_config": {"gam": {}}}}},
            )

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("t1", "p1", "display_300x250")

        assert result is None


class TestListAvailableFormats:
    """Test list_available_formats() error paths."""

    def test_registry_creation_fails_returns_empty(self):
        """Returns empty list when get_creative_agent_registry raises."""
        with patch(
            "src.core.creative_agent_registry.get_creative_agent_registry",
            side_effect=RuntimeError("registry init failed"),
        ):
            from src.core.format_resolver import list_available_formats

            result = list_available_formats(tenant_id="t1")

        assert result == []

    def test_format_fetch_fails_returns_empty(self):
        """Returns empty list when registry.list_all_formats raises."""
        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(side_effect=RuntimeError("fetch failed"))

        with patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry):
            from src.core.format_resolver import list_available_formats

            result = list_available_formats(tenant_id="t1")

        assert result == []
