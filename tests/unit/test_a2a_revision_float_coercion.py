"""B8 (#1544): pin the A2A whole-number-float ``revision`` coercion at the skill boundary.

A2A carries numbers as protobuf doubles, so an inbound integer ``revision`` arrives as a
whole-number float (7 -> 7.0). ``_handle_update_media_buy_skill`` coerces whole-number
floats back to ``int`` so a round-tripped optimistic-concurrency token is accepted, while
a non-integral float (7.5) stays a float and is rejected downstream.

The BDD/harness A2A leg (``tests/harness/media_buy_dual.py::_call_update_a2a``) calls
``update_media_buy_raw`` DIRECTLY and never exercises this skill-boundary coercion, so it
would stay green if the coercion were deleted. These tests call the REAL skill handler and
assert the value handed to core — deleting the coercion turns
``test_whole_number_float_revision_coerced_to_int`` red.
"""

import asyncio
from unittest.mock import MagicMock, patch

from tests.factories import PrincipalFactory

_MOCK_IDENTITY = PrincipalFactory.make_identity(principal_id="principal_123", tenant_id="tenant_123", protocol="a2a")


def _revision_handed_to_core(revision_param: object) -> object:
    """Invoke the real ``_handle_update_media_buy_skill`` and return the ``revision`` it forwards to core."""
    from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

    handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
    captured: dict[str, object] = {}

    def _fake_core(**kwargs: object) -> MagicMock:
        captured["revision"] = kwargs.get("revision")
        result = MagicMock()
        result.model_dump.return_value = {}
        return result

    with patch("src.a2a_server.adcp_a2a_server.core_update_media_buy_tool", side_effect=_fake_core):
        asyncio.run(
            handler._handle_update_media_buy_skill(
                parameters={"media_buy_id": "mb_1", "revision": revision_param},
                identity=_MOCK_IDENTITY,
            )
        )
    return captured["revision"]


class TestA2ARevisionFloatCoercion:
    """The skill boundary normalizes protobuf whole-number floats but not fractional ones."""

    def test_whole_number_float_revision_coerced_to_int(self):
        """7.0 (protobuf double for integer 7) is coerced to int 7 before reaching core."""
        result = _revision_handed_to_core(7.0)
        assert result == 7
        assert isinstance(result, int)

    def test_non_integral_float_revision_left_as_float(self):
        """7.5 is NOT integral — it stays a float (rejected downstream), never silently truncated."""
        result = _revision_handed_to_core(7.5)
        assert result == 7.5
        assert isinstance(result, float)

    def test_integer_revision_passes_through(self):
        """A genuine int (as MCP/REST deliver it) is forwarded unchanged."""
        result = _revision_handed_to_core(7)
        assert result == 7
        assert isinstance(result, int)
