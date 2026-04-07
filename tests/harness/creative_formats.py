"""CreativeFormatsEnv — integration test environment for _list_creative_formats_impl.

Patches: audit logger only.
Real: creative agent registry (uses ADCP_TESTING=true mock formats), format processing logic.

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with CreativeFormatsEnv() as env:
            env.set_registry_formats([mock_format_1, mock_format_2])
            response = env.call_impl()
            assert len(response.formats) == 2

Available mocks via env.mock:
    "audit_logger" -- get_audit_logger (module-level import in creative_formats.py)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from src.core.schemas import ListCreativeFormatsResponse
from tests.harness._base import IntegrationEnv


class CreativeFormatsEnv(IntegrationEnv):
    """Integration test environment for _list_creative_formats_impl.

    The creative agent registry runs for real — in-process mode relies on
    ADCP_TESTING=true which makes the registry return _get_mock_formats().
    Scenarios that need specific formats call set_registry_formats() which
    patches _get_mock_formats at the module level.

    In E2E mode, the real adcp reference creative agent runs in Docker.
    It serves a fixed 49-format catalog — no admin API to control formats.
    To test "empty catalog" scenarios, control which agents the TENANT has
    registered rather than trying to empty the agent's catalog.
    """

    EXTERNAL_PATCHES = {
        "audit_logger": "src.core.tools.creative_formats.get_audit_logger",
    }
    REST_ENDPOINT = "/api/v1/creative-formats"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._format_patcher: Any | None = None

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults.

        The real registry runs with ADCP_TESTING=true which returns
        _get_mock_formats(). Scenarios that need specific formats
        override via set_registry_formats().
        """
        # Audit logger: no-op
        mock_logger = MagicMock()
        self.mock["audit_logger"].return_value = mock_logger

    def set_registry_formats(self, formats: list[Any]) -> None:
        """Configure registry to return these formats from list_all_formats.

        Patches _get_mock_formats to return the given formats
        (ADCP_TESTING=true ensures this path is taken).
        """
        # In-process: patch _get_mock_formats so the real registry returns our formats.
        # Also invalidate the global registry cache so stale cached formats don't leak.
        from src.core.creative_agent_registry import get_creative_agent_registry

        registry = get_creative_agent_registry()
        registry._format_cache.clear()

        # Stop previous patcher if any (idempotent replacement)
        if self._format_patcher is not None:
            self._format_patcher.stop()
            if self._format_patcher in self._patchers:
                self._patchers.remove(self._format_patcher)

        self._format_patcher = patch(
            "src.core.creative_agent_registry._get_mock_formats",
            return_value=list(formats),
        )
        self._format_patcher.start()
        self._patchers.append(self._format_patcher)

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
        return self._run_a2a_handler("list_creative_formats", ListCreativeFormatsResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> ListCreativeFormatsResponse:
        """Call list_creative_formats via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("list_creative_formats", ListCreativeFormatsResponse, **kwargs)

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
