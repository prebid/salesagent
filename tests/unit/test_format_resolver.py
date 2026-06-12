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
# DECISION_BACKED (5 tests):
#   test_base_platform_config_preserved_during_override — bug fix (salesagent-c4s)
#   test_override_merges_into_existing_platform — bug fix (salesagent-c4s)
#   test_registry_creation_failure_returns_error_context — FD-ERR-03
#   test_format_fetch_failure_returns_error_context — FD-ERR-03
#   test_filters_are_applied_locally_after_unfiltered_registry_fetch — FD-ERR-03 regression
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

from unittest.mock import AsyncMock, MagicMock, patch

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
                    "triton": {"zone_id": 99},
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
        assert "triton" in result.platform_config
        assert result.platform_config["triton"] == {"zone_id": 99}

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
        """get_format searches all agents when agent_url is None."""
        mock_fmt = _make_format("display_300x250", name="Found Format")

        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=[mock_fmt]),
        ):
            from src.core.format_resolver import get_format

            result = get_format("display_300x250", tenant_id="t1")

        assert result.name == "Found Format"

    def test_search_all_agents_matches_legacy_request_to_canonical_parameters(self):
        """get_format maps legacy fixed-size IDs to canonical parameterized formats."""
        mock_fmt = _make_format("display_image", name="Found Format")
        mock_fmt.format_id.width = 300
        mock_fmt.format_id.height = 250

        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=[mock_fmt]),
        ):
            from src.core.format_resolver import get_format

            result = get_format("display_300x250", tenant_id="t1")

        assert result.name == "Found Format"

    def test_search_all_agents_does_not_match_generic_request_to_parameterized_variant(self):
        """A generic format lookup should not choose an arbitrary fixed-size variant."""
        mock_fmt = _make_format("display_image", name="Found Format")
        mock_fmt.format_id.width = 300
        mock_fmt.format_id.height = 250

        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=[mock_fmt]),
        ):
            from src.core.format_resolver import get_format

            with pytest.raises(AdCPNotFoundError, match="Unknown format_id 'display_image'"):
                get_format("display_image", tenant_id="t1")

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

    def test_registry_creation_failure_returns_error_context(self):
        """Error-aware helper distinguishes registry failure from empty catalog."""
        with patch(
            "src.core.creative_agent_registry.get_creative_agent_registry",
            side_effect=RuntimeError("Registry initialization failed"),
        ):
            from src.core.format_resolver import list_available_formats_with_errors

            result = list_available_formats_with_errors(tenant_id="t1")

        assert result.formats == []
        assert len(result.errors) == 1
        assert result.errors[0].code == "REGISTRY_ERROR"
        assert "Registry initialization failed" in result.errors[0].message

    def test_format_fetch_failure_returns_error_context(self):
        """Error-aware helper distinguishes fetch failure from empty catalog."""
        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry"),
            patch(
                "src.core.format_resolver.run_async_in_sync_context",
                side_effect=RuntimeError("Connection failed"),
            ),
        ):
            from src.core.format_resolver import list_available_formats_with_errors

            result = list_available_formats_with_errors(tenant_id="t1")

        assert result.formats == []
        assert len(result.errors) == 1
        assert result.errors[0].code == "FORMAT_DISCOVERY_ERROR"
        assert "Connection failed" in result.errors[0].message

    def test_filters_are_applied_locally_after_unfiltered_registry_fetch(self):
        """Filtered product-form discovery must not force a remote filtered agent call."""
        from src.core.creative_agent_registry import FormatFetchResult

        display_format = _make_format("display_300x250", name="Display 300x250")
        video_format = Format(
            format_id=create_test_format_id("video_640x480"),
            name="Video 640x480",
            type="video",
            assets=[
                {"item_type": "individual", "asset_id": "video", "asset_type": "video", "required": True},
            ],
        )
        registry = MagicMock()
        registry.list_all_formats_with_errors = AsyncMock(
            return_value=FormatFetchResult(formats=[display_format, video_format], errors=[])
        )

        with patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=registry):
            from src.core.format_resolver import list_available_formats_with_errors

            result = list_available_formats_with_errors(
                tenant_id="t1",
                asset_types=["display"],
                name_search="300x250",
            )

        registry.list_all_formats_with_errors.assert_called_once_with(tenant_id="t1")
        assert result.errors == []
        assert [fmt.format_id.id for fmt in result.formats] == ["display_300x250"]

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
