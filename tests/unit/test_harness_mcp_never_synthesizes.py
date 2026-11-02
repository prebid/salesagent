"""MCP must not offer a rebuilt copy of an envelope it already captures.

``synthesized_error_envelope`` is what production WOULD emit for an exception,
recomputed by the harness from the same ``AdcpErrorResponse`` production builds. On IMPL that is
honest: there is no wire by definition, so the synthesized value is the only view
that exists and its name says so. On MCP there IS a wire, so the field is either
redundant (the wire is present) or a mask (the wire was lost) -- and a mask is
what let ``MediaBuyListEnv`` declare a wire it never captured, right up until
the fix in #1802.

The MCP error unwrap's own comment already said "NEVER the synthesized fallback --
a dead MCP wire path must yield None here" (``unwrap_mcp_error``,
``tests/harness/client.py``, delegated to by ``McpDispatcher``). The construction
it replaced passed one anyway. These tests pin the comment.
"""

import json

import pytest
from fastmcp.exceptions import ToolError

from src.core.exceptions import AdCPValidationError
from tests.harness._base import WireError
from tests.harness.dispatchers import A2ADispatcher, McpDispatcher, RestDispatcher
from tests.helpers.envelope_assertions import envelope_for


def _raising_env(exc: Exception):
    """A stand-in env whose every transport entry point raises *exc*."""

    class _Env:
        # The entry points the dispatchers ACTUALLY call. Stubbing anything
        # else lets the dispatcher die on an AttributeError BEFORE it reaches
        # the error branch under test, and the assertions below then hold for a
        # reason unrelated to what they claim to test -- both of the "no
        # envelope" ones pass by construction on a dispatch that never ran.
        #
        # #1858 renamed the env dispatch contract call_mcp/call_a2a ->
        # deliver_mcp/deliver_a2a: the deliver_* pair is now THE override point
        # (it returns a DeliverResult carrying payload AND wire), while
        # call_mcp/call_a2a are defined once on BaseTestEnv as
        # ``deliver_*(...).payload`` and are explicitly never overridden, so
        # stubbing the old names would let both dispatchers die on an
        # AttributeError before the branch under test. There is no deliver_rest --
        # RestDispatcher reads ``REST_ENDPOINT`` and then calls
        # ``_run_rest_request`` -- and the IMPL leg still calls ``call_impl``.
        REST_ENDPOINT = "/stub"

        def call_impl(self, **kwargs):
            raise exc

        def deliver_mcp(self, **kwargs):
            raise exc

        def deliver_a2a(self, **kwargs):
            raise exc

        def _run_rest_request(self, endpoint, **kwargs):
            raise exc

        def build_rest_body(self, **kwargs):
            return {}

        def parse_rest_response(self, data):
            return data

    return _Env()


def _an_error() -> AdCPValidationError:
    """A typed production error, constructed the way production constructs one.

    Keyword-only, and no ``message``: buyer-facing text is a read-only property
    over CODE_TABLE, so ``AdCPSalesAgentError.__init__`` takes no message
    parameter at all. Passing one is a TypeError at construction, which would
    error every case in this file rather than grade anything.
    """
    return AdCPValidationError(field="push_notification_config.authentication.credentials")


class TestMcpDoesNotSynthesize:
    def test_a_typed_error_with_no_captured_wire_yields_no_envelope_at_all(self):
        """A dead MCP wire path must produce nothing to read, not a rebuilt copy.

        This is the whole point: a test downstream that falls back to the
        synthesized value would go green off a value regenerated from the
        exception, which cannot witness a regression in the production
        translator -- both sides compute it from the same in-memory object.
        """
        result = McpDispatcher().dispatch(_raising_env(_an_error()))

        assert result.wire_error_envelope is None

    def test_a_tool_error_carrying_wire_json_is_read_as_the_wire(self):
        """The real capture path still works -- asserted with a REAL envelope.

        Without this case the sibling above is vacuous: a bare exception is
        neither a ToolError carrying JSON nor stash-carrying, so both capture
        paths return None by construction and the file would stay green with
        MCP's wire capture entirely dead. That is the exact defect pldmk.24
        fixed one commit ago, so it is the one this file must be able to see.
        """
        envelope = envelope_for(_an_error())
        result = McpDispatcher().dispatch(_raising_env(ToolError(json.dumps(envelope))))

        assert result.wire_error_envelope == envelope

    def test_a_wire_error_carrying_the_captured_envelope_is_read_as_the_wire(self):
        """The second capture path: the ``WireError`` production actually raises.

        This is the MCP failure shape that reaches the dispatcher in practice.
        ``BaseTestEnv._run_mcp_client`` catches the FastMCP failure, recovers the
        two-layer envelope with ``_mcp_wire_envelope``, and raises
        ``WireError(envelope)`` (``tests/harness/_base.py``); the envelope rides
        out on ``.envelope``, which is the ONE attribute
        ``transport._wire_envelope_from_exception`` reads.

        The double used to hand-plant a ``_wire_error_envelope`` attribute on a
        plain ``AdCPValidationError`` instead. That attribute had no producer
        left anywhere in the tree, so the case graded an exception shape
        production cannot raise: the guard could not see the reader drifting off
        the real one, which is exactly the live regression it exists to catch.
        """
        envelope = envelope_for(_an_error())

        result = McpDispatcher().dispatch(_raising_env(WireError(envelope)))

        assert result.wire_error_envelope == envelope


class TestOnlyTheTransportWithNoWireMaySynthesize:
    """The contract ``TransportResult`` already documents, pinned as a whole.

    Pinning only the deleted construction would grade "line 229 stayed deleted".
    Pinning every dispatcher grades the rule that line violated, which is what
    stops the field quietly becoming a fallback again on some other transport.

    A2A passing here does NOT mean A2A is clean. It leaves this field ``None``
    while putting a builder-regenerated envelope into ``wire_error_envelope``
    instead -- the same substitution under the name of the real thing, which is
    strictly worse and is why it needs its own change (#1417).
    """

    @pytest.mark.parametrize("dispatcher", [A2ADispatcher, McpDispatcher, RestDispatcher])
    def test_a_transport_that_has_a_wire_never_synthesizes(self, dispatcher):
        """``wire_error_envelope is None`` is the whole assertion now.

        This once asserted a second, private ``_synthesized_error_envelope``
        field as well. That field is deleted with ``Transport.IMPL``: nothing
        synthesizes an envelope any more, so there is no channel to close. The
        review that added the pair (Chris SF3, #1802) named the private half as
        the one that did NOT redden, so nothing that graded is lost.
        The deleted fallback (then in ``tests/harness/dispatchers.py``, now one
        unwrap per transport family in ``tests/harness/client.py``) did not put
        a rebuilt envelope in the private field -- it handed it back under
        ``wire_error_envelope``, the name of the thing it was impersonating. So
        re-introducing that line left this case green: the private field stayed
        None either way. Probed on the pre-fix tree, exactly as the review
        describes.

        ``wire_error_envelope is None`` is the assertion that reddens. It is the
        A2A twin of what ``TestMcpDoesNotSynthesize`` one class up already
        asserts for MCP, and its absence is what this file's own docstring
        named as the gap.
        """
        result = dispatcher().dispatch(_raising_env(_an_error()))

        assert result.wire_error_envelope is None
