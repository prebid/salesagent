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
            "assets": build_assets(
                text_spec("message", content="Build me a banner")
            ),
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
from tests.harness._realize import e2e_unsupported, realize_e2e
from tests.harness.egress import EgressHatchMixin
from tests.harness.transport import DeliverResult

# Kwargs ``IntegrationEnv._run_a2a_handler`` consumes itself (identity mock, protocol-level
# push config, request model) rather than forwarding as skill parameters — see
# tests/harness/_base.py. They must reach it untouched.
_A2A_RESERVED_KWARGS = frozenset({"identity", "a2a_push_notification_config", "req"})


def _clear_gemini_api_key_e2e(env: IntegrationEnv) -> None:
    """Null the shared-DB tenant ``gemini_api_key`` so live e2e sees a keyless seller.

    Production creative-sync advisories are account-scoped (tenant dict / DB
    column only), so clearing the process env is unnecessary — the live server
    re-reads the tenant row on each request.
    """
    from sqlalchemy import select

    from src.core.database.models import Tenant

    tenant = env.get_session().scalars(select(Tenant).filter_by(tenant_id=env._tenant_id)).first()
    if tenant is not None:
        tenant.gemini_api_key = None
        env._commit_factory_data()
    tenant_dict = getattr(env.identity, "tenant", None)
    if isinstance(tenant_dict, dict):
        tenant_dict["gemini_api_key"] = None


