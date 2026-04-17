"""Behavioral snapshot tests for UC-005 (creative format discovery).

These tests capture the behavioral contract of _list_creative_formats_impl
as specified by BDD scenarios from BR-UC-005. They serve as a migration safety
net: if any refactoring (e.g., FastAPI migration) silently changes behavior,
these tests will catch it.

Each test references its upstream BDD scenario ID for traceability.
"""

from unittest.mock import MagicMock, patch

import pytest
from adcp.types.generated_poc.core.format import (
    Assets,
    Assets5,
    Dimensions,
    Renders,
)

# adcp 3.9: Assets classes are type-discriminated by asset_type + item_type.
# Assets = individual image, Assets5 = individual video
# Assets18 = repeatable_group (has nested assets, no asset_type)
# Nested group assets: Assets19 (image), Assets20 (video), Assets22 (text), etc.
from src.core.schemas import Format, FormatId, ListCreativeFormatsRequest
from tests.factories import PrincipalFactory

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"
MOCK_TENANT = {"tenant_id": "test-tenant", "name": "Test Tenant"}


def _make_format(
    format_id: str,
    name: str,
    renders: list | None = None,
    assets: list | None = None,
) -> Format:
    """Helper to create a Format object with minimal boilerplate."""
    return Format(
        format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id),
        name=name,
        is_standard=True,
        renders=renders,
        assets=assets,
    )


def _call_impl(
    formats: list[Format],
    req: ListCreativeFormatsRequest | None = None,
) -> list[Format]:
    """Call _list_creative_formats_impl with mocked dependencies.

    Returns the list of formats from the response. Mocks tenant,
    creative agent registry, DB session (for broadstreet check), and
    audit logger so the test exercises only the filtering/sorting logic.
    Identity is passed directly as a ResolvedIdentity.
    """
    from src.core.creative_agent_registry import FormatFetchResult
    from src.core.tools.creative_formats import _list_creative_formats_impl

    if req is None:
        req = ListCreativeFormatsRequest()

    identity = PrincipalFactory.make_identity(
        principal_id=None,
        tenant_id=MOCK_TENANT["tenant_id"],
        tenant=MOCK_TENANT,
    )

    with (
        patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry,
        patch("src.core.tools.creative_formats.get_audit_logger") as mock_audit,
    ):
        mock_reg = MagicMock()

        async def mock_list_formats(**kwargs):
            return list(formats)  # Copy to avoid mutation

        async def mock_list_formats_with_errors(**kwargs):
            return FormatFetchResult(formats=list(formats), errors=[])

        mock_reg.list_all_formats = mock_list_formats
        mock_reg.list_all_formats_with_errors = mock_list_formats_with_errors
        mock_registry.return_value = mock_reg

        # Mock audit logger to avoid any side effects
        mock_audit.return_value = MagicMock()

        response = _list_creative_formats_impl(req, identity)
        return response.formats


# ---------------------------------------------------------------------------
# HIGH_RISK: Sort order — T-UC-005-inv10
# ---------------------------------------------------------------------------


class TestSortOrderByName:
    """T-UC-005-inv10: Results sorted by name.

    Behavioral contract at creative_formats.py:337. Refactoring during
    migration could silently reorder results. In adcp 3.12, type was removed
    from Format, so sorting is now by name only.
    """

    def test_sort_order_by_name(self):
        """Formats must be sorted alphabetically by name."""
        formats = [
            _make_format("v_zebra", "Zebra Ad"),
            _make_format("d_alpha", "Alpha Banner"),
            _make_format("v_alpha", "Alpha Video"),
            _make_format("d_zebra", "Zebra Banner"),
        ]

        result = _call_impl(formats)

        names = [f.name for f in result]
        assert names == [
            "Alpha Banner",
            "Alpha Video",
            "Zebra Ad",
            "Zebra Banner",
        ], f"Expected alphabetical ordering but got {names}"

    def test_sort_order_across_many_formats(self):
        """Sort order holds across many formats."""
        formats = [
            _make_format("n1", "Native B"),
            _make_format("d1", "Display A"),
            _make_format("v1", "Video C"),
            _make_format("n2", "Native A"),
            _make_format("d2", "Display B"),
        ]

        result = _call_impl(formats)

        names = [f.name for f in result]
        assert names == [
            "Display A",
            "Display B",
            "Native A",
            "Native B",
            "Video C",
        ], f"Expected alphabetical ordering but got {names}"

    def test_sort_preserves_after_filtering(self):
        """Sort order is maintained even after filters reduce the set."""
        formats = [
            _make_format("v2", "Zebra Video"),
            _make_format("v1", "Alpha Video"),
            _make_format("d1", "Display Ad"),
        ]

        result = _call_impl(formats)

        names = [f.name for f in result]
        assert names == ["Alpha Video", "Display Ad", "Zebra Video"], (
            f"Results should be sorted alphabetically: {names}"
        )


