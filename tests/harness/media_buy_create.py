"""MediaBuyCreateEnv — integration test environment for _create_media_buy_impl.

Patches: adapter, audit logger, slack notifier, context manager.
Real: get_db_session, MediaBuyRepository, all validation (all hit real DB).

Requires: integration_db fixture.

"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, cast
from unittest.mock import MagicMock

from src.adapters.base import AdapterCreateResult, ResponsePackage
from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import CreateMediaBuyRequest, Principal
from src.core.schemas._base import CreateMediaBuyResult
from tests.factories.mint import mint
from tests.harness._base import IntegrationEnv, json_safe
from tests.harness._realize import realize_e2e
from tests.harness.peer_agent import PeerCreativeAgentMixin
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


def _live_ad_server_on_the_live_stack(self: MediaBuyCreateEnv, adapter_type: str) -> None:
    """Realize ``run_ad_server`` on the live e2e stack: the row, and only the row.

    The live server runs with no patches, so the half of the in-process method that
    hands the adapter decision back to production has nothing to undo there — the
    decision was never taken away. The ``adapter_config`` row is written through the
    same factory, against the shared database the live server reads, so the tenant
    this env minted genuinely runs *adapter_type* on both sides.

    Declared rather than left to the in-process branch: without it the in-process
    ``self.mock[...]`` lookup would raise over e2e_rest (there is no mock registry),
    and claiming to have set something in a process that is not the server's is the
    silent lie :func:`realize_e2e` exists to prevent.
    """
    self._write_adapter_config(adapter_type)


#: Audit operation the Mock adapter writes, one row per creative push, as
#: ``"<adapter_name>.<operation>"`` — so the lookup below is a substring match.
_CREATIVE_PUSH_OPERATION = "add_creative_assets"

#: Audit operation ``MockAdServer`` writes once per ``create_media_buy`` it is handed.
_ADAPTER_CREATE_OPERATION = "create_media_buy"


def _adapter_operation_receipts(self: MediaBuyCreateEnv, operation: str) -> list[dict[str, Any]]:
    """The ``details`` of every audit row the server's ad server wrote for *operation*.

    THE ONE e2e READBACK, parameterised by the operation rather than copied per seam. Over
    e2e_rest the adapter runs inside the server container, so a mock read in this process
    would report nothing on every branch — which is worse than no evidence, because a
    scenario asserting something did NOT happen would pass vacuously.

    What the server leaves behind is a row. ``MockAdServer`` calls
    ``audit_logger.log_operation(...)`` in production code for this operation,
    on its own session, committed before the create response returns — so the receipt exists
    by the time a Then runs, with no polling. (Same seam shape as
    ``_e2e_slack_notified_creatives``: the server's durable trace, not the test rig's mock.)

    THE RECORD IS THE CALL, not its success: ``log_operation`` runs before the work can
    raise, exactly as an in-process ``call_args_list`` records a call whatever it returned.
    The two branches therefore mean the same thing.

    ``expire_all`` first, and ordered by ``log_id``: the server committed through its own
    session, so rows written since this session last read are otherwise served from the
    identity map, and ``find_by_operation`` imposes no order of its own.

    ONLY THE AD SERVER'S OWN ROWS ARE RECEIPTS. The tool audits itself as
    ``AdCP.create_media_buy`` and a failed call is audited again as
    ``<TRANSPORT>.create_media_buy`` (``src/core/tool_error_logging.py``), so a bare
    substring match would count that bookkeeping as an ad-server call. The adapter writes
    under its own ``adapter_name`` (``AdServerAdapter.__init__``), which is the prefix kept.
    """
    from src.core.database.repositories.audit_log import AuditLogRepository

    session = self.get_session()
    session.expire_all()
    rows = [
        row
        for row in AuditLogRepository(session, self.tenant_id).find_by_operation(operation)
        if (row.operation or "").startswith(f"{MockAdServer.adapter_name}.")
    ]
    return [row.details or {} for row in sorted(rows, key=lambda r: r.log_id)]


def _adapter_create_receipts_on_the_live_stack(self: MediaBuyCreateEnv) -> int:
    """How many media buys the SERVER's ad server was handed, read off its receipts."""
    return len(_adapter_operation_receipts(self, _ADAPTER_CREATE_OPERATION))


