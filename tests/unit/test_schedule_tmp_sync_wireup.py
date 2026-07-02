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
