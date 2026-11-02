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
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

from adcp.types import AccountReference

from src.core.schemas import SyncCreativesRequest
from tests.factories.creative_asset import AssetSpec, assert_assets, build_assets, image_spec
from tests.helpers.adcp_factories import create_test_format_id

if TYPE_CHECKING:
    from src.core.schemas import Creative
    from src.core.schemas.creative import SyncCreativesRequest


def make_creative_dict(creative_id: str = "c1", name: str = "Test Banner") -> dict:
    """Build a valid creative REQUEST dict (core/creative-asset.json @ AdCP 3.1.1).

    ``variants`` was in here and is not a request field at all -- it is delivery-only (see
    test_adcp_contract.py's delivery_only_fields, and the listing-response assertions that
    it must NOT appear). It survived while these payloads went straight into
    ``_sync_creatives_impl``, which coerced to the response model; now that every caller
    builds a SyncCreativesRequest first, CreativeAssetRequest refuses it -- as every
    transport already did for a real buyer.
    """
    return {
        "creative_id": creative_id,
        "name": name,
        "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
        "assets": build_assets(image_spec("banner_image", url="https://example.com/banner.png")),
    }


_DEFAULT_AGENT_URL = "https://creative.test.example.com"

#: Stands in for an account a test is not about. ``_sync_creatives_impl`` never reads
#: ``req.account`` -- the transport wrapper resolves the reference into ``identity`` before
#: the call -- so nothing ever looks this id up. It exists because
#: creative/sync-creatives-request.json lists ``account`` in /required, and a request that
#: omits it is a request the schema rejects before the test reaches its subject.
_PLACEHOLDER_ACCOUNT_ID = "acct_test_placeholder"


def sync_creatives_request(**fields: Any) -> SyncCreativesRequest:
    """Build a SyncCreativesRequest from the loose per-field kwargs tests used to pass.

    ``_sync_creatives_impl`` takes ``(req, identity, request_hash)``; every protocol field
    travels on the request. Tests construct it through the SAME builder the three transports
    use, so a test cannot grade a shape no transport can produce.

    ``account`` and ``idempotency_key`` are spec-REQUIRED. A test that is about neither gets
    a placeholder for the first and a fresh key for the second — fresh, not fixed, because
    sync_creatives honours the key: a constant would make two calls in one test the same
    request, and the second would replay the first instead of executing.
    """
    fields.setdefault("creatives", [creative_payload()])
    fields.setdefault("idempotency_key", f"test-sync-idem-{uuid4().hex}")
    if fields.get("account") is None:
        fields["account"] = AccountReference(root={"account_id": _PLACEHOLDER_ACCOUNT_ID})

    # Imported at call time: this module is imported by tests that patch objects inside the
    # creatives package, and a module-level import would bind before their patches.

    return SyncCreativesRequest(**fields)


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

    # Deferred, and from the DEFINING module rather than the ``tests.harness`` package:
    # ``tests/helpers/`` is the lower layer that ``tests/harness/`` builds on, and several
    # harness envs (``creative_sync``) import this module. A module-level import here --
    # of the package OR of a submodule, since either executes ``tests/harness/__init__.py``
    # and its eager env imports -- closes that loop.
    from tests.harness._mock_uow import make_mock_uow

    repos: dict = {"creatives": mock_creative_repo}
    if include_assignments:
        repos["assignments"] = MagicMock()

    _, mock_uow = make_mock_uow(repos=repos)
    return mock_uow, mock_creative_repo


def make_format_spec(
    format_id: str = "display_300x250_image",
    *,
    agent_url: str = "https://creative.adcontextprotocol.org",
    name: str = "Medium Rectangle",
    output_format_ids: list[str] | None = None,
) -> Mock:
    """A registry listing entry shaped the way production's actually is.

    ONE definition, because three test modules hand-rolled this same stand-in and all
    three carried the same two defects:

    - ``format_id`` as a bare STRING. ``Format.format_id`` is annotated as the library
      FormatId, so a string is a shape production never produces. It made the old
      class-sensitive ``fmt.format_id == creative_format`` match by accident in unit
      tests while it matched NOTHING in production (#2093) -- the mock was propping up
      the bug it should have caught.
    - ``output_format_ids`` left auto-specced. _processing reads that attribute to
      decide whether a format is generative, and a bare Mock attribute is truthy, so
      every static format silently became a generative one the moment the lookup
      started working. Empty by default; pass a list to build a generative spec.
    """
    spec = Mock()
    spec.format_id = create_test_format_id(format_id, agent_url=agent_url)
    spec.agent_url = agent_url
    spec.name = name
    spec.output_format_ids = output_format_ids or []
    return spec


