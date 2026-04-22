"""Admin-UI authentication primitives.

``Principal`` (see ``principal.py``) is the canonical detached POJO
stashed by ``UnifiedAuthMiddleware`` on ``request.state.principal``.
Per canonical spec §11.3.1 (B15 mitigation).

``AdminUser`` (see ``deps/auth.py``) is the admin-UI handler identity
per canonical spec §11.4. ``Principal`` and ``AdminUser`` are distinct
by layer (middleware POJO with tenant context + available_tenants vs
handler dep POJO with narrower email/role/is_test_user surface); the
DRY cleanup at PR #1221 intentionally did NOT merge them.

Shared primitives below: the ``Role`` literal + ``normalize_email`` +
``coerce_role`` were declared in 2 files (principal.py, deps/auth.py)
and scattered in 4+ places (email lowercasing) / 2 places (role whitelist)
before this module became their single source of truth. CLAUDE.md DRY
policy treats three copies as a defect.
"""

from __future__ import annotations

from typing import Any, Final, Literal, cast

Role = Literal["super_admin", "tenant_admin", "tenant_user", "test"]

# The closed set of valid admin-UI roles. Extending this set requires a
# coordinated update across ``Principal``, ``AdminUser``, the session
# middleware (``unified_auth.py``), and any handler that role-gates.
_VALID_ROLES: Final[frozenset[Role]] = frozenset(("super_admin", "tenant_admin", "tenant_user", "test"))


def normalize_email(raw: str | None) -> str:
    """Return an email in canonical form: stripped + lowercased.

    Empty/missing input yields ``""``. Matches Flask's historical behavior
    across the 40+ ad-hoc ``.strip().lower()`` call sites this helper
    replaces. Preserves non-ASCII characters in the local part (``lower()``
    handles unicode case folding per Python string semantics).
    """
    return (raw or "").strip().lower()


def coerce_role(raw: Any) -> Role | None:
    """Parse a session-stored role value into the ``Role`` literal, else None.

    Returns ``None`` for unknown / non-string / empty input — callers MUST
    choose their own default explicitly (usually ``or "tenant_user"``).
    Rationale: a default embedded in the helper would silently promote
    malformed session data to an authenticated role. By returning ``None``,
    the default choice is visible at the callsite and is auditable.

    Note: does NOT strip or lowercase — the session layer stores the exact
    Literal value (post-login normalization happens at session-write time,
    not on every read).
    """
    if isinstance(raw, str) and raw in _VALID_ROLES:
        return cast(Role, raw)
    return None


__all__ = ["Role", "coerce_role", "normalize_email"]
