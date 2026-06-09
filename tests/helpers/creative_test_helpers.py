"""Shared creative test helpers.

DRY extraction for creative sync and serialization test utilities shared across:
- test_creative_coverage_gaps
- test_sync_creatives_format_validation
- test_creative_response_serialization
- test_list_creatives_serialization
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, patch

from tests.factories.creative_asset import make_image_assets
from tests.harness import make_mock_uow


def make_creative_dict(creative_id: str = "c1", name: str = "Test Banner") -> dict:
    """Build a valid creative dict with SDK 5.7 asset structure."""
    return {
        "creative_id": creative_id,
        "name": name,
        "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
        "assets": make_image_assets("banner_image", "https://example.com/banner.png"),
        "variants": [],
    }


def make_creative_uow(*, include_assignments: bool = False):
    """Create a mock CreativeUoW with creative_repo returning sensible defaults.

    Args:
        include_assignments: If True, include an assignments mock repo.
    """
    mock_creative_repo = MagicMock()
    mock_creative_repo.get_provenance_policies.return_value = []
    mock_creative_repo.get_by_id.return_value = None
    mock_creative_repo.begin_nested.return_value.__enter__ = MagicMock(return_value=None)
    mock_creative_repo.begin_nested.return_value.__exit__ = MagicMock(return_value=None)

    # create() must return a mock with proper string attributes (Pydantic validation)
    def mock_create(**kwargs):
        db_creative = MagicMock()
        db_creative.creative_id = kwargs.get("creative_id", "c_unknown")
        db_creative.status = kwargs.get("status", "approved")
        return db_creative

    mock_creative_repo.create.side_effect = mock_create

    repos: dict = {"creatives": mock_creative_repo}
    if include_assignments:
        repos["assignments"] = MagicMock()

    _, mock_uow = make_mock_uow(repos=repos)
    return mock_uow, mock_creative_repo


def sync_patches():
    """Context manager returning (mock_creative_repo, mock_registry) with standard patches."""

    @contextmanager
    def ctx(mock_format_spec_arg=None):
        async def mock_list_all_formats(tenant_id=None):
            return [mock_format_spec_arg] if mock_format_spec_arg else []

        async def mock_get_format(agent_url, format_id):
            return mock_format_spec_arg

        mock_registry = Mock()
        mock_registry.list_all_formats = mock_list_all_formats
        mock_registry.get_format = mock_get_format

        mock_uow, mock_creative_repo = make_creative_uow()

        with (
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry),
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
            patch("src.core.tools.creatives._workflow.WorkflowUoW") as mock_wf_uow,  # noqa: F841
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow
            mock_uow_cls.return_value.__exit__.return_value = None
            yield mock_creative_repo, mock_registry

    return ctx


# ---------------------------------------------------------------------------
# Creative model construction helpers for serialization tests
# ---------------------------------------------------------------------------


def make_test_creative(
    creative_id: str = "test_123",
    name: str = "Test Banner",
    *,
    principal_id: str = "principal_456",
    status: str = "approved",
    tags: list[str] | None = None,
) -> Creative:  # type: ignore[name-defined]
    """Build a Creative model with standard fields for serialization tests.

    Shared between test_creative_response_serialization and test_list_creatives_serialization.
    """
    from src.core.schemas import Creative

    kwargs: dict = {
        "creative_id": creative_id,
        "variants": [],
        "name": name,
        "format": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        "assets": make_image_assets("banner", url="https://example.com/banner.jpg"),
        "principal_id": principal_id,
        "created_date": datetime.now(UTC),
        "updated_date": datetime.now(UTC),
        "status": status,
    }
    if tags is not None:
        kwargs["tags"] = tags
    return Creative(**kwargs)


def make_test_creative_list(count: int = 3) -> list:
    """Build multiple Creative models with varying status for serialization tests."""
    from src.core.schemas import Creative

    return [
        Creative(
            creative_id=f"creative_{i}",
            variants=[],
            name=f"Test Creative {i}",
            format={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
            assets=make_image_assets("banner", url=f"https://example.com/banner{i}.jpg"),
            principal_id=f"principal_{i}",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
            status="approved" if i % 2 == 0 else "pending_review",
        )
        for i in range(count)
    ]


def assert_listing_creative_fields(creative_data: dict, creative_id: str, *, prefix: str = "") -> None:
    """Assert standard listing Creative public fields in serialized output.

    Shared assertion pattern between serialization test files.
    """
    label = f"{prefix}: " if prefix else ""
    assert "principal_id" not in creative_data, f"{label}principal_id should be excluded"
    assert creative_data["creative_id"] == creative_id
    assert "format_id" in creative_data, f"{label}format_id should be present"
    assert "name" in creative_data, f"{label}name is a public listing field"
    assert "status" in creative_data, f"{label}status is a public listing field"
