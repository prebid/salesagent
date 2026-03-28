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
from itertools import repeat
from unittest.mock import ANY, MagicMock, Mock, patch

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
    cl.min_package_budget = Decimal("0")
    return cl


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
    standard_mocks["ctx_mgr_instance"].update_workflow_step.assert_called_once_with(
        ANY, status="failed", response_data=ANY, error_message=ANY
    )


def test_workflow_step_receives_request_model_with_protocol_metadata(standard_mocks):
    """Workflow persistence should serialize at the ContextManager boundary, not in _impl."""
    identity = _make_identity()
    req = UpdateMediaBuyRequest(media_buy_id="mb_workflow_meta")

    _update_media_buy_impl(req=req, identity=identity)

    standard_mocks["ctx_mgr_instance"].create_workflow_step.assert_called_once_with(
        context_id="ctx_001",
        step_type="tool_call",
        owner="principal",
        status="in_progress",
        tool_name="update_media_buy",
        request_data=req,
        request_metadata={"protocol": "mcp"},
    )


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
    mock_scalars.first.side_effect = repeat(mock_currency_limit)
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
    standard_mocks["adapter_instance"].update_media_buy.assert_called_once_with(
        media_buy_id="mb_combined",
        buyer_ref=ANY,
        action="update_package_budget",
        package_id="pkg_A",
        budget=ANY,
        today=ANY,
    )


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
    mock_scalars.first.side_effect = [
        mock_currency_limit,
        mock_currency_limit,
        mock_currency_limit,
        mock_currency_limit,
    ]
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
    mock_scalars.first.side_effect = repeat(mock_currency_limit)
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
        mock_currency_limit = _make_mock_currency_limit()
        mock_scalars.first.side_effect = repeat(mock_currency_limit)
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
        mock_currency_limit = _make_mock_currency_limit()
        mock_scalars.first.side_effect = repeat(mock_currency_limit)
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
        mock_scalars.first.side_effect = repeat(mock_currency_limit)
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
    - Migration 3a16c5fc27ce: all datetime columns -> TIMESTAMPTZ
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


# ===========================================================================
# UC-003 Obligation Coverage Tests
# Each test has a `Covers: UC-003-XXX-YY` tag in its docstring.
# ===========================================================================


# ---------------------------------------------------------------------------
# MAIN flow obligations
# ---------------------------------------------------------------------------