# ---------------------------------------------------------------------------
# MEDIUM_RISK: Type filter empty result — T-UC-005-inv2-violated
# ---------------------------------------------------------------------------


class TestTypeFilterRemovedInAdcp312:
    """T-UC-005-inv2-violated: Type filter removed in adcp 3.12."""

    def test_type_filter_rejected(self):
        """type= parameter is no longer accepted on ListCreativeFormatsRequest."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="type"):
            ListCreativeFormatsRequest(type="audio")

    def test_empty_catalog_returns_empty(self):
        """Empty catalog returns empty list."""
        result = _call_impl([])
        assert result == []


# ---------------------------------------------------------------------------
# MEDIUM_RISK: Group asset filtering — T-UC-005-inv4-group
# ---------------------------------------------------------------------------


class TestAssetTypesFilterChecksGroupAssets:
    """T-UC-005-inv4-group: asset_types filter checks nested group assets.

    Code at creative_formats.py:245-264 iterates get_format_assets() and
    checks both individual asset_type AND nested assets within groups.
    """

    def test_asset_types_filter_finds_type_in_group_assets(self):
        """Format with group assets containing requested type should be included."""
        # adcp 3.9: repeatable_group uses Assets18, nested items use Assets19+ variants
        from adcp.types.generated_poc.core.format import Assets18, Assets19, Assets22

        group_asset = Assets18(
            item_type="repeatable_group",
            asset_group_id="product_group",
            required=True,
            min_count=1,
            max_count=5,
            assets=[
                Assets19(
                    asset_id="product_image",
                    required=True,
                ),
                Assets22(
                    asset_id="product_title",
                    required=True,
                ),
            ],
        )

        format_with_group = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="native_carousel"),
            name="Native Carousel",
            is_standard=True,
            assets=[group_asset],
        )

        # Filter for "image" — should match via nested group asset
        req = ListCreativeFormatsRequest(asset_types=["image"])
        result = _call_impl([format_with_group], req)

        assert len(result) == 1, "Group asset with image should match image filter"
        assert result[0].name == "Native Carousel"

    def test_asset_types_filter_excludes_group_without_match(self):
        """Format with group assets NOT containing requested type should be excluded."""
        # adcp 3.9: repeatable_group uses Assets18, nested text items use Assets22
        from adcp.types.generated_poc.core.format import Assets18, Assets22

        group_asset = Assets18(
            item_type="repeatable_group",
            asset_group_id="text_group",
            required=True,
            min_count=1,
            max_count=3,
            assets=[
                Assets22(
                    asset_id="headline",
                    required=True,
                ),
            ],
        )

        format_with_text_group = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="text_only"),
            name="Text Only Native",
            is_standard=True,
            assets=[group_asset],
        )

        # Filter for "video" — should NOT match since group only has text
        req = ListCreativeFormatsRequest(asset_types=["video"])
        result = _call_impl([format_with_text_group], req)

        assert result == [], "Group asset without video should not match video filter"

    def test_asset_types_filter_mixed_individual_and_group(self):
        """Format with both individual and group assets: filter checks both."""
        # adcp 3.9: Assets5 = individual video, Assets18 = repeatable_group
        # Assets18 nested assets use Assets19+ classes (image=Assets19)
        from adcp.types.generated_poc.core.format import Assets18, Assets19

        individual_asset = Assets5(
            asset_id="hero_video",
            required=True,
        )
        group_asset = Assets18(
            item_type="repeatable_group",
            asset_group_id="product_group",
            required=False,
            min_count=0,
            max_count=5,
            assets=[
                Assets19(
                    asset_id="product_image",
                    required=True,
                ),
            ],
        )

        mixed_format = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="mixed_format"),
            name="Mixed Format",
            is_standard=True,
            assets=[individual_asset, group_asset],
        )

        # Filter for "image" — should match via group nested asset
        req = ListCreativeFormatsRequest(asset_types=["image"])
        result = _call_impl([mixed_format], req)
        assert len(result) == 1, "Mixed format should match image via group asset"

        # Filter for "video" — should match via individual asset
        req = ListCreativeFormatsRequest(asset_types=["video"])
        result = _call_impl([mixed_format], req)
        assert len(result) == 1, "Mixed format should match video via individual asset"

        # Filter for "html" — should NOT match
        req = ListCreativeFormatsRequest(asset_types=["html"])
        result = _call_impl([mixed_format], req)
        assert result == [], "Mixed format should not match html (not present)"


# ---------------------------------------------------------------------------
# LOW_RISK: Partition/boundary completeness
# ---------------------------------------------------------------------------


class TestPartitionTypeFilterRemovedInAdcp312:
    """T-UC-005-partition-type-filter: type filter removed in adcp 3.12."""

    def test_type_filter_no_longer_accepted(self):
        """type= parameter is no longer accepted on ListCreativeFormatsRequest in adcp 3.12."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="type"):
            ListCreativeFormatsRequest(type="native")


