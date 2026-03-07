"""Integration tests for list_authorized_properties with PublisherPartner architecture.

Tests that list_authorized_properties correctly reads from PublisherPartner table
(single source of truth) and returns AdCP-compliant responses.
"""

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import PublisherPartner, Tenant
from src.core.resolved_identity import ResolvedIdentity
from src.core.tools.properties import _list_authorized_properties_impl


@pytest.mark.requires_db
def test_list_authorized_properties_reads_from_publisher_partner(integration_db):
    """Test that list_authorized_properties uses PublisherPartner table as source of truth."""
    with get_db_session() as session:
        # Create test tenant
        tenant = Tenant(
            tenant_id="test_tenant",
            name="Test Publisher",
            subdomain="testpub",
            ad_server="mock",
        )
        session.add(tenant)
        session.commit()

        # Create PublisherPartner records (mix of verified and unverified)
        partners = [
            PublisherPartner(
                tenant_id="test_tenant",
                publisher_domain="example.com",
                display_name="Example Publisher",
                is_verified=True,
                sync_status="success",
            ),
            PublisherPartner(
                tenant_id="test_tenant",
                publisher_domain="test.org",
                display_name="Test Publisher",
                is_verified=True,
                sync_status="success",
            ),
            # Unverified publisher (should still be included - buyers see full portfolio)
            PublisherPartner(
                tenant_id="test_tenant",
                publisher_domain="pending.com",
                display_name="Pending Publisher",
                is_verified=False,
                sync_status="pending",
            ),
        ]
        session.add_all(partners)
        session.commit()

        # Pass identity directly to the impl function
        identity = ResolvedIdentity(
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant"},
            protocol="mcp",
        )
        response = _list_authorized_properties_impl(req=None, identity=identity)

        # Verify all registered publishers are returned (regardless of verification status)
        assert len(response.publisher_domains) == 3
        assert "example.com" in response.publisher_domains
        assert "test.org" in response.publisher_domains
        assert "pending.com" in response.publisher_domains


@pytest.mark.requires_db
def test_list_authorized_properties_returns_all_registered_publishers(integration_db):
    """Test that all registered publishers are returned regardless of verification status."""
    with get_db_session() as session:
        # Create test tenant
        tenant = Tenant(
            tenant_id="test_tenant2",
            name="Test Publisher 2",
            subdomain="testpub2",
            ad_server="mock",
        )
        session.add(tenant)
        session.commit()

        # Create mix of verified and unverified partners
        partners = [
            PublisherPartner(
                tenant_id="test_tenant2",
                publisher_domain="verified1.com",
                display_name="Verified 1",
                is_verified=True,
                sync_status="success",
            ),
            PublisherPartner(
                tenant_id="test_tenant2",
                publisher_domain="unverified1.com",
                display_name="Unverified 1",
                is_verified=False,
                sync_status="pending",
            ),
            PublisherPartner(
                tenant_id="test_tenant2",
                publisher_domain="verified2.com",
                display_name="Verified 2",
                is_verified=True,
                sync_status="success",
            ),
            PublisherPartner(
                tenant_id="test_tenant2",
                publisher_domain="failed.com",
                display_name="Failed",
                is_verified=False,
                sync_status="error",
            ),
        ]
        session.add_all(partners)
        session.commit()

        # Pass identity directly to the impl function
        identity = ResolvedIdentity(
            tenant_id="test_tenant2",
            tenant={"tenant_id": "test_tenant2"},
            protocol="mcp",
        )
        response = _list_authorized_properties_impl(req=None, identity=identity)

        # Verify all registered publishers are returned (regardless of verification status)
        # This allows buyers to see the full portfolio during publisher setup
        assert len(response.publisher_domains) == 4
        assert "verified1.com" in response.publisher_domains
        assert "verified2.com" in response.publisher_domains
        assert "unverified1.com" in response.publisher_domains
        assert "failed.com" in response.publisher_domains


@pytest.mark.requires_db
def test_list_authorized_properties_returns_empty_when_no_publishers(integration_db):
    """Test that empty list is returned when no verified publishers exist."""
    with get_db_session() as session:
        # Create test tenant with no publishers
        tenant = Tenant(
            tenant_id="test_tenant3",
            name="Empty Publisher",
            subdomain="emptypub",
            ad_server="mock",
        )
        session.add(tenant)
        session.commit()

        # Pass identity directly - should return empty list, not error
        identity = ResolvedIdentity(
            tenant_id="test_tenant3",
            tenant={"tenant_id": "test_tenant3"},
            protocol="mcp",
        )
        response = _list_authorized_properties_impl(req=None, identity=identity)

        # Verify empty response with helpful description
        assert response.publisher_domains == []
        assert response.portfolio_description is not None
        assert "No publisher partnerships" in response.portfolio_description