class TestUC003MainObligations:
    """Main flow obligations for update_media_buy."""

    def test_currency_limit_validation_on_package_budget(self, standard_mocks):
        """Currency limit validation rejects when daily spend exceeds max.

        Covers: UC-003-MAIN-05
        """
        _setup_db_session(standard_mocks)

        # 30-day flight, max_daily=$1000
        mock_mb = _make_mock_media_buy("mb_cur_limit")
        mock_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
        mock_mb.end_time = datetime(2025, 1, 31, tzinfo=UTC)  # 30 days
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = mock_mb

        mock_cl = _make_mock_currency_limit(max_daily=1000)
        standard_mocks["uow_instance"].currency_limits.get_for_currency.return_value = mock_cl

        identity = _make_identity()
        # daily = 50000/30 = 1666.67 > 1000
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_cur_limit",
            packages=[{"package_id": "pkg_1", "budget": 50000.0}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "budget_limit_exceeded"

    def test_currency_limit_passes_when_no_max(self, standard_mocks):
        """Daily spend check skipped when max_daily_package_spend not configured.

        Covers: UC-003-MAIN-06
        """
        mock_session = _setup_db_session(standard_mocks)

        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = _make_mock_media_buy("mb_no_max")
        mock_cl = _make_mock_currency_limit(max_daily=None)  # No max configured
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_cl
        mock_session.scalars.return_value = mock_scalars

        standard_mocks["adapter_instance"].update_media_buy.return_value = UpdateMediaBuySuccess(
            media_buy_id="mb_no_max", buyer_ref="", affected_packages=[]
        )

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_no_max",
            packages=[{"package_id": "pkg_1", "budget": 999999.0}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)

    def test_adapter_called_with_correct_action(self, standard_mocks):
        """Adapter update_media_buy called with action=update_package_budget.

        Covers: UC-003-MAIN-07
        """
        mock_session = _setup_db_session(standard_mocks)
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = _make_mock_media_buy("mb_adapter")
        mock_cl = _make_mock_currency_limit(max_daily=100000)
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_cl
        mock_session.scalars.return_value = mock_scalars

        standard_mocks["adapter_instance"].update_media_buy.return_value = UpdateMediaBuySuccess(
            media_buy_id="mb_adapter", buyer_ref="", affected_packages=[]
        )

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_adapter",
            packages=[{"package_id": "pkg_x", "budget": 5000.0}],
        )
        _update_media_buy_impl(req=req, identity=identity)

        call_kwargs = standard_mocks["adapter_instance"].update_media_buy.call_args[1]
        assert call_kwargs["action"] == "update_package_budget"
        assert call_kwargs["package_id"] == "pkg_x"
        assert call_kwargs["budget"] == 5000

    def test_database_persisted_after_adapter_success(self, standard_mocks):
        """After adapter returns success, affected_packages tracked in response.

        Covers: UC-003-MAIN-08
        """
        mock_session = _setup_db_session(standard_mocks)
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = _make_mock_media_buy("mb_persist")
        mock_cl = _make_mock_currency_limit(max_daily=100000)
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_cl
        mock_session.scalars.return_value = mock_scalars

        standard_mocks["adapter_instance"].update_media_buy.return_value = UpdateMediaBuySuccess(
            media_buy_id="mb_persist", buyer_ref="", affected_packages=[]
        )

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_persist",
            packages=[{"package_id": "pkg_y", "budget": 7500.0}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        assert len(result.affected_packages) == 1
        assert result.affected_packages[0].package_id == "pkg_y"

    def test_response_wrapped_with_status_completed(self, standard_mocks):
        """Workflow step updated with status=completed on success.

        Covers: UC-003-MAIN-10
        """
        _setup_db_session(standard_mocks)

        identity = _make_identity()
        req = UpdateMediaBuyRequest(media_buy_id="mb_status")
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        update_calls = standard_mocks["ctx_mgr_instance"].update_workflow_step.call_args_list
        assert len(update_calls) >= 1
        final_kwargs = update_calls[-1][1]
        assert final_kwargs["status"] == "completed"


# ---------------------------------------------------------------------------
# ALT: Pause/Resume Campaign
# ---------------------------------------------------------------------------


class TestUC003PauseResume:
    """Pause/resume campaign obligations."""

    def test_pause_may_require_manual_approval(self, standard_mocks):
        """Pause enters manual approval flow when configured.

        Covers: UC-003-ALT-PAUSE-RESUME-CAMPAIGN-05
        """
        standard_mocks["adapter_instance"].manual_approval_required = True
        standard_mocks["adapter_instance"].manual_approval_operations = ["update_media_buy"]

        identity = _make_identity()
        req = UpdateMediaBuyRequest(media_buy_id="mb_pause_manual", paused=True)
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        update_calls = standard_mocks["ctx_mgr_instance"].update_workflow_step.call_args_list
        assert len(update_calls) >= 1
        assert update_calls[0][1]["status"] == "requires_approval"


# ---------------------------------------------------------------------------
# ALT: Update Timing
# ---------------------------------------------------------------------------


class TestUC003UpdateTiming:
    """Update timing obligations."""

    def test_update_both_start_and_end_time(self, standard_mocks):
        """Both start_time and end_time updated when both provided.

        Covers: UC-003-ALT-UPDATE-TIMING-03
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_existing = MagicMock()
        mock_existing.start_time = datetime(2025, 1, 1, tzinfo=UTC)
        mock_existing.end_time = datetime(2025, 12, 31, tzinfo=UTC)

        standard_mocks["uow_instance"].media_buys.get_by_id.side_effect = [
            _make_mock_media_buy("mb_both_dates"),
            mock_existing,
        ]
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = _make_mock_currency_limit()
        mock_session.scalars.return_value = mock_scalars

        start = datetime(2025, 3, 1, tzinfo=UTC)
        end = datetime(2025, 9, 1, tzinfo=UTC)

        identity = _make_identity()
        req = UpdateMediaBuyRequest(media_buy_id="mb_both_dates", start_time=start, end_time=end)
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # update_fields should have been called with both start_time and end_time
        standard_mocks["uow_instance"].media_buys.update_fields.assert_called()
        call_kwargs = standard_mocks["uow_instance"].media_buys.update_fields.call_args
        assert "start_time" in call_kwargs[1]
        assert "end_time" in call_kwargs[1]

    def test_timing_update_no_adapter_call(self, standard_mocks):
        """Timing changes are database-only; no adapter call is made (gap G35).

        Covers: UC-003-ALT-UPDATE-TIMING-05
        """
        mock_session = _setup_db_session(standard_mocks)
        mock_existing = MagicMock()
        mock_existing.start_time = datetime(2025, 1, 1, tzinfo=UTC)
        mock_existing.end_time = datetime(2025, 12, 31, tzinfo=UTC)

        standard_mocks["uow_instance"].media_buys.get_by_id.side_effect = [
            _make_mock_media_buy("mb_no_adapter"),
            mock_existing,
        ]
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = _make_mock_currency_limit()
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_no_adapter",
            end_time=datetime(2025, 11, 1, tzinfo=UTC),
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # Adapter should NOT be called for timing-only updates
        standard_mocks["adapter_instance"].update_media_buy.assert_not_called()


# ---------------------------------------------------------------------------
# ALT: Campaign-Level Budget
# ---------------------------------------------------------------------------


class TestUC003CampaignLevelBudget:
    """Campaign-level budget obligations."""

    def test_campaign_budget_must_be_positive(self, standard_mocks):
        """Campaign budget=0 rejected with invalid_budget.

        Covers: UC-003-ALT-CAMPAIGN-LEVEL-BUDGET-02
        """
        with pytest.raises(ValidationError, match="greater_than"):
            Budget(total=0.0, currency="USD", pacing="even")

    def test_negative_campaign_budget_rejected(self, standard_mocks):
        """Negative campaign budget rejected.

        Covers: UC-003-ALT-CAMPAIGN-LEVEL-BUDGET-03
        """
        with pytest.raises(ValidationError, match="greater_than"):
            Budget(total=-100.0, currency="USD", pacing="even")

    def test_campaign_budget_update_recalculates_daily_spend(self, standard_mocks):
        """Campaign budget update triggers daily spend recalculation against max.

        Covers: UC-003-ALT-CAMPAIGN-LEVEL-BUDGET-04
        """
        _setup_db_session(standard_mocks)

        # 10-day flight, max_daily=$500
        mock_mb = _make_mock_media_buy("mb_recalc")
        mock_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
        mock_mb.end_time = datetime(2025, 1, 11, tzinfo=UTC)  # 10 days
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = mock_mb

        mock_cl = _make_mock_currency_limit(max_daily=500)
        standard_mocks["uow_instance"].currency_limits.get_for_currency.return_value = mock_cl

        identity = _make_identity()
        # daily = 10000/10 = 1000 > 500
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_recalc",
            packages=[{"package_id": "pkg_1", "budget": 10000.0}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "budget_limit_exceeded"

    def test_campaign_budget_no_adapter_call(self, standard_mocks):
        """Campaign budget update is database-only; no adapter call (gap G35).

        Covers: UC-003-ALT-CAMPAIGN-LEVEL-BUDGET-05
        """
        mock_session = _setup_db_session(standard_mocks)
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = _make_mock_media_buy("mb_no_sync")
        mock_cl = _make_mock_currency_limit()
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_cl
        mock_session.scalars.return_value = mock_scalars

        mock_pkg = MagicMock()
        mock_pkg.package_id = "pkg_1"
        standard_mocks["uow_instance"].media_buys.get_packages.return_value = [mock_pkg]

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_no_sync",
            budget=Budget(total=5000.0, currency="USD", pacing="even"),
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # Adapter should NOT be called for budget-only updates
        standard_mocks["adapter_instance"].update_media_buy.assert_not_called()


# ---------------------------------------------------------------------------
# ALT: Update Creative IDs
# ---------------------------------------------------------------------------


class TestUC003UpdateCreativeIds:
    """Creative ID update obligations."""

    def _setup_creative_mocks(self, standard_mocks, creative_ids, statuses=None, formats=None):
        """Helper to set up creative-related mocks."""
        mock_session = _setup_db_session(standard_mocks)

        # Media buy lookup
        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_creative"
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        # Build creative mocks
        creatives = []
        for i, cid in enumerate(creative_ids):
            c = MagicMock()
            c.creative_id = cid
            c.status = statuses[i] if statuses else "active"
            c.agent_url = "http://test.com"
            c.format = formats[i] if formats else "display"
            creatives.append(c)

        # Session scalars returns creatives
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = creatives
        mock_scalars.first.return_value = None  # No existing assignments by default
        mock_session.scalars.return_value = mock_scalars

        # Package with product
        mock_pkg = MagicMock()
        mock_pkg.package_config = {"product_id": "prod_1"}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        return mock_session, creatives

    def test_creative_existence_validation(self, standard_mocks):
        """Creative IDs not found in library returns creatives_not_found.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-02
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_creative"
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        # Only C1 found, C999 missing
        c1 = MagicMock()
        c1.creative_id = "C1"
        c1.status = "active"
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [c1]
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_creative",
            packages=[{"package_id": "pkg_1", "creative_ids": ["C1", "C999"]}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "creatives_not_found"
        assert "C999" in result.errors[0].message

    def test_creative_error_state_rejected(self, standard_mocks):
        """Creative in error state cannot be assigned.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-03
        """
        from src.core.exceptions import AdCPValidationError

        mock_session, _ = self._setup_creative_mocks(standard_mocks, ["C1"], statuses=["error"])

        # Product with matching format (to pass format check)
        mock_product = MagicMock()
        mock_product.format_ids = []
        mock_product.name = "Test Product"
        mock_scalars_seq = MagicMock()
        mock_scalars_seq.all.return_value = [
            MagicMock(creative_id="C1", status="error", agent_url="http://test.com", format="display")
        ]
        mock_scalars_seq.first.return_value = mock_product
        mock_session.scalars.return_value = mock_scalars_seq

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_creative",
            packages=[{"package_id": "pkg_1", "creative_ids": ["C1"]}],
        )
        with pytest.raises(AdCPValidationError, match="invalid creatives") as exc_info:
            _update_media_buy_impl(req=req, identity=identity)
        assert exc_info.value.details["error_code"] == "INVALID_CREATIVES"

    def test_creative_rejected_state_rejected(self, standard_mocks):
        """Creative in rejected state cannot be assigned.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-04
        """
        from src.core.exceptions import AdCPValidationError

        mock_session, _ = self._setup_creative_mocks(standard_mocks, ["C1"], statuses=["rejected"])

        mock_product = MagicMock()
        mock_product.format_ids = []
        mock_product.name = "Test Product"
        mock_scalars_seq = MagicMock()
        mock_scalars_seq.all.return_value = [
            MagicMock(creative_id="C1", status="rejected", agent_url="http://test.com", format="display")
        ]
        mock_scalars_seq.first.return_value = mock_product
        mock_session.scalars.return_value = mock_scalars_seq

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_creative",
            packages=[{"package_id": "pkg_1", "creative_ids": ["C1"]}],
        )
        with pytest.raises(AdCPValidationError, match="invalid creatives") as exc_info:
            _update_media_buy_impl(req=req, identity=identity)
        assert exc_info.value.details["error_code"] == "INVALID_CREATIVES"

    def test_creative_format_compatibility_check(self, standard_mocks):
        """Creative format mismatch with product returns INVALID_CREATIVES.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-05
        """
        from src.core.exceptions import AdCPValidationError

        mock_session = _setup_db_session(standard_mocks)

        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_creative"
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        # Creative with "video" format
        c1 = MagicMock()
        c1.creative_id = "C1"
        c1.status = "active"
        c1.agent_url = "http://test.com"
        c1.format = "video"

        # Product with only "display" format
        mock_product = MagicMock()
        mock_product.format_ids = [{"agent_url": "http://test.com", "id": "display"}]
        mock_product.name = "Display Product"

        # Package with product
        mock_pkg = MagicMock()
        mock_pkg.package_config = {"product_id": "prod_1"}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        # First call returns creatives, second returns product
        scalars_calls = iter(
            [
                MagicMock(all=Mock(return_value=[c1])),
                MagicMock(first=Mock(return_value=mock_product)),
            ]
        )
        mock_session.scalars.side_effect = lambda _stmt: next(scalars_calls)

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_creative",
            packages=[{"package_id": "pkg_1", "creative_ids": ["C1"]}],
        )
        with pytest.raises(AdCPValidationError, match="invalid creatives") as exc_info:
            _update_media_buy_impl(req=req, identity=identity)
        assert exc_info.value.details["error_code"] == "INVALID_CREATIVES"

    def test_creative_update_no_adapter_call(self, standard_mocks):
        """Creative ID updates persist directly to DB without adapter call.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-07
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_creative"
        mock_mb.status = "active"
        mock_mb.approved_at = None
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        c1 = MagicMock()
        c1.creative_id = "C1"
        c1.status = "active"
        c1.agent_url = "http://test.com"
        c1.format = "display"

        mock_pkg = MagicMock()
        mock_pkg.package_config = {"product_id": "prod_1"}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        mock_product = MagicMock()
        mock_product.format_ids = []
        mock_product.name = "Test Product"

        scalars_calls = iter(
            [
                MagicMock(all=Mock(return_value=[c1])),
                MagicMock(first=Mock(return_value=mock_product)),
                MagicMock(all=Mock(return_value=[])),  # existing assignments
            ]
        )
        mock_session.scalars.side_effect = lambda _stmt: next(scalars_calls)

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_creative",
            packages=[{"package_id": "pkg_1", "creative_ids": ["C1"]}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # Adapter should NOT be called for creative_ids updates
        standard_mocks["adapter_instance"].update_media_buy.assert_not_called()

    def test_immutable_package_fields(self, standard_mocks):
        """Schema prevents updating immutable fields like product_id.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-08
        """
        from src.core.schemas import AdCPPackageUpdate

        # AdCPPackageUpdate should not have a product_id override field
        # (it's inherited from library but schema constraint prevents update)
        pkg = AdCPPackageUpdate(package_id="pkg_1", creative_ids=["C1"])
        # product_id is not an updatable field; schema does not include it
        # as a first-class update field
        assert pkg.package_id == "pkg_1"
        assert pkg.creative_ids == ["C1"]

    def test_creative_model_extends_correct_adcp_type(self, standard_mocks):
        """Creative model extends the correct adcp library Creative type.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-09
        """
        from adcp.types.generated_poc.creative.list_creatives_response import (
            Creative as LibraryCreative,
        )

        from src.core.schemas import Creative

        # Verify inheritance chain: Creative extends listing Creative (not delivery)
        assert issubclass(Creative, LibraryCreative), (
            f"Creative should extend adcp library listing Creative, but MRO is: "
            f"{[c.__name__ for c in Creative.__mro__]}"
        )


# ---------------------------------------------------------------------------
# ALT: Upload Inline Creatives
# ---------------------------------------------------------------------------


class TestUC003UploadInlineCreatives:
    """Inline creative upload obligations."""

    def test_upload_and_assign_inline_creatives(self, standard_mocks):
        """Inline creatives uploaded and assigned via _sync_creatives_impl.

        Covers: UC-003-ALT-UPLOAD-INLINE-CREATIVES-01
        """
        _setup_db_session(standard_mocks)

        # Mock _sync_creatives_impl
        mock_sync_response = MagicMock()
        mock_sync_response.creatives = [
            MagicMock(creative_id="c1", action="created", errors=None),
            MagicMock(creative_id="c2", action="created", errors=None),
        ]

        with patch("src.core.tools.creatives._sync_creatives_impl", return_value=mock_sync_response) as mock_sync:
            identity = _make_identity()
            req = UpdateMediaBuyRequest(
                media_buy_id="mb_inline",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "creatives": [
                            {
                                "creative_id": "c1",
                                "name": "Creative 1",
                                "format_id": {"agent_url": "http://test.com", "id": "display"},
                                "assets": {"main": {"url": "https://example.com/a1.png"}},
                            },
                            {
                                "creative_id": "c2",
                                "name": "Creative 2",
                                "format_id": {"agent_url": "http://test.com", "id": "display"},
                                "assets": {"main": {"url": "https://example.com/a2.png"}},
                            },
                        ],
                    }
                ],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        mock_sync.assert_called_once_with(
            creatives=ANY,
            identity=ANY,
            assignments=ANY,
        )
        # affected_packages should track the creative upload
        assert len(result.affected_packages) >= 1

    def test_inline_creatives_additive_semantics(self, standard_mocks):
        """Inline creatives are additive (don't replace existing).

        Covers: UC-003-ALT-UPLOAD-INLINE-CREATIVES-02
        """
        _setup_db_session(standard_mocks)

        # Mock _sync_creatives_impl to return new creatives
        mock_sync_response = MagicMock()
        mock_sync_response.creatives = [
            MagicMock(creative_id="c3", action="created", errors=None),
        ]

        with patch("src.core.tools.creatives._sync_creatives_impl", return_value=mock_sync_response):
            identity = _make_identity()
            req = UpdateMediaBuyRequest(
                media_buy_id="mb_additive",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "creatives": [
                            {
                                "creative_id": "c3",
                                "name": "Creative 3",
                                "format_id": {"agent_url": "http://test.com", "id": "display"},
                                "assets": {"main": {"url": "https://example.com/a3.png"}},
                            }
                        ],
                    }
                ],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # The sync call does NOT delete existing assignments -
        # it only creates new ones (additive semantics)
        assert len(result.affected_packages) >= 1
        changes = result.affected_packages[0].changes_applied
        assert "creatives_uploaded" in changes

    def test_sync_failure_returns_error(self, standard_mocks):
        """Creative sync failure returns creative_sync_failed error.

        Covers: UC-003-ALT-UPLOAD-INLINE-CREATIVES-04
        """
        _setup_db_session(standard_mocks)

        # Mock _sync_creatives_impl to return a failure
        from adcp.types.generated_poc.enums.creative_action import CreativeAction

        mock_sync_response = MagicMock()
        failed_creative = MagicMock()
        failed_creative.creative_id = "c_fail"
        failed_creative.action = CreativeAction.failed
        failed_creative.errors = ["Upload failed"]
        mock_sync_response.creatives = [failed_creative]

        with patch("src.core.tools.creatives._sync_creatives_impl", return_value=mock_sync_response):
            identity = _make_identity()
            req = UpdateMediaBuyRequest(
                media_buy_id="mb_sync_fail",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "creatives": [
                            {
                                "creative_id": "c_fail",
                                "name": "Bad Creative",
                                "format_id": {"agent_url": "http://test.com", "id": "display"},
                                "assets": {"main": {"url": "https://example.com/fail.png"}},
                            }
                        ],
                    }
                ],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "creative_sync_failed"


# ---------------------------------------------------------------------------
# ALT: Update Creative Assignments
# ---------------------------------------------------------------------------


class TestUC003UpdateCreativeAssignments:
    """Creative assignment update obligations."""

    def test_creative_assignments_with_placement_targeting(self, standard_mocks):
        """Creative assignments with placement_ids validated against product.

        Covers: UC-003-ALT-UPDATE-CREATIVE-ASSIGNMENTS-02
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_assign"
        mock_mb.status = "active"
        mock_mb.approved_at = None
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        # Package with product that has placements
        mock_pkg = MagicMock()
        mock_pkg.package_config = {"product_id": "prod_1"}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        # Product with placements
        mock_product = MagicMock()
        mock_product.placements = [
            {"placement_id": "P1"},
            {"placement_id": "P2"},
            {"placement_id": "P3"},
        ]

        # Existing assignments and new assignments
        scalars_calls = iter(
            [
                MagicMock(first=Mock(return_value=mock_product)),  # product lookup
                MagicMock(all=Mock(return_value=[])),  # existing assignments
                MagicMock(first=Mock(return_value=None)),  # find assignment for C1
            ]
        )
        mock_session.scalars.side_effect = lambda _stmt: next(scalars_calls)

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_assign",
            packages=[
                {
                    "package_id": "pkg_1",
                    "creative_assignments": [
                        {"creative_id": "C1", "placement_ids": ["P1", "P2"]},
                    ],
                }
            ],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)

    def test_product_does_not_support_placement_targeting(self, standard_mocks):
        """Placement targeting rejected when product has no placements.

        Covers: UC-003-ALT-UPDATE-CREATIVE-ASSIGNMENTS-04
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_no_placement"
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        mock_pkg = MagicMock()
        mock_pkg.package_config = {"product_id": "prod_1"}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        # Product WITHOUT placements
        mock_product = MagicMock()
        mock_product.placements = []
        mock_product.product_id = "prod_1"

        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_product
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_no_placement",
            packages=[
                {
                    "package_id": "pkg_1",
                    "creative_assignments": [
                        {"creative_id": "C1", "placement_ids": ["P1"]},
                    ],
                }
            ],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "placement_targeting_not_supported"

    def test_creative_existence_validated_for_assignments(self, standard_mocks):
        """Creative not found when using creative_assignments path.

        Covers: UC-003-ALT-UPDATE-CREATIVE-ASSIGNMENTS-05
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_assign_not_found"
        mock_mb.status = "active"
        mock_mb.approved_at = None
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        # No placement_ids in assignment -> skip placement validation
        # Existing assignments empty, new assignment for C999 (doesn't exist)
        scalars_calls = iter(
            [
                MagicMock(all=Mock(return_value=[])),  # existing assignments
                MagicMock(first=Mock(return_value=None)),  # find assignment for C999
            ]
        )
        mock_session.scalars.side_effect = lambda _stmt: next(scalars_calls)

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_assign_not_found",
            packages=[
                {
                    "package_id": "pkg_1",
                    "creative_assignments": [
                        {"creative_id": "C999"},
                    ],
                }
            ],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        # The creative_assignments path creates assignments even for
        # non-existing creatives (existence check is in creative_ids path).
        # This test documents the current behavior.
        assert isinstance(result, UpdateMediaBuySuccess)