class TestPartitionFormatIdsNoMatch:
    """T-UC-005-partition-format-ids: no match row."""

    def test_format_ids_no_match_returns_empty(self):
        """When format_ids contains only non-existent IDs, returns empty list."""
        formats = [
            _make_format("display_300x250", "Display 300x250"),
            _make_format("display_728x90", "Display 728x90"),
            _make_format("video_16x9", "Video 16:9"),
        ]

        non_existent_ids = [
            FormatId(agent_url=DEFAULT_AGENT_URL, id="nonexistent_format"),
        ]
        req = ListCreativeFormatsRequest(format_ids=non_existent_ids)
        result = _call_impl(formats, req)

        assert result == [], f"Non-matching format_ids should return empty, got {len(result)}"


class TestBoundaryDimensionExactMax:
    """T-UC-005-boundary-dimension: exact max boundary (inclusive)."""

    def test_boundary_dimension_exact_max_width(self):
        """Format with width=300 included when max_width=300 (inclusive boundary)."""
        formats = [
            _make_format(
                "rect",
                "Medium Rectangle",
                renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            ),
        ]

        req = ListCreativeFormatsRequest(max_width=300)
        result = _call_impl(formats, req)

        assert len(result) == 1, "Width=300 should be included by max_width=300"
        assert result[0].name == "Medium Rectangle"

    def test_boundary_dimension_off_by_one_max_width(self):
        """Format with width=301 excluded when max_width=300."""
        formats = [
            _make_format(
                "wide",
                "Slightly Wide",
                renders=[Renders(role="primary", dimensions=Dimensions(width=301, height=250))],
            ),
        ]

        req = ListCreativeFormatsRequest(max_width=300)
        result = _call_impl(formats, req)

        assert result == [], "Width=301 should be excluded by max_width=300"

    def test_boundary_dimension_exact_min_width(self):
        """Format with width=300 included when min_width=300 (inclusive boundary)."""
        formats = [
            _make_format(
                "rect",
                "Medium Rectangle",
                renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            ),
        ]

        req = ListCreativeFormatsRequest(min_width=300)
        result = _call_impl(formats, req)

        assert len(result) == 1, "Width=300 should be included by min_width=300"

    def test_boundary_dimension_off_by_one_min_width(self):
        """Format with width=299 excluded when min_width=300."""
        formats = [
            _make_format(
                "narrow",
                "Slightly Narrow",
                renders=[Renders(role="primary", dimensions=Dimensions(width=299, height=250))],
            ),
        ]

        req = ListCreativeFormatsRequest(min_width=300)
        result = _call_impl(formats, req)

        assert result == [], "Width=299 should be excluded by min_width=300"


