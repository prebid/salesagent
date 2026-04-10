"""Guard: BDD Then steps must not read request_kwargs from ctx.

Then steps assert on OUTCOMES (response, DB state). They must not reach back
into ``ctx["request_kwargs"]`` to derive expected values — that couples Then
to When and breaks in E2E mode where the request is serialized differently.

**Wrong**: Then step parses ``ctx["request_kwargs"]["packages"]`` to count
how many packages to expect in the response.

**Right**: Given step publishes ``ctx["expected_package_count"]``. Then step
reads that named contract key.

The Given→Then contract uses explicit ctx keys:
- Given sets up data AND registers expectations (``ctx["expected_*"]``)
- When executes the operation
- Then reads expectations from ctx and verifies against outcomes

NFR Then steps that SEND a second request (rate limiting, auth-before-logic,
payload size, SLA timing) are also flagged — they contain When-like actions
that should be restructured into separate When steps.
"""

from __future__ import annotations

import ast

from tests.unit._bdd_guard_helpers import iter_then_functions

# ── Allowlist (ratcheting — may only shrink) ─────────────────────────────
# Pre-existing violations. Each must have a FIXME comment at the source.

_REQUEST_KWARGS_IN_THEN_ALLOWLIST: set[str] = {
    # FIXME(GH-TBD): Given should publish ctx["expected_total_budget"]
    "bdd/steps/domain/uc002_create_media_buy.py:2438 then_budget_distributed_per_allocations",
    # FIXME(GH-TBD): Given should publish ctx["expected_product_ids"]
    "bdd/steps/domain/uc002_create_media_buy.py:2498 then_response_has_derived_packages",
    # FIXME(GH-TBD): Given should publish ctx["expected_package_fields"]
    "bdd/steps/domain/uc026_package_media_buy.py:1949 then_package_all_fields",
    # FIXME(GH-TBD): Given should publish ctx["expected_package_count"]
    "bdd/steps/generic/then_media_buy.py:76 then_response_has_packages",
    # FIXME(GH-TBD): Remove fallback to request_kwargs for push_notification_config
    "bdd/steps/generic/then_media_buy.py:319 then_webhook_notification",
    # FIXME(GH-TBD): Given should publish ctx["expected_buyer_ref"]
    "bdd/steps/generic/then_media_buy.py:438 then_media_buy_persisted",
    # FIXME(GH-TBD): Given should publish ctx["expected_package_count"]
    "bdd/steps/generic/then_media_buy.py:492 then_package_records_persisted",
    # FIXME(GH-TBD): Given should publish ctx["expected_creative_ids"]
    "bdd/steps/generic/then_media_buy.py:573 then_creative_assignment_records_persisted",
    # FIXME(GH-TBD): Then sends second request — restructure into When step
    "bdd/steps/domain/uc002_nfr.py:45 then_auth_before_business_logic",
    # FIXME(GH-TBD): Then sends second request — restructure into When step
    "bdd/steps/domain/uc002_nfr.py:104 then_rate_limiting_enforced",
    # FIXME(GH-TBD): Then sends second request — restructure into When step
    "bdd/steps/domain/uc002_nfr.py:146 then_payload_size_limits",
    # FIXME(GH-TBD): Then sends second request — restructure into When step
    "bdd/steps/domain/uc002_nfr.py:239 then_response_within_sla",
    # FIXME(GH-TBD): Then sends second request — restructure into When step
    "bdd/steps/domain/uc002_nfr.py:283 then_budget_validated_against_min_order",
}


def _find_request_kwargs_access(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Detect ``request_kwargs`` access anywhere in a Then function body.

    Catches:
    - ``ctx.get("request_kwargs")``
    - ``ctx["request_kwargs"]``
    - ``request_kwargs = ctx.get(...)`` or ``ctx[...]``
    - Any string constant ``"request_kwargs"`` in the function (covers all forms)
    """
    for node in ast.walk(func):
        if isinstance(node, ast.Constant) and node.value == "request_kwargs":
            return True
    return False


class TestBddNoRequestKwargsInThen:
    """Structural guard: Then steps must not access request_kwargs.

    Then steps verify outcomes. Expected values come from Named ctx keys
    published by Given steps, not from parsing the raw request.
    """

    def test_no_new_request_kwargs_in_then(self) -> None:
        """No Then step accesses request_kwargs outside the allowlist."""
        new_violations: list[str] = []
        seen_in_allowlist: set[str] = set()

        for key, func in iter_then_functions():
            if _find_request_kwargs_access(func):
                if key in _REQUEST_KWARGS_IN_THEN_ALLOWLIST:
                    seen_in_allowlist.add(key)
                else:
                    new_violations.append(key)

        errors: list[str] = []
        if new_violations:
            errors.append(
                f"Found {len(new_violations)} Then step(s) accessing request_kwargs:\n"
                + "\n".join(f"  {v}" for v in sorted(new_violations))
                + "\n\nThen steps must not parse the request. "
                "Given steps should publish expected values via named ctx keys "
                '(e.g., ctx["expected_package_count"]).'
            )

        stale = sorted(_REQUEST_KWARGS_IN_THEN_ALLOWLIST - seen_in_allowlist)
        if stale:
            errors.append(
                "Stale allowlist entries (violations were fixed — remove from "
                "_REQUEST_KWARGS_IN_THEN_ALLOWLIST):\n"
                + "\n".join(f"  {s}" for s in stale)
            )

        assert not errors, "\n\n".join(errors)