def _inject_create_failure_on_the_live_stack(self: MediaBuyCreateEnv, error: Exception, **behavior: Any) -> None:
    """Realize ``fail_adapter_create`` on the live stack: the ``test_behavior`` row.

    The live server runs the real ``MockAdServer``, which reads injected faults from
    ``AdapterConfig.config_json["test_behavior"]``. *error* is the in-process carrier and has
    no meaning in another process; *behavior* names the same fault in the adapter's knobs.
    """
    from tests.factories.core import set_adapter_test_behavior

    set_adapter_test_behavior(self, self.tenant_id, fail_on_create=True, manual_approval_required=False, **behavior)


def _uploaded_assets_from_the_audit_trail(self: MediaBuyCreateEnv) -> list[dict[str, Any]]:
    """The assets the SERVER's ad server was handed, read off the receipt it wrote.

    Over e2e_rest the adapter runs inside the server container, so the mock the in-process
    branch reads lives in the wrong process and would report an empty list on every branch —
    which is worse than no evidence, because a scenario asserting that something was NOT
    sent would pass vacuously.

    What the server leaves behind is a row. ``MockAdServer._add_creative_assets_immediate``
    calls ``audit_logger.log_operation(operation="add_creative_assets", ...)`` in production
    code, on its own session, committed before the create response returns — so the receipt
    exists by the time a Then runs, with no polling. That audit record carries the asset
    dicts AS HANDED, which is why both branches of this seam return the same shape and one
    assertion can read either. (Same seam shape as
    ``_e2e_slack_notified_creatives``: the server's durable trace, not the test rig's mock.)

    THE RECORD IS THE CALL, not its success. ``log_operation`` runs before the
    media-buy-exists check raises, exactly as the in-process ``call_args_list`` records a
    call whatever it returned. The two branches therefore mean the same thing.

    That audit record carries the asset dicts AS HANDED, which is why both branches of this
    seam return the same shape and one assertion can read either.

    ONLY THE IMMEDIATE AND SYNC-DELAY MODES write it. ``_add_creative_assets_async`` parks
    the request on a workflow step and audits nothing, so a scenario driving the async
    upload mode needs its own seam rather than a widened one here — which path ran is part
    of what such a scenario would be asserting.
    """
    uploaded: list[dict[str, Any]] = []
    for details in _adapter_operation_receipts(self, _CREATIVE_PUSH_OPERATION):
        uploaded.extend(details.get("assets") or [])
    return uploaded


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


