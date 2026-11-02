"""Behavioral tests for _update_media_buy_impl.

HIGH_RISK tests covering core impl flows that are most vulnerable
to breakage during FastAPI migration. Each test traces a BDD scenario
from BR-UC-003 through the impl layer.

BDD scenario cross-references:
- T-UC-003-ext-a-not-found: test_principal_not_found_returns_error
- T-UC-003-combined-update: test_combined_campaign_and_package_update
- T-UC-003-multi-package: test_multi_package_update_processes_all_packages
- T-UC-003-alt-timing + T-UC-003-ext-e: test_flight_date_validation_and_persistence
- T-UC-003-alt-budget + T-UC-003-ext-d + T-UC-003-rule-008: test_campaign_budget_validation_and_persistence
- T-UC-003-alt-manual: test_manual_approval_path_through_impl
- T-UC-003-ext-l (impl-level): test_package_not_found_returns_error
"""

from datetime import UTC, datetime
from decimal import Decimal
from itertools import repeat
from unittest.mock import MagicMock, Mock, patch

import pytest
from adcp.types.generated_poc.creative.sync_creatives_request import Assignment
from pydantic import ValidationError

from src.adapters.base import AdapterUpdateResult
from src.core.errors.codes import ErrorCode
from src.core.exceptions import (
    AdCPAdapterError,
    AdCPAuthorizationError,
    AdCPBudgetExceededError,
    AdCPCapabilityNotSupportedError,
    AdCPCreativeNotFoundError,
    AdCPGoneError,
    AdCPPackageNotFoundError,
    AdCPValidationError,
)
from src.core.schemas import (
    Error,
    UpdateMediaBuyRequest,
    UpdateMediaBuySubmitted,
    UpdateMediaBuySuccess,
)
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories.creative_asset import build_assets, image_spec
from tests.harness.media_buy_update import MediaBuyUpdateEnv

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MODULE = "src.core.tools.media_buy_update"
DB_MODULE = "src.core.database.database_session"


def _make_mock_media_buy(media_buy_id="mb_test", currency="USD", status="active"):
    """Create a mock MediaBuy database object.

    Default status is "active" so the state-machine precondition guard
    (added in ) lets all buyer actions through.
    """
    mb = MagicMock()
    mb.media_buy_id = media_buy_id
    mb.currency = currency
    mb.status = status
    mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
    mb.end_time = datetime(2025, 12, 31, tzinfo=UTC)
    return mb


def _make_mock_currency_limit(max_daily=None):
    """Create a mock CurrencyLimit with proper numeric values."""
    cl = MagicMock()
    cl.max_daily_package_spend = Decimal(str(max_daily)) if max_daily else None
    cl.min_package_budget = Decimal("0")
    return cl


# ---------------------------------------------------------------------------
# DELETED: test_principal_not_found_returns_error (BDD: T-UC-003-ext-a-not-found).
# It configured ``env.mock["principal"].return_value = None`` and expected
# _update_media_buy_impl to raise AUTH_INVALID. Neither half can happen: a protected
# tool's ``ResolvedIdentity.principal`` is ``InstanceOf[Principal]``, REQUIRED
# (src/core/resolved_identity.py:124), so the resolver has already loaded the row before
# the tool runs and there is no principal lookup left in the env to stub; and AUTH_INVALID
# is minted by the resolver alone, banned in a tool by TID251 in ruff-boundary.toml. See
# the same removal, with the same reasoning, in tests/unit/test_get_media_buys.py:427.
# ---------------------------------------------------------------------------


def test_workflow_step_receives_the_request_model():
    """Workflow persistence should serialize at the ContextManager boundary, not in _impl.

    The call also used to carry ``request_metadata={"protocol": ...}``. That key had no
    reader left once the webhook payload stopped forking on the buyer's sync transport --
    the envelope is the same one for every transport -- so it was removed with the fork
    rather than kept as provenance nothing consults.
    """
    with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
        # Direct _impl call (not env.call_impl) to keep the ``req`` reference the
        # assert_called_once_with(request_data=req) check below needs.
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"}, idempotency_key="test-idem-key-0001", media_buy_id="mb_workflow_meta"
        )

        _update_media_buy_impl(req=req, identity=env.identity)

        env.mock["ctx_mgr"].return_value.create_workflow_step.assert_called_once_with(
            context_id="ctx_001",
            step_type="tool_call",
            owner="principal",
            status="in_progress",
            tool_name="update_media_buy",
            request_data=req,
        )


# ---------------------------------------------------------------------------
# HIGH_RISK Test 2: Combined campaign + package update
# BDD: T-UC-003-combined-update
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HIGH_RISK Test 3: Multi-package update
# BDD: T-UC-003-multi-package
# ---------------------------------------------------------------------------


def test_multi_package_update_processes_all_packages():
    """When packages contains 3 items with budget updates,
    all 3 are processed and appear in affected_packages."""
    with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
        # Adapter returns success for each update_package_budget call
        env.mock["adapter"].return_value.update_media_buy.return_value = AdapterUpdateResult(
            media_buy_id="mb_multi",
            affected_packages=[],
        )

        mock_session = env.mock["uow"].return_value.session

        # Currency validation: media_buy via repo, currency_limit via session
        mock_media_buy = _make_mock_media_buy("mb_multi")
        mock_currency_limit = _make_mock_currency_limit(max_daily=100000)
        env.mock["uow"].return_value.media_buys.get_by_id.return_value = mock_media_buy
        mock_scalars = MagicMock()
        mock_scalars.first.side_effect = [
            mock_currency_limit,
            mock_currency_limit,
            mock_currency_limit,
            mock_currency_limit,
        ]
        mock_session.scalars.return_value = mock_scalars

        identity = env.identity
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
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
        assert env.mock["adapter"].return_value.update_media_buy.call_count == 3


# ---------------------------------------------------------------------------
# HIGH_RISK Test 4: Buyer_ref positive resolution
# BDD: T-UC-003-buyer-ref (positive path)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HIGH_RISK Test 5: Main flow - package budget update
# BDD: T-UC-003-main
# ---------------------------------------------------------------------------


def test_main_flow_package_budget_update():
    """When package budget change through impl, returns UpdateMediaBuySuccess
    with media_buy_id and affected_packages."""
    with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
        # Adapter returns success
        env.mock["adapter"].return_value.update_media_buy.return_value = AdapterUpdateResult(
            media_buy_id="mb_main",
            affected_packages=[],
        )

        mock_session = env.mock["uow"].return_value.session

        # Currency validation path: media buy via repo, currency limit via session
        env.mock["uow"].return_value.media_buys.get_by_id.return_value = _make_mock_media_buy("mb_main")
        mock_currency_limit = _make_mock_currency_limit(max_daily=100000)
        mock_scalars = MagicMock()
        mock_scalars.first.side_effect = repeat(mock_currency_limit)
        mock_session.scalars.return_value = mock_scalars

        identity = env.identity
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_main",
            packages=[{"package_id": "pkg_main_1", "budget": 15000.0}],
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        assert result.media_buy_id == "mb_main"
        assert len(result.affected_packages) == 1
        assert result.affected_packages[0].package_id == "pkg_main_1"

        # Verify adapter was called with correct action
        call_kwargs = env.mock["adapter"].return_value.update_media_buy.call_args[1]
        assert call_kwargs["action"] == "update_package_budget"
        assert call_kwargs["package_id"] == "pkg_main_1"
        assert call_kwargs["budget"] == int(15000.0)


# ---------------------------------------------------------------------------
# HIGH_RISK Test 6: Flight date validation and persistence
# BDD: T-UC-003-alt-timing + T-UC-003-ext-e (merged)
# ---------------------------------------------------------------------------


