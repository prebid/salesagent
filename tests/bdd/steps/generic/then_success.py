"""Then steps for success assertions (response status, fields, sandbox).

These steps assert on ``ctx["response"]`` which holds a real response
object from production code (any use case).
"""

from __future__ import annotations

from pytest_bdd import parsers, then

# ── Response status ──────────────────────────────────────────────────


# Success collections that prove a status-less response is genuinely
# "completed" — at least one must be present and a list, not None.
_STATUSLESS_SUCCESS_ATTRS: tuple[str, ...] = (
    "formats",  # ListCreativeFormatsResponse
    "media_buy_deliveries",  # GetMediaBuyDeliveryResponse
    "aggregated_totals",  # GetMediaBuyDeliveryResponse
)


@then(parsers.parse('the response status should be "{status}"'))
def then_response_status(ctx: dict, status: str) -> None:
    """Assert the operation completed with expected status.

    Works across use cases:
    - UC-004 (GetMediaBuyDeliveryResponse): has explicit ``status`` field —
      assert it equals the expected value directly.
    - UC-005 (ListCreativeFormatsResponse): no ``status`` field. Such
      response types can only represent the *completed* state, so
      "completed" is proven by (a) no error recorded for the operation and
      (b) the schema-required success payload being present. Any requested
      status other than "completed" against a status-less response fails.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"

    # Determine if response type declares a ``status`` field via Pydantic metadata.
    # Uses getattr on the class (not instance) to handle non-Pydantic test doubles.
    resp_fields = getattr(type(resp), "model_fields", {})
    if "status" in resp_fields:
        actual = resp.status
        # SDK 5.7: status may be a non-StrEnum; extract .value for comparison
        actual_str = actual.value if hasattr(actual, "value") else str(actual)
        assert actual_str == status, f"Expected status '{status}', got '{actual_str}'"
        return

    # Status-less response: only the completed/success state is representable.
    if status != "completed":
        raise AssertionError(
            f"Status '{status}' requested but response {type(resp).__name__} "
            f"has no status field — status-less responses can only be 'completed'"
        )

    # "completed" must be proven, not assumed from a non-None object.
    error = ctx.get("error")
    assert error is None, f"Status 'completed' claimed but the operation recorded an error: {error!r}"

    # Verify at least one schema-required success collection is present and populated.
    found_count = 0
    for attr in _STATUSLESS_SUCCESS_ATTRS:
        if attr not in resp_fields:
            continue
        found_count += 1
        value = getattr(resp, attr)
        assert value is not None, (
            f"Status 'completed' claimed but response.{attr} is None — the schema-required success payload is missing"
        )
        if attr in ("formats", "media_buy_deliveries"):
            assert isinstance(value, list), (
                f"Status 'completed' claimed but response.{attr} is {type(value).__name__}, expected a list"
            )
    assert found_count >= 1, (
        f"Status-less response {type(resp).__name__} exposes none of the "
        f"expected success collections {_STATUSLESS_SUCCESS_ATTRS} — cannot "
        f"prove the operation completed successfully"
    )


# ── Response contains field ──────────────────────────────────────────


@then(parsers.parse('the response should contain "{field}" array'))
def then_response_contains_array(ctx: dict, field: str) -> None:
    """Assert response contains a field that is an array (list)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    value = getattr(resp, field, None)
    assert value is not None, f"Expected '{field}' in response, got attrs: {dir(resp)}"
    assert isinstance(value, list), f"Expected '{field}' to be a list, got {type(value)}"


# ── Sandbox flag assertions ──────────────────────────────────────────


@then("the response should include sandbox equals true")
def then_sandbox_true(ctx: dict) -> None:
    """Assert response includes sandbox: true."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    assert getattr(resp, "sandbox", None) is True, f"Expected sandbox=True, got {getattr(resp, 'sandbox', None)}"


@then("the response should not include a sandbox field")
def then_no_sandbox_field(ctx: dict) -> None:
    """Assert serialized response does not contain a sandbox field.

    Checks model_dump() (what API consumers see), not the Python attribute.
    A field present with value None still serializes as ``{"sandbox": null}``
    which counts as "including a sandbox field".
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    dumped = resp.model_dump()
    assert "sandbox" not in dumped, (
        f"Expected no sandbox field in serialized response, got sandbox={dumped.get('sandbox')}"
    )


# ── No real API calls assertion ──────────────────────────────────────


@then("no real ad platform API calls should have been made")
def then_no_real_api_calls(ctx: dict) -> None:
    """Assert no real ad platform API calls were made.

    Operation-agnostic: works across all use cases (UC-001 products,
    UC-004 delivery, UC-005 creative formats, UC-018 list creatives,
    UC-019 query media buys) by inspecting the harness environment's
    external patches rather than checking a single mock by name.

    Three-part verification:
    1. Production produced a response (the operation actually ran).
    2. The harness has active external service patches — every external
       integration point is replaced by a mock, so real HTTP/SOAP/gRPC
       calls cannot escape the patch boundary.
    3. The response carries ``sandbox=True``, corroborating that
       production took the simulated/sandbox code path.
    """
    env = ctx["env"]
    assert env is not None, "Expected harness env in ctx — without the harness, real API calls could occur"

    # 1. Production must have produced a response. A missing response means
    #    the operation didn't run — that's a test failure, not a vacuous pass.
    resp = ctx.get("response")
    assert resp is not None, (
        "Expected a response from production code but none found — "
        "cannot verify 'no real API calls' without a completed operation"
    )

    # 2. The harness must have external service patches active. Each harness
    #    env declares EXTERNAL_PATCHES that replace real ad-platform clients
    #    (adapter, registry, etc.) with mocks. If external patches exist,
    #    real calls are structurally impossible — the import target is replaced.
    external_patches = getattr(env, "EXTERNAL_PATCHES", {})
    assert len(external_patches) > 0 or len(env.mock) > 0, (
        f"Harness {type(env).__name__} has no external patches and no active mocks — "
        "cannot guarantee real ad-platform calls were suppressed"
    )

    # Verify at least one external mock was exercised by production code.
    # This proves production actually ran through the patched seam (not that
    # it silently skipped the external call entirely and returned a stub).
    any_external_mock_called = any(mock.called for mock in env.mock.values())
    assert any_external_mock_called, (
        f"None of the harness mocks ({list(env.mock.keys())}) were called — "
        "production code may have bypassed all patched external services. "
        f"Harness: {type(env).__name__}"
    )

    # 3. Corroborate via the sandbox flag on the response. The sandbox=True
    #    flag proves the sandbox/simulated code path served the result.
    sandbox = getattr(resp, "sandbox", None)
    assert sandbox is True, (
        f"Expected sandbox=True on response confirming simulated mode, "
        f"got sandbox={sandbox!r}. Without sandbox=True, the response may "
        f"have been served by real ad-platform integration."
    )