class MediaBuyCreateEnv(PeerCreativeAgentMixin, IntegrationEnv):
    """Integration test environment for _create_media_buy_impl.

    Mocks external services (adapter, audit, slack, context manager).
    Everything else is real: DB, repositories, validation, schema processing —
    including the egress seam's ingest verdict on webhook URLs, which is why
    the env carries ``set_egress_hatches`` (the @egress ingest-twin scenarios
    pin the hatch posture the refusal is graded under), and the dial to a
    tenant-configured creative agent when a package's format_id names one, which
    is why it carries ``serve_peer_catalog``.
    """

    EXTERNAL_PATCHES = {
        "adapter": "src.core.tools.media_buy_create.get_adapter",
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
        # The buy-level deadline the fake ad server reports on every order it creates.
        # None is what a real ad server that sets no deadline answers; see
        # ``report_creative_deadline``.
        self._reported_creative_deadline: datetime | None = None

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
            # An id the PINNED CAPTURE actually serves. A creative referenced from a package
            # must satisfy two conditions at once -- its claimed id resolves against the
            # capture, AND its claimed identity equals this arm, which ``_legacy_lane``
            # compares whole -- and with the arm pinned to the synthetic ``display_300x250``
            # those were unsatisfiable together: the only id equal to the arm was the one the
            # agent does not serve. Measured against
            # tests/fixtures/creative_formats/reference_formats.json:
            # ``display_300x250`` is ABSENT, ``display_300x250_image`` is PRESENT. This is
            # also already ProductFactory's own default, for the reason written at
            # tests/factories/product.py.
            format_ids = [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"}]

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

            ``paused`` is read off the CARRIER for the same reason, and that is what
            makes it gradeable. ``Package.SELLER_DECIDED`` names ``paused``, so the tool
            does not echo the request — it reports what the ad server answered. A real
            adapter answers from ``MediaPackage.paused`` (every one of them through
            ``AdServerAdapter._build_package_responses``), so this answers from the same
            place. A seller that fails to put the buyer's ask on the carrier gets
            ``False`` back here, exactly as it would from a real ad server.
            """
            # The tool calls adapter.create_media_buy(request, packages, ...)
            # positionally, so the MediaPackage list arrives as args[1].
            media_packages = kwargs.get("packages") or (args[1] if len(args) > 1 else None) or []

            return AdapterCreateResult(
                media_buy_id=mint(f"mb_{uuid.uuid4().hex[:8]}"),
                packages=[ResponsePackage(package_id=pkg.package_id, paused=pkg.paused) for pkg in media_packages],
                creative_deadline=self._reported_creative_deadline,
            )

        mock_adapter.create_media_buy.side_effect = _adapter_create_response
        # Save original side_effect so Given steps can restore it after error injection
        mock_adapter._original_create_side_effect = _adapter_create_response
        # The pre-creation check is the REAL mock ad server's, not a constant. It is a
        # production step: ``_create_media_buy_impl`` asks the adapter for its problems and
        # refuses with VALIDATION_ERROR when there are any, and the e2e_rest server runs
        # ``MockAdServer`` unpatched. A ``return_value = None`` here answered "no problems"
        # for every request, so the three in-process transports accepted creates the live
        # seller refuses -- a CPA package among them, which ``get_supported_pricing_models``
        # does not list. Only the ad-server CALLS are faked; the seller's own rules are not.
        # The check reads nothing off the principal or the config, so the env's own ids
        # and an empty config are all it needs.
        mock_adapter.validate_media_buy_request.side_effect = MockAdServer(
            config={},
            principal=Principal(principal_id=self._principal_id, name=self._principal_id, platform_mappings={}),
            tenant_id=self._tenant_id,
        ).validate_media_buy_request
        mock_adapter.add_creative_assets.return_value = None
        mock_adapter.associate_creatives.return_value = None
        mock_adapter.manual_approval_required = False
        mock_adapter.manual_approval_operations = []
        self.mock["adapter"].return_value = mock_adapter

        # The audit logger is NOT patched: it is our own code, and the audit trail it writes
        # (the audit_logs table) is what the audit scenarios grade on every transport --
        # the live e2e_rest server writes the same rows.

        # Slack notifier: no-op
        mock_slack = MagicMock()
        mock_slack.notify_media_buy_event.return_value = None
        self.mock["slack"].return_value = mock_slack

        # Context manager: mock returning objects with .context_id / .step_id.
        # The replay and adapter-rejection paths return before a WorkflowStep is
        # linked to a media buy, so no real ObjectWorkflowMapping FK row is needed.
        self.mock["context_mgr"].return_value = self._build_mock_context_manager(tool_name="create_media_buy")

        # Setup checklist: pass by default

        # Format spec: mock _get_format_spec_sync to avoid asyncio.run() inside a running
        # event loop. Resolution itself is NOT faked -- it reads the same pinned capture the
        # live server reads under ADCP_TESTING, so in-process and e2e agree about which
        # format ids exist. Tests for format mismatch (ext-p) override via
        # mock["format_spec"].side_effect.
        #
        # THERE IS NO PER-ID STUB DICT, and that is the point. A one-entry dict consulted
        # BEFORE the capture used to sit here, keyed ``display_300x250`` -- a synthetic id
        # the agent does not serve, made resolvable in process and only in process. It was
        # the single counterexample to the two sentences this comment used to end with
        # ("in-process and e2e format resolution agree by construction. Synthetic ids stay
        # unresolvable (None), matching the server"), and it is why three transports
        # answered one way and e2e_rest the other. Re-adding a stub re-creates that
        # blindness; a scenario needing a format the capture lacks wants its own finding
        # filed, not a stand-in.
        def _format_spec_side_effect(agent_url: str, format_id: str, *, provenance: Any = None) -> Any:
            from src.core.format_cache import load_reference_formats

            for fmt in load_reference_formats():
                if fmt.format_id.id == format_id:
                    return fmt
            # Unresolvable, exactly as the live server answers for an id it does not serve.
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

    @realize_e2e(_uploaded_assets_from_the_audit_trail)
    def uploaded_adapter_assets(self) -> list[dict[str, Any]]:
        """The asset dicts the create or update path handed the ad server, in :meth:`adapter_mocks` order.

        A NAMED SEAM instead of a step reaching into ``env.mock``: the escape-hatch guard
        refuses that reach in a step because it "works in-process, cannot work on e2e_rest,
        and is declarable nowhere". Declaring it here is what gives it a fourth-transport
        answer -- :func:`_uploaded_assets_from_the_audit_trail` reads the receipt the
        server's own ad server wrote -- rather than an ``e2e_unsupported`` row in a pinned
        allowlist that may only shrink.

        What a scenario asks of this is what the ad server RECEIVED, which for several
        fields is the only place they are observable at all: the click destination appears
        in no response the buyer sees, which is precisely why dropping it is silent.

        Reads ``adapter.add_creative_assets``, the spelling the SYNCHRONOUS create path
        uses. The approval and retroactive-push paths go through
        ``adapter.creatives_manager.add_creative_assets`` instead; a scenario wanting those
        needs its own seam rather than a widened one here, because which path ran is part of
        what it is asserting.

        Every adapter in :meth:`adapter_mocks` is read: an upload the update path makes lands
        on the update module's adapter and nowhere else, so reading only the create module's
        would report an empty list for every update. The order is NOT newest-last across
        adapters: each adapter's calls are in call order, and the adapters follow
        :meth:`adapter_mocks`, so every create-module upload precedes every update-module one
        whenever they happened. A Then reading ``[-1]`` gets the newest update upload in a
        scenario that updates, and the newest create upload in one that only creates.
        """
        uploaded: list[dict[str, Any]] = []
        for adapter in self.adapter_mocks():
            for call in adapter.add_creative_assets.call_args_list:
                assets = call.args[1] if len(call.args) > 1 else call.kwargs.get("assets")
                uploaded.extend(assets or [])
        return uploaded

    def adapter_mocks(self) -> list[MagicMock]:
        """Every ad-server adapter this env patches, one entry per production ``get_adapter``.

        The one list a Given configuring the adapter and a Then reading what it received
        both walk, so a module that gains its own patched ``get_adapter`` is added here once
        and both sides see it. This env patches the create module's; a subclass that patches
        another module's overrides this and appends its own.
        """
        return [self.mock["adapter"].return_value]

    def report_creative_deadline(self, deadline: datetime) -> None:
        """Make the ad server report *deadline* as the creative_deadline of every order it creates.

        ``AdapterCreateResult.creative_deadline`` is the buy-level deadline an adapter hands
        back from ``create_media_buy``, and the create tool persists it on the
        ``media_buys.creative_deadline`` column on both approval paths. The ad server is a
        system this repo does not own, so its answer is the one thing a scenario may fake.

        WHO READS IT: the adapter mock in THIS process. That is every create on the
        in-process transports, and on every transport the manual-approval execution the
        operator step runs (``run_approval`` calls ``execute_approved_media_buy`` here,
        with the env's adapter patch in force). A create that reaches the ad server inside
        the e2e_rest container is served by ``MockAdServer``, which reports no deadline, so
        a scenario reading this value off an AUTO-approved create on e2e_rest would need a
        live-stack realization this method does not have.
        """
        self._reported_creative_deadline = deadline

    @realize_e2e(_adapter_create_receipts_on_the_live_stack)
    def adapter_create_calls(self) -> int:
        """How many times the create path handed the ad server a media buy.

        The fourth-transport answer is the receipt the server's ``MockAdServer`` writes per
        ``create_media_buy`` it is handed, for the reason ``uploaded_adapter_assets`` reads
        one: a mock in this process cannot see a call made in the server container, so a
        scenario asserting the ad server was NOT reached would pass vacuously over e2e_rest.

        One asymmetry, so a caller does not lean on it: ``MockAdServer`` raises a
        ``test_behavior`` fault BEFORE it writes the receipt, so after ``fail_adapter_create``
        the live count is 0 where the in-process count is 1. Count only creates that reached
        the ad server unfaulted.
        """
        return int(self.mock["adapter"].return_value.create_media_buy.call_count)

    @realize_e2e(_inject_create_failure_on_the_live_stack)
    def fail_adapter_create(self, error: Exception, **behavior: Any) -> None:
        """Make the ad server refuse ``create_media_buy`` with *error*.

        *behavior* is the same fault spelled in ``MockAdServer``'s ``test_behavior`` knobs,
        which is how the live stack's real adapter is told to raise it.
        """
        self.mock["adapter"].return_value.create_media_buy.side_effect = error

    @realize_e2e(_live_ad_server_on_the_live_stack)
    def run_ad_server(self, adapter_type: str) -> None:
        """Give the tenant a REAL ad-server configuration of *adapter_type*.

        Two halves, and the second is the reason this is not just a factory call.

        1. The ``adapter_config`` row, so ``resolve_tenant_adapter_type`` answers
           *adapter_type* instead of the ``tenant.ad_server`` default. For
           ``google_ad_manager`` a network code goes with it: without one,
           selection refuses at its own ``network_code is required`` check and the
           scenario would grade a half-configured tenant rather than a reachable
           ad server. No credential is seeded, and that is the point — the one
           thing a deployment with no GAM credential can honestly do with a live
           GAM selection is refuse it:
           ``GoogleAdManager.__init__`` raises
           ``AdCPConfigurationError(field="authentication")``
           (google_ad_manager.py:176) before it builds a client manager, so the
           refusal reaches the wire as CONFIGURATION_ERROR and costs no network
           call at all.

        2. The fake adapter STOPS answering. ``EXTERNAL_PATCHES`` replaces
           ``media_buy_create.get_adapter`` wholesale, so by default no scenario in
           this env can see which adapter production would have chosen — the
           decision is the thing being faked. Pointing the patched name's
           ``side_effect`` at the real :func:`get_adapter` hands the decision back
           to production while leaving the patch in place for the rest of the env.
           A scenario that says the tenant runs google_ad_manager and is then
           served by a MagicMock is not running that ad server, so the two halves
           have to move together.

        The e2e branch does only half 1: the live server patches nothing, so the
        decision is already production's there.
        """
        from src.core.helpers.adapter_helpers import get_adapter

        self._write_adapter_config(adapter_type)
        self.mock["adapter"].side_effect = get_adapter

    def get_currency_limit(self, currency: str) -> Any:
        """The tenant's ``CurrencyLimit`` row for ``currency``. Raises when there is none.

        The row the create path reads for the tenant's minimum package budget and daily
        cap, so a Given that changes either edits this row rather than adding one.
        """
        from sqlalchemy import select

        from src.core.database.models import CurrencyLimit

        stmt = select(CurrencyLimit).filter_by(tenant_id=self._tenant_id, currency_code=currency)
        return self.get_session().scalars(stmt).one()

    def _write_adapter_config(self, adapter_type: str) -> None:
        """The ``adapter_config`` row naming *adapter_type*, committed."""
        from tests.factories import AdapterConfigFactory

        tenant, _principal = self.setup_default_data()
        AdapterConfigFactory(
            tenant=tenant,
            adapter_type=adapter_type,
            # Only read on the GAM branch; harmless on the others, and spelling it
            # unconditionally keeps one call site rather than a per-adapter kwargs dict.
            gam_network_code="123456789",
        )
        self._commit_factory_data()

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