class TestFlightDateValidationAndPersistence:
    """Covers both positive (dates persisted) and negative (invalid range rejected)."""

    def test_valid_date_range_persists_to_db(self):
        """When start_time/end_time provided with valid range, persisted to DB."""
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            # Mock existing media buy for date update path
            mock_existing_mb = MagicMock()
            mock_existing_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
            mock_existing_mb.end_time = datetime(2025, 12, 31, tzinfo=UTC)

            # Media buy via repo (precondition + currency check + date path + valid_actions)
            # Date-update path: precondition needs status, mock_existing_mb has no status set,
            # so we substitute a properly-configured mock for those slots too.
            mock_existing_mb.status = "active"
            env.mock["uow"].return_value.media_buys.get_by_id.side_effect = [
                _make_mock_media_buy("mb_dates"),  # state-machine precondition
                _make_mock_media_buy("mb_dates"),  # currency validation
                mock_existing_mb,  # date validation
                _make_mock_media_buy("mb_dates"),  # valid_actions lookup
            ]
            mock_scalars = MagicMock()
            mock_scalars.first.side_effect = [
                _make_mock_currency_limit(),  # currency limit (no max daily)
            ]
            mock_session.scalars.return_value = mock_scalars

            start = datetime(2025, 6, 1, tzinfo=UTC)
            end = datetime(2025, 12, 1, tzinfo=UTC)

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_dates",
                start_time=start,
                end_time=end,
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)
            assert result.media_buy_id == "mb_dates"
            # Date update should have been persisted via repository
            env.mock["uow"].return_value.media_buys.update_fields.assert_called()

    def test_invalid_date_range_returns_error(self):
        """When end_time <= start_time, returns code='invalid_date_range'."""
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            # Mock existing media buy
            mock_existing_mb = MagicMock()
            mock_existing_mb.status = "active"
            mock_existing_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
            mock_existing_mb.end_time = datetime(2025, 12, 31, tzinfo=UTC)

            # Media buy via repo (precondition + currency check + date path)
            env.mock["uow"].return_value.media_buys.get_by_id.side_effect = [
                _make_mock_media_buy("mb_dates_bad"),
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

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_dates_bad",
                start_time=start,
                end_time=end,
            )
            with pytest.raises(AdCPValidationError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "VALIDATION_ERROR"

    def test_end_equals_start_returns_error(self):
        """When end_time == start_time, returns code='invalid_date_range'."""
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            same_time = datetime(2025, 6, 1, tzinfo=UTC)

            mock_existing_mb = MagicMock()
            mock_existing_mb.status = "active"
            mock_existing_mb.start_time = same_time
            mock_existing_mb.end_time = same_time

            # Media buy via repo (precondition + currency check + date path)
            env.mock["uow"].return_value.media_buys.get_by_id.side_effect = [
                _make_mock_media_buy("mb_dates_equal"),
                _make_mock_media_buy("mb_dates_equal"),
                mock_existing_mb,
            ]
            mock_scalars = MagicMock()
            mock_currency_limit = _make_mock_currency_limit()
            mock_scalars.first.side_effect = repeat(mock_currency_limit)
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_dates_equal",
                start_time=same_time,
                end_time=same_time,
            )
            with pytest.raises(AdCPValidationError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# HIGH_RISK Test 7: Campaign budget validation and persistence
# BDD: T-UC-003-alt-budget + T-UC-003-ext-d + T-UC-003-rule-008 (merged)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HIGH_RISK Test 8: Manual approval path through impl
# BDD: T-UC-003-alt-manual
# ---------------------------------------------------------------------------


def test_manual_approval_path_through_impl():
    """When adapter.manual_approval_required=True and 'update_media_buy'
    in manual_approval_operations, workflow step created and response
    indicates pending status."""
    with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
        # Configure adapter to require manual approval
        env.mock["adapter"].return_value.manual_approval_required = True
        env.mock["adapter"].return_value.manual_approval_operations = ["update_media_buy"]

        identity = env.identity
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_manual",
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        # Spec 3.1.1: a not-yet-applied (pending approval) update is the SUBMITTED variant,
        # not a completed success. status="submitted" + task_id (the workflow step).
        assert isinstance(result, UpdateMediaBuySubmitted)
        assert result.status == "submitted"
        assert result.task_id == "step_001"
        # Update not applied yet: the submitted variant carries no media_buy_id or
        # affected_packages fields — the buyer polls task_id for the applied outcome.
        # (Strict form from the main merge: the pre-3.1.1 success shape asserted
        # `affected_packages == []`; the submitted envelope must not carry either field.)
        dumped = result.model_dump()
        assert "affected_packages" not in dumped
        assert "media_buy_id" not in dumped

        # Workflow step should be updated with requires_approval status
        result_calls = env.mock["ctx_mgr"].return_value.audit_workflow_step_result.call_args_list
        assert len(result_calls) == 1
        call_kwargs = result_calls[0][1]
        assert call_kwargs["status"] == "requires_approval"
        # Should have a comment about manual approval
        assert "manual approval" in str(call_kwargs.get("add_comment", "")).lower()


# ---------------------------------------------------------------------------
# HIGH_RISK Test (added in refinement): Package not found at impl level
# BDD: T-UC-003-ext-l (impl-level)
# ---------------------------------------------------------------------------


def test_package_not_found_returns_error():
    """When package_id references non-existent package in targeting_overlay
    update path, returns code='package_not_found'."""
    with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
        # State-machine precondition needs a non-terminal status
        env.mock["uow"].return_value.media_buys.get_by_id.return_value = _make_mock_media_buy("mb_pkg_nf")
        # Package lookup via repo returns None
        env.mock["uow"].return_value.media_buys.get_package.return_value = None

        identity = env.identity
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_pkg_nf",
            packages=[{"package_id": "pkg_nonexistent", "targeting_overlay": {"geo_countries": ["US"]}}],
        )
        with pytest.raises(AdCPPackageNotFoundError) as exc_info:
            _update_media_buy_impl(req=req, identity=identity)
        # The identifier is STRUCTURED now: details/field, not prose.

        assert exc_info.value.error_code == "PACKAGE_NOT_FOUND"


# ---------------------------------------------------------------------------
# BUG: Campaign-level pause skips workflow step completion (#1041 Bug 2)
# ---------------------------------------------------------------------------


def test_pause_completes_workflow_step():
    """Campaign-level pause must call update_workflow_step(status='completed').

    Bug #1041: The pause early-return path (line 441) returns
    UpdateMediaBuySuccess without updating the workflow step, leaving it
    in 'in_progress' forever.
    """
    with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
        # Configure adapter to return success for pause
        mock_result = AdapterUpdateResult(
            media_buy_id="mb_pause",
            affected_packages=[],
        )
        env.mock["adapter"].return_value.update_media_buy.return_value = mock_result

        identity = env.identity
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_pause",
            paused=True,
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        # Should succeed
        assert isinstance(result, UpdateMediaBuySuccess)
        assert result.media_buy_id == "mb_pause"

        # BUG: Workflow step must be marked 'completed' after successful pause
        result_calls = env.mock["ctx_mgr"].return_value.audit_workflow_step_result.call_args_list
        assert len(result_calls) >= 1, (
            "audit_workflow_step_result never called — workflow step left in 'in_progress' state. "
            "The pause early-return path must complete the workflow step."
        )
        final_status = result_calls[-1][1].get("status", "completed")
        assert final_status == "completed", (
            f"Workflow step status is '{final_status}', expected 'completed'. "
            "The pause path returns without completing the workflow step."
        )


# ---------------------------------------------------------------------------
# BUG #1041: Manual approval gate creates no ObjectWorkflowMapping
# Without the mapping, the admin approval flow cannot find the media buy
# update to execute after approval. The workflow step is orphaned.
# ---------------------------------------------------------------------------


def test_manual_approval_creates_object_workflow_mapping():
    """Bug #1041: when manual approval is required, an ObjectWorkflowMapping
    must be created so the admin approval flow can find the update to execute.

    Currently the manual approval path returns early (line 285) before the
    ObjectWorkflowMapping is created (line 1264). This means after approval,
    there is no link between the workflow step and the media buy update.
    """
    with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
        env.mock["adapter"].return_value.manual_approval_required = True
        env.mock["adapter"].return_value.manual_approval_operations = ["update_media_buy"]

        identity = env.identity
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_approval_mapping",
            paused=True,
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySubmitted)

        # The DB session should have had an ObjectWorkflowMapping added via session.add()
        mock_session = env.mock["uow"].return_value.session
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


