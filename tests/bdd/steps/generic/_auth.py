"""Shared authentication helper for BDD step definitions.

Plain helper module (no ``@given``/``@when``/``@then`` decorators) — importing it
never registers a step, mirroring ``_account_resolution.py``. Home for the
principal-switch shared by every "authenticated as principal" Given/When across
use cases (UC-018 isolation; UC-003 error + update).

**Live coverage.** Only UC-018 (``test_uc018_list_creatives``) exercises this
helper at runtime today. The UC-003 callers (``uc003_update_media_buy`` and
``uc003_ext_error_scenarios``) are currently **dormant**: neither step module is
registered in ``tests/bdd/conftest.py`` ``pytest_plugins`` and UC-003 is not wired
into the BDD harness, so every UC-003 update scenario auto-xfails (verified: 0
passed / 1404 xfailed). Those sites are converted to this shared helper anyway so
they are correct-by-construction when UC-003 is later activated — but the DRY edit
there is uncovered until then. Wiring UC-003 into the harness (registering the step
modules + mapping ``MediaBuyUpdateEnv`` in ``_detect_uc`` / ``_harness_env``) remains
a follow-up.
"""

from __future__ import annotations

from typing import Any


def authenticate_env_as(ctx: dict, principal_id: str) -> Any:
    """Switch the harness env to *principal_id*, record it canonically; return the env.

    Owns the full principal-switch contract so callers don't re-implement it:

    - re-points the env via the public ``env.switch_principal`` (clears the identity
      cache so the next ``env.identity`` re-resolves — picking up a principal row
      committed after the env was created);
    - records the canonical ``ctx["principal_id"]`` (the key read downstream by
      uc004/uc006 — there is no second key for this concept);
    - asserts the identity mutation took effect.

    Callers add only genuinely use-case-specific ctx state (e.g. uc003's ``has_auth``).
    """
    env = ctx["env"]
    env.switch_principal(principal_id)
    ctx["principal_id"] = principal_id
    actual = env.identity.principal_id
    assert actual == principal_id, (
        f"env.identity.principal_id is {actual!r} after switching to {principal_id!r} — cache not rebuilt"
    )
    return env
