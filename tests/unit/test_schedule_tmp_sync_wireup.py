"""Unit tests for _schedule_tmp_sync REST wireup in api_v1.py.

Verifies that POST /api/v1/media-buys and PUT /api/v1/media-buys/{id} both
call _schedule_tmp_sync with the BackgroundTasks instance, the resolved
identity, and the raw response object.

The guard `if response.media_buy_id and identity.tenant_id` inside
_schedule_tmp_sync is a silent no-op on either falsy value — these tests pin
the call shape so that guard cannot silently regress.

beads: salesagent-tmp-sync
"""

from __future__ import annotations

from unittest.mock import ANY, AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


def _make_identity(tenant_id: str = "tenant-1") -> MagicMock:
    """Return a minimal ResolvedIdentity mock."""
    identity = MagicMock()
    identity.tenant_id = tenant_id
    return identity


def _make_response(media_buy_id: str = "mb-abc") -> MagicMock:
    """Return a mock response with a media_buy_id and model_dump."""
    resp = MagicMock()
    resp.media_buy_id = media_buy_id
    resp.model_dump.return_value = {"media_buy_id": media_buy_id}
    return resp


class TestScheduleTmpSyncOnCreate:
    """POST /api/v1/media-buys calls _schedule_tmp_sync with correct args."""

    def test_schedule_tmp_sync_called_on_create(self):
        """_schedule_tmp_sync is called with (background_tasks, identity, response)."""
        from src.app import app
        from src.core.auth_context import _require_auth_dep

        identity = _make_identity(tenant_id="tenant-1")
        create_response = _make_response(media_buy_id="mb-create-1")

        app.dependency_overrides[_require_auth_dep] = lambda: identity
        try:
            with (
                patch(
                    "src.routes.api_v1.media_buy_create_module.create_media_buy_raw",
                    new_callable=AsyncMock,
                    return_value=create_response,
                ),
                patch("src.routes.api_v1._schedule_tmp_sync") as mock_schedule,
            ):
                client = TestClient(app, raise_server_exceptions=True)
                response = client.post(
                    "/api/v1/media-buys",
                    json={"packages": []},
                )
        finally:
            app.dependency_overrides.pop(_require_auth_dep, None)

        assert response.status_code == 200
        # ANY matches the BackgroundTasks instance (opaque, injected by FastAPI)
        mock_schedule.assert_called_once_with(ANY, identity, create_response)

    def test_schedule_tmp_sync_not_called_when_create_raises(self):
        """_schedule_tmp_sync is NOT called when create_media_buy_raw raises."""
        from src.app import app
        from src.core.auth_context import _require_auth_dep

        identity = _make_identity(tenant_id="tenant-1")

        app.dependency_overrides[_require_auth_dep] = lambda: identity
        try:
            with (
                patch(
                    "src.routes.api_v1.media_buy_create_module.create_media_buy_raw",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("boom"),
                ),
                patch("src.routes.api_v1._schedule_tmp_sync") as mock_schedule,
            ):
                client = TestClient(app, raise_server_exceptions=False)
                client.post("/api/v1/media-buys", json={"packages": []})
        finally:
            app.dependency_overrides.pop(_require_auth_dep, None)

        mock_schedule.assert_not_called()


class TestScheduleTmpSyncOnUpdate:
    """PUT /api/v1/media-buys/{id} calls _schedule_tmp_sync with correct args."""

    def test_schedule_tmp_sync_called_on_update(self):
        """_schedule_tmp_sync is called with (background_tasks, identity, response)."""
        from src.app import app
        from src.core.auth_context import _require_auth_dep

        identity = _make_identity(tenant_id="tenant-2")
        update_response = _make_response(media_buy_id="mb-update-1")

        app.dependency_overrides[_require_auth_dep] = lambda: identity
        try:
            with (
                patch(
                    "src.routes.api_v1.media_buy_update_module.update_media_buy_raw",
                    return_value=update_response,
                ),
                patch("src.routes.api_v1._schedule_tmp_sync") as mock_schedule,
            ):
                client = TestClient(app, raise_server_exceptions=True)
                response = client.put(
                    "/api/v1/media-buys/mb-update-1",
                    json={},
                )
        finally:
            app.dependency_overrides.pop(_require_auth_dep, None)

        assert response.status_code == 200
        # ANY matches the BackgroundTasks instance (opaque, injected by FastAPI)
        mock_schedule.assert_called_once_with(ANY, identity, update_response)

    def test_schedule_tmp_sync_not_called_when_update_raises(self):
        """_schedule_tmp_sync is NOT called when update_media_buy_raw raises."""
        from src.app import app
        from src.core.auth_context import _require_auth_dep

        identity = _make_identity(tenant_id="tenant-2")

        app.dependency_overrides[_require_auth_dep] = lambda: identity
        try:
            with (
                patch(
                    "src.routes.api_v1.media_buy_update_module.update_media_buy_raw",
                    side_effect=RuntimeError("boom"),
                ),
                patch("src.routes.api_v1._schedule_tmp_sync") as mock_schedule,
            ):
                client = TestClient(app, raise_server_exceptions=False)
                client.put("/api/v1/media-buys/mb-update-1", json={})
        finally:
            app.dependency_overrides.pop(_require_auth_dep, None)

        mock_schedule.assert_not_called()


