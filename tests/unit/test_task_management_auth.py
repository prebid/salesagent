"""Unit tests for F-03: task management tools require authenticated principal.

Covers the vulnerability where list_tasks / get_task / complete_task accepted
requests that had a resolved tenant (via localhost fallback) but no principal_id.
An unauthenticated caller could read or mutate workflow task state.

The fix: all three _impl functions now check identity.is_authenticated
immediately after the tenant check.
"""

import pytest

from src.core.exceptions import AdCPAuthenticationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.tools.task_management import complete_task, get_task, list_tasks


def _identity_no_principal() -> ResolvedIdentity:
    """Simulate the localhost tenant-fallback case: tenant is resolved, principal is not."""
    return ResolvedIdentity(
        principal_id=None,
        tenant={"tenant_id": "test-tenant", "name": "Test"},
        protocol="mcp",
    )


def _identity_with_principal() -> ResolvedIdentity:
    """Fully authenticated identity."""
    return ResolvedIdentity(
        principal_id="principal-abc",
        tenant={"tenant_id": "test-tenant", "name": "Test"},
        protocol="mcp",
    )


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_no_principal_raises_auth_error() -> None:
    """list_tasks must reject identity that has tenant but no principal_id."""
    with pytest.raises(AdCPAuthenticationError) as exc_info:
        await list_tasks(identity=_identity_no_principal())

    assert "Authentication required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_list_tasks_no_identity_raises_auth_error() -> None:
    """list_tasks must reject a completely missing identity."""
    with pytest.raises(AdCPAuthenticationError):
        await list_tasks(identity=None)


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_no_principal_raises_auth_error() -> None:
    """get_task must reject identity that has tenant but no principal_id."""
    with pytest.raises(AdCPAuthenticationError) as exc_info:
        await get_task(task_id="step-123", identity=_identity_no_principal())

    assert "Authentication required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_task_no_identity_raises_auth_error() -> None:
    """get_task must reject a completely missing identity."""
    with pytest.raises(AdCPAuthenticationError):
        await get_task(task_id="step-123", identity=None)


# ---------------------------------------------------------------------------
# complete_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_task_no_principal_raises_auth_error() -> None:
    """complete_task must reject identity that has tenant but no principal_id."""
    with pytest.raises(AdCPAuthenticationError) as exc_info:
        await complete_task(task_id="step-123", identity=_identity_no_principal())

    assert "Authentication required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_complete_task_no_identity_raises_auth_error() -> None:
    """complete_task must reject a completely missing identity."""
    with pytest.raises(AdCPAuthenticationError):
        await complete_task(task_id="step-123", identity=None)


# ---------------------------------------------------------------------------
# Regression: authenticated identity is not affected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_authenticated_proceeds_past_auth_check(
    mocker: pytest.FixtureRequest,
) -> None:
    """Authenticated identity must pass the auth check and proceed to DB access."""
    mock_uow = mocker.patch("src.core.tools.task_management.WorkflowUoW")
    mock_uow.return_value.__enter__.return_value.workflows.count_by_tenant.return_value = 0
    mock_uow.return_value.__enter__.return_value.workflows.list_by_tenant.return_value = []
    mock_uow.return_value.__enter__.return_value.workflows.get_mappings_for_steps.return_value = {}

    result = await list_tasks(identity=_identity_with_principal())

    assert result["tasks"] == []
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_get_task_authenticated_proceeds_past_auth_check(
    mocker: pytest.FixtureRequest,
) -> None:
    """Authenticated identity must pass the auth check and proceed to DB access."""
    mock_uow = mocker.patch("src.core.tools.task_management.WorkflowUoW")
    mock_uow.return_value.__enter__.return_value.workflows.get_by_step_id.return_value = None

    with pytest.raises(ValueError, match="not found"):
        await get_task(task_id="step-999", identity=_identity_with_principal())


@pytest.mark.asyncio
async def test_complete_task_authenticated_proceeds_past_auth_check(
    mocker: pytest.FixtureRequest,
) -> None:
    """Authenticated identity must pass the auth check and proceed to DB access."""
    mock_uow = mocker.patch("src.core.tools.task_management.WorkflowUoW")
    mock_uow.return_value.__enter__.return_value.workflows.get_by_step_id.return_value = None

    with pytest.raises(ValueError, match="not found"):
        await complete_task(task_id="step-999", identity=_identity_with_principal())
