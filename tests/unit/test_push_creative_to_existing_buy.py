"""Unit tests: push_creative_to_existing_buy.

Verifies the retroactive-push function that sends a single approved creative
to an already-active ad server line item when it was held back at buy-approval
time due to pending_review status (prebid#1038).
"""

from unittest.mock import ANY, MagicMock, patch

# Patch targets
_MODULE = "src.core.tools.media_buy_create"
_UOW_PATCH = "src.core.database.repositories.uow.AdminCreativeUoW"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_uow(*, tenant=None, creative=None, assignments=None, package=None):
    """AdminCreativeUoW context manager with configurable repository returns."""
    uow = MagicMock()
    uow.tenant_config.get_tenant.return_value = tenant
    uow.creatives.admin_get_by_id.return_value = creative
    uow.assignments.get_by_creative.return_value = assignments if assignments is not None else []
    uow.media_buys.get_package.return_value = package
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    return uow


def _make_tenant(tenant_id: str = "t1") -> MagicMock:
    t = MagicMock()
    t.tenant_id = tenant_id
    t.ad_server = "mock"
    return t


def _make_creative(
    creative_id: str = "cre_1",
    principal_id: str = "p1",
) -> MagicMock:
    c = MagicMock()
    c.creative_id = creative_id
    c.principal_id = principal_id
    c.status = "approved"
    c.format = "display_300x250_image"
    c.agent_url = None
    c.data = {}
    c.name = "Test Creative"
    return c


def _make_assignment(
    creative_id: str = "cre_1",
    media_buy_id: str = "mb_1",
    package_id: str = "pkg_1",
) -> MagicMock:
    a = MagicMock()
    a.creative_id = creative_id
    a.media_buy_id = media_buy_id
    a.package_id = package_id
    a.weight = 100
    return a


def _make_package(
    package_id: str = "pkg_1",
    platform_order_id: str = "gam_order_99",
    platform_line_item_id: str = "gam_li_99",
) -> MagicMock:
    p = MagicMock()
    p.package_id = package_id
    p.package_config = {
        "platform_order_id": platform_order_id,
        "platform_line_item_id": platform_line_item_id,
    }
    return p


def _make_adapter(*, status: str = "success") -> MagicMock:
    asset_status = MagicMock()
    asset_status.creative_id = "cre_1"
    asset_status.status = status
    asset_status.message = "Adapter error" if status == "failed" else None

    adapter = MagicMock()
    adapter.creatives_manager.add_creative_assets.return_value = [asset_status]
    return adapter