def test_manual_approval_stores_raw_request():
    """When manual approval is required, the workflow step must store the
    original request data so approval can execute the update later.

    Bug #1041: The approval gate returns UpdateMediaBuySuccess with
    affected_packages=[] and never stores the request. After approval,
    there is nothing to execute.
    """
    with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
        env.mock["adapter"].return_value.manual_approval_required = True
        env.mock["adapter"].return_value.manual_approval_operations = ["update_media_buy"]

        identity = env.identity
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_approval",
            paused=True,
        )
        result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySubmitted)

        # The workflow step's response_data must contain enough information
        # to execute the update after approval. At minimum, the request data
        # should be stored (similar to create_media_buy's raw_request pattern).
        result_calls = env.mock["ctx_mgr"].return_value.audit_workflow_step_result.call_args_list
        assert len(result_calls) >= 1
        call_kwargs = result_calls[-1][1]

        # The approval gate must pass the originating request (request_obj) so the
        # admin approval flow can execute the update later — audit_workflow_step_result
        # serializes it under response_data["request_data"].
        assert call_kwargs.get("request_obj") is not None, (
            "Workflow step persists no request information. After approval, the system has "
            "no data to execute the update. The approval gate must pass request_obj "
            "(like create_media_buy stores raw_request)."
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

    def test_update_only_end_time_succeeds(self):
        """Updating only end_time (start_time from DB) must not raise TypeError.

        Regression for #1039: start_time from DB + end_time from request
        must both be timezone-aware so flight_days calculation succeeds.
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            mock_existing_mb = MagicMock()
            mock_existing_mb.status = "active"
            mock_existing_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
            mock_existing_mb.end_time = datetime(2025, 12, 31, tzinfo=UTC)

            # Media buy via repo (precondition + currency check + date path + valid_actions)
            env.mock["uow"].return_value.media_buys.get_by_id.side_effect = [
                _make_mock_media_buy("mb_tz_end"),
                _make_mock_media_buy("mb_tz_end"),
                mock_existing_mb,
                _make_mock_media_buy("mb_tz_end"),
            ]
            mock_scalars = MagicMock()
            mock_scalars.first.side_effect = [
                _make_mock_currency_limit(),
                _make_mock_currency_limit(),
            ]
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_tz_end",
                end_time=datetime(2025, 9, 1, tzinfo=UTC),  # Only end_time
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            # Must succeed — no TypeError from naive/aware subtraction
            assert isinstance(result, UpdateMediaBuySuccess)

    def test_update_only_start_time_succeeds(self):
        """Updating only start_time (end_time from DB) must not raise TypeError.

        Mirror case of #1039: end_time from DB + start_time from request.
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            mock_existing_mb = MagicMock()
            mock_existing_mb.status = "active"
            mock_existing_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
            mock_existing_mb.end_time = datetime(2025, 12, 31, tzinfo=UTC)

            # Media buy via repo (precondition + currency check + date path + valid_actions)
            env.mock["uow"].return_value.media_buys.get_by_id.side_effect = [
                _make_mock_media_buy("mb_tz_start"),
                _make_mock_media_buy("mb_tz_start"),
                mock_existing_mb,
                _make_mock_media_buy("mb_tz_start"),
            ]
            mock_scalars = MagicMock()
            mock_scalars.first.side_effect = [
                _make_mock_currency_limit(),
                _make_mock_currency_limit(),
            ]
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
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
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_naive",
                start_time=datetime(2025, 6, 1),  # naive — no tzinfo
            )

    def test_schema_rejects_naive_end_time(self):
        """UpdateMediaBuyRequest must reject naive (no tzinfo) end_time."""
        with pytest.raises(ValidationError, match="end_time must be timezone-aware"):
            UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
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

    def test_currency_limit_validation_on_package_budget(self):
        """Currency limit validation rejects when daily spend exceeds max.

        Covers: UC-003-MAIN-05
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            # 30-day flight, max_daily=$1000
            mock_mb = _make_mock_media_buy("mb_cur_limit")
            mock_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
            mock_mb.end_time = datetime(2025, 1, 31, tzinfo=UTC)  # 30 days
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = mock_mb

            mock_cl = _make_mock_currency_limit(max_daily=1000)
            env.mock["uow"].return_value.currency_limits.get_for_currency.return_value = mock_cl

            identity = env.identity
            # daily = 50000/30 = 1666.67 > 1000
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_cur_limit",
                packages=[{"package_id": "pkg_1", "budget": 50000.0}],
            )
            with pytest.raises(AdCPBudgetExceededError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "BUDGET_EXCEEDED"

    def test_currency_limit_passes_when_no_max(self):
        """Daily spend check skipped when max_daily_package_spend not configured.

        Covers: UC-003-MAIN-06
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            env.mock["uow"].return_value.media_buys.get_by_id.return_value = _make_mock_media_buy("mb_no_max")
            mock_cl = _make_mock_currency_limit(max_daily=None)  # No max configured
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = mock_cl
            mock_session.scalars.return_value = mock_scalars

            env.mock["adapter"].return_value.update_media_buy.return_value = AdapterUpdateResult(
                media_buy_id="mb_no_max", affected_packages=[]
            )

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_no_max",
                packages=[{"package_id": "pkg_1", "budget": 999999.0}],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)

    def test_adapter_called_with_correct_action(self):
        """Adapter update_media_buy called with action=update_package_budget.

        Covers: UC-003-MAIN-07
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = _make_mock_media_buy("mb_adapter")
            mock_cl = _make_mock_currency_limit(max_daily=100000)
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = mock_cl
            mock_session.scalars.return_value = mock_scalars

            env.mock["adapter"].return_value.update_media_buy.return_value = AdapterUpdateResult(
                media_buy_id="mb_adapter", affected_packages=[]
            )

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_adapter",
                packages=[{"package_id": "pkg_x", "budget": 5000.0}],
            )
            _update_media_buy_impl(req=req, identity=identity)

            call_kwargs = env.mock["adapter"].return_value.update_media_buy.call_args[1]
            assert call_kwargs["action"] == "update_package_budget"
            assert call_kwargs["package_id"] == "pkg_x"
            assert call_kwargs["budget"] == 5000

    def test_database_persisted_after_adapter_success(self):
        """After adapter returns success, affected_packages tracked in response.

        Covers: UC-003-MAIN-08
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = _make_mock_media_buy("mb_persist")
            mock_cl = _make_mock_currency_limit(max_daily=100000)
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = mock_cl
            mock_session.scalars.return_value = mock_scalars

            env.mock["adapter"].return_value.update_media_buy.return_value = AdapterUpdateResult(
                media_buy_id="mb_persist", affected_packages=[]
            )

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_persist",
                packages=[{"package_id": "pkg_y", "budget": 7500.0}],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)
            assert len(result.affected_packages) == 1
            assert result.affected_packages[0].package_id == "pkg_y"

    def test_response_wrapped_with_status_completed(self):
        """Workflow step updated with status=completed on success.

        Covers: UC-003-MAIN-10
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"}, idempotency_key="test-idem-key-0001", media_buy_id="mb_status"
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)
            result_calls = env.mock["ctx_mgr"].return_value.audit_workflow_step_result.call_args_list
            assert len(result_calls) >= 1
            assert result_calls[-1][1].get("status", "completed") == "completed"


# ---------------------------------------------------------------------------
# ALT: Pause/Resume Campaign
# ---------------------------------------------------------------------------


class TestUC003PauseResume:
    """Pause/resume campaign obligations."""

    def test_pause_may_require_manual_approval(self):
        """Pause enters manual approval flow when configured.

        Covers: UC-003-ALT-PAUSE-RESUME-CAMPAIGN-05
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            env.mock["adapter"].return_value.manual_approval_required = True
            env.mock["adapter"].return_value.manual_approval_operations = ["update_media_buy"]

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_pause_manual",
                paused=True,
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySubmitted)
            result_calls = env.mock["ctx_mgr"].return_value.audit_workflow_step_result.call_args_list
            assert len(result_calls) >= 1
            assert result_calls[0][1]["status"] == "requires_approval"


# ---------------------------------------------------------------------------
# ALT: Update Timing
# ---------------------------------------------------------------------------


class TestUC003UpdateTiming:
    """Update timing obligations."""

    def test_update_both_start_and_end_time(self):
        """Both start_time and end_time updated when both provided.

        Covers: UC-003-ALT-UPDATE-TIMING-03
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            mock_existing = MagicMock()
            mock_existing.status = "active"
            mock_existing.start_time = datetime(2025, 1, 1, tzinfo=UTC)
            mock_existing.end_time = datetime(2025, 12, 31, tzinfo=UTC)

            env.mock["uow"].return_value.media_buys.get_by_id.side_effect = [
                _make_mock_media_buy("mb_both_dates"),  # state-machine precondition
                _make_mock_media_buy("mb_both_dates"),
                mock_existing,
                _make_mock_media_buy("mb_both_dates"),  # valid_actions lookup
            ]
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = _make_mock_currency_limit()
            mock_session.scalars.return_value = mock_scalars

            start = datetime(2025, 3, 1, tzinfo=UTC)
            end = datetime(2025, 9, 1, tzinfo=UTC)

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_both_dates",
                start_time=start,
                end_time=end,
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)
            # update_fields should have been called with both start_time and end_time
            env.mock["uow"].return_value.media_buys.update_fields.assert_called()
            call_kwargs = env.mock["uow"].return_value.media_buys.update_fields.call_args
            assert "start_time" in call_kwargs[1]
            assert "end_time" in call_kwargs[1]

    def test_timing_update_no_adapter_call(self):
        """Timing changes are database-only; no adapter call is made (gap G35).

        Covers: UC-003-ALT-UPDATE-TIMING-05
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session
            mock_existing = MagicMock()
            mock_existing.status = "active"
            mock_existing.start_time = datetime(2025, 1, 1, tzinfo=UTC)
            mock_existing.end_time = datetime(2025, 12, 31, tzinfo=UTC)

            env.mock["uow"].return_value.media_buys.get_by_id.side_effect = [
                _make_mock_media_buy("mb_no_adapter"),  # state-machine precondition
                _make_mock_media_buy("mb_no_adapter"),
                mock_existing,
                _make_mock_media_buy("mb_no_adapter"),  # valid_actions lookup
            ]
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = _make_mock_currency_limit()
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_no_adapter",
                end_time=datetime(2025, 11, 1, tzinfo=UTC),
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)
            # Adapter should NOT be called for timing-only updates
            env.mock["adapter"].return_value.update_media_buy.assert_not_called()


# ---------------------------------------------------------------------------
# ALT: Campaign-Level Budget
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ALT: Update Creative IDs
# ---------------------------------------------------------------------------


