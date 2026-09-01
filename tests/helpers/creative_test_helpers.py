"""Shared creative test helpers.

DRY extraction for creative test utilities — sync/serialization builders, a
status-varying DB seeder, and a shared empty-array-filter wire-rejection assertion —
reused across the creative unit, integration, and BDD suites. Consumers are not
enumerated here; a grep of the exported names finds them.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import MagicMock, Mock, patch

from tests.factories.creative_asset import AssetSpec, assert_assets, build_assets, image_spec
from tests.harness import make_mock_uow

if TYPE_CHECKING:
    from src.core.database.models import Principal, Tenant
    from src.core.schemas import Creative  # src.core.schemas.Creative — distinct from the models import above
    from tests.harness._base import BaseTestEnv
    from tests.harness.transport import Transport


def seed_creative_in_status(
    tenant: Tenant,
    principal: Principal,
    status: str = "approved",
    *,
    creative_id: str | None = None,
) -> str:
    """Create one creative owned by *principal* in *status*; return its creative_id.

    The single seeder for the #1502 statuses-filter tests (and available to any test that
    needs one status-varying creative). ``status`` varies per call; pass ``creative_id``
    when the test asserts on a literal id (the repository tests) — otherwise CreativeFactory
    generates one. The remaining fields (format, data with real assets) come from
    CreativeFactory defaults, which already satisfy the repository's
    ``data["assets"] IS NOT NULL`` guard.
    """
    from tests.factories import CreativeFactory

    extra: dict[str, Any] = {"creative_id": creative_id} if creative_id is not None else {}
    return CreativeFactory(tenant=tenant, principal=principal, status=status, **extra).creative_id


def assert_empty_array_filter_rejected(
    env: BaseTestEnv, transport: Transport, field: Literal["concept_ids", "statuses"]
) -> None:
    """Assert ``filters={field: []}`` (a minItems:1 violation) is rejected with a two-layer
    VALIDATION_ERROR envelope that NAMES the rejected field and carries a recovery suggestion,
    on the given wire transport.

    Shared by the concept_ids and statuses empty-array validation tests: structurally the
    same operation with only the filter field substituted, so it lives here once rather than
    copy-pasted per filter (DRY). POST-F3 requires the buyer be told how to recover.

    Routes through the harness ``TransportResult.assert_wire_error`` (the single wire-error
    oracle), which returns the validated envelope and runs the no-raw-Pydantic-leak check
    itself — no re-fetch, no hand-rolled re-narrowing. ``recovery`` defaults to the pinned
    AdCP enum's VALIDATION_ERROR classification (pin-wins — no hardcoded ``"correctable"`` to
    drift if the pin reclassifies), and ``require_suggestion`` owns the POST-F3 check.

    PRIMARY cross-transport signal — ``errors[0]["field"] == f"filters.{field}"``: since
    ``coerce_creative_filters`` now reports the request-relative pointer (``filters.statuses`` /
    ``filters.concept_ids``) on REST/A2A to match what MCP already emits (AdCP 3.1.1
    ``core/error.json`` field pointer), the field pins WHICH filter was rejected — swap the
    ``field`` argument and the oracle moves. ``message_substr="List should have"`` is now a
    SECONDARY check that the violated constraint is minItems; it pins Pydantic's
    framework-internal phrasing (a reword reddens all wire arms at once), tracked for removal
    in #2066 now that ``field`` carries the cross-transport WHICH-field signal it was once the
    only source of.
    """
    result = env.call_via(transport, filters={field: []})
    envelope = result.assert_wire_error("VALIDATION_ERROR", require_suggestion=True, message_substr="List should have")
    assert envelope["errors"][0].get("field") == f"filters.{field}", (
        f"{transport}: VALIDATION_ERROR must name the rejected filter field 'filters.{field}', "
        f"got {envelope['errors'][0].get('field')!r}"
    )


def make_creative_dict(creative_id: str = "c1", name: str = "Test Banner") -> dict:
    """Build a valid creative dict with SDK 5.7 asset structure."""
    return {
        "creative_id": creative_id,
        "name": name,
        "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
        "assets": build_assets(image_spec("banner_image", url="https://example.com/banner.png")),
        "variants": [],
    }


_DEFAULT_AGENT_URL = "https://creative.test.example.com"


def creative_payload(**overrides: object) -> dict:
    """Build a minimal creative request dict (``creative_id``/``name``/``format_id``/``assets``).

    Shared skeleton for the per-file ``_creative`` helpers. The four base fields fall
    back to sensible defaults via ``setdefault``; any field a caller supplies (the
    file-specific defaults a ``_creative`` passes, or a per-test override) wins. Extra
    keys (e.g. ``data``, ``variants``) pass straight through.
    """
    payload: dict = dict(overrides)
    payload.setdefault("creative_id", "c1")
    payload.setdefault("name", "Test")
    payload.setdefault("format_id", {"id": "display_300x250", "agent_url": _DEFAULT_AGENT_URL})
    payload.setdefault("assets", build_assets(image_spec("banner")))
    return payload


def assert_stored_creative_assets(creative_id: str, *specs: AssetSpec, tenant_id: str | None = None) -> None:
    """Fetch the stored creative by id and assert its data["assets"] contains each spec.

    Opens a DB session, selects the Creative by ``creative_id`` (scoped to
    ``tenant_id`` when given), and runs ``assert_assets`` against its stored
    ``data["assets"]`` so build-and-verify flow through the same AssetSpec.
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Creative as DBCreative

    filters: dict = {"creative_id": creative_id}
    if tenant_id is not None:
        filters["tenant_id"] = tenant_id
    with get_db_session() as session:
        db = session.scalars(select(DBCreative).filter_by(**filters)).first()
        assert db is not None, f"Creative {creative_id} not found in DB"
        assert_assets((db.data or {}).get("assets", {}), *specs)


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
    assets: dict | None = None,
) -> Creative:
    """Build a Creative model with standard fields for serialization tests.

    Shared between test_creative_response_serialization and test_list_creatives_serialization.

    ``assets`` overrides the default single-banner slot map — pass
    ``build_assets(...)`` output (e.g. ``image_spec("banner").with_fields(alt_text=None)``)
    so asset shapes stay declared through AssetSpec rather than hand-rolled here.
    """
    from src.core.schemas import Creative

    kwargs: dict = {
        "creative_id": creative_id,
        "variants": [],
        "name": name,
        "format": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        "assets": build_assets(image_spec("banner", url="https://example.com/banner.jpg"))
        if assets is None
        else assets,
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
            assets=build_assets(image_spec("banner", url=f"https://example.com/banner{i}.jpg")),
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
