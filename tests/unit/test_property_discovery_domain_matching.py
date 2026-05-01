"""Tests for domain normalization in property filtering.

When publishers register with ``www.ladepeche.fr`` but the adagents.json
property identifier says ``ladepeche.fr`` (or vice-versa), the property
should still match.  These tests verify that ``_normalize_domain`` and
``_domains_match`` handle common subdomain variants correctly.
"""

from src.services.property_discovery_service import _domains_match, _normalize_domain


class TestNormalizeDomain:
    """Unit tests for _normalize_domain()."""

    def test_strips_www_prefix(self):
        assert _normalize_domain("www.ladepeche.fr") == "ladepeche.fr"

    def test_strips_m_prefix(self):
        assert _normalize_domain("m.ladepeche.fr") == "ladepeche.fr"

    def test_strips_mobile_prefix(self):
        assert _normalize_domain("mobile.ladepeche.fr") == "ladepeche.fr"

    def test_lowercases(self):
        assert _normalize_domain("WWW.LadePeche.FR") == "ladepeche.fr"

    def test_bare_domain_unchanged(self):
        assert _normalize_domain("ladepeche.fr") == "ladepeche.fr"

    def test_strips_only_one_prefix(self):
        # "www.m.example.com" should strip "www." only, leaving "m.example.com"
        assert _normalize_domain("www.m.example.com") == "m.example.com"

    def test_strips_surrounding_whitespace(self):
        assert _normalize_domain("  www.example.com  ") == "example.com"


class TestDomainsMatch:
    """Unit tests for _domains_match()."""

    def test_www_publisher_matches_bare_identifier(self):
        """www.ladepeche.fr should match identifier ladepeche.fr."""
        assert _domains_match("www.ladepeche.fr", ["ladepeche.fr"]) is True

    def test_bare_publisher_matches_www_identifier(self):
        """ladepeche.fr should match identifier www.ladepeche.fr."""
        assert _domains_match("ladepeche.fr", ["www.ladepeche.fr"]) is True

    def test_m_publisher_matches_bare_identifier(self):
        """m.ladepeche.fr should match identifier ladepeche.fr."""
        assert _domains_match("m.ladepeche.fr", ["ladepeche.fr"]) is True

    def test_exact_match_still_works(self):
        assert _domains_match("ladepeche.fr", ["ladepeche.fr"]) is True

    def test_non_matching_domain_filtered_out(self):
        assert _domains_match("other.com", ["ladepeche.fr"]) is False

    def test_multiple_identifiers_one_matches(self):
        assert _domains_match("www.example.com", ["foo.com", "example.com"]) is True

    def test_multiple_identifiers_none_match(self):
        assert _domains_match("www.example.com", ["foo.com", "bar.com"]) is False

    def test_empty_identifiers(self):
        assert _domains_match("example.com", []) is False