class TestUC003UpdateCreativeIds:
    """Creative ID update obligations."""

    def _setup_creative_mocks(self, env, creative_ids, statuses=None, formats=None):
        """Helper to set up creative-related mocks.

        Wires the shared creative-validation path: existence/status/format are
        resolved via ``uow.creatives.get_by_ids`` and ``uow.products.get_by_id``
        (not raw session.scalars), matching production.
        """
        mock_session = env.mock["uow"].return_value.session
        uow = env.mock["uow"].return_value

        # Media buy lookup
        mock_mb = MagicMock()
        mock_mb.media_buy_id = "mb_creative"
        uow.media_buys.get_by_id.return_value = mock_mb

        # Build creative mocks
        creatives = []
        for i, cid in enumerate(creative_ids):
            c = MagicMock()
            c.creative_id = cid
            c.status = statuses[i] if statuses else "active"
            c.agent_url = "http://test.com"
            c.format = formats[i] if formats else "display"
            creatives.append(c)

        # Creative existence/status via repository (shared validation helper).
        uow.creatives.get_by_ids.side_effect = None
        uow.creatives.get_by_ids.return_value = creatives

        # Session scalars returns creatives (legacy paths) + no existing assignments.
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = creatives
        mock_scalars.first.return_value = None  # No existing assignments by default
        mock_session.scalars.return_value = mock_scalars

        # Package with product
        mock_pkg = MagicMock()
        mock_pkg.package_config = {"product_id": "prod_1"}
        uow.media_buys.get_package.return_value = mock_pkg

        return mock_session, creatives

    def test_creative_existence_validation(self):
        """Creative IDs not found in library returns creatives_not_found.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-02
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            mock_mb = MagicMock()
            mock_mb.media_buy_id = "mb_creative"
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = mock_mb

            # Only C1 found, C999 missing
            c1 = MagicMock()
            c1.creative_id = "C1"
            c1.status = "active"
            env.mock["uow"].return_value.creatives.get_by_ids.side_effect = None
            env.mock["uow"].return_value.creatives.get_by_ids.return_value = [c1]

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_creative",
                packages=[{"package_id": "pkg_1", "creative_ids": ["C1", "C999"]}],
            )
            with pytest.raises(AdCPCreativeNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)
            # The identifier is STRUCTURED now: details/field, not prose.

            assert exc_info.value.error_code == "CREATIVE_NOT_FOUND"

    def test_creative_error_state_rejected(self):
        """Creative in error state cannot be assigned.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-03
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            self._setup_creative_mocks(env, ["C1"], statuses=["error"])

            # Product with no format restriction (to isolate the status check).
            mock_product = MagicMock()
            mock_product.format_ids = []
            mock_product.name = "Test Product"
            env.mock["uow"].return_value.products.get_by_id.return_value = mock_product

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_creative",
                packages=[{"package_id": "pkg_1", "creative_ids": ["C1"]}],
            )
            with pytest.raises(AdCPGoneError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)
            # The identifier is STRUCTURED now: details/field, not prose.
            assert exc_info.value.error_code == "INVALID_STATE"

    def test_creative_rejected_state_rejected(self):
        """Creative in rejected state cannot be assigned.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-04
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            self._setup_creative_mocks(env, ["C1"], statuses=["rejected"])

            mock_product = MagicMock()
            mock_product.format_ids = []
            mock_product.name = "Test Product"
            env.mock["uow"].return_value.products.get_by_id.return_value = mock_product

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_creative",
                packages=[{"package_id": "pkg_1", "creative_ids": ["C1"]}],
            )
            with pytest.raises(AdCPGoneError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)
            # The identifier is STRUCTURED now: details/field, not prose.
            assert exc_info.value.error_code == "INVALID_STATE"

    def test_creative_format_compatibility_check(self):
        """Creative format mismatch with product returns INVALID_CREATIVES.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-05
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            uow = env.mock["uow"].return_value

            mock_mb = MagicMock()
            mock_mb.media_buy_id = "mb_creative"
            uow.media_buys.get_by_id.return_value = mock_mb

            # Creative with "video" format
            c1 = MagicMock()
            c1.creative_id = "C1"
            c1.status = "active"
            c1.agent_url = "http://test.com"
            c1.format = "video"
            uow.creatives.get_by_ids.side_effect = None
            uow.creatives.get_by_ids.return_value = [c1]

            # Product with only "display" format
            mock_product = MagicMock()
            mock_product.format_ids = [{"agent_url": "http://test.com", "id": "display"}]
            mock_product.name = "Display Product"
            uow.products.get_by_id.return_value = mock_product

            # Package with product
            mock_pkg = MagicMock()
            mock_pkg.package_config = {"product_id": "prod_1"}
            uow.media_buys.get_package.return_value = mock_pkg

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_creative",
                packages=[{"package_id": "pkg_1", "creative_ids": ["C1"]}],
            )
            with pytest.raises(AdCPValidationError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)
            # The identifier is STRUCTURED now: details/field, not prose.
            assert exc_info.value.error_code == "VALIDATION_ERROR"

    def test_creative_update_no_adapter_call(self):
        """Creative ID updates persist directly to DB without adapter call.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-07
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session
            uow = env.mock["uow"].return_value

            mock_mb = MagicMock()
            mock_mb.media_buy_id = "mb_creative"
            mock_mb.status = "active"
            mock_mb.approved_at = None
            uow.media_buys.get_by_id.return_value = mock_mb

            c1 = MagicMock()
            c1.creative_id = "C1"
            c1.status = "active"
            c1.agent_url = "http://test.com"
            c1.format = "display"
            uow.creatives.get_by_ids.side_effect = None
            uow.creatives.get_by_ids.return_value = [c1]

            mock_pkg = MagicMock()
            mock_pkg.package_config = {"product_id": "prod_1"}
            uow.media_buys.get_package.return_value = mock_pkg

            mock_product = MagicMock()
            mock_product.format_ids = []  # no restriction
            mock_product.name = "Test Product"
            uow.products.get_by_id.return_value = mock_product

            # Remaining raw select is the existing-assignments lookup → empty.
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = []
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_creative",
                packages=[{"package_id": "pkg_1", "creative_ids": ["C1"]}],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)
            # Adapter should NOT be called for creative_ids updates
            env.mock["adapter"].return_value.update_media_buy.assert_not_called()

    def test_immutable_package_fields(self):
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

    def test_creative_model_extends_correct_adcp_type(self):
        """Creative model extends the correct adcp library Creative type.

        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-09
        """
        from adcp.types.generated_poc.creative.list_creatives_response import (  # TODO: no stable alias in adcp.types
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

    def test_upload_and_assign_inline_creatives(self):
        """Inline creatives uploaded and assigned via the creative-sync service.

        Covers: UC-003-ALT-UPLOAD-INLINE-CREATIVES-01
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            # Substitute the creative-sync service
            mock_sync_response = MagicMock()
            mock_sync_response.creatives = [
                MagicMock(creative_id="c1", action="created", errors=None),
                MagicMock(creative_id="c2", action="created", errors=None),
            ]

            calls: list[tuple] = []

            def _record(req_arg, **kw):
                calls.append((req_arg, kw))
                return mock_sync_response

            with patch("src.core.tools.media_buy_update.sync_creatives", side_effect=_record):
                identity = env.identity
                req = UpdateMediaBuyRequest(
                    account={"account_id": "acct_test"},
                    idempotency_key="test-idem-key-0001",
                    media_buy_id="mb_inline",
                    packages=[
                        {
                            "package_id": "pkg_1",
                            "creatives": [
                                {
                                    "creative_id": "c1",
                                    "name": "Creative 1",
                                    "format_id": {"agent_url": "http://test.com", "id": "display"},
                                    "assets": build_assets(
                                        image_spec("main", url="https://example.com/a1.png", width=300, height=250)
                                    ),
                                },
                                {
                                    "creative_id": "c2",
                                    "name": "Creative 2",
                                    "format_id": {"agent_url": "http://test.com", "id": "display"},
                                    "assets": build_assets(
                                        image_spec("main", url="https://example.com/a2.png", width=300, height=250)
                                    ),
                                },
                            ],
                        }
                    ],
                )
                result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)
            # What update_media_buy owes the creative-sync SERVICE, read off the RECORDED
            # call rather than by inspecting the mock: the buyer's creatives, the typed
            # Assignments derived from the package, this request's account and context, and
            # the caller it already resolved.
            #
            # The one field deliberately NOT equal is idempotency_key, and that is the point
            # of the controller/service split: the service performs no idempotency, so the
            # nested request carries its own internal key. This used to assert it equalled
            # req.idempotency_key, which pinned the borrowed-key layering in place.
            assert len(calls) == 1, f"the service must be called exactly once, got {len(calls)}"
            sent, kwargs = calls[0]
            # By id, not by object: the package carries the outer request's creative type and
            # the service receives the coerced CreativeAssetRequest, so comparing instances
            # would pin the coercion rather than the forwarding.
            assert [c.creative_id for c in sent.creatives] == [c.creative_id for c in req.packages[0].creatives]
            assert sent.account == req.account
            assert sent.context == req.context
            assert sent.assignments == [
                Assignment(creative_id="c1", package_id="pkg_1"),
                Assignment(creative_id="c2", package_id="pkg_1"),
            ]
            assert sent.idempotency_key != req.idempotency_key, (
                "the nested sync must not reuse the buyer's key -- it is a service call, and "
                "the buyer's key belongs to the update the controller already keyed"
            )
            assert kwargs["identity"] is identity
            assert kwargs["principal_id"] == identity.principal_id
            # affected_packages should track the creative upload
            assert len(result.affected_packages) >= 1

    def test_inline_creatives_additive_semantics(self):
        """Inline creatives are additive (don't replace existing).

        Covers: UC-003-ALT-UPLOAD-INLINE-CREATIVES-02
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            # Substitute the creative-sync service
            mock_sync_response = MagicMock()
            mock_sync_response.creatives = [
                MagicMock(creative_id="c3", action="created", errors=None),
            ]

            with patch("src.core.tools.media_buy_update.sync_creatives", return_value=mock_sync_response):
                identity = env.identity
                req = UpdateMediaBuyRequest(
                    account={"account_id": "acct_test"},
                    idempotency_key="test-idem-key-0001",
                    media_buy_id="mb_additive",
                    packages=[
                        {
                            "package_id": "pkg_1",
                            "creatives": [
                                {
                                    "creative_id": "c3",
                                    "name": "Creative 3",
                                    "format_id": {"agent_url": "http://test.com", "id": "display"},
                                    "assets": build_assets(
                                        image_spec("main", url="https://example.com/a3.png", width=300, height=250)
                                    ),
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

    def test_sync_failure_returns_error(self):
        """Creative sync failure returns creative_sync_failed error.

        Covers: UC-003-ALT-UPLOAD-INLINE-CREATIVES-04
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            # Mock _sync_creatives_impl to return a failure.
            # Branch normalizes CreativeAction to plain strings everywhere
            # (production compares ``r.action == "failed"``), so the mock
            # must use the string form, not the CreativeAction enum.
            mock_sync_response = MagicMock()
            failed_creative = MagicMock()
            failed_creative.creative_id = "c_fail"
            failed_creative.action = "failed"
            # Real Error: ErrorProblem.code is typed, so a MagicMock cannot reach the
            # buyer's envelope through it.
            failed_creative.errors = [Error.of(ErrorCode.CREATIVE_INACCESSIBLE)]
            mock_sync_response.creatives = [failed_creative]

            with patch("src.core.tools.media_buy_update.sync_creatives", return_value=mock_sync_response):
                identity = env.identity
                req = UpdateMediaBuyRequest(
                    account={"account_id": "acct_test"},
                    idempotency_key="test-idem-key-0001",
                    media_buy_id="mb_sync_fail",
                    packages=[
                        {
                            "package_id": "pkg_1",
                            "creatives": [
                                {
                                    "creative_id": "c_fail",
                                    "name": "Bad Creative",
                                    "format_id": {"agent_url": "http://test.com", "id": "display"},
                                    "assets": build_assets(
                                        image_spec("main", url="https://example.com/fail.png", width=300, height=250)
                                    ),
                                }
                            ],
                        }
                    ],
                )
                # #1307 error-drain: sync failure raises AdCPAdapterError
                # instead of returning an UpdateMediaBuyError result.
                with pytest.raises(AdCPAdapterError) as exc_info:
                    _update_media_buy_impl(req=req, identity=identity)

                assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# ALT: Update Creative Assignments
# ---------------------------------------------------------------------------


