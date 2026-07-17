"""Domain step definitions for UC-010 version negotiation (VERSION_UNSUPPORTED).

Covers the four BR-UC-010 scenarios derived from the pinned schema
(``error-details/version-unsupported.json`` + ``core/version-envelope.json``
at v3.1.0-beta.3, @source commit 04f59d2d5), plus the hand-authored
release-resolution companion grounded in ``docs/reference/versioning.mdx``:

- version-unsupported (string ``adcp_version`` pin)
- version-unsupported-major-fallback (deprecated int ``adcp_major_version``)
- version-unsupported-build-version-advisory
- details (VERSION_UNSUPPORTED error) boundary outline
- cross-major pins above and below the seller's native major
- stable same-major pins below the seller's minimum release
- unmatched prerelease pins

Every scenario dispatches through the parametrized ``ctx["transport"]``
(MCP / A2A / REST), so each transport's boundary validation is graded.
Error assertions read the REAL wire envelope (``ctx["wire_error_envelope"]``)
per the Error Verification Policy.
"""

from __future__ import annotations

import json
import re

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.generic._dispatch import dispatch_request

# ── context echo helpers (BR-RULE-043 / POST-S9) ─────────────────────


_CONTEXT_ABSENT = object()  # sentinel: the `context` key is not present on the wire body


def _response_context(ctx: dict) -> object:
    """The echoed context as a plain JSON value, read from the REAL wire body.

    When a wire body exists (REST/MCP/A2A) it is the SOLE authority — a missing
    echo must surface to the assertion, never be silently patched from the typed
    payload. A wire body that OMITS ``context`` returns the ``_CONTEXT_ABSENT``
    sentinel; a wire body carrying ``"context": null`` returns ``None``. The two
    are distinct: callers must not treat an explicit null as an omitted field.

    Only when NO wire body was stashed (IMPL, which has no wire by definition) does
    it fall back to the typed payload.
    """
    wire = ctx.get("wire_response")
    if isinstance(wire, dict):
        return wire["context"] if "context" in wire else _CONTEXT_ABSENT
    # No wire body (IMPL only): fall back to the typed payload.
    resp = ctx.get("response")
    assert resp is not None, f"Expected a capabilities response, got error: {ctx.get('error')!r}"
    context = getattr(resp, "context", None)
    if context is None:
        return _CONTEXT_ABSENT
    return context.model_dump(mode="json") if hasattr(context, "model_dump") else context


# Release-precision wire pattern for negotiation pins (version-envelope.json).
_RELEASE_PIN_PATTERN = r"^\d+\.\d+(-[a-zA-Z0-9.-]+)?$"


# ── Helpers ──────────────────────────────────────────────────────────


def _wire_error(ctx: dict) -> dict:
    """The single error object from the captured two-layer wire envelope."""
    envelope = ctx["wire_error_envelope"]
    assert envelope is not None, "No wire error envelope captured — did the dispatch fail on the wire?"
    errors = envelope["errors"]
    assert len(errors) == 1, f"Expected exactly one wire error, got {len(errors)}"
    return errors[0]


def _wire_error_details(ctx: dict) -> dict:
    details = _wire_error(ctx).get("details")
    assert isinstance(details, dict), f"Expected structured details on the wire error, got {details!r}"
    return details


def _supported_versions(ctx: dict) -> list[str]:
    versions = _wire_error_details(ctx)["supported_versions"]
    assert isinstance(versions, list), f"supported_versions must be an array, got {type(versions).__name__}"
    return versions


def _repin_and_retry(ctx: dict) -> None:
    """Re-pin to a supported_versions entry from the ERROR DETAILS and retry.

    The re-pin value comes straight from the VERSION_UNSUPPORTED details —
    no capabilities discovery round-trip in between — and the retry must
    succeed on the same transport.
    """
    repin = _supported_versions(ctx)[0]
    env = ctx["env"]
    result = env.call_via(ctx["transport"], adcp_version=repin)
    assert result.is_error is False, f"Retry pinned to supported version {repin!r} failed: {result.error}"
    ctx["retry_response"] = result.payload


# ── Given ────────────────────────────────────────────────────────────