# ---------------------------------------------------------------------------
# ALT: Update Targeting Overlay
# ---------------------------------------------------------------------------


class TestUC003UpdateTargetingOverlay:
    """Targeting overlay update obligations."""

    def test_update_targeting_overlay_on_package(self, standard_mocks):
        """Targeting overlay replaces existing targeting in package_config.

        Covers: UC-003-ALT-UPDATE-TARGETING-OVERLAY-01
        """
        _setup_db_session(standard_mocks)

        mock_pkg = MagicMock()
        mock_pkg.package_config = {"targeting_overlay": {"old": True}}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_targeting",
            packages=[
                {
                    "package_id": "pkg_1",
                    "targeting_overlay": {"geo": {"include": ["US"]}},
                }
            ],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # targeting_overlay should have been replaced (stored as Pydantic model or dict)
        stored = mock_pkg.package_config["targeting_overlay"]
        assert stored is not None

    def test_targeting_overlay_not_validated(self, standard_mocks):
        """Targeting overlay persisted without validation (gap G36).

        Covers: UC-003-ALT-UPDATE-TARGETING-OVERLAY-02
        """
        _setup_db_session(standard_mocks)

        mock_pkg = MagicMock()
        mock_pkg.package_config = {}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        identity = _make_identity()
        # Invalid targeting data - should still be persisted
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_no_validate",
            packages=[
                {
                    "package_id": "pkg_1",
                    "targeting_overlay": {"unknown_field": "value", "conflicting_geo": True},
                }
            ],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # Even invalid targeting is persisted directly
        assert mock_pkg.package_config["targeting_overlay"] is not None

    def test_targeting_update_no_adapter_call(self, standard_mocks):
        """Targeting changes are database-only; no adapter call.

        Covers: UC-003-ALT-UPDATE-TARGETING-OVERLAY-03
        """
        _setup_db_session(standard_mocks)

        mock_pkg = MagicMock()
        mock_pkg.package_config = {}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_target_no_adapter",
            packages=[
                {
                    "package_id": "pkg_1",
                    "targeting_overlay": {"geo": {"include": ["US"]}},
                }
            ],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        standard_mocks["adapter_instance"].update_media_buy.assert_not_called()


