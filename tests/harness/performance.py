"""PerformanceEnv — integration test environment for update_performance_index.

Real: MediaBuyUoW, adapter resolution (mock adapter via platform_mappings),
audit logger. Requires an existing MediaBuy owned by the calling principal
(``_verify_principal``) and auth (no anonymous access — REST route uses
``require_auth``).

Note: ``update_performance_index`` has no corresponding task in the pinned
AdCP 3.1 schema tree — it is listed in ``_KNOWN_MISSING_SCHEMA_SKILLS``
(tests/e2e/test_a2a_protocol_compliance.py); the pinned
``provide-performance-feedback-response.json`` is a structurally different,
unrelated task. Tests using this env can assert field ABSENCE on the wire
but must not validate against a pinned response schema for this tool.

Requires: integration_db fixture.
"""

from __future__ import annotations

from typing import Any

from src.core.schemas import UpdatePerformanceIndexResponse
from tests.harness._base import IntegrationEnv


class PerformanceEnv(IntegrationEnv):
    """Integration test environment for update_performance_index.

    No patches — the adapter call runs for real against the mock adapter.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}
    REST_ENDPOINT = "/api/v1/performance-index"

    def _configure_mocks(self) -> None:
        """No mocks needed — real MediaBuyUoW + mock adapter."""

    def call_impl(self, **kwargs: Any) -> UpdatePerformanceIndexResponse:
        """Call _update_performance_index_impl with real DB.

        Accepts flat wire params (media_buy_id, performance_data: list[dict],
        context) — same shape MCP/A2A/REST all pass — built via the shared
        production helper so IMPL exercises the identical construction path.
        """
        from src.core.tools.performance import _build_update_performance_index_request, _update_performance_index_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        req = kwargs.pop("req", None)
        if req is None:
            req = _build_update_performance_index_request(
                media_buy_id=kwargs["media_buy_id"],
                performance_data=kwargs.get("performance_data", []),
                context=kwargs.get("context"),
            )
        return _update_performance_index_impl(req=req, identity=identity)

    def call_a2a(self, **kwargs: Any) -> Any:
        """Dispatch update_performance_index through the REAL A2A pipeline."""
        return self._run_a2a_handler("update_performance_index", UpdatePerformanceIndexResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call update_performance_index via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("update_performance_index", UpdatePerformanceIndexResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert flat wire params into the /performance-index POST body.

        UpdatePerformanceIndexBody takes media_buy_id/performance_data/context
        directly — no ``req`` bundling like the discovery endpoints.
        """
        body: dict[str, Any] = {
            "media_buy_id": kwargs["media_buy_id"],
            "performance_data": kwargs.get("performance_data", []),
        }
        if kwargs.get("context") is not None:
            body["context"] = kwargs["context"]
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> UpdatePerformanceIndexResponse:
        """Parse REST JSON into UpdatePerformanceIndexResponse."""
        return UpdatePerformanceIndexResponse(**data)