class CreativeSyncEnv(EgressHatchMixin, IntegrationEnv):
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

    @realize_e2e(
        e2e_unsupported(
            "generative build grading injects an in-process format mock and "
            "registry.build_creative AsyncMock; the live e2e creative-agent "
            "catalog cannot be stubbed mid-suite, and Then steps assert the mock (#1964)"
        )
    )
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
        - gemini_api_key on the account-scoped tenant dict (and config mock
          for unrelated surfaces)
        - run_async to return the generative format list

        In-process only (a2a/mcp/rest). Live e2e_rest cannot stub the
        creative-agent catalog or observe ``registry.build_creative`` —
        declare E2EUnsupportedSetup so the BDD hook non-strict-xfails e2e
        (#1964; partial adoption — ``set_run_async_result`` and direct
        registry pokes remain unwrapped).

        Returns a format_id dict for use in creative payloads::

            fmt = env.setup_generative_build(format_id="gen_banner")
            creative = {"creative_id": "c1", "name": "Test", "format_id": fmt, ...}
        """
        from adcp.types import FormatId as LibraryFormatId

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

        # Account-scoped key is the sole advisory source; keep config mock
        # aligned for unrelated surfaces. Prefer ``set_gemini_keys`` when the
        # surfaces must diverge.
        self.set_gemini_keys(tenant=gemini_api_key, global_key=gemini_api_key)

        return {"agent_url": agent, "id": format_id}

    def set_gemini_keys(
        self,
        *,
        tenant: str | None = None,
        global_key: str | None = None,
    ) -> None:
        """Set tenant and process-global GEMINI keys independently.

        Production creative-sync advisories read only
        ``tenant["gemini_api_key"]`` (account-scoped). ``call_via`` builds a
        per-protocol identity via ``identity_for`` *after* setup, and MCP/REST
        with a real auth token re-load the tenant from the DB — so this setter
        MUST (1) stash the key in ``_tenant_overrides`` and clear the identity
        cache, (2) update any already-cached tenant dicts, and (3) persist the
        DB column when a session is bound. The config mock remains writable so
        regression arms can still set a process-global key that must *not*
        rescue a keyless tenant.
        """
        self.mock["config"].return_value.gemini_api_key = global_key
        # Future identity_for builds (all protocols) pick this up via make_tenant.
        self._tenant_overrides["gemini_api_key"] = tenant
        for ident in self._identity_cache.values():
            tenant_dict = getattr(ident, "tenant", None)
            if isinstance(tenant_dict, dict):
                tenant_dict["gemini_api_key"] = tenant
        # Drop cached identities so the next call_via rebuilds with overrides.
        self._identity_cache.clear()
        if self.use_real_db and self._session is not None:
            from sqlalchemy import select

            from src.core.database.models import Tenant

            row = self._session.scalars(select(Tenant).filter_by(tenant_id=self._tenant_id)).first()
            if row is not None:
                row.gemini_api_key = tenant
                self._commit_factory_data()

    @realize_e2e(_clear_gemini_api_key_e2e)
    def clear_gemini_api_key(self) -> None:
        """Clear account-scoped GEMINI key (tenant dict + config mock).

        E2E: null the shared-DB tenant column so the live server sees a keyless
        seller even when the process still has ``GEMINI_API_KEY`` injected.
        """
        self.set_gemini_keys(tenant=None, global_key=None)

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

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch sync_creatives through the REAL A2A ``on_message_send`` pipeline.

        This used to call ``sync_creatives_raw`` directly, routing AROUND
        ``on_message_send``. The consequence (per tests/CLAUDE.md's own table:
        A2A ``wire_response`` is populated ONLY when the env routes through
        ``_run_a2a_handler``) was that the A2A leg produced no wire at all — so
        every storyboard Then on this transport had nothing transport-observable
        to assert and fell back to reading an in-memory object. Delegating to the
        base ``_run_a2a_handler`` (message parse → skill routing →
        ``_handle_sync_creatives_skill`` → ``_serialize_for_a2a`` →
        Task/Artifact DataPart) makes the a2a seat grade the real handler,
        per-item failures included, instead of standing in for it.

        Outbound kwargs are JSON-normalized through :meth:`_wire_value` — the
        SAME normalizer the REST leg's ``build_rest_body`` uses, not a second
        hand-rolled one — because they now travel via
        ``create_a2a_message_with_skill`` -> ``_dict_to_value`` (protobuf),
        which cannot carry the Pydantic models the raw wrapper accepted as live
        Python objects (account, push_notification_config) and would otherwise
        turn them into repr strings instead of a document a buyer could send.
        Normalization is applied per-kwarg rather than by calling
        ``build_rest_body``, because that helper SELECTS the REST body's keys:
        routing through it would silently drop the three kwargs
        ``_run_a2a_handler`` consumes itself (see ``_A2A_RESERVED_KWARGS``),
        which must reach it untouched.
        """
        kwargs.setdefault("creatives", [])
        return self._run_a2a_handler(
            "sync_creatives",
            SyncCreativesResponse,
            **{k: v if k in _A2A_RESERVED_KWARGS else self._wire_value(v) for k, v in kwargs.items()},
        )

    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        """Call sync_creatives via Client(mcp) — full pipeline dispatch.

        No enum coercion needed — FastMCP's TypeAdapter handles it automatically.
        """
        kwargs.setdefault("creatives", [])
        return self._run_mcp_client("sync_creatives", SyncCreativesResponse, **kwargs)

    @classmethod
    def _wire_value(cls, value: Any) -> Any:
        """Convert a Pydantic model (or a list of them) to its wire dict form.

        Anything else passes through unchanged. Conversion only: no key
        filtering, no ``exclude_none`` — the REST seat's existing semantics.
        """
        if isinstance(value, list):
            return [cls._wire_value(item) for item in value]
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return value

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to SyncCreativesBody shape for REST POST."""
        # The REST body expects 'creatives' as list[dict], matching SyncCreativesBody.
        # 'push_notification_config' and 'account' are declared by SyncCreativesBody and
        # forwarded by the route to sync_creatives_raw — dropping either here would make
        # any REST test of that behavior silently vacuous.
        #
        # 'idempotency_key' is schema-REQUIRED on sync_creatives (pinned_request_schema_fields
        # reports it in the required set). It is carried by the acceptance seam rather than
        # declared on SyncCreativesBody, so it must ride the REST body for the REST leg to
        # grade idempotency at all — omitting it here would make every REST idempotency
        # assertion vacuous.
        body: dict[str, Any] = {}
        if "creatives" in kwargs:
            body["creatives"] = self._wire_value(kwargs["creatives"])
        for key in ("assignments", "creative_ids", "push_notification_config", "account", "idempotency_key"):
            if kwargs.get(key) is not None:
                body[key] = self._wire_value(kwargs[key])
        for key in ("delete_missing", "dry_run", "validation_mode"):
            if key in kwargs:
                body[key] = self._wire_value(kwargs[key])
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> SyncCreativesResponse:
        """Parse REST JSON into SyncCreativesResponse."""
        return SyncCreativesResponse(**data)


class RealRegistryCreativeSyncEnv(CreativeSyncEnv):
    """``CreativeSyncEnv`` with the creative-agent registry left UNPATCHED.

    ``CreativeSyncEnv`` mocks ``get_creative_agent_registry`` so ordinary sync
    tests never reach the network. This variant drops exactly that one patch and
    changes nothing else, so a buyer-supplied ``format_id.agent_url`` travels the
    real registry and is judged by the real egress seam — which is the point: a
    refusal produced by a mock proves nothing about production.

    Hermetic for the causes worth grading. A refused destination (cloud metadata,
    a reserved literal, a non-https scheme) is refused BEFORE a connection is
    opened, so no packet leaves and no DNS answer is needed. A scenario that
    wants a SUCCESSFUL fetch wants plain ``CreativeSyncEnv`` — here the reference
    agent would be dialled for real.

    TRAP inherited from the same pattern in ``RealResolverProductEnv``:
    ``self.mock["registry"]`` does not exist after ``__enter__``, so any helper
    that programs the registry mock (``setup_generative_build``,
    ``set_run_async_result``) will ``KeyError`` on this env.
    """

    EXTERNAL_PATCHES = {name: target for name, target in CreativeSyncEnv.EXTERNAL_PATCHES.items() if name != "registry"}

    def _configure_mocks(self) -> None:
        """Configure everything except the registry, which is real here."""
        self.mock["run_async"].side_effect = lambda coro: []
        self.mock["send_notifications"].return_value = None
        self.mock["audit_log"].return_value = None
        mock_config = MagicMock()
        mock_config.gemini_api_key = None
        self.mock["config"].return_value = mock_config
