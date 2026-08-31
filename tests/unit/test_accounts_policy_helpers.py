"""Unit tests for pure helper functions in src/core/tools/accounts.py.

Covers:
- _check_billing_policy (BR-RULE-059): validates billing against seller's supported_billing
- _build_setup_for_approval (BR-RULE-060): builds Setup for pending_approval modes

These are pure functions with no DB or transport dependencies, so they are
tested in isolation without the harness.

Part of epic (Complete #1184), ticket .
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.exceptions import AdCPConfigurationError
from src.core.helpers.account_helpers import resolve_supported_billing
from src.core.tenant_context import TenantContext
from src.core.tools.accounts import _build_setup_for_approval, _build_sync_result, _check_billing_policy
from tests.factories import PrincipalFactory


def _identity_with(**tenant_overrides):
    """Build a ResolvedIdentity with the given tenant_overrides applied to the tenant dict."""
    return PrincipalFactory.make_identity(tenant_id="t1", **tenant_overrides)


def _identity_with_tenantcontext(**fields):
    """Build a ResolvedIdentity whose .tenant is a TenantContext (not a dict)."""
    ctx = TenantContext(tenant_id="t1", name="T1", subdomain="t1", **fields)
    return PrincipalFactory.make_identity(tenant_id="t1", tenant=ctx.model_dump())


class TestCheckBillingPolicy:
    """BR-RULE-059: seller billing policy enforcement.

    ``_check_billing_policy`` is now a pure gate — the accepted ``supported`` set is
    resolved once by the caller (see ``TestResolveSupportedBilling`` for the seller-
    misconfiguration raise, which is now a precondition before any DB work — #1329
    finding 10) and passed in. This function returns errors or None, never raises.
    """

    def test_no_policy_configured_accepts_all(self):
        # None config -> resolver returns the default {operator, agent}; both accepted.
        supported = [b.value for b in resolve_supported_billing(None)]
        assert _check_billing_policy("operator", supported) is None
        assert _check_billing_policy("agent", supported) is None

    def test_supported_value_accepted(self):
        assert _check_billing_policy("agent", ["agent"]) is None

    def test_unsupported_value_rejected(self):
        errors = _check_billing_policy("operator", ["agent"])
        assert errors is not None
        assert len(errors) == 1
        assert errors[0].code == "BILLING_NOT_SUPPORTED"

    def test_error_message_includes_supported_list(self):
        errors = _check_billing_policy("prepaid", ["agent", "operator"])
        assert errors is not None
        assert "agent" in errors[0].message
        assert "operator" in errors[0].message

    def test_error_includes_suggestion_field(self):
        errors = _check_billing_policy("operator", ["agent"])
        assert errors is not None
        assert errors[0].suggestion is not None
        assert "agent" in errors[0].suggestion

    def test_pure_gate_never_raises_on_empty_supported(self):
        # The empty-set seller misconfiguration is a PRECONDITION raise
        # (resolve_supported_billing), not this gate's job. Given an empty accepted
        # set, this pure gate simply rejects — it never raises (#1329 finding 10).
        errors = _check_billing_policy("agent", [])
        assert errors is not None
        assert errors[0].code == "BILLING_NOT_SUPPORTED"


class TestResolveSupportedBilling:
    """The accepted-billing resolver — the seller-config precondition (#1329 finding 10).

    The raise moved here from ``_check_billing_policy`` so a misconfigured tenant fails
    before the sync_accounts UoW opens, not per-entry mid-loop.
    """

    def test_empty_supported_list_is_seller_misconfiguration(self):
        # An explicit empty supported_billing is not spec-expressible at the pin
        # (account.supported_billing minItems:1) and names no account-billable party,
        # so it is a SELLER misconfiguration: a TERMINAL CONFIGURATION_ERROR rather than
        # returning [] and letting the gate emit a buyer-correctable code (#1329 R9-C2).
        with pytest.raises(AdCPConfigurationError) as exc_info:
            resolve_supported_billing({"supported_billing": []})
        assert exc_info.value.recovery == "terminal"
        # Buyer message must not disclose the tenant config or the internal constraint name.
        assert "ck_accounts_billing" not in str(exc_info.value)

    def test_non_account_billing_list_is_seller_misconfiguration(self):
        # A NON-empty list naming only non-account-billable parties (advertiser is a valid
        # media-buy party but not account-billable) yields no account-billable party →
        # same TERMINAL CONFIGURATION_ERROR, buyer-safe message (#1329 R9-C1).
        with pytest.raises(AdCPConfigurationError) as exc_info:
            resolve_supported_billing({"supported_billing": ["advertiser"]})
        assert exc_info.value.recovery == "terminal"
        assert "advertiser" not in str(exc_info.value)
        assert "ck_accounts_billing" not in str(exc_info.value)

    def test_tenant_none_returns_default(self):
        assert resolve_supported_billing(None)  # non-empty default set

    def test_tenantcontext_access_works(self):
        """A TenantContext (not a dict) exposes .get() identically for the resolver."""
        ctx = TenantContext(tenant_id="t1", name="T1", subdomain="t1", supported_billing=["agent"])
        supported = [b.value for b in resolve_supported_billing(ctx.model_dump())]
        assert supported == ["agent"]
        errors = _check_billing_policy("operator", supported)
        assert errors is not None
        assert errors[0].code == "BILLING_NOT_SUPPORTED"


class TestAdvisoryErrorMetadataDerivation:
    """Advisory per-account Error code/recovery cannot drift from the pin (#1329 finding 10).

    VALIDATION_ERROR derives from ``AdCPValidationError`` class metadata; BILLING_NOT_SUPPORTED
    is a demoted spec code with no typed subclass, so its literal recovery is pinned here
    against the pinned error-code.json enumMetadata — the pin governance.py gets for free
    from its exception class.
    """

    def test_validation_error_advisory_derives_from_class(self):
        from src.core.exceptions import AdCPValidationError
        from src.core.tools.accounts import _VALIDATION_ERROR_CODE, _VALIDATION_ERROR_RECOVERY

        assert _VALIDATION_ERROR_CODE == AdCPValidationError._default_error_code == "VALIDATION_ERROR"
        assert _VALIDATION_ERROR_RECOVERY == AdCPValidationError._default_recovery

    def test_billing_not_supported_recovery_matches_pinned_enum(self):
        from src.core.tools.accounts import _BILLING_NOT_SUPPORTED_CODE, _BILLING_NOT_SUPPORTED_RECOVERY
        from tests.harness.transport import _pinned_error_metadata

        meta = _pinned_error_metadata()
        assert _BILLING_NOT_SUPPORTED_CODE in meta, "BILLING_NOT_SUPPORTED must exist in the pinned enumMetadata"
        assert _BILLING_NOT_SUPPORTED_RECOVERY == meta[_BILLING_NOT_SUPPORTED_CODE]["recovery"]


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


class TestBuildSyncResult:
    """BR-UC-011 POST-S5: seller-assigned account_id round-trips through sync responses.

    Regression guard for : _build_sync_result previously dropped
    account_id, leaving buyers without the seller-assigned identifier they need
    for subsequent account-scoped operations.
    """

    def _brand(self):
        # Minimal brand object; SyncResponseAccount accepts the AdCP brand shape.
        return {"domain": "example.com"}

    def test_account_id_round_trips_for_created(self):
        result = _build_sync_result(
            brand=self._brand(),
            operator="op_1",
            action="created",
            status="active",
            account_id="acct_123",
            name="Example",
        )
        assert result.account_id == "acct_123"

    def test_account_id_round_trips_for_updated(self):
        result = _build_sync_result(
            brand=self._brand(),
            operator="op_1",
            action="updated",
            status="active",
            account_id="acct_456",
        )
        assert result.account_id == "acct_456"

    def test_account_id_round_trips_for_unchanged(self):
        result = _build_sync_result(
            brand=self._brand(),
            operator="op_1",
            action="unchanged",
            status="active",
            account_id="acct_789",
        )
        assert result.account_id == "acct_789"

    def test_account_id_omitted_for_failed(self):
        """Failed accounts have no provisioned id — account_id stays None."""
        result = _build_sync_result(
            brand=self._brand(),
            operator="op_1",
            action="failed",
            status="rejected",
        )
        assert result.account_id is None
