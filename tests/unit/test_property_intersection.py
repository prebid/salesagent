"""Tests for PropertyIntersection — faithful filter across all selector variants.

Replaces the legacy per-product filter that silently dropped products whose
only selector was ``by_tag``. These tests pin the observable behavior per
selector variant, comparing in the *identifier-value* namespace (e.g.
``espn.com``) — not the AdCP ``PropertyId`` slug namespace:

- ``selection_type="all"``: unbounded product → always include.
- ``selection_type="by_id"``: ``property_ids`` are AuthorizedProperty IDs
  (slugs) resolved via ``list_by_ids`` to identifier values, then intersected.
- ``selection_type="by_tag"``: tags resolved via ``list_by_tags`` to rows, then
  to identifier values → no longer silently dropped.

Plus strict-mode preservation (``property_targeting_allowed=False`` requires the
buyer to accept every covered identifier value) and domain normalization
(``www.``/case folded so the covered and buyer values compare equal).
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


def _ap(property_id: str, identifier_values: list[str], *, ident_type: str = "domain") -> MagicMock:
    """Mock AuthorizedProperty row: ``property_id`` PK + ``identifiers`` (list of {type,value} dicts)."""
    ap = MagicMock()
    ap.property_id = property_id
    ap.identifiers = [{"type": ident_type, "value": value} for value in identifier_values]
    return ap


def _repo(
    by_id: dict[str, list[str]] | None = None,
    by_tag: dict[tuple[str, frozenset], list[tuple[str, list[str]]]] | None = None,
) -> MagicMock:
    """Build a mock AuthorizedPropertyRepository over identifier values.

    ``by_id``: maps an AuthorizedProperty ID (slug) → its identifier values.
    ``by_tag``: maps ``(publisher_domain, frozenset(tags))`` → list of
        ``(property_id, identifier_values)`` rows. Any unmapped call returns [].
    """
    repo = MagicMock()

    def list_by_ids(property_ids: list[str]) -> list[MagicMock]:
        if not by_id:
            return []
        return [_ap(pid, by_id[pid]) for pid in property_ids if pid in by_id]

    def list_by_tags(publisher_domain: str, tags: list[str]) -> list[MagicMock]:
        if not by_tag:
            return []
        rows = by_tag.get((publisher_domain, frozenset(tags)), [])
        return [_ap(pid, values) for pid, values in rows]

    repo.list_by_ids.side_effect = list_by_ids
    repo.list_by_tags.side_effect = list_by_tags
    return repo


class TestAllSelectorAlwaysIncluded:
    """``selection_type='all'`` makes the product unbounded — include regardless of buyer's list."""

    def test_keeps_product_with_all_selector(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_all()])

        result = intersection.filter_products([product], allowed_properties={"espn.com"})

        assert result.kept_products == [product]
        assert result.dropped_products == []

    def test_mixed_all_and_by_id_still_unbounded(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_all(), _sel_by_id(["prop_a"])])

        result = intersection.filter_products([product], allowed_properties={"espn.com"})

        assert result.kept_products == [product]


class TestByIdSelectorResolvesToIdentifierValues:
    """``by_id`` IDs are AuthorizedProperty slugs resolved to identifier values, then intersected."""

    def test_overlap_keeps_product(self):
        repo = _repo(by_id={"prop_a": ["espn.com"], "prop_b": ["cnn.com"]})
        intersection = PropertyIntersection(repo)
        # property_targeting_allowed=True: any intersection is enough.
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=True)

        result = intersection.filter_products([product], allowed_properties={"espn.com"})

        assert result.kept_products == [product]
        # IDs are resolved through the repo (not compared as raw slugs), sorted for determinism.
        repo.list_by_ids.assert_called_once_with(["prop_a", "prop_b"])

    def test_no_overlap_drops_with_reason(self):
        repo = _repo(by_id={"prop_a": ["espn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])])

        result = intersection.filter_products([product], allowed_properties={"nytimes.com"})

        assert result.kept_products == []
        assert len(result.dropped_products) == 1
        assert result.dropped_products[0].reason is DropReason.NO_PROPERTY_OVERLAP

    def test_unknown_id_drops_with_no_resolvable_properties(self):
        """by_id IDs that resolve to no AuthorizedProperty row → NO_RESOLVABLE_PROPERTIES.

        This is the namespace fix's teeth: the slug ``ghost_id`` is NOT treated
        as a comparable value — it must resolve to a row's identifier values.
        """
        repo = _repo()  # list_by_ids returns []
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["ghost_id"])])

        result = intersection.filter_products([product], allowed_properties={"espn.com"})

        assert result.kept_products == []
        assert result.dropped_products[0].reason is DropReason.NO_RESOLVABLE_PROPERTIES


