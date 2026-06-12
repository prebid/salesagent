"""adcp 6.3 obligation population on the create_media_buy success response.

Covers the b9 follow-up (#706): context echo (BR-RULE-043-01), sandbox flag
(UC-002-UPG-09), and the buyer-safe account projection (UC-002-UPG-07) — including
the redaction guard that seller financials never reach the buyer-facing wire dump.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.core.tools.media_buy_create import _buyer_safe_account, _success_response_extras

# The ONLY fields the buyer-safe projection may emit. An allowlist (not a denylist)
# so the guard stays correct as the adcp Account model grows new sensitive fields:
# any field outside this set leaking onto the dump fails the test.
_BUYER_SAFE_FIELDS = {"account_id", "name", "status"}


class TestSuccessResponseExtras:
    """context echo + sandbox flag (account_id=None keeps these DB-free)."""

    def test_context_is_echoed(self):
        ctx = SimpleNamespace(buyer_ref="ref-1")
        extras = _success_response_extras(
            req=SimpleNamespace(context=ctx),
            sandbox_mode=SimpleNamespace(active=False),
            tenant_id="t1",
            account_id=None,
        )
        assert extras["context"] is ctx

    def test_sandbox_true_only_in_sandbox_mode(self):
        active = _success_response_extras(
            req=SimpleNamespace(context=None),
            sandbox_mode=SimpleNamespace(active=True),
            tenant_id="t1",
            account_id=None,
        )
        inactive = _success_response_extras(
            req=SimpleNamespace(context=None),
            sandbox_mode=SimpleNamespace(active=False),
            tenant_id="t1",
            account_id=None,
        )
        assert active["sandbox"] is True
        assert inactive["sandbox"] is None  # dropped by exclude_none → absent on the wire


class TestBuyerSafeAccount:
    """Account projection is buyer-safe by construction and never raises."""

    def _with_row(self, row):
        uow = MagicMock()
        uow.session = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = False
        repo = MagicMock()
        repo.return_value.get_by_id.return_value = row
        return (
            patch("src.core.database.repositories.MediaBuyUoW", return_value=cm),
            patch("src.core.database.repositories.account.AccountRepository", repo),
        )

    def test_redacts_seller_financials(self):
        # Row carries financials; the projection must expose ONLY the safe fields.
        row = SimpleNamespace(name="Acme Corp", status="active", rate_card="premium", credit_limit=999999)
        gds, repo = self._with_row(row)
        with gds, repo:
            account = _buyer_safe_account("t1", "acct_1")
        assert account is not None
        dumped = account.model_dump(exclude_none=True)
        assert dumped["account_id"] == "acct_1"
        assert dumped["name"] == "Acme Corp"
        leaked = set(dumped) - _BUYER_SAFE_FIELDS
        assert leaked == set(), f"buyer-safe account leaked non-allowlisted fields: {leaked}"

    def test_none_without_account_id(self):
        assert _buyer_safe_account("t1", None) is None

    def test_pending_provision_surfaces_as_pending_approval(self):
        # 'pending_provision' is an internal ORM status with no AdCP AccountStatus.
        # It must project to 'pending_approval' (#332) — the SAME wire status the
        # get_accounts flow emits — not be dropped from the response.
        row = SimpleNamespace(name="Acme", status="pending_provision")
        gds, repo = self._with_row(row)
        with gds, repo:
            account = _buyer_safe_account("t1", "acct_1")
        assert account is not None
        assert account.status == "pending_approval"

    def test_genuinely_unmappable_status_returns_none_without_raising(self):
        # A status with no spec enum AND no translation must be skipped (return
        # None), never raise into the buy flow — enrichment is not load-bearing.
        row = SimpleNamespace(name="Acme", status="some_future_internal_state")
        gds, repo = self._with_row(row)
        with gds, repo:
            assert _buyer_safe_account("t1", "acct_1") is None
