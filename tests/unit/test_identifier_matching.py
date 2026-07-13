"""Tests for the buyer-facing identifier matching seam.

The grammar itself (wildcards, bare-domain www/m selection) is the SDK's
(``adcp.adagents.domain_matches``) — these tests pin OUR orchestration: the
dict shaping, the pattern DIRECTION (buyer side is the pattern), type-aware
non-collision, and host extraction for adapter site indexes.
"""

from __future__ import annotations

import pytest

from src.services.identifier_matching import (
    buyer_identifier_matches_host,
    host_from_url_or_host,
    identifier_dicts,
    property_matches_buyer_list,
)
from tests.helpers.adcp_factories import create_test_identifier as _identifier

pytestmark = pytest.mark.unit


class TestIdentifierDicts:
    def test_shapes_typed_identifiers_for_sdk_matchers(self):
        idents = [_identifier("espn.com"), _identifier("com.foo.bar", type_="ios_bundle")]
        assert identifier_dicts(idents) == [
            {"type": "domain", "value": "espn.com"},
            {"type": "ios_bundle", "value": "com.foo.bar"},
        ]


class TestPropertyMatchesBuyerList:
    """Type-aware property↔buyer matching with the buyer side as the pattern."""

    def test_type_mismatch_never_matches_on_equal_values(self):
        """An ios_bundle value must not collide with a domain value.

        This is the flatten bug the typed round-trip exists to prevent: the
        same string ``com.foo.bar`` under different identifier types denotes
        different inventory.
        """
        property_identifiers = [{"type": "ios_bundle", "value": "com.foo.bar"}]
        buyer = identifier_dicts([_identifier("com.foo.bar", type_="domain")])
        assert property_matches_buyer_list(property_identifiers, buyer) is False

    def test_same_type_exact_value_matches_for_non_domain(self):
        property_identifiers = [{"type": "ios_bundle", "value": "com.foo.bar"}]
        buyer = identifier_dicts([_identifier("com.foo.bar", type_="ios_bundle")])
        assert property_matches_buyer_list(property_identifiers, buyer) is True

    def test_buyer_wildcard_selects_subdomain_property(self):
        property_identifiers = [{"type": "domain", "value": "sports.espn.com"}]
        buyer = identifier_dicts([_identifier("*.espn.com")])
        assert property_matches_buyer_list(property_identifiers, buyer) is True

    def test_buyer_bare_domain_selects_www_property(self):
        property_identifiers = [{"type": "domain", "value": "www.espn.com"}]
        buyer = identifier_dicts([_identifier("espn.com")])
        assert property_matches_buyer_list(property_identifiers, buyer) is True

    def test_buyer_bare_domain_does_not_select_mobile_property(self):
        """Spec grammar: bare domain selects www/m — ``mobile.`` is not special."""
        property_identifiers = [{"type": "domain", "value": "mobile.espn.com"}]
        buyer = identifier_dicts([_identifier("espn.com")])
        assert property_matches_buyer_list(property_identifiers, buyer) is False

    def test_direction_property_side_wildcard_is_literal(self):
        """The buyer side is the pattern; a property-side wildcard is a literal.

        Mirrors the SDK's verify-agent-authorization direction. A property
        declared ``*.cnn.com`` is only selected by a buyer pattern that
        matches that literal string (e.g. the identical wildcard).
        """
        property_identifiers = [{"type": "domain", "value": "*.cnn.com"}]
        assert (
            property_matches_buyer_list(property_identifiers, identifier_dicts([_identifier("sports.cnn.com")]))
            is False
        )
        assert property_matches_buyer_list(property_identifiers, identifier_dicts([_identifier("*.cnn.com")])) is True

    def test_empty_property_identifiers_never_match(self):
        assert property_matches_buyer_list(None, identifier_dicts([_identifier("espn.com")])) is False
        assert property_matches_buyer_list([], identifier_dicts([_identifier("espn.com")])) is False


class TestBuyerIdentifierMatchesHost:
    """The adapter-side host match (Kevel site index)."""

    def test_domain_identifier_uses_grammar(self):
        assert buyer_identifier_matches_host(_identifier("espn.com"), "www.espn.com") is True
        assert buyer_identifier_matches_host(_identifier("*.espn.com"), "sports.espn.com") is True
        assert buyer_identifier_matches_host(_identifier("*.espn.com"), "espn.com") is False

    def test_subdomain_identifier_requires_exact_host(self):
        assert (
            buyer_identifier_matches_host(_identifier("edition.cnn.com", type_="subdomain"), "edition.cnn.com") is True
        )
        assert buyer_identifier_matches_host(_identifier("edition.cnn.com", type_="subdomain"), "cnn.com") is False


class TestHostFromUrlOrHost:
    """Host extraction keeps the TRUE host — no prefix stripping."""

    def test_url_with_path(self):
        assert host_from_url_or_host("https://www.espn.com/sports/nba") == "www.espn.com"

    def test_bare_host_lowercased(self):
        assert host_from_url_or_host("ESPN.COM") == "espn.com"

    def test_host_with_path_no_scheme(self):
        assert host_from_url_or_host("edition.cnn.com/world") == "edition.cnn.com"

    def test_empty_input_returns_empty(self):
        assert host_from_url_or_host("") == ""

    def test_port_stripped_in_url_branch(self):
        assert host_from_url_or_host("https://www.espn.com:8443/x") == "www.espn.com"

    def test_port_stripped_in_bare_host_branch(self):
        # Both branches must normalize identically: a subdomain identifier value
        # carrying a port must still match the same host.
        assert host_from_url_or_host("www.espn.com:8443") == "www.espn.com"

    def test_bare_host_with_port_and_path(self):
        assert host_from_url_or_host("edition.cnn.com:80/world") == "edition.cnn.com"
