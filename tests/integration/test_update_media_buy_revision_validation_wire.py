"""Integration wire pins: schema-invalid ``revision`` on update_media_buy.

The BDD outlines that cover these rows (T-UC-003-partition-revision /
T-UC-003-boundary-revision) grade ``INVALID_REQUEST`` per the generated
storyboard, while production routes schema validation through the sanctioned
``adcp_validation_boundary`` — which emits ``VALIDATION_ERROR``. Those outlines
therefore stay xfailed until the storyboard is reconciled (see
tests/bdd/conftest.py; graduation tracked in #1694), which would leave the
changed, buyer-visible emission UNGRADED on the wire: reverting the boundary
emission would keep every wired scenario green.

These tests are that grade. They pin the real wire envelope per transport via
the harness (``result.wire_error_envelope`` + ``assert_envelope_shape``):

- below-minimum revision (0): an ``int`` on every transport, so it reaches the
  Pydantic ``ge=1`` constraint at the shared ``adcp_validation_boundary``
  everywhere -> ``VALIDATION_ERROR`` on A2A, MCP, and REST alike.
- wrong-type revision ("not-an-int"): on A2A the raw kwarg reaches the shared
  boundary -> ``VALIDATION_ERROR``; on MCP the tool's typed schema rejects it
  at the FastMCP TypeAdapter layer, which emits the SAME wire code
  (``VALIDATION_ERROR`` — see test_mcp_typeadapter_validation_envelope.py), so
  the buyer contract is uniform in-process; on REST, FastAPI's typed body model
  (``revision: int | None``, src/routes/api_v1.py) rejects it during body
  parsing BEFORE the shared boundary -> ``INVALID_REQUEST``. That per-transport
  wire-code split is a documented deliberate deviation (in-code at
  src/routes/api_v1.py; the cross-transport classification policy that would
  resolve it is tracked in #1604) — this test pins it AS documented, so an
  unplanned convergence (or a new divergence) goes red instead of drifting
  silently.
"""

from __future__ import annotations

import pytest

from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# All three wire transports surface a real wire envelope for update errors.
_WIRE_TRANSPORTS = [Transport.A2A, Transport.MCP, Transport.REST]


class TestUpdateRevisionValidationWire:
    """Schema-invalid ``revision`` values must emit the documented wire codes.

    Uses the shared ``env_with_media_buy`` fixture (tests/integration/conftest.py)
    — the single home for the dual-env + seeded-buy setup.
    """

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
    def test_below_min_revision_emits_validation_error_on_every_transport(self, env_with_media_buy, transport):
        """revision=0 violates the schema minimum (ge=1) at the shared Pydantic
        boundary on every transport -> VALIDATION_ERROR/correctable on the wire."""
        env, media_buy = env_with_media_buy
        result = env.call_via(transport, media_buy_id=media_buy.media_buy_id, paused=True, revision=0)

        assert result.is_error, "expected a validation error for revision=0"
        assert result.wire_error_envelope is not None, "wire envelope not captured"
        assert_envelope_shape(result.wire_error_envelope, "VALIDATION_ERROR", recovery="correctable")

    @pytest.mark.parametrize("transport", [Transport.A2A, Transport.MCP], ids=lambda t: t.value)
    def test_wrong_type_revision_emits_validation_error_in_process(self, env_with_media_buy, transport):
        """A non-integer revision emits VALIDATION_ERROR/correctable on the wire for
        both in-process transports (A2A via the shared adcp_validation_boundary;
        MCP via the FastMCP TypeAdapter layer — same buyer-visible code)."""
        env, media_buy = env_with_media_buy
        result = env.call_via(transport, media_buy_id=media_buy.media_buy_id, paused=True, revision="not-an-int")

        assert result.is_error, "expected a validation error for a non-integer revision"
        assert result.wire_error_envelope is not None, "wire envelope not captured"
        assert_envelope_shape(result.wire_error_envelope, "VALIDATION_ERROR", recovery="correctable")

    def test_wrong_type_revision_on_rest_emits_invalid_request(self, env_with_media_buy):
        """On REST the typed body model rejects a non-integer revision during body
        parsing, before the shared boundary -> INVALID_REQUEST (documented
        per-transport divergence; classification policy tracked in #1604).
        Pinned as documented: if REST converges onto VALIDATION_ERROR (or
        diverges further), this goes red."""
        env, media_buy = env_with_media_buy
        result = env.call_via(Transport.REST, media_buy_id=media_buy.media_buy_id, paused=True, revision="not-an-int")

        assert result.is_error, "expected a body-parse rejection for a non-integer revision"
        assert result.wire_error_envelope is not None, "wire envelope not captured"
        assert_envelope_shape(result.wire_error_envelope, "INVALID_REQUEST", recovery="correctable")