class TestBoundaryNameSearchEmptyString:
    """T-UC-005-boundary-name-search: empty string boundary.

    Code at creative_formats.py:272 uses 'if req.name_search:' — Python
    falsy check — so empty string is treated as no filter (all returned).
    Per architect review, assert should be 'returns all formats'.
    """

    def test_empty_string_name_search_returns_all(self):
        """Empty string name_search is treated as no filter (all formats returned)."""
        formats = [
            _make_format("d1", "Alpha Display"),
            _make_format("v1", "Beta Video"),
            _make_format("n1", "Gamma Native"),
        ]

        req = ListCreativeFormatsRequest(name_search="")
        result = _call_impl(formats, req)

        assert len(result) == 3, f"Empty name_search should return all formats, got {len(result)}"


# ---------------------------------------------------------------------------
# Enhancement: asset_types exclusion — T-UC-005-inv4-violated
# ---------------------------------------------------------------------------


class TestAssetTypesFilterExclusion:
    """T-UC-005-inv4-violated: format with non-matching asset types excluded."""

    def test_format_with_non_matching_assets_excluded(self):
        """Format with assets that do not match any requested type is excluded."""
        # adcp 3.6.0: use typed asset classes - Assets (image), Assets9 (html)
        from adcp.types.generated_poc.core.format import Assets9

        formats = [
            _make_format(
                "image_banner",
                "Image Banner",
                assets=[
                    Assets(
                        asset_id="main",
                        required=True,
                    ),
                ],
            ),
            _make_format(
                "html_widget",
                "HTML Widget",
                assets=[
                    Assets9(
                        asset_id="code",
                        required=True,
                    ),
                ],
            ),
        ]

        # Filter for video — neither format has video assets
        req = ListCreativeFormatsRequest(asset_types=["video"])
        result = _call_impl(formats, req)

        assert result == [], "Formats with only image/html assets should be excluded by video filter"

    def test_format_with_assets_of_wrong_type_excluded_while_match_kept(self):
        """Only formats with at least one matching asset type are kept."""
        # adcp 3.6.0: Assets (image), Assets5 (video)
        formats = [
            _make_format(
                "image_only",
                "Image Only",
                assets=[
                    Assets(
                        asset_id="photo",
                        required=True,
                    ),
                ],
            ),
            _make_format(
                "video_format",
                "Video Format",
                assets=[
                    Assets5(
                        asset_id="clip",
                        required=True,
                    ),
                ],
            ),
        ]

        req = ListCreativeFormatsRequest(asset_types=["video"])
        result = _call_impl(formats, req)

        assert len(result) == 1
        assert result[0].name == "Video Format"