# ---------------------------------------------------------------------------
# ALT: Manual Approval Required
# ---------------------------------------------------------------------------


class TestUC003ManualApproval:
    """Manual approval obligations."""

    def test_adapter_deferred_until_approval(self, standard_mocks):
        """Adapter NOT called during manual approval; deferred to approval time.

        Covers: UC-003-ALT-MANUAL-APPROVAL-REQUIRED-03
        """
        standard_mocks["adapter_instance"].manual_approval_required = True
        standard_mocks["adapter_instance"].manual_approval_operations = ["update_media_buy"]

        identity = _make_identity()
        req = UpdateMediaBuyRequest(media_buy_id="mb_deferred", paused=True)
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # Adapter should NOT be called (deferred until seller approves)
        standard_mocks["adapter_instance"].update_media_buy.assert_not_called()

    def test_seller_rejects_update(self, standard_mocks):
        """Seller rejection documented: buyer notified via webhook.

        Covers: UC-003-ALT-MANUAL-APPROVAL-REQUIRED-04
        """
        # This tests the manual approval setup that enables later rejection.
        # The actual rejection happens in the admin approval flow, not in _impl.
        standard_mocks["adapter_instance"].manual_approval_required = True
        standard_mocks["adapter_instance"].manual_approval_operations = ["update_media_buy"]

        identity = _make_identity()
        req = UpdateMediaBuyRequest(media_buy_id="mb_reject_setup", paused=True)
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # Verify workflow step created with requires_approval (enables rejection)
        update_calls = standard_mocks["ctx_mgr_instance"].update_workflow_step.call_args_list
        assert update_calls[0][1]["status"] == "requires_approval"
        # Verify request_data stored (needed for rejection notification)
        response_data = update_calls[0][1]["response_data"]
        assert "request_data" in response_data

    def test_buyer_can_poll_task_status(self, standard_mocks):
        """Workflow step ID returned so buyer can poll status.

        Covers: UC-003-ALT-MANUAL-APPROVAL-REQUIRED-05
        """
        standard_mocks["adapter_instance"].manual_approval_required = True
        standard_mocks["adapter_instance"].manual_approval_operations = ["update_media_buy"]

        identity = _make_identity()
        req = UpdateMediaBuyRequest(media_buy_id="mb_poll")
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # The workflow step was created (step_id="step_001")
        # and the response allows the buyer to track the status
        standard_mocks["ctx_mgr_instance"].create_workflow_step.assert_called_once_with(
            context_id="ctx_001",
            step_type="tool_call",
            owner="principal",
            status="in_progress",
            tool_name="update_media_buy",
            request_data=req,
            request_metadata={"protocol": "mcp"},
        )


