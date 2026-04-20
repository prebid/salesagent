"""L0-15 — app_factory empty obligation tests (Pattern b: module-absence Red).

Red state (module missing): ``pytest.raises(ModuleNotFoundError)`` asserts
the module has not yet been authored — the absence IS the obligation at L0
per L0-implementation-plan-v2.md §L0-15.

Green state: ``build_admin_router()`` exists and returns an ``APIRouter``
with:
  - ``prefix="/tenant/{tenant_id}"`` (D1 2026-04-16 canonical URL routing)
  - ``tags=["admin"]``
  - ``include_in_schema=False`` (Invariant #2; adcp-safety.md:141-165)
  - ``redirect_slashes=True`` (Invariant #2)
  - zero routes (empty scaffold at L0)

L1a+ adds real routes; the empty-router assertion keeps shrinking.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter


class TestBuildAdminRouterObligation:
    def test_module_and_factory_exist(self) -> None:
        """At Green, the module AND the ``build_admin_router`` symbol both
        resolve. Red history preserves the ``ModuleNotFoundError`` line below
        — reactivate by deleting ``src/admin/app_factory.py``."""
        try:
            from src.admin.app_factory import build_admin_router  # noqa: F401
        except ModuleNotFoundError:
            pytest.fail(
                "src/admin/app_factory.py MUST exist at L0-15 Green. "
                "If the module is missing, the Red obligation is unmet."
            )

    def test_returns_api_router_instance(self) -> None:
        from src.admin.app_factory import build_admin_router

        router = build_admin_router()
        assert isinstance(router, APIRouter)

    def test_prefix_is_canonical_tenant_mount(self) -> None:
        """D1 2026-04-16: the canonical admin prefix is
        ``/tenant/{tenant_id}``, NOT ``/admin``. Legacy ``/admin/*`` is
        handled by ``LegacyAdminRedirectMiddleware`` at L1c."""
        from src.admin.app_factory import build_admin_router

        router = build_admin_router()
        assert router.prefix == "/tenant/{tenant_id}"

    def test_tags_are_admin(self) -> None:
        from src.admin.app_factory import build_admin_router

        router = build_admin_router()
        assert router.tags == ["admin"]

    def test_include_in_schema_is_false(self) -> None:
        """Invariant #2 + adcp-safety.md:141-165 — admin routes MUST NOT
        appear in the OpenAPI schema (AdCP surface is the only published
        schema)."""
        from src.admin.app_factory import build_admin_router

        router = build_admin_router()
        assert router.include_in_schema is False

    def test_redirect_slashes_is_true(self) -> None:
        """Invariant #2 — ``APIRouter(redirect_slashes=True)`` is required on
        every admin router so trailing-slash variants are accepted."""
        from src.admin.app_factory import build_admin_router

        router = build_admin_router()
        assert router.redirect_slashes is True

    def test_empty_at_l0(self) -> None:
        """L0 scaffold obligation: zero routes registered. L1a adds real
        routers one blueprint at a time."""
        from src.admin.app_factory import build_admin_router

        router = build_admin_router()
        assert router.routes == []