def _call(creative_id: str = "cre_1", media_buy_id: str = "mb_1", tenant_id: str = "t1"):
    from src.core.tools.media_buy_create import push_creative_to_existing_buy

    return push_creative_to_existing_buy(
        creative_id=creative_id,
        media_buy_id=media_buy_id,
        tenant_id=tenant_id,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestPushCreativeToExistingBuy:
    """push_creative_to_existing_buy sends an approved creative to a live buy."""

    def test_happy_path_calls_adapter_returns_success(self):
        """Adapter is called once with the GAM order ID; returns (True, None)."""
        creative = _make_creative()
        uow = _make_uow(
            tenant=_make_tenant(),
            creative=creative,
            assignments=[_make_assignment()],
            package=_make_package(),
        )
        mock_adapter = _make_adapter()

        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
            patch(f"{_MODULE}.get_principal_object", return_value=MagicMock()),
            patch(f"{_MODULE}.get_adapter", return_value=mock_adapter),
            patch(
                f"{_MODULE}.extract_media_url_and_dimensions", return_value=("https://ad.example.com/ad.jpg", 300, 250)
            ),
            patch(f"{_MODULE}.extract_click_url", return_value=None),
            patch(f"{_MODULE}.extract_impression_tracker_url", return_value=None),
        ):
            success, err = _call()

        assert success is True
        assert err is None
        mock_adapter.creatives_manager.add_creative_assets.assert_called_once_with(
            "gam_order_99",  # GAM order ID from package_config["platform_order_id"]
            ANY,  # assets list
            ANY,  # datetime
        )
        uow.creatives.update_data.assert_called_once_with(
            creative,
            {"platform_creative_id": "cre_1"},
        )

    def test_happy_path_skips_adapter_when_platform_creative_id_already_set(self):
        """Does not call the adapter when platform_creative_id is already persisted."""
        creative = _make_creative()
        creative.data = {"platform_creative_id": "existing_gam_id"}
        uow = _make_uow(
            tenant=_make_tenant(),
            creative=creative,
            assignments=[_make_assignment()],
            package=_make_package(),
        )
        mock_adapter = _make_adapter()

        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
            patch(f"{_MODULE}.get_principal_object", return_value=MagicMock()) as mock_principal,
            patch(f"{_MODULE}.get_adapter", return_value=mock_adapter) as mock_get_adapter,
        ):
            success, err = _call()

        assert success is True
        assert err is None
        mock_principal.assert_not_called()
        mock_get_adapter.assert_not_called()
        mock_adapter.creatives_manager.add_creative_assets.assert_not_called()
        uow.creatives.update_data.assert_not_called()

    def test_tenant_not_found_returns_failure(self):
        """Returns (False, ...) when tenant lookup returns None."""
        uow = _make_uow(tenant=None)
        with patch(_UOW_PATCH, return_value=uow):
            success, err = _call()

        assert success is False
        assert "Tenant" in err

    def test_creative_not_found_returns_failure(self):
        """Returns (False, ...) when creative does not exist for the tenant."""
        uow = _make_uow(tenant=_make_tenant(), creative=None)
        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
        ):
            success, err = _call()

        assert success is False
        assert "Creative" in err

    def test_pending_review_creative_returns_failure(self):
        """Returns (False, ...) when creative is not yet approved."""
        creative = _make_creative()
        creative.status = "pending_review"
        uow = _make_uow(
            tenant=_make_tenant(),
            creative=creative,
            assignments=[_make_assignment()],
            package=_make_package(),
        )
        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
        ):
            success, err = _call()

        assert success is False
        assert "not approved" in err.lower()

    def test_assignment_not_found_returns_failure(self):
        """Returns (False, ...) when no assignment links creative to the buy."""
        uow = _make_uow(
            tenant=_make_tenant(),
            creative=_make_creative(),
            assignments=[],  # no matching assignment
        )
        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
        ):
            success, err = _call()

        assert success is False
        assert "assignment" in err.lower()

    def test_package_not_found_returns_failure(self):
        """Returns (False, ...) when no MediaPackage row exists for the buy."""
        uow = _make_uow(
            tenant=_make_tenant(),
            creative=_make_creative(),
            assignments=[_make_assignment()],
            package=None,
        )
        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
            patch(f"{_MODULE}.get_principal_object", return_value=MagicMock()),
            patch(f"{_MODULE}.get_adapter", return_value=_make_adapter()),
        ):
            success, err = _call()

        assert success is False
        assert "package" in err.lower()

    def test_missing_platform_order_id_returns_failure(self):
        """Returns (False, ...) when package_config has no platform_order_id.

        Happens when the buy was never successfully pushed to the ad server.
        """
        pkg = MagicMock()
        pkg.package_id = "pkg_1"
        pkg.package_config = {}

        uow = _make_uow(
            tenant=_make_tenant(),
            creative=_make_creative(),
            assignments=[_make_assignment()],
            package=pkg,
        )
        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
            patch(f"{_MODULE}.get_principal_object", return_value=MagicMock()),
            patch(f"{_MODULE}.get_adapter", return_value=_make_adapter()),
        ):
            success, err = _call()

        assert success is False
        assert "platform_order_id" in err

    def test_adapter_without_creatives_manager_returns_failure(self):
        """Returns (False, ...) when adapter does not support creative upload."""
        adapter = MagicMock(spec=[])  # no creatives_manager attribute

        uow = _make_uow(
            tenant=_make_tenant(),
            creative=_make_creative(),
            assignments=[_make_assignment()],
            package=_make_package(),
        )
        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
            patch(f"{_MODULE}.get_principal_object", return_value=MagicMock()),
            patch(f"{_MODULE}.get_adapter", return_value=adapter),
        ):
            success, err = _call()

        assert success is False
        assert "does not support" in err

    def test_adapter_raises_returns_failure(self):
        """Returns (False, error_str) when add_creative_assets raises."""
        adapter = MagicMock()
        adapter.creatives_manager.add_creative_assets.side_effect = RuntimeError("GAM timeout")

        uow = _make_uow(
            tenant=_make_tenant(),
            creative=_make_creative(),
            assignments=[_make_assignment()],
            package=_make_package(),
        )
        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
            patch(f"{_MODULE}.get_principal_object", return_value=MagicMock()),
            patch(f"{_MODULE}.get_adapter", return_value=adapter),
            patch(
                f"{_MODULE}.extract_media_url_and_dimensions", return_value=("https://ad.example.com/ad.jpg", 300, 250)
            ),
            patch(f"{_MODULE}.extract_click_url", return_value=None),
            patch(f"{_MODULE}.extract_impression_tracker_url", return_value=None),
        ):
            success, err = _call()

        assert success is False
        assert "GAM timeout" in err

    def test_adapter_returns_failed_status(self):
        """Returns (False, message) when adapter reports a failed AssetStatus."""
        adapter = _make_adapter(status="failed")

        uow = _make_uow(
            tenant=_make_tenant(),
            creative=_make_creative(),
            assignments=[_make_assignment()],
            package=_make_package(),
        )
        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
            patch(f"{_MODULE}.get_principal_object", return_value=MagicMock()),
            patch(f"{_MODULE}.get_adapter", return_value=adapter),
            patch(
                f"{_MODULE}.extract_media_url_and_dimensions", return_value=("https://ad.example.com/ad.jpg", 300, 250)
            ),
            patch(f"{_MODULE}.extract_click_url", return_value=None),
            patch(f"{_MODULE}.extract_impression_tracker_url", return_value=None),
        ):
            success, err = _call()

        assert success is False
        assert err is not None

    def test_missing_creative_dimensions_returns_failure(self):
        """Returns (False, ...) when url/width/height cannot be extracted."""
        uow = _make_uow(
            tenant=_make_tenant(),
            creative=_make_creative(),
            assignments=[_make_assignment()],
            package=_make_package(),
        )
        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
            patch(f"{_MODULE}.get_principal_object", return_value=MagicMock()),
            patch(f"{_MODULE}.get_adapter", return_value=_make_adapter()),
            patch(f"{_MODULE}.extract_media_url_and_dimensions", return_value=(None, None, None)),
            patch(f"{_MODULE}.extract_click_url", return_value=None),
            patch(f"{_MODULE}.extract_impression_tracker_url", return_value=None),
        ):
            success, err = _call()

        assert success is False
        assert "missing" in err.lower()

    def test_adapter_no_status_for_creative_returns_failure(self):
        """Returns (False, ...) when adapter returns no status entry for our creative."""
        adapter = MagicMock()
        adapter.creatives_manager.add_creative_assets.return_value = []  # empty — no status

        uow = _make_uow(
            tenant=_make_tenant(),
            creative=_make_creative(),
            assignments=[_make_assignment()],
            package=_make_package(),
        )
        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
            patch(f"{_MODULE}.get_principal_object", return_value=MagicMock()),
            patch(f"{_MODULE}.get_adapter", return_value=adapter),
            patch(
                f"{_MODULE}.extract_media_url_and_dimensions", return_value=("https://ad.example.com/ad.jpg", 300, 250)
            ),
            patch(f"{_MODULE}.extract_click_url", return_value=None),
            patch(f"{_MODULE}.extract_impression_tracker_url", return_value=None),
        ):
            success, err = _call()

        assert success is False
        assert "did not report status" in err

    def test_multi_package_all_assignments_included(self):
        """All package assignments for the creative+buy are included in the asset dict."""
        asgn1 = _make_assignment(package_id="pkg_1")
        asgn2 = _make_assignment(package_id="pkg_2")

        pkg1 = _make_package(package_id="pkg_1")
        pkg2 = _make_package(package_id="pkg_2")

        uow = _make_uow(
            tenant=_make_tenant(),
            creative=_make_creative(),
            assignments=[asgn1, asgn2],
        )
        # get_package returns different packages per package_id
        uow.media_buys.get_package.side_effect = lambda _buy, pkg_id: pkg1 if pkg_id == "pkg_1" else pkg2

        mock_adapter = _make_adapter()

        with (
            patch(_UOW_PATCH, return_value=uow),
            patch("src.core.config_loader.get_tenant_by_id", return_value=None),
            patch("src.core.config_loader.set_current_tenant"),
            patch(f"{_MODULE}.get_principal_object", return_value=MagicMock()),
            patch(f"{_MODULE}.get_adapter", return_value=mock_adapter),
            patch(
                f"{_MODULE}.extract_media_url_and_dimensions", return_value=("https://ad.example.com/ad.jpg", 300, 250)
            ),
            patch(f"{_MODULE}.extract_click_url", return_value=None),
            patch(f"{_MODULE}.extract_impression_tracker_url", return_value=None),
        ):
            success, err = _call()

        assert success is True
        call_args = mock_adapter.creatives_manager.add_creative_assets.call_args
        assert call_args is not None, "add_creative_assets was not called"
        _, assets_arg, *_ = call_args.args
        package_ids = {pa["package_id"] for pa in assets_arg[0]["package_assignments"]}
        assert package_ids == {"pkg_1", "pkg_2"}
