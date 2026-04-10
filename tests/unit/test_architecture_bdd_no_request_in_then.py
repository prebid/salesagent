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
The violation is *dispatching*, not *reading*.
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
    # FIXME(GH-TBD): Split into When (send with bad creds) + Then (assert auth error)
    "bdd/steps/domain/uc002_nfr.py:45 then_auth_before_business_logic",
    # FIXME(GH-TBD): Split into When (rapid follow-up) + Then (assert rate limit)
    "bdd/steps/domain/uc002_nfr.py:104 then_rate_limiting_enforced",
    # FIXME(GH-TBD): Split into When (oversized payload) + Then (assert rejection)
    "bdd/steps/domain/uc002_nfr.py:146 then_payload_size_limits",
    # FIXME(GH-TBD): Split into When (timed call) + Then (assert SLA)
    "bdd/steps/domain/uc002_nfr.py:239 then_response_within_sla",
    # FIXME(GH-TBD): Split into When (below-min budget) + Then (assert rejection)
    "bdd/steps/domain/uc002_nfr.py:283 then_budget_validated_against_min_order",
    # FIXME(GH-TBD): Split into When (re-dispatch with decoy) + Then (assert isolation)
    "bdd/steps/domain/uc011_accounts.py:490 then_accounts_are_agent_scoped",
    # FIXME(GH-TBD): Split into When (unfiltered request) + Then (assert same set)
    "bdd/steps/domain/uc011_accounts.py:735 then_result_set_identical",
}


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
