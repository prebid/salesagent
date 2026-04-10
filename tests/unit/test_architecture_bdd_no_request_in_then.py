"""Guard: BDD Then steps must not dispatch requests (When-inside-Then).

Then steps assert on OUTCOMES (response, DB state). They must never send
a new request — that's a When action. When a Then step calls
``env.call_impl()``, ``env.call_via()``, ``dispatch_request()``, or
constructs an ``httpx.Client``, it is performing an action, not verifying
an outcome.

**Wrong** (Then sends request to test enforcement)::

    @then("auth should fire before business logic")
    def then_auth_first(ctx):
        req = CreateMediaBuyRequest(**ctx["request_kwargs"])
        env.call_impl(req=req, identity=bad_identity)  # ← When action!
        assert auth_error ...

**Right** (separate When step for the second request)::

    @when("the Buyer sends a request with invalid credentials")
    def when_bad_creds(ctx):
        env.call_impl(req=req, identity=bad_identity)

    @then("the system should reject with authentication error")
    def then_auth_error(ctx):
        assert ctx["error"] is AdCPAuthenticationError

Reading ``request_kwargs`` to derive expectations is fine — Then steps
need to know what was requested to verify the response is correct.

**Pattern 2 — Asserting on request data**:

A Then step that reads ``request_kwargs`` and asserts the request-derived
value against a constant (``assert expected_count > 0``) is testing the
Given step setup, not the system. These precondition checks belong in
the Given step that created the data, not the Then step.

**Wrong** (asserting request had creative IDs)::

    expected_count = sum(len(pkg["creative_ids"]) for pkg in request_kwargs["packages"])
    assert expected_count > 0  # ← testing Given, not production

**Right** (assert in Given, or derive from ctx contract)::

    # Given step:
    ctx["expected_creative_ids"] = {"c1", "c2"}
    assert ctx["expected_creative_ids"]  # precondition HERE

    # Then step:
    assert db_count == len(ctx["expected_creative_ids"])
"""

from __future__ import annotations

import ast

from tests.unit._bdd_guard_helpers import iter_then_functions

# ── Dispatch methods that indicate a When action ────────────────────────
_DISPATCH_METHODS = {
    "call_impl",
    "call_via",
    "call_a2a",
    "call_mcp",
}

# ── Allowlist (ratcheting — may only shrink) ─────────────────────────────

_DISPATCH_IN_THEN_ALLOWLIST: set[str] = {
    # Legacy dispatch-in-Then steps — original scenarios are xfailed.
    # Auth and budget enforcement replaced by BR-UC-002-nfr-enforcement.feature.
    # Rate limiting, payload size, SLA test unimplemented features (dead code).
    "bdd/steps/domain/uc002_nfr.py:127 then_auth_before_business_logic",
    "bdd/steps/domain/uc002_nfr.py:186 then_rate_limiting_enforced",
    "bdd/steps/domain/uc002_nfr.py:228 then_payload_size_limits",
    "bdd/steps/domain/uc002_nfr.py:321 then_response_within_sla",
    "bdd/steps/domain/uc002_nfr.py:365 then_budget_validated_against_min_order",
    # FIXME(GH-TBD): Split into When (re-dispatch with decoy) + Then (assert isolation)
    "bdd/steps/domain/uc011_accounts.py:490 then_accounts_are_agent_scoped",
    # FIXME(GH-TBD): Split into When (unfiltered request) + Then (assert same set)
    "bdd/steps/domain/uc011_accounts.py:735 then_result_set_identical",
}

_ASSERT_ON_REQUEST_ALLOWLIST: set[str] = set()


