"""SignalsEnv — integration test environment for _get_signals_impl.

Patches: NOTHING. The signals implementation (src/core/tools/signals.py)
serves an in-process signal catalog with no external-service seam — no
adapter, no HTTP client, no signal-provider API. Real: principal/tenant
resolution from the DB, all transport wrappers (MCP tool, A2A skill,
REST POST /api/v1/signals).

Requires: integration_db fixture.

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with SignalsEnv() as env:
            env.setup_default_data()

            result = env.call_via(Transport.MCP, req=GetSignalsRequest(signal_spec="sports"))
            assert result.is_success

MCP wire shape: the MCP wrapper (signals.py:204) takes a single typed
``req: GetSignalsRequest`` parameter, so FastMCP exposes the tool with a
nested ``{"req": {...}}`` argument schema (verified empirically against
FastMCP's TypeAdapter — flat arguments are rejected with
``unexpected_keyword_argument``). ``call_mcp`` therefore wraps the request
in a one-field envelope model so ``_run_mcp_client``'s req-flattening
produces the nested ``{"req": {...}}`` arguments dict the registered tool
expects on the wire.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.core.schemas import GetSignalsRequest, GetSignalsResponse
from tests.harness._base import IntegrationEnv


class SignalsEnv(IntegrationEnv):
    """Integration test environment for get_signals.

    Nothing is mocked — the impl's signal catalog is in-process and
    identity/tenant resolution runs against the real database.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}

    REST_ENDPOINT = "/api/v1/signals"

    def call_impl(self, **kwargs: Any) -> GetSignalsResponse:
        """Call _get_signals_impl with real DB-backed identity."""
        from src.core.tools.signals import _get_signals_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        req = kwargs.pop("req", None) or GetSignalsRequest(**kwargs)
        return asyncio.run(_get_signals_impl(req, identity))

    def call_a2a(self, **kwargs: Any) -> Any:
        """Dispatch through the REAL A2A pipeline (get_signals skill)."""
        return self._run_a2a_handler("get_signals", GetSignalsResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call get_signals via Client(mcp) — full pipeline dispatch.

        Dispatches the FLAT request fields — exactly the arguments a
        conformant buyer sends per the v3.1.1 get-signals-request schema.
        """
        req = kwargs.pop("req", None) or GetSignalsRequest()
        return self._run_mcp_client("get_signals", GetSignalsResponse, req=req, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Serialize only the fields the caller actually set.

        ``exclude_unset`` keeps library-schema defaults (e.g. ``discovery_mode``)
        off the wire so the test pins only the fields it sends — the REST Body
        model's exact field inventory is the implementation's concern.
        """
        req = kwargs.get("req")
        if req is not None:
            return req.model_dump(mode="json", exclude_unset=True, exclude_none=True)
        return {}

    def parse_rest_response(self, data: dict[str, Any]) -> GetSignalsResponse:
        """Parse REST JSON response into GetSignalsResponse."""
        return GetSignalsResponse(**data)
