"""Tests for Kevel adapter's property_list compilation (B3).

Backs the contract item from #1302 that "an enabled adapter MUST translate
property_list" — Kevel is the first salesagent adapter to satisfy that side
of the contract (vs B4's UNSUPPORTED_FEATURE raises for adapters that can't).

These tests pin the three observable behaviors:

1. Kevel advertises ``supports_property_list_filtering=True`` so
   ``get_adcp_capabilities`` flips ``property_list_filtering`` to True for
   Kevel-configured tenants.
2. Identifier-type validation at ``_check_property_list_supported`` rejects
   property lists whose contents Kevel cannot compile.
3. ``_build_targeting`` writes the resolved Kevel ``siteIds`` into the
   native targeting payload.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from adcp.types import PropertyListReference

from src.adapters.kevel import Kevel
from src.core.schemas import CreateMediaBuyError, MediaPackage, Principal
from src.services.kevel_site_resolver import ResolvedSiteIds

pytestmark = pytest.mark.unit


def _principal() -> Principal:
    return Principal(
        principal_id="p_test",
        name="Test Principal",
        platform_mappings={"kevel": {"advertiser_id": "kevel_adv_1"}},
    )


def _kevel(*, dry_run: bool = False) -> Kevel:
    return Kevel(
        config={"network_id": "100", "api_key": "test-key"},
        principal=_principal(),
        dry_run=dry_run,
        tenant_id="t_test",
    )


def _package_with_ref(ref: PropertyListReference) -> MediaPackage:
    pkg = MagicMock(spec=MediaPackage)
    pkg.package_id = "pkg_pl"
    pkg.targeting_overlay = MagicMock()
    pkg.targeting_overlay.property_list = ref
    return pkg


def _ref() -> PropertyListReference:
    return PropertyListReference(agent_url="https://gov.example/lists", list_id="cb_news_v1")


class TestSupportsPropertyListFilteringFlag:
    """Class attribute drives the capability declaration."""

    def test_kevel_class_advertises_native_support(self):
        assert Kevel.supports_property_list_filtering is True


class TestCheckPropertyListSupportedAcceptsCompilable:
    """All-domain/subdomain lists pass pre-flight."""

    def test_returns_none_when_all_identifiers_are_domain(self):
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42, 99}, unsupported_types=set(), unresolvable_values=[]
            )
            result = adapter._check_property_list_supported([_package_with_ref(_ref())])

        assert result is None

    def test_returns_none_when_zero_sites_match(self):
        """Zero-match must be accept-with-context (SD2 in the inventory-targeting plan)."""
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids=set(), unsupported_types=set(), unresolvable_values=["never-onboarded.example"]
            )
            result = adapter._check_property_list_supported([_package_with_ref(_ref())])

        assert result is None, "Zero-match must not reject; downstream handles inventory_list_no_match"

    def test_no_property_list_skipped(self):
        adapter = _kevel()
        pkg = MagicMock(spec=MediaPackage)
        pkg.targeting_overlay = MagicMock()
        pkg.targeting_overlay.property_list = None
        assert adapter._check_property_list_supported([pkg]) is None


class TestCheckPropertyListSupportedRejectsIncompatible:
    """Lists containing identifier types Kevel can't compile must surface UNSUPPORTED_FEATURE."""

    def test_returns_error_when_list_contains_unsupported_types(self):
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids=set(), unsupported_types={"ios_bundle", "podcast_guid"}, unresolvable_values=[]
            )
            result = adapter._check_property_list_supported([_package_with_ref(_ref())])

        assert isinstance(result, CreateMediaBuyError)
        assert result.errors[0].code == "UNSUPPORTED_FEATURE"
        assert "ios_bundle" in result.errors[0].message
        assert "podcast_guid" in result.errors[0].message

    def test_dry_run_falls_back_to_base_helper(self):
        """In dry-run mode the resolver isn't constructed; defer to the base reject-all helper."""
        adapter = _kevel(dry_run=True)
        result = adapter._check_property_list_supported([_package_with_ref(_ref())])

        # Dry-run bypasses Kevel's overridden check; the base helper's behavior
        # depends on supports_property_list_filtering. Kevel's class attribute
        # is True, so base helper returns None — pre-flight passes, but
        # compilation in _build_targeting will also short-circuit when
        # _site_resolver is None.
        assert result is None


class TestBuildTargetingCompilesPropertyList:
    """_build_targeting writes resolved siteIds into the Kevel native payload."""

    def _make_overlay(self, ref: PropertyListReference | None):
        overlay = MagicMock()
        overlay.geo_countries = None
        overlay.geo_regions = None
        overlay.geo_metros = None
        overlay.keywords_any_of = None
        overlay.device_type_any_of = None
        overlay.audiences_any_of = None
        overlay.frequency_cap = None
        overlay.custom = None
        overlay.key_value_pairs = None
        overlay.property_list = ref
        return overlay

    def test_resolved_site_ids_land_in_kevel_targeting(self):
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42, 99, 7}, unsupported_types=set(), unresolvable_values=[]
            )
            result = adapter._build_targeting(self._make_overlay(_ref()))

        assert result.get("siteIds") == [7, 42, 99], "siteIds must be sorted for deterministic output"

    def test_property_list_site_ids_union_with_custom_site_ids(self):
        """When buyer specifies both targeting_overlay.property_list and custom.kevel.site_ids, both contribute."""
        adapter = _kevel()
        overlay = self._make_overlay(_ref())
        overlay.custom = {"kevel": {"site_ids": [1, 2, 42]}}

        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42, 99}, unsupported_types=set(), unresolvable_values=[]
            )
            result = adapter._build_targeting(overlay)

        assert result["siteIds"] == [1, 2, 42, 99], "custom site_ids and property_list-resolved siteIds must union"

    def test_no_property_list_leaves_targeting_unchanged(self):
        adapter = _kevel()
        result = adapter._build_targeting(self._make_overlay(None))
        assert "siteIds" not in result

    def test_dry_run_skips_property_list_compilation(self):
        """In dry-run mode the resolver isn't available; compilation must be a no-op."""
        adapter = _kevel(dry_run=True)
        # Even though property_list is set, dry-run mode has _site_resolver=None
        result = adapter._build_targeting(self._make_overlay(_ref()))
        assert "siteIds" not in result, "Dry-run must not attempt Kevel /v1/site fetches"
