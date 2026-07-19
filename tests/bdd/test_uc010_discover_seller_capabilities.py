"""Selected live bindings from the upstream-generated UC-010 feature.

Only scenarios with complete production steps and harness support are collected.
The generated feature's stale capability-filter cases are intentionally not
collected; the AdCP 3.1.1 companion owns that contract without masking anything
behind an xfail.
"""

from __future__ import annotations

from pytest_bdd import scenario

_FEATURE = "features/BR-UC-010-discover-seller-capabilities.feature"


@scenario(_FEATURE, "context_provided — context echoed in capabilities response via MCP")
def test_context_echo_mcp() -> None:
    pass


@scenario(_FEATURE, "context_provided — context echoed via A2A, context with properties")
def test_context_echo_a2a() -> None:
    pass


@scenario(_FEATURE, "context_absent — context absent, no context in request means no context in response")
def test_context_absent() -> None:
    pass


@scenario(_FEATURE, "context_nested — deeply nested context object echoed unchanged")
def test_context_nested() -> None:
    pass


@scenario(_FEATURE, "context_empty_object — empty context echoed, context = {}")
def test_context_empty() -> None:
    pass


@scenario(_FEATURE, "Context echo implementation gap in capabilities endpoint")
def test_generated_context_gap_is_closed() -> None:
    pass


@scenario(_FEATURE, "version-unsupported — VERSION_UNSUPPORTED error carries authoritative supported_versions")
def test_version_unsupported() -> None:
    pass


@scenario(
    _FEATURE,
    "version-unsupported-major-fallback — major-version negotiation falls back to supported_versions",
)
def test_version_unsupported_major_fallback() -> None:
    pass


@scenario(
    _FEATURE,
    "version-unsupported-build-version-advisory — build_version is advisory and MUST NOT drive negotiation",
)
def test_version_unsupported_build_version_advisory() -> None:
    pass


@scenario(_FEATURE, "details (VERSION_UNSUPPORTED error) boundary - <boundary_point>")
def test_version_unsupported_details_bounds() -> None:
    pass
