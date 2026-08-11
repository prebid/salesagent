"""Unit tests for resolve_supported_billing and the sync_accounts billing gate.

Pins the #1521 / #1592 billing-policy contract (BR-RULE-059):

- ``resolve_supported_billing(tenant)`` is the single resolution point for BOTH
  the sync_accounts gate and the capabilities ``account.supported_billing``
  emission. Unconfigured (None/missing) → the full spec enum (owner decision,
  2026-07-14); configured → exactly as configured; ``[]`` → reject-everything
  (empty is a real policy, NOT "unconfigured" — no falsy collapse).
- ``_check_billing_policy`` governs UNSUPPORTED billing, never OMITTED billing:
  ``SyncAccountsAccount.billing`` is Optional on the create path, and BR-RULE-059
  does not authorize rejecting an omitted optional field. An omitted billing
  (billing_val=None) must pass the gate under both unconfigured and configured
  tenants.
"""

from src.core.tools.accounts import _check_billing_policy
from tests.factories import PrincipalFactory, TenantFactory


class TestResolveSupportedBilling:
    """resolve_supported_billing: single source for gate + capabilities."""

    def test_no_tenant_returns_full_enum(self):
        """Unconfigured (tenant=None) → the full spec billing-party enum."""
        from src.core.billing_policy import BILLING_PARTY_VALUES, resolve_supported_billing

        assert resolve_supported_billing(None) == list(BILLING_PARTY_VALUES)

    def test_tenant_without_policy_returns_full_enum(self):
        """Unconfigured (no supported_billing key) → the full spec billing-party enum."""
        from src.core.billing_policy import BILLING_PARTY_VALUES, resolve_supported_billing

        tenant = TenantFactory.make_tenant(tenant_id="bp_unset")
        assert resolve_supported_billing(tenant) == list(BILLING_PARTY_VALUES)

    def test_configured_policy_returned_as_configured(self):
        """Configured → exactly the configured list, no widening or reordering."""
        from src.core.billing_policy import resolve_supported_billing

        tenant = TenantFactory.make_tenant(tenant_id="bp_agent", supported_billing=["agent"])
        assert resolve_supported_billing(tenant) == ["agent"]

    def test_empty_policy_stays_reject_all(self):
        """[] is a real 'reject everything' policy — only None/missing means unconfigured.

        A falsy check here would silently convert an operator's explicit
        lockdown into accept-everything. Must be an ``is None`` branch.
        """
        from src.core.billing_policy import resolve_supported_billing

        tenant = TenantFactory.make_tenant(tenant_id="bp_empty", supported_billing=[])
        assert resolve_supported_billing(tenant) == []


class TestBillingGateOmittedBilling:
    """Omitted billing (billing_val=None) is never rejected by the billing gate.

    BR-RULE-059 governs UNSUPPORTED billing, not OMITTED billing. The gate must
    guard ``billing_val is None`` FIRST, before any membership check against
    the resolved policy.
    """

    def test_omitted_billing_passes_gate_unconfigured_tenant(self):
        """Unconfigured tenant: omitted billing is accepted (no error list)."""
        identity = PrincipalFactory.make_identity(tenant_id="gate_unset")

        assert _check_billing_policy(None, identity) is None

    def test_omitted_billing_passes_gate_configured_restrictive_tenant(self):
        """Configured restrictive tenant (supported_billing=['agent']): omitted
        billing is still accepted — restricting SUPPORTED models must not turn
        an omitted optional field into a BILLING_NOT_SUPPORTED rejection."""
        identity = PrincipalFactory.make_identity(
            tenant_id="gate_agent_only",
            supported_billing=["agent"],
        )

        assert _check_billing_policy(None, identity) is None
