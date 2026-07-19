"""Regression tests for UC-010 success-path wire authority."""

from unittest.mock import MagicMock

import pytest

from tests.bdd.steps.domain.uc010_version_negotiation import (
    then_response_context_equals,
    then_response_no_context,
)
from tests.harness.transport import Transport


@pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST])
@pytest.mark.parametrize(
    "assertion,args",
    [
        (then_response_context_equals, ('{"trace_id":"abc"}',)),
        (then_response_no_context, ()),
    ],
)
def test_real_transport_cannot_fall_back_to_typed_response(transport, assertion, args):
    """Missing real-wire capture must fail even when the typed payload would pass."""
    response = MagicMock()
    response.model_dump.return_value = {"context": {"trace_id": "abc"}}

    with pytest.raises(AssertionError, match="wire_response missing"):
        assertion({"transport": transport, "response": response}, *args)


def test_impl_transport_uses_production_serialization_fallback():
    response = MagicMock()
    response.model_dump.return_value = {"context": {"trace_id": "abc"}}

    then_response_context_equals(
        {"transport": Transport.IMPL, "response": response},
        '{"trace_id":"abc"}',
    )


def test_wire_is_authoritative_over_typed_response():
    response = MagicMock()
    response.model_dump.return_value = {"context": {"trace_id": "typed"}}
    ctx = {
        "transport": Transport.MCP,
        "response": response,
        "wire_response": {"context": {"trace_id": "wire"}},
    }

    with pytest.raises(AssertionError, match="Echoed context"):
        then_response_context_equals(ctx, '{"trace_id":"typed"}')


def test_absent_context_is_distinct_from_null():
    response = MagicMock()
    then_response_no_context({"transport": Transport.REST, "response": response, "wire_response": {}})

    with pytest.raises(AssertionError, match="context field to be absent"):
        then_response_no_context(
            {
                "transport": Transport.REST,
                "response": response,
                "wire_response": {"context": None},
            }
        )


def test_context_equality_requires_wire_field_presence():
    with pytest.raises(AssertionError, match="contain the echoed context"):
        then_response_context_equals(
            {"transport": Transport.A2A, "response": MagicMock(), "wire_response": {}},
            '{"trace_id":"abc"}',
        )
