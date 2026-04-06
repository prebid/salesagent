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

    In E2E mode, ``_mock_agent_url`` points to the mock creative agent
    sidecar (read from ``MOCK_CREATIVE_AGENT_URL`` env var). Given steps
    POST formats to the sidecar instead of patching in-process mocks.
    """

    @property
    def _mock_agent_url(self) -> str | None:
        """URL of the mock creative agent sidecar (E2E only)."""
        import os

        return os.environ.get("MOCK_CREATIVE_AGENT_URL")

    EXTERNAL_PATCHES = {
        "audit_logger": "src.core.tools.creative_formats.get_audit_logger",
    }
    REST_ENDPOINT = "/api/v1/creative-formats"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._format_patcher: Any | None = None

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults.

        In E2E mode, resets the mock creative agent sidecar to empty so each
        scenario starts with a clean slate. Each scenario MUST seed its own
        formats via set_registry_formats() in the Given step — no shared state.

        In in-process mode, the real registry runs with ADCP_TESTING=true
        which returns _get_mock_formats(). Scenarios that need specific
        formats override via set_registry_formats().
        """
        if self.e2e_config and self._mock_agent_url:
            # E2E: reset the sidecar to empty — each scenario seeds its own formats
            import httpx

            httpx.post(f"{self._mock_agent_url}/test/reset")

        # Audit logger: no-op
        mock_logger = MagicMock()
        self.mock["audit_logger"].return_value = mock_logger

    def set_registry_formats(self, formats: list[Any]) -> None:
        """Configure registry to return these formats from list_all_formats.

        In E2E mode (e2e_config is set), POSTs the formats to the mock
        creative agent sidecar so Docker's registry fetches them.
        In in-process mode, patches _get_mock_formats to return the
        given formats (ADCP_TESTING=true ensures this path is taken).
        """
        if self.e2e_config and self._mock_agent_url:
            import httpx

            httpx.post(
                f"{self._mock_agent_url}/test/set-formats",
                json=[f.model_dump(mode="json") if hasattr(f, "model_dump") else f for f in formats],
            )
            return

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
