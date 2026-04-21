"""Unit tests for pure helper functions in src/core/tools/accounts.py.

Covers:
- _check_billing_policy (BR-RULE-059): validates billing against seller's supported_billing
- _build_setup_for_approval (BR-RULE-060): builds Setup for pending_approval modes

These are pure functions with no DB or transport dependencies, so they are
tested in isolation without the harness.

Part of epic salesagent-ng3n (Complete #1184), ticket salesagent-bh22.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.core.tenant_context import TenantContext
from src.core.tools.accounts import _build_setup_for_approval, _check_billing_policy
from tests.factories import PrincipalFactory


def _identity_with(**tenant_overrides):
    """Build a ResolvedIdentity with the given tenant_overrides applied to the tenant dict."""
    return PrincipalFactory.make_identity(tenant_id="t1", **tenant_overrides)


def _identity_with_tenantcontext(**fields):
    """Build a ResolvedIdentity whose .tenant is a TenantContext (not a dict)."""
    ctx = TenantContext(tenant_id="t1", name="T1", subdomain="t1", **fields)
    return PrincipalFactory.make_identity(tenant_id="t1", tenant=ctx.model_dump())


class TestCheckBillingPolicy:
    """BR-RULE-059: seller billing policy enforcement."""

    def test_no_policy_configured_accepts_all(self):
        identity = _identity_with()  # no supported_billing key
        assert _check_billing_policy("operator", identity) is None
        assert _check_billing_policy("agent", identity) is None

    def test_supported_value_accepted(self):
        identity = _identity_with(supported_billing=["agent"])
        assert _check_billing_policy("agent", identity) is None

    def test_unsupported_value_rejected(self):
        identity = _identity_with(supported_billing=["agent"])
        errors = _check_billing_policy("operator", identity)
        assert errors is not None
        assert len(errors) == 1
        assert errors[0].code == "BILLING_NOT_SUPPORTED"

    def test_error_message_includes_supported_list(self):
        identity = _identity_with(supported_billing=["agent", "operator"])
        errors = _check_billing_policy("prepaid", identity)
        assert errors is not None
        assert "agent" in errors[0].message
        assert "operator" in errors[0].message

    def test_error_includes_suggestion_field(self):
        identity = _identity_with(supported_billing=["agent"])
        errors = _check_billing_policy("operator", identity)
        assert errors is not None
        assert errors[0].suggestion is not None
        assert "agent" in errors[0].suggestion

    def test_empty_supported_list_rejects_all(self):
        identity = _identity_with(supported_billing=[])
        errors = _check_billing_policy("agent", identity)
        assert errors is not None
        assert errors[0].code == "BILLING_NOT_SUPPORTED"

    def test_tenant_none_accepts(self):
        identity = PrincipalFactory.make_identity(tenant_id="t1", tenant=None)
        assert _check_billing_policy("operator", identity) is None

    def test_tenantcontext_access_works(self):
        """When identity.tenant is a TenantContext object, the same .get() contract applies."""
        identity = _identity_with_tenantcontext(supported_billing=["agent"])
        assert _check_billing_policy("agent", identity) is None
        errors = _check_billing_policy("operator", identity)
        assert errors is not None
        assert errors[0].code == "BILLING_NOT_SUPPORTED"

    def test_dict_access_works(self):
        """When identity.tenant is a raw dict (IMPL transport), same behavior."""
        identity = _identity_with(supported_billing=["agent"])
        assert isinstance(identity.tenant, dict)
        assert _check_billing_policy("agent", identity) is None


class TestBuildSetupForApproval:
    """BR-RULE-060: setup object generation for account approval modes."""

    def test_credit_review_returns_setup_with_url_message_expires(self):
        setup = _build_setup_for_approval("credit_review", "tenant_a")
        assert setup is not None
        assert setup.message
        assert setup.url is not None
        assert "tenant_a" in str(setup.url)
        assert setup.expires_at is not None

    def test_credit_review_expiry_is_seven_days(self):
        before = datetime.now(tz=UTC)
        setup = _build_setup_for_approval("credit_review", "tenant_a")
        after = datetime.now(tz=UTC)
        lower = before + timedelta(days=7) - timedelta(seconds=5)
        upper = after + timedelta(days=7) + timedelta(seconds=5)
        assert lower <= setup.expires_at <= upper

    def test_legal_review_returns_message_only(self):
        setup = _build_setup_for_approval("legal_review", "tenant_a")
        assert setup is not None
        assert setup.message
        assert setup.url is None
        assert setup.expires_at is None

    def test_auto_returns_none(self):
        assert _build_setup_for_approval("auto", "tenant_a") is None

    def test_unknown_mode_returns_none(self):
        """Defensive: unknown modes behave like auto (no setup, account active)."""
        assert _build_setup_for_approval("something_else", "tenant_a") is None

    def test_empty_string_mode_returns_none(self):
        assert _build_setup_for_approval("", "tenant_a") is None
