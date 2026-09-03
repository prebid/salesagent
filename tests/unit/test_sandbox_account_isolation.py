"""Sandbox accounts must never reach a real ad-server adapter.

AdCP 3.1.1 ``dist/docs/3.1.1/media-buy/advanced-topics/sandbox.mdx`` §Seller
implementation, for any request referencing a sandbox account:

    - MUST NOT make real ad platform API calls (no real orders, line items, etc.)
    - MUST NOT charge real money or create real billing records
    - MUST return realistic response shapes with simulated data

Before this fix a ``sandbox: true`` create_media_buy produced a byte-identical outbound
call to the live path (verified against a real Kevel tenant), because adapters are
selected per-tenant and the account never reached ``get_adapter``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.mock_ad_server import MockAdServer as MockAdServerAdapter
from src.core.helpers.adapter_helpers import get_adapter
from tests.factories.principal import PrincipalFactory


@pytest.fixture
def kevel_tenant() -> MagicMock:
    """A tenant whose real ad server is Kevel — the live-dispatch configuration."""
    tenant = MagicMock()
    tenant.tenant_id = "tenant_acme"
    tenant.ad_server = "kevel"
    return tenant


@pytest.fixture
def principal() -> MagicMock:
    principal = MagicMock()
    principal.principal_id = "principal_1"
    principal.platform_mappings = {}
    return principal


class TestSandboxForcesMockAdapter:
    def test_sandbox_account_gets_mock_adapter_on_a_kevel_tenant(self, kevel_tenant, principal) -> None:
        """The whole defect in one assertion: sandbox must not select the tenant's real adapter."""
        adapter = get_adapter(principal, dry_run=False, tenant=kevel_tenant, sandbox=True)

        assert isinstance(adapter, MockAdServerAdapter), (
            f"sandbox account selected {type(adapter).__name__}; a sandbox request must never "
            "reach a real ad-platform adapter (sandbox.mdx §Seller implementation)"
        )

    def test_sandbox_short_circuits_before_reading_adapter_config(self, kevel_tenant, principal) -> None:
        """Sandbox must not even load the real adapter's credentials.

        Pins the *ordering*, not just the outcome: returning mock after fetching Kevel's
        API key would still satisfy the test above while touching live configuration.
        """
        with patch("src.core.helpers.adapter_helpers.get_db_session") as mock_session:
            adapter = get_adapter(principal, dry_run=False, tenant=kevel_tenant, sandbox=True)

        assert isinstance(adapter, MockAdServerAdapter)
        mock_session.assert_not_called()

    def test_live_account_still_selects_the_tenant_adapter(self, kevel_tenant, principal) -> None:
        """Negative control: without sandbox the tenant's configured adapter is still chosen.

        Without this, a fix that returned mock unconditionally would pass the suite while
        silently disabling every real campaign.
        """
        with patch("src.core.helpers.adapter_helpers.get_db_session") as mock_session:
            mock_session.return_value.__enter__.return_value = MagicMock()
            get_adapter(principal, dry_run=False, tenant=kevel_tenant, sandbox=False)

        # The live path opens a session to read AdapterConfig; the sandbox path never does.
        mock_session.assert_called_once_with()


class TestSandboxRidesOnIdentity:
    def test_identity_defaults_to_live(self) -> None:
        """An identity with no resolved account is live — sandbox suppresses side effects,
        so the safe default is the one that does not claim suppression."""
        assert PrincipalFactory.make_identity().sandbox is False

    def test_identity_is_immutable_but_copyable(self) -> None:
        """The transport funnel sets sandbox via model_copy on a frozen identity."""
        identity = PrincipalFactory.make_identity()
        updated = identity.model_copy(update={"account_id": "acc_sandbox", "sandbox": True})

        assert updated.sandbox is True
        assert identity.sandbox is False, "the original identity must not be mutated"


class TestResolvedAccountCarriesMode:
    """The NamedTuple's own contract — the DERIVATION is graded elsewhere.

    This class once claimed to grade the resolution seam ("the flag was previously
    discarded at the one place it was known") while asserting
    ``ResolvedAccount("acc_1", bool(None)).sandbox is False`` — which computes
    ``bool()`` in the test body and reads a field back. Production's
    ``bool(account.sandbox)`` at ``account_helpers.py:216``/``:270`` never ran, so
    dropping the coercion at both sites left it green (it emits 2 mypy arg-type
    errors, so the coercion is enforced by ``make quality``, but not by this test).

    The derivation is now graded against a real row, through production, in
    ``tests/integration/test_resolve_account.py::TestResolveAccountCarriesSandboxMode``
    — including the NULL-column case this once approximated. What remains here is
    the narrow thing a unit test can honestly own: the container preserves what it
    is handed.
    """

    def test_resolved_account_preserves_the_mode_it_is_given(self) -> None:
        from src.core.helpers.account_helpers import ResolvedAccount

        assert ResolvedAccount("acc_1", False).sandbox is False
        assert ResolvedAccount("acc_2", True).sandbox is True


