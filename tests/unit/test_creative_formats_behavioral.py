"""Behavioral snapshot tests for UC-005 (creative format discovery).

These tests capture the behavioral contract of _list_creative_formats_impl
as specified by BDD scenarios from BR-UC-005. They serve as a migration safety
net: if any refactoring (e.g., FastAPI migration) silently changes behavior,
these tests will catch it.

Each test references its upstream BDD scenario ID for traceability.
"""

from unittest.mock import MagicMock, patch

from adcp.types import AssetContentType
from adcp.types.generated_poc.core.format import (
    Asset,
    Assets,
    Assets5,
    Dimensions,
    Renders,
)
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.schemas import Format, FormatId, ListCreativeFormatsRequest

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"
MOCK_TENANT = {"tenant_id": "test-tenant", "name": "Test Tenant"}


def _make_format(
    format_id: str,
    name: str,
    type: FormatCategory = FormatCategory.display,
    renders: list | None = None,
    assets: list | None = None,
) -> Format:
    """Helper to create a Format object with minimal boilerplate."""
    return Format(
        format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id),
        name=name,
        type=type,
        is_standard=True,
        renders=renders,
        assets=assets,
    )


def _call_impl(
    formats: list[Format],
    req: ListCreativeFormatsRequest | None = None,
) -> list[Format]:
    """Call _list_creative_formats_impl with mocked dependencies.

    Returns the list of formats from the response. Mocks auth, tenant,
    creative agent registry, DB session (for broadstreet check), and
    audit logger so the test exercises only the filtering/sorting logic.
    """
    from src.core.tools.creative_formats import _list_creative_formats_impl

    if req is None:
        req = ListCreativeFormatsRequest()

    with (
        patch(
            "src.core.tools.creative_formats.get_principal_from_context",
            return_value=(None, MOCK_TENANT),
        ),
        patch("src.core.tools.creative_formats.set_current_tenant"),
        patch(
            "src.core.tools.creative_formats.get_current_tenant",
            return_value=MOCK_TENANT,
        ),
        patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry,
        patch("src.core.tools.creative_formats.get_audit_logger") as mock_audit,
    ):
        mock_reg = MagicMock()

        async def mock_list_formats(**kwargs):
            return list(formats)  # Copy to avoid mutation

        mock_reg.list_all_formats = mock_list_formats
        mock_registry.return_value = mock_reg

        # Mock audit logger to avoid any side effects
        mock_audit.return_value = MagicMock()

        response = _list_creative_formats_impl(req, None)
        return response.formats


# ---------------------------------------------------------------------------
# HIGH_RISK: Sort order — T-UC-005-inv10
# ---------------------------------------------------------------------------


class TestSortOrderTypeThenName:
    """T-UC-005-inv10: Results sorted by type.value then name.

    Behavioral contract at creative_formats.py:296. Refactoring during
    migration could silently reorder results.
    """

    def test_sort_order_type_then_name(self):
        """Formats must be sorted by (type.value, name) — display before video,
        alphabetical within each type."""
        formats = [
            _make_format("v_zebra", "Zebra Ad", type=FormatCategory.video),
            _make_format("d_alpha", "Alpha Banner", type=FormatCategory.display),
            _make_format("v_alpha", "Alpha Video", type=FormatCategory.video),
            _make_format("d_zebra", "Zebra Banner", type=FormatCategory.display),
        ]

        result = _call_impl(formats)

        names = [f.name for f in result]
        assert names == [
            "Alpha Banner",
            "Zebra Banner",
            "Alpha Video",
            "Zebra Ad",
        ], f"Expected display(alpha, zebra), video(alpha, zebra) but got {names}"

    def test_sort_order_across_three_types(self):
        """Sort order holds across more than two types."""
        formats = [
            _make_format("n1", "Native B", type=FormatCategory.native),
            _make_format("d1", "Display A", type=FormatCategory.display),
            _make_format("v1", "Video C", type=FormatCategory.video),
            _make_format("n2", "Native A", type=FormatCategory.native),
            _make_format("d2", "Display B", type=FormatCategory.display),
        ]

        result = _call_impl(formats)

        names = [f.name for f in result]
        # display < native < video (alphabetical on .value)
        assert names == [
            "Display A",
            "Display B",
            "Native A",
            "Native B",
            "Video C",
        ], f"Expected display, native, video ordering but got {names}"

    def test_sort_preserves_after_filtering(self):
        """Sort order is maintained even after filters reduce the set."""
        formats = [
            _make_format("v2", "Zebra Video", type=FormatCategory.video),
            _make_format("v1", "Alpha Video", type=FormatCategory.video),
            _make_format("d1", "Display Ad", type=FormatCategory.display),
        ]

        req = ListCreativeFormatsRequest(type="video")
        result = _call_impl(formats, req)

        names = [f.name for f in result]
        assert names == ["Alpha Video", "Zebra Video"], f"Filtered results should still be sorted: {names}"


# ---------------------------------------------------------------------------
# MEDIUM_RISK: Type filter empty result — T-UC-005-inv2-violated
# ---------------------------------------------------------------------------