@given(parsers.parse('the seller speaks adcp release-precision versions "{first}", "{second}"'))
def given_seller_release_versions(ctx: dict, first: str, second: str) -> None:
    """Pin the seller's supported release set for this scenario.

    ``CapabilitiesEnv`` realizes this intent on the actual transport surface:
    managed in-process patches for MCP/A2A/REST, or an atomic leased snapshot
    on the separate live server for E2E REST.
    """
    versions = (first, second)
    ctx["env"].configure_version_policy(ctx["transport"], supported_versions=versions)
    ctx["seller_versions"] = versions


@given(parsers.parse('the seller\'s build_version is "{build_version}"'))
def given_seller_build_version(ctx: dict, build_version: str) -> None:
    """Pin the seller's advisory build_version for this scenario."""
    ctx["env"].configure_version_policy(ctx["transport"], build_version=build_version)
    ctx["seller_build_version"] = build_version


@given(parsers.parse("a VERSION_UNSUPPORTED error is produced with details at {boundary_point}"))
def given_version_unsupported_at_boundary(ctx: dict, boundary_point: str) -> None:
    """Produce a REAL VERSION_UNSUPPORTED error through production and stage the boundary case.

    The error is produced through the production path (a cross-major pin
    dispatched over the scenario's transport). The boundary rows are then
    graded against production's ACTUAL wire details in the When step — not
    against a mutated copy validated by the SDK's pydantic model, which would
    only grade the SDK's constraint rather than our emission.
    """
    if boundary_point not in (
        "supported_versions empty array",
        "supported_versions omitted",
        "build_version used as negotiation input",
    ):
        raise AssertionError(f"Unknown boundary point: {boundary_point!r}")
    dispatch_request(ctx, adcp_version="4.0")
    ctx["boundary_point"] = boundary_point


# ── When ─────────────────────────────────────────────────────────────


@when(parsers.parse('the Buyer Agent calls get_adcp_capabilities MCP tool with adcp_version "{version}"'))
def when_call_capabilities_with_version_pin(ctx: dict, version: str) -> None:
    ctx["pinned_version"] = version
    dispatch_request(ctx, adcp_version=version)


@when(parsers.parse("the Buyer Agent calls get_adcp_capabilities MCP tool with adcp_major_version {major:d}"))
def when_call_capabilities_with_major_pin(ctx: dict, major: int) -> None:
    ctx["pinned_major"] = major
    dispatch_request(ctx, adcp_major_version=major)


@given("the tenant has full capabilities configured")
def given_tenant_full_capabilities(ctx: dict) -> None:
    """The resolvable tenant exposes its full capabilities surface.

    CapabilitiesEnv.setup_default_data() already seeds a tenant with an adapter,
    so the capabilities response is fully populated. The @context scenarios only
    grade context echo (opaque, capability-independent), so this records the intent
    without further configuration.
    """
    ctx["tenant_full_capabilities"] = True


def _dispatch_context(ctx: dict, ctx_json: str, default_transport: object) -> None:
    """Dispatch get_adcp_capabilities with a context object.

    Untagged @context scenarios (absent/nested/empty) are parametrized across the
    wire transports, so ``ctx['transport']`` is already set and wins. The
    transport-tagged scenarios (@mcp / @a2a) are NOT parametrized (pytest_generate_tests
    skips them), so this fixes the transport from the step phrasing.
    """
    ctx.setdefault("transport", default_transport)
    context = json.loads(ctx_json)
    ctx["sent_context"] = context
    dispatch_request(ctx, context=context)


@when(parsers.re(r"the Buyer Agent calls get_adcp_capabilities MCP tool with context (?P<ctx_json>\{.*\})$"))
def when_call_capabilities_with_context_mcp(ctx: dict, ctx_json: str) -> None:
    from tests.harness.transport import Transport

    _dispatch_context(ctx, ctx_json, Transport.MCP)


@when(
    parsers.re(
        r"the Buyer Agent sends a get_adcp_capabilities skill request via A2A with context (?P<ctx_json>\{.*\})$"
    )
)
def when_call_capabilities_with_context_a2a(ctx: dict, ctx_json: str) -> None:
    from tests.harness.transport import Transport

    _dispatch_context(ctx, ctx_json, Transport.A2A)


@when("the Buyer Agent calls get_adcp_capabilities MCP tool without context")
def when_call_capabilities_without_context(ctx: dict) -> None:
    """Dispatch get_adcp_capabilities with NO context (absence must be echoed as absence)."""
    # Record the omission so the paired Then step grades "absence echoed as absence",
    # and forward no context kwarg — the request carries none.
    ctx["sent_context"] = None
    dispatch_request(ctx)


