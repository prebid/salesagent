"""CreativeFormatsEnv — integration test environment for _list_creative_formats_impl.

Patches: audit logger only.
Real: creative agent registry, format fetching, filtering.

When Docker is running, the registry fetches from the real creative agent
container. When tests need controlled formats (specific filtering behavior),
set_registry_formats() pre-warms the registry cache — the same mechanism
production uses after fetching. No mock patches, no _get_mock_formats bypasses.

Requires: Docker stack running (creative agent + Postgres) for real-catalog tests.

Usage::

    @pytest.mark.requires_db
    def test_filter_by_type(self, integration_db):
        with CreativeFormatsEnv() as env:
            env.set_registry_formats([display_format, video_format])
            response = env.call_impl(req=ListCreativeFormatsRequest(type="display"))
            assert all(f.type == "display" for f in response.formats)

Available mocks via env.mock:
    "audit_logger" -- get_audit_logger (module-level in creative_formats.py)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import ListCreativeFormatsResponse
from tests.harness._base import IntegrationEnv


class CreativeFormatsEnv(IntegrationEnv):
    """Integration test environment for _list_creative_formats_impl.

    The creative agent registry runs for real. Only the audit logger
    is mocked (internal, no external call).

    set_registry_formats() injects formats into the registry's own cache —
    same data structure production uses after an HTTP fetch. No patches.
    """

    EXTERNAL_PATCHES = {
        "audit_logger": "src.core.tools.creative_formats.get_audit_logger",
    }
    REST_ENDPOINT = "/api/v1/creative-formats"

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults — audit logger only."""
        mock_logger = MagicMock()
        self.mock["audit_logger"].return_value = mock_logger

    def call_impl(self, **kwargs: Any) -> ListCreativeFormatsResponse:
        """Call _list_creative_formats_impl."""
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
        """Convert kwargs to ListCreativeFormatsBody shape for REST POST."""
        return {}

    def parse_rest_response(self, data: dict[str, Any]) -> ListCreativeFormatsResponse:
        """Parse REST JSON into ListCreativeFormatsResponse."""
        return ListCreativeFormatsResponse(**data)