class TestScheduleTmpSyncInternals:
    """Unit tests for _schedule_tmp_sync media_buy_id extraction logic.

    The function must handle two response shapes:
    - ``CreateMediaBuyResult`` wrapper: media_buy_id is on ``.response.media_buy_id``
    - ``UpdateMediaBuySuccess | UpdateMediaBuyError``: media_buy_id is directly on the object
    """

    def _make_background_tasks(self):
        from fastapi import BackgroundTasks

        return BackgroundTasks()

    def _make_identity(self, tenant_id: str = "tenant-1") -> MagicMock:
        identity = MagicMock()
        identity.tenant_id = tenant_id
        return identity

    def test_extracts_media_buy_id_from_direct_attribute(self):
        """Update path: media_buy_id directly on response (UpdateMediaBuySuccess shape)."""
        from src.routes.api_v1 import _schedule_tmp_sync

        resp = MagicMock()
        resp.media_buy_id = "mb-direct-001"
        identity = self._make_identity()
        bg = self._make_background_tasks()

        with patch("src.services.tmp_provider_sync.sync_packages_for_media_buy"):
            _schedule_tmp_sync(bg, identity, resp)

        assert len(bg.tasks) == 1

    def test_extracts_media_buy_id_from_inner_response(self):
        """Create path: media_buy_id on .response.media_buy_id (CreateMediaBuyResult shape).

        CreateMediaBuyResult has no direct media_buy_id — it wraps a
        CreateMediaBuySuccess in its .response field. Accessing .media_buy_id
        directly raises AttributeError; the fix uses getattr with a fallback.
        """
        from src.routes.api_v1 import _schedule_tmp_sync

        # Simulate CreateMediaBuyResult: inner response has media_buy_id, wrapper does not.
        inner = MagicMock()
        inner.media_buy_id = "mb-inner-001"

        class _WrapperWithNoMediaBuyId:
            """Minimal stand-in for CreateMediaBuyResult (no media_buy_id attribute)."""

            response = inner

        identity = self._make_identity()
        bg = self._make_background_tasks()

        with patch("src.services.tmp_provider_sync.sync_packages_for_media_buy"):
            _schedule_tmp_sync(bg, identity, _WrapperWithNoMediaBuyId())

        assert len(bg.tasks) == 1

    def test_no_task_when_media_buy_id_absent(self):
        """No task scheduled when neither direct nor inner response has media_buy_id."""
        from src.routes.api_v1 import _schedule_tmp_sync

        class _NoIdResponse:
            """Response with no media_buy_id anywhere."""

        identity = self._make_identity()
        bg = self._make_background_tasks()

        with patch("src.services.tmp_provider_sync.sync_packages_for_media_buy"):
            _schedule_tmp_sync(bg, identity, _NoIdResponse())

        assert len(bg.tasks) == 0

    def test_no_task_when_tenant_id_absent(self):
        """No task scheduled when identity has no tenant_id."""
        from src.routes.api_v1 import _schedule_tmp_sync

        resp = MagicMock()
        resp.media_buy_id = "mb-001"
        identity = MagicMock()
        identity.tenant_id = None
        bg = self._make_background_tasks()

        with patch("src.services.tmp_provider_sync.sync_packages_for_media_buy"):
            _schedule_tmp_sync(bg, identity, resp)

        assert len(bg.tasks) == 0