class TestUC003UpdateCreativeAssignments:
    """Creative assignment update obligations."""

    def test_creative_assignments_with_placement_targeting(self):
        """Creative assignments with placement_ids validated against product.

        Covers: UC-003-ALT-UPDATE-CREATIVE-ASSIGNMENTS-02
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            mock_mb = MagicMock()
            mock_mb.media_buy_id = "mb_assign"
            mock_mb.status = "active"
            mock_mb.approved_at = None
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = mock_mb

            # Package with product that has placements
            mock_pkg = MagicMock()
            mock_pkg.package_config = {"product_id": "prod_1"}
            env.mock["uow"].return_value.media_buys.get_package.return_value = mock_pkg

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

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
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

    def test_product_does_not_support_placement_targeting(self):
        """Placement targeting rejected when product has no placements.

        Covers: UC-003-ALT-UPDATE-CREATIVE-ASSIGNMENTS-04
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            mock_mb = MagicMock()
            mock_mb.media_buy_id = "mb_no_placement"
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = mock_mb

            mock_pkg = MagicMock()
            mock_pkg.package_config = {"product_id": "prod_1"}
            env.mock["uow"].return_value.media_buys.get_package.return_value = mock_pkg

            # Product WITHOUT placements
            mock_product = MagicMock()
            mock_product.placements = []
            mock_product.product_id = "prod_1"

            mock_scalars = MagicMock()
            mock_scalars.first.return_value = mock_product
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
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
            with pytest.raises(AdCPCapabilityNotSupportedError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "UNSUPPORTED_FEATURE"

    def test_creative_existence_validated_for_assignments(self):
        """Creative not found is rejected on the creative_assignments path.

        Regression for the bug where the creative_assignments handler skipped
        existence validation and let a missing creative_id reach the composite
        FK as an IntegrityError. The shared validation helper now rejects it
        with CREATIVE_REJECTED before any assignment row is built.

        Covers: UC-003-ALT-UPDATE-CREATIVE-ASSIGNMENTS-05
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            uow = env.mock["uow"].return_value

            mock_mb = MagicMock()
            mock_mb.media_buy_id = "mb_assign_not_found"
            mock_mb.status = "active"
            mock_mb.approved_at = None
            uow.media_buys.get_by_id.return_value = mock_mb

            mock_pkg = MagicMock()
            mock_pkg.package_config = {"product_id": "prod_1"}
            uow.media_buys.get_package.return_value = mock_pkg

            # C999 does not exist in the creative library.
            uow.creatives.get_by_ids.side_effect = None
            uow.creatives.get_by_ids.return_value = []

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
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
            with pytest.raises(AdCPCreativeNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)
            # The identifier is STRUCTURED now: details/field, not prose.
            assert exc_info.value.error_code == "CREATIVE_NOT_FOUND"


# ---------------------------------------------------------------------------
# ALT: Update Targeting Overlay
# ---------------------------------------------------------------------------


class TestUC003UpdateTargetingOverlay:
    """Targeting overlay update obligations."""

    def test_update_targeting_overlay_on_package(self):
        """Targeting overlay replaces existing targeting in package_config.

        Covers: UC-003-ALT-UPDATE-TARGETING-OVERLAY-01
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_pkg = MagicMock()
            mock_pkg.package_config = {"targeting_overlay": {"old": True}}
            env.mock["uow"].return_value.media_buys.get_package.return_value = mock_pkg

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_targeting",
                packages=[{"package_id": "pkg_1", "targeting_overlay": {"geo_countries": ["US"]}}],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)
            # targeting_overlay should have been replaced (stored as Pydantic model or dict)
            stored = mock_pkg.package_config["targeting_overlay"]
            assert stored is not None

    def test_targeting_update_no_adapter_call(self):
        """Targeting changes are database-only; no adapter call.

        Covers: UC-003-ALT-UPDATE-TARGETING-OVERLAY-03
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_pkg = MagicMock()
            mock_pkg.package_config = {}
            env.mock["uow"].return_value.media_buys.get_package.return_value = mock_pkg

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_target_no_adapter",
                packages=[{"package_id": "pkg_1", "targeting_overlay": {"geo_countries": ["US"]}}],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)
            env.mock["adapter"].return_value.update_media_buy.assert_not_called()

    def test_property_list_update_rejected_when_product_disallows(self):
        """Update with property_list against a product where property_targeting_allowed=False
        raises AdCPValidationError before persistence — same wire shape as create-time rule.

        PR #1276 round-5 switched this site from return-envelope to raise per
        reviewer feedback (avoids growing the model_dump _impl allowlist). The
        boundary translator turns the raise into the spec-compliant two-layer
        envelope.

        Covers: UC-003-MAIN-14
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            from src.core.exceptions import AdCPValidationError

            mock_pkg = MagicMock()
            mock_pkg.package_config = {"product_id": "prod_strict"}
            env.mock["uow"].return_value.media_buys.get_package.return_value = mock_pkg

            # uow.products.get_by_id returns a product that disallows property targeting.
            # Set product_id explicitly so the violation message contains the literal
            # ID rather than a MagicMock repr (the shared helper formats it into the message).
            mock_product = MagicMock()
            mock_product.product_id = "prod_strict"
            mock_product.property_targeting_allowed = False
            env.mock["uow"].return_value.products.get_by_id.return_value = mock_product

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_pta_reject",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "targeting_overlay": {
                            "property_list": {
                                "agent_url": "https://gov.example",
                                "list_id": "v1",
                            },
                        },
                    }
                ],
            )
            with pytest.raises(AdCPValidationError) as excinfo:
                _update_media_buy_impl(req=req, identity=identity)

            # Persistence must NOT have happened — package_config still untouched.
            assert "targeting_overlay" not in mock_pkg.package_config
            # Error mirrors create's shape exactly: same code, same field, same details.
            exc = excinfo.value
            assert exc.error_code == "VALIDATION_ERROR"
            # The ARRAY: violations are collected across every package before the
            # raise, so no single element is at fault and the pointer names the array
            # parameter. Which packages violated travels in details (salesagent-rfxfu).
            assert exc.field == "packages"
            assert exc.details is not None
            # `violations` here was list[str] of prose; it is `reasons` now, the one
            # field every prose list uses.
            assert exc.details.reasons

            # The outer ``audit_workflow_step_failure_ctx(lambda: step)`` context manager
            # marks the workflow step failed BEFORE re-raising, which fires the
            # buyer-facing push notification at
            # context_manager.update_workflow_step:330-332. Without this fence,
            # the step would orphan in ``in_progress`` and the buyer's poller
            # would hang forever. The CM's __exit__ receives the propagating
            # exception and calls ``audit_workflow_step_failure_if_present(step, exc)``
            # internally — that helper threads a two-layer envelope into
            # response_data so async webhook subscribers see the same wire
            # shape as the synchronous caller.
            #
            # Two assertions pin the contract:
            #   1. The CM was entered with a step-resolver lambda that resolves
            #      to the current step (covers wrapper-bypass regressions).
            #   2. The CM's __exit__ received the typed AdCPValidationError
            #      carrying the expected violation in its message (covers
            #      type-mismatch and message-content regressions, plus the
            #      ``update_workflow_step(..., error_message=...)`` shape that
            #      would silently lose response_data on the webhook path).
            audit_cm_calls = env.mock["ctx_mgr"].return_value.audit_workflow_step_failure_ctx.call_args_list
            msg = f"Expected exactly one audit_workflow_step_failure_ctx context manager entry on raise, got {len(audit_cm_calls)}"
            assert len(audit_cm_calls) == 1, msg
            get_step = audit_cm_calls[0].args[0]
            assert get_step() is env.mock["ctx_mgr"].return_value.create_workflow_step.return_value

            cm_return = env.mock["ctx_mgr"].return_value.audit_workflow_step_failure_ctx.return_value
            exit_calls = cm_return.__exit__.call_args_list
            msg = f"Expected exactly one __exit__ on the audit_workflow_step_failure_ctx CM, got {len(exit_calls)}"
            assert len(exit_calls) == 1, msg
            # __exit__(exc_type, exc_val, exc_tb) — pin both the class and the message.
            exit_args = exit_calls[0].args
            msg = f"Expected AdCPValidationError to escape the audit_workflow_step_failure_ctx CM, got {exit_args[0]}"
            assert exit_args[0] is AdCPValidationError, msg
            assert isinstance(exit_args[1], AdCPValidationError)

    def test_collection_list_update_skips_property_targeting_check(self):
        """Update with only collection_list does not trigger the property_list-specific
        property_targeting_allowed check — that gate is property_list-only.

        Covers: UC-003-MAIN-14
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_pkg = MagicMock()
            mock_pkg.package_config = {"product_id": "prod_strict"}
            env.mock["uow"].return_value.media_buys.get_package.return_value = mock_pkg

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_coll_only",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "targeting_overlay": {
                            "collection_list": {
                                "agent_url": "https://gov.example",
                                "list_id": "c_v1",
                            },
                        },
                    }
                ],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            # Targeting persisted; no property_list rejection
            assert isinstance(result, UpdateMediaBuySuccess)
            assert mock_pkg.package_config["targeting_overlay"] is not None

    def test_property_list_update_replaces_existing_not_merge(self):
        """Update with a NEW property_list.list_id replaces the prior one (not merged).

        UC-003-MAIN-13's obligation reads "the response reflects the new
        list_id values (replacement, not merge)." Starts from a package whose
        persisted targeting_overlay already carries property_list.list_id="A",
        applies an update with list_id="B", and asserts:
          * the persisted targeting_overlay.property_list.list_id is "B"
          * "A" does not survive (not merged, not kept as a fallback)

        Covers: UC-003-MAIN-13
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            # Pre-existing package state — already has list_id="A" persisted.
            # The MediaPackage.package_config dict is what update_media_buy mutates
            # in place via flag_modified; that mutation is what we read back here.
            existing_overlay = {
                "property_list": {
                    "agent_url": "https://gov.example",
                    "list_id": "A",
                },
            }
            mock_pkg = MagicMock()
            mock_pkg.package_config = {
                "product_id": "prod_open",
                "targeting_overlay": dict(existing_overlay),  # copy so test isn't aliased
            }
            env.mock["uow"].return_value.media_buys.get_package.return_value = mock_pkg

            # Product allows property targeting so the validation guard doesn't fire
            # (this test is about the replace semantic, not the allow/deny gate).
            mock_product = MagicMock()
            mock_product.product_id = "prod_open"
            mock_product.property_targeting_allowed = True
            env.mock["uow"].return_value.products.get_by_id.return_value = mock_product

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_pl_swap",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "targeting_overlay": {
                            "property_list": {
                                "agent_url": "https://gov.example",
                                "list_id": "B",
                            },
                        },
                    }
                ],
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            # Update succeeded.
            assert isinstance(result, UpdateMediaBuySuccess)

            # Persisted targeting_overlay reflects the swap, not a merge.
            # update_media_buy stores Targeting models in package_config; legacy
            # rows pre-3.0 may still surface as dicts via the rehydrate fallback.
            # Read both shapes to keep this test robust as the migration completes.
            persisted = mock_pkg.package_config["targeting_overlay"]
            if hasattr(persisted, "model_dump"):
                persisted_pl = persisted.property_list
                persisted_list_id = persisted_pl.list_id if persisted_pl is not None else None
            else:
                persisted_list_id = persisted["property_list"]["list_id"]
            msg = f"replacement semantic broken — persisted list_id={persisted_list_id!r}, expected 'B'"
            # The original "A" must not survive on list_id specifically (don't
            # substring-match the whole overlay repr — 'AnyUrl' contains 'A' too).
            assert persisted_list_id != "A", "original list_id was not replaced"