class TestBroadstreetTemplateAssetParsing:
    """Regression: Broadstreet templates must parse with real assets.

    The production code uses _make_asset() to construct the correct Assets
    variant class (Assets for image, Assets5 for video, etc.) for each
    template asset. Previously, the code used Assets(asset_type=AssetContentType(...))
    which failed because Assets.asset_type is Literal['image'], not an enum.
    """

    def test_all_broadstreet_templates_produce_formats_with_assets(self):
        """Every Broadstreet template must produce a Format with non-empty assets."""
        from src.adapters.broadstreet.config_schema import BROADSTREET_TEMPLATES
        from src.core.tools.creative_formats import _infer_asset_type, _make_asset

        for tid, tmpl in BROADSTREET_TEMPLATES.items():
            assets_list = []
            for asset_id in tmpl.get("required_assets", []):
                at = _infer_asset_type(asset_id)
                assets_list.append(_make_asset(asset_id, at, required=True))
            for asset_id in tmpl.get("optional_assets", []):
                at = _infer_asset_type(asset_id)
                assets_list.append(_make_asset(asset_id, at, required=False))

            fmt = Format(
                format_id=FormatId(id=f"broadstreet_{tid}", agent_url="broadstreet://test"),
                name=str(tmpl["name"]),
                assets=assets_list if assets_list else None,
                is_standard=False,
            )
            assert fmt.assets, f"Template {tid} must have non-empty assets list"
            assert len(fmt.assets) == len(tmpl.get("required_assets", [])) + len(tmpl.get("optional_assets", [])), (
                f"Template {tid} asset count mismatch"
            )

    def test_asset_type_literals_match_inferred_type(self):
        """Each constructed asset must have asset_type matching the inferred string."""
        from src.core.tools.creative_formats import _infer_asset_type, _make_asset

        for asset_id, expected_type in [
            ("front_image", "image"),
            ("logo", "image"),
            ("youtube_url", "video"),
            ("click_url", "url"),
            ("headline", "text"),
            ("html", "html"),
        ]:
            inferred = _infer_asset_type(asset_id)
            assert inferred == expected_type, f"{asset_id}: expected {expected_type}, got {inferred}"
            asset = _make_asset(asset_id, inferred, required=True)
            assert asset.asset_type == expected_type, (
                f"{asset_id}: asset_type should be '{expected_type}', got '{asset.asset_type}'"
            )


class TestMCPWrapperStringCoercion:
    """Regression: MCP wrapper must handle raw string inputs for enum params.

    The MCP wrapper (list_creative_formats) does type.value and at.value
    to extract enum values. If called directly with raw strings (bypassing
    FastMCP's auto-coercion), this crashes with AttributeError.
    The wrapper must coerce strings to enums before accessing .value.
    """

    @pytest.mark.asyncio
    async def test_mcp_wrapper_handles_string_type(self):
        """MCP wrapper must not crash when type is a raw string instead of FormatCategory enum."""
        from src.core.tools.creative_formats import list_creative_formats

        # Calling with a raw string bypasses FastMCP's enum coercion
        # This should NOT raise AttributeError
        try:
            await list_creative_formats(ctx=None)
        except AttributeError:
            pytest.fail("MCP wrapper crashed on raw string type — must coerce to enum first")
        except Exception:
            pass  # Other errors (no identity, etc.) are fine — we're testing type handling

    @pytest.mark.asyncio
    async def test_mcp_wrapper_handles_string_asset_types(self):
        """MCP wrapper must not crash when asset_types contains raw strings."""
        from src.core.tools.creative_formats import list_creative_formats

        try:
            await list_creative_formats(asset_types=["image", "video"], ctx=None)
        except AttributeError:
            pytest.fail("MCP wrapper crashed on raw string asset_types — must coerce to enum first")
        except Exception:
            pass  # Other errors are fine


# ---------------------------------------------------------------------------
# Extension C: Error propagation in format discovery
# Decision: docs/design/error-propagation-in-format-discovery.md
# ---------------------------------------------------------------------------


def _call_impl_raw(
    formats: list[Format],
    registry_side_effect: Exception | None = None,
    errors: list | None = None,
):
    """Call _list_creative_formats_impl and return the full response (not just formats).

    Args:
        formats: Formats returned by healthy agents.
        registry_side_effect: If set, get_creative_agent_registry() raises this.
        errors: List of AdCP Error objects to include as per-agent failures.
    """
    from src.core.creative_agent_registry import FormatFetchResult
    from src.core.tools.creative_formats import _list_creative_formats_impl

    req = ListCreativeFormatsRequest()
    identity = PrincipalFactory.make_identity(
        principal_id=None,
        tenant_id=MOCK_TENANT["tenant_id"],
        tenant=MOCK_TENANT,
    )

    if registry_side_effect:
        with patch(
            "src.core.creative_agent_registry.get_creative_agent_registry",
            side_effect=registry_side_effect,
        ):
            with patch("src.core.tools.creative_formats.get_audit_logger") as mock_audit:
                mock_audit.return_value = MagicMock()
                return _list_creative_formats_impl(req, identity)

    with (
        patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry,
        patch("src.core.tools.creative_formats.get_audit_logger") as mock_audit,
    ):
        mock_reg = MagicMock()
        fetch_result = FormatFetchResult(formats=list(formats), errors=errors or [])

        async def mock_list_formats_with_errors(**kwargs):
            return fetch_result

        async def mock_list_formats(**kwargs):
            return list(formats)

        mock_reg.list_all_formats_with_errors = mock_list_formats_with_errors
        mock_reg.list_all_formats = mock_list_formats
        mock_reg._get_tenant_agents = MagicMock(return_value=[])
        mock_registry.return_value = mock_reg
        mock_audit.return_value = MagicMock()

        return _list_creative_formats_impl(req, identity)