@when("the Buyer Agent inspects the error details")
def when_inspect_error_details(ctx: dict) -> None:
    """Grade the boundary property against PRODUCTION's real wire details.

    Each row asserts a property of what production actually emitted (read off
    ``ctx["wire_error_envelope"]``), not of a mutated copy fed to the SDK model:

    - ``supported_versions empty array`` / ``supported_versions omitted``:
      production cannot emit an empty or missing ``supported_versions`` given a
      configured supported set (it is REQUIRED with minItems 1). The verdict is
      "invalid" precisely because production's wire array is present and
      non-empty — a regression that dropped or emptied it would flip the verdict
      to "valid" and fail the scenario.
    - ``build_version used as negotiation input``: graded on negotiation-
      usability of production's build_version — a value the buyer could legally
      re-pin (a release-precision wire value or a supported_versions member)
      would be "valid" as negotiation input, and the schema says it MUST NOT be.
    """
    details = _wire_error_details(ctx)
    boundary_point = ctx["boundary_point"]

    if boundary_point == "supported_versions empty array":
        # "invalid" == production's wire supported_versions is NOT empty.
        emitted_empty = details.get("supported_versions") == []
        ctx["details_verdict"] = "valid" if emitted_empty else "invalid"
    elif boundary_point == "supported_versions omitted":
        # "invalid" == production's wire details DID carry supported_versions.
        emitted_omitted = "supported_versions" not in details
        ctx["details_verdict"] = "valid" if emitted_omitted else "invalid"
    else:  # build_version used as negotiation input
        build_version = details["build_version"]
        usable_as_pin = bool(re.fullmatch(_RELEASE_PIN_PATTERN, build_version)) or (
            build_version in details["supported_versions"]
        )
        ctx["details_verdict"] = "valid" if usable_as_pin else "invalid"


# ── Then: error identity ─────────────────────────────────────────────


@then("the response should be a VERSION_UNSUPPORTED error")
def then_response_is_version_unsupported(ctx: dict) -> None:
    """Assert the REAL wire envelope carries VERSION_UNSUPPORTED (correctable).

    Recovery is ``correctable`` per the spec's ``enums/error-code.json``
    enumMetadata: the buyer re-pins to a ``supported_versions`` entry and
    retries.
    """
    from tests.helpers.envelope_assertions import assert_envelope_shape

    assert ctx.get("response") is None, f"Expected an error, got a success response: {ctx.get('response')!r}"
    assert_envelope_shape(ctx["wire_error_envelope"], "VERSION_UNSUPPORTED", recovery="correctable")


# ── Then: details payload ────────────────────────────────────────────


@then("the error details should include supported_versions as a non-empty array")
def then_details_supported_versions_nonempty(ctx: dict) -> None:
    """supported_versions is REQUIRED with minItems 1 (version-unsupported.json).

    Asserted element-wise: the wire array must be exactly the seller's
    configured release set from the Given step (which is non-empty).
    """
    versions = _supported_versions(ctx)
    expected = list(ctx["seller_versions"])
    assert versions == expected, f"Wire supported_versions {versions} != seller's configured releases {expected}"


@then(parsers.parse('each supported_versions entry should match pattern "{pattern}"'))
def then_supported_versions_entries_match_pattern(ctx: dict, pattern: str) -> None:
    # The feature file escapes backslashes for Gherkin readability; undo it.
    regex = re.compile(pattern.replace("\\\\", "\\"))
    versions = _supported_versions(ctx)
    non_matching = [v for v in versions if not regex.fullmatch(v)]
    assert non_matching == [], f"supported_versions entries not matching {regex.pattern!r}: {non_matching}"


@then(parsers.parse('the error details should include supported_versions containing "{first}" and "{second}"'))
def then_supported_versions_contains(ctx: dict, first: str, second: str) -> None:
    versions = _supported_versions(ctx)
    missing = [v for v in (first, second) if v not in versions]
    assert missing == [], f"supported_versions {versions} missing {missing}"


@then("the error details may include supported_majors as a deprecated array of integers")
def then_details_supported_majors_integers(ctx: dict) -> None:
    """This seller emits the deprecated field through 3.x (servers SHOULD)."""
    majors = _wire_error_details(ctx)["supported_majors"]
    expected = sorted({int(v.split(".", 1)[0]) for v in _supported_versions(ctx)})
    assert majors == expected, f"supported_majors {majors} must mirror supported_versions majors {expected}"


