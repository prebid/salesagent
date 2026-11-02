"""Domain step definitions for BR-SECURITY-001 (#1587 / salesagent-prkv.18).

Formalizes, as a Gherkin scenario graded across a2a/mcp/rest, the
obligation that an untyped exception raised inside a dispatched skill's
business logic never puts its own text on the buyer-facing wire.
AdCP 3.1.1 v3.1.1:docs/building/operating/transport-errors.mdx,
Security Considerations MUST-NOT list (L659-L670).

Given: "a tenant is configured for product discovery" is reused from
    tests/bdd/steps/domain/uc_get_products_inventory.py (already registered
    as a pytest-bdd step by that module) — not redefined here.
When: dispatch_request(ctx, ...) — the ONE sanctioned writer of ctx["result"].
Then: TWO assertions, both required (neither alone closes the gap) —
    positively pins the observation to THIS injected fault via the server-side
    boundary log (record_boundary_error attaches the original exception with
    exc_info, so the pin is exception-TYPE IDENTITY, not a substring), and
    negatively asserts the fault's own (plainly-internal-looking) marker text is
    absent from the FULL wire envelope via assert_no_marker_in_envelope.

    The positive pin CANNOT live on the wire any more, and that is the point of
    this epic: the buyer-facing message is now a function of the error CODE via
    CODE_TABLE, so it can no longer carry type(exc).__name__ — precisely the
    leak-adjacent behaviour this scenario grades. The observability channel is
    where the fault's identity legitimately survives.
    Not graded on e2e_rest: inject_untyped_exception is e2e_unsupported
    (tests/harness/_base.py), and an out-of-process server's logs are not
    visible to caplog.
"""

from __future__ import annotations

import logging

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_error_dict
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.helpers import assert_no_marker_in_envelope

# A distinctive, plainly-internal-looking payload (a fake DSN) — if this
# string appears anywhere in the wire envelope, the raw exception leaked.
# Mirrors tests/integration/test_prkv8_untyped_exception_wire_leak.py's
# _SECRET_MARKER technique.
_FAKE_DSN_MARKER = "postgres://admin:s3cr3t-security001@10.0.0.9:5432/prod_shadow"


class _InjectedUntypedFault(RuntimeError):
    """Distinctively-named untyped exception injected by this scenario.

    Not a bare RuntimeError: production's adcp_error_for() fallback
    emits ``type(exc).__name__`` as the wire-safe message, so this class name
    becomes the positive assertion target — pinning the observation to THIS
    injected fault specifically (any unrelated unhandled exception would not
    match the class-name substring), which is what kills the vacuity risk
    flagged in architect review.
    """


# ── Given steps ─────────────────────────────────────────────────────


@given("an untyped exception is raised inside the dispatched skill's business logic")
def given_untyped_exception(ctx: dict, caplog) -> None:
    """Force the dispatched skill's business logic to raise an untyped fault.

    Delegates to the shared harness capability (BaseTestEnv.inject_untyped_exception)
    rather than a per-scenario mock.patch, so the same Given works unmodified
    across a2a/mcp/rest (and e2e-REST, where it declares itself unsupported).

    Also branches log capture HERE rather than in the Then, because the boundary log is
    emitted during the When: by the time the Then runs the record must already have
    been captured. ``propagate=False`` is production-gated (src/core/logging_config.py),
    so the record reaches caplog's handler under test.
    """
    caplog.set_level(logging.ERROR)
    ctx["security001_caplog"] = caplog

    env = ctx["env"]
    env.inject_untyped_exception(_InjectedUntypedFault(_FAKE_DSN_MARKER))


# ── When steps ──────────────────────────────────────────────────────


@when("the Buyer Agent requests products")
def when_buyer_agent_requests_products(ctx: dict) -> None:
    """Dispatch get_products through the current transport."""
    dispatch_request(ctx, brief="video ads")


# ── Then steps ──────────────────────────────────────────────────────


@then(parsers.parse('the response is an error with code "{code}" and no raw exception text'))
def then_safe_wire_envelope(ctx: dict, code: str) -> None:
    """Assert the wire envelope is buyer-safe: the right code AND no leaked text.

    Positive: pins the observation to THIS fault by exception-type identity on the
    server-side boundary log (src/core/tool_error_logging.py record_boundary_error
    logs the untyped branch at ERROR with exc_info on the ORIGINAL exception).
    Identity beats the substring it replaces: an unrelated transient error cannot
    satisfy it even by coincidence of wording.
    Negative: scans the FULL envelope (not just errors[0].message) for the
    fault's own internal-looking marker text — a leak anywhere in the
    envelope (adcp_error.message, errors[0].details, suggestion, context)
    fails this check.
    """
    result = ctx["result"]
    result.assert_wire_error(code, recovery="transient")
    # The envelope goes through the guarded accessor (wire_error_dict), never a direct
    # result.wire_error_envelope read: only the accessor knows whether the bytes are a
    # real wire capture. The LOUD accessor rather than the tolerant one because
    # assert_wire_error above has already established an envelope exists, so a None here
    # is a harness fault that must raise, not a scan over nothing.
    assert_no_marker_in_envelope(wire_error_dict(ctx), _FAKE_DSN_MARKER)

    records = ctx["security001_caplog"].records
    assert any(
        rec.levelno >= logging.ERROR and rec.exc_info and rec.exc_info[0] is _InjectedUntypedFault for rec in records
    ), (
        "the injected fault was not recorded at the transport boundary; "
        f"ERROR records seen: {[(r.name, r.getMessage()[:80]) for r in records if r.levelno >= logging.ERROR]!r}"
    )