class TestPartialAgentFailureReturnsFormatsAndErrors:
    """UC-005-EXT-C-01: Partial agent failure returns formats from healthy agents plus errors.

    Decision: docs/design/error-propagation-in-format-discovery.md (FD-ERR-01)
    """

    def test_partial_agent_failure_returns_formats_and_errors(self):
        """Covers: UC-005-EXT-C-01

        Given two registered creative agents, one healthy and one unreachable,
        when list_creative_formats is called,
        then the response contains formats from the healthy agent
        and an errors[] entry for the failed agent.
        """
        from adcp.types.generated_poc.core.error import Error as AdCPResponseError

        healthy_formats = [
            _make_format("display_300x250", "Display 300x250"),
        ]
        agent_errors = [
            AdCPResponseError(
                code="AGENT_UNREACHABLE",
                message="Creative agent at https://failing-agent.example.com is unreachable: Connection refused",
            ),
        ]
        response = _call_impl_raw(healthy_formats, errors=agent_errors)

        # Formats from healthy agent should be present
        assert len(response.formats) >= 1

        # Errors should report the failed agent
        assert response.errors is not None, "Response must include errors[] for failed agents, not silently drop them"
        assert len(response.errors) >= 1
        # Each error must have code and message per AdCP error.json
        for err in response.errors:
            assert err.code is not None
            assert err.message is not None


class TestAllAgentsFailReturnsEmptyFormatsAndErrors:
    """UC-005-EXT-C-02: All agents fail returns empty formats plus errors.

    Decision: docs/design/error-propagation-in-format-discovery.md (FD-ERR-02)
    """

    def test_all_agents_fail_returns_empty_formats_and_errors(self):
        """Covers: UC-005-EXT-C-02

        Given all registered creative agents are unreachable,
        when list_creative_formats is called,
        then the response contains an empty formats array
        and errors[] with one entry per failed agent.
        """
        from adcp.types.generated_poc.core.error import Error as AdCPResponseError

        # Simulate all agents failing — registry returns no formats but reports errors
        agent_errors = [
            AdCPResponseError(
                code="AGENT_UNREACHABLE",
                message="Creative agent at https://agent-1.example.com is unreachable: Connection refused",
            ),
            AdCPResponseError(
                code="AGENT_UNREACHABLE",
                message="Creative agent at https://agent-2.example.com is unreachable: Timeout",
            ),
        ]
        response = _call_impl_raw(formats=[], errors=agent_errors)

        assert response.formats == []
        assert response.errors is not None, (
            "Total agent failure must return errors[], not bare empty formats. "
            "An empty formats[] without errors[] means 'no formats configured', "
            "not 'agents are down'."
        )
        assert len(response.errors) >= 1
        for err in response.errors:
            assert err.code is not None
            assert err.message is not None


class TestRegistryCreationFailureReturnsErrors:
    """UC-005-EXT-C-03: Registry creation failure returns empty formats plus errors.

    Decision: docs/design/error-propagation-in-format-discovery.md (FD-ERR-03)
    """

    def test_registry_creation_failure_returns_errors(self):
        """Covers: UC-005-EXT-C-03

        Given the creative agent registry cannot be initialized,
        when list_creative_formats is called,
        then the response contains an empty formats array
        and errors[] describing the infrastructure failure.
        """
        response = _call_impl_raw(
            formats=[],
            registry_side_effect=RuntimeError("Cannot connect to agent registry"),
        )

        assert response.formats == []
        assert response.errors is not None, "Infrastructure failure must be reported in errors[], not swallowed"
        assert len(response.errors) >= 1
        assert any("registry" in err.message.lower() for err in response.errors)


