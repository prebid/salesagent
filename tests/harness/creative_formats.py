"""CreativeFormatsEnv — integration test environment for _list_creative_formats_impl.

Patches: creative agent registry, audit logger.
Real: format processing logic (no direct DB access in this _impl).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with CreativeFormatsEnv() as env:
            env.set_registry_formats([mock_format_1, mock_format_2])
            response = env.call_impl()
            assert len(response.formats) == 2

Available mocks via env.mock:
    "registry"     -- get_creative_agent_registry (lazy import in creative_formats.py)
    "audit_logger" -- get_audit_logger (module-level import in creative_formats.py)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from src.core.schemas import ListCreativeFormatsResponse
from tests.harness._base import IntegrationEnv
from tests.harness._realize import E2EUnsupportedSetup, realize_e2e


def _format_id_key(fmt: Any) -> str:
    """Stable comparable id for a Format / FormatId / raw string.

    The reference catalog keys on the namespaced ``format_id.id`` (e.g.
    ``"display_300x250"``); ``str(format_id)`` is a verbose structured repr and
    is NOT a stable identity. Accepts a ``Format`` (``.format_id.id``), a bare
    ``FormatId`` (``.id``), or a plain string id.
    """
    format_id = getattr(fmt, "format_id", fmt)
    return str(getattr(format_id, "id", format_id))


def _validate_registry_formats(env: Any, formats: list[Any]) -> None:
    """E2E realization of set_registry_formats: validate against the live catalog.

    The live stack serves the full reference-format catalog by construction
    (``ADCP_TESTING`` reads the same fixture this validates against), so there
    is no per-scenario server registry to write. Instead we validate the
    scenario's intent is realizable:

    - empty list (empty-catalog scenarios) -> unrealizable: the live stack
      always serves the agent catalog.
    - requested ids ⊆ reference set -> no-op: the server already serves them.
    - requested ⊄ reference set -> unrealizable: name the missing ids and point
      at the fixture-refresh path.
    """
    from src.core.format_cache import load_reference_formats

    if not formats:
        raise E2EUnsupportedSetup(
            "live stack always serves the agent catalog; an empty catalog cannot be realized over e2e"
        )

    reference_ids = {_format_id_key(f) for f in load_reference_formats()}
    requested_ids = {_format_id_key(f) for f in formats}
    missing = requested_ids - reference_ids
    if missing:
        raise E2EUnsupportedSetup(
            f"requested formats not in the reference catalog: {sorted(missing)}. "
            "Register them in the creative agent registry and refresh the fixture "
            "(`make creative-formats-refresh`)."
        )


class CreativeFormatsEnv(IntegrationEnv):
    """Integration test environment for _list_creative_formats_impl.

    Mocks creative agent registry (external service) and audit logger.
    The format processing logic runs for real.
    """

    EXTERNAL_PATCHES = {
        "registry": "src.core.creative_agent_registry.get_creative_agent_registry",
        "audit_logger": "src.core.tools.creative_formats.get_audit_logger",
    }
    REST_ENDPOINT = "/api/v1/creative-formats"

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for external mocks.

        Seeds a minimal set of default formats so scenarios that don't
        explicitly call set_registry_formats() still get non-empty results.
        Scenarios needing specific formats override via set_registry_formats().
        """
        from src.core.creative_agent_registry import FormatFetchResult
        from src.core.format_cache import load_reference_formats

        default_formats = list(load_reference_formats())

        # Registry: return a mock with async list_all_formats + list_all_formats_with_errors
        mock_registry = MagicMock()
        mock_registry.list_all_formats = AsyncMock(return_value=default_formats)
        mock_registry.list_all_formats_with_errors = AsyncMock(
            return_value=FormatFetchResult(formats=default_formats, errors=[])
        )
        self.mock["registry"].return_value = mock_registry

        # Audit logger: no-op
        mock_logger = MagicMock()
        self.mock["audit_logger"].return_value = mock_logger

    @realize_e2e(_validate_registry_formats)
    def set_registry_formats(self, formats: list[Any]) -> None:
        """Configure mock registry to return these formats from list_all_formats.

        In-process: injects ``formats`` on the registry mock. E2E: validates the
        request is realizable against the live catalog (the live stack serves the
        full reference set by construction, so there is no per-scenario registry
        to write) — see :func:`_validate_registry_formats`.
        """
        from src.core.creative_agent_registry import FormatFetchResult

        self.mock["registry"].return_value.list_all_formats = AsyncMock(return_value=formats)
        self.mock["registry"].return_value.list_all_formats_with_errors = AsyncMock(
            return_value=FormatFetchResult(formats=list(formats), errors=[])
        )

    def call_impl(self, **kwargs: Any) -> ListCreativeFormatsResponse:
        """Call _list_creative_formats_impl.

        Accepts 'req' (ListCreativeFormatsRequest) and 'identity' kwargs.
        Defaults to self.identity if not provided.
        """
        from src.core.tools.creative_formats import _list_creative_formats_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        kwargs.setdefault("req", None)
        return _list_creative_formats_impl(**kwargs)

    def call_a2a(self, **kwargs: Any) -> ListCreativeFormatsResponse:
        """Call list_creative_formats via real AdCPRequestHandler — full A2A pipeline."""
        raw_params = kwargs.pop("raw_params", None)
        if raw_params is not None:
            kwargs.update(raw_params)
        return self._run_a2a_handler("list_creative_formats", ListCreativeFormatsResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> ListCreativeFormatsResponse:
        """Call list_creative_formats via Client(mcp) — full pipeline dispatch."""
        raw_params = kwargs.pop("raw_params", None)
        if raw_params is not None:
            kwargs.update(raw_params)
        return self._run_mcp_client("list_creative_formats", ListCreativeFormatsResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Allow error scenarios to submit raw JSON through the real REST route."""
        raw_params = kwargs.get("raw_params")
        if raw_params is not None:
            return raw_params
        return super().build_rest_body(**kwargs)

    # build_rest_body is inherited from IntegrationEnv: it serializes the Pydantic
    # ``req`` via model_dump(mode="json", exclude_none=True). ListCreativeFormatsBody
    # (src/routes/api_v1.py) declares format_ids + every other filter and the route
    # maps them into ListCreativeFormatsRequest, so REST filters for real — there is
    # no need to drop kwargs. (A prior override returned {} behind a stale docstring
    # claiming the body had no parameters; that suppressed REST filter coverage.)

    def parse_rest_response(self, data: dict[str, Any]) -> ListCreativeFormatsResponse:
        """Parse REST JSON into ListCreativeFormatsResponse."""
        return ListCreativeFormatsResponse(**data)
