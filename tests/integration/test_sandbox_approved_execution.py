"""SA-013: approving a sandbox media buy must not dispatch to a real ad platform.

`execute_approved_media_buy` runs after an operator approves a pending buy — long after
the request identity is gone — so it must re-derive sandbox mode from the buy's own
account. A regression passing `sandbox=False` here would create a real order, upload real
creatives, and approve real objects on the tenant's ad server.

Written as an integration test on purpose. Two attempts to drive this path with unit mocks
failed on `session.scalars(...).first().side_effect` ordering: the sandbox derivation adds
an account lookup to the same mock chain, so the sequence either exhausts ("Adapter
creation failed: " with an empty message) or steals a slot package reconstruction needed
("Failed to reconstruct package pkg_1: "). Real rows remove the ordering problem entirely.

Per-site mutation matrix (each site mutated ALONE — a blanket mutation of all sites only
proves the first assertion fires, which is how an earlier version of this test overstated
its reach). Every site is named by its enclosing function in ``media_buy_create.py``, line
number second: line numbers move with each merge, so a bare number goes silently wrong,
while an anchor carrying the symbol degrades into "search this function" instead.

    _execute_adapter_media_buy_creation  helper's own get_adapter      (:583)   CAUGHT
    execute_approved_media_buy           executor -> helper forwarding (:1175)  CAUGHT
    execute_approved_media_buy           creative upload               (:1302)  CAUGHT
    execute_approved_media_buy           final order approval          (:1361)  CAUGHT

An earlier version read the last two as "branch not reached". That was wrong: they were
reached, but ``execute_approved_media_buy`` re-imports ``get_adapter`` from
``adapter_helpers`` at call time (in that same function, media_buy_create.py:1226), so
those selections went to an unpatched binding the test could not see. Both bindings are
patched now, and reachability is asserted rather than assumed — add_creative_assets and
approve_order must have been called, and both mocks must be non-empty.

AdCP 3.1.1 ``dist/docs/3.1.1/media-buy/advanced-topics/sandbox.mdx`` §Seller
implementation: sandbox requests MUST NOT make real ad platform API calls.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tests.harness._base import IntegrationEnv
from tests.helpers.sandbox_assertions import assert_all_live, assert_all_sandbox

pytestmark = pytest.mark.requires_db

_MODULE = "src.core.tools.media_buy_create"
_HELPERS = "src.core.helpers.adapter_helpers"


class _ExecEnv(IntegrationEnv):
    """Bare integration env: binds factory sessions, patches nothing.

    The executor is called directly, so no transport patches are wanted — only the real
    database the factories write into.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}


def _seed_approved_buy(*, tenant_id: str, sandbox: bool, buy_id: str = "mb_exec"):
    """A pending_approval buy whose raw_request reconstructs, owned by a sandbox/live account."""
    from tests.factories import (
        AccountFactory,
        AgentAccountAccessFactory,
        CreativeAssignmentFactory,
        CreativeFactory,
        MediaBuyFactory,
        MediaPackageFactory,
        PricingOptionFactory,
        PrincipalFactory,
        ProductFactory,
        TenantFactory,
    )

    tenant = TenantFactory(tenant_id=tenant_id, ad_server="mock")
    principal = PrincipalFactory(tenant=tenant, principal_id=f"p_{tenant_id}")

    account = AccountFactory(tenant=tenant, account_id=f"acc_{tenant_id}", sandbox=sandbox)
    AgentAccountAccessFactory(
        tenant_id=tenant.tenant_id, principal_id=principal.principal_id, account_id=account.account_id
    )

    product = ProductFactory(tenant=tenant, product_id="prod_exec")
    PricingOptionFactory(product=product, pricing_model="cpm", currency="USD", rate=Decimal("10.00"))

    buy = MediaBuyFactory(
        media_buy_id=buy_id,
        tenant=tenant,
        principal=principal,
        account_id=account.account_id,
        status="pending_approval",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        end_time=datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
        raw_request={
            "brand": {"domain": "acme-exec.com"},
            "start_time": "2026-01-01T00:00:00Z",
            "end_time": "2026-12-31T23:59:59Z",
            "packages": [
                {
                    "package_id": "pkg_exec",
                    "product_id": "prod_exec",
                    "pricing_option_id": "po_exec",
                    "budget": 1000.0,
                }
            ],
        },
    )
    # An APPROVED creative assigned to the package, so the creative-upload branch runs
    # (a pending_review creative is filtered out before the adapter is touched).
    creative = CreativeFactory(
        tenant=tenant,
        principal=principal,
        approved=True,
        # Root-level url/width/height is the supported simple-creative shape; without
        # dimensions and a content URL the executor rejects the buy before any upload.
        data={"url": "https://cdn.example.com/banner.jpg", "width": 300, "height": 250},
    )
    CreativeAssignmentFactory(creative=creative, media_buy_id=buy_id, package_id="pkg_exec")

    # The executor reads persisted packages, not only raw_request.
    MediaPackageFactory(
        media_buy=buy,
        package_id="pkg_exec",
        package_config={"product_id": "prod_exec", "pricing_option_id": "po_exec"},
    )
    return buy