def _find_request_kwargs_vars(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Find variables assigned from expressions involving request_kwargs."""
    names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        if "request_kwargs" not in ast.dump(node.value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _find_assert_on_request(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Detect assertions on request-derived variables compared to constants.

    Catches Then steps that assert a request-derived value is truthy or
    passes a threshold — these are precondition checks (testing Given setup),
    not outcome assertions (testing production behavior).

    Patterns caught:
    - ``assert request_derived_var`` (bare truthiness)
    - ``assert request_derived_var > 0`` (comparison to constant)
    - ``assert len(request_derived_var) > 0`` (count comparison to constant)
    """
    rk_vars = _find_request_kwargs_vars(func)
    if not rk_vars:
        return False
    for node in ast.walk(func):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        # assert rk_var  (bare truthiness)
        if isinstance(test, ast.Name) and test.id in rk_vars:
            return True
        if not isinstance(test, ast.Compare):
            continue
        # assert rk_var > 0  (comparison to constant)
        left = test.left
        if isinstance(left, ast.Name) and left.id in rk_vars:
            if all(isinstance(c, ast.Constant) for c in test.comparators):
                return True
        # assert len(rk_var) > 0
        if (
            isinstance(left, ast.Call)
            and isinstance(left.func, ast.Name)
            and left.func.id == "len"
            and left.args
            and isinstance(left.args[0], ast.Name)
            and left.args[0].id in rk_vars
        ):
            if all(isinstance(c, ast.Constant) for c in test.comparators):
                return True
    return False


def _find_dispatch_call(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Detect dispatch/call_impl/call_via calls in a Then function.

    Catches method calls like ``env.call_impl(...)``, ``env.call_via(...)``,
    ``dispatch_request(...)``, and ``client.post(...)`` — all of which
    perform actions rather than verify outcomes.
    """
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        # method call: obj.method(...)
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in _DISPATCH_METHODS:
                return True
            # httpx.Client().post() or client.post()
            if node.func.attr == "post" and isinstance(node.func.value, ast.Name):
                return True
        # bare function call: dispatch_request(...)
        if isinstance(node.func, ast.Name) and node.func.id == "dispatch_request":
            return True
    return False


class TestBddNoDispatchInThen:
    """Structural guard: Then steps must not dispatch requests.

    Then steps verify outcomes. Dispatching a request is a When action.
    If a Then step needs to test a negative path (auth failure, rate limit),
    the scenario should have a separate When step for the second request.
    """

    def test_no_new_dispatch_in_then(self) -> None:
        """No Then step dispatches a request outside the allowlist."""
        new_violations: list[str] = []
        seen_in_allowlist: set[str] = set()

        for key, func in iter_then_functions():
            if _find_dispatch_call(func):
                if key in _DISPATCH_IN_THEN_ALLOWLIST:
                    seen_in_allowlist.add(key)
                else:
                    new_violations.append(key)

        errors: list[str] = []
        if new_violations:
            errors.append(
                f"Found {len(new_violations)} Then step(s) that dispatch requests:\n"
                + "\n".join(f"  {v}" for v in sorted(new_violations))
                + "\n\nThen steps must not send requests — that's a When action. "
                "Split into a When step (sends request) and a Then step (asserts outcome)."
            )

        stale = sorted(_DISPATCH_IN_THEN_ALLOWLIST - seen_in_allowlist)
        if stale:
            errors.append(
                "Stale allowlist entries (violations fixed — remove from "
                "_DISPATCH_IN_THEN_ALLOWLIST):\n"
                + "\n".join(f"  {s}" for s in stale)
            )

        assert not errors, "\n\n".join(errors)

    def test_no_new_assert_on_request(self) -> None:
        """No Then step asserts on request-derived values against constants.

        Assertions like ``assert expected_count > 0`` where expected_count
        comes from request_kwargs are precondition checks — they test the
        Given step setup, not the system. Move them to the Given step.
        """
        new_violations: list[str] = []
        seen_in_allowlist: set[str] = set()

        for key, func in iter_then_functions():
            if _find_assert_on_request(func):
                if key in _ASSERT_ON_REQUEST_ALLOWLIST:
                    seen_in_allowlist.add(key)
                else:
                    new_violations.append(key)

        errors: list[str] = []
        if new_violations:
            errors.append(
                f"Found {len(new_violations)} Then step(s) asserting on request data:\n"
                + "\n".join(f"  {v}" for v in sorted(new_violations))
                + "\n\nThen steps must assert on outcomes (response, DB), not on "
                "request-derived values. Move precondition checks to Given steps."
            )

        stale = sorted(_ASSERT_ON_REQUEST_ALLOWLIST - seen_in_allowlist)
        if stale:
            errors.append(
                "Stale allowlist entries (violations fixed — remove from "
                "_ASSERT_ON_REQUEST_ALLOWLIST):\n"
                + "\n".join(f"  {s}" for s in stale)
            )

        assert not errors, "\n\n".join(errors)
