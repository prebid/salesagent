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
        from src.core.creative_agent_registry import FormatFetchResult, _get_mock_formats

        default_formats = _get_mock_formats()

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

    def set_registry_formats(self, formats: list[Any]) -> None:
        """Configure mock registry to return these formats from list_all_formats."""
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
        """Call list_creative_formats_raw (A2A wrapper)."""
        from src.core.tools.creative_formats import list_creative_formats_raw

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        kwargs.setdefault("req", None)
        return list_creative_formats_raw(**kwargs)

    def call_mcp(self, **kwargs: Any) -> ListCreativeFormatsResponse:
        """Call list_creative_formats MCP wrapper with mock Context.

        Pops 'req' kwarg (MCP wrapper takes individual params, not req object).
        """
        from src.core.tools.creative_formats import list_creative_formats

        # MCP wrapper takes individual params, not 'req'
        kwargs.pop("req", None)
        return self._run_mcp_wrapper(list_creative_formats, ListCreativeFormatsResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to ListCreativeFormatsBody shape for REST POST.

        Returns empty dict intentionally: ListCreativeFormatsBody
        (src/routes/api_v1.py) only defines ``adcp_version: str = "1.0.0"``
        with no user-facing parameters. All kwargs are dropped.
        """
        return {}

    def parse_rest_response(self, data: dict[str, Any]) -> ListCreativeFormatsResponse:
        """Parse REST JSON into ListCreativeFormatsResponse."""
        return ListCreativeFormatsResponse(**data)