class TestTypeFilterNoMatchReturnsEmpty:
    """T-UC-005-inv2-violated: Type filter excludes non-matching formats."""

    def test_type_filter_no_match_returns_empty(self):
        """When no formats match the type filter, returns empty list without error."""
        formats = [
            _make_format("d1", "Display Banner", type=FormatCategory.display),
            _make_format("d2", "Display Rectangle", type=FormatCategory.display),
        ]

        req = ListCreativeFormatsRequest(type="audio")
        result = _call_impl(formats, req)

        assert result == [], f"Expected empty list for non-matching type, got {result}"

    def test_type_filter_returns_empty_from_empty_catalog(self):
        """Type filter on empty catalog returns empty list."""
        result = _call_impl([], ListCreativeFormatsRequest(type="video"))
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
        group_asset = Assets5(
            item_type="repeatable_group",
            asset_group_id="product_group",
            required=True,
            min_count=1,
            max_count=5,
            assets=[
                Asset(
                    asset_id="product_image",
                    asset_type=AssetContentType.image,
                    required=True,
                ),
                Asset(
                    asset_id="product_title",
                    asset_type=AssetContentType.text,
                    required=True,
                ),
            ],
        )

        format_with_group = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="native_carousel"),
            name="Native Carousel",
            type=FormatCategory.native,
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
        group_asset = Assets5(
            item_type="repeatable_group",
            asset_group_id="text_group",
            required=True,
            min_count=1,
            max_count=3,
            assets=[
                Asset(
                    asset_id="headline",
                    asset_type=AssetContentType.text,
                    required=True,
                ),
            ],
        )

        format_with_text_group = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="text_only"),
            name="Text Only Native",
            type=FormatCategory.native,
            is_standard=True,
            assets=[group_asset],
        )

        # Filter for "video" — should NOT match since group only has text
        req = ListCreativeFormatsRequest(asset_types=["video"])
        result = _call_impl([format_with_text_group], req)

        assert result == [], "Group asset without video should not match video filter"

    def test_asset_types_filter_mixed_individual_and_group(self):
        """Format with both individual and group assets: filter checks both."""
        individual_asset = Assets(
            item_type="individual",
            asset_id="hero_video",
            asset_type=AssetContentType.video,
            required=True,
        )
        group_asset = Assets5(
            item_type="repeatable_group",
            asset_group_id="product_group",
            required=False,
            min_count=0,
            max_count=5,
            assets=[
                Asset(
                    asset_id="product_image",
                    asset_type=AssetContentType.image,
                    required=True,
                ),
            ],
        )

        mixed_format = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="mixed_format"),
            name="Mixed Format",
            type=FormatCategory.display,
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


class TestPartitionNativeTypeFilter:
    """T-UC-005-partition-type-filter: native type row."""

    def test_native_type_filter(self):
        """Filter type=native returns only native formats."""
        formats = [
            _make_format("d1", "Display Banner", type=FormatCategory.display),
            _make_format("n1", "Native Feed", type=FormatCategory.native),
            _make_format("v1", "Video Pre-roll", type=FormatCategory.video),
            _make_format("n2", "Native Recommendation", type=FormatCategory.native),
        ]

        req = ListCreativeFormatsRequest(type="native")
        result = _call_impl(formats, req)

        assert len(result) == 2
        names = [f.name for f in result]
        assert "Native Feed" in names
        assert "Native Recommendation" in names


class TestPartitionFormatIdsNoMatch:
    """T-UC-005-partition-format-ids: no match row."""

    def test_format_ids_no_match_returns_empty(self):
        """When format_ids contains only non-existent IDs, returns empty list."""
        formats = [
            _make_format("display_300x250", "Display 300x250"),
            _make_format("display_728x90", "Display 728x90"),
            _make_format("video_16x9", "Video 16:9", type=FormatCategory.video),
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
            _make_format("v1", "Beta Video", type=FormatCategory.video),
            _make_format("n1", "Gamma Native", type=FormatCategory.native),
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
        formats = [
            _make_format(
                "image_banner",
                "Image Banner",
                assets=[
                    Assets(
                        item_type="individual",
                        asset_id="main",
                        asset_type=AssetContentType.image,
                        required=True,
                    ),
                ],
            ),
            _make_format(
                "html_widget",
                "HTML Widget",
                assets=[
                    Assets(
                        item_type="individual",
                        asset_id="code",
                        asset_type=AssetContentType.html,
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
        formats = [
            _make_format(
                "image_only",
                "Image Only",
                assets=[
                    Assets(
                        item_type="individual",
                        asset_id="photo",
                        asset_type=AssetContentType.image,
                        required=True,
                    ),
                ],
            ),
            _make_format(
                "video_format",
                "Video Format",
                assets=[
                    Assets(
                        item_type="individual",
                        asset_id="clip",
                        asset_type=AssetContentType.video,
                        required=True,
                    ),
                ],
            ),
        ]

        req = ListCreativeFormatsRequest(asset_types=["video"])
        result = _call_impl(formats, req)

        assert len(result) == 1
        assert result[0].name == "Video Format"
