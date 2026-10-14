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

from src.core.tenant_context import TenantContext
from src.core.tools.accounts import (
    _FAILURE_CLASS_TO_CODE,
    _build_setup_for_approval,
    _build_sync_result,
    _check_billing_policy,
)
from tests.factories import PrincipalFactory


def _identity_with(**tenant_overrides):
    """Build a ResolvedIdentity with the given tenant_overrides applied to the tenant dict."""
    return PrincipalFactory.make_identity(tenant_id="t1", **tenant_overrides)


def _identity_with_tenantcontext(**fields):
    """Build a ResolvedIdentity whose .tenant is a TenantContext (not a dict)."""
    # virtual_host is required on the projection (a tenant always declares its host);
    # these cases grade billing policy and never read it.
    ctx = TenantContext(tenant_id="t1", name="T1", virtual_host="t1.adcp.test", **fields)
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
        """The gate states a failure CLASS; the wire code is derived from it.

        #1721 moved the gates onto GateFailure + _FAILURE_CLASS_TO_CODE, so the
        code is no longer chosen at the refusal site — asserting the class here
        and the mapping below keeps the same claim without re-pinning a literal
        the gate no longer owns.
        """
        identity = _identity_with(supported_billing=["agent"])
        failures = _check_billing_policy("operator", identity)
        assert failures is not None
        assert len(failures) == 1
        assert failures[0].failure_class == "billing_not_supported"
        assert _FAILURE_CLASS_TO_CODE[failures[0].failure_class] == "BILLING_NOT_SUPPORTED"

    def test_failure_carries_the_supported_list_in_details(self):
        """The supported models travel STRUCTURALLY, not in a sentence.

        billing-not-supported.json puts them at details.supported_billing, and
        after salesagent-3dawm.13 GateFailure has no message to interpolate them
        into -- the buyer-facing sentence is derived from the code.
        """
        identity = _identity_with(supported_billing=["agent", "operator"])
        errors = _check_billing_policy("prepaid", identity)
        assert errors is not None
        # to_wire() is the wire shape; the details block itself is a declared class
        # now, so a misspelled key is a typecheck failure rather than a silent absence.
        assert errors[0].details is not None
        assert errors[0].details.to_wire() == {"scope": "capability", "supported_billing": ["agent", "operator"]}

    def test_failure_names_the_code_and_the_one_supported_model(self):
        """The suggestion is a function of the code (ADR-010), so it is asserted
        on the wire Error the gate produces, not on GateFailure -- which no longer
        carries prose at all."""
        identity = _identity_with(supported_billing=["agent"])
        errors = _check_billing_policy("operator", identity)
        assert errors is not None
        assert _FAILURE_CLASS_TO_CODE[errors[0].failure_class] == "BILLING_NOT_SUPPORTED"
        assert errors[0].details is not None
        assert errors[0].details.supported_billing == ["agent"]

    def test_empty_supported_list_rejects_all(self):
        identity = _identity_with(supported_billing=[])
        failures = _check_billing_policy("agent", identity)
        assert failures is not None
        assert _FAILURE_CLASS_TO_CODE[failures[0].failure_class] == "BILLING_NOT_SUPPORTED"

    # test_tenant_none_accepts is REMOVED. It built an identity with principal_id=None and
    # tenant=None and asserted _check_billing_policy accepted it.
    #
    # Neither half is constructible now. _check_billing_policy takes a ResolvedIdentity,
    # whose principal AND tenant are both required fields, so "no principal" and "no tenant"
    # are not values the parameter can hold -- PrincipalFactory.make_identity cannot build
    # either, and the branch the test drove (resolve_supported_billing's `if tenant` arm) is
    # unreachable from every caller: accounts.py:770 passes the identity the resolver built,
    # and the resolver refuses a missing credential before it can construct one.
    #
    # There is no obligation to re-home. The guarantee the test was probing is now carried
    # by the TYPE -- that is the whole point of a protected tool declaring ResolvedIdentity --
    # and mypy grades it on every run instead of one test asserting one arm of it.

    def test_tenantcontext_access_works(self):
        """The policy reads supported_billing off identity.tenant via the .get() contract.

        The sibling this replaces, ``test_dict_access_works``, claimed to grade the
        OTHER shape — "a raw dict (IMPL transport)" — and asserted
        ``isinstance(identity.tenant, dict)``. Both halves of that premise are gone:
        ``ResolvedIdentity.tenant`` is typed ``LazyTenantContext | None``, so a raw
        dict is not a value it can hold, and ``Transport.IMPL`` is deleted. Both tests
        built their identity through ``PrincipalFactory.make_identity``, which coerces,
        so the pair was one shape tested twice — and only the one with the isinstance
        said so out loud, by failing.
        """
        identity = _identity_with_tenantcontext(supported_billing=["agent"])
        assert _check_billing_policy("agent", identity) is None
        failures = _check_billing_policy("operator", identity)
        assert failures is not None
        assert _FAILURE_CLASS_TO_CODE[failures[0].failure_class] == "BILLING_NOT_SUPPORTED"


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