class TestSandboxMockAdapterConfigIsStated:
    """The sandbox mock and the tenant's own mock must not silently differ.

    ``get_adapter`` builds a mock adapter on two paths: the sandbox short-circuit, and
    the tenant whose configured ad server IS mock. The second reads AdapterConfig and
    defaults ``manual_approval_required`` to True "for safety"; the first cannot read
    that row at all — short-circuiting before any adapter config is the point.

    ``_create_media_buy_impl`` reads ``adapter.manual_approval_required``, so an absent
    key on one path and an explicit default on the other is a live behavioural fork: a
    sandbox buy auto-executes where the tenant's mock queues. A test asserting only
    "the mock adapter was selected" cannot see it, which is why this asserts the value.
    """

    @staticmethod
    def _sandbox_adapter():
        from unittest.mock import MagicMock

        from src.core.helpers.adapter_helpers import get_adapter

        principal = MagicMock()
        principal.platform_mappings = {}
        return get_adapter(principal, dry_run=True, tenant={"tenant_id": "t_cfg", "ad_server": "mock"}, sandbox=True)

    def test_sandbox_mock_does_not_require_manual_approval(self) -> None:
        """Deliberate, and stated in the config rather than inherited from the base class.

        If this ever needs to become True, the change belongs in adapter_helpers with
        its reason — not by deleting the key and letting the base default decide.
        """
        assert self._sandbox_adapter().manual_approval_required is False, (
            "the sandbox mock's approval behaviour changed; a simulator that parks the "
            "buyer's request awaiting a human defeats what the sandbox is for"
        )

    def test_sandbox_mock_is_actually_the_mock_adapter(self) -> None:
        """Anchor: without this the assertion above could pass on any object."""
        from src.adapters.mock_ad_server import MockAdServer

        assert isinstance(self._sandbox_adapter(), MockAdServer)


class TestDanglingAccountReferenceIsSellerSide:
    """``_account_is_sandbox`` on a buy-keyed path: a stale id is a seller-side fault.

    This raise is reached from update / performance / delivery / admin, where the buyer
    supplied no account reference — the id came off the seller's own
    ``media_buys.account_id``. So the condition is data integrity on the seller's side,
    not a buyer mistake, and the two things that follow from that are pinned here.

    The code stays ``ACCOUNT_NOT_FOUND`` deliberately (recovery is ``terminal`` either
    way, and CONFIGURATION_ERROR would move the wire status to 5xx for a condition the
    composite FK makes barely reachable). That decision is only defensible while the
    suggestion is the spec's own for that code and the telemetry is not buyer-grade;
    both are asserted, because a later edit could quietly undo either.
    """

    @staticmethod
    def _accounts_repo_missing(account_id: str = "acc_gone") -> MagicMock:
        repo = MagicMock()
        repo.tenant_id = "t1"
        repo.get_by_id.return_value = None
        return repo

    def test_suggestion_is_the_specs_canonical_remedy_for_this_code(self) -> None:
        """AdCP 3.1.1 accounts-and-agents.mdx:464 pairs ACCOUNT_NOT_FOUND with
        "Check account reference, re-run sync_accounts".

        Scoped to THIS raise — ``_account_is_sandbox``'s buy-keyed dangling-reference
        path, described in the class docstring above — not a claim that every
        ACCOUNT_NOT_FOUND raise in this module shares one remedy; the buyer-supplied
        account_id paths (explicit account_id, natural-key lookup) carry their own,
        different remedies for a buyer-facing miss. Emitting the spec's code with a
        locally-invented remedy here gives a buyer parsing this code a second phrasing
        it has no way to anticipate. If the remedy is ever judged wrong for this path,
        the fix is a different CODE, not a different suggestion under the same one —
        which is exactly what this asserts.
        """
        from src.core.exceptions import AdCPAccountNotFoundError
        from src.core.helpers.account_helpers import _account_is_sandbox

        with pytest.raises(AdCPAccountNotFoundError) as exc_info:
            _account_is_sandbox(self._accounts_repo_missing(), "acc_gone")

        assert exc_info.value.error_code == "ACCOUNT_NOT_FOUND"
        assert exc_info.value.suggestion == "Check account reference, re-run sync_accounts."

    def test_the_fault_is_logged_at_error_not_left_to_the_boundary(self) -> None:
        """Typed AdCPErrors reach ``record_boundary_error``, which logs WARNING with no
        traceback — right for a buyer mistake, wrong for a corruption only an operator
        can fix. Spying the module logger rather than using caplog: other tests leave
        global logging state behind, so a capture-based assertion can silently see
        nothing and pass.
        """
        from src.core.exceptions import AdCPAccountNotFoundError
        from src.core.helpers import account_helpers

        with patch.object(account_helpers, "logger") as spy:
            with pytest.raises(AdCPAccountNotFoundError):
                account_helpers._account_is_sandbox(self._accounts_repo_missing(), "acc_gone")

        spy.warning.assert_not_called()
        assert spy.error.call_count == 1, "the dangling reference was not logged at ERROR"
        _fmt, *args = spy.error.call_args.args
        assert "acc_gone" in args, "the log does not name the unresolvable account id"
        assert spy.error.call_args.kwargs.get("stack_info") is True, (
            "no stack was captured, so an operator gets the severity but not the call path"
        )

    def test_a_resolvable_account_neither_raises_nor_logs(self) -> None:
        """Negative control: an unconditional log or raise would satisfy the above."""
        from src.core.helpers import account_helpers

        repo = MagicMock()
        repo.tenant_id = "t1"
        repo.get_by_id.return_value = MagicMock(sandbox=True)

        with patch.object(account_helpers, "logger") as spy:
            assert account_helpers._account_is_sandbox(repo, "acc_ok") is True

        spy.error.assert_not_called()
