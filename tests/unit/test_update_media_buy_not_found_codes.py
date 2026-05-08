"""Wire-code coverage for update_media_buy not-found rejections (issue #73).

The AdCP storyboard's ``invalid_transitions`` scenarios assert that
``update_media_buy`` rejections surface specific spec codes:

* unknown ``media_buy_id`` -> ``MEDIA_BUY_NOT_FOUND``
* unknown ``package_id`` -> ``PACKAGE_NOT_FOUND``
* cross-tenant access (a buy that exists on a different tenant) ->
  ``MEDIA_BUY_NOT_FOUND`` (NOT a permissions code; surfacing 403 leaks
  cross-tenant existence to attackers)

These tests pin both layers:

1. ``_update_media_buy_impl`` raises the typed ``AdCPError`` subclass
   (transport-agnostic).
2. ``_delegate_update_media_buy`` translates that into the framework's
   wire-shaped ``AdcpError`` so the dispatcher emits the correct
   ``adcp_error.code`` envelope.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.core.exceptions import (
    AdCPMediaBuyNotFoundError,
    AdCPPackageNotFoundError,
)
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import UpdateMediaBuyRequest
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_update import _update_media_buy_impl, _verify_principal

MODULE = "src.core.tools.media_buy_update"


def _identity(principal_id: str = "principal_a", tenant_id: str = "tenant_a") -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id, "name": "Test"},
        protocol="mcp",
        testing_context=AdCPTestContext(),
    )


# ---------------------------------------------------------------------------
# Layer 1: _impl raises AdCPMediaBuyNotFoundError / AdCPPackageNotFoundError
# ---------------------------------------------------------------------------


class TestImplRaisesTypedNotFoundErrors:
    """``_update_media_buy_impl`` raises the typed AdCPError, not ToolError."""

    def test_unknown_media_buy_raises_media_buy_not_found(self) -> None:
        """media_buy_id with no matching row in the caller's tenant raises
        AdCPMediaBuyNotFoundError, which projects to wire code MEDIA_BUY_NOT_FOUND."""
        repo = MagicMock()
        repo.get_by_id.return_value = None

        with pytest.raises(AdCPMediaBuyNotFoundError) as exc_info:
            _verify_principal("mb_does_not_exist", _identity(), repo)

        assert exc_info.value.error_code == "MEDIA_BUY_NOT_FOUND"
        assert "mb_does_not_exist" in str(exc_info.value)

    def test_cross_tenant_lookup_surfaces_as_media_buy_not_found(self) -> None:
        """Tenant isolation invariant: when a buyer probes a media_buy_id that
        belongs to a different tenant, the tenant-scoped repo returns ``None``
        and we surface MEDIA_BUY_NOT_FOUND -- never a permissions error.
        Returning AUTHORIZATION_ERROR would leak cross-tenant existence.
        """
        # Repo is tenant-scoped; cross-tenant rows come back as None.
        repo = MagicMock()
        repo.get_by_id.return_value = None

        with pytest.raises(AdCPMediaBuyNotFoundError) as exc_info:
            _verify_principal("mb_exists_on_other_tenant", _identity(tenant_id="tenant_a"), repo)

        # Critically NOT AdCPAuthorizationError / AUTHORIZATION_ERROR.
        assert exc_info.value.error_code == "MEDIA_BUY_NOT_FOUND"

    def test_unknown_package_in_targeting_overlay_raises_package_not_found(self) -> None:
        """Updating targeting on a package_id that doesn't exist on this media buy
        raises AdCPPackageNotFoundError (wire code PACKAGE_NOT_FOUND)."""
        with self._stub_uow() as uow:
            # Media buy exists, package does not.
            uow.media_buys.get_package.return_value = None

            req = UpdateMediaBuyRequest(
                media_buy_id="mb_exists",
                packages=[
                    {
                        "package_id": "pkg_does_not_exist",
                        "targeting_overlay": {"include_segment": [{"segment_id": "s1"}]},
                    }
                ],
            )
            with pytest.raises(AdCPPackageNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=_identity())

        assert exc_info.value.error_code == "PACKAGE_NOT_FOUND"
        assert "pkg_does_not_exist" in str(exc_info.value)

    def test_unknown_package_with_paused_raises_package_not_found(self) -> None:
        """Pausing a package_id that doesn't exist on this media buy raises
        AdCPPackageNotFoundError (wire code PACKAGE_NOT_FOUND).

        This is the live-storyboard scenario: ``packages=[{"package_id":
        "does-not-exist", "paused": true}]`` against a real media buy.
        Without impl-level validation, the adapter returns a non-AdCP code
        like ``package_not_found`` (lowercase) which surfaces as opaque
        INTERNAL_ERROR after delegate translation.
        """
        with self._stub_uow() as uow:
            uow.media_buys.get_package.return_value = None

            req = UpdateMediaBuyRequest(
                media_buy_id="mb_exists",
                packages=[{"package_id": "pkg_does_not_exist", "paused": True}],
            )
            with pytest.raises(AdCPPackageNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=_identity())

        assert exc_info.value.error_code == "PACKAGE_NOT_FOUND"
        assert "pkg_does_not_exist" in str(exc_info.value)

    def test_unknown_package_with_budget_raises_package_not_found(self) -> None:
        """Budget update on a package_id that doesn't exist on this media buy
        raises AdCPPackageNotFoundError."""
        with self._stub_uow() as uow:
            uow.media_buys.get_package.return_value = None
            existing_mb = MagicMock()
            existing_mb.currency = "USD"
            uow.media_buys.get_by_id.return_value = existing_mb

            req = UpdateMediaBuyRequest(
                media_buy_id="mb_exists",
                packages=[{"package_id": "pkg_does_not_exist", "budget": 1000.0}],
            )
            with pytest.raises(AdCPPackageNotFoundError) as exc_info:
                _update_media_buy_impl(req=req, identity=_identity())

        assert exc_info.value.error_code == "PACKAGE_NOT_FOUND"
        assert "pkg_does_not_exist" in str(exc_info.value)

    def _stub_uow(self) -> Any:
        """Compose the patches that bypass DB / approval / audit."""
        return _NotFoundFixture()


class _NotFoundFixture:
    """Context manager wiring up just enough mocks for _impl to reach the
    package-lookup branch without touching real DB code paths.

    Yields the UoW mock instance so tests can configure repository returns.
    """

    def __enter__(self) -> Any:
        self._patchers: list[Any] = []

        ctx_mgr = MagicMock()
        ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_001")
        ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_001")

        mock_session = MagicMock()
        uow = MagicMock()
        uow.session = mock_session
        uow.media_buys = MagicMock()
        uow.currency_limits = MagicMock()
        cl = MagicMock()
        cl.max_daily_package_spend = None
        cl.min_package_budget = 0
        uow.currency_limits.get_for_currency.return_value = cl
        uow.__enter__ = Mock(return_value=uow)
        uow.__exit__ = Mock(return_value=False)

        adapter = MagicMock()
        adapter.manual_approval_required = False
        adapter.manual_approval_operations = []

        targets = {
            f"{MODULE}._verify_principal": MagicMock(return_value=None),
            f"{MODULE}.get_principal_object": MagicMock(
                return_value=MagicMock(principal_id="principal_a", name="P", platform_mappings={})
            ),
            f"{MODULE}.get_context_manager": MagicMock(return_value=ctx_mgr),
            f"{MODULE}.MediaBuyUoW": MagicMock(return_value=uow),
            f"{MODULE}.get_adapter": MagicMock(return_value=adapter),
            f"{MODULE}.get_audit_logger": MagicMock(return_value=MagicMock()),
        }
        for target, value in targets.items():
            p = patch(target, value)
            p.start()
            self._patchers.append(p)
        self._uow = uow
        return uow

    def __exit__(self, *exc: object) -> bool:
        for p in reversed(self._patchers):
            p.stop()
        return False


# ---------------------------------------------------------------------------
# Layer 2: delegate translates AdCPError -> framework AdcpError on the wire
# ---------------------------------------------------------------------------


class TestDelegateProjectsTypedErrorsToWireEnvelope:
    """``_delegate_update_media_buy`` translates AdCPError subclasses into
    the framework's decisioning ``AdcpError`` so the dispatcher emits a
    spec-compliant ``adcp_error.code`` envelope."""

    def test_media_buy_not_found_projects_to_wire_code(self) -> None:
        from adcp.decisioning.types import AdcpError

        from core.platforms._delegate import _delegate_update_media_buy

        ctx = self._make_ctx()

        with patch(
            "core.platforms._delegate._update_media_buy_impl",
            side_effect=AdCPMediaBuyNotFoundError("Media buy 'mb_x' not found."),
        ):
            with pytest.raises(AdcpError) as exc_info:
                self._run(_delegate_update_media_buy("mb_x", {}, ctx))

        assert exc_info.value.code == "MEDIA_BUY_NOT_FOUND"
        assert exc_info.value.recovery == "correctable"
        assert "mb_x" in (exc_info.value.args[0] if exc_info.value.args else "")

    def test_package_not_found_projects_to_wire_code(self) -> None:
        from adcp.decisioning.types import AdcpError

        from core.platforms._delegate import _delegate_update_media_buy

        ctx = self._make_ctx()

        with patch(
            "core.platforms._delegate._update_media_buy_impl",
            side_effect=AdCPPackageNotFoundError("Package 'pkg_z' not found"),
        ):
            with pytest.raises(AdcpError) as exc_info:
                self._run(
                    _delegate_update_media_buy(
                        "mb_x",
                        {"packages": [{"package_id": "pkg_z", "paused": True}]},
                        ctx,
                    )
                )

        assert exc_info.value.code == "PACKAGE_NOT_FOUND"
        assert exc_info.value.recovery == "correctable"

    def _make_ctx(self) -> Any:
        """Minimal ctx that satisfies _build_identity()."""
        ctx = MagicMock()
        ctx.account.metadata.get.return_value = "tenant_a"
        return ctx

    def _run(self, coro: Any) -> Any:
        import asyncio

        # Patch get_tenant_by_id so we don't hit the DB.
        with patch(
            "core.platforms._delegate.get_tenant_by_id",
            return_value={"tenant_id": "tenant_a", "name": "Test"},
        ):
            with patch(
                "core.platforms._delegate.current_principal",
                MagicMock(get=MagicMock(return_value="principal_a")),
            ):
                return asyncio.run(coro)
