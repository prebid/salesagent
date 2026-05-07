"""Unit tests for the per-principal billing_enabled gate (BR-RULE-061).

Slice 4 of the per-buyer-agent refactor. ``Principal.billing_enabled``
controls whether a buyer agent is allowed to be the billing party on any
Account it owns.

Rules tested here:

* ``billing_enabled=True`` (default) + ``billing="agent"`` → accepted
* ``billing_enabled=False`` + ``billing="agent"`` → rejected with
  ``BILLING_NOT_PERMITTED_FOR_AGENT`` + ``recovery="correctable"``
* ``billing_enabled=False`` + ``billing="operator"`` → accepted
  (the gate only fires for ``"agent"``; operator-paid accounts are always
  allowed regardless of the agent's flag)
* tenant-level ``supported_billing`` filter (BR-RULE-059) still applies
  AND short-circuits before the principal-level check (different error
  code, ``BILLING_NOT_SUPPORTED``)
* ``_read_principal_billing_enabled_sync`` returns False when the row
  vanishes (fail-closed)
"""

from __future__ import annotations

from unittest.mock import patch

from src.core.resolved_identity import ResolvedIdentity
from src.core.tools.accounts import (
    _check_billing_policy,
    _read_principal_billing_enabled_sync,
)


def _identity(
    *, principal_id: str = "p1", tenant_id: str = "t1", supported: list[str] | None = None
) -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={
            "tenant_id": tenant_id,
            "supported_billing": supported,
        },
        protocol="mcp",
    )


class TestTenantPolicy:
    """BR-RULE-059 — tenant-level supported_billing filter (existing behavior)."""

    def test_billing_in_supported_list_accepted(self):
        identity = _identity(supported=["operator", "agent"])
        result = _check_billing_policy("operator", identity, principal_billing_enabled=True)
        assert result is None

    def test_billing_not_in_supported_list_rejected(self):
        identity = _identity(supported=["operator"])
        result = _check_billing_policy("agent", identity, principal_billing_enabled=True)
        assert result is not None
        assert result[0].code == "BILLING_NOT_SUPPORTED"
        assert "not supported by this seller" in result[0].message

    def test_no_supported_list_means_no_tenant_filter(self):
        # supported_billing not configured → tenant filter accepts; falls
        # through to the principal-level check.
        identity = _identity(supported=None)
        result = _check_billing_policy("agent", identity, principal_billing_enabled=True)
        assert result is None


class TestPrincipalBillingEnabled:
    """BR-RULE-061 — per-principal billing_enabled gate."""

    def test_billing_agent_accepted_when_principal_enabled(self):
        identity = _identity()
        result = _check_billing_policy("agent", identity, principal_billing_enabled=True)
        assert result is None

    def test_billing_agent_rejected_when_principal_disabled(self):
        identity = _identity()
        result = _check_billing_policy("agent", identity, principal_billing_enabled=False)
        assert result is not None
        # Spec-defined code so buyers can distinguish recoverable
        # (per-principal — try billing="operator") from terminal (tenant-policy).
        assert result[0].code == "BILLING_NOT_PERMITTED_FOR_AGENT"
        assert result[0].recovery.value == "correctable"
        # Error message uses protocol-level language; no internal column names.
        assert "billing_enabled" not in result[0].message
        assert result[0].suggestion == "Use billing='operator'."

    def test_billing_operator_accepted_regardless_of_principal_flag(self):
        # billing="operator" should NEVER hit the principal gate — the gate
        # only fires for billing="agent".
        identity = _identity()
        result = _check_billing_policy("operator", identity, principal_billing_enabled=False)
        assert result is None

    def test_billing_null_accepted_regardless_of_principal_flag(self):
        # billing=NULL (untyped) likewise bypasses the agent gate.
        identity = _identity()
        result = _check_billing_policy(None, identity, principal_billing_enabled=False)
        assert result is None


class TestGateOrdering:
    """Tenant filter must short-circuit before the principal filter — when
    billing isn't supported by the seller at all, the principal gate
    shouldn't even consider whether the agent's flag is set."""

    def test_tenant_filter_runs_first_when_principal_also_disabled(self):
        identity = _identity(supported=["operator"])  # excludes "agent"
        # Principal also disabled — tenant filter should still win because
        # it's the more general "this seller never bills agents at all".
        result = _check_billing_policy("agent", identity, principal_billing_enabled=False)
        assert result is not None
        # Code is the tenant-level one, NOT the principal-level one.
        assert result[0].code == "BILLING_NOT_SUPPORTED"
        assert "Supported models: operator" in result[0].message


class TestReadPrincipalBillingEnabled:
    """The one-shot DB read pulled out of the per-entry hot path."""

    def test_returns_true_when_row_billing_enabled(self):
        with patch("src.core.database.database_session.get_db_session") as mock_db:
            mock_db.return_value.__enter__.return_value.scalars.return_value.first.return_value = True
            result = _read_principal_billing_enabled_sync("t1", "p1")
        assert result is True

    def test_returns_false_when_row_billing_disabled(self):
        with patch("src.core.database.database_session.get_db_session") as mock_db:
            mock_db.return_value.__enter__.return_value.scalars.return_value.first.return_value = False
            result = _read_principal_billing_enabled_sync("t1", "p1")
        assert result is False

    def test_returns_false_when_principal_vanished(self):
        # Token validates but principal row was deleted between auth + check —
        # extremely narrow race, fail closed.
        with patch("src.core.database.database_session.get_db_session") as mock_db:
            mock_db.return_value.__enter__.return_value.scalars.return_value.first.return_value = None
            result = _read_principal_billing_enabled_sync("t1", "p1")
        assert result is False
