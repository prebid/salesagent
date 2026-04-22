"""Fully-detached admin identity (B15 mitigation).

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.3.1.

All fields are primitives or frozenset — no ORM, no DB-backed
relationships, no ``Mapped[]`` annotations, no SQLAlchemy Base
inheritance. ``UnifiedAuthMiddleware`` constructs this from an ORM
query at request entry, closes the session immediately, and stashes
the detached POJO on ``request.state.principal``. Downstream
middleware (``LegacyAdminRedirectMiddleware`` at L1c) and handlers
read ``principal.tenant_id`` and ``principal.available_tenants``
without risking ``DetachedInstanceError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.admin.auth import Role, normalize_email


@dataclass(frozen=True, slots=True)
class Principal:
    """Immutable admin-UI identity carried on ``request.state.principal``.

    ``user_email`` is canonicalized (stripped + lowercased) at construction
    time via ``normalize_email`` — the shared helper from ``src.admin.auth``
    that centralizes the 40+ ad-hoc ``.lower()`` calls that otherwise
    accumulated across the Flask blueprints.

    ``is_test_user`` is set only when ``ADCP_AUTH_TEST_MODE=true`` AND
    the session carries a test-user marker — it enables the test-fixture
    bypass path in admin deps.
    """

    user_email: str
    role: Role
    tenant_id: str
    available_tenants: frozenset[str] = field(default_factory=frozenset)
    is_test_user: bool = False

    def __post_init__(self) -> None:
        canonical = normalize_email(self.user_email)
        if canonical != self.user_email:
            object.__setattr__(self, "user_email", canonical)

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"
