"""Unit tests for fire_tmp_sync transport-agnostic wireup.

Verifies that ``fire_tmp_sync`` (in tmp_provider_sync) is called from all
transport wrappers — ``create_media_buy`` / ``update_media_buy`` (MCP) and
``create_media_buy_raw`` / ``update_media_buy_raw`` (A2A + REST) — so that
TMP package sync fires on every transport, not only on the REST path.

The guard ``if not media_buy_id or not tenant_id`` inside ``fire_tmp_sync``
is a silent no-op on either falsy value — these tests pin the call shape so
that guard cannot silently regress.

beads: salesagent-tmp-sync
"""

from __future__ import annotations

from unittest.mock import ANY, MagicMock, patch


def _make_identity(tenant_id: str = "tenant-1") -> MagicMock:
    """Return a minimal ResolvedIdentity mock."""
    identity = MagicMock()
    identity.tenant_id = tenant_id
    return identity


def _make_response(media_buy_id: str = "mb-abc") -> MagicMock:
    """Return a mock response with a media_buy_id and model_dump returning a dict."""
    resp = MagicMock()
    resp.media_buy_id = media_buy_id
    resp.model_dump.return_value = {"media_buy_id": media_buy_id}
    return resp


class TestFireTmpSyncOnCreate:
    """create_media_buy_raw spawns a TMP sync thread after a successful create.

    We verify the thread is spawned (via threading.Thread) rather than patching
    fire_tmp_sync directly, because fire_tmp_sync is imported inside the function
    body and patching the deferred import site is fragile.  threading.Thread is
    the observable side-effect that proves the sync was triggered.
    """

    def test_thread_spawned_on_successful_create(self):
        """A daemon thread targeting sync_packages_for_media_buy is started on success."""
        import asyncio

        from src.core.tools.media_buy_create import create_media_buy_raw

        identity = _make_identity(tenant_id="tenant-1")
        create_response = _make_response(media_buy_id="mb-create-1")

        mock_req = MagicMock()
        mock_req.account = None

        with (
            patch(
                "src.core.tools.media_buy_create._create_media_buy_impl",
                return_value=create_response,
            ),
            patch(
                "src.core.tools.media_buy_create._build_create_media_buy_request",
                return_value=mock_req,
            ),
            patch(
                "src.core.transport_helpers.enrich_identity_with_account",
                return_value=identity,
            ),
            patch("src.services.tmp_provider_sync.threading.Thread") as mock_thread_cls,
        ):
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            asyncio.run(create_media_buy_raw(identity=identity))

        mock_thread_cls.assert_called_once_with(
            target=ANY,
            args=("tenant-1", "mb-create-1"),
            daemon=True,
            name=ANY,
        )
        mock_thread.start.assert_called_once_with()

    def test_no_thread_when_create_raises(self):
        """No thread is spawned when _create_media_buy_impl raises."""
        import asyncio

        import pytest

        from src.core.tools.media_buy_create import create_media_buy_raw

        identity = _make_identity(tenant_id="tenant-1")

        mock_req = MagicMock()
        mock_req.account = None

        with (
            patch(
                "src.core.tools.media_buy_create._create_media_buy_impl",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "src.core.tools.media_buy_create._build_create_media_buy_request",
                return_value=mock_req,
            ),
            patch(
                "src.core.transport_helpers.enrich_identity_with_account",
                return_value=identity,
            ),
            patch("src.services.tmp_provider_sync.threading.Thread") as mock_thread_cls,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                asyncio.run(create_media_buy_raw(identity=identity))

        mock_thread_cls.assert_not_called()


class TestFireTmpSyncOnUpdate:
    """update_media_buy_raw spawns a TMP sync thread after a successful update.

    Same rationale as TestFireTmpSyncOnCreate — we observe threading.Thread.
    """

    def test_thread_spawned_on_successful_update(self):
        """A daemon thread targeting sync_packages_for_media_buy is started on success."""
        from src.core.tools.media_buy_update import update_media_buy_raw

        identity = _make_identity(tenant_id="tenant-2")
        update_response = _make_response(media_buy_id="mb-update-1")

        with (
            patch(
                "src.core.tools.media_buy_update._update_media_buy_impl",
                return_value=update_response,
            ),
            patch(
                "src.core.tools.media_buy_update._build_update_request",
                return_value=MagicMock(),
            ),
            patch("src.services.tmp_provider_sync.threading.Thread") as mock_thread_cls,
        ):
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            update_media_buy_raw(media_buy_id="mb-update-1", identity=identity)

        mock_thread_cls.assert_called_once_with(
            target=ANY,
            args=("tenant-2", "mb-update-1"),
            daemon=True,
            name=ANY,
        )
        mock_thread.start.assert_called_once_with()

    def test_no_thread_when_update_raises(self):
        """No thread is spawned when _update_media_buy_impl raises."""
        import pytest

        from src.core.tools.media_buy_update import update_media_buy_raw

        identity = _make_identity(tenant_id="tenant-2")

        with (
            patch(
                "src.core.tools.media_buy_update._update_media_buy_impl",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "src.core.tools.media_buy_update._build_update_request",
                return_value=MagicMock(),
            ),
            patch("src.services.tmp_provider_sync.threading.Thread") as mock_thread_cls,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                update_media_buy_raw(media_buy_id="mb-update-1", identity=identity)

        mock_thread_cls.assert_not_called()


class TestFireTmpSyncInternals:
    """Unit tests for fire_tmp_sync media_buy_id extraction and thread-spawn logic.

    The function must handle two response shapes:
    - ``CreateMediaBuyResult`` wrapper: media_buy_id is on ``.response.media_buy_id``
    - ``UpdateMediaBuySuccess | UpdateMediaBuyError``: media_buy_id is directly on the object
    """

    def test_spawns_thread_for_direct_media_buy_id(self):
        """Update path: media_buy_id directly on response — thread is spawned."""
        from src.services.tmp_provider_sync import fire_tmp_sync

        resp = MagicMock()
        resp.media_buy_id = "mb-direct-001"

        with patch("src.services.tmp_provider_sync.threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            fire_tmp_sync(resp, "tenant-1")

        mock_thread_cls.assert_called_once_with(
            target=ANY,
            args=("tenant-1", "mb-direct-001"),
            daemon=True,
            name=ANY,
        )
        mock_thread.start.assert_called_once_with()

    def test_spawns_thread_for_inner_response_media_buy_id(self):
        """Create path: media_buy_id on .response.media_buy_id — thread is spawned.

        CreateMediaBuyResult has no direct media_buy_id — it wraps a
        CreateMediaBuySuccess in its .response field.
        """
        from src.services.tmp_provider_sync import fire_tmp_sync

        inner = MagicMock()
        inner.media_buy_id = "mb-inner-001"

        class _WrapperWithNoMediaBuyId:
            """Minimal stand-in for CreateMediaBuyResult (no media_buy_id attribute)."""

            response = inner

        with patch("src.services.tmp_provider_sync.threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            fire_tmp_sync(_WrapperWithNoMediaBuyId(), "tenant-1")

        mock_thread_cls.assert_called_once_with(
            target=ANY,
            args=("tenant-1", "mb-inner-001"),
            daemon=True,
            name=ANY,
        )
        mock_thread.start.assert_called_once_with()

    def test_no_thread_when_media_buy_id_absent(self):
        """No thread spawned when neither direct nor inner response has media_buy_id."""
        from src.services.tmp_provider_sync import fire_tmp_sync

        class _NoIdResponse:
            """Response with no media_buy_id anywhere."""

        with patch("src.services.tmp_provider_sync.threading.Thread") as mock_thread_cls:
            fire_tmp_sync(_NoIdResponse(), "tenant-1")

        mock_thread_cls.assert_not_called()

    def test_no_thread_when_tenant_id_absent(self):
        """No thread spawned when tenant_id is None."""
        from src.services.tmp_provider_sync import fire_tmp_sync

        resp = MagicMock()
        resp.media_buy_id = "mb-001"

        with patch("src.services.tmp_provider_sync.threading.Thread") as mock_thread_cls:
            fire_tmp_sync(resp, None)

        mock_thread_cls.assert_not_called()

    def test_thread_targets_sync_packages_for_media_buy(self):
        """Thread is created with sync_packages_for_media_buy as target and correct args."""
        from src.services.tmp_provider_sync import fire_tmp_sync, sync_packages_for_media_buy

        resp = MagicMock()
        resp.media_buy_id = "mb-xyz"

        with patch("src.services.tmp_provider_sync.threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            fire_tmp_sync(resp, "tenant-99")

        mock_thread_cls.assert_called_once_with(
            target=sync_packages_for_media_buy,
            args=("tenant-99", "mb-xyz"),
            daemon=True,
            name="tmp-sync-mb-xyz",
        )
        mock_thread.start.assert_called_once_with()


class TestFireTmpSyncOnMcpCreate:
    """create_media_buy (MCP wrapper) spawns a TMP sync thread after a successful create.

    The MCP wrapper calls _create_media_buy_impl directly (not via _raw), so it
    needs its own fire_tmp_sync call.  This class pins that the MCP path is wired.
    """

    def test_thread_spawned_on_successful_mcp_create(self):
        """A daemon thread is started when the MCP create wrapper succeeds."""
        import asyncio

        from src.core.tools.media_buy_create import create_media_buy

        identity = _make_identity(tenant_id="tenant-mcp-1")
        create_response = _make_response(media_buy_id="mb-mcp-create-1")

        mock_req = MagicMock()
        mock_req.account = None

        async def _fake_get_state(key):
            if key == "identity":
                return identity
            return None

        mock_ctx = MagicMock()
        mock_ctx.get_state = _fake_get_state

        with (
            patch(
                "src.core.tools.media_buy_create._create_media_buy_impl",
                return_value=create_response,
            ),
            patch(
                "src.core.tools.media_buy_create._build_create_media_buy_request",
                return_value=mock_req,
            ),
            patch(
                "src.core.transport_helpers.enrich_identity_with_account",
                return_value=identity,
            ),
            patch(
                "src.core.tools.media_buy_create.ToolResult",
                side_effect=lambda content, structured_content: MagicMock(),
            ),
            patch("src.services.tmp_provider_sync.threading.Thread") as mock_thread_cls,
        ):
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            asyncio.run(create_media_buy(ctx=mock_ctx))

        mock_thread_cls.assert_called_once_with(
            target=ANY,
            args=("tenant-mcp-1", "mb-mcp-create-1"),
            daemon=True,
            name=ANY,
        )
        mock_thread.start.assert_called_once_with()


class TestFireTmpSyncOnMcpUpdate:
    """update_media_buy (MCP wrapper) spawns a TMP sync thread after a successful update.

    The MCP wrapper calls _update_media_buy_impl directly (not via _raw), so it
    needs its own fire_tmp_sync call.  This class pins that the MCP path is wired.
    """

    def test_thread_spawned_on_successful_mcp_update(self):
        """A daemon thread is started when the MCP update wrapper succeeds."""
        import asyncio

        from fastmcp.server.context import Context

        from src.core.tools.media_buy_update import update_media_buy

        identity = _make_identity(tenant_id="tenant-mcp-2")
        update_response = _make_response(media_buy_id="mb-mcp-update-1")

        async def _fake_get_state(key):
            if key == "identity":
                return identity
            return None

        # spec=Context makes isinstance(mock_ctx, Context) return True so the
        # wrapper's `if isinstance(ctx, Context)` branch is taken and identity
        # is read from get_state rather than falling back to None.
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = _fake_get_state

        with (
            patch(
                "src.core.tools.media_buy_update._update_media_buy_impl",
                return_value=update_response,
            ),
            patch(
                "src.core.tools.media_buy_update._build_update_request",
                return_value=MagicMock(),
            ),
            patch(
                "src.core.tools.media_buy_update.ToolResult",
                side_effect=lambda content, structured_content: MagicMock(),
            ),
            patch("src.services.tmp_provider_sync.threading.Thread") as mock_thread_cls,
        ):
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            asyncio.run(update_media_buy(ctx=mock_ctx))

        mock_thread_cls.assert_called_once_with(
            target=ANY,
            args=("tenant-mcp-2", "mb-mcp-update-1"),
            daemon=True,
            name=ANY,
        )
        mock_thread.start.assert_called_once_with()
