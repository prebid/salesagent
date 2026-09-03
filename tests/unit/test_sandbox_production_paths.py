"""Production-path oracles for sandbox isolation (SA-004, second attempt).

The first attempt tested the three helpers in isolation, which the review correctly
rejected: replacing a production ``sandbox=`` argument with ``False`` left those tests
green. These drive the actual ``_impl`` functions and assert on the real ``get_adapter``
binding in each module — the call that decides whether a real ad platform is contacted.

Each test is mutation-checked: flipping the production argument it grades makes it fail.

AdCP 3.1.1 ``dist/docs/3.1.1/media-buy/advanced-topics/sandbox.mdx`` §Seller
implementation: sandbox requests MUST NOT make real ad platform API calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.helpers.sandbox_assertions import assert_all_live, assert_all_sandbox
from tests.helpers.sandbox_assertions import sandbox_modes as _sandbox_kwargs
from tests.helpers.sandbox_seam import bind_real_sandbox_seam


def _account(sandbox: bool) -> MagicMock:
    account = MagicMock()
    account.sandbox = sandbox
    return account


def _buy(media_buy_id: str, account_id: str | None) -> MagicMock:
    buy = MagicMock()
    buy.media_buy_id = media_buy_id
    buy.account_id = account_id
    return buy


def _raise_not_found(media_buy_id: str):
    from src.core.exceptions import AdCPMediaBuyNotFoundError

    raise AdCPMediaBuyNotFoundError(f"Media buy '{media_buy_id}' not found")


def _uow_with(accounts: dict[str, bool], buys: dict[str, str | None]) -> MagicMock:
    """A UoW stub whose accounts/media_buys repos answer from the given maps."""
    uow = MagicMock()
    uow.accounts.get_by_id.side_effect = lambda aid: _account(accounts[aid]) if aid in accounts else None
    uow.media_buys.get_by_id.side_effect = lambda mid: _buy(mid, buys[mid]) if mid in buys else None
    # sandbox_mode_by_id uses the RAISING accessor: an id resolving to no row must not
    # silently mean LIVE. Stubbed from the same map so the two cannot diverge.
    uow.media_buys.get_by_id_or_raise.side_effect = lambda mid, **_kw: (
        _buy(mid, buys[mid]) if mid in buys else _raise_not_found(mid)
    )
    bind_real_sandbox_seam(uow)
    uow.__enter__.return_value = uow
    uow.__exit__.return_value = False
    return uow


class TestPerformancePath:
    """update_performance_index is addressed by media_buy_id only."""

    def _run_capturing(self, *, account_sandbox: bool):
        from src.core.tools import performance as perf

        req = MagicMock()
        req.media_buy_id = "mb_1"
        req.performance_data = []
        identity = MagicMock()
        identity.tenant_id = "t1"
        identity.sandbox = False  # the wrong source — must not be what decides

        uow = _uow_with({"acc_1": account_sandbox}, {"mb_1": "acc_1"})

        with (
            patch.object(perf, "MediaBuyUoW", return_value=uow),
            patch.object(perf, "get_adapter") as mock_get_adapter,
            patch.object(perf, "require_identity", return_value=identity),
            patch.object(perf, "require_tenant", return_value={"tenant_id": "t1"}),
            patch.object(perf, "require_principal_id", return_value="p1"),
            patch.object(perf, "resolve_principal_or_raise", return_value=MagicMock()),
            patch.object(perf, "_verify_principal"),
        ):
            mock_get_adapter.return_value.update_media_buy_performance_index.return_value = True
            try:
                perf._update_performance_index_impl(req, identity=identity)
            except Exception:  # noqa: BLE001 - response shaping is not what this grades
                pass

        return mock_get_adapter

    def test_sandbox_buy_selects_sandbox_adapter(self) -> None:
        assert_all_sandbox(self._run_capturing(account_sandbox=True), context="update_performance_index")

    def test_live_buy_selects_live_adapter(self) -> None:
        """Negative control — 'always sandbox' would pass the test above."""
        assert_all_live(self._run_capturing(account_sandbox=False), context="update_performance_index")


class TestSnapshotPartitioning:
    """get_media_buys snapshots can span both modes in one response."""

    def _run(self, buys_by_account: dict[str, str]) -> tuple[list[bool], MagicMock]:
        from src.core.helpers import adapter_helpers
        from src.core.tools import media_buy_list as mbl

        accounts = {"acc_sbx": True, "acc_live": False}
        target = [_buy(mb_id, acc) for mb_id, acc in buys_by_account.items()]

        uow = _uow_with(accounts, {b.media_buy_id: b.account_id for b in target})
        uow.session = MagicMock()

        with (
            patch.object(mbl, "MediaBuyUoW", return_value=uow),
            # mbl calls adapter_for_mode, which resolves get_adapter through
            # adapter_helpers' own module namespace — patch at that source, not mbl
            # (which no longer imports get_adapter directly).
            patch.object(adapter_helpers, "get_adapter") as mock_get_adapter,
            patch.object(mbl, "_fetch_target_media_buys", return_value=target),
            patch.object(mbl, "_fetch_creative_approvals", return_value={}),
            patch.object(mbl, "_fetch_packages", return_value={}),
        ):
            adapter = mock_get_adapter.return_value
            adapter.capabilities.supports_realtime_reporting = True
            adapter.get_packages_snapshot.return_value = {}

            req = MagicMock()
            req.account = None
            req.account_id = None
            identity = MagicMock()
            identity.tenant_id = "t1"
            identity.principal_id = "p1"
            identity.sandbox = False
            identity.testing_context = None

            with (
                patch.object(mbl, "require_identity", return_value=identity),
                patch.object(mbl, "require_tenant", return_value={"tenant_id": "t1"}),
                patch.object(mbl, "get_principal_object", return_value=MagicMock()),
            ):
                try:
                    mbl._get_media_buys_impl(req, identity=identity, include_snapshot=True)
                except Exception:  # noqa: BLE001 - response shaping is not what this grades
                    pass

        return _sandbox_kwargs(mock_get_adapter), mock_get_adapter

    def test_all_live_builds_only_a_live_adapter(self) -> None:
        modes, _ = self._run({"mb_1": "acc_live", "mb_2": "acc_live"})
        assert modes == [False], f"expected one live adapter, got {modes}"

    def test_all_sandbox_builds_only_a_sandbox_adapter(self) -> None:
        modes, _ = self._run({"mb_1": "acc_sbx"})
        assert modes == [True], (
            f"expected one sandbox adapter, got {modes}; a live adapter here would read "
            "sandbox buys through the tenant's real ad server"
        )

    def test_mixed_request_builds_one_adapter_per_mode(self) -> None:
        modes, _ = self._run({"mb_live": "acc_live", "mb_sbx": "acc_sbx"})
        assert sorted(modes) == [False, True], f"expected one adapter per mode, got {modes}"


class TestUpdateMediaBuyPath:
    """update_media_buy mutates a buy addressed by id — the highest-risk buy-keyed path.

    Driven through MediaBuyUpdateEnv rather than hand-rolled mocks: the harness already
    patches the whole dependency set, so the impl actually reaches get_adapter. A
    hand-mocked attempt reached zero calls and would have asserted vacuously.
    """

    def _adapter_modes(self, *, account_sandbox: bool) -> MagicMock:
        from tests.harness.media_buy_update import MediaBuyUpdateEnv

        with MediaBuyUpdateEnv() as env:
            env.set_media_buy(media_buy_id="mb-001", account_id="acc-001")
            env.set_account(account_id="acc-001", sandbox=account_sandbox)
            env.set_currency_limit()

            try:
                # A package update is what actually reaches adapter dispatch.
                env.call_impl(media_buy_id="mb-001", packages=[{"package_id": "pkg-1", "budget": 50.0}])
            except Exception:  # noqa: BLE001 - response shaping is not what this grades
                pass

            return env.mock["adapter"]

    def test_sandbox_buy_selects_sandbox_adapter(self) -> None:
        assert_all_sandbox(self._adapter_modes(account_sandbox=True), context="update_media_buy")

    def test_live_buy_selects_live_adapter(self) -> None:
        """Negative control — 'always sandbox' would silently disable real updates."""
        assert_all_live(self._adapter_modes(account_sandbox=False), context="update_media_buy")


# Deferred creative push is graded at its own adapter boundary in
# tests/unit/test_push_creative_to_existing_buy.py::TestSandboxAdapterSelection, and
# approved-buy execution in tests/integration/test_sandbox_approved_execution.py — both
# mutation-verified. One path remains graded only by its derivation:
#
#   - admin media-buy detail: read-only, and the lowest risk of the set.

#
# The executor was closed by dropping mock-sequence fixtures entirely: real rows produce
# actionable validation errors ("packages.0.budget: Required field is missing") instead of
# opaque side_effect exhaustion, which is what made it tractable after two failed attempts.


# Delivery account scoping (SA-006) is graded in
# tests/integration/test_sandbox_delivery_account_scoping.py: _get_media_buy_delivery_impl
# cannot be driven far enough with unit mocks (it raises before the seam), and an
# assertion on an empty call list passes vacuously — which is how the first attempt at
# this test read green while grading nothing.


# A former oracle here asserted hasattr(module, "<helper name>") for each buy-keyed
# module. It graded import PRESENCE, not use — a module could import the helper and
# still pass identity.sandbox — and it broke the moment the derivation moved behind
# MediaBuyUoW.sandbox_mode*, which is the correct shape. The invariant it reached for
# is carried by test_architecture_get_adapter_sandbox.py (every call site decides
# explicitly; buy-keyed modules may not source it from identity) plus the behavioural
# pairs above, both of which fail on real regressions rather than on import churn.
