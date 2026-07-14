"""Tests for TenantFactory subdomain derivation.

Regression coverage for the split-brain subdomain bug (#1418): the ORM
``TenantFactory.subdomain`` LazyAttribute and the ``make_tenant()`` identity
dict must derive the subdomain identically. When they diverge for a tenant_id
containing an underscore, the live e2e_rest dispatcher sends the identity-dict
subdomain as ``x-adcp-tenant`` while the DB row carries the LazyAttribute value;
the server's subdomain lookup misses and auth fails with 401.
"""

from __future__ import annotations


class TestTenantSubdomainDerivation:
    """ORM row, identity dict, and dispatcher header derive one subdomain."""

    def test_orm_row_matches_make_tenant_for_underscore_id(self):
        """ORM-row subdomain == make_tenant identity-dict subdomain.

        TenantFactory.build() evaluates the same subdomain LazyAttribute that is
        persisted to the DB row, so its value is the authoritative ORM-row value.
        """
        from tests.factories.core import TenantFactory

        tenant_id = "test_tenant"
        orm_subdomain = TenantFactory.build(tenant_id=tenant_id).subdomain
        identity_subdomain = TenantFactory.make_tenant(tenant_id)["subdomain"]

        assert orm_subdomain == identity_subdomain

    def test_dispatcher_header_matches_orm_row_for_underscore_id(self):
        """The value the RestE2EDispatcher sends as x-adcp-tenant == ORM-row subdomain.

        The dispatcher reads ``identity.tenant["subdomain"]`` (the make_tenant
        dict) and forwards it as the ``x-adcp-tenant`` header. That header must
        match the persisted DB row's subdomain or the server's subdomain lookup
        misses and falls back to treating the header as a tenant_id, failing auth.
        """
        from tests.factories.core import TenantFactory

        tenant_id = "test_tenant"
        orm_subdomain = TenantFactory.build(tenant_id=tenant_id).subdomain
        # Replicates dispatchers.py: header = identity.tenant["subdomain"].
        dispatcher_header = TenantFactory.make_tenant(tenant_id)["subdomain"]

        assert dispatcher_header == orm_subdomain

    def test_all_three_derivations_identical_for_underscore_id(self):
        """ORM row, identity dict, and dispatcher header are all identical."""
        from tests.factories.core import TenantFactory

        tenant_id = "test_tenant"
        orm_subdomain = TenantFactory.build(tenant_id=tenant_id).subdomain
        identity_subdomain = TenantFactory.make_tenant(tenant_id)["subdomain"]
        dispatcher_header = identity_subdomain

        assert orm_subdomain == identity_subdomain == dispatcher_header

    def test_subdomain_has_no_underscores_for_underscore_id(self):
        """Derived subdomain is hyphen-safe (no underscores).

        Subdomains feed publisher_domain (``f"{subdomain}.example.com"``), and the
        AdCP domain pattern rejects underscores, so an underscore tenant_id must
        produce a hyphen-only subdomain.
        """
        from tests.factories.core import TenantFactory

        orm_subdomain = TenantFactory.build(tenant_id="test_tenant").subdomain

        assert "_" not in orm_subdomain
