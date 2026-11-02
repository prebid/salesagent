"""MediaBuyCreateEnv — integration test environment for _create_media_buy_impl.

Patches: adapter, audit logger, slack notifier, context manager.
Real: get_db_session, MediaBuyRepository, all validation (all hit real DB).

Requires: integration_db fixture.

"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, cast
from unittest.mock import MagicMock

from src.adapters.base import AdapterCreateResult, ResponsePackage
from src.core.schemas import CreateMediaBuyRequest
from src.core.schemas._base import CreateMediaBuyResult
from tests.factories.mint import mint
from tests.harness._base import IntegrationEnv, json_safe
from tests.harness.egress import EgressHatchMixin
from tests.harness.transport import DeliverResult

# Sentinel for missing-key tests: pass idempotency_key=OMIT_IDEMPOTENCY_KEY to send a
# request with NO key (the schema rejects it as "Field required" — AdCP 3.0.1).
# Deliberately NOT tests.harness.transport.NO_IDENTITY_OVERRIDE — a different
# sentinel for a different field (idempotency_key, not identity); the "one
# sentinel" consolidation is scoped to the
# identity-argument omission disease, not every object()-as-sentinel use in
# tests/harness/.
OMIT_IDEMPOTENCY_KEY: Any = object()

# The sibling sentinel for ``account``, which create-media-buy-request.json also lists in
# /required. A scenario that means to send NO account cannot signal that by omitting the
# kwarg -- omission is what every scenario that simply does not care about accounts also
# looks like, and those get the seeded default. Same shape, same reason, as the key above.
OMIT_ACCOUNT: Any = object()


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


class MediaBuyCreateEnv(EgressHatchMixin, IntegrationEnv):
    """Integration test environment for _create_media_buy_impl.

    Mocks external services (adapter, audit, slack, context manager).
    Everything else is real: DB, repositories, validation, schema processing —
    including the egress seam's ingest verdict on webhook URLs, which is why
    the env carries ``set_egress_hatches`` (the @egress ingest-twin scenarios
    pin the hatch posture the refusal is graded under).
    """

    EXTERNAL_PATCHES = {
        "adapter": "src.core.tools.media_buy_create.get_adapter",
        "audit": "src.core.tools.media_buy_create.get_audit_logger",
        "slack": "src.core.tools.media_buy_create.get_slack_notifier",
        "context_mgr": "src.core.tools.media_buy_create.get_context_manager",
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
        kwargs.setdefault("tenant_id", mint(f"mbcreate{suffix}"))
        kwargs.setdefault("principal_id", mint(f"agent{suffix}"))
        super().__init__(**kwargs)

    def setup_media_buy_data(self) -> tuple:
        """Create the full dependency chain needed for create_media_buy.

        Creates: tenant (with auto CurrencyLimit USD), principal,
        PropertyTag ("all_inventory"), Product with PricingOption, and one
        verified AuthorizedProperty.

        Returns (tenant, principal, product, pricing_option).
        """

        # Seed the tenant as auto-approve (human_review_required=False). The
        # in-process transports never hit the tenant approval gate because this
        # env's mocked adapter sets manual_approval_operations=[] — but the live
        # e2e_rest server has no patches and reads the REAL tenant row, where
        # the column defaults to True, routing every create to the submitted
        # (manual-approval) envelope instead of completing it. Seeding False
        # makes the live server's gate state match what every other transport
        # already grades (#1418 class; #1417/#1537 scenarios re-triaged after
        # the adcp-6.6 merge). Scenarios that grade the approval path flip it
        # explicitly via the "tenant requires manual approval" Given (which
        # commits the change to the shared DB).
        tenant, principal = self.setup_default_data(human_review_required=False)
        # And the ACCOUNT, with this principal's access to it. Both
        # create-media-buy-request.json and update-media-buy-request.json list ``account``
        # in /required, and the boundary RESOLVES the reference now rather than accepting
        # and dropping it -- so a scenario using this chain and sending the default account
        # got PERMISSION_DENIED out of resolve_account before reaching what it grades.
        # Here and not in setup_default_data: seeding an account for EVERY tenant made
        # UC-011's account-listing scenarios wrong ("0 accounts visible" saw one).
        self.setup_default_account()
        product, pricing_option = self.setup_product_chain(tenant)
        return tenant, principal, product, pricing_option

    def setup_default_data(self, **tenant_kwargs: Any) -> tuple[Any, Any]:
        """The base seed, plus the rows the create_media_buy setup-checklist gate grades.

        Here rather than in ``setup_media_buy_data``, because that is not the path the callers
        share: BDD reaches this env through it, the integration suite calls
        ``setup_default_data`` and ``setup_product_chain`` directly. Here rather than in the
        base, because ``validate_setup_complete`` has one production caller -- seeding an
        AuthorizedProperty for every tenant would break UC-013's property counts the way
        seeding an account for every tenant broke UC-011's.

        Everything is CREATE-ONLY. ``call_impl`` re-enters this method (through
        ``setup_default_account``), so an assignment on every call silently reverts whatever a
        test set up: flipping ``auth_setup_mode`` to make a tenant incomplete was undone
        between the flip and the dispatch. The factories are no help either -- neither checks
        for an existing row, and ``tenant_auth_configs`` is UNIQUE on ``tenant_id``.
        """
        from sqlalchemy import select

        from src.core.database.models import AuthorizedProperty, Tenant, TenantAuthConfig
        from tests.factories import AuthorizedPropertyFactory, TenantAuthConfigFactory

        creating = self._session.scalars(select(Tenant).filter_by(tenant_id=self._tenant_id)).first() is None
        tenant, principal = super().setup_default_data(**tenant_kwargs)

        if self._session.scalars(select(AuthorizedProperty).filter_by(tenant_id=tenant.tenant_id)).first() is None:
            AuthorizedPropertyFactory(tenant=tenant)
        if self._session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant.tenant_id)).first() is None:
            TenantAuthConfigFactory(tenant=tenant, oidc_enabled=True)
        if creating:
            # The column's server_default is "true", which leaves sso_configuration incomplete.
            tenant.auth_setup_mode = False

        self._commit_factory_data()
        return tenant, principal

    def setup_product_chain(
        self,
        tenant: Any,
        *,
        product_id: str = "prod_1",
        currency: str = "USD",
        with_pricing: bool = True,
        format_ids: list[dict[str, str]] | None = None,
    ) -> tuple:
        """Seed a real PropertyTag ("all_inventory") + Product + PricingOption row set.

        The "all_inventory" tag is created once per env (idempotent across repeated
        calls). Returns ``(product, pricing_option)``; ``pricing_option`` is ``None``
        when ``with_pricing=False``.
        """
        from tests.factories import PricingOptionFactory, ProductFactory
        from tests.factories.core import PropertyTagFactory

        if not getattr(self, "_seeded_all_inventory_tag", False):
            PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
            self._seeded_all_inventory_tag = True

        if format_ids is None:
            format_ids = [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]

        product = ProductFactory(
            tenant=tenant,
            product_id=product_id,
            delivery_type="non_guaranteed",
            format_ids=format_ids,
            property_tags=["all_inventory"],
        )
        pricing_option = None
        if with_pricing:
            pricing_option = PricingOptionFactory(
                product=product, pricing_model="cpm", currency=currency, is_fixed=True
            )
        return product, pricing_option

    def _build_mock_context_manager(self, tool_name: str) -> MagicMock:
        """Mock context manager that delegates create_context / create_workflow_step /
        link_workflow_to_object to the REAL one.

        Persisting real Context / WorkflowStep / ObjectWorkflowMapping rows lets both
        the manual-approval path and the auto-approve path satisfy the FK constraints
        that _send_push_notifications relies on to deliver webhooks.
        """
        from src.core.context_manager import get_context_manager

        real = get_context_manager()
        mgr = MagicMock()

        def _create_context(*_args: Any, **kwargs: Any):
            return real.create_context(
                tenant_id=kwargs.get("tenant_id", self._tenant_id),
                principal_id=kwargs.get("principal_id", self._principal_id),
            )

        def _get_or_create_context(*_args: Any, **kwargs: Any):
            # The update path (media_buy_update.py:263) resolves its context via
            # get_or_create_context, not create_context. Delegate to the real one so
            # persistent_ctx.context_id is a real value the workflow_step INSERT can
            # persist (a MagicMock context_id fails psycopg2 with "can't adapt").
            return real.get_or_create_context(
                tenant_id=kwargs.get("tenant_id", self._tenant_id),
                principal_id=kwargs.get("principal_id", self._principal_id),
                context_id=kwargs.get("context_id"),
                is_async=kwargs.get("is_async", True),
            )

        def _create_workflow_step(*_args: Any, **kwargs: Any):
            kwargs.setdefault("step_type", "media_buy_creation")
            kwargs.setdefault("owner", "system")
            kwargs.setdefault("tool_name", tool_name)
            return real.create_workflow_step(**kwargs)

        def _link_workflow_to_object(*_args: Any, **kwargs: Any):
            return real.link_workflow_to_object(**kwargs)

        mgr.create_context.side_effect = _create_context
        mgr.get_context.return_value = None
        mgr.get_or_create_context.side_effect = _get_or_create_context
        mgr.create_workflow_step.side_effect = _create_workflow_step
        mgr.link_workflow_to_object.side_effect = _link_workflow_to_object
        mgr.update_workflow_step.return_value = None
        mgr.add_message.return_value = None
        return mgr

    def seed_success(
        self,
        idempotency_key: str,
        *,
        payload_hash: str,
        media_buy_id: str = "mb_seeded",
    ) -> None:
        """Persist a cached create_media_buy SUCCESS for this env's principal.

        Writes a real ``IdempotencyAttempt`` row via a real ``MediaBuyUoW`` so the
        production replay lookup (``find_by_key``) serves it VERBATIM on the next
        call carrying the same ``idempotency_key``. ``payload_hash`` must be the
        canonical hash of the request the test will retry (compute it with
        ``canonical_request_hash``) for a replay; pass a non-matching hash to
        exercise the ``IDEMPOTENCY_CONFLICT`` path. The stored envelope is the
        structured ``{status, response}`` shape production caches — errors are
        never cached.
        """
        from tests.helpers import make_active_cached_success, seed_cached_success

        self._commit_factory_data()
        seed_cached_success(
            self._tenant_id,
            self._principal_id,
            idempotency_key,
            response_model=make_active_cached_success(media_buy_id),
            payload_hash=payload_hash,
        )

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for external mocks."""
        # Adapter: mock create_media_buy — the side_effect returns one response
        # package per package the tool handed it, echoing each package_id.
        mock_adapter = MagicMock()

        def _adapter_create_response(*args: Any, **kwargs: Any) -> AdapterCreateResult:
            """Stand in for an ad server's ``create_media_buy`` return.

            The carrier type is the adapter contract, not a wire model: an adapter
            has no row to read ``confirmed_at`` / ``revision`` from, and
            ``AdapterCreateResult`` simply does not declare them, so the fake cannot
            speak for fields it is not entitled to. It carries exactly what the tool
            reads off an adapter — ``media_buy_id``, and each package's
            ``package_id`` and ``paused``.

            The seller has already minted a ``package_id`` per requested package by
            the time the adapter is called, and every real adapter echoes it back
            through ``AdServerAdapter._build_package_responses``. So this echoes it
            too, one response package per requested package, which is what keeps
            the tool's positional ``req.packages[i] -> response.packages[i]`` walk
            lined up.
            """
            # The tool calls adapter.create_media_buy(request, packages, ...)
            # positionally, so the MediaPackage list arrives as args[1].
            media_packages = kwargs.get("packages") or (args[1] if len(args) > 1 else None) or []

            return AdapterCreateResult(
                media_buy_id=mint(f"mb_{uuid.uuid4().hex[:8]}"),
                packages=[ResponsePackage(package_id=pkg.package_id, paused=False) for pkg in media_packages],
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

        def _format_spec_side_effect(agent_url: str, format_id: str, *, provenance: Any = None) -> Any:
            spec = self._format_specs.get(format_id)
            if spec is not None:
                return spec
            # Fall back to the 54-format reference catalog — the SAME fixture the
            # live server resolves under ADCP_TESTING (format_cache), so
            # in-process and e2e format resolution agree by construction.
            # Synthetic ids stay unresolvable (None), matching the server.
            from src.core.format_cache import load_reference_formats

            for fmt in load_reference_formats():
                if fmt.format_id.id == format_id:
                    return fmt
            return None

        self.mock["format_spec"].side_effect = _format_spec_side_effect

    def _ensure_required_request_fields(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Default the two fields create-media-buy-request.json lists in /required.

        ``idempotency_key`` gets a fresh spec-shaped key per call — unique because a reused
        key would replay the original response (or raise IDEMPOTENCY_CONFLICT) instead of
        creating a new buy. Pass ``OMIT_IDEMPOTENCY_KEY`` to send none.

        ``account`` gets the tenant's SEEDED account, not a literal: the create wrappers
        resolve the reference at the transport boundary, so a fabricated id would come back
        as ACCOUNT_NOT_FOUND and every scenario that is not about accounts would fail on
        account resolution instead of reaching what it grades. A scenario that IS about
        accounts sets its own ``account``, and one that means to send none passes
        ``OMIT_ACCOUNT``. Was a free ``_ensure_idempotency_key`` function; it needs ``self``
        now because seeding the account needs the env's session.
        """
        if kwargs.get("idempotency_key") is OMIT_IDEMPOTENCY_KEY:
            kwargs.pop("idempotency_key")
        else:
            kwargs.setdefault("idempotency_key", mint(f"test-key-{uuid.uuid4().hex}"))

        if kwargs.get("account") is OMIT_ACCOUNT:
            kwargs.pop("account")
        elif kwargs.get("account") is None:
            kwargs["account"] = {"account_id": self._default_account_id()}
        else:
            # An account was NAMED -- by a step, or by create_test_media_buy_request_dict,
            # which writes the literal DEFAULT_TEST_ACCOUNT_ID. Present is not the same as
            # resolvable: the transport boundary looks the reference up, so the row still
            # has to exist or the dispatch answers ACCOUNT_NOT_FOUND instead of whatever the
            # test is about. Seeding only the suite's own default is what keeps a test that
            # names a MISSING account still missing it.
            self._seed_named_account_ref(kwargs["account"])
        return kwargs

    def _default_account_id(self) -> str:
        """The seeded account's id, or a literal when there is no DB bound.

        A contract test building a body outside ``with env:`` has nothing to seed against
        and nothing that will resolve the reference, so a literal gives the body its
        required SHAPE, which is all such a caller is asking for.
        """
        if self._session is None:
            return "acct_unbound"
        return self.setup_default_account().account_id

    def call_impl(self, **kwargs: Any) -> CreateMediaBuyResult:
        """Dispatch create_media_buy at the shared boundary, with a real DB.

        IMPL means "the production path minus the wire", and for this tool that path starts
        at ``invoke_tool``: nothing calls ``_create_media_buy_impl`` in process, so its only
        real callers are transports and they all enter here. Account resolution and the
        idempotency probe therefore run, exactly as they do for a buyer.

        Contrast ``CreativeSyncEnv.call_impl``, which stays a direct implementation call
        because production genuinely has an in-process caller there -- this tool's own inline
        creative upload.
        """
        from src.core.resolved_identity import TransportProtocol
        from src.core.tools._boundary import invoke_tool

        self._commit_factory_data()
        credential = kwargs.pop("credential", self.credential())

        # Build request from kwargs if not provided directly
        req = kwargs.pop("req", None)
        if req is None:
            req = CreateMediaBuyRequest(**self._ensure_required_request_fields(kwargs))
        else:
            self._seed_named_account(req)

        return asyncio.run(invoke_tool("create_media_buy", req, credential, TransportProtocol.MCP))

    def _flatten_request(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Convert a ``req=`` kwarg into the flat parameter dict the wrappers take.

        MCP/A2A wrappers accept individual create_media_buy parameters, not a
        request model. Drops fields the wrappers don't declare and re-injects
        ``creative_ids`` (stripped by ``exclude=True`` on model_dump).
        """
        req = kwargs.pop("req", None)
        if req is not None:
            self._seed_named_account(req)
        if req is None:
            # NOT json_safe'd: the MCP/A2A wrappers take TYPED parameters, so a raw bag
            # goes to them as-is. Only the REST body needs JSON, and build_rest_body
            # normalizes there.
            return self._ensure_required_request_fields(kwargs)
        flat = req.model_dump(mode="json", exclude_none=True)
        # Keep ``account``: the create_media_buy wrappers declare it and resolve it
        # at the transport boundary (998ad1be2). Stripping it here regresses
        # account-not-found scenarios to a successful create.
        _restore_creative_ids(req, flat)
        flat.update(kwargs)
        return flat

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch create_media_buy through the real A2A ``on_message_send`` pipeline.

        Delegates to the base ``_run_a2a_handler`` (drives ``on_message_send`` →
        skill routing → ``serve`` → ``to_wire`` → Task/Artifact DataPart, strips
        the A2A-envelope protocol fields, unwraps A2AError), reconstructing the
        ``CreateMediaBuyResult`` via ``parse_rest_response`` — the
        success|error union needs the ``media_buy_id`` discriminator plus the
        top-level ``status``, which a plain Pydantic class can't recover.
        """
        return self._run_a2a_handler(
            "create_media_buy", lambda **data: self.parse_rest_response(data), **self._flatten_request(kwargs)
        )

    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
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
        req = kwargs.pop("req", None)
        if req is not None:
            self._seed_named_account(req)
            body = req.model_dump(mode="json", exclude_none=True)
            # Preserve creative_ids — exclude=True strips them from model_dump
            _restore_creative_ids(req, body)
            return body
        # The RAW path: normalize to JSON. A step dispatching a raw bag (so a
        # schema-invalid payload actually reaches the transport) may hand us typed objects
        # it built for setup; a REST body cannot carry them.
        return json_safe(self._ensure_required_request_fields(kwargs))

    def parse_rest_response(self, data: dict[str, Any]) -> CreateMediaBuyResult:
        """Rebuild a create_media_buy wire body as the branch the buyer received.

        ``CreateMediaBuyResult.revive`` is the same discrimination production runs when it
        reads a cached idempotency replay out of the store, so a test asserting on the
        reconstructed branch is asserting on production's own resolution rather than on a
        copy of it that can disagree.
        """
        return cast("CreateMediaBuyResult", CreateMediaBuyResult.revive(data))


class RealFormatResolverMediaBuyCreateEnv(MediaBuyCreateEnv):
    """``MediaBuyCreateEnv`` with the format-spec fetch left UNPATCHED.

    ``MediaBuyCreateEnv`` mocks ``_get_format_spec_sync`` so ordinary
    create_media_buy tests never resolve a format over the network. This variant
    drops exactly that one patch and changes nothing else, so the pre-adapter
    creative validation runs the real ``format_resolver`` → ``CreativeAgentRegistry``
    → egress-seam chain — which is the point: a refusal whose wire envelope is
    under test has to be produced by production code, including the ``field``
    the production call site chooses for it.

    TRAP: because the mock is gone, ``self.mock["format_spec"]`` does not exist
    after ``__enter__`` — the stand-in below is deleted as soon as the happy-path
    wiring has finished with it. A test that wants to INJECT a format-spec result
    or error wants plain ``MediaBuyCreateEnv``, not this class.
    """

    EXTERNAL_PATCHES = {
        name: target for name, target in MediaBuyCreateEnv.EXTERNAL_PATCHES.items() if name != "format_spec"
    }

    def _configure_mocks(self) -> None:
        # The happy-path wiring pokes ``self.mock["format_spec"]``. A throwaway
        # stand-in keeps those lines harmless without forking the rest of the
        # wiring, which this env does want.
        self.mock["format_spec"] = MagicMock()
        try:
            super()._configure_mocks()
        finally:
            del self.mock["format_spec"]