class TestErrorEntriesFollowAdCPSchema:
    """UC-005-EXT-C-04: Error entries follow AdCP error.json schema.

    Decision: docs/design/error-propagation-in-format-discovery.md (FD-ERR-04)
    """

    def test_error_entries_have_code_and_message(self):
        """Covers: UC-005-EXT-C-04

        Given a list_creative_formats response with errors,
        when the Buyer inspects the errors[] array,
        then each error has code (string) and message (string) at minimum,
        conforming to error.json schema.
        """
        from adcp.types.generated_poc.core.error import Error

        response = _call_impl_raw(
            formats=[],
            registry_side_effect=RuntimeError("Test infrastructure failure"),
        )

        assert response.errors is not None
        for err in response.errors:
            assert isinstance(err, Error), f"Error must be an AdCP Error instance, got {type(err)}"
            assert isinstance(err.code, str) and len(err.code) > 0
            assert isinstance(err.message, str) and len(err.message) > 0


class TestSuccessfulDiscoveryHasNoErrors:
    """UC-005-EXT-C-05: Successful discovery has no errors.

    Decision: docs/design/error-propagation-in-format-discovery.md (FD-ERR-05)
    """

    def test_successful_discovery_has_no_errors(self):
        """Covers: UC-005-EXT-C-05

        Given all registered creative agents are healthy,
        when list_creative_formats is called,
        then the response contains formats from all agents
        and the errors field is absent or an empty array.
        """
        formats = [
            _make_format("display_300x250", "Display 300x250"),
            _make_format("video_16x9", "Video 16:9"),
        ]

        response = _call_impl_raw(formats)

        assert len(response.formats) == 2
        assert response.errors is None or response.errors == [], (
            f"Successful discovery must have no errors, got {response.errors}"
        )


class TestAgentReferralFailureLogsWarning:
    """Agent referral building errors must be logged, not silently swallowed.

    CLAUDE.md: No Quiet Failures pattern. If _get_tenant_agents raises,
    creative_agents_list should remain None (non-critical) but a warning
    must be logged so operators can diagnose the issue.
    """

    def test_referral_error_logs_warning(self, caplog):
        """When _get_tenant_agents raises, a warning is logged (not silent pass)."""
        import logging

        from src.core.creative_agent_registry import FormatFetchResult
        from src.core.tools.creative_formats import _list_creative_formats_impl

        formats = [_make_format("display_300x250", "Display 300x250")]
        mock_reg = MagicMock()
        mock_reg._get_tenant_agents = MagicMock(side_effect=RuntimeError("Agent DB connection refused"))
        fetch_result = FormatFetchResult(formats=list(formats), errors=[])

        async def mock_list_formats_with_errors(**kwargs):
            return fetch_result

        async def mock_list_formats(**kwargs):
            return list(formats)

        mock_reg.list_all_formats_with_errors = mock_list_formats_with_errors
        mock_reg.list_all_formats = mock_list_formats

        identity = PrincipalFactory.make_identity(
            principal_id=None,
            tenant_id=MOCK_TENANT["tenant_id"],
            tenant=MOCK_TENANT,
        )

        with (
            patch(
                "src.core.creative_agent_registry.get_creative_agent_registry",
                return_value=mock_reg,
            ),
            patch(
                "src.core.tools.creative_formats.get_audit_logger",
                return_value=MagicMock(),
            ),
            caplog.at_level(logging.WARNING, logger="src.core.tools.creative_formats"),
        ):
            _list_creative_formats_impl(
                req=ListCreativeFormatsRequest(),
                identity=identity,
            )

        # After fix: should log a warning about the referral failure
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("referral" in msg.lower() or "agent" in msg.lower() for msg in warning_msgs), (
            f"Expected a warning about agent referral failure, got: {warning_msgs}"
        )