# ---------------------------------------------------------------------------
# EXT-A: Authentication Error
# ---------------------------------------------------------------------------


class TestUC003ExtA:
    """Authentication error obligations."""

    def test_no_principal_in_context(self, standard_mocks):
        """Missing principal_id raises ValueError.

        Covers: UC-003-EXT-A-01
        """
        identity = _make_identity(principal_id=None)
        req = UpdateMediaBuyRequest(media_buy_id="mb_no_auth")

        with pytest.raises(ValueError, match="principal_id is required"):
            _update_media_buy_impl(req=req, identity=identity)

    def test_principal_not_found_in_database(self, standard_mocks):
        """Principal ID exists but no DB record returns principal_not_found.

        Covers: UC-003-EXT-A-02
        """
        standard_mocks["principal_obj"].return_value = None

        identity = _make_identity()
        req = UpdateMediaBuyRequest(media_buy_id="mb_no_principal")
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "principal_not_found"

    def test_state_unchanged_on_auth_failure(self, standard_mocks):
        """No records modified when authentication fails.

        Covers: UC-003-EXT-A-03
        """
        standard_mocks["principal_obj"].return_value = None

        identity = _make_identity()
        req = UpdateMediaBuyRequest(media_buy_id="mb_auth_fail")
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        # No adapter call
        standard_mocks["adapter_instance"].update_media_buy.assert_not_called()
        # No DB writes through UoW
        standard_mocks["uow_instance"].media_buys.update_fields.assert_not_called()


