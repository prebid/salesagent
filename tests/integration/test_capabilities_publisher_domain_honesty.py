"""salesagent-piyo — get_adcp_capabilities must not fabricate a publisher domain.

Core Invariant under test: ``portfolio.publisher_domains`` is a CLAIM the seller
makes about which publishers' inventory it represents. A buyer resolves that claim
against each publisher's ``/.well-known/adagents.json`` (dist/schemas/3.1.1/adagents.json
@ v3.1.1). Inventing a domain the tenant has no partnership with asserts an
authorization no publisher granted -- the same class of dishonesty the STRICT
declaration policy exists to prevent.

Production today (src/core/tools/capabilities.py:492-494) fabricates
``f"{subdomain}.example.com"`` whenever no ``PublisherPartner`` row exists.
``example.com`` is RFC 2606 reserved, so the fabricated value is guaranteed
unreachable -- it can never host a real ``adagents.json``.

Per the pinned v3.1.1 schema (bundled ``protocol/get-adcp-capabilities-response.json``):
``media_buy.portfolio.publisher_domains`` is REQUIRED with ``minItems: 1`` when
``portfolio`` is present, but ``media_buy.portfolio`` itself is OPTIONAL (media_buy
has no ``required`` list). A portfolio with zero real domains is therefore
schema-INVALID to emit; the only spec-legal response when a tenant has no publisher
partnership is to OMIT ``portfolio`` entirely, not to fabricate a domain and not to
emit an empty ``publisher_domains`` array.

TDD RED: this test fails against production today because production always
constructs a ``Portfolio`` (with a fabricated domain when none are real) instead of
omitting it.

Covers: salesagent-piyo.
"""

from __future__ import annotations

import pytest

from tests.harness.capabilities import CapabilitiesEnv


@pytest.mark.requires_db
class TestNoPublisherDomainOmitsPortfolio:
    """A tenant with zero PublisherPartner rows must get no fabricated domain."""

    def test_no_publisher_partners_omits_portfolio_entirely(self, integration_db):
        """No PublisherPartner rows -> portfolio is omitted (None), never a
        fabricated <subdomain>.example.com domain and never an empty
        publisher_domains array (both are schema-illegal or dishonest).
        """
        with CapabilitiesEnv(tenant_id="t_no_pub_partner", principal_id="p_no_pub_partner") as env:
            tenant, _principal = env.setup_default_data()
            assert tenant.subdomain, "fixture must set a subdomain so a fabrication would be observable"

            response = env.call_impl()

        assert response.media_buy is not None
        portfolio = response.media_buy.portfolio
        assert portfolio is None, (
            "expected media_buy.portfolio to be omitted entirely when the tenant has no "
            f"publisher partnership (publisher_domains is REQUIRED+minItems:1 when portfolio "
            f"is present per the pinned v3.1.1 schema, so no legal non-fabricated portfolio "
            f"exists) -- got portfolio={portfolio!r}"
        )

    def test_no_publisher_partners_never_fabricates_example_com(self, integration_db):
        """Even if a future change keeps emitting a (possibly empty) portfolio,
        no fabricated example.com domain may ever appear on the wire -- this is
        the deletion-test-proof assertion: it survives independent of the
        portfolio=None design choice above.
        """
        with CapabilitiesEnv(tenant_id="t_no_pub_partner2", principal_id="p_no_pub_partner2") as env:
            tenant, _principal = env.setup_default_data()

            response = env.call_impl()

        assert response.media_buy is not None
        portfolio = response.media_buy.portfolio
        domains = [] if portfolio is None else [d.root for d in portfolio.publisher_domains]
        fabricated = [d for d in domains if d.endswith(".example.com") or d == "example.com"]
        assert not fabricated, (
            f"portfolio.publisher_domains must never contain a fabricated example.com-style "
            f"domain (RFC 2606 reserved, guaranteed unreachable) -- got {domains!r}"
        )