class TestByTagSelectorFaithfulResolution:
    """``selection_type='by_tag'`` resolves tags via the repo — no longer silently dropped."""

    def test_tags_resolve_to_intersecting_property_keeps_product(self):
        repo = _repo(by_tag={("example.com", frozenset({"sports"})): [("prop_a", ["espn.com"])]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_tag(["sports"])])

        result = intersection.filter_products([product], allowed_properties={"espn.com"})

        assert result.kept_products == [product]
        repo.list_by_tags.assert_called_once_with("example.com", ["sports"])

    def test_tags_resolve_to_nothing_drops_with_no_resolvable_properties(self):
        repo = _repo()  # all lookups return []
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_tag(["unknown_tag"])])

        result = intersection.filter_products([product], allowed_properties={"espn.com"})

        assert result.kept_products == []
        assert result.dropped_products[0].reason is DropReason.NO_RESOLVABLE_PROPERTIES

    def test_tags_resolve_but_no_overlap_with_buyer_list(self):
        repo = _repo(by_tag={("example.com", frozenset({"news"})): [("prop_news", ["cnn.com"])]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_tag(["news"])])

        result = intersection.filter_products([product], allowed_properties={"espn.com"})

        assert result.kept_products == []
        assert result.dropped_products[0].reason is DropReason.NO_PROPERTY_OVERLAP


class TestMixedSelectors:
    """Products with both by_id and by_tag selectors aggregate the resolved identifier values."""

    def test_by_id_union_by_tag_resolved(self):
        repo = _repo(
            by_id={"prop_a": ["espn.com"]},
            by_tag={("example.com", frozenset({"sports"})): [("prop_b", ["cnn.com"])]},
        )
        intersection = PropertyIntersection(repo)
        # Permissive mode so any covered identifier in the allowed set is sufficient.
        product = _product("p1", [_sel_by_id(["prop_a"]), _sel_by_tag(["sports"])], property_targeting_allowed=True)

        # Buyer's list overlaps only the tag-resolved property's identifier.
        result = intersection.filter_products([product], allowed_properties={"cnn.com"})

        assert result.kept_products == [product]


class TestStrictModeSemantics:
    """``property_targeting_allowed=False`` requires the buyer to accept EVERY covered identifier value."""

    def test_strict_partial_subset_drops(self):
        repo = _repo(by_id={"prop_a": ["espn.com"], "prop_b": ["cnn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=False)

        # Buyer has only one of the two covered identifiers — strict mode rejects.
        result = intersection.filter_products([product], allowed_properties={"espn.com"})

        assert result.kept_products == []
        assert result.dropped_products[0].reason is DropReason.STRICT_MODE_VIOLATION

    def test_strict_full_subset_keeps(self):
        repo = _repo(by_id={"prop_a": ["espn.com"], "prop_b": ["cnn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=False)

        result = intersection.filter_products([product], allowed_properties={"espn.com", "cnn.com", "extra.com"})

        assert result.kept_products == [product]

    def test_permissive_partial_subset_keeps(self):
        repo = _repo(by_id={"prop_a": ["espn.com"], "prop_b": ["cnn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=True)

        # Permissive: any intersection is enough.
        result = intersection.filter_products([product], allowed_properties={"espn.com"})

        assert result.kept_products == [product]


class TestDomainNormalization:
    """Covered and buyer identifier values are normalized (www/m/mobile-stripped, lowercased)."""

    def test_www_prefix_matches_bare_domain(self):
        repo = _repo(by_id={"prop_a": ["www.espn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])], property_targeting_allowed=True)

        # Covered side carries www.; buyer lists the bare domain → normalization matches them.
        result = intersection.filter_products([product], allowed_properties={"espn.com"})

        assert result.kept_products == [product]

    def test_case_insensitive_match(self):
        repo = _repo(by_id={"prop_a": ["ESPN.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])], property_targeting_allowed=True)

        result = intersection.filter_products([product], allowed_properties={"espn.com"})

        assert result.kept_products == [product]


class TestZeroMatchAdvisory:
    """IntersectionResult.zero_match flag drives the SD2 advisory log path."""

    def test_zero_match_true_when_everything_dropped(self):
        repo = _repo(by_id={"prop_a": ["espn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])])

        result = intersection.filter_products([product], allowed_properties={"nytimes.com"})

        assert result.zero_match is True

    def test_zero_match_false_when_anything_kept(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_all()])

        result = intersection.filter_products([product], allowed_properties=set())

        assert result.zero_match is False

    def test_empty_input_is_zero_match(self):
        intersection = PropertyIntersection(_repo())
        result = intersection.filter_products([], allowed_properties={"espn.com"})

        assert isinstance(result, IntersectionResult)
        assert result.zero_match is True
        assert result.kept_products == []
        assert result.dropped_products == []
