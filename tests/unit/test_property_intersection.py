"""Tests for PropertyIntersection — faithful filter across all selector variants.

Replaces the legacy ``filter_products_by_property_list`` shortcut that
silently dropped products whose only selector was ``by_tag``. These tests
pin the three observable behaviors per selector variant:

- ``selection_type="all"``: unbounded product → always include.
- ``selection_type="by_id"``: explicit property IDs → direct intersection.
- ``selection_type="by_tag"``: tags resolved via AuthorizedPropertyRepository
  → no longer silently dropped.

Plus the strict-mode preservation: when ``property_targeting_allowed=False``
the buyer must accept every covered property, not just any intersection.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from adcp.types.generated_poc.core.property_id import PropertyId
from adcp.types.generated_poc.core.property_tag import PropertyTag
from adcp.types.generated_poc.core.publisher_property_selector import (
    PublisherPropertySelector,
    PublisherPropertySelector1,
    PublisherPropertySelector2,
    PublisherPropertySelector3,
)

from src.services.property_intersection import DropReason, IntersectionResult, PropertyIntersection

pytestmark = pytest.mark.unit


def _sel_all(domain: str = "example.com") -> PublisherPropertySelector:
    return PublisherPropertySelector(root=PublisherPropertySelector1(publisher_domain=domain, selection_type="all"))


def _sel_by_id(property_ids: list[str], domain: str = "example.com") -> PublisherPropertySelector:
    return PublisherPropertySelector(
        root=PublisherPropertySelector2(
            publisher_domain=domain,
            selection_type="by_id",
            property_ids=[PropertyId(root=pid) for pid in property_ids],
        )
    )


def _sel_by_tag(tags: list[str], domain: str = "example.com") -> PublisherPropertySelector:
    return PublisherPropertySelector(
        root=PublisherPropertySelector3(
            publisher_domain=domain,
            selection_type="by_tag",
            property_tags=[PropertyTag(root=tag) for tag in tags],
        )
    )


def _product(
    product_id: str,
    selectors: list[PublisherPropertySelector],
    *,
    property_targeting_allowed: bool = False,
) -> MagicMock:
    p = MagicMock()
    p.product_id = product_id
    p.publisher_properties = selectors
    p.property_targeting_allowed = property_targeting_allowed
    return p


def _authorized_property(property_id: str) -> MagicMock:
    p = MagicMock()
    p.property_id = property_id
    return p


def _repo(tag_lookup: dict[tuple[str, frozenset], list[str]] | None = None) -> MagicMock:
    """Build a mock AuthorizedPropertyRepository.

    ``tag_lookup`` maps ``(publisher_domain, frozenset(tags))`` to the list of
    property IDs that should be returned. Any unmapped call returns [].
    """
    repo = MagicMock()

    def list_by_tags(publisher_domain: str, tags: list[str]) -> list[MagicMock]:
        if tag_lookup is None:
            return []
        key = (publisher_domain, frozenset(tags))
        property_ids = tag_lookup.get(key, [])
        return [_authorized_property(pid) for pid in property_ids]

    repo.list_by_tags.side_effect = list_by_tags
    return repo


class TestAllSelectorAlwaysIncluded:
    """``selection_type='all'`` makes the product unbounded — include regardless of buyer's list."""

    def test_keeps_product_with_all_selector(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_all()])

        result = intersection.filter_products([product], allowed_properties={"prop_x"})

        assert result.kept_products == [product]
        assert result.dropped_products == []

    def test_mixed_all_and_by_id_still_unbounded(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_all(), _sel_by_id(["prop_a"])])

        result = intersection.filter_products([product], allowed_properties={"prop_x"})

        assert result.kept_products == [product]


