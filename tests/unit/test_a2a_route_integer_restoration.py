"""Unit test for the production-side fix: src/app.py wraps the
real ``/a2a`` JSON-RPC route so integer-typed AdCP fields survive the
a2a-sdk's protobuf Struct/Value -> JSON conversion (which otherwise widens
every number to a double; see restore_a2a_integer_types in
src/a2a_server/adcp_a2a_server.py for the root cause).

This tests the ASGI-level wrapper (_restore_a2a_wire_integers) in isolation
-- the actual HTTP boundary where a real client would otherwise still see
86400.0 even with the harness-side fix (tests/utils/a2a_helpers.py) applied,
since the harness's in-process capture never goes through this route.
"""

from __future__ import annotations

import json

from starlette.responses import JSONResponse, PlainTextResponse


class TestA2ARouteIntegerRestoration:
    def test_wrapped_endpoint_restores_known_integer_fields(self):
        from src.app import _restore_a2a_wire_integers

        async def fake_endpoint(request):
            return JSONResponse(
                {
                    "result": {
                        "artifacts": [
                            {
                                "parts": [
                                    {
                                        "data": {
                                            "adcp": {"idempotency": {"replay_ttl_seconds": 86400.0}},
                                            "message": "capabilities",
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                }
            )

        wrapped = _restore_a2a_wire_integers(fake_endpoint)

        import asyncio

        response = asyncio.run(wrapped(request=None))

        body = json.loads(bytes(response.body))
        replay_ttl_seconds = body["result"]["artifacts"][0]["parts"][0]["data"]["adcp"]["idempotency"][
            "replay_ttl_seconds"
        ]
        assert isinstance(replay_ttl_seconds, int)
        assert replay_ttl_seconds == 86400

    def test_wrapped_endpoint_passes_through_non_json_response_unchanged(self):
        from src.app import _restore_a2a_wire_integers

        async def fake_endpoint(request):
            return PlainTextResponse("not json")

        wrapped = _restore_a2a_wire_integers(fake_endpoint)

        import asyncio

        response = asyncio.run(wrapped(request=None))
        assert isinstance(response, PlainTextResponse)
        assert response.body == b"not json"

    def test_wrapped_endpoint_does_not_touch_unlisted_fields(self):
        """A whole-numbered float NOT in A2A_WIRE_INTEGER_FIELDS stays a float --
        the coercion must not blanket-convert every whole-numbered number."""
        from src.app import _restore_a2a_wire_integers

        async def fake_endpoint(request):
            return JSONResponse({"average_bid_cpm": 5.0})

        wrapped = _restore_a2a_wire_integers(fake_endpoint)

        import asyncio

        response = asyncio.run(wrapped(request=None))
        body = json.loads(bytes(response.body))
        assert body["average_bid_cpm"] == 5.0
        assert isinstance(body["average_bid_cpm"], float)
