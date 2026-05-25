"""Tenant ID colon rejection tests.

The proposals table derives tenant_id from compound account_id values with
``split_part(account_id, ':', 1)``. Tenant IDs must not contain colons, or
future direct writes could silently parse a different tenant boundary.
"""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import IntegrityError

from tests.integration.migration_helpers import run_alembic_downgrade, run_alembic_upgrade

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

MIGRATION_REV = "c67ff82b7514"
PRE_MIGRATION_REV = "f1b2c3d4e5f6"


def _insert_minimal_tenant(conn, *, tenant_id: str, subdomain: str) -> None:
    conn.execute(
        text(
            "INSERT INTO tenants (tenant_id, name, subdomain, ad_server, is_active, "
            "billing_plan, enable_axe_signals, human_review_required, approval_mode, "
            "creative_auto_approve_threshold, creative_auto_reject_threshold, "
            "created_at, updated_at) "
            "VALUES (:tenant_id, 'Tenant Constraint Test', :subdomain, 'mock', TRUE, "
            "'standard', TRUE, FALSE, 'require-human', 0.9, 0.1, NOW(), NOW())"
        ),
        {"tenant_id": tenant_id, "subdomain": subdomain},
    )


def test_tenant_model_declares_no_colon_constraint():
    """ORM metadata mirrors the tenant_id no-colon database constraint."""
    from src.core.database.models import Tenant

    constraints = {
        constraint.name: constraint
        for constraint in Tenant.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    constraint = constraints["ck_tenants_tenant_id_no_colon"]
    assert "position(':' in tenant_id) = 0" in str(constraint.sqltext)


def test_tenant_id_colon_constraint_upgrade_and_downgrade(migration_db):
    """Migration adds and removes the tenant_id no-colon CHECK constraint."""
    engine, db_url = migration_db

    run_alembic_upgrade(db_url, PRE_MIGRATION_REV)
    run_alembic_upgrade(db_url, MIGRATION_REV)

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_minimal_tenant(conn, tenant_id="bad:tenant", subdomain="bad-tenant")

    run_alembic_downgrade(db_url, PRE_MIGRATION_REV)

    with engine.begin() as conn:
        _insert_minimal_tenant(conn, tenant_id="bad:tenant", subdomain="bad-tenant")


def test_tenant_id_colon_constraint_upgrade_fails_with_existing_violations(migration_db):
    """Migration names existing colon-containing tenant IDs before failing."""
    engine, db_url = migration_db

    run_alembic_upgrade(db_url, PRE_MIGRATION_REV)
    with engine.begin() as conn:
        _insert_minimal_tenant(conn, tenant_id="alpha:bad", subdomain="alpha-bad")
        _insert_minimal_tenant(conn, tenant_id="zeta:bad", subdomain="zeta-bad")

    with pytest.raises(RuntimeError) as exc_info:
        run_alembic_upgrade(db_url, MIGRATION_REV)

    message = str(exc_info.value)
    assert "ck_tenants_tenant_id_no_colon" in message
    assert "alpha:bad" in message
    assert "zeta:bad" in message