def make_registry_mock(
    formats: list | None = None,
    *,
    list_all_formats=None,
    get_format=None,
    preview_creative=None,
    build_creative=None,
) -> Mock:
    """A CreativeAgentRegistry stand-in, with the async methods actually async.

    ONE definition of the CONSTRUCTION, because that is the duplicated part: seven
    sites in one module plus sync_patches each hand-rolled ``Mock()`` +
    ``list_all_formats`` + ``get_format``, and every one shared the same defect --
    ``build_creative`` / ``preview_creative`` left as plain ``Mock`` attributes.
    _processing hands both to ``run_async_in_sync_context``, which rejects anything
    that is not a coroutine, so each copy sat one working format lookup away from
    ``TypeError: Expected coroutine``. They stayed green only because the lookup
    itself never matched (#2093).

    What each site does DIFFERENTLY -- returning a spec, returning None, raising
    AdCPServiceUnavailableError, branching on the requested id -- stays at that site
    and is passed in. Collapsing those too would be the other DRY mistake: merging
    behaviours that only look alike.

    Args:
        formats: convenience for the common case -- ``list_all_formats`` returns this
            and ``get_format`` returns its first entry (or None).
        list_all_formats: an async callable, when the site needs its own.
        get_format: an async callable, when the site needs its own.
        preview_creative: an async callable, when the site asserts on the preview.
        build_creative: an async callable, when the site asserts on the generative build.
    """
    listing = list(formats or [])

    # The defaults swallow keyword arguments because production's do too:
    # ``CreativeAgentRegistry.get_format`` grew a keyword-only ``provenance`` with the
    # SSRF egress seam (#1802) and ``format_resolver`` passes it on every call, while
    # ``list_all_formats`` takes a bag of filter kwargs. A stand-in that accepts only
    # the two positional parameters is a shape production no longer has, and raises
    # TypeError the moment the lookup it is standing in for actually runs.
    async def _default_list_all_formats(tenant_id=None, **_kwargs):
        return listing

    async def _default_get_format(agent_url, format_id, **_kwargs):
        return listing[0] if listing else None

    registry = Mock()
    registry.list_all_formats = list_all_formats or _default_list_all_formats
    registry.get_format = get_format or _default_get_format
    # Both defaults are the INERT no-result case _processing already guards for
    # (``if build_result:`` / ``if preview_result and preview_result.get("previews")``),
    # so a site that does not care about these branches sees no extra data written. The
    # preview default is spelled the way the agent actually answers with nothing --
    # a ``previews`` list -- not a bare ``preview_url``, which is a shape the
    # preview_creative response never has at the top level.
    registry.build_creative = build_creative or AsyncMock(return_value={})
    registry.preview_creative = preview_creative or AsyncMock(return_value={"previews": []})
    return registry


def sync_patches():
    """Context manager returning (mock_creative_repo, mock_registry) with standard patches."""

    @contextmanager
    def ctx(mock_format_spec_arg=None):
        mock_registry = make_registry_mock([mock_format_spec_arg] if mock_format_spec_arg else [])

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

    ``variants`` is NOT passed. It is a pre-3.1.1 delivery-only field that the local
    Creative validator pops unread (``Creative.validate_format_id``), so seeding it
    changed no model and only kept the old shape visible for the next author to copy.
    """
    from src.core.schemas import Creative

    kwargs: dict = {
        "creative_id": creative_id,
        "name": name,
        "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
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
            name=f"Test Creative {i}",
            format_id={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
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