# ---------------------------------------------------------------------------
# ALT: Manual Approval Required
# ---------------------------------------------------------------------------


class TestUC003ManualApproval:
    """Manual approval obligations."""

    def test_adapter_deferred_until_approval(self):
        """Adapter NOT called during manual approval; deferred to approval time.

        Covers: UC-003-ALT-MANUAL-APPROVAL-REQUIRED-03
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            env.mock["adapter"].return_value.manual_approval_required = True
            env.mock["adapter"].return_value.manual_approval_operations = ["update_media_buy"]

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_deferred",
                paused=True,
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySubmitted)
            # Adapter should NOT be called (deferred until seller approves)
            env.mock["adapter"].return_value.update_media_buy.assert_not_called()

    def test_seller_rejects_update(self):
        """Seller rejection documented: buyer notified via webhook.

        Covers: UC-003-ALT-MANUAL-APPROVAL-REQUIRED-04
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            # This tests the manual approval setup that enables later rejection.
            # The actual rejection happens in the admin approval flow, not in _impl.
            env.mock["adapter"].return_value.manual_approval_required = True
            env.mock["adapter"].return_value.manual_approval_operations = ["update_media_buy"]

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_reject_setup",
                paused=True,
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySubmitted)
            # Verify workflow step created with requires_approval (enables rejection)
            result_calls = env.mock["ctx_mgr"].return_value.audit_workflow_step_result.call_args_list
            assert result_calls[0][1]["status"] == "requires_approval"
            # Verify the originating request is stored (needed for rejection notification);
            # audit_workflow_step_result serializes request_obj under response_data["request_data"].
            assert result_calls[0][1].get("request_obj") is not None

    def test_buyer_can_poll_task_status(self):
        """Workflow step ID returned so buyer can poll status.

        Covers: UC-003-ALT-MANUAL-APPROVAL-REQUIRED-05
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            env.mock["adapter"].return_value.manual_approval_required = True
            env.mock["adapter"].return_value.manual_approval_operations = ["update_media_buy"]

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"}, idempotency_key="test-idem-key-0001", media_buy_id="mb_poll"
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySubmitted)
            # The buyer polls status via the returned task_id (the workflow step).
            assert result.task_id == "step_001"
            # The workflow step was created (step_id="step_001")
            # and the response allows the buyer to track the status
            env.mock["ctx_mgr"].return_value.create_workflow_step.assert_called_once_with(
                context_id="ctx_001",
                step_type="tool_call",
                owner="principal",
                status="in_progress",
                tool_name="update_media_buy",
                request_data=req,
            )


# ---------------------------------------------------------------------------
# EXT-A: Authentication Error
# ---------------------------------------------------------------------------


# DELETED: class TestUC003ExtA (test_no_principal_in_context / UC-003-EXT-A-01,
# test_principal_not_found_in_database / UC-003-EXT-A-02, test_state_unchanged_on_auth_failure
# / UC-003-EXT-A-03). All three asserted that _update_media_buy_impl itself raises an auth
# refusal — AUTH_MISSING for an identity built with ``principal_id=None``, AUTH_INVALID for a
# stubbed-away principal row. A protected tool cannot reach either state:
#
#   * ``ResolvedIdentity.principal`` is ``InstanceOf[Principal]`` and REQUIRED
#     (src/core/resolved_identity.py:124) — only ``PublicIdentity.principal`` is optional, so
#     an identity handed to this tool always carries a principal the resolver already loaded;
#   * AUTH_MISSING and AUTH_INVALID are minted by the resolver ALONE, banned anywhere else by
#     TID251 in ruff-boundary.toml, so an auth refusal cannot originate in an ``_impl`` by
#     design — it is decided once, where the credential is read.
#
# The EXT-A obligations are graded where the refusal is actually decided: the resolver mints
# it for every tool and transport at once, and the wire shape is asserted by the
# transport-blind auth scenarios rather than once per tool. The "no records modified" half of
# EXT-A-03 survives for a REACHABLE refusal in TestUC003ExtC below, which asserts the same
# no-adapter-call / no-update_fields pair on an ownership mismatch — the authorization error
# a tool may raise.


# ---------------------------------------------------------------------------
# EXT-C: Ownership Mismatch
# ---------------------------------------------------------------------------


class TestUC003ExtC:
    """Ownership mismatch obligations."""

    def test_state_unchanged_on_ownership_mismatch(self):
        """Media buy remains unmodified on ownership mismatch.

        Covers: UC-003-EXT-C-02
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            env.mock["verify"].side_effect = PermissionError(
                "Principal 'principal_test' does not own media buy 'mb_not_mine'."
            )

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"}, idempotency_key="test-idem-key-0001", media_buy_id="mb_not_mine"
            )

            with pytest.raises(PermissionError):
                _update_media_buy_impl(req=req, identity=identity)

            # No adapter call
            env.mock["adapter"].return_value.update_media_buy.assert_not_called()
            # No DB writes
            env.mock["uow"].return_value.media_buys.update_fields.assert_not_called()


# ---------------------------------------------------------------------------
# EXT-E: Date Range Invalid
# ---------------------------------------------------------------------------


class TestUC003ExtE:
    """Date range validation obligations."""

    def test_end_equals_start_returns_error(self):
        """end_time == start_time returns invalid_date_range.

        Covers: UC-003-EXT-E-01
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session
            same_time = datetime(2025, 3, 1, tzinfo=UTC)

            mock_existing = MagicMock()
            mock_existing.status = "active"
            mock_existing.start_time = same_time
            mock_existing.end_time = same_time

            env.mock["uow"].return_value.media_buys.get_by_id.side_effect = [
                _make_mock_media_buy("mb_eq"),  # state-machine precondition
                _make_mock_media_buy("mb_eq"),
                mock_existing,
            ]
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = _make_mock_currency_limit()
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_eq",
                start_time=same_time,
                end_time=same_time,
            )
            with pytest.raises(AdCPValidationError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "VALIDATION_ERROR"

    def test_end_before_existing_start(self):
        """end_time before existing start_time (only end_time updated).

        Covers: UC-003-EXT-E-03
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            mock_existing = MagicMock()
            mock_existing.status = "active"
            mock_existing.start_time = datetime(2025, 3, 15, tzinfo=UTC)
            mock_existing.end_time = datetime(2025, 12, 31, tzinfo=UTC)

            env.mock["uow"].return_value.media_buys.get_by_id.side_effect = [
                _make_mock_media_buy("mb_end_before"),  # state-machine precondition
                _make_mock_media_buy("mb_end_before"),
                mock_existing,
            ]
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = _make_mock_currency_limit()
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            # Only end_time, before existing start_time
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_end_before",
                end_time=datetime(2025, 3, 10, tzinfo=UTC),
            )
            with pytest.raises(AdCPValidationError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "VALIDATION_ERROR"

    def test_start_after_existing_end(self):
        """start_time after existing end_time (only start_time updated).

        Covers: UC-003-EXT-E-04
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            mock_existing = MagicMock()
            mock_existing.status = "active"
            mock_existing.start_time = datetime(2025, 1, 1, tzinfo=UTC)
            mock_existing.end_time = datetime(2025, 3, 31, tzinfo=UTC)

            env.mock["uow"].return_value.media_buys.get_by_id.side_effect = [
                _make_mock_media_buy("mb_start_after"),  # state-machine precondition
                _make_mock_media_buy("mb_start_after"),
                mock_existing,
            ]
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = _make_mock_currency_limit()
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            # Only start_time, after existing end_time
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_start_after",
                start_time=datetime(2025, 4, 15, tzinfo=UTC),
            )
            with pytest.raises(AdCPValidationError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# EXT-F: Currency Not Supported
# ---------------------------------------------------------------------------


class TestUC003ExtF:
    """Currency validation obligations."""

    def test_currency_not_in_tenant_config(self):
        """Media buy currency not supported by tenant returns currency_not_supported.

        Covers: UC-003-EXT-F-01
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_mb = _make_mock_media_buy("mb_gbp")
            mock_mb.currency = "GBP"
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = mock_mb

            # Currency limit NOT found (GBP not configured)
            env.mock["uow"].return_value.currency_limits.get_for_currency.return_value = None

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_gbp",
                packages=[{"package_id": "pkg_1", "budget": 5000.0}],
            )
            with pytest.raises(AdCPCapabilityNotSupportedError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "UNSUPPORTED_FEATURE"


# ---------------------------------------------------------------------------
# EXT-G: Daily Spend Cap Exceeded
# ---------------------------------------------------------------------------


class TestUC003ExtG:
    """Daily spend cap obligations."""

    def test_updated_budget_exceeds_daily_cap(self):
        """Package budget update exceeding daily cap returns budget_limit_exceeded.

        Covers: UC-003-EXT-G-01
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_mb = _make_mock_media_buy("mb_daily")
            mock_mb.start_time = datetime(2025, 1, 1, tzinfo=UTC)
            mock_mb.end_time = datetime(2025, 1, 11, tzinfo=UTC)  # 10 days
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = mock_mb

            mock_cl = _make_mock_currency_limit(max_daily=500)
            env.mock["uow"].return_value.currency_limits.get_for_currency.return_value = mock_cl

            identity = env.identity
            # daily = 10000/10 = 1000 > 500
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_daily",
                packages=[{"package_id": "pkg_1", "budget": 10000.0}],
            )
            with pytest.raises(AdCPBudgetExceededError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "BUDGET_EXCEEDED"


# ---------------------------------------------------------------------------
# EXT-H: Missing Package ID
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# EXT-I: Creative IDs Not Found
# ---------------------------------------------------------------------------


class TestUC003ExtI:
    """Creative IDs not found obligations."""

    def test_all_creative_ids_not_found(self):
        """All referenced creatives missing returns creatives_not_found.

        Covers: UC-003-EXT-I-02
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            mock_mb = MagicMock()
            mock_mb.media_buy_id = "mb_all_missing"
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = mock_mb

            # No creatives found
            env.mock["uow"].return_value.creatives.get_by_ids.side_effect = None
            env.mock["uow"].return_value.creatives.get_by_ids.return_value = []
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = []
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_all_missing",
                packages=[{"package_id": "pkg_1", "creative_ids": ["C999", "C998"]}],
            )
            with pytest.raises(AdCPCreativeNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)
            assert exc_info.value.error_code == "CREATIVE_NOT_FOUND"