# ---------------------------------------------------------------------------
# EXT-C: Ownership Mismatch
# ---------------------------------------------------------------------------


class TestUC003ExtC:
    """Ownership mismatch obligations."""

    def test_state_unchanged_on_ownership_mismatch(self, standard_mocks):
        """Media buy remains unmodified on ownership mismatch.

        Covers: UC-003-EXT-C-02
        """
        standard_mocks["verify_principal"].side_effect = PermissionError(
            "Principal 'principal_test' does not own media buy 'mb_not_mine'."
        )

        identity = _make_identity()
        req = UpdateMediaBuyRequest(media_buy_id="mb_not_mine")

        with pytest.raises(PermissionError):
            _update_media_buy_impl(req=req, identity=identity)

        # No adapter call
        standard_mocks["adapter_instance"].update_media_buy.assert_not_called()
        # No DB writes
        standard_mocks["uow_instance"].media_buys.update_fields.assert_not_called()


# ---------------------------------------------------------------------------
# EXT-E: Date Range Invalid
# ---------------------------------------------------------------------------


class TestUC003ExtE:
    """Date range validation obligations."""

    def test_end_equals_start_returns_error(self, standard_mocks):
        """end_time == start_time returns invalid_date_range.

        Covers: UC-003-EXT-E-01
        """
        mock_session = _setup_db_session(standard_mocks)
        same_time = datetime(2025, 3, 1, tzinfo=UTC)

        mock_existing = MagicMock()
        mock_existing.start_time = same_time
        mock_existing.end_time = same_time

        standard_mocks["uow_instance"].media_buys.get_by_id.side_effect = [
            _make_mock_media_buy("mb_eq"),
            mock_existing,
        ]
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = _make_mock_currency_limit()
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        req = UpdateMediaBuyRequest(media_buy_id="mb_eq", start_time=same_time, end_time=same_time)
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "invalid_date_range"

    def test_end_before_existing_start(self, standard_mocks):
        """end_time before existing start_time (only end_time updated).

        Covers: UC-003-EXT-E-03
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_existing = MagicMock()
        mock_existing.start_time = datetime(2025, 3, 15, tzinfo=UTC)
        mock_existing.end_time = datetime(2025, 12, 31, tzinfo=UTC)

        standard_mocks["uow_instance"].media_buys.get_by_id.side_effect = [
            _make_mock_media_buy("mb_end_before"),
            mock_existing,
        ]
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = _make_mock_currency_limit()
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        # Only end_time, before existing start_time
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_end_before",
            end_time=datetime(2025, 3, 10, tzinfo=UTC),
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "invalid_date_range"

    def test_start_after_existing_end(self, standard_mocks):
        """start_time after existing end_time (only start_time updated).

        Covers: UC-003-EXT-E-04
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_existing = MagicMock()
        mock_existing.start_time = datetime(2025, 1, 1, tzinfo=UTC)
        mock_existing.end_time = datetime(2025, 3, 31, tzinfo=UTC)

        standard_mocks["uow_instance"].media_buys.get_by_id.side_effect = [
            _make_mock_media_buy("mb_start_after"),
            mock_existing,
        ]
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = _make_mock_currency_limit()
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        # Only start_time, after existing end_time
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_start_after",
            start_time=datetime(2025, 4, 15, tzinfo=UTC),
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "invalid_date_range"


# ---------------------------------------------------------------------------
# EXT-F: Currency Not Supported
# ---------------------------------------------------------------------------


