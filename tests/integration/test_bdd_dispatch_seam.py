"""The fold deleted a per-transport ``req`` shaping. This proves nothing was lost.

``when_request._call_via`` used to flatten ``req.model_dump(exclude_none=True)`` into
the kwargs FOR MCP ONLY and pass ``req=`` on a2a/rest. Folding it into
``dispatch_request`` deleted that branch, on the ground that the env's own
``deliver_mcp`` already performs the identical dump (``_run_mcp_client`` pops ``req``
and merges ``req.model_dump(exclude_none=True)`` under the caller's explicit kwargs).

That ground is an argument, and an argument is not a measurement. The measurement is
here: send a ``format_ids`` FILTER through the seam on every transport and require the
seller to honour it. If ``req`` stopped reaching the tool as arguments on MCP, the
filter would be dropped and the full reference catalog would come back — so this fails
on exactly the regression the deletion could have caused, rather than restating the
implementation.

The equivalence is real but NOT universal: an env on the base client-core path would
send ``{"req": <model>}`` as the MCP arguments and filter nothing. Both remaining
``req``-passing callers dispatch on ``CreativeFormatsEnv``, which overrides
``deliver_mcp``; the next env to use this seam has to check that for itself, and this
test is where it will find out.
"""

from __future__ import annotations

import pytest

from src.core.schemas import FormatId, ListCreativeFormatsRequest
from tests.bdd.steps.generic.when_request import DEFAULT_AGENT_URL, _call_via
from tests.harness.creative_formats import CreativeFormatsEnv

#: One of the twelve pin-expressible reference formats (the same id UC-005's
#: roundtrip scenario seeds, for the reason its comment gives).
FILTERED_FORMAT_ID = "video_vast"


@pytest.mark.requires_db
class TestCallViaForwardsReqWholeAndTheEnvUnpacksIt:
    @pytest.mark.parametrize("transport", ["rest", "a2a", "mcp"])
    def test_a_format_ids_filter_still_reaches_the_tool(self, transport: str, integration_db: object) -> None:
        with CreativeFormatsEnv() as env:
            ctx: dict = {"env": env}
            req = ListCreativeFormatsRequest(format_ids=[FormatId(agent_url=DEFAULT_AGENT_URL, id=FILTERED_FORMAT_ID)])
            _call_via(ctx, transport, req=req)

            assert "error" not in ctx, f"{transport}: dispatch failed: {ctx.get('error')!r}"
            result = ctx["result"]
            assert result.is_success, f"{transport}: {result.error!r}"
            returned = [str(fmt.format_id.id) for fmt in result.payload.formats]
            assert returned == [FILTERED_FORMAT_ID], (
                f"{transport}: expected the seller to return only the filtered format; got {returned[:5]} "
                f"({len(returned)} formats). An unfiltered catalog here means req never reached the tool "
                "as arguments — which is exactly what deleting _call_via's MCP flattening would cause if "
                "the env did not do the same dump."
            )
