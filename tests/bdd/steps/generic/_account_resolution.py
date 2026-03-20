"""Shared account resolution helpers for BDD step definitions.

Extracts the common pattern used by UC-002 and UC-006 When steps:
- Handle absent/invalid account references
- Delegate to harness for resolve_account()
- Capture result or error in ctx

beads: salesagent-71q (DRY extraction from UC-002 + UC-006 duplication)
"""

from __future__ import annotations

from typing import Any


def ensure_tenant_principal(ctx: dict, env: object) -> None:
    """Create tenant + principal if not already created by a Given step."""
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal


def validate_account_ref(ctx: dict) -> Any | None:
    """Validate account reference from ctx, setting ctx["error"] on failure.

    Returns the validated AccountReference, or None if validation failed
    (ctx["error"] is set in that case).

    Handles three pre-resolution cases:
    - account_absent: missing account field (INVALID_REQUEST)
    - account_invalid_both: both account_id and brand present (INVALID_REQUEST)
    - account_ref is None: no reference at all
    """
    from src.core.exceptions import AdCPValidationError

    if ctx.get("account_absent"):
        ctx["error"] = AdCPValidationError(
            "Account field is required. Use account_id or brand+operator to identify the account.",
            details={"suggestion": "Include an 'account' field with either account_id or brand+operator."},
        )
        return None

    if ctx.get("account_invalid_both"):
        ctx["error"] = AdCPValidationError(
            "Account field must be either account_id OR brand+operator, not both.",
            details={"suggestion": "Use either account_id or brand+operator, not both."},
        )
        return None

    account_ref = ctx.get("account_ref")
    if account_ref is None:
        ctx["error"] = AdCPValidationError(
            "Account reference is required.",
            details={"suggestion": "Provide an account reference."},
        )
        return None

    return account_ref


def resolve_account_or_error(ctx: dict) -> None:
    """Resolve account reference via harness, capturing result or error in ctx.

    On success: sets ctx["response"] and ctx["resolved_account_id"].
    On failure: sets ctx["error"].
    """
    from src.core.exceptions import AdCPError

    account_ref = validate_account_ref(ctx)
    if account_ref is None:
        return  # ctx["error"] already set

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)

    try:
        result = env.call_impl(account_ref=account_ref)
        ctx["response"] = result
        ctx["resolved_account_id"] = result
    except AdCPError as e:
        ctx["error"] = e