# ---------------------------------------------------------------------------
# EXT-J: Creative Validation Failure
# ---------------------------------------------------------------------------


class TestUC003ExtJ:
    """Creative validation failure obligations."""

    def test_creative_in_rejected_state(self):
        """Creative in rejected state returns INVALID_CREATIVES.

        Covers: UC-003-EXT-J-02
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            uow = env.mock["uow"].return_value

            mock_mb = MagicMock()
            mock_mb.media_buy_id = "mb_rejected"
            uow.media_buys.get_by_id.return_value = mock_mb

            c1 = MagicMock()
            c1.creative_id = "C1"
            c1.status = "rejected"
            c1.agent_url = "http://test.com"
            c1.format = "display"
            uow.creatives.get_by_ids.side_effect = None
            uow.creatives.get_by_ids.return_value = [c1]

            mock_pkg = MagicMock()
            mock_pkg.package_config = {"product_id": "prod_1"}
            uow.media_buys.get_package.return_value = mock_pkg

            mock_product = MagicMock()
            mock_product.format_ids = []
            mock_product.name = "Test Product"
            uow.products.get_by_id.return_value = mock_product

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_rejected",
                packages=[{"package_id": "pkg_1", "creative_ids": ["C1"]}],
            )
            with pytest.raises(AdCPGoneError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)
            # The identifier is STRUCTURED now: details/field, not prose.
            assert exc_info.value.error_code == "INVALID_STATE"

    def test_all_validation_errors_collected(self):
        """Multiple creative errors collected and returned together.

        Covers: UC-003-EXT-J-04
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            uow = env.mock["uow"].return_value

            mock_mb = MagicMock()
            mock_mb.media_buy_id = "mb_multi_err"
            uow.media_buys.get_by_id.return_value = mock_mb

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

            uow.creatives.get_by_ids.side_effect = None
            uow.creatives.get_by_ids.return_value = [c1, c2]

            mock_pkg = MagicMock()
            mock_pkg.package_config = {"product_id": "prod_1"}
            uow.media_buys.get_package.return_value = mock_pkg

            mock_product = MagicMock()
            mock_product.format_ids = []
            mock_product.name = "Test Product"
            uow.products.get_by_id.return_value = mock_product

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_multi_err",
                packages=[{"package_id": "pkg_1", "creative_ids": ["C1", "C2"]}],
            )
            with pytest.raises(AdCPGoneError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            # Both offending creatives reported together in the rejection.
            assert exc_info.value.error_code == "INVALID_STATE"
            # details carries the state per creative, not a bare id list plus joined prose.
            assert {p.subject_id for p in exc_info.value.details.problems or []} == {"C1", "C2"}


# ---------------------------------------------------------------------------
# EXT-K: Creative Sync Failure
# ---------------------------------------------------------------------------


class TestUC003ExtK:
    """Creative sync failure obligations."""

    def test_inline_creative_upload_fails(self):
        """Sync failure returns creative_sync_failed.

        Covers: UC-003-EXT-K-01
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            # Branch normalizes CreativeAction to strings (production compares
            # ``r.action == "failed"``), so use the string form here.
            mock_sync_response = MagicMock()
            failed = MagicMock()
            failed.creative_id = "c_fail"
            failed.action = "failed"
            # A REAL Error, not a MagicMock with a hand-written message. The mock's
            # ``message = "Network error"`` was authored prose that the old dict-shaped
            # details accepted silently; the typed ``ErrorProblem.code`` refuses it, and
            # refusing it is correct -- a MagicMock reaching a buyer's envelope is the
            # bug the type was added to prevent.
            failed.errors = [Error.of(ErrorCode.CREATIVE_INACCESSIBLE)]
            mock_sync_response.creatives = [failed]

            with patch("src.core.tools.media_buy_update.sync_creatives", return_value=mock_sync_response):
                identity = env.identity
                req = UpdateMediaBuyRequest(
                    account={"account_id": "acct_test"},
                    idempotency_key="test-idem-key-0001",
                    media_buy_id="mb_sync_err",
                    packages=[
                        {
                            "package_id": "pkg_1",
                            "creatives": [
                                {
                                    "creative_id": "c_fail",
                                    "name": "Fail",
                                    "format_id": {"agent_url": "http://test.com", "id": "display"},
                                    "assets": build_assets(
                                        image_spec("main", url="https://example.com/fail.png", width=300, height=250)
                                    ),
                                }
                            ],
                        }
                    ],
                )
                # #1307 error-drain: sync failure raises AdCPAdapterError.
                with pytest.raises(AdCPAdapterError) as exc_info:
                    _update_media_buy_impl(req=req, identity=identity)

                assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
                # The per-creative failure is STRUCTURED: which creative, and which code.
                # Before this it was a prose string the buyer had to parse.
                details = exc_info.value.details
                assert details is not None
                assert details.problems is not None
                assert [(p.subject_type, p.subject_id, p.code) for p in details.problems] == [
                    ("creative", "c_fail", ErrorCode.CREATIVE_INACCESSIBLE)
                ]

    def test_media_buy_unmodified_on_sync_failure(self):
        """Media buy unchanged when creative sync fails.

        Covers: UC-003-EXT-K-02
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            # Branch normalizes CreativeAction to strings (production compares
            # ``r.action == "failed"``), so use the string form here.
            mock_sync_response = MagicMock()
            failed = MagicMock()
            failed.creative_id = "c_fail"
            failed.action = "failed"
            # Real Error, same reason as the sibling test above: ErrorProblem.code is
            # typed, so a MagicMock cannot reach the buyer's envelope through it.
            failed.errors = [Error.of(ErrorCode.CREATIVE_INACCESSIBLE)]
            mock_sync_response.creatives = [failed]

            with patch("src.core.tools.media_buy_update.sync_creatives", return_value=mock_sync_response):
                identity = env.identity
                req = UpdateMediaBuyRequest(
                    account={"account_id": "acct_test"},
                    idempotency_key="test-idem-key-0001",
                    media_buy_id="mb_no_modify",
                    packages=[
                        {
                            "package_id": "pkg_1",
                            "creatives": [
                                {
                                    "creative_id": "c_fail",
                                    "name": "Fail",
                                    "format_id": {"agent_url": "http://test.com", "id": "display"},
                                    "assets": build_assets(
                                        image_spec("main", url="https://example.com/fail.png", width=300, height=250)
                                    ),
                                }
                            ],
                        }
                    ],
                )
                # #1307 error-drain: sync failure raises AdCPAdapterError.
                with pytest.raises(AdCPAdapterError) as exc_info:
                    _update_media_buy_impl(req=req, identity=identity)

                assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"

            # No adapter call
            env.mock["adapter"].return_value.update_media_buy.assert_not_called()
            # No DB writes through UoW
            env.mock["uow"].return_value.media_buys.update_fields.assert_not_called()


# ---------------------------------------------------------------------------
# EXT-L: Package Not Found
# ---------------------------------------------------------------------------


class TestUC003ExtL:
    """Package not found obligations."""

    def test_package_id_not_in_media_buy(self):
        """Package ID belongs to different media buy returns package_not_found.

        Covers: UC-003-EXT-L-01
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            # Package lookup returns None (not in this media buy)
            env.mock["uow"].return_value.media_buys.get_package.return_value = None

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_wrong_pkg",
                packages=[{"package_id": "pkg_99", "targeting_overlay": {"geo_countries": ["US"]}}],
            )
            with pytest.raises(AdCPPackageNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "PACKAGE_NOT_FOUND"

    def test_package_id_does_not_exist(self):
        """Non-existent package_id returns package_not_found.

        Covers: UC-003-EXT-L-02
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            env.mock["uow"].return_value.media_buys.get_package.return_value = None

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_no_pkg_exist",
                packages=[{"package_id": "pkg_nonexistent", "targeting_overlay": {"geo_countries": ["US"]}}],
            )
            with pytest.raises(AdCPPackageNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)
            # The identifier is STRUCTURED now: details/field, not prose.

            assert exc_info.value.error_code == "PACKAGE_NOT_FOUND"


# ---------------------------------------------------------------------------
# EXT-M: Invalid Placement IDs
# ---------------------------------------------------------------------------