@pytest.mark.requires_db
def test_list_authorized_properties_returns_sorted_domains(integration_db):
    """Test that publisher domains are returned in sorted order."""
    with get_db_session() as session:
        # Create test tenant
        tenant = Tenant(
            tenant_id="test_tenant4",
            name="Sorted Test",
            subdomain="sorted",
            ad_server="mock",
        )
        session.add(tenant)
        session.commit()

        # Create publishers in unsorted order
        partners = [
            PublisherPartner(
                tenant_id="test_tenant4",
                publisher_domain="zebra.com",
                display_name="Zebra",
                is_verified=True,
                sync_status="success",
            ),
            PublisherPartner(
                tenant_id="test_tenant4",
                publisher_domain="alpha.com",
                display_name="Alpha",
                is_verified=True,
                sync_status="success",
            ),
            PublisherPartner(
                tenant_id="test_tenant4",
                publisher_domain="beta.com",
                display_name="Beta",
                is_verified=True,
                sync_status="success",
            ),
        ]
        session.add_all(partners)
        session.commit()

        # Pass identity directly to the impl function
        identity = ResolvedIdentity(
            tenant_id="test_tenant4",
            tenant={"tenant_id": "test_tenant4"},
            protocol="mcp",
        )
        response = _list_authorized_properties_impl(req=None, identity=identity)

        # Verify domains are sorted
        assert response.publisher_domains == ["alpha.com", "beta.com", "zebra.com"]


@pytest.mark.requires_db
def test_list_authorized_properties_tenant_isolation(integration_db):
    """Test that publishers from other tenants are not included."""
    with get_db_session() as session:
        # Create two tenants
        tenant1 = Tenant(
            tenant_id="tenant_a",
            name="Tenant A",
            subdomain="tenanta",
            ad_server="mock",
        )
        tenant2 = Tenant(
            tenant_id="tenant_b",
            name="Tenant B",
            subdomain="tenantb",
            ad_server="mock",
        )
        session.add_all([tenant1, tenant2])
        session.commit()

        # Create publishers for each tenant
        partners = [
            # Tenant A publishers
            PublisherPartner(
                tenant_id="tenant_a",
                publisher_domain="tenanta-pub1.com",
                display_name="Tenant A Pub 1",
                is_verified=True,
                sync_status="success",
            ),
            PublisherPartner(
                tenant_id="tenant_a",
                publisher_domain="tenanta-pub2.com",
                display_name="Tenant A Pub 2",
                is_verified=True,
                sync_status="success",
            ),
            # Tenant B publishers
            PublisherPartner(
                tenant_id="tenant_b",
                publisher_domain="tenantb-pub1.com",
                display_name="Tenant B Pub 1",
                is_verified=True,
                sync_status="success",
            ),
        ]
        session.add_all(partners)
        session.commit()

        # Query tenant A
        identity_a = ResolvedIdentity(
            tenant_id="tenant_a",
            tenant={"tenant_id": "tenant_a"},
            protocol="mcp",
        )
        response_a = _list_authorized_properties_impl(req=None, identity=identity_a)

        # Verify only Tenant A publishers returned
        assert len(response_a.publisher_domains) == 2
        assert "tenanta-pub1.com" in response_a.publisher_domains
        assert "tenanta-pub2.com" in response_a.publisher_domains
        assert "tenantb-pub1.com" not in response_a.publisher_domains

        # Query tenant B
        identity_b = ResolvedIdentity(
            tenant_id="tenant_b",
            tenant={"tenant_id": "tenant_b"},
            protocol="mcp",
        )
        response_b = _list_authorized_properties_impl(req=None, identity=identity_b)

        # Verify only Tenant B publishers returned
        assert len(response_b.publisher_domains) == 1
        assert "tenantb-pub1.com" in response_b.publisher_domains
        assert "tenanta-pub1.com" not in response_b.publisher_domains
        assert "tenanta-pub2.com" not in response_b.publisher_domains
