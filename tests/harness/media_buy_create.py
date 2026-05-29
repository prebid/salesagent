"""MediaBuyCreateEnv — integration test environment for _create_media_buy_impl.

Patches: adapter, audit logger, slack notifier, context manager.
Real: get_db_session, MediaBuyRepository, all validation (all hit real DB).

Requires: integration_db fixture.

beads: salesagent-4n0
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import CreateMediaBuyRequest
from src.core.schemas._base import CreateMediaBuyError, CreateMediaBuyResult, CreateMediaBuySuccess
from tests.harness._base import IntegrationEnv


def _restore_creative_ids(req: CreateMediaBuyRequest, flat: dict[str, Any]) -> None:
    """Re-inject creative_ids stripped by model_dump(exclude=True).

    PackageRequest.creative_ids is an internal field with exclude=True,
    so model_dump drops it. Transport wrappers (A2A, MCP, REST) need it
    in the flat dict so the re-parsed request preserves creative assignments.
    """
    if not req.packages:
        return
    flat_pkgs = flat.get("packages")
    if not flat_pkgs:
        return
    for i, pkg in enumerate(req.packages):
        cids = getattr(pkg, "creative_ids", None)
        if cids and i < len(flat_pkgs):
            flat_pkgs[i]["creative_ids"] = cids


class MediaBuyCreateEnv(IntegrationEnv):
    """Integration test environment for _create_media_buy_impl.

    Mocks external services (adapter, audit, slack, context manager).
    Everything else is real: DB, repositories, validation, schema processing.
    """

    EXTERNAL_PATCHES = {
        "adapter": "src.core.tools.media_buy_create.get_adapter",
        "audit": "src.core.tools.media_buy_create.get_audit_logger",
        "slack": "src.core.tools.media_buy_create.get_slack_notifier",
        "context_mgr": "src.core.tools.media_buy_create.get_context_manager",
        "setup_check": "src.core.tools.media_buy_create.validate_setup_complete",
        "format_spec": "src.core.tools.media_buy_create._get_format_spec_sync",
    }
    REST_ENDPOINT = "/api/v1/media-buys"

    def __init__(self, **kwargs: Any) -> None:
        # Unique, hyphen-safe tenant/principal IDs per instance: avoids
        # cross-test collisions under xdist, and keeps the derived
        # subdomain ("pub-<tenant_id>") a valid publisher domain — an
        # underscore in the id (e.g. the "test_tenant" default) fails the
        # AdCP publisher_domain pattern when products resolve property_tags.
        suffix = uuid.uuid4().hex[:10]
        kwargs.setdefault("tenant_id", f"mbcreate{suffix}")
        kwargs.setdefault("principal_id", f"agent{suffix}")
        super().__init__(**kwargs)

    def setup_media_buy_data(self) -> tuple:
        """Create the full dependency chain needed for create_media_buy.

        Creates: tenant (with auto CurrencyLimit USD), principal,
        PropertyTag ("all_inventory"), Product with PricingOption.

        Returns (tenant, principal, product, pricing_option).
        """
        tenant, principal = self.setup_default_data()
        product, pricing_option = self.setup_product_chain(tenant)
        return tenant, principal, product, pricing_option

    def setup_product_chain(self, tenant: Any) -> tuple[Any, Any]:
        """Create the Product + PricingOption + PropertyTag chain for a tenant.

        ``create_media_buy`` resolves each package's ``product_id`` against a DB
        Product (with a PricingOption matching ``pricing_option_id``), and the
        Product references the ``all_inventory`` PropertyTag. This builds that
        chain via factories so a full (non-dry-run) create can reach the adapter.

        Returns (product, pricing_option) ORM instances. The default
        PricingOption is CPM/USD/fixed, whose generated id is ``cpm_usd_fixed``.
        """
        from tests.factories.core import PropertyTagFactory
        from tests.factories.product import PricingOptionFactory, ProductFactory

        # Product.property_tags references "all_inventory" by factory default.
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
        product = ProductFactory(tenant=tenant)
        pricing_option = PricingOptionFactory(product=product)
        return product, pricing_option

    def _build_mock_context_manager(self, tool_name: str) -> MagicMock:
        """Return a mock context manager covering the create + update call surface.

        ``_create_media_buy_impl`` (and the update impl) call the context
        manager to create/fetch a Context and a WorkflowStep. This mock returns
        objects exposing ``.context_id`` / ``.step_id`` but creates NO DB rows.

        Limitation: this env therefore supports only the failed / replay paths,
        which return before a WorkflowStep is FK-linked to a media buy (the
        rejection-replay tests). A *successful* create reaches
        ``ObjectWorkflowMapping(step_id=...)`` and would FK-violate against the
        mock ``step_id``; a future success-create test through this env must
        replace this mock with real Context + WorkflowStep rows.
        """
        ctx_obj = MagicMock()
        ctx_obj.context_id = f"ctx_{uuid.uuid4().hex[:8]}"

        step_obj = MagicMock()
        step_obj.step_id = f"step_{uuid.uuid4().hex[:8]}"

        ctx_mgr = MagicMock()
        ctx_mgr.create_context.return_value = ctx_obj
        ctx_mgr.get_context.return_value = ctx_obj
        ctx_mgr.get_or_create_context.return_value = ctx_obj
        ctx_mgr.create_workflow_step.return_value = step_obj
        ctx_mgr.update_workflow_step.return_value = None
        ctx_mgr.add_message.return_value = None
        return ctx_mgr

    def seed_rejection(self, idempotency_key: str, message: str) -> None:
        """Persist a cached create_media_buy rejection for this env's principal.

        Writes a real ``IdempotencyAttempt`` row via a real ``MediaBuyUoW`` so
        the production replay lookup (``find_by_key``) serves it on the next
        call carrying the same ``idempotency_key``. The cached envelope is the
        ``model_dump`` of a ``CreateMediaBuyError`` — exactly what production
        caches at the adapter-rejection path.
        """
        from src.core.database.repositories import MediaBuyUoW
        from src.core.schemas import Error

        self._commit_factory_data()
        rejection = CreateMediaBuyError(
            errors=[Error(code="VALIDATION_ERROR", message=message, details=None)],
            context=None,
        )
        with MediaBuyUoW(self._tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            uow.idempotency_attempts.record_rejection(
                principal_id=self._principal_id,
                tool_name="create_media_buy",
                idempotency_key=idempotency_key,
                response_envelope=rejection.model_dump(mode="json"),
            )

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for external mocks."""
        # Adapter: mock create_media_buy — returns response matching the request packages.
        # The side_effect dynamically generates package_ids from the request.
        mock_adapter = MagicMock()

        def _adapter_create_response(*args: Any, **kwargs: Any) -> Any:
            """Generate adapter response with package_ids matching request packages."""
            from src.core.schemas._base import CreateMediaBuySuccess

            # Determine package count from request
            req_obj = kwargs.get("request") or (args[0] if args else None)
            pkg_count = 0
            if req_obj and hasattr(req_obj, "packages") and req_obj.packages:
                pkg_count = len(req_obj.packages)
            # Also check the 'packages' kwarg (MediaPackage list)
            pkgs_arg = kwargs.get("packages")
            if pkgs_arg:
                pkg_count = max(pkg_count, len(pkgs_arg))
            if pkg_count == 0:
                pkg_count = 1

            media_buy_id = f"mb_{uuid.uuid4().hex[:8]}"
            return CreateMediaBuySuccess(
                media_buy_id=media_buy_id,
                packages=[
                    {
                        "package_id": f"pkg_{uuid.uuid4().hex[:8]}",
                        "product_id": f"prod_{i}",
                        "budget": 5000.0,
                        "status": "active",
                    }
                    for i in range(pkg_count)
                ],
            )

        mock_adapter.create_media_buy.side_effect = _adapter_create_response
        # Save original side_effect so Given steps can restore it after error injection
        mock_adapter._original_create_side_effect = _adapter_create_response
        mock_adapter.validate_media_buy_request.return_value = None
        mock_adapter.add_creative_assets.return_value = None
        mock_adapter.associate_creatives.return_value = None
        mock_adapter.manual_approval_required = False
        mock_adapter.manual_approval_operations = []
        self.mock["adapter"].return_value = mock_adapter

        # Audit logger: no-op
        mock_audit = MagicMock()
        mock_audit.log_operation.return_value = None
        mock_audit.log_security_violation.return_value = None
        self.mock["audit"].return_value = mock_audit

        # Slack notifier: no-op
        mock_slack = MagicMock()
        mock_slack.notify_media_buy_event.return_value = None
        self.mock["slack"].return_value = mock_slack

        # Context manager: mock returning objects with .context_id / .step_id.
        # The replay and adapter-rejection paths return before a WorkflowStep is
        # linked to a media buy, so no real ObjectWorkflowMapping FK row is needed.
        self.mock["context_mgr"].return_value = self._build_mock_context_manager(tool_name="create_media_buy")

        # Setup checklist: pass by default
        self.mock["setup_check"].return_value = None

        # Format spec: mock _get_format_spec_sync to avoid asyncio.run() inside
        # running event loop. Returns a valid format keyed by format_id. Tests
        # for format mismatch (ext-p) override via mock["format_spec"].side_effect.
        from tests.helpers.adcp_factories import create_test_format

        self._format_specs: dict[str, Any] = {
            "display_300x250": create_test_format(
                format_id="display_300x250",
                name="Display 300x250",
                type="display",
            ),
        }

        def _format_spec_side_effect(agent_url: str, format_id: str) -> Any:
            return self._format_specs.get(format_id)

        self.mock["format_spec"].side_effect = _format_spec_side_effect

    def call_impl(self, **kwargs: Any) -> CreateMediaBuyResult:
        """Call _create_media_buy_impl with real DB."""
        from src.core.tools.media_buy_create import _create_media_buy_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)

        # Build request from kwargs if not provided directly
        req = kwargs.pop("req", None)
        if req is None:
            req = CreateMediaBuyRequest(**kwargs)

        return asyncio.run(_create_media_buy_impl(req=req, identity=identity))

    def _flatten_request(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Convert a ``req=`` kwarg into the flat parameter dict the wrappers take.

        MCP/A2A wrappers accept individual create_media_buy parameters, not a
        request model. Drops fields the wrappers don't declare and re-injects
        ``creative_ids`` (stripped by ``exclude=True`` on model_dump).
        """
        req = kwargs.pop("req", None)
        if req is None:
            return kwargs
        flat = req.model_dump(mode="json", exclude_none=True)
        for key in ("account", "proposal_id", "total_budget"):
            flat.pop(key, None)
        _restore_creative_ids(req, flat)
        flat.update(kwargs)
        return flat

    def call_a2a(self, **kwargs: Any) -> CreateMediaBuyResult:
        """Dispatch create_media_buy through the real A2A ``on_message_send`` pipeline.

        Delegates to the base ``_run_a2a_handler`` (drives ``on_message_send`` →
        skill routing → ``_serialize_for_a2a`` → Task/Artifact DataPart, strips
        the A2A-envelope protocol fields, unwraps A2AError), reconstructing the
        ``CreateMediaBuyResult`` via ``parse_rest_response`` — the
        success|error union needs the ``media_buy_id`` discriminator plus the
        top-level ``status``, which a plain Pydantic class can't recover.
        """
        return self._run_a2a_handler(
            "create_media_buy", lambda **data: self.parse_rest_response(data), **self._flatten_request(kwargs)
        )

    def call_mcp(self, **kwargs: Any) -> CreateMediaBuyResult:
        """Dispatch create_media_buy through the real FastMCP ``Client`` pipeline.

        Delegates to the base ``_run_mcp_client`` (in-memory FastMCP transport →
        middleware → TypeAdapter → MCP wrapper → ``_impl``, with the real
        token→DB→identity auth chain and its patch-called guard), reconstructing
        the ``CreateMediaBuyResult`` from the flattened ``structured_content`` via
        ``parse_rest_response`` for the success|error union discrimination.
        """
        return self._run_mcp_client(
            "create_media_buy", lambda **data: self.parse_rest_response(data), **self._flatten_request(kwargs)
        )

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Build REST request body from kwargs."""
        kwargs.pop("identity", None)
        req = kwargs.pop("req", None)
        if req is not None:
            body = req.model_dump(mode="json", exclude_none=True)
            # Preserve creative_ids — exclude=True strips them from model_dump
            _restore_creative_ids(req, body)
            return body
        return kwargs

    def parse_rest_response(self, data: dict[str, Any]) -> CreateMediaBuyResult:
        """Parse a flattened create_media_buy wire body back into a CreateMediaBuyResult.

        ``CreateMediaBuyResult`` serializes flat: the response fields plus a
        top-level protocol ``status``. The CreateMediaBuySuccess|CreateMediaBuyError
        union discriminates on ``media_buy_id`` (present only on success) — not
        on ``errors``, since a *successful* buy may also carry non-fatal advisory
        ``errors``. A replayed rejection has ``errors`` and no ``media_buy_id``,
        so it reconstructs as a CreateMediaBuyError.
        """
        status = data.pop("status", "completed")
        if data.get("media_buy_id") is not None:
            response: CreateMediaBuySuccess | CreateMediaBuyError = CreateMediaBuySuccess(**data)
        else:
            response = CreateMediaBuyError(**data)
        return CreateMediaBuyResult(response=response, status=status)
