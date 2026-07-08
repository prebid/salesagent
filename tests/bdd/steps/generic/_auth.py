"""Shared authentication helpers for BDD step definitions.

Plain helper module (no ``@given``/``@when``/``@then`` decorators) — importing it
never registers a step, mirroring ``_account_resolution.py``. Home for the
principal-switch mutation shared by every "authenticated as principal" Given/When
across use cases (UC-003 error + update, UC-018 isolation).
"""

from __future__ import annotations

from typing import Any


def authenticate_env_as(ctx: dict, principal_id: str) -> Any:
    """Switch the harness env's authenticated principal to *principal_id*; return the env.

    Clears the identity cache so the next ``env.identity`` access re-resolves identity
    from scratch — picking up a principal row committed after the env was created — then
    points the env at *principal_id*. Callers layer their own ctx bookkeeping
    (``principal_id`` / ``principal_override``) and post-conditions on top.
    """
    env = ctx["env"]
    env._identity_cache.clear()
    env._principal_id = principal_id
    return env
