"""Steps for local-pre-dispatch-refusals.feature.

The scenarios send documents no request model accepts and names no registry knows, and
grade where each is refused: by the request model, as an AdCP INVALID_REQUEST body, or by
the transport's own protocol, with no AdCP body at all. The feature header states the
pinned authority for each. This module is only the wiring.

WHY THE DOCUMENT IS RAW. ``dispatch_request`` takes a keyword BAG and serializes it, so it
can only ever send a JSON object addressed at a known tool. Every document here is one of
the shapes that bag cannot take -- bytes that are not JSON, a JSON list, a message with no
skill or two, a name the registry lacks -- which is why the When goes through
``dispatch_raw_document`` (``tests/bdd/steps/generic/_dispatch.py``), the one entry that
hands a document to the wire as written and publishes the result through the same single
ctx writer every other dispatch uses.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import error_envelope_or_none
from tests.bdd.steps.generic._dispatch import dispatch_raw_document
from tests.harness.raw_wire import RawDocument
from tests.harness.transport import DERIVED_STATUS_TRANSPORT_FAULT

#: The ctx slot holding the document to send. Named so nothing else writes it.
_DOCUMENT = "predispatch_document"

#: The tool every routable scenario addresses: the env the @predispatch tag builds serves
#: it on all three transports, and its request model refuses a non-object on its own.
_TOOL = "get_products"

#: A name no registry row carries.
_UNKNOWN_TOOL = "summon_unicorns"


# ── Given ────────────────────────────────────────────────────────────


@given("the buyer sends bytes that are not JSON as the request body")
def given_not_json(ctx: dict) -> None:
    ctx[_DOCUMENT] = RawDocument(tool=_TOOL, body=b"not json {")


@given("the buyer sends a JSON list as the request body")
def given_list_body(ctx: dict) -> None:
    ctx[_DOCUMENT] = RawDocument(tool=_TOOL, body=[1, 2])


@given("the buyer's A2A message names no skill")
def given_no_skill(ctx: dict) -> None:
    """A DataPart that carries a document but no ``skill`` key: nothing to route on."""
    ctx[_DOCUMENT] = RawDocument(tool=_TOOL, a2a_parts=({"brief": "video inventory"},))


@given("the buyer's A2A message names two skills")
def given_two_skills(ctx: dict) -> None:
    ctx[_DOCUMENT] = RawDocument(
        tool=_TOOL,
        a2a_parts=({"skill": _TOOL, "input": {}}, {"skill": "list_creative_formats", "input": {}}),
    )


@given("the buyer names a tool the seller does not have")
def given_unknown_tool(ctx: dict) -> None:
    ctx[_DOCUMENT] = RawDocument(tool=_UNKNOWN_TOOL, body={})


# ── When ─────────────────────────────────────────────────────────────


@when("the Buyer Agent sends the document as written")
def when_send_document(ctx: dict) -> None:
    document = ctx[_DOCUMENT]
    env = ctx["env"]
    if document.tool != _UNKNOWN_TOOL:
        assert env.MCP_TOOL == document.tool, (
            f"the document addresses {document.tool!r}, but the routing tag selected "
            f"{type(env).__name__}, which serves {env.MCP_TOOL!r}"
        )
    dispatch_raw_document(ctx, document)


# ── Then ─────────────────────────────────────────────────────────────


@then(parsers.parse("the transport refuses the message with JSON-RPC error {code:d}"))
def then_jsonrpc_refusal(ctx: dict, code: int) -> None:
    """The A2A layer answered with its own error, and this is the code it chose."""
    envelope = ctx["result"].envelope
    assert envelope.get("status") == DERIVED_STATUS_TRANSPORT_FAULT, (
        f"expected a refusal by the transport itself, got {envelope!r}"
    )
    assert envelope.get("jsonrpc_error_code") == code, (
        f"expected JSON-RPC error {code}, got {envelope.get('jsonrpc_error_code')!r} ({envelope!r})"
    )


@then("the route is not found")
def then_route_not_found(ctx: dict) -> None:
    envelope = ctx["result"].envelope
    assert envelope.get("status_code") == 404, f"expected HTTP 404 for an unknown route, got {envelope!r}"


@then("the MCP server reports the tool unknown")
def then_mcp_tool_unknown(ctx: dict) -> None:
    envelope = ctx["result"].envelope
    assert envelope.get("status") == DERIVED_STATUS_TRANSPORT_FAULT, (
        f"expected a refusal by the MCP server itself, got {envelope!r}"
    )
    assert "Unknown tool" in envelope.get("mcp_tool_error", ""), (
        f"expected the MCP server's unknown-tool error, got {envelope.get('mcp_tool_error')!r}"
    )


@then("no AdCP response was produced")
def then_no_adcp_response(ctx: dict) -> None:
    """A refusal made before any tool was named carries no AdCP body, success or error."""
    assert error_envelope_or_none(ctx) is None, (
        f"a transport-level refusal must carry no AdCP error body, got {error_envelope_or_none(ctx)!r}"
    )
    assert ctx["result"].wire_response is None, (
        f"a transport-level refusal must carry no AdCP success body, got {ctx['result'].wire_response!r}"
    )
