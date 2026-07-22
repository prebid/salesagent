"""Integration test: MCP update error path surfaces the wire error envelope.

Before #1417, MediaBuyDualEnv._call_update_mcp invokes the raw
``update_media_buy`` tool function directly (asyncio.run), bypassing the
production ``with_error_logging`` decorator that real MCP registration applies
(src/core/main.py). On error the raw AdCPError propagates instead of becoming an
AdCPToolError carrying the two-layer envelope, so McpDispatcher's
``_envelope_from_mcp_error`` returns None and ``result.wire_error_envelope`` is
None — the MCP update error path cannot be asserted at the wire layer.

After ihwl, the harness wraps the call with ``with_error_logging`` (the same
decorator production uses), so a raised AdCPError surfaces as the wire
ToolError envelope and ``result.wire_error_envelope`` is populated.

A2A and REST already capture the envelope (verified here as a guard); MCP is
the gap this test pins.

beads: salesagent-ihwl
"""

from __future__ import annotations

import pytest

from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# All three wire transports must surface the wire error envelope on update errors.
_WIRE_TRANSPORTS = [Transport.A2A, Transport.MCP, Transport.REST]


class TestUC003McpUpdateErrorCapture:
    """Wire transports must surface the two-layer error envelope on update errors.

    Core Invariant (#1417): the harness MCP update path must exercise
    the same production boundary error-translation (with_error_logging ->
    AdCPToolError) that real MCP registration applies, so a raised AdCPError
    surfaces as the wire ToolError envelope captured on
    ``result.wire_error_envelope`` — never a raw exception yielding None.
    """

    # env_with_media_buy comes from tests/integration/conftest.py (shared home).

    @staticmethod
    def _top_level_suggestion(envelope) -> str | None:
        """Read the SPEC top-level ``suggestion`` from the two-layer envelope.

        Per /schemas/3.0.9/core/error.json the buyer-facing ``suggestion`` is a
        DISTINCT top-level property, sibling to ``details`` — NOT ``details.suggestion``.
        Accepts a dict envelope (A2A/REST) or an AdCPToolError (MCP, ``.envelope``).
        """
        env = envelope.envelope if hasattr(envelope, "envelope") else envelope
        adcp_error = env.get("adcp_error", {}) if isinstance(env, dict) else {}
        return adcp_error.get("suggestion")

    def test_update_request_validation_error_carries_top_level_suggestion(self, env_with_media_buy):
        """A request that fails Pydantic validation at the update boundary
        (_build_update_request) must carry the buyer-facing correction hint in the
        SPEC top-level ``suggestion`` field, not leave it empty (#1417 / 3rqe fix B).

        Fails before the fix: _build_update_request raised AdCPValidationError with
        no ``suggestion=`` so the wire ``suggestion`` field is empty. Passing a
        ``req`` routes to the update path; the uncoercible ``budget`` override reaches
        _build_update_request's generic Pydantic ValidationError branch at the boundary.
        """
        from src.core.schemas import UpdateMediaBuyRequest

        env, media_buy = env_with_media_buy
        result = env.call_via(
            Transport.A2A,
            req=UpdateMediaBuyRequest(media_buy_id=media_buy.media_buy_id, paused=True),
            # package_id present (passes the shape check) but an uncoercible budget ->
            # UpdateMediaBuyRequest(**params) raises a Pydantic ValidationError at the boundary.
            packages=[{"package_id": "pkg_001", "budget": "notanumber"}],
        )

        assert result.is_error, "expected a validation error"
        assert result.wire_error_envelope is not None, "wire envelope not captured"
        assert_envelope_shape(result.wire_error_envelope, "VALIDATION_ERROR", recovery="correctable")
        suggestion = self._top_level_suggestion(result.wire_error_envelope)
        assert suggestion, (
            f"update request-validation error must carry a non-empty TOP-LEVEL "
            f"'suggestion' (spec error.json), got {suggestion!r}"
        )

    def test_ownership_error_suggestion_is_top_level_not_details(self, env_with_media_buy):
        """The ownership AdCPAuthorizationError must expose its hint in the SPEC
        top-level ``suggestion`` field, not buried in ``details['suggestion']``
        (#1417 / 3rqe fix C). Asserted on the real A2A wire envelope."""
        from src.core.schemas import UpdateMediaBuyRequest
        from tests.factories import PrincipalFactory

        env, media_buy = env_with_media_buy
        # A different principal (not the media buy's owner) triggers _verify_principal.
        PrincipalFactory(tenant=env._owner_tenant, principal_id="principal_other")
        env._commit_factory_data()
        other_identity = PrincipalFactory.make_identity(tenant_id=media_buy.tenant_id, principal_id="principal_other")

        result = env.call_via(
            Transport.A2A,
            req=UpdateMediaBuyRequest(media_buy_id=media_buy.media_buy_id, paused=True),
            identity=other_identity,
        )

        assert result.is_error, "expected an ownership authorization error"
        assert result.wire_error_envelope is not None
        suggestion = self._top_level_suggestion(result.wire_error_envelope)
        assert suggestion and "own" in suggestion.lower(), (
            f"ownership error must carry the hint in the TOP-LEVEL 'suggestion' field, got {suggestion!r}"
        )

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_empty_update_surfaces_wire_error_envelope(self, env_with_media_buy, transport):
        """An empty update (no updatable fields) is an INVALID_REQUEST whose
        two-layer envelope must be captured at the wire layer on every transport.

        Grounded against AdCP 3.1 GA (nzjx): every update field is optional in
        update-media-buy-request.json, so an empty update passes schema validation
        and is a SEMANTIC rejection (BR-RULE-022) -> INVALID_REQUEST, not the
        schema-level VALIDATION_ERROR (GA L3 error-handling). The rejection carries
        a top-level buyer suggestion (error.json), not a copy buried in details.

        Fails on MCP before ihwl: wire_error_envelope is None because the raw
        AdCPError never becomes an AdCPToolError.
        """
        from src.core.schemas import UpdateMediaBuyRequest

        env, media_buy = env_with_media_buy
        # No updatable fields -> production raises AdCPInvalidRequestError at the boundary.
        req = UpdateMediaBuyRequest(media_buy_id=media_buy.media_buy_id)

        result = env.call_via(transport, req=req)

        assert result.is_error, f"{transport}: expected an error for an empty update"
        assert result.wire_error_envelope is not None, (
            f"{transport}: wire_error_envelope NOT captured — the MCP update error "
            f"path bypasses with_error_logging (salesagent-ihwl)."
        )
        assert_envelope_shape(
            result.wire_error_envelope,
            "INVALID_REQUEST",
            recovery="correctable",
            message_substr="at least one updatable field",
        )
        suggestion = self._top_level_suggestion(result.wire_error_envelope)
        assert suggestion and "at least one updatable field" in suggestion.lower(), (
            f"{transport}: empty-update rejection must carry a non-empty TOP-LEVEL "
            f"'suggestion' (error.json) naming the updatable fields, got {suggestion!r}"
        )