class TestByIdSelectorDirectIntersection:
    """``selection_type='by_id'`` intersects the product's IDs with the buyer's allowed set."""

    def test_overlap_keeps_product(self):
        intersection = PropertyIntersection(_repo())
        # property_targeting_allowed=True: any intersection is enough
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=True)

        result = intersection.filter_products([product], allowed_properties={"prop_a"})

        assert result.kept_products == [product]

    def test_no_overlap_drops_with_reason(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_by_id(["prop_a"])])

        result = intersection.filter_products([product], allowed_properties={"prop_z"})

        assert result.kept_products == []
        assert len(result.dropped_products) == 1
        assert result.dropped_products[0].reason is DropReason.NO_PROPERTY_OVERLAP


class TestByTagSelectorFaithfulResolution:
    """``selection_type='by_tag'`` resolves tags via the repo — no longer silently dropped."""

    def test_tags_resolve_to_intersecting_property_keeps_product(self):
        repo = _repo({("example.com", frozenset({"sports"})): ["prop_a"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_tag(["sports"])])

        result = intersection.filter_products([product], allowed_properties={"prop_a"})

        assert result.kept_products == [product]
        repo.list_by_tags.assert_called_once_with("example.com", ["sports"])

    def test_tags_resolve_to_nothing_drops_with_no_resolvable_properties(self):
        repo = _repo()  # all lookups return []
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_tag(["unknown_tag"])])

        result = intersection.filter_products([product], allowed_properties={"prop_a"})

        assert result.kept_products == []
        assert result.dropped_products[0].reason is DropReason.NO_RESOLVABLE_PROPERTIES

    def test_tags_resolve_but_no_overlap_with_buyer_list(self):
        repo = _repo({("example.com", frozenset({"news"})): ["prop_news_1"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_tag(["news"])])

        result = intersection.filter_products([product], allowed_properties={"prop_sports_1"})

        assert result.kept_products == []
        assert result.dropped_products[0].reason is DropReason.NO_PROPERTY_OVERLAP


class TestMixedSelectors:
    """Products with both by_id and by_tag selectors aggregate the resolved property IDs."""

    def test_by_id_union_by_tag_resolved(self):
        repo = _repo({("example.com", frozenset({"sports"})): ["prop_b"]})
        intersection = PropertyIntersection(repo)
        # Permissive mode so any covered property in the allowed set is sufficient.
        product = _product("p1", [_sel_by_id(["prop_a"]), _sel_by_tag(["sports"])], property_targeting_allowed=True)

        # Buyer's list overlaps only the tag-resolved property
        result = intersection.filter_products([product], allowed_properties={"prop_b"})

        assert result.kept_products == [product]


class TestStrictModeSemantics:
    """``property_targeting_allowed=False`` requires the buyer to accept EVERY covered property."""

    def test_strict_partial_subset_drops(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=False)

        # Buyer has only one of the two — strict mode rejects
        result = intersection.filter_products([product], allowed_properties={"prop_a"})

        assert result.kept_products == []
        assert result.dropped_products[0].reason is DropReason.STRICT_MODE_VIOLATION

    def test_strict_full_subset_keeps(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=False)

        result = intersection.filter_products([product], allowed_properties={"prop_a", "prop_b", "prop_c"})

        assert result.kept_products == [product]

    def test_permissive_partial_subset_keeps(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=True)

        # Permissive: any intersection is enough
        result = intersection.filter_products([product], allowed_properties={"prop_a"})

        assert result.kept_products == [product]


class TestZeroMatchAdvisory:
    """IntersectionResult.zero_match flag drives the SD2 advisory log path."""

    def test_zero_match_true_when_everything_dropped(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_by_id(["prop_a"])])

        result = intersection.filter_products([product], allowed_properties={"prop_z"})

        assert result.zero_match is True

    def test_zero_match_false_when_anything_kept(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_all()])

        result = intersection.filter_products([product], allowed_properties=set())

        assert result.zero_match is False

    def test_empty_input_is_zero_match(self):
        intersection = PropertyIntersection(_repo())
        result = intersection.filter_products([], allowed_properties={"prop_a"})

        assert isinstance(result, IntersectionResult)
        assert result.zero_match is True
        assert result.kept_products == []
        assert result.dropped_products == []
