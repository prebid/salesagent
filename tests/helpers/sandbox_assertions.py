"""Shared reader for the sandbox mode of production ``get_adapter`` calls.

Three test modules were extracting ``call.kwargs["sandbox"]`` independently. One reader
means one place to fix if the keyword is ever renamed — and one place that knows an EMPTY
call list must never satisfy a sandbox assertion, which is how earlier attempts at these
oracles read green while grading nothing.
"""

from __future__ import annotations

from typing import Any


def sandbox_modes(mock_get_adapter: Any) -> list[bool]:
    """The ``sandbox=`` value of every recorded get_adapter call, in call order.

    Raises when a call omitted the keyword: every call site is required to decide
    explicitly (see tests/unit/test_architecture_get_adapter_sandbox.py), so a missing
    keyword is a defect rather than a default.
    """
    modes: list[bool] = []
    for call in mock_get_adapter.call_args_list:
        if "sandbox" not in call.kwargs:
            raise AssertionError(
                f"get_adapter called without an explicit sandbox= argument: {call}. "
                "Every call site must decide the mode explicitly."
            )
        value = call.kwargs["sandbox"]
        if type(value) is not bool:
            raise AssertionError(
                f"get_adapter called with a non-bool sandbox= argument: {value!r} ({type(value).__name__}) "
                f"in {call}. A falsy-but-not-False value (None, 0, '') would silently satisfy "
                "assert_all_live() as if it were an explicit live decision."
            )
        modes.append(value)
    return modes


def assert_all_sandbox(mock_get_adapter: Any, *, context: str) -> None:
    """Assert at least one adapter was built and every one was a sandbox adapter."""
    modes = sandbox_modes(mock_get_adapter)
    assert modes, f"{context}: no adapter was constructed — this assertion would be vacuous"
    assert all(modes), (
        f"{context}: a LIVE adapter was constructed for a sandbox account (modes={modes}); "
        "the request would reach the tenant's real ad platform"
    )


def assert_all_live(mock_get_adapter: Any, *, context: str) -> None:
    """Negative control: at least one adapter, and none of them sandbox."""
    modes = sandbox_modes(mock_get_adapter)
    assert modes, f"{context}: no adapter was constructed — this assertion would be vacuous"
    assert not any(modes), f"{context}: expected the live adapter for a live account, got {modes}"
