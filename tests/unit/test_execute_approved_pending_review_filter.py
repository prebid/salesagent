"""Unit test: a creative that is not cleared to serve holds the buy.

``execute_approved_media_buy`` now owns the creative gate (it moved out of the three
admin routes, which had three disagreeing copies of it). The gate asks
``CreativeAssignmentRepository.unapproved_creative_ids``, which treats only
``approved`` and ``active`` as cleared — so a ``pending_review`` creative holds the
buy at ``PENDING_CREATIVES`` and the ad server is never contacted at all.

THE SUBJECT CHANGED, and the reader should know why. This test used to assert that a
``pending_review`` creative was merely skipped from the adapter's asset list while the
buy went ahead (prebid#1038). That skip lives further down the same function, past the
adapter call — and it is now unreachable from here, because the gate returns first.
Asserting "the upload did not happen" against the current code would pass no matter
what: nothing downstream of the gate runs. So the assertion below is the gate's own
outcome plus the ad-server boundary, which is what actually decides it.
"""

from unittest.mock import MagicMock, patch

from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.tools.media_buy_create import ApprovalOutcome

_MODULE = "src.core.tools.media_buy_create"


class TestExecuteApprovedPendingReviewFilter:
    """A pending_review creative holds the buy before any adapter call."""

    def test_pending_review_creative_holds_the_buy(self):
        """The gate names the creative, and nothing downstream of it runs."""
        from src.core.tools.media_buy_create import execute_approved_media_buy

        # The gate returns inside the FIRST unit of work, so only one is opened.
        uow1 = MagicMock()
        uow1.__enter__ = MagicMock(return_value=uow1)
        uow1.__exit__ = MagicMock(return_value=False)
        uow1.session = MagicMock()

        tenant = MagicMock()
        tenant.tenant_id = "t1"
        tenant.ad_server = "mock"

        media_buy = MagicMock()
        media_buy.media_buy_id = "mb_1"
        media_buy.tenant_id = "t1"
        media_buy.principal_id = "p1"
        media_buy.status = "pending_approval"

        assignment = MagicMock()
        assignment.creative_id = "cre_pending"
        assignment.package_id = "pkg_1"

        pending_creative = MagicMock()
        pending_creative.creative_id = "cre_pending"
        pending_creative.status = "pending_review"

        # The REAL gate runs over these results — ``unapproved_creative_ids`` is the
        # code under test here, so it is deliberately not mocked. Its two queries are
        # the assignments for the buy, then the creatives those assignments name.
        session = uow1.session
        session.scalars.return_value.first.side_effect = [tenant, media_buy]
        session.scalars.return_value.all.side_effect = [[assignment], [pending_creative]]

        mock_adapter = MagicMock()

        with (
            patch("src.core.database.repositories.MediaBuyUoW", return_value=uow1),
            patch("src.core.config_loader.get_tenant_by_id", return_value={"tenant_id": "t1"}),
            patch(f"{_MODULE}.get_adapter", return_value=mock_adapter),
            patch(f"{_MODULE}._execute_adapter_media_buy_creation") as adapter_boundary,
            # The held write is a different obligation (the integration tests grade the
            # persisted row); stubbing it keeps this test on the gate's decision.
            patch.object(MediaBuyRepository, "update_status"),
        ):
            result = execute_approved_media_buy(
                "mb_1",
                "t1",
                approved_by="approver@example.com",
                approved_at=None,
            )

        assert result.outcome is ApprovalOutcome.HELD_PENDING_CREATIVES, (
            f"a pending_review creative must hold the buy, got {result}"
        )
        assert "cre_pending" in (result.error_msg or ""), (
            f"the held result must name the creative that is holding it: {result.error_msg!r}"
        )
        # The whole point of holding: nothing is created in the ad server. Asserting on
        # the BOUNDARY rather than on the creative upload is what makes this real — the
        # upload is downstream of a call that never happens.
        adapter_boundary.assert_not_called()
        mock_adapter.creatives_manager.add_creative_assets.assert_not_called()


class TestPersistAdapterPackageIds:
    """_persist_adapter_package_ids must not overwrite mismatched platform_order_id."""

    def test_refuses_to_overwrite_mismatched_platform_order_id(self):
        from src.core.tools.media_buy_create import _persist_adapter_package_ids

        pkg = MagicMock()
        pkg.package_id = "pkg_1"
        pkg.package_config = {"platform_order_id": "existing_gam_order"}

        repo = MagicMock()
        repo.get_packages.return_value = [pkg]

        _persist_adapter_package_ids(
            repo,
            media_buy_id="mb_1",
            platform_order_id="new_gam_order",
            log_label="TEST",
        )

        assert pkg.package_config["platform_order_id"] == "existing_gam_order"

    def test_writes_platform_order_id_when_unset(self):
        from src.core.tools.media_buy_create import _persist_adapter_package_ids

        pkg = MagicMock()
        pkg.package_id = "pkg_1"
        pkg.package_config = {}

        repo = MagicMock()
        repo.get_packages.return_value = [pkg]

        _persist_adapter_package_ids(
            repo,
            media_buy_id="mb_1",
            platform_order_id="gam_order_1",
        )

        assert pkg.package_config["platform_order_id"] == "gam_order_1"

    def test_refuses_to_overwrite_mismatched_platform_line_item_id(self):
        from src.core.tools.media_buy_create import _persist_adapter_package_ids

        pkg = MagicMock()
        pkg.package_id = "pkg_1"
        pkg.package_config = {"platform_line_item_id": "existing_li"}

        repo = MagicMock()
        repo.get_packages.return_value = [pkg]

        _persist_adapter_package_ids(
            repo,
            media_buy_id="mb_1",
            platform_order_id="gam_order_1",
            platform_line_item_ids={"pkg_1": "new_li"},
        )

        assert pkg.package_config["platform_line_item_id"] == "existing_li"
