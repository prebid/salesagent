"""Integration test: Decimal budget must serialize identically across audit sinks.

Regression coverage for #1417 (PR #1417 re-review, @e1280b368).

``AuditLogger.log_operation()`` writes the same ``details`` dict to TWO sinks:

- the DB ``AuditLog.details`` JSONType column, and
- the ``.jsonl`` structured backup log.

A ``Decimal`` budget (what ``get_total_budget()`` returns — float is wrong for
money) currently serializes DIVERGENTLY: the DB path runs through the engine-wide
``_pydantic_json_serializer`` (``pydantic_core.to_json`` with ``fallback=str``),
which stringifies a bare ``Decimal`` -> the budget is stored as a JSON STRING
("12345.67"). The ``.jsonl`` path runs through ``_audit_json_default``
(``Decimal`` -> ``float``) -> the budget is written as a JSON NUMBER (12345.67).

The two sinks MUST agree: both should carry a JSON number. The DB string form
also breaks admin readers (activity_stream.py:80,
business_activity_service.py:192) which format the budget with ``:,.0f`` — that
format spec raises ``ValueError`` on a str.

This test calls the REAL production ``log_operation()`` with a ``Decimal`` budget,
reads the persisted row back through a fresh DB session, and asserts the stored
budget is a JSON number (matching the ``.jsonl`` sink). It FAILS against current
production, which persists the string form.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.harness._base import BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT_ID = "audit_budget_ser_t1"
_BUDGET = Decimal("12345.67")


def _read_back_details(env: BareIntegrationEnv, tenant_id: str) -> dict:
    """Fetch the persisted AuditLog.details through the env session (round-trip).

    ``log_operation()`` writes + commits through its OWN ``get_db_session()``,
    so reading via the env's separate session observes the actual DB-serialized
    JSONB (not an identity-mapped Python dict). ``expire_all()`` guards against
    the session serving a stale snapshot from an already-loaded row.
    """
    from src.core.database.models import AuditLog

    session = env.get_session()
    session.expire_all()
    row = env.get_one(AuditLog, tenant_id=tenant_id)
    assert row is not None, "log_operation() did not persist an AuditLog row"
    assert row.details is not None
    return row.details


class TestDecimalBudgetSerializesAsNumberInDB:
    """The DB details sink must store a Decimal budget as a JSON number.

    This pins the Core Invariant of #1417: a Decimal budget serializes
    identically (as a JSON number) across the DB details column and the .jsonl
    structured log.
    """

    def test_db_details_budget_is_a_number_not_a_string(self, integration_db):
        from src.core.audit_logger import get_audit_logger

        with BareIntegrationEnv(tenant_id=_TENANT_ID) as env:
            from tests.factories import TenantFactory

            TenantFactory(tenant_id=_TENANT_ID)
            env.get_session()  # commit factory data so the FK is satisfied

            logger = get_audit_logger("mock", tenant_id=_TENANT_ID)
            logger.log_operation(
                operation="create_media_buy",
                principal_name="Acme Buyer",
                principal_id="acme",
                adapter_id="mock-advertiser-1",
                success=True,
                details={"budget": _BUDGET},
                tenant_id=_TENANT_ID,
            )

            persisted = _read_back_details(env, _TENANT_ID)
            budget = persisted["budget"]

            # The .jsonl sink emits a JSON number (Decimal -> float). The DB sink
            # must match. Current production stores the string "12345.67".
            assert type(budget) is not str, (
                f"DB audit details budget persisted as a string {budget!r} "
                f"(type {type(budget).__name__}); the .jsonl sink emits a JSON "
                f"number, so the two audit sinks diverge (salesagent-2882)."
            )
            assert float(budget) == pytest.approx(float(_BUDGET))

    def test_db_details_budget_is_number_formattable_for_admin_readers(self, integration_db):
        """The persisted budget must survive the admin ':,.0f' render path.

        admin/blueprints/activity_stream.py:80 and
        admin/services/business_activity_service.py:192 do
        ``f"...${details['budget']:,.0f}"``. ``:,.0f`` raises ValueError on a
        str, so high-value create rows the DB stored as strings already break
        the admin activity stream. Grounds the reviewer's admin-reader finding.
        """
        from src.core.audit_logger import get_audit_logger

        with BareIntegrationEnv(tenant_id=_TENANT_ID) as env:
            from tests.factories import TenantFactory

            TenantFactory(tenant_id=_TENANT_ID)
            env.get_session()

            logger = get_audit_logger("mock", tenant_id=_TENANT_ID)
            logger.log_operation(
                operation="create_media_buy",
                principal_name="Acme Buyer",
                principal_id="acme",
                adapter_id="mock-advertiser-1",
                success=True,
                details={"budget": _BUDGET},
                tenant_id=_TENANT_ID,
            )

            persisted = _read_back_details(env, _TENANT_ID)

            # Reproduces the latent admin ValueError: ':,.0f' on a DB-string budget.
            rendered = f"Budget: ${persisted['budget']:,.0f}"
            assert rendered.startswith("Budget: $")
