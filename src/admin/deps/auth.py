"""L0-12 STUB (Red commit) — real implementation lands in Green commit.

Canonical spec: flask-to-fastapi-foundation-modules.md §11.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends
from starlette.requests import Request

Role = Literal["super_admin", "tenant_admin", "tenant_user", "test"]


@dataclass(frozen=True)
class AdminUser:
    email: str
    role: Role
    is_test_user: bool = False


class AdminRedirect(Exception):
    def __init__(self, to: str, next_url: str = ""):
        super().__init__(f"redirect to {to}")
        self.to = to
        self.next_url = next_url


class AdminAccessDenied(Exception):
    def __init__(self, message: str = "Access denied"):
        super().__init__(message)
        self.message = message


def _extract_email(raw: Any) -> str:
    """STUB — always returns empty string."""
    return ""


def is_super_admin(email: str) -> bool:  # pragma: no cover
    """STUB — always False."""
    return False


def _get_admin_user_or_none(request: Request) -> AdminUser | None:
    """STUB — always None, simulating unauthenticated state."""
    return None


def get_admin_user_optional(request: Request) -> AdminUser | None:
    return _get_admin_user_or_none(request)


def get_admin_user(request: Request) -> AdminUser:
    """STUB — always raises AdminRedirect (no real user resolution)."""
    raise AdminRedirect(to="/admin/login", next_url=str(request.url))


AdminUserDep = Annotated[AdminUser, Depends(get_admin_user)]
AdminUserOptional = Annotated[AdminUser | None, Depends(get_admin_user_optional)]


def require_super_admin(user: AdminUserDep) -> AdminUser:
    return user  # STUB — no role check


SuperAdminDep = Annotated[AdminUser, Depends(require_super_admin)]

# Aliases used by the L0 work-item spec.
CurrentUserDep = AdminUserDep
RequireAdminDep = AdminUserDep
RequireSuperAdminDep = SuperAdminDep