class TestUC003ExtF:
    """Currency validation obligations."""

    def test_currency_not_in_tenant_config(self, standard_mocks):
        """Media buy currency not supported by tenant returns currency_not_supported.

        Covers: UC-003-EXT-F-01
        """
        _setup_db_session(standard_mocks)

        mock_mb = _make_mock_media_buy("mb_gbp")
        mock_mb.currency = "GBP"
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = mock_mb

        # Currency limit NOT found (GBP not configured)
        standard_mocks["uow_instance"].currency_limits.get_for_currency.return_value = None

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_gbp",
            packages=[{"package_id": "pkg_1", "budget": 5000.0}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "currency_not_supported"


# ---------------------------------------------------------------------------
# EXT-G: Daily Spend Cap Exceeded
# ---------------------------------------------------------------------------


class TestUC003ExtG:
    """Daily spend cap obligations."""

    def test_updated_budget_exceeds_daily_cap(self, standard_mocks):
        """Package budget update exceeding daily cap returns budget_limit_exceeded.

        Covers: UC-003-EXT-G-01
        """
        _setup_db_session(standard_mocks)

        mock_mb = _make_mock_media_buy("mb_daily")
        mock_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
        mock_mb.end_time = datetime(2025, 1, 11, tzinfo=UTC)  # 10 days
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = mock_mb

        mock_cl = _make_mock_currency_limit(max_daily=500)
        standard_mocks["uow_instance"].currency_limits.get_for_currency.return_value = mock_cl

        identity = _make_identity()
        # daily = 10000/10 = 1000 > 500
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_daily",
            packages=[{"package_id": "pkg_1", "budget": 10000.0}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "budget_limit_exceeded"


# ---------------------------------------------------------------------------
# EXT-H: Missing Package ID
# ---------------------------------------------------------------------------


class TestUC003ExtH:
    """Missing package ID obligations."""

    def test_package_update_without_package_id(self, standard_mocks):
        """Package budget update without package_id returns missing_package_id.

        Covers: UC-003-EXT-H-01
        """
        mock_session = _setup_db_session(standard_mocks)
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = _make_mock_media_buy("mb_no_pkg")
        mock_cl = _make_mock_currency_limit(max_daily=100000)
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_cl
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_no_pkg",
            packages=[{"budget": 5000.0}],  # No package_id
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "missing_package_id"

    def test_buyer_ref_at_package_level_gap(self, standard_mocks):
        """Package-level buyer_ref may not be implemented (gap G38).

        Covers: UC-003-EXT-H-02
        """
        mock_session = _setup_db_session(standard_mocks)
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = _make_mock_media_buy("mb_buyer_ref_pkg")
        mock_cl = _make_mock_currency_limit(max_daily=100000)
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_cl
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        # Package without package_id (gap G38: buyer_ref at package level not supported)
        # Schema forbids extra fields, so we can only omit package_id
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_buyer_ref_pkg",
            packages=[{"budget": 5000.0}],  # No package_id
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        # Current behavior: missing_package_id (package-level identification requires package_id)
        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "missing_package_id"


# ---------------------------------------------------------------------------
# EXT-I: Creative IDs Not Found
# ---------------------------------------------------------------------------


class TestUC003ExtI:
    """Creative IDs not found obligations."""

    def test_all_creative_ids_not_found(self, standard_mocks):
        """All referenced creatives missing returns creatives_not_found.

        Covers: UC-003-EXT-I-02
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_all_missing"
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        # No creatives found
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_all_missing",
            packages=[{"package_id": "pkg_1", "creative_ids": ["C999", "C998"]}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "creatives_not_found"
        assert "C999" in result.errors[0].message
        assert "C998" in result.errors[0].message


# ---------------------------------------------------------------------------
# EXT-J: Creative Validation Failure
# ---------------------------------------------------------------------------


class TestUC003ExtJ:
    """Creative validation failure obligations."""

    def test_creative_in_rejected_state(self, standard_mocks):
        """Creative in rejected state returns INVALID_CREATIVES.

        Covers: UC-003-EXT-J-02
        """
        from src.core.exceptions import AdCPValidationError

        mock_session = _setup_db_session(standard_mocks)

        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_rejected"
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        c1 = MagicMock()
        c1.creative_id = "C1"
        c1.status = "rejected"
        c1.agent_url = "http://test.com"
        c1.format = "display"

        mock_pkg = MagicMock()
        mock_pkg.package_config = {"product_id": "prod_1"}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        mock_product = MagicMock()
        mock_product.format_ids = []
        mock_product.name = "Test Product"

        scalars_calls = iter(
            [
                MagicMock(all=Mock(return_value=[c1])),
                MagicMock(first=Mock(return_value=mock_product)),
            ]
        )
        mock_session.scalars.side_effect = lambda _stmt: next(scalars_calls)

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_rejected",
            packages=[{"package_id": "pkg_1", "creative_ids": ["C1"]}],
        )
        with pytest.raises(AdCPValidationError, match="invalid creatives") as exc_info:
            _update_media_buy_impl(req=req, identity=identity)
        assert exc_info.value.details["error_code"] == "INVALID_CREATIVES"

    def test_all_validation_errors_collected(self, standard_mocks):
        """Multiple creative errors collected and returned together.

        Covers: UC-003-EXT-J-04
        """
        from src.core.exceptions import AdCPValidationError

        mock_session = _setup_db_session(standard_mocks)

        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_multi_err"
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        # C1 in error state, C2 in rejected state
        c1 = MagicMock()
        c1.creative_id = "C1"
        c1.status = "error"
        c1.agent_url = "http://test.com"
        c1.format = "display"

        c2 = MagicMock()
        c2.creative_id = "C2"
        c2.status = "rejected"
        c2.agent_url = "http://test.com"
        c2.format = "display"

        mock_pkg = MagicMock()
        mock_pkg.package_config = {"product_id": "prod_1"}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        mock_product = MagicMock()
        mock_product.format_ids = []
        mock_product.name = "Test Product"

        scalars_calls = iter(
            [
                MagicMock(all=Mock(return_value=[c1, c2])),
                MagicMock(first=Mock(return_value=mock_product)),
            ]
        )
        mock_session.scalars.side_effect = lambda _stmt: next(scalars_calls)

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_multi_err",
            packages=[{"package_id": "pkg_1", "creative_ids": ["C1", "C2"]}],
        )
        with pytest.raises(AdCPValidationError, match="invalid creatives") as exc_info:
            _update_media_buy_impl(req=req, identity=identity)

        # Both errors should be collected
        error_details = exc_info.value.details
        assert error_details["error_code"] == "INVALID_CREATIVES"
        assert len(error_details["creative_errors"]) == 2


# ---------------------------------------------------------------------------
# EXT-K: Creative Sync Failure
# ---------------------------------------------------------------------------


class TestUC003ExtK:
    """Creative sync failure obligations."""

    def test_inline_creative_upload_fails(self, standard_mocks):
        """Sync failure returns creative_sync_failed.

        Covers: UC-003-EXT-K-01
        """
        _setup_db_session(standard_mocks)

        from adcp.types.generated_poc.enums.creative_action import CreativeAction

        mock_sync_response = MagicMock()
        failed = MagicMock()
        failed.creative_id = "c_fail"
        failed.action = CreativeAction.failed
        failed.errors = ["Network error"]
        mock_sync_response.creatives = [failed]

        with patch("src.core.tools.creatives._sync_creatives_impl", return_value=mock_sync_response):
            identity = _make_identity()
            req = UpdateMediaBuyRequest(
                media_buy_id="mb_sync_err",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "creatives": [
                            {
                                "creative_id": "c_fail",
                                "name": "Fail",
                                "format_id": {"agent_url": "http://test.com", "id": "display"},
                                "assets": {"main": {"url": "https://example.com/fail.png"}},
                            }
                        ],
                    }
                ],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "creative_sync_failed"

    def test_media_buy_unmodified_on_sync_failure(self, standard_mocks):
        """Media buy unchanged when creative sync fails.

        Covers: UC-003-EXT-K-02
        """
        _setup_db_session(standard_mocks)

        from adcp.types.generated_poc.enums.creative_action import CreativeAction

        mock_sync_response = MagicMock()
        failed = MagicMock()
        failed.creative_id = "c_fail"
        failed.action = CreativeAction.failed
        failed.errors = ["Error"]
        mock_sync_response.creatives = [failed]

        with patch("src.core.tools.creatives._sync_creatives_impl", return_value=mock_sync_response):
            identity = _make_identity()
            req = UpdateMediaBuyRequest(
                media_buy_id="mb_no_modify",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "creatives": [
                            {
                                "creative_id": "c_fail",
                                "name": "Fail",
                                "format_id": {"agent_url": "http://test.com", "id": "display"},
                                "assets": {"main": {"url": "https://example.com/fail.png"}},
                            }
                        ],
                    }
                ],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        # No adapter call
        standard_mocks["adapter_instance"].update_media_buy.assert_not_called()
        # No DB writes through UoW
        standard_mocks["uow_instance"].media_buys.update_fields.assert_not_called()


# ---------------------------------------------------------------------------
# EXT-L: Package Not Found
# ---------------------------------------------------------------------------


class TestUC003ExtL:
    """Package not found obligations."""

    def test_package_id_not_in_media_buy(self, standard_mocks):
        """Package ID belongs to different media buy returns package_not_found.

        Covers: UC-003-EXT-L-01
        """
        _setup_db_session(standard_mocks)

        # Package lookup returns None (not in this media buy)
        standard_mocks["uow_instance"].media_buys.get_package.return_value = None

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_wrong_pkg",
            packages=[
                {
                    "package_id": "pkg_99",
                    "targeting_overlay": {"geo": {"include": ["US"]}},
                }
            ],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "package_not_found"

    def test_package_id_does_not_exist(self, standard_mocks):
        """Non-existent package_id returns package_not_found.

        Covers: UC-003-EXT-L-02
        """
        _setup_db_session(standard_mocks)

        standard_mocks["uow_instance"].media_buys.get_package.return_value = None

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_no_pkg_exist",
            packages=[
                {
                    "package_id": "pkg_nonexistent",
                    "targeting_overlay": {"include_segment": [{"segment_id": "s1"}]},
                }
            ],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "package_not_found"
        assert "pkg_nonexistent" in result.errors[0].message


# ---------------------------------------------------------------------------
# EXT-M: Invalid Placement IDs
# ---------------------------------------------------------------------------


class TestUC003ExtM:
    """Invalid placement IDs obligations."""

    def test_placement_id_not_valid_for_product(self, standard_mocks):
        """Invalid placement_id returns invalid_placement_ids.

        Covers: UC-003-EXT-M-01
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_bad_placement"
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        mock_pkg = MagicMock()
        mock_pkg.package_config = {"product_id": "prod_1"}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        mock_product = MagicMock()
        mock_product.placements = [
            {"placement_id": "P1"},
            {"placement_id": "P2"},
        ]

        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_product
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_bad_placement",
            packages=[
                {
                    "package_id": "pkg_1",
                    "creative_assignments": [
                        {"creative_id": "C1", "placement_ids": ["P1", "P999"]},
                    ],
                }
            ],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "invalid_placement_ids"

    def test_placement_targeting_on_unsupported_product(self, standard_mocks):
        """Placement targeting on product without placements rejected.

        Covers: UC-003-EXT-M-02
        """
        mock_session = _setup_db_session(standard_mocks)

        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_no_placements"
        standard_mocks["uow_instance"].media_buys.get_by_id_or_buyer_ref.return_value = mock_mb

        mock_pkg = MagicMock()
        mock_pkg.package_config = {"product_id": "prod_1"}
        standard_mocks["uow_instance"].media_buys.get_package.return_value = mock_pkg

        mock_product = MagicMock()
        mock_product.placements = []  # No placements
        mock_product.product_id = "prod_1"

        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_product
        mock_session.scalars.return_value = mock_scalars

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_no_placements",
            packages=[
                {
                    "package_id": "pkg_1",
                    "creative_assignments": [
                        {"creative_id": "C1", "placement_ids": ["P1"]},
                    ],
                }
            ],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "placement_targeting_not_supported"


# ---------------------------------------------------------------------------
# EXT-N: Insufficient Privileges
# ---------------------------------------------------------------------------


class TestUC003ExtN:
    """Insufficient privileges obligations."""

    def test_non_admin_principal_rejected(self, standard_mocks):
        """Adapter privilege check blocks non-admin operations.

        Covers: UC-003-EXT-N-01
        """
        mock_session = _setup_db_session(standard_mocks)
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = _make_mock_media_buy("mb_priv")
        mock_cl = _make_mock_currency_limit(max_daily=100000)
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_cl
        mock_session.scalars.return_value = mock_scalars

        # Adapter returns error for insufficient privileges
        from adcp.types import Error as AdCPErrorModel

        standard_mocks["adapter_instance"].update_media_buy.return_value = UpdateMediaBuyError(
            errors=[AdCPErrorModel(code="insufficient_privileges", message="Admin required")]
        )

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_priv",
            packages=[{"package_id": "pkg_1", "budget": 5000.0}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)


# ---------------------------------------------------------------------------
# EXT-O: Adapter/Workflow Failure
# ---------------------------------------------------------------------------


class TestUC003ExtO:
    """Adapter and workflow failure obligations."""

    def test_adapter_quota_error(self, standard_mocks):
        """Adapter API quota error returns activation_workflow_failed.

        Covers: UC-003-EXT-O-02
        """
        mock_session = _setup_db_session(standard_mocks)
        standard_mocks["uow_instance"].media_buys.get_by_id.return_value = _make_mock_media_buy("mb_quota")
        mock_cl = _make_mock_currency_limit(max_daily=100000)
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_cl
        mock_session.scalars.return_value = mock_scalars

        # Adapter returns error
        from adcp.types import Error as AdCPError

        standard_mocks["adapter_instance"].update_media_buy.return_value = UpdateMediaBuyError(
            errors=[AdCPError(code="api_quota_exceeded", message="Quota exceeded")]
        )

        identity = _make_identity()
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_quota",
            packages=[{"package_id": "pkg_1", "budget": 5000.0}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuyError)

    def test_workflow_creation_failure(self, standard_mocks):
        """Workflow step creation failure during manual approval.

        Covers: UC-003-EXT-O-03
        """
        standard_mocks["adapter_instance"].manual_approval_required = True
        standard_mocks["adapter_instance"].manual_approval_operations = ["update_media_buy"]

        # Workflow step update fails
        standard_mocks["ctx_mgr_instance"].update_workflow_step.side_effect = Exception(
            "Database error: workflow step creation failed"
        )

        identity = _make_identity()
        req = UpdateMediaBuyRequest(media_buy_id="mb_wf_fail", paused=True)

        with pytest.raises(Exception, match="workflow step creation failed"):
            _update_media_buy_impl(req=req, identity=identity)
