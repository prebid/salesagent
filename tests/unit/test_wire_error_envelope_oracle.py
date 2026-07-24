"""Regression tests for the error-path wire oracle (tests.bdd.steps._outcome_helpers.wire_error_envelope)."""

from unittest.mock import MagicMock

import pytest

from tests.bdd.steps._outcome_helpers import wire_error_envelope
from tests.harness.transport import Transport


@pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST])
def test_real_transport_missing_capture_raises(transport):
    """A real-wire transport reporting no envelope, with no exemption, is a dispatcher bug."""
    result = MagicMock(wire_error_envelope=None, wire_capture_unavailable=False)

    with pytest.raises(AssertionError, match="wire_error_envelope missing"):
        wire_error_envelope({"transport": transport, "result": result})


def test_a2a_direct_raw_dispatch_returns_none_without_raising():
    """A2A's documented direct-raw mode (e.g. CreativeSyncEnv) never captures wire — by design.

    Regression guard: an earlier version of this oracle could not distinguish
    "this dispatch mode never promised wire" from "the dispatcher failed to
    stash it", so every direct-raw A2A error assertion would have raised a
    false "dispatcher regression" alarm the moment it was used.
    """
    result = MagicMock(wire_error_envelope=None, wire_capture_unavailable=True)

    assert wire_error_envelope({"transport": Transport.A2A, "result": result}) is None


def test_a2a_full_pipeline_missing_capture_still_raises():
    """The exemption is narrow: a full-pipeline A2A dispatch still owes a real envelope."""
    result = MagicMock(wire_error_envelope=None, wire_capture_unavailable=False)

    with pytest.raises(AssertionError, match="wire_error_envelope missing"):
        wire_error_envelope({"transport": Transport.A2A, "result": result})


def test_captured_envelope_is_returned():
    envelope = {"adcp_error": {"code": "VALIDATION_ERROR"}}
    result = MagicMock(wire_error_envelope=envelope, wire_capture_unavailable=False)

    assert wire_error_envelope({"transport": Transport.REST, "result": result}) == envelope


def test_impl_transport_returns_none_without_raising():
    result = MagicMock(wire_error_envelope=None, wire_capture_unavailable=False)

    assert wire_error_envelope({"transport": Transport.IMPL, "result": result}) is None


def test_pre_dispatch_rejection_returns_none_without_raising():
    """No ``result`` at all (rejected before reaching any transport) is legitimate regardless of transport."""
    assert wire_error_envelope({"transport": Transport.A2A, "result": None}) is None
    assert wire_error_envelope({"transport": Transport.A2A}) is None
