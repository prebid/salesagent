"""Admin "Verify all" checks every pending property of the tenant, not just the first.

``PropertyVerificationService.verify_all_properties`` used to iterate ORM rows
from an open ``get_db_session()`` and call ``verify_property`` per row, which
opened ``get_db_session()`` again. Both are the same scoped session, so the
inner commit and close detached the outer rows, and the second iteration
raised ``DetachedInstanceError``: the first property was verified, the rest
were reported as one "Bulk verification error".

``fetch_adagents`` is the only thing mocked: it is the network boundary. The
database reads and writes and the authorization check are real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.core.database.models import AuthorizedProperty
from src.services.property_verification_service import PropertyVerificationService
from tests.factories import AuthorizedPropertyFactory, TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "verify_all_properties"
OTHER_TENANT_ID = "verify_all_properties_other"
AGENT_URL = "https://sales-agent.example.com"

# An agent listed with no property filter is authorized for every property on the domain.
ADAGENTS_AUTHORIZING_AGENT = {"authorized_agents": [{"url": AGENT_URL}]}


@pytest.fixture
def seeded_properties(integration_db):
    """Three pending properties to verify, plus two rows the bulk run must not touch."""
    with IntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=TENANT_ID)
        other_tenant = TenantFactory(tenant_id=OTHER_TENANT_ID)
        pending = [AuthorizedPropertyFactory(tenant=tenant, verification_status="pending") for _ in range(3)]
        already_failed = AuthorizedPropertyFactory(tenant=tenant, verification_status="failed")
        other_tenant_pending = AuthorizedPropertyFactory(tenant=other_tenant, verification_status="pending")
        yield (
            env,
            [p.property_id for p in pending],
            already_failed.property_id,
            other_tenant_pending.property_id,
        )


def _statuses(env: IntegrationEnv) -> dict[tuple[str, str], str]:
    session = env.get_session()
    session.rollback()
    rows = session.scalars(select(AuthorizedProperty)).all()
    return {(row.tenant_id, row.property_id): row.verification_status for row in rows}


def test_verify_all_properties_verifies_every_pending_property(seeded_properties):
    env, pending_ids, already_failed_id, other_tenant_id = seeded_properties

    with patch(
        "src.services.property_verification_service.fetch_adagents",
        new_callable=AsyncMock,
        return_value=ADAGENTS_AUTHORIZING_AGENT,
    ):
        results = PropertyVerificationService().verify_all_properties(TENANT_ID, AGENT_URL)

    assert results == {"total_checked": 3, "verified": 3, "failed": 0, "errors": []}

    statuses = _statuses(env)
    for property_id in pending_ids:
        assert statuses[(TENANT_ID, property_id)] == "verified"
    assert statuses[(TENANT_ID, already_failed_id)] == "failed"
    assert statuses[(OTHER_TENANT_ID, other_tenant_id)] == "pending"
