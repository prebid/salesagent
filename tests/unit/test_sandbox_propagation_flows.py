"""Flow-level oracles for sandbox isolation (SA-004).

`test_sandbox_account_isolation.py` proves ``get_adapter(sandbox=True)`` returns the mock
adapter. That is necessary but not sufficient: it cannot catch a call site that passes
``sandbox=False`` *from the wrong source* — which was the actual defect on the update,
performance, delivery and snapshot paths, where ``identity.sandbox`` is structurally
False because those operations carry no account reference.

These tests exercise the derivation instead of the destination: given a sandbox account
in the database, does each entry point end up choosing the sandbox adapter?

AdCP 3.1.1 ``dist/docs/3.1.1/media-buy/advanced-topics/sandbox.mdx`` §Seller
implementation: sandbox requests MUST NOT make real ad platform API calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.helpers.account_helpers import (
    _account_is_sandbox,
    partition_by_sandbox_mode,
)
from tests.helpers.sandbox_seam import bind_real_sandbox_seam


def _accounts_repo(modes: dict[str, bool | None]) -> MagicMock:
    """AccountRepository stub: account_id -> Account.sandbox value (None = unknown row)."""
    repo = MagicMock()

    def get_by_id(account_id: str):
        if account_id not in modes:
            return None  # account does not exist
        account = MagicMock()
        account.sandbox = modes[account_id]
        return account

    repo.get_by_id.side_effect = get_by_id
    return repo


def _media_buys_repo(buys: dict[str, str | None]) -> MagicMock:
    """MediaBuyRepository stub: media_buy_id -> account_id.

    Stubs BOTH accessors with one policy. ``sandbox_mode_by_id`` uses the raising
    variant — an id resolving to no row must not silently mean LIVE — so a stub that
    defined only ``get_by_id`` would hand the seam a MagicMock whose ``.account_id`` is
    itself a MagicMock, and the failure would look like a production defect rather than
    a stub gap.
    """
    repo = MagicMock()

    def get_by_id(media_buy_id: str):
        if media_buy_id not in buys:
            return None
        buy = MagicMock()
        buy.account_id = buys[media_buy_id]
        return buy

    def get_by_id_or_raise(media_buy_id: str, **_kwargs):
        buy = get_by_id(media_buy_id)
        if buy is None:
            from src.core.exceptions import AdCPMediaBuyNotFoundError

            raise AdCPMediaBuyNotFoundError(f"Media buy '{media_buy_id}' not found")
        return buy

    repo.get_by_id.side_effect = get_by_id
    repo.get_by_id_or_raise.side_effect = get_by_id_or_raise
    return repo


def _seam_for(accounts: MagicMock, buys: MagicMock) -> MagicMock:
    """A UoW stub carrying the REAL buy-keyed seam over the given repo stubs.

    Binds production's own methods rather than reimplementing the derivation, so
    these stay oracles for the seam and not for a copy of it.
    """
    uow = MagicMock()
    uow.accounts = accounts
    uow.media_buys = buys
    return bind_real_sandbox_seam(uow)


class TestSingleBuyDerivation:
    """update / performance / single-buy delivery are addressed by media_buy_id only."""

    def test_sandbox_buy_yields_sandbox_mode(self) -> None:
        accounts = _accounts_repo({"acc_sbx": True})
        buys = _media_buys_repo({"mb_1": "acc_sbx"})

        assert _seam_for(accounts, buys).sandbox_mode_by_id("mb_1") is True

    def test_live_buy_yields_live_mode(self) -> None:
        """Negative control: without it, 'always sandbox' would pass the test above."""
        accounts = _accounts_repo({"acc_live": False})
        buys = _media_buys_repo({"mb_2": "acc_live"})

        assert _seam_for(accounts, buys).sandbox_mode_by_id("mb_2") is False

    def test_null_sandbox_column_is_live(self) -> None:
        accounts = _accounts_repo({"acc_legacy": None})
        buys = _media_buys_repo({"mb_3": "acc_legacy"})

        assert _seam_for(accounts, buys).sandbox_mode_by_id("mb_3") is False

    def test_buy_with_no_account_is_live(self) -> None:
        """Legacy buys predate account references; live is their only meaningful mode."""
        accounts = _accounts_repo({})
        buys = _media_buys_repo({"mb_legacy": None})

        assert _seam_for(accounts, buys).sandbox_mode_by_id("mb_legacy") is False


class TestUnresolvedAccountRefusesDispatch:
    """SA-003: an unresolvable non-null account must never fall through to live."""

    def test_unresolved_account_raises_rather_than_returning_live(self) -> None:
        """The wire message matches the file's other ACCOUNT_NOT_FOUND raises.

        A buyer parsing ACCOUNT_NOT_FOUND on the wire cannot distinguish a
        site-specific phrasing from the family's; the seller-side "why" this refusal
        differs from a buyer-typo'd account_id is documented at the raise site and
        emitted at ERROR with a stack (an operator concern), not carried in the
        buyer-facing message.
        """
        from src.core.exceptions import AdCPAccountNotFoundError

        accounts = _accounts_repo({})  # the referenced account does not exist

        with pytest.raises(AdCPAccountNotFoundError) as exc_info:
            _account_is_sandbox(accounts, "acc_missing")

        assert str(exc_info.value) == "Account 'acc_missing' not found.", (
            f"expected the terse family-consistent message, got: {exc_info.value!r}"
        )
        assert exc_info.value.suggestion == "Check account reference, re-run sync_accounts.", (
            "the spec-canonical suggestion for ACCOUNT_NOT_FOUND must survive on this raise"
        )

    def test_unresolved_account_aborts_a_partition(self) -> None:
        """A mixed snapshot with one bad account fails loudly instead of half-dispatching."""
        from src.core.exceptions import AdCPAccountNotFoundError

        accounts = _accounts_repo({"acc_sbx": True})
        buys = [MagicMock(account_id="acc_sbx"), MagicMock(account_id="acc_gone")]

        with pytest.raises(AdCPAccountNotFoundError):
            partition_by_sandbox_mode(accounts, buys, lambda b: b.account_id)


class TestMultiBuyPartitioning:
    """get_media_buys snapshots and delivery reads can span both modes at once."""

    def test_mixed_request_splits_by_account_mode(self) -> None:
        accounts = _accounts_repo({"acc_sbx": True, "acc_live": False})
        sbx_buy = MagicMock(account_id="acc_sbx")
        live_buy = MagicMock(account_id="acc_live")

        sandbox_items, live_items = partition_by_sandbox_mode(accounts, [live_buy, sbx_buy], lambda b: b.account_id)

        assert sandbox_items == [sbx_buy]
        assert live_items == [live_buy]

    def test_all_live_leaves_the_sandbox_partition_empty(self) -> None:
        """An empty partition means its adapter is never constructed."""
        accounts = _accounts_repo({"acc_live": False})
        buys = [MagicMock(account_id="acc_live"), MagicMock(account_id="acc_live")]

        sandbox_items, live_items = partition_by_sandbox_mode(accounts, buys, lambda b: b.account_id)

        assert sandbox_items == []
        assert len(live_items) == 2

    def test_all_sandbox_leaves_the_live_partition_empty(self) -> None:
        accounts = _accounts_repo({"acc_sbx": True})
        buys = [MagicMock(account_id="acc_sbx"), MagicMock(account_id="acc_sbx")]

        sandbox_items, live_items = partition_by_sandbox_mode(accounts, buys, lambda b: b.account_id)

        assert live_items == []
        assert len(sandbox_items) == 2

    def test_modes_are_cached_per_account(self) -> None:
        """200 buys on one account must cost one lookup, not 200."""
        accounts = _accounts_repo({"acc_sbx": True})
        buys = [MagicMock(account_id="acc_sbx") for _ in range(50)]

        partition_by_sandbox_mode(accounts, buys, lambda b: b.account_id)

        assert accounts.get_by_id.call_count == 1, (
            f"resolved the same account {accounts.get_by_id.call_count} times; partitioning must cache per account id"
        )

    def test_partition_preserves_input_order_within_each_mode(self) -> None:
        """Merging is keyed by media_buy_id, but order within a partition must be stable."""
        accounts = _accounts_repo({"acc_live": False})
        first, second, third = (MagicMock(account_id="acc_live", name=f"b{i}") for i in range(3))

        _, live_items = partition_by_sandbox_mode(accounts, [first, second, third], lambda b: b.account_id)

        assert live_items == [first, second, third]