@then(parsers.parse("the error details may include supported_majors containing {major:d}"))
def then_supported_majors_contains(ctx: dict, major: int) -> None:
    majors = _wire_error_details(ctx)["supported_majors"]
    assert major in majors, f"Expected major {major} in supported_majors {majors}"


@then("the error details may include build_version as an advisory semver string")
def then_details_build_version_semver(ctx: dict) -> None:
    build_version = _wire_error_details(ctx)["build_version"]
    semver = r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?(\+[a-zA-Z0-9.-]+)?$"
    assert re.fullmatch(semver, build_version), f"build_version {build_version!r} is not full semver"


@then(parsers.parse('the error details should include build_version equal to "{build_version}"'))
def then_details_build_version_equals(ctx: dict, build_version: str) -> None:
    actual = _wire_error_details(ctx)["build_version"]
    assert actual == build_version, f"Expected build_version {build_version!r}, got {actual!r}"


# ── Then: suggestion ─────────────────────────────────────────────────


@then(
    parsers.re(
        r'the error should include "suggestion" field advising '
        r"(?:the Buyer to re-pin to|re-pin to|the Buyer to select) a supported_versions entry"
    )
)
def then_suggestion_advises_supported_versions_repin(ctx: dict) -> None:
    suggestion = _wire_error(ctx).get("suggestion")
    assert suggestion is not None, "Expected a suggestion on the wire error"
    assert "supported_versions" in suggestion, f"Suggestion must point at supported_versions: {suggestion!r}"


# ── Then: buyer re-pin behavior ──────────────────────────────────────


@then("the Buyer Agent may re-pin to a value from supported_versions and retry without a second discovery round-trip")
def then_buyer_can_repin_and_retry(ctx: dict) -> None:
    _repin_and_retry(ctx)


@then("the Buyer Agent must select the next adcp_version from supported_versions")
def then_buyer_selects_from_supported_versions(ctx: dict) -> None:
    _repin_and_retry(ctx)


@then("the Buyer Agent must not use build_version to choose a retry version")
def then_build_version_not_a_retry_candidate(ctx: dict) -> None:
    """build_version is advisory triage only (BR-19): it is not a legal re-pin.

    Grades the seller's side of the contract: the advertised build_version is
    neither a supported_versions member nor a valid release-precision wire pin,
    so a buyer following supported_versions can never pick it.
    """
    details = _wire_error_details(ctx)
    build_version = details["build_version"]
    assert build_version not in details["supported_versions"], (
        f"build_version {build_version!r} must not appear in supported_versions"
    )
    assert re.fullmatch(_RELEASE_PIN_PATTERN, build_version) is None, (
        f"build_version {build_version!r} must not be a valid release-precision negotiation pin"
    )


# ── Then: details boundary verdict ───────────────────────────────────


@then(parsers.parse("the VERSION_UNSUPPORTED error details should be {expected}"))
def then_details_verdict(ctx: dict, expected: str) -> None:
    assert ctx["details_verdict"] == expected, (
        f"Details at {ctx['boundary_point']!r} graded {ctx['details_verdict']!r}, expected {expected!r}"
    )


# ── Then: context echo (BR-RULE-043 / POST-S9) ───────────────────────


@then(parsers.re(r"the response context should equal (?P<ctx_json>\{.*\})$"))
def then_response_context_equals(ctx: dict, ctx_json: str) -> None:
    """The echoed context must equal the request context VERBATIM (opaque round-trip).

    Graded on the real wire body so a serialization drop/mutation fails the scenario.
    """
    assert ctx.get("response") is not None, f"Expected a success response, got error: {ctx.get('error')!r}"
    expected = json.loads(ctx_json)
    actual = _response_context(ctx)
    assert actual == expected, f"Echoed context {actual!r} != request context {expected!r}"


@then("the response should not contain a context field")
def then_response_no_context(ctx: dict) -> None:
    """Context absence in the request must be echoed as true absence (INV-2).

    The field must be OMITTED, not present-as-null: ``{"context": null}`` on the
    wire is a *present* field and violates 'should not contain a context field'.
    """
    assert ctx.get("response") is not None, f"Expected a success response, got error: {ctx.get('error')!r}"
    actual = _response_context(ctx)
    assert actual is _CONTEXT_ABSENT, f"Expected the context field to be absent, got {actual!r}"