def _run_executor(*, tenant_id: str, sandbox: bool):
    """Run the approval executor for real; return its TWO get_adapter mocks.

    ``_execute_adapter_media_buy_creation`` is deliberately NOT patched: patching it grades
    only the first forwarding hop (executor -> helper) and leaves the helper's own
    get_adapter, the creative upload, and the final order approval unproven.

    Adapter selections land on two DISTINCT bindings, so both are patched and both returned:

    - ``mock_module_binding`` — ``media_buy_create.get_adapter`` (module-global import).
      Records the media-buy creation selection.
    - ``mock_lazy_binding`` — ``adapter_helpers.get_adapter``, which the executor re-imports
      at call time (inside ``execute_approved_media_buy``, media_buy_create.py:1226).
      Records the creative-upload and final-approval selections, which the module-global
      patch cannot observe at all.

    Callers assert over both. Asserting over either alone leaves half the sites ungraded —
    which is precisely how an earlier version of this test mis-read two live call sites as
    "branch not reached".

    Returns:
        tuple[MagicMock, MagicMock]: (module-global binding, lazy-import binding).
    """
    from src.core.schemas import CreateMediaBuySuccess
    from src.core.tools.media_buy_create import execute_approved_media_buy

    adapter = MagicMock()
    adapter.create_media_buy.return_value = CreateMediaBuySuccess.carrier(media_buy_id="gam_order_1", packages=[])
    adapter.creatives_manager.add_creative_assets.return_value = []
    adapter.orders_manager.approve_order.return_value = True

    with _ExecEnv(tenant_id=tenant_id) as env:
        buy = _seed_approved_buy(tenant_id=tenant_id, sandbox=sandbox)
        env._commit_factory_data()

        # execute_approved_media_buy re-imports get_adapter from adapter_helpers at call
        # time (inside that function, media_buy_create.py:1226), so the creative-upload
        # and approval selections bypass the module-global binding entirely. Patch BOTH or
        # those two sites are invisible — which is exactly why an earlier matrix read them
        # as "branch not reached" when they were in fact reached through an unpatched
        # adapter.
        with (
            patch(f"{_MODULE}.get_adapter", return_value=adapter) as mock_module_binding,
            patch(f"{_HELPERS}.get_adapter", return_value=adapter) as mock_lazy_binding,
        ):
            approval = execute_approved_media_buy(buy.media_buy_id, tenant_id)

    # Reachability, asserted rather than printed: every branch whose adapter selection this
    # test claims to grade must actually have run.
    assert adapter.create_media_buy.called, (
        f"never reached the adapter's create_media_buy (outcome={approval.outcome}, error={approval.error_msg})"
    )
    assert adapter.creatives_manager.add_creative_assets.called, (
        f"never reached the creative-upload branch (outcome={approval.outcome}, error={approval.error_msg}); "
        "its adapter selection would be ungraded"
    )
    assert adapter.orders_manager.approve_order.called, (
        f"never reached the final order-approval branch (outcome={approval.outcome}, error={approval.error_msg}); "
        "its adapter selection would be ungraded"
    )
    assert mock_module_binding.call_args_list, "module-global get_adapter was never used"
    assert mock_lazy_binding.call_args_list, (
        "the lazily-imported get_adapter was never used — the upload/approval selections are not being recorded"
    )
    return mock_module_binding, mock_lazy_binding


class TestApprovedExecutionSandboxDispatch:
    """Grades EVERY adapter the approval run selects: creation, creative upload, approval."""

    def test_approving_a_sandbox_buy_uses_sandbox_adapters_throughout(self, integration_db):
        module_binding, lazy_binding = _run_executor(tenant_id="t_exec_sbx", sandbox=True)

        assert_all_sandbox(module_binding, context="approved-buy execution (creation)")
        assert_all_sandbox(lazy_binding, context="approved-buy execution (upload/approval)")

    def test_approving_a_live_buy_uses_live_adapters_throughout(self, integration_db):
        """Negative control — 'always sandbox' would silently stop real approvals."""
        module_binding, lazy_binding = _run_executor(tenant_id="t_exec_live", sandbox=False)

        assert_all_live(module_binding, context="approved-buy execution (creation)")
        assert_all_live(lazy_binding, context="approved-buy execution (upload/approval)")
