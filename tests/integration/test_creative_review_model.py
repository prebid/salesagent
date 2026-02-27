"""Integration tests for CreativeReview helper functions.

These tests focus on testing OUR query helper functions (get_creative_reviews,
get_ai_review_stats, get_creative_with_latest_review), not the ORM/database itself.

Tests that were deleted (they only tested SQLAlchemy/PostgreSQL):
- test_creative_review_model_creation: Tested ORM insert/query
- test_creative_review_relationship: Tested SQLAlchemy relationships

Per CLAUDE.md: "Test YOUR code's logic and behavior, not Python/SQLAlchemy."
"""

import uuid
from datetime import UTC, datetime

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative, CreativeReview, Principal, Tenant
from src.core.database.queries import (
    get_ai_review_stats,
    get_creative_reviews,
    get_creative_with_latest_review,
)


def _create_test_tenant_with_creative(session, tenant_id: str, creative_id: str):
    """Helper to create test tenant, principal, and creative.

    This reduces test setup boilerplate. Uses tenant_id-derived unique values
    for principal_id and access_token to allow multiple tenants per test.
    """
    principal_id = f"principal_{tenant_id}"
    access_token = f"token_{tenant_id}"

    tenant = Tenant(
        tenant_id=tenant_id,
        name=f"Test Tenant {tenant_id}",
        subdomain=tenant_id,
        is_active=True,
    )
    session.add(tenant)
    session.commit()

    principal = Principal(
        tenant_id=tenant_id,
        principal_id=principal_id,
        name="Test Principal",
        access_token=access_token,
        platform_mappings={"mock": {"id": "test_advertiser"}},
    )
    session.add(principal)
    session.commit()

    creative = Creative(
        creative_id=creative_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        name="Test Creative",
        format="display_300x250",
        status="pending",
        agent_url="https://test-agent.example.com",
        data={},
    )
    session.add(creative)
    session.commit()


@pytest.mark.requires_db
def test_get_creative_reviews_query(integration_db):
    """Test get_creative_reviews helper function filters by creative_id correctly."""
    with get_db_session() as session:
        creative_id = f"creative_{uuid.uuid4().hex[:8]}"
        tenant_id = "test_tenant1"
        _create_test_tenant_with_creative(session, tenant_id, creative_id)

        # Create 3 reviews for this creative
        for i in range(3):
            review = CreativeReview(
                review_id=f"review_{uuid.uuid4().hex[:8]}",
                creative_id=creative_id,
                tenant_id=tenant_id,
                principal_id=f"principal_{tenant_id}",
                reviewed_at=datetime.now(UTC),
                review_type="ai",
                ai_decision="approve",
                confidence_score=0.9,
                policy_triggered="auto_approve",
                reason=f"Review {i}",
                human_override=False,
                final_decision="approved",
            )
            session.add(review)

        session.commit()

        # TEST: get_creative_reviews returns correct number of reviews
        reviews = get_creative_reviews(session, creative_id, tenant_id)
        assert len(reviews) == 3
        assert all(r.creative_id == creative_id for r in reviews)


@pytest.mark.requires_db
def test_get_ai_review_stats_empty(integration_db):
    """Test get_ai_review_stats returns correct empty state for nonexistent tenant."""
    with get_db_session() as session:
        # TEST: get_ai_review_stats handles no data gracefully
        stats = get_ai_review_stats(session, "nonexistent_tenant", days=30)

        assert stats["total_reviews"] == 0
        assert stats["auto_approved"] == 0
        assert stats["auto_rejected"] == 0
        assert stats["required_human"] == 0
        assert stats["human_overrides"] == 0
        assert stats["override_rate"] == 0.0
        assert stats["avg_confidence"] == 0.0
        assert stats["approval_rate"] == 0.0
        assert stats["policy_breakdown"] == {}


@pytest.mark.requires_db
def test_get_creative_reviews_filters_by_review_type(integration_db):
    """Test get_creative_reviews returns reviews that can be filtered by type."""
    with get_db_session() as session:
        creative_id = f"creative_{uuid.uuid4().hex[:8]}"
        tenant_id = "test_tenant2"
        _create_test_tenant_with_creative(session, tenant_id, creative_id)

        # Create 2 AI reviews and 1 human review
        for i in range(3):
            review = CreativeReview(
                review_id=f"review_{uuid.uuid4().hex[:8]}",
                creative_id=creative_id,
                tenant_id=tenant_id,
                principal_id=f"principal_{tenant_id}",
                reviewed_at=datetime.now(UTC),
                review_type="ai" if i < 2 else "human",
                ai_decision="approve" if i < 2 else None,
                confidence_score=0.9 if i < 2 else None,
                policy_triggered="auto_approve" if i < 2 else None,
                reason=f"Review {i}",
                human_override=(i == 2),
                final_decision="approved",
            )
            session.add(review)

        session.commit()

        # TEST: get_creative_reviews returns all reviews, filtering works
        reviews = get_creative_reviews(session, creative_id, tenant_id)

        assert len(reviews) == 3
        ai_reviews = [r for r in reviews if r.review_type == "ai"]
        human_reviews = [r for r in reviews if r.review_type == "human"]

        assert len(ai_reviews) == 2
        assert len(human_reviews) == 1
        assert human_reviews[0].human_override is True


