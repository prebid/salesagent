"""CreativeSyncEnv — integration test environment for _sync_creatives_impl.

Patches: creative agent registry, run_async_in_sync_context, notifications, audit, config.
Real: get_db_session, CreativeRepository, all validation/processing (all hit real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")

            response = env.call_impl(creatives=[{
                "creative_id": "c1",
                "name": "Test Creative",
                "format_id": {"id": "display_300x250", "agent_url": "..."},
                "media_url": "https://example.com/img.png",
            }])
            assert len(response.results) == 1

Generative creative usage::

    with CreativeSyncEnv() as env:
        env.setup_default_data()
        fmt = env.setup_generative_build(
            format_id="gen_banner",
            build_result={"status": "draft", "context_id": "ctx-1", "creative_output": {}},
        )
        result = env.call_via(transport, creatives=[{
            "creative_id": "c1",
            "name": "Gen Creative",
            "format_id": fmt,
            "assets": {"message": {"content": "Build me a banner"}},
        }])

Available mocks via env.mock:
    "registry"           -- get_creative_agent_registry (lazy import in _sync.py)
    "run_async"          -- run_async_in_sync_context (module-level import in _sync.py)
    "send_notifications" -- _send_creative_notifications (from _workflow)
    "audit_log"          -- _audit_log_sync (from _workflow)
    "config"             -- get_config (lazy import in _processing.py)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from src.core.schemas import SyncCreativesResponse
from tests.harness._base import IntegrationEnv


class CreativeSyncEnv(IntegrationEnv):
    """Integration test environment for _sync_creatives_impl.

    Only mocks external services (creative agent registry, async runner,
    notifications, audit logging). Everything else is real:
    - Real get_db_session -> real DB queries
    - Real CreativeRepository -> real DB writes
    - Real validation/processing -> real business logic
    """

    EXTERNAL_PATCHES = {
        "registry": "src.core.creative_agent_registry.get_creative_agent_registry",
        "run_async": "src.core.tools.creatives._sync.run_async_in_sync_context",
        "send_notifications": "src.core.tools.creatives._sync._send_creative_notifications",
        "audit_log": "src.core.tools.creatives._sync._audit_log_sync",
        "config": "src.core.config.get_config",
    }
    DEFAULT_AGENT_URL = "https://creative.test.example.com"
    REST_ENDPOINT = "/api/v1/creatives/sync"

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for external mocks."""
        # Registry: return a mock that supports list_all_formats() + get_format()
        mock_registry = MagicMock()
        mock_registry.list_all_formats.return_value = []
        # get_format must return a coroutine (consumed by run_async_in_sync_context
        # in _validation.py). Return a truthy value to pass format existence check.
        mock_registry.get_format = AsyncMock(return_value={"id": "display_300x250", "name": "Display 300x250"})
        # build_creative and preview_creative must be AsyncMock because
        # _processing.py uses the REAL run_async_in_sync_context (not patched there).
        mock_registry.build_creative = AsyncMock(return_value={})
        mock_registry.preview_creative = AsyncMock(return_value={})
        self.mock["registry"].return_value = mock_registry

        # run_async: execute the coroutine synchronously (return empty list)
        self.mock["run_async"].side_effect = lambda coro: []

        # Notifications: no-op
        self.mock["send_notifications"].return_value = None

        # Audit log: no-op
        self.mock["audit_log"].return_value = None

        # Config: default with no gemini key (safe for static creatives)
        mock_config = MagicMock()
        mock_config.gemini_api_key = None
        self.mock["config"].return_value = mock_config

    def setup_generative_build(
        self,
        format_id: str = "display_gen",
        agent_url: str | None = None,
        build_result: dict[str, Any] | None = None,
        gemini_api_key: str = "test-gemini-key",
    ) -> dict[str, str]:
        """Configure harness for generative creative testing.

        Sets up:
        - A format mock with output_format_ids (makes it generative)
        - build_creative AsyncMock with the given return value
        - gemini_api_key on the config mock
        - run_async to return the generative format list

        Returns a format_id dict for use in creative payloads::

            fmt = env.setup_generative_build(format_id="gen_banner")
            creative = {"creative_id": "c1", "name": "Test", "format_id": fmt, ...}
        """
        from adcp.types.generated_poc.core.format_id import FormatId as LibraryFormatId

        agent = agent_url or self.DEFAULT_AGENT_URL

        # Create format mock with matching FormatId
        mock_format = MagicMock()
        mock_format.format_id = LibraryFormatId(agent_url=agent, id=format_id)
        mock_format.agent_url = agent
        mock_format.output_format_ids = [format_id]  # Non-empty → generative

        # Configure run_async to return this format for list_all_formats
        self.set_run_async_result([mock_format])

        # Configure build_creative return value
        default_build = {
            "status": "draft",
            "context_id": "ctx-test-123",
            "creative_output": {
                "assets": {"headline": {"text": "Generated headline"}},
                "output_format": {"url": "https://generated.example.com/creative.html"},
            },
        }
        registry = self.mock["registry"].return_value
        registry.build_creative = AsyncMock(return_value=build_result or default_build)

        # Also configure get_format to return this format for validation
        registry.get_format = AsyncMock(return_value=mock_format)

        # Set gemini API key
        self.mock["config"].return_value.gemini_api_key = gemini_api_key

        return {"agent_url": agent, "id": format_id}

    def set_run_async_result(self, formats: list[Any]) -> None:
        """Configure run_async_in_sync_context to return *formats*.

        Unlike CreativeFormatsEnv.set_registry_formats (which patches
        registry.list_all_formats directly), this patches the sync bridge
        that wraps the async call in _sync.py.
        """
        self.mock["run_async"].side_effect = lambda coro: formats

    def call_impl(self, **kwargs: Any) -> SyncCreativesResponse:
        """Call _sync_creatives_impl with real DB.

        Accepts all _sync_creatives_impl kwargs. The 'identity' kwarg
        defaults to self.identity if not provided.

        If 'account' is present, resolves it via enrich_identity_with_account
        (same as the transport wrappers do) before calling _impl.
        """
        from src.core.tools.creatives._sync import _sync_creatives_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        kwargs.setdefault("creatives", [])

        # Handle account kwarg — resolve at boundary, same as wrappers
        account = kwargs.pop("account", None)
        if account is not None:
            from src.core.transport_helpers import enrich_identity_with_account

            kwargs["identity"] = enrich_identity_with_account(kwargs["identity"], account)

        return _sync_creatives_impl(**kwargs)

    def call_a2a(self, **kwargs: Any) -> SyncCreativesResponse:
        """Call sync_creatives_raw (A2A wrapper) with real DB.

        Note: uses _raw() path instead of _run_a2a_handler because the real
        A2A handler's _handle_sync_creatives_skill constructs CreativeAsset
        from raw dicts, which fails validation (assets field required).
        That handler bug needs a separate fix.
        """
        from src.core.tools.creatives.sync_wrappers import sync_creatives_raw

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        kwargs.setdefault("creatives", [])
        return sync_creatives_raw(**kwargs)

    def call_mcp(self, **kwargs: Any) -> SyncCreativesResponse:
        """Call sync_creatives via Client(mcp) — full pipeline dispatch.

        No enum coercion needed — FastMCP's TypeAdapter handles it automatically.
        """
        kwargs.setdefault("creatives", [])
        return self._run_mcp_client("sync_creatives", SyncCreativesResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to SyncCreativesBody shape for REST POST."""
        # The REST body expects 'creatives' as list[dict], matching SyncCreativesBody
        body: dict[str, Any] = {}
        if "creatives" in kwargs:
            creatives = kwargs["creatives"]
            # Convert Pydantic models to dicts if needed
            body["creatives"] = [c.model_dump(mode="json") if hasattr(c, "model_dump") else c for c in creatives]
        if "assignments" in kwargs and kwargs["assignments"] is not None:
            body["assignments"] = kwargs["assignments"]
        if "creative_ids" in kwargs and kwargs["creative_ids"] is not None:
            body["creative_ids"] = kwargs["creative_ids"]
        if "delete_missing" in kwargs:
            body["delete_missing"] = kwargs["delete_missing"]
        if "dry_run" in kwargs:
            body["dry_run"] = kwargs["dry_run"]
        if "validation_mode" in kwargs:
            body["validation_mode"] = kwargs["validation_mode"]
        if "account" in kwargs and kwargs["account"] is not None:
            account = kwargs["account"]
            body["account"] = account.model_dump(mode="json") if hasattr(account, "model_dump") else account
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> SyncCreativesResponse:
        """Parse REST JSON into SyncCreativesResponse."""
        return SyncCreativesResponse(**data)
