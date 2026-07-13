"""Tests for PropertyIntersection — faithful filter across all selector variants.

Replaces the legacy per-product filter that silently dropped products whose
only selector was ``by_tag``. These tests pin the observable behavior per
selector variant. Matching is per covered PROPERTY and fully typed: the
buyer's resolved property_list identifiers keep their ``.type`` (an
``ios_bundle`` value never collides with a ``domain`` value) and
``domain``-type values keep the spec grammar (bare domain selects www/m,
``*.`` wildcards select subdomains) via the SDK matchers.

- ``selection_type="all"``: unbounded product → always include.
- ``selection_type="by_id"``: ``property_ids`` are AuthorizedProperty IDs
  (slugs) scoped to the selector's ``publisher_domain`` and resolved via
  ``list_by_ids(publisher_domain, ids)``.
- ``selection_type="by_tag"``: tags resolved via ``list_by_tags`` to rows →
  no longer silently dropped.

Plus strict-mode preservation: ``property_targeting_allowed=False`` requires
EVERY covered property to match the buyer's list.
"""

from __future__ import annotations

import logging
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
from tests.helpers.adcp_factories import create_test_identifiers as _buyers

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
    by_id: dict[tuple[str, str], list[str]] | None = None,
    by_tag: dict[tuple[str, frozenset], list[tuple[str, list[str]]]] | None = None,
) -> MagicMock:
    """Build a mock AuthorizedPropertyRepository over identifier values.

    ``by_id``: maps ``(publisher_domain, property_id slug)`` → identifier
        values — publisher-scoped, mirroring the production query.
    ``by_tag``: maps ``(publisher_domain, frozenset(tags))`` → list of
        ``(property_id, identifier_values)`` rows. Any unmapped call returns [].
    """
    repo = MagicMock()

    def list_by_ids(publisher_domain: str, property_ids: list[str]) -> list[MagicMock]:
        if not by_id:
            return []
        return [_ap(pid, by_id[(publisher_domain, pid)]) for pid in property_ids if (publisher_domain, pid) in by_id]

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

        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == (product,)
        assert result.dropped_products == ()

    def test_mixed_all_and_by_id_still_unbounded(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_all(), _sel_by_id(["prop_a"])])

        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == (product,)


