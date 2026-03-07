"""Behavioral tests for _update_media_buy_impl.

HIGH_RISK tests covering core impl flows that are most vulnerable
to breakage during FastAPI migration. Each test traces a BDD scenario
from BR-UC-003 through the impl layer.

BDD scenario cross-references:
- T-UC-003-ext-a-not-found: test_principal_not_found_returns_error
- T-UC-003-combined-update: test_combined_campaign_and_package_update
- T-UC-003-multi-package: test_multi_package_update_processes_all_packages
- T-UC-003-buyer-ref (positive): test_buyer_ref_positive_resolution
- T-UC-003-alt-timing + T-UC-003-ext-e: test_flight_date_validation_and_persistence
- T-UC-003-alt-budget + T-UC-003-ext-d + T-UC-003-rule-008: test_campaign_budget_validation_and_persistence
- T-UC-003-alt-manual: test_manual_approval_path_through_impl
- T-UC-003-ext-l (impl-level): test_package_not_found_returns_error
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch

import pytest
from pydantic import ValidationError

from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    Budget,
    UpdateMediaBuyError,
    UpdateMediaBuyRequest,
    UpdateMediaBuySuccess,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_update import _update_media_buy_impl

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MODULE = "src.core.tools.media_buy_update"
DB_MODULE = "src.core.database.database_session"


def _make_identity(
    principal_id: str | None = "principal_test",
    tenant_id: str = "tenant_test",
    dry_run: bool = False,
) -> ResolvedIdentity:
    """Create a ResolvedIdentity for tests."""
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id, "name": "Test"},
        protocol="mcp",
        testing_context=AdCPTestContext(dry_run=dry_run),
    )


def _make_mock_db_session():
    """Create a mock DB session with context manager support."""
    mock_session = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = Mock(return_value=mock_session)
    mock_cm.__exit__ = Mock(return_value=False)
    return mock_session, mock_cm


def _make_mock_media_buy(media_buy_id="mb_test", currency="USD"):
    """Create a mock MediaBuy database object."""
    mb = MagicMock()
    mb.media_buy_id = media_buy_id
    mb.currency = currency
    mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
    mb.end_time = datetime(2025, 12, 31, tzinfo=UTC)
    return mb


def _make_mock_currency_limit(max_daily=None):
    """Create a mock CurrencyLimit with proper numeric values."""
    cl = MagicMock()
    cl.max_daily_package_spend = Decimal(str(max_daily)) if max_daily else None
    return cl


@pytest.fixture
def standard_mocks():
    """Context manager that patches all common dependencies for _update_media_buy_impl.

    Patches MediaBuyUoW to provide a mock session and repository,
    and patches all other common dependencies.

    Yields a dict of mock objects keyed by short name.
    """
    mock_session, mock_cm = _make_mock_db_session()

    # Build a mock UoW that provides session and media_buys repo
    mock_uow = MagicMock()
    mock_uow.session = mock_session
    mock_uow.media_buys = MagicMock()
    mock_uow.__enter__ = Mock(return_value=mock_uow)
    mock_uow.__exit__ = Mock(return_value=False)

    with (
        patch("src.core.helpers.context_helpers.ensure_tenant_context") as m_tenant,
        patch(f"{MODULE}.get_principal_object") as m_principal_obj,
        patch(f"{MODULE}._verify_principal") as m_verify,
        patch(f"{MODULE}.get_context_manager") as m_ctx_mgr,
        patch(f"{MODULE}.get_adapter") as m_adapter,
        patch(f"{MODULE}.get_audit_logger") as m_audit,
        patch(f"{MODULE}.MediaBuyUoW") as m_uow,
        patch(f"{DB_MODULE}.get_db_session") as m_db,
    ):
        # Standard setup: authenticated principal, test tenant, no dry_run
        m_tenant.return_value = {"tenant_id": "tenant_test", "name": "Test"}
        m_principal_obj.return_value = MagicMock(
            principal_id="principal_test",
            name="Test Principal",
            platform_mappings={},
        )

        # UoW mock
        m_uow.return_value = mock_uow

        # Context manager with workflow step
        mock_step = MagicMock()
        mock_step.step_id = "step_001"
        mock_ctx_mgr_instance = MagicMock()
        mock_ctx_mgr_instance.get_or_create_context.return_value = MagicMock(context_id="ctx_001")
        mock_ctx_mgr_instance.create_workflow_step.return_value = mock_step
        m_ctx_mgr.return_value = mock_ctx_mgr_instance

        # Adapter: no manual approval, standard config
        mock_adapter_instance = MagicMock()
        mock_adapter_instance.manual_approval_required = False
        mock_adapter_instance.manual_approval_operations = []
        m_adapter.return_value = mock_adapter_instance

        # Audit logger
        m_audit.return_value = MagicMock()

        # DB session: return the shared mock context manager
        m_db.return_value = mock_cm

        yield {
            "tenant": m_tenant,
            "principal_obj": m_principal_obj,
            "verify_principal": m_verify,
            "ctx_mgr": m_ctx_mgr,
            "ctx_mgr_instance": mock_ctx_mgr_instance,
            "adapter": m_adapter,
            "adapter_instance": mock_adapter_instance,
            "audit": m_audit,
            "uow": m_uow,
            "uow_instance": mock_uow,
            "db": m_db,
            "db_session": mock_session,
            "step": mock_step,
        }


def _setup_db_session(standard_mocks):
    """Create a fresh DB session mock and wire it into the fixture.

    Also updates the UoW mock's session to use the new mock session.
    Returns the mock_session for further configuration.
    """
    mock_session, mock_cm = _make_mock_db_session()
    standard_mocks["db"].return_value = mock_cm
    standard_mocks["db_session"] = mock_session
    # Keep UoW session in sync
    standard_mocks["uow_instance"].session = mock_session
    return mock_session


# ---------------------------------------------------------------------------
# HIGH_RISK Test 1: Principal not found
# BDD: T-UC-003-ext-a-not-found
# ---------------------------------------------------------------------------


def test_principal_not_found_returns_error(standard_mocks):
    """When auth resolves to a non-existent principal, impl returns
    UpdateMediaBuyError with code='principal_not_found'."""
    # Principal ID resolves but the object doesn't exist in DB
    standard_mocks["principal_obj"].return_value = None

    identity = _make_identity()
    req = UpdateMediaBuyRequest(media_buy_id="mb_001")
    result = _update_media_buy_impl(req=req, identity=identity)

    assert isinstance(result, UpdateMediaBuyError)
    assert len(result.errors) == 1
    assert result.errors[0].code == "principal_not_found"
    assert "principal_test" in result.errors[0].message

    # Workflow step should be marked failed
    standard_mocks["ctx_mgr_instance"].update_workflow_step.assert_called_once()
    call_kwargs = standard_mocks["ctx_mgr_instance"].update_workflow_step.call_args
    assert call_kwargs[1]["status"] == "failed" or call_kwargs[0][1] == "failed"


# ---------------------------------------------------------------------------
# HIGH_RISK Test 2: Combined campaign + package update
# BDD: T-UC-003-combined-update
# ---------------------------------------------------------------------------


def test_combined_campaign_and_package_update(standard_mocks):
    """When both total_budget and packages with budget provided,
    both are applied; response has affected_packages for all packages."""
    # Adapter returns success for package budget update
    standard_mocks["adapter_instance"].update_media_buy.return_value = UpdateMediaBuySuccess(
        media_buy_id="mb_combined",
        buyer_ref="",
        affected_packages=[],
    )

    mock_session = _setup_db_session(standard_mocks)

    # Set up DB return values for the currency validation path:
    # 1. uow.media_buys.get_by_id() -> media_buy (for currency check)
    # 2. session.scalars().first() -> currency_limit (for daily spend check)
    # 3. uow.media_buys.update_fields() -> updated media buy (for budget update)
    # 4. uow.media_buys.get_packages() -> packages (for affected tracking)
    mock_media_buy = _make_mock_media_buy("mb_combined")
    mock_currency_limit = _make_mock_currency_limit(max_daily=100000)

    # Configure repo mock for media buy lookups and writes
    standard_mocks["uow_instance"].media_buys.get_by_id.return_value = mock_media_buy
    standard_mocks["uow_instance"].media_buys.update_fields.return_value = mock_media_buy

    # Mock packages for campaign-level budget affected tracking
    mock_pkg_a = MagicMock()
    mock_pkg_a.package_id = "pkg_A"
    mock_pkg_b = MagicMock()
    mock_pkg_b.package_id = "pkg_B"
    standard_mocks["uow_instance"].media_buys.get_packages.return_value = [mock_pkg_a, mock_pkg_b]

    # Session scalars for currency limit lookup
    mock_scalars = MagicMock()
    mock_scalars.first.side_effect = [mock_currency_limit]
    mock_session.scalars.return_value = mock_scalars

    identity = _make_identity()
    req = UpdateMediaBuyRequest(
        media_buy_id="mb_combined",
        budget=Budget(total=5000.0, currency="USD", pacing="even"),
        packages=[{"package_id": "pkg_A", "budget": 2500.0}],
    )
    result = _update_media_buy_impl(req=req, identity=identity)

    assert isinstance(result, UpdateMediaBuySuccess)
    assert result.media_buy_id == "mb_combined"
    # affected_packages should contain entries from both package-level and campaign-level updates
    # Package budget update -> 1 entry for pkg_A
    # Campaign budget update -> entries for pkg_A, pkg_B
    assert len(result.affected_packages) >= 2
    affected_pkg_ids = {ap.package_id for ap in result.affected_packages}
    assert "pkg_A" in affected_pkg_ids
    assert "pkg_B" in affected_pkg_ids
    # The adapter should have been called for package budget update
    standard_mocks["adapter_instance"].update_media_buy.assert_called_once()


# ---------------------------------------------------------------------------
# HIGH_RISK Test 3: Multi-package update
# BDD: T-UC-003-multi-package
# ---------------------------------------------------------------------------


def test_multi_package_update_processes_all_packages(standard_mocks):
    """When packages contains 3 items with budget updates,
    all 3 are processed and appear in affected_packages."""
    # Adapter returns success for each update_package_budget call
    standard_mocks["adapter_instance"].update_media_buy.return_value = UpdateMediaBuySuccess(
        media_buy_id="mb_multi",
        buyer_ref="",
        affected_packages=[],
    )

    mock_session = _setup_db_session(standard_mocks)

    # Currency validation: media_buy via repo, currency_limit via session
    mock_media_buy = _make_mock_media_buy("mb_multi")
    mock_currency_limit = _make_mock_currency_limit(max_daily=100000)
    standard_mocks["uow_instance"].media_buys.get_by_id.return_value = mock_media_buy
    mock_scalars = MagicMock()
    mock_scalars.first.side_effect = [mock_currency_limit]
    mock_session.scalars.return_value = mock_scalars

    identity = _make_identity()
    req = UpdateMediaBuyRequest(
        media_buy_id="mb_multi",
        packages=[
            {"package_id": "pkg_1", "budget": 1000.0},
            {"package_id": "pkg_2", "budget": 2000.0},
            {"package_id": "pkg_3", "budget": 3000.0},
        ],
    )
    result = _update_media_buy_impl(req=req, identity=identity)

    assert isinstance(result, UpdateMediaBuySuccess)
    # All 3 packages should appear in affected_packages
    assert len(result.affected_packages) == 3
    affected_pkg_ids = {ap.package_id for ap in result.affected_packages}
    assert affected_pkg_ids == {"pkg_1", "pkg_2", "pkg_3"}

    # Adapter should have been called 3 times (once per package)
    assert standard_mocks["adapter_instance"].update_media_buy.call_count == 3


# ---------------------------------------------------------------------------
# HIGH_RISK Test 4: Buyer_ref positive resolution
# BDD: T-UC-003-buyer-ref (positive path)
# ---------------------------------------------------------------------------


def test_buyer_ref_positive_resolution(standard_mocks):
    """When buyer_ref provided, DB lookup resolves to correct media_buy_id,
    and update proceeds successfully."""
    _setup_db_session(standard_mocks)

    # The buyer_ref lookup returns a media buy with the resolved ID
    mock_media_buy = MagicMock()
    mock_media_buy.media_buy_id = "mb_resolved_123"
    standard_mocks["uow_instance"].media_buys.get_by_buyer_ref.return_value = mock_media_buy

    identity = _make_identity()
    # Use buyer_ref instead of media_buy_id (no packages, no budget = empty update)
    req = UpdateMediaBuyRequest(buyer_ref="buyer_ref_abc")
    result = _update_media_buy_impl(req=req, identity=identity)

    assert isinstance(result, UpdateMediaBuySuccess)
    # The resolved media_buy_id should be used in the response
    assert result.media_buy_id == "mb_resolved_123"

    # _verify_principal should have been called with the resolved ID and identity
    standard_mocks["verify_principal"].assert_called_once_with(
        "mb_resolved_123", identity, standard_mocks["uow_instance"].media_buys
    )


# ---------------------------------------------------------------------------
# HIGH_RISK Test 5: Main flow - package budget update
# BDD: T-UC-003-main
# ---------------------------------------------------------------------------


def test_main_flow_package_budget_update(standard_mocks):
    """When package budget change through impl, returns UpdateMediaBuySuccess
    with media_buy_id and affected_packages."""
    # Adapter returns success
    standard_mocks["adapter_instance"].update_media_buy.return_value = UpdateMediaBuySuccess(
        media_buy_id="mb_main",
        buyer_ref="",
        affected_packages=[],
    )

    mock_session = _setup_db_session(standard_mocks)

    # Currency validation path: media buy via repo, currency limit via session
    standard_mocks["uow_instance"].media_buys.get_by_id.return_value = _make_mock_media_buy("mb_main")
    mock_currency_limit = _make_mock_currency_limit(max_daily=100000)
    mock_scalars = MagicMock()
    mock_scalars.first.side_effect = [mock_currency_limit]
    mock_session.scalars.return_value = mock_scalars

    identity = _make_identity()
    req = UpdateMediaBuyRequest(
        media_buy_id="mb_main",
        packages=[{"package_id": "pkg_main_1", "budget": 15000.0}],
    )
    result = _update_media_buy_impl(req=req, identity=identity)

    assert isinstance(result, UpdateMediaBuySuccess)
    assert result.media_buy_id == "mb_main"
    assert len(result.affected_packages) == 1
    assert result.affected_packages[0].package_id == "pkg_main_1"

    # Verify adapter was called with correct action
    call_kwargs = standard_mocks["adapter_instance"].update_media_buy.call_args[1]
    assert call_kwargs["action"] == "update_package_budget"
    assert call_kwargs["package_id"] == "pkg_main_1"
    assert call_kwargs["budget"] == int(15000.0)


# ---------------------------------------------------------------------------
# HIGH_RISK Test 6: Flight date validation and persistence
# BDD: T-UC-003-alt-timing + T-UC-003-ext-e (merged)
# ---------------------------------------------------------------------------


class TestFlightDateValidationAndPersistence:
    """Covers both positive (dates persisted) and negative (invalid range rejected)."""

    def test_valid_date_range_persists_to_db(self, standard_mocks):
        """When start_time/end_time provided with valid range, persisted to DB."""
        mock_session = _setup_db_session(standard_mocks)

        # Mock existing media buy for date update path
        mock_existing_mb = MagicMock()
        mock_existing_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
        mock_existing_mb.end_time = datetime(2025, 12, 31, tzinfo=UTC)

        # Currency validation: media buy via repo (called twice: currency check + date path)
        standard_mocks["uow_instance"].media_buys.get_by_id.side_effect = [
            _make_mock_media_buy("mb_dates"),  # currency validation
            mock_existing_mb,  # date validation
        ]
        mock_scalars = MagicMock()
        mock_scalars.first.side_effect = [
            _make_mock_currency_limit(),  # currency limit (no max daily)
        ]
        mock_session.scalars.return_value = mock_scalars

        start = datetime(2025, 6, 1, tzinfo=UTC)
        end = datetime(2025, 12, 1, tzinfo=UTC)

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_dates",
            start_time=start,
            end_time=end,
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        assert result.media_buy_id == "mb_dates"
        # Date update should have been persisted via repository
        standard_mocks["uow_instance"].media_buys.update_fields.assert_called()

    def test_invalid_date_range_returns_error(self, standard_mocks):
        """When end_time <= start_time, returns code='invalid_date_range'."""
        mock_session = _setup_db_session(standard_mocks)

        # Mock existing media buy
        mock_existing_mb = MagicMock()
        mock_existing_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
        mock_existing_mb.end_time = datetime(2025, 12, 31, tzinfo=UTC)

        # Media buy via repo (called twice: currency check + date path)
        standard_mocks["uow_instance"].media_buys.get_by_id.side_effect = [
            _make_mock_media_buy("mb_dates_bad"),
            mock_existing_mb,
        ]
        mock_scalars = MagicMock()
        mock_scalars.first.side_effect = [
            _make_mock_currency_limit(),
        ]
        mock_session.scalars.return_value = mock_scalars

        # end_time BEFORE start_time
        start = datetime(2025, 6, 1, tzinfo=UTC)
        end = datetime(2025, 3, 1, tzinfo=UTC)

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_dates_bad",
            start_time=start,
            end_time=end,
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert len(result.errors) == 1
        assert result.errors[0].code == "invalid_date_range"

    def test_end_equals_start_returns_error(self, standard_mocks):
        """When end_time == start_time, returns code='invalid_date_range'."""
        mock_session = _setup_db_session(standard_mocks)

        same_time = datetime(2025, 6, 1, tzinfo=UTC)

        mock_existing_mb = MagicMock()
        mock_existing_mb.start_time = same_time
        mock_existing_mb.end_time = same_time

        # Media buy via repo (called twice: currency check + date path)
        standard_mocks["uow_instance"].media_buys.get_by_id.side_effect = [
            _make_mock_media_buy("mb_dates_equal"),
            mock_existing_mb,
        ]
        mock_scalars = MagicMock()
        mock_scalars.first.side_effect = [
            _make_mock_currency_limit(),
        ]
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_dates_equal",
            start_time=same_time,
            end_time=same_time,
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "invalid_date_range"


# ---------------------------------------------------------------------------
# HIGH_RISK Test 7: Campaign budget validation and persistence
# BDD: T-UC-003-alt-budget + T-UC-003-ext-d + T-UC-003-rule-008 (merged)
# ---------------------------------------------------------------------------


class TestCampaignBudgetValidationAndPersistence:
    """Covers both positive (budget persisted) and negative (invalid budget rejected)."""

    def test_positive_budget_persists_to_db(self, standard_mocks):
        """When total_budget > 0, persisted to DB, all packages affected."""
        mock_session = _setup_db_session(standard_mocks)

        # Currency validation: media buy via repo
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = _make_mock_media_buy("mb_budget")

        mock_currency_limit = _make_mock_currency_limit()
        mock_scalars = MagicMock()
        mock_scalars.first.side_effect = [mock_currency_limit]
        mock_session.scalars.return_value = mock_scalars

        # Mock packages for campaign budget affected tracking (via repo)
        mock_pkg = MagicMock()
        mock_pkg.package_id = "pkg_budget_1"
        standard_mocks["uow_instance"].media_buys.get_packages.return_value = [mock_pkg]

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_budget",
            budget=Budget(total=10000.0, currency="USD", pacing="even"),
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        assert result.media_buy_id == "mb_budget"
        # All packages should be listed as affected
        assert len(result.affected_packages) >= 1
        assert result.affected_packages[0].package_id == "pkg_budget_1"

        # Budget should have been persisted via repository
        standard_mocks["uow_instance"].media_buys.update_fields.assert_called()
        standard_mocks["uow_instance"].media_buys.get_packages.assert_called_once_with("mb_budget")

    def test_zero_budget_returns_error(self, standard_mocks):
        """When total_budget == 0, rejected at schema level (gt=0) per BR-RULE-008."""
        with pytest.raises(ValidationError, match="greater_than"):
            Budget(total=0.0, currency="USD", pacing="even")

    def test_negative_budget_returns_error(self, standard_mocks):
        """When total_budget < 0, rejected at schema level (gt=0) per BR-RULE-008."""
        with pytest.raises(ValidationError, match="greater_than"):
            Budget(total=-500.0, currency="USD", pacing="even")


# ---------------------------------------------------------------------------
# HIGH_RISK Test 8: Manual approval path through impl
# BDD: T-UC-003-alt-manual
# ---------------------------------------------------------------------------


def test_manual_approval_path_through_impl(standard_mocks):
    """When adapter.manual_approval_required=True and 'update_media_buy'
    in manual_approval_operations, workflow step created and response
    indicates pending status."""
    # Configure adapter to require manual approval
    standard_mocks["adapter_instance"].manual_approval_required = True
    standard_mocks["adapter_instance"].manual_approval_operations = ["update_media_buy"]

    identity = _make_identity()
    req = UpdateMediaBuyRequest(
        media_buy_id="mb_manual",
    )
    result = _update_media_buy_impl(req=req, identity=identity)

    # Should return UpdateMediaBuySuccess (not error)
    assert isinstance(result, UpdateMediaBuySuccess)
    assert result.media_buy_id == "mb_manual"
    # affected_packages should be empty (update not applied yet)
    assert result.affected_packages == []

    # Workflow step should be updated with requires_approval status
    update_calls = standard_mocks["ctx_mgr_instance"].update_workflow_step.call_args_list
    assert len(update_calls) == 1
    call_kwargs = update_calls[0][1]
    assert call_kwargs["status"] == "requires_approval"
    # Should have a comment about manual approval
    assert "manual approval" in str(call_kwargs.get("add_comment", "")).lower()


# ---------------------------------------------------------------------------
# HIGH_RISK Test (added in refinement): Package not found at impl level
# BDD: T-UC-003-ext-l (impl-level)
# ---------------------------------------------------------------------------


def test_package_not_found_returns_error(standard_mocks):
    """When package_id references non-existent package in targeting_overlay
    update path, returns code='package_not_found'."""
    _setup_db_session(standard_mocks)

    # Package lookup via repo returns None
    standard_mocks["uow_instance"].media_buys.get_package.return_value = None

    identity = _make_identity()
    req = UpdateMediaBuyRequest(
        media_buy_id="mb_pkg_nf",
        packages=[
            {
                "package_id": "pkg_nonexistent",
                "targeting_overlay": {"include_segment": [{"segment_id": "seg_1"}]},
            }
        ],
    )
    result = _update_media_buy_impl(req=req, identity=identity)

    assert isinstance(result, UpdateMediaBuyError)
    assert len(result.errors) == 1
    assert result.errors[0].code == "package_not_found"
    assert "pkg_nonexistent" in result.errors[0].message


# ---------------------------------------------------------------------------
# BUG: Campaign-level pause skips workflow step completion (#1041 Bug 2)
# ---------------------------------------------------------------------------


def test_pause_completes_workflow_step(standard_mocks):
    """Campaign-level pause must call update_workflow_step(status='completed').

    Bug #1041: The pause early-return path (line 441) returns
    UpdateMediaBuySuccess without updating the workflow step, leaving it
    in 'in_progress' forever.
    """
    # Configure adapter to return success for pause
    mock_result = UpdateMediaBuySuccess(
        media_buy_id="mb_pause",
        buyer_ref="buyer_pause",
        affected_packages=[],
    )
    standard_mocks["adapter_instance"].update_media_buy.return_value = mock_result

    identity = _make_identity()
    req = UpdateMediaBuyRequest(
        media_buy_id="mb_pause",
        paused=True,
    )
    result = _update_media_buy_impl(req=req, identity=identity)

    # Should succeed
    assert isinstance(result, UpdateMediaBuySuccess)
    assert result.media_buy_id == "mb_pause"

    # BUG: Workflow step must be marked 'completed' after successful pause
    update_calls = standard_mocks["ctx_mgr_instance"].update_workflow_step.call_args_list
    assert len(update_calls) >= 1, (
        "update_workflow_step never called — workflow step left in 'in_progress' state. "
        "The pause early-return path must complete the workflow step."
    )
    final_call_kwargs = update_calls[-1][1]
    assert final_call_kwargs["status"] == "completed", (
        f"Workflow step status is '{final_call_kwargs['status']}', expected 'completed'. "
        "The pause path returns without completing the workflow step."
    )


# ---------------------------------------------------------------------------
# BUG #1041: Manual approval gate creates no ObjectWorkflowMapping
# Without the mapping, the admin approval flow cannot find the media buy
# update to execute after approval. The workflow step is orphaned.
# ---------------------------------------------------------------------------


def test_manual_approval_creates_object_workflow_mapping(standard_mocks):
    """Bug #1041: when manual approval is required, an ObjectWorkflowMapping
    must be created so the admin approval flow can find the update to execute.

    Currently the manual approval path returns early (line 285) before the
    ObjectWorkflowMapping is created (line 1264). This means after approval,
    there is no link between the workflow step and the media buy update.
    """
    standard_mocks["adapter_instance"].manual_approval_required = True
    standard_mocks["adapter_instance"].manual_approval_operations = ["update_media_buy"]

    identity = _make_identity()
    req = UpdateMediaBuyRequest(
        media_buy_id="mb_approval_mapping",
        paused=True,
    )
    result = _update_media_buy_impl(req=req, identity=identity)

    assert isinstance(result, UpdateMediaBuySuccess)

    # The DB session should have had an ObjectWorkflowMapping added via session.add()
    mock_session = standard_mocks["db_session"]
    add_calls = mock_session.add.call_args_list

    # Find ObjectWorkflowMapping among session.add() calls
    from src.core.database.models import ObjectWorkflowMapping

    mapping_adds = [call for call in add_calls if isinstance(call[0][0], ObjectWorkflowMapping)]

    assert len(mapping_adds) >= 1, (
        f"No ObjectWorkflowMapping was added to the DB session during manual approval. "
        f"session.add() was called {len(add_calls)} times but none with ObjectWorkflowMapping. "
        f"Without this mapping, the admin approval flow cannot find the media buy update "
        f"to execute after approval (workflow step is orphaned)."
    )

    # Verify the mapping links the workflow step to the media buy update
    mapping = mapping_adds[0][0][0]
    assert mapping.step_id == "step_001"
    assert mapping.object_id == "mb_approval_mapping"
    assert mapping.object_type == "media_buy"
    assert mapping.action == "update"


# ---------------------------------------------------------------------------
# BUG: Manual approval gate stores no request data (#1041 Bug 1)
# ---------------------------------------------------------------------------


def test_manual_approval_stores_raw_request(standard_mocks):
    """When manual approval is required, the workflow step must store the
    original request data so approval can execute the update later.

    Bug #1041: The approval gate returns UpdateMediaBuySuccess with
    affected_packages=[] and never stores the request. After approval,
    there is nothing to execute.
    """
    standard_mocks["adapter_instance"].manual_approval_required = True
    standard_mocks["adapter_instance"].manual_approval_operations = ["update_media_buy"]

    identity = _make_identity()
    req = UpdateMediaBuyRequest(
        media_buy_id="mb_approval",
        paused=True,
    )
    result = _update_media_buy_impl(req=req, identity=identity)

    assert isinstance(result, UpdateMediaBuySuccess)

    # The workflow step's response_data must contain enough information
    # to execute the update after approval. At minimum, the request data
    # should be stored (similar to create_media_buy's raw_request pattern).
    update_calls = standard_mocks["ctx_mgr_instance"].update_workflow_step.call_args_list
    assert len(update_calls) >= 1
    call_kwargs = update_calls[-1][1]

    # The response_data stored in the workflow step
    response_data = call_kwargs.get("response_data", {})

    # BUG: response_data contains affected_packages=[] and nothing about
    # the actual update request. After approval, there's nothing to execute.
    # It should contain the original request (paused=True, etc.)
    assert (
        "request_data" in response_data or "raw_request" in response_data or response_data.get("paused") is not None
    ), (
        f"Workflow step response_data contains no request information: {response_data}. "
        "After approval, the system has no data to execute the update. "
        "The approval gate must store the original request (like create_media_buy stores raw_request)."
    )


# ---------------------------------------------------------------------------
# Regression: #1039 timezone mismatch in update_media_buy
# ---------------------------------------------------------------------------


class TestTimezoneHandlingRegression:
    """Regression tests for GitHub #1039: timezone mismatch when updating dates.

    The original bug: updating only end_time caused 'can't subtract
    offset-naive and offset-aware datetimes' because the DB value for
    start_time was naive while the request value was aware.

    Fixed by:
    - Migration 3a16c5fc27ce: all datetime columns → TIMESTAMPTZ
    - Schema validation: UpdateMediaBuyRequest rejects naive datetimes
    - Model definition: DateTime(timezone=True) on all datetime columns
    """

    def test_update_only_end_time_succeeds(self, standard_mocks):
        """Updating only end_time (start_time from DB) must not raise TypeError.

        Regression for #1039: start_time from DB + end_time from request
        must both be timezone-aware so flight_days calculation succeeds.
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_existing_mb = MagicMock()
        mock_existing_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
        mock_existing_mb.end_time = datetime(2025, 12, 31, tzinfo=UTC)

        # Media buy via repo (called twice: currency check + date path)
        standard_mocks["uow_instance"].media_buys.get_by_id.side_effect = [
            _make_mock_media_buy("mb_tz_end"),
            mock_existing_mb,
        ]
        mock_scalars = MagicMock()
        mock_scalars.first.side_effect = [
            _make_mock_currency_limit(),
        ]
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_tz_end",
            end_time=datetime(2025, 9, 1, tzinfo=UTC),  # Only end_time
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        # Must succeed — no TypeError from naive/aware subtraction
        assert isinstance(result, UpdateMediaBuySuccess)

    def test_update_only_start_time_succeeds(self, standard_mocks):
        """Updating only start_time (end_time from DB) must not raise TypeError.

        Mirror case of #1039: end_time from DB + start_time from request.
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_existing_mb = MagicMock()
        mock_existing_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
        mock_existing_mb.end_time = datetime(2025, 12, 31, tzinfo=UTC)

        # Media buy via repo (called twice: currency check + date path)
        standard_mocks["uow_instance"].media_buys.get_by_id.side_effect = [
            _make_mock_media_buy("mb_tz_start"),
            mock_existing_mb,
        ]
        mock_scalars = MagicMock()
        mock_scalars.first.side_effect = [
            _make_mock_currency_limit(),
        ]
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_tz_start",
            start_time=datetime(2025, 3, 1, tzinfo=UTC),  # Only start_time
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)

    def test_schema_rejects_naive_start_time(self):
        """UpdateMediaBuyRequest must reject naive (no tzinfo) start_time.

        This is the schema-level guard that prevents #1039 from recurring.
        """
        with pytest.raises(ValidationError, match="start_time must be timezone-aware"):
            UpdateMediaBuyRequest(
                media_buy_id="mb_naive",
                start_time=datetime(2025, 6, 1),  # naive — no tzinfo
            )

    def test_schema_rejects_naive_end_time(self):
        """UpdateMediaBuyRequest must reject naive (no tzinfo) end_time."""
        with pytest.raises(ValidationError, match="end_time must be timezone-aware"):
            UpdateMediaBuyRequest(
                media_buy_id="mb_naive",
                end_time=datetime(2025, 6, 1),  # naive — no tzinfo
            )
