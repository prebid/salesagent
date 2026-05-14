"""Unit tests for format resolver override logic and coverage gaps.

salesagent-c4s: format_resolver uses model_dump() dict roundtrip to merge
platform_config overrides, but model_dump() drops exclude=True fields
(like platform_config), causing the base format's platform_config to be
silently lost during merging.

salesagent-uujr: Cover get_format(), _get_product_format_override() edge cases,
and list_available_formats() error paths — 67% → 100%.

Note: Must use src.core.schemas.Format (which has exclude=True on platform_config),
not the adcp library Format (which does not).

# --- Test Source-of-Truth Audit ---
# Audited: 2026-03-07
#
# SPEC_BACKED (2 tests):
#   test_search_all_agents_no_match_raises_not_found — AdCP error.json: unknown format_id is an error
#   test_success_returns_formats — AdCP list-creative-formats-response.json: returns formats array
#
# DECISION_BACKED (2 tests):
#   test_base_platform_config_preserved_during_override — bug fix (salesagent-c4s)
#   test_override_merges_into_existing_platform — bug fix (salesagent-c4s)
#
# CHARACTERIZATION (10 tests):
#   test_no_platform_config_override_preserves_base — locks: base preserved when no override
#   test_base_with_none_platform_config — locks: override applies to None base
#   test_product_override_path — locks: resolution order (override → agent → error)
#   test_product_override_none_falls_through_to_agent — locks: fallthrough path
#   test_search_all_agents_no_agent_url — locks: search-all behavior
#   test_not_found_error_includes_agent_url — locks: error message format
#   test_not_found_error_no_agent_url_no_tenant — locks: minimal error format
#   test_no_product_row_returns_none — locks: None for missing DB row
#   test_format_id_not_in_overrides_returns_none — locks: None for missing format_id
#   test_no_format_overrides_key_returns_none — locks: None for missing key
#
# SUSPECT (3 tests):
#   test_base_format_lookup_fails_returns_none — salesagent-z4zl: swallows AdCPNotFoundError silently
#   test_registry_creation_fails_returns_empty — salesagent-z60b: infrastructure error → []
#   test_format_fetch_fails_returns_empty — salesagent-z60b: connection error → []
# ---
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.exceptions import AdCPNotFoundError
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


# ---------------------------------------------------------------------------
# get_format() — top-level resolution paths
# ---------------------------------------------------------------------------


class TestGetFormat:
    """Tests for get_format() covering product override, all-agents search, and error paths."""

    def test_product_override_path(self):
        """get_format returns product override when product_id and tenant_id are provided."""
        override_format = _make_format("display_300x250", name="Override Format")

        with patch(
            "src.core.format_resolver._get_product_format_override",
            return_value=override_format,
        ):
            from src.core.format_resolver import get_format

            result = get_format(
                "display_300x250",
                agent_url="https://agent.example.com",
                tenant_id="t1",
                product_id="prod_1",
            )

        assert result.name == "Override Format"

    def test_product_override_none_falls_through_to_agent(self):
        """get_format falls through to agent when product override returns None."""
        agent_format = _make_format("display_300x250", name="Agent Format")

        with (
            patch("src.core.format_resolver._get_product_format_override", return_value=None),
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=agent_format),
        ):
            from src.core.format_resolver import get_format

            result = get_format(
                "display_300x250",
                agent_url="https://agent.example.com",
                tenant_id="t1",
                product_id="prod_1",
            )

        assert result.name == "Agent Format"

    def test_search_all_agents_no_agent_url(self):
        """get_format searches all agents when agent_url is None.

        Note: The search loop at L53-56 compares Format.format_id (FormatId)
        with the string parameter. To match, we use a mock with matching
        format_id attribute instead of a real Format object.
        """
        mock_fmt = MagicMock()
        mock_fmt.format_id = "display_300x250"  # Plain string to match parameter
        mock_fmt.name = "Found Format"

        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=[mock_fmt]),
        ):
            from src.core.format_resolver import get_format

            result = get_format("display_300x250", tenant_id="t1")

        assert result.name == "Found Format"

    def test_search_all_agents_no_match_raises_not_found(self):
        """get_format raises AdCPNotFoundError when format not found in any agent."""
        mock_fmt = MagicMock()
        mock_fmt.format_id = "video_1920x1080"  # Different format — won't match

        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=[mock_fmt]),
        ):
            from src.core.format_resolver import get_format

            with pytest.raises(AdCPNotFoundError, match="Unknown format_id 'display_300x250'"):
                get_format("display_300x250", tenant_id="t1")

    def test_not_found_error_includes_agent_url(self):
        """AdCPNotFoundError message includes agent_url when provided."""
        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=None),
        ):
            from src.core.format_resolver import get_format

            with pytest.raises(AdCPNotFoundError, match="from agent https://agent.example.com") as exc_info:
                get_format("display_300x250", agent_url="https://agent.example.com", tenant_id="t1")

            assert "for tenant t1" in str(exc_info.value)

    def test_not_found_error_no_agent_url_no_tenant(self):
        """AdCPNotFoundError message is minimal without agent_url and tenant_id."""
        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=[]),
        ):
            from src.core.format_resolver import get_format

            with pytest.raises(AdCPNotFoundError, match="Unknown format_id 'nonexistent'") as exc_info:
                get_format("nonexistent")

            error_msg = str(exc_info.value)
            assert "from agent" not in error_msg
            assert "for tenant" not in error_msg


# ---------------------------------------------------------------------------
# _get_product_format_override() — edge case paths
# ---------------------------------------------------------------------------


class TestProductFormatOverrideEdgeCases:
    """Edge cases for _get_product_format_override not covered by merge tests."""

    def test_no_product_row_returns_none(self):
        """Returns None when product doesn't exist in DB."""
        with patch("src.core.format_resolver.get_db_session") as mock_db:
            mock_session = mock_db.return_value.__enter__.return_value
            mock_session.execute.return_value.fetchone.return_value = None

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("t1", "nonexistent", "display_300x250")

        assert result is None

    def test_format_id_not_in_overrides_returns_none(self):
        """Returns None when format_id is not in format_overrides."""
        with patch("src.core.format_resolver.get_db_session") as mock_db:
            mock_session = mock_db.return_value.__enter__.return_value
            mock_session.execute.return_value.fetchone.return_value = (
                {"format_overrides": {"other_format": {"platform_config": {}}}},
            )

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("t1", "prod1", "display_300x250")

        assert result is None

    def test_no_format_overrides_key_returns_none(self):
        """Returns None when implementation_config has no format_overrides key."""
        with patch("src.core.format_resolver.get_db_session") as mock_db:
            mock_session = mock_db.return_value.__enter__.return_value
            mock_session.execute.return_value.fetchone.return_value = ({"some_other_config": "value"},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("t1", "prod1", "display_300x250")

        assert result is None

    # SUSPECT(salesagent-z4zl): swallows AdCPNotFoundError — should override path propagate?
    def test_base_format_lookup_fails_returns_none(self):
        """Returns None when recursive get_format call raises AdCPNotFoundError."""
        format_overrides = {"display_300x250": {"platform_config": {"gam": {"width": 1}}}}

        with (
            patch("src.core.format_resolver.get_db_session") as mock_db,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch(
                "src.core.format_resolver.get_format",
                side_effect=AdCPNotFoundError("Format not found"),
            ),
        ):
            mock_session = mock_db.return_value.__enter__.return_value
            mock_session.execute.return_value.fetchone.return_value = ({"format_overrides": format_overrides},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("t1", "prod1", "display_300x250")

        assert result is None


# ---------------------------------------------------------------------------
# list_available_formats() — error paths
# ---------------------------------------------------------------------------


class TestListAvailableFormats:
    """Tests for list_available_formats() error and success paths."""

    # SUSPECT(salesagent-z60b): infrastructure error silently returns [] — should it propagate?
    def test_registry_creation_fails_returns_empty(self):
        """Returns empty list when get_creative_agent_registry raises."""
        with patch(
            "src.core.creative_agent_registry.get_creative_agent_registry",
            side_effect=RuntimeError("Registry initialization failed"),
        ):
            from src.core.format_resolver import list_available_formats

            result = list_available_formats(tenant_id="t1")

        assert result == []

    # SUSPECT(salesagent-z60b): connection error silently returns [] — should it propagate?
    def test_format_fetch_fails_returns_empty(self):
        """Returns empty list when list_all_formats raises."""
        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch(
                "src.core.format_resolver.run_async_in_sync_context",
                side_effect=RuntimeError("Connection failed"),
            ),
        ):
            from src.core.format_resolver import list_available_formats

            result = list_available_formats(tenant_id="t1")

        assert result == []

    def test_success_returns_formats(self):
        """Returns formats from registry on success."""
        fmt1 = _make_format("display_300x250", name="Format 1")
        fmt2 = _make_format("display_728x90", name="Format 2")

        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch(
                "src.core.format_resolver.run_async_in_sync_context",
                return_value=[fmt1, fmt2],
            ),
        ):
            from src.core.format_resolver import list_available_formats

            result = list_available_formats(tenant_id="t1")

        assert len(result) == 2
        assert result[0].name == "Format 1"