class TestByIdSelectorResolvesToRows:
    """``by_id`` IDs are AuthorizedProperty slugs resolved publisher-scoped, then matched."""

    def test_overlap_keeps_product(self):
        repo = _repo(by_id={("example.com", "prop_a"): ["espn.com"], ("example.com", "prop_b"): ["cnn.com"]})
        intersection = PropertyIntersection(repo)
        # property_targeting_allowed=True: any covered property matching is enough.
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=True)

        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == (product,)
        # IDs are resolved through the repo (not compared as raw slugs),
        # scoped to the selector's publisher_domain, sorted for determinism.
        repo.list_by_ids.assert_called_once_with("example.com", ["prop_a", "prop_b"])

    def test_by_id_lookup_is_publisher_scoped(self):
        """A slug authored for pub-a must NOT resolve against pub-b's row.

        ``PropertyId`` slugs (e.g. ``homepage``) are only unique per publisher
        — the spec makes ``publisher_domain`` required on the by_id selector.
        Here the only ``homepage`` row belongs to pub-b; a selector scoped to
        pub-a must not match it (and the product drops as unresolvable).
        """
        repo = _repo(by_id={("pub-b.com", "homepage"): ["espn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["homepage"], domain="pub-a.com")], property_targeting_allowed=True)

        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == ()
        assert result.dropped_products[0].reason is DropReason.NO_RESOLVABLE_PROPERTIES
        repo.list_by_ids.assert_called_once_with("pub-a.com", ["homepage"])

    def test_by_id_selectors_grouped_per_publisher(self):
        """Two by_id selectors for different publishers each query their own scope."""
        repo = _repo(
            by_id={
                ("pub-a.com", "homepage"): ["espn.com"],
                ("pub-b.com", "homepage"): ["cnn.com"],
            }
        )
        intersection = PropertyIntersection(repo)
        product = _product(
            "p1",
            [_sel_by_id(["homepage"], domain="pub-a.com"), _sel_by_id(["homepage"], domain="pub-b.com")],
            property_targeting_allowed=True,
        )

        result = intersection.filter_products([product], _buyers("cnn.com"))

        assert result.kept_products == (product,)
        assert repo.list_by_ids.call_count == 2

    def test_no_overlap_drops_with_reason(self):
        repo = _repo(by_id={("example.com", "prop_a"): ["espn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])])

        result = intersection.filter_products([product], _buyers("nytimes.com"))

        assert result.kept_products == ()
        assert len(result.dropped_products) == 1
        assert result.dropped_products[0].reason is DropReason.NO_PROPERTY_OVERLAP

    def test_drop_logs_intersection_advisory_marker(self, caplog):
        # The [INTERSECTION-ADVISORY] operator marker is emitted from INSIDE the
        # intersection, so get_products and the create-side advisory builder both
        # inherit consistent observability for a buyer property_list drop.
        repo = _repo(by_id={("example.com", "prop_a"): ["espn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])])

        with caplog.at_level(logging.WARNING, logger="src.services.property_intersection"):
            result = intersection.filter_products([product], _buyers("nytimes.com"))

        assert len(result.dropped_products) == 1
        messages = [r.getMessage() for r in caplog.records]
        assert any("INTERSECTION-ADVISORY" in m and "p1" in m and "no_property_overlap" in m for m in messages), (
            f"expected the intersection drop marker; got {messages}"
        )

    def test_unknown_id_drops_with_no_resolvable_properties(self):
        """by_id IDs that resolve to no AuthorizedProperty row → NO_RESOLVABLE_PROPERTIES.

        This is the namespace fix's teeth: the slug ``ghost_id`` is NOT treated
        as a comparable value — it must resolve to a row's identifiers.
        """
        repo = _repo()  # list_by_ids returns []
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["ghost_id"])])

        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == ()
        assert result.dropped_products[0].reason is DropReason.NO_RESOLVABLE_PROPERTIES


class TestByTagSelectorFaithfulResolution:
    """``selection_type='by_tag'`` resolves tags via the repo — no longer silently dropped."""

    def test_tags_resolve_to_intersecting_property_keeps_product(self):
        repo = _repo(by_tag={("example.com", frozenset({"sports"})): [("prop_a", ["espn.com"])]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_tag(["sports"])])

        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == (product,)
        repo.list_by_tags.assert_called_once_with("example.com", ["sports"])

    def test_tags_resolve_to_nothing_drops_with_no_resolvable_properties(self):
        repo = _repo()  # all lookups return []
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_tag(["unknown_tag"])])

        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == ()
        assert result.dropped_products[0].reason is DropReason.NO_RESOLVABLE_PROPERTIES

    def test_tags_resolve_but_no_overlap_with_buyer_list(self):
        repo = _repo(by_tag={("example.com", frozenset({"news"})): [("prop_news", ["cnn.com"])]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_tag(["news"])])

        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == ()
        assert result.dropped_products[0].reason is DropReason.NO_PROPERTY_OVERLAP


class TestMixedSelectors:
    """Products with both by_id and by_tag selectors aggregate the covered rows."""

    def test_by_id_union_by_tag_resolved(self):
        repo = _repo(
            by_id={("example.com", "prop_a"): ["espn.com"]},
            by_tag={("example.com", frozenset({"sports"})): [("prop_b", ["cnn.com"])]},
        )
        intersection = PropertyIntersection(repo)
        # Permissive mode so any covered property matching is sufficient.
        product = _product("p1", [_sel_by_id(["prop_a"]), _sel_by_tag(["sports"])], property_targeting_allowed=True)

        # Buyer's list overlaps only the tag-resolved property's identifier.
        result = intersection.filter_products([product], _buyers("cnn.com"))

        assert result.kept_products == (product,)


class TestStrictModeSemantics:
    """``property_targeting_allowed=False`` requires EVERY covered property to match."""

    def test_strict_partial_coverage_drops(self):
        repo = _repo(by_id={("example.com", "prop_a"): ["espn.com"], ("example.com", "prop_b"): ["cnn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=False)

        # Buyer's list selects only one of the two covered properties — strict mode rejects.
        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == ()
        assert result.dropped_products[0].reason is DropReason.STRICT_MODE_VIOLATION

    def test_strict_full_coverage_keeps(self):
        repo = _repo(by_id={("example.com", "prop_a"): ["espn.com"], ("example.com", "prop_b"): ["cnn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=False)

        result = intersection.filter_products([product], _buyers("espn.com", "cnn.com", "extra.com"))

        assert result.kept_products == (product,)

    def test_permissive_partial_coverage_keeps(self):
        repo = _repo(by_id={("example.com", "prop_a"): ["espn.com"], ("example.com", "prop_b"): ["cnn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a", "prop_b"])], property_targeting_allowed=True)

        # Permissive: any covered property matching is enough.
        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == (product,)


class TestSpecValueGrammar:
    """Matching honors the spec Identifier.value grammar and identifier types."""

    def test_www_property_selected_by_bare_buyer_domain(self):
        repo = _repo(by_id={("example.com", "prop_a"): ["www.espn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])], property_targeting_allowed=True)

        # Covered side carries www.; a bare buyer domain selects www/m per the grammar.
        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == (product,)

    def test_mobile_property_not_selected_by_bare_buyer_domain(self):
        """Spec grammar: bare domain selects www/m only — ``mobile.`` is not special."""
        repo = _repo(by_id={("example.com", "prop_a"): ["mobile.espn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])], property_targeting_allowed=True)

        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == ()
        assert result.dropped_products[0].reason is DropReason.NO_PROPERTY_OVERLAP

    def test_buyer_wildcard_selects_subdomain_property(self):
        repo = _repo(by_id={("example.com", "prop_a"): ["sports.espn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])], property_targeting_allowed=True)

        result = intersection.filter_products([product], _buyers("*.espn.com"))

        assert result.kept_products == (product,)

    def test_case_insensitive_match(self):
        repo = _repo(by_id={("example.com", "prop_a"): ["ESPN.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])], property_targeting_allowed=True)

        result = intersection.filter_products([product], _buyers("espn.com"))

        assert result.kept_products == (product,)

    def test_identifier_type_participates_in_matching(self):
        """An ios_bundle identifier never collides with a domain identifier of equal value."""
        repo = _repo(by_id={("example.com", "prop_a"): ["com.foo.bar"]})  # rows are domain-typed
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])], property_targeting_allowed=True)

        result = intersection.filter_products([product], _buyers("com.foo.bar", type_="ios_bundle"))

        assert result.kept_products == ()
        assert result.dropped_products[0].reason is DropReason.NO_PROPERTY_OVERLAP


class TestEmptyBuyerList:
    """A property_list resolving to ZERO identifiers keeps unbounded products only.

    Parity with the pre-typed behavior: an empty buyer list selects nothing, so
    every bounded product drops as NO_PROPERTY_OVERLAP (surfaced as advisories),
    while 'all'-selector products stay — the buyer's empty list says nothing
    about unbounded coverage.
    """

    def test_bounded_product_drops_with_no_overlap(self):
        repo = _repo(by_id={("example.com", "prop_a"): ["espn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])], property_targeting_allowed=True)

        result = intersection.filter_products([product], _buyers())

        assert result.kept_products == ()
        assert result.dropped_products[0].reason is DropReason.NO_PROPERTY_OVERLAP

    def test_all_selector_product_kept(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_all()])

        result = intersection.filter_products([product], _buyers())

        assert result.kept_products == (product,)


class TestZeroMatchAdvisory:
    """IntersectionResult.zero_match flag drives the zero-overlap advisory path."""

    def test_zero_match_true_when_everything_dropped(self):
        repo = _repo(by_id={("example.com", "prop_a"): ["espn.com"]})
        intersection = PropertyIntersection(repo)
        product = _product("p1", [_sel_by_id(["prop_a"])])

        result = intersection.filter_products([product], _buyers("nytimes.com"))

        assert result.zero_match is True

    def test_zero_match_false_when_anything_kept(self):
        intersection = PropertyIntersection(_repo())
        product = _product("p1", [_sel_all()])

        result = intersection.filter_products([product], _buyers())

        assert result.zero_match is False

    def test_empty_input_is_zero_match(self):
        intersection = PropertyIntersection(_repo())
        result = intersection.filter_products([], _buyers("espn.com"))

        assert isinstance(result, IntersectionResult)
        assert result.zero_match is True
        assert result.kept_products == ()