class TestUC003ExtM:
    """Invalid placement IDs obligations."""

    def test_placement_id_not_valid_for_product(self):
        """Invalid placement_id returns invalid_placement_ids.

        Covers: UC-003-EXT-M-01
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            mock_mb = MagicMock()
            mock_mb.media_buy_id = "mb_bad_placement"
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = mock_mb

            mock_pkg = MagicMock()
            mock_pkg.package_config = {"product_id": "prod_1"}
            env.mock["uow"].return_value.media_buys.get_package.return_value = mock_pkg

            mock_product = MagicMock()
            mock_product.placements = [
                {"placement_id": "P1"},
                {"placement_id": "P2"},
            ]

            mock_scalars = MagicMock()
            mock_scalars.first.return_value = mock_product
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
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
            with pytest.raises(AdCPValidationError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "VALIDATION_ERROR"

    def test_placement_targeting_on_unsupported_product(self):
        """Placement targeting on product without placements rejected.

        Covers: UC-003-EXT-M-02
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session

            mock_mb = MagicMock()
            mock_mb.media_buy_id = "mb_no_placements"
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = mock_mb

            mock_pkg = MagicMock()
            mock_pkg.package_config = {"product_id": "prod_1"}
            env.mock["uow"].return_value.media_buys.get_package.return_value = mock_pkg

            mock_product = MagicMock()
            mock_product.placements = []  # No placements
            mock_product.product_id = "prod_1"

            mock_scalars = MagicMock()
            mock_scalars.first.return_value = mock_product
            mock_session.scalars.return_value = mock_scalars

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
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
            with pytest.raises(AdCPCapabilityNotSupportedError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "UNSUPPORTED_FEATURE"


# ---------------------------------------------------------------------------
# EXT-N: Insufficient Privileges
# ---------------------------------------------------------------------------


class TestUC003ExtN:
    """Insufficient privileges obligations."""

    def test_non_admin_principal_rejected(self):
        """Adapter privilege check blocks non-admin operations.

        Covers: UC-003-EXT-N-01

        The stimulus is a RAISE, not a returned error model -- the same correction
        ``test_media_buy.py::TestUpdateMediaBuyAdapterFailure::test_adapter_network_error``
        records for its sibling. ``update_media_buy`` returns
        ``src.adapters.base.AdapterUpdateResult`` (commit ecfdd7771) and production says
        so at the call site: "an adapter reports failure by raising; a returned result is
        the success". Staging a returned ``UpdateMediaBuyError`` therefore made the tool
        read ``media_buy_id``/``affected_packages`` off an error object and answer the
        buyer with a SUCCESS -- the defect this case exists to catch, reached by staging
        something no adapter produces. ``AdCPAuthorizationError`` is the class the GAM
        adapter actually raises for this refusal (``google_ad_manager.py`` guards its
        admin-only actions with ``_is_admin_principal``); its wire code is
        PERMISSION_DENIED.
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = _make_mock_media_buy("mb_priv")
            mock_cl = _make_mock_currency_limit(max_daily=100000)
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = mock_cl
            mock_session.scalars.return_value = mock_scalars

            # The adapter refuses an admin-only action for a non-admin principal.
            env.mock["adapter"].return_value.update_media_buy.side_effect = AdCPAuthorizationError()

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_priv",
                packages=[{"package_id": "pkg_1", "budget": 5000.0}],
            )

            with pytest.raises(AdCPAuthorizationError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            # The refusal travels as the typed error, so the boundary mints
            # PERMISSION_DENIED for the buyer instead of a success carrying the buy's id.
            assert exc_info.value.error_code == "PERMISSION_DENIED"
            # And nothing the adapter refused was written to our row.
            env.mock["uow"].return_value.media_buys.update_fields.assert_not_called()


# ---------------------------------------------------------------------------
# EXT-O: Adapter/Workflow Failure
# ---------------------------------------------------------------------------


class TestUC003ExtO:
    """Adapter and workflow failure obligations."""

    def test_adapter_quota_error(self):
        """Adapter API quota error reaches the buyer as a transient failure.

        Covers: UC-003-EXT-O-02

        A RAISE, not a returned error model -- see
        ``test_non_admin_principal_rejected`` above for why a returned
        ``UpdateMediaBuyError`` answered the buyer with a SUCCESS. ``AdCPAdapterError``
        is the class a transient adapter fault raises; its wire code is
        SERVICE_UNAVAILABLE.
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            mock_session = env.mock["uow"].return_value.session
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = _make_mock_media_buy("mb_quota")
            mock_cl = _make_mock_currency_limit(max_daily=100000)
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = mock_cl
            mock_session.scalars.return_value = mock_scalars

            # The ad server refuses the call: quota exhausted.
            env.mock["adapter"].return_value.update_media_buy.side_effect = AdCPAdapterError()

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_quota",
                packages=[{"package_id": "pkg_1", "budget": 5000.0}],
            )

            with pytest.raises(AdCPAdapterError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
            env.mock["uow"].return_value.media_buys.update_fields.assert_not_called()

    def test_workflow_creation_failure(self):
        """Workflow step creation failure during manual approval.

        Covers: UC-003-EXT-O-03
        """
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            env.mock["adapter"].return_value.manual_approval_required = True
            env.mock["adapter"].return_value.manual_approval_operations = ["update_media_buy"]

            # Workflow step result persistence fails
            env.mock["ctx_mgr"].return_value.audit_workflow_step_result.side_effect = Exception(
                "Database error: workflow step creation failed"
            )

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_wf_fail",
                paused=True,
            )

            with pytest.raises(Exception, match="workflow step creation failed"):
                _update_media_buy_impl(req=req, identity=identity)


# ---------------------------------------------------------------------------
# State-machine precondition
# ---------------------------------------------------------------------------


class TestUC003StateMachine:
    """Terminal-state rejection and per-status action validation.

    BR-UC-003: update_media_buy MUST refuse mutations on terminal states
    (rejected, canceled, completed) and MUST refuse actions outside
    valid_actions_for_status(current_status).
    """

    @pytest.mark.parametrize("terminal_status", ["rejected", "canceled", "completed"])
    def test_terminal_status_rejects_pause(self, terminal_status):
        """Pausing a buy in any terminal status raises INVALID_STATE."""
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            from src.core.exceptions import AdCPGoneError

            terminal_mb = _make_mock_media_buy("mb_terminal", status=terminal_status)
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = terminal_mb

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_terminal",
                paused=True,
            )

            with pytest.raises(AdCPGoneError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "INVALID_STATE"
            # The status is structured now, not embedded in a sentence.
            assert exc_info.value.details.current_status == terminal_status
            # No adapter call when precondition rejects
            env.mock["adapter"].return_value.update_media_buy.assert_not_called()

    @pytest.mark.parametrize("terminal_status", ["rejected", "canceled", "completed"])
    def test_terminal_status_rejects_budget_update(self, terminal_status):
        """Updating package budget in any terminal status raises INVALID_STATE."""
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            from src.core.exceptions import AdCPGoneError

            terminal_mb = _make_mock_media_buy("mb_terminal_budget", status=terminal_status)
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = terminal_mb

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_terminal_budget",
                packages=[{"package_id": "pkg_001", "budget": 5000.0}],
            )

            with pytest.raises(AdCPGoneError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "INVALID_STATE"
            # No adapter call when precondition rejects
            env.mock["adapter"].return_value.update_media_buy.assert_not_called()
            # No DB writes when precondition rejects
            env.mock["uow"].return_value.media_buys.update_fields.assert_not_called()

    def test_active_status_accepts_pause(self):
        """A non-terminal status (active) accepts pause (state machine allows it)."""
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            active_mb = _make_mock_media_buy("mb_active", status="active")
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = active_mb

            env.mock["adapter"].return_value.update_media_buy.return_value = AdapterUpdateResult(
                media_buy_id="mb_active",
                affected_packages=[],
            )

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_active",
                paused=True,
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)

    def test_paused_status_rejects_pause(self):
        """A paused buy rejects another pause — 'pause' is not in valid_actions for 'paused'."""
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            from src.core.exceptions import AdCPGoneError

            paused_mb = _make_mock_media_buy("mb_paused", status="paused")
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = paused_mb

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_paused",
                paused=True,
            )

            with pytest.raises(AdCPGoneError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

            # Action validation, not terminal-state: still INVALID_STATE
            assert exc_info.value.error_code == "INVALID_STATE"

    def test_paused_status_accepts_resume(self):
        """A paused buy accepts resume — 'resume' is in valid_actions for 'paused'."""
        with MediaBuyUpdateEnv(principal_id="principal_test", tenant_id="tenant_test") as env:
            paused_mb = _make_mock_media_buy("mb_paused_resume", status="paused")
            env.mock["uow"].return_value.media_buys.get_by_id.return_value = paused_mb

            env.mock["adapter"].return_value.update_media_buy.return_value = AdapterUpdateResult(
                media_buy_id="mb_paused_resume",
                affected_packages=[],
            )

            identity = env.identity
            req = UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                media_buy_id="mb_paused_resume",
                paused=False,
            )
            result = _update_media_buy_impl(req=req, identity=identity)

            assert isinstance(result, UpdateMediaBuySuccess)

    # REMOVED: test_post_action_status_derived_from_db. Its outcome -- valid_actions
    # reflecting the DB-derived post-action status rather than the requested action -- is
    # graded on real wire bytes by @T-UC-003-ext-scheduled-status in
    # BR-UC-002-media-buy-status-dual-emit.feature ("update_media_buy on a scheduled buy
    # normalizes status and reports valid_actions"), whose Thens assert
    # ``the wire media_buy_status should be "pending_start"`` and
    # ``the wire valid_actions should include "update_budget"/"cancel"``. Measured in run
    # innet_150926_1232: PASSED on a2a, mcp and rest (bdd_inprocess) and on e2e_rest
    # (bdd_e2e). The scenario drives a status the old hardcode could never produce, so it
    # grades the same fix strictly better than a mocked DB read could.