@pytest.mark.requires_db
def test_get_creative_reviews_tenant_isolation(integration_db):
    """Regression: get_creative_reviews must not return reviews from other tenants.

    Two tenants share the same creative_id. Querying as tenant A must not
    return tenant B's reviews. Without the tenant_id filter this test fails
    because both tenants' reviews match on creative_id alone.
    """
    with get_db_session() as session:
        # Use the same creative_id in both tenants to prove isolation
        shared_creative_id = f"creative_{uuid.uuid4().hex[:8]}"
        tenant_a = f"tenant_a_{uuid.uuid4().hex[:6]}"
        tenant_b = f"tenant_b_{uuid.uuid4().hex[:6]}"

        _create_test_tenant_with_creative(session, tenant_a, shared_creative_id)
        _create_test_tenant_with_creative(session, tenant_b, shared_creative_id)

        # Create 2 reviews for tenant A
        for i in range(2):
            session.add(
                CreativeReview(
                    review_id=f"review_a_{uuid.uuid4().hex[:8]}",
                    creative_id=shared_creative_id,
                    tenant_id=tenant_a,
                    principal_id=f"principal_{tenant_a}",
                    reviewed_at=datetime.now(UTC),
                    review_type="ai",
                    ai_decision="approve",
                    confidence_score=0.95,
                    policy_triggered="auto_approve",
                    reason=f"Tenant A review {i}",
                    human_override=False,
                    final_decision="approved",
                )
            )

        # Create 3 reviews for tenant B
        for i in range(3):
            session.add(
                CreativeReview(
                    review_id=f"review_b_{uuid.uuid4().hex[:8]}",
                    creative_id=shared_creative_id,
                    tenant_id=tenant_b,
                    principal_id=f"principal_{tenant_b}",
                    reviewed_at=datetime.now(UTC),
                    review_type="human",
                    ai_decision=None,
                    confidence_score=None,
                    policy_triggered=None,
                    reason=f"Tenant B review {i}",
                    human_override=True,
                    final_decision="rejected",
                )
            )

        session.commit()

        # Query as tenant A -- must see only tenant A's 2 reviews
        reviews_a = get_creative_reviews(session, shared_creative_id, tenant_a)
        assert len(reviews_a) == 2
        assert all(r.tenant_id == tenant_a for r in reviews_a)
        assert all(r.final_decision == "approved" for r in reviews_a)

        # Query as tenant B -- must see only tenant B's 3 reviews
        reviews_b = get_creative_reviews(session, shared_creative_id, tenant_b)
        assert len(reviews_b) == 3
        assert all(r.tenant_id == tenant_b for r in reviews_b)
        assert all(r.final_decision == "rejected" for r in reviews_b)


@pytest.mark.requires_db
def test_get_creative_with_latest_review_tenant_isolation(integration_db):
    """Regression: get_creative_with_latest_review must not leak across tenants.

    Two tenants share the same creative_id. Querying as tenant A must return
    tenant A's creative and review, not tenant B's. Without the tenant_id
    filter the query could return a cross-tenant review or creative.
    """
    with get_db_session() as session:
        shared_creative_id = f"creative_{uuid.uuid4().hex[:8]}"
        tenant_a = f"tenant_a_{uuid.uuid4().hex[:6]}"
        tenant_b = f"tenant_b_{uuid.uuid4().hex[:6]}"

        _create_test_tenant_with_creative(session, tenant_a, shared_creative_id)
        _create_test_tenant_with_creative(session, tenant_b, shared_creative_id)

        # Create a review for tenant A (approved)
        session.add(
            CreativeReview(
                review_id=f"review_a_{uuid.uuid4().hex[:8]}",
                creative_id=shared_creative_id,
                tenant_id=tenant_a,
                principal_id=f"principal_{tenant_a}",
                reviewed_at=datetime.now(UTC),
                review_type="ai",
                ai_decision="approve",
                confidence_score=0.95,
                policy_triggered="auto_approve",
                reason="Tenant A review",
                human_override=False,
                final_decision="approved",
            )
        )

        # Create a review for tenant B (rejected) -- different decision to distinguish
        session.add(
            CreativeReview(
                review_id=f"review_b_{uuid.uuid4().hex[:8]}",
                creative_id=shared_creative_id,
                tenant_id=tenant_b,
                principal_id=f"principal_{tenant_b}",
                reviewed_at=datetime.now(UTC),
                review_type="human",
                ai_decision=None,
                confidence_score=None,
                policy_triggered=None,
                reason="Tenant B review",
                human_override=True,
                final_decision="rejected",
            )
        )

        session.commit()

        # Query as tenant A
        creative_a, review_a = get_creative_with_latest_review(session, shared_creative_id, tenant_a)
        assert creative_a is not None
        assert creative_a.tenant_id == tenant_a
        assert review_a is not None
        assert review_a.tenant_id == tenant_a
        assert review_a.final_decision == "approved"

        # Query as tenant B
        creative_b, review_b = get_creative_with_latest_review(session, shared_creative_id, tenant_b)
        assert creative_b is not None
        assert creative_b.tenant_id == tenant_b
        assert review_b is not None
        assert review_b.tenant_id == tenant_b
        assert review_b.final_decision == "rejected"
