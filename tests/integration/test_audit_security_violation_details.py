"""Integration repro: security-violation audit details must persist, not empty to {}.

``AuditLogger.log_security_violation()`` (src/core/audit_logger.py) passes
``details=json.dumps({...})`` into ``AuditLog.details`` — a JSONType column.
``JSONType.process_bind_param`` accepts only dict/list/BaseModel and silently
coerces anything else (including the str json.dumps produces) to ``{}`` with a
warning. Result: every security violation's forensic payload (which resource
was probed, why access was denied) is stored as an empty object.

This test calls the REAL production ``log_security_violation()``, reads the
persisted row back through a separate session, and asserts the details
round-trip intact. It fails while the json.dumps call is in place
(salesagent-akjc); the same disease affects principal ``platform_mappings``
and the self-serve signup tenant fields, guarded separately.
"""

from __future__ import annotations

import pytest

from tests.harness._base import BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT_ID = "audit_secviol_t1"


class TestSecurityViolationDetailsPersist:
    def test_details_round_trip_not_emptied(self, integration_db):
        from src.core.audit_logger import get_audit_logger
        from src.core.database.models import AuditLog

        with BareIntegrationEnv(tenant_id=_TENANT_ID) as env:
            from tests.factories import TenantFactory

            TenantFactory(tenant_id=_TENANT_ID)
            env.get_session()  # commit factory data so the FK is satisfied

            logger = get_audit_logger("mock", tenant_id=_TENANT_ID)
            logger.log_security_violation(
                operation="get_media_buy_delivery",
                principal_id="acme",
                resource_id="mb_other_tenant_1",
                reason="principal does not own media buy",
            )

            session = env.get_session()
            session.expire_all()
            row = env.get_one(AuditLog, tenant_id=_TENANT_ID)
            assert row is not None, "log_security_violation() did not persist an AuditLog row"

            # The forensic payload must survive the write. Before the fix,
            # json.dumps(...) hit JSONType's non-dict coercion and the row
            # stored details == {} — silent evidence loss on every violation.
            assert row.details == {
                "resource_id": "mb_other_tenant_1",
                "reason": "principal does not own media buy",
            }, f"AuditLog.details emptied on write: {row.details!r}"
