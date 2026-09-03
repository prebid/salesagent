"""Sandbox requests are validated against the TENANT's declared adapter, not the simulator.

The sandbox short-circuit in ``get_adapter`` substitutes ``MockAdServer`` so no real
ad-platform call is ever made. That substitution is correct for EXECUTION and wrong for
CONSTRAINT DECLARATION: the simulator can simulate all seven AdCP pricing models, so a
sandbox buy read its supported-pricing answer off the simulator and accepted ``cpcv`` on a
tenant whose real ad server rejects it.

AdCP 3.1.1 ``dist/docs/3.1.1/media-buy/advanced-topics/sandbox.mdx``, §Seller
implementation:

    - **MUST** validate inputs the same way as production (reject invalid budgets, bad
      dates, etc.)

and §Protocol compliance: "Apply normal input validation (sandbox does not bypass
validation)."

So: execution goes to the mock, constraints come from the adapter the tenant DECLARED.
Both production readers of ``get_supported_pricing_models()`` — the pre-create validation
at ``media_buy_create.py`` and the ``pricing_options[].supported`` annotation in
``products.py`` — ask the adapter instance ``get_adapter`` returned, so both are graded
here through that one seam.

Kevel is the declared adapter for the adapter-level live-vs-sandbox comparison because
``get_adapter`` can build a real ``Kevel`` in dry-run mode without credentials, which lets
the sandbox error list be compared against the LIVE adapter's own error list rather than
against a string this test made up. GAM is the declared adapter everywhere else, including
the end-to-end class, which seeds the ``AdapterConfig`` row a live GAM tenant needs so the
same comparison can be made on the outcome of a real ``create_media_buy`` request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.adapters.google_ad_manager import GoogleAdManager
from src.adapters.kevel import Kevel
from src.adapters.mock_ad_server import MockAdServer
from src.core.helpers.adapter_helpers import get_adapter
from src.core.schemas import (
    CreateMediaBuyRequest,
    FormatId,
    MediaPackage,
    PackageRequest,
    Principal,
)
from tests.factories import (
    AccountFactory,
    AdapterConfigFactory,
    AgentAccountAccessFactory,
    PricingOptionFactory,
    ProductFactory,
)
from tests.harness.assertions import assert_rejected
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.product import ProductEnv
from tests.harness.transport import Transport, TransportResult

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_FORMAT = FormatId(agent_url="https://creative.test", id="display_300x250")


def _principal() -> Principal:
    """A principal that can build any adapter get_adapter dispatches to."""
    return Principal(
        principal_id="p_sandbox_parity",
        name="Sandbox Parity Buyer",
        # Kevel's __init__ requires an advertiser id even in dry-run; the mock reads its
        # own key for HITL config and tolerates its absence.
        platform_mappings={"kevel": {"advertiser_id": "kevel-adv-1"}},
    )


def _adapter_for(ad_server: str, *, sandbox: bool):
    """Build the adapter get_adapter returns for a tenant declaring *ad_server*.

    Uses the dict-shaped tenant get_adapter already supports, so no tenant row is
    needed — the only DB touch on the live path is the AdapterConfig lookup, which
    returns nothing for an absent tenant and leaves tenant.ad_server as the selection.
    """
    return get_adapter(
        _principal(),
        dry_run=True,
        tenant={"tenant_id": "t_sandbox_parity", "ad_server": ad_server},
        sandbox=sandbox,
    )


def _request() -> CreateMediaBuyRequest:
    """A request that is valid on every axis EXCEPT the pricing model under test.

    Future dates and a mid-range budget matter: the mock layers GAM-like date, goal and
    budget checks on top of the shared pricing check, and those extra errors would mask
    what this module is grading.
    """
    start = datetime.now(UTC) + timedelta(days=1)
    return CreateMediaBuyRequest(
        brand={"domain": "sandbox-parity.example.com"},
        idempotency_key="sandbox-parity-key-0001",
        start_time=start,
        end_time=start + timedelta(days=30),
        packages=[
            PackageRequest(
                product_id="prod_parity",
                budget=5000.0,
                pricing_option_id="parity_pricing",
                format_ids=[_FORMAT],
            )
        ],
    )


def _packages() -> list[MediaPackage]:
    return [
        MediaPackage(
            package_id="pkg_parity",
            name="Parity Package",
            delivery_type="guaranteed",
            cpm=5.0,
            impressions=10000,
            format_ids=[_FORMAT],
            product_id="prod_parity",
        )
    ]


def _validate(adapter, pricing_model: str) -> list[str]:
    """Call the adapter's pre-create validation directly and return its error list.

    This is the same adapter method ``_create_media_buy_impl`` calls, invoked here
    WITHOUT driving the create path — so it grades the constraint profile the adapter
    answers with, not the outcome of a request. The request outcome is graded by
    ``TestSandboxCreateRefusesTheRequest`` below, which drives the real create path.

    ``start_time``/``end_time`` are passed as resolved datetimes, not read back off the
    request: ``req.start_time`` is a ``StartTiming`` union, and ``_create_media_buy_impl``
    resolves it to a datetime before handing it to the adapter.
    """
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(days=30)
    pricing_info: dict[str, dict[str, Any]] = {
        "pkg_parity": {"pricing_model": pricing_model, "rate": 5.0, "currency": "USD", "is_fixed": True}
    }
    return adapter.validate_media_buy_request(_request(), _packages(), start, end, pricing_info)


class TestSandboxDeclaresTheTenantsAdapterConstraints:
    """``get_supported_pricing_models()`` answers for the declared ad server."""

    def test_gam_tenant_sandbox_declares_gams_models(self, integration_db):
        """A sandbox buyer on a GAM tenant may buy exactly what GAM can execute.

        Reads the expected set off ``GoogleAdManager`` rather than restating it, because
        the obligation is "the DECLARED adapter's models", not a particular four — but
        the ``cpcv`` assertion below pins the concrete bite, so a declaration that
        silently widened to the simulator's seven cannot pass.
        """
        adapter = _adapter_for("google_ad_manager", sandbox=True)

        assert adapter.get_supported_pricing_models() == set(GoogleAdManager.supported_pricing_models)
        assert "cpcv" not in adapter.get_supported_pricing_models(), (
            "the simulator supports cpcv and GAM does not — a sandbox buy on a GAM tenant "
            "must not be told it may buy cpcv"
        )
        assert "cpm" in adapter.get_supported_pricing_models()

    def test_sandbox_still_executes_on_the_simulator(self, integration_db):
        """Constraint declaration moved; execution routing did NOT.

        The whole point of the short-circuit is that a sandbox request never reaches a
        real ad platform (AdCP 3.1.1 sandbox.mdx: "MUST NOT make real ad platform API
        calls"). If reading the declared adapter's constraints ever turned into building
        the declared adapter, this reddens.
        """
        assert isinstance(_adapter_for("google_ad_manager", sandbox=True), MockAdServer)
        assert isinstance(_adapter_for("kevel", sandbox=True), MockAdServer)

    def test_mock_tenant_sandbox_keeps_the_simulators_full_range(self, integration_db):
        """Negative control: the fix must not narrow tenants that really declared mock.

        Without this, "always answer {cpm}" would pass every other test in this class.
        """
        adapter = _adapter_for("mock", sandbox=True)

        assert adapter.get_supported_pricing_models() == set(MockAdServer.supported_pricing_models)
        assert _validate(adapter, "cpcv") == []

    @pytest.mark.parametrize(
        "ad_server",
        ["creative_engine", "not_a_real_adapter", ""],
        ids=["registered-but-not-an-ad-server", "unregistered", "empty"],
    )
    def test_unresolvable_ad_server_declares_the_simulators_models(self, integration_db, ad_server):
        """Mirror live, which runs these on the simulator too.

        ``get_adapter``'s final else-branch falls back to the mock for any adapter type
        it cannot dispatch, so declaring anything narrower here would make sandbox
        STRICTER than the production it mirrors — the opposite failure, but still a
        parity break.
        """
        adapter = _adapter_for(ad_server, sandbox=True)

        assert adapter.get_supported_pricing_models() == set(MockAdServer.supported_pricing_models)


class TestSandboxRejectsWhatTheDeclaredAdapterCannotExecute:
    """The pre-create validation ``media_buy_create`` runs rejects identically."""

    def test_sandbox_rejects_a_model_the_declared_adapter_cannot_execute(self, integration_db):
        errors = _validate(_adapter_for("kevel", sandbox=True), "cpcv")

        assert errors, "a sandbox buy for cpcv on a Kevel tenant must be rejected, not accepted"
        assert any("cpcv" in e for e in errors), errors

    def test_sandbox_rejection_is_the_live_adapters_own_error(self, integration_db):
        """Parity is graded against the LIVE adapter's error list, not a literal.

        ``get_adapter(..., sandbox=False)`` on this tenant builds a real ``Kevel``; the
        same request is validated by both and the two lists must be equal. This is the
        "with the same error a live request gets" half of the obligation — a fix that
        rejected cpcv with a different message or a different supported-models list
        would satisfy the test above and fail this one.
        """
        live = _adapter_for("kevel", sandbox=False)
        sandboxed = _adapter_for("kevel", sandbox=True)
        assert isinstance(live, Kevel), "this test is only meaningful against a real live adapter"

        live_errors = _validate(live, "cpcv")
        sandbox_errors = _validate(sandboxed, "cpcv")

        assert live_errors, "precondition: the live adapter must itself reject cpcv"
        assert sandbox_errors == live_errors

    def test_sandbox_accepts_a_model_the_declared_adapter_can_execute(self, integration_db):
        """Negative control: the constraint profile rejects by CONTENT, not always."""
        assert _validate(_adapter_for("kevel", sandbox=True), "cpm") == []
        assert _validate(_adapter_for("google_ad_manager", sandbox=True), "vcpm") == []


class TestSandboxCreateRefusesTheRequest:
    """End-to-end: ``create_media_buy`` REFUSES a model the declared adapter can't execute.

    The two classes above call ``validate_media_buy_request`` on an adapter directly.
    That grades the constraint DECLARATION and the adapter's own error list, but never
    drives ``_create_media_buy_impl`` — so on its own it cannot say whether a sandbox
    ``cpcv`` buy on a GAM tenant is actually refused. The obligation is about the
    request: sandbox.mdx:231 "MUST validate inputs the same way as production (reject
    invalid budgets, bad dates, etc.)". This class grades the request outcome, over a
    real transport, and is the arm that reddens if the constraint profile stops
    reaching production's create path.

    ``MediaBuyCreateEnv`` patches ``media_buy_create.get_adapter`` with a stand-in whose
    ``validate_media_buy_request`` returns ``None``, so the real resolver is restored
    onto that patch below — left mocked, these arms would accept everything and grade
    nothing. The env's other patches (audit, slack, context manager, setup checklist,
    format spec) stay in place; none of them touch adapter selection or validation.
    """

    _GAM_MODELS_SENTENCE = "Supported pricing models: " + ", ".join(
        sorted(model.upper() for model in GoogleAdManager.supported_pricing_models)
    )

    @staticmethod
    def _create_cpcv(*, sandbox: bool) -> tuple[TransportResult, list[Any]]:
        """Send a ``cpcv`` create_media_buy from a tenant that declares GAM.

        Returns the transport result and every adapter production built while serving
        it, so a caller can assert WHICH adapter answered — a live arm that silently ran
        on the simulator would compare the simulator against itself.

        ``dry_run`` is on for BOTH modes so the two arms differ in exactly one bit — the
        account's sandbox flag. It is required by the live mode: a live GAM tenant builds
        a real ``GoogleAdManager``, whose ``__init__`` opens a GAM client (fetching OAuth
        credentials) unless dry_run is set. It changes nothing about what is graded here,
        because the pre-create adapter validation runs "regardless of dry_run" — and it
        is not what keeps the SANDBOX arm off the network: that arm returns the simulator
        from the short-circuit before any adapter config or credential is read.
        """
        built: list[Any] = []

        def _recording_get_adapter(*args: Any, **kwargs: Any) -> Any:
            adapter = get_adapter(*args, **kwargs)
            built.append(adapter)
            return adapter

        with MediaBuyCreateEnv(ad_server="google_ad_manager", dry_run=True) as env:
            env.mock["adapter"].side_effect = _recording_get_adapter

            tenant, principal, product, _cpm_option = env.setup_media_buy_data()
            # ad_server is declared in BOTH places for the same reason as the catalog
            # class below: the DB row is what a real deployment reads, while it is
            # identity.tenant — built from this env's tenant overrides — that reaches
            # get_adapter.
            env.setup_default_data(ad_server="google_ad_manager")
            PricingOptionFactory(product=product, pricing_model="cpcv")
            if not sandbox:
                # Only the live path reads this row, and without it get_adapter cannot
                # build GAM at all ("network_code is required"). Seeding it for the
                # sandbox arm too would prove nothing: that arm short-circuits first.
                AdapterConfigFactory(tenant=tenant, adapter_type="google_ad_manager", gam_network_code="99999")

            account_id = "acc_sbx_create"
            AccountFactory(tenant=tenant, account_id=account_id, sandbox=sandbox)
            # Resolution enforces agent access; without the grant the request is
            # rejected before any adapter is constructed.
            AgentAccountAccessFactory(
                tenant_id=tenant.tenant_id,
                principal_id=principal.principal_id,
                account_id=account_id,
            )

            start = datetime.now(UTC) + timedelta(days=30)
            result = env.call_via(
                Transport.REST,
                account={"account_id": account_id},
                brand={"domain": "sandbox-parity.example.com"},
                packages=[
                    {
                        "product_id": product.product_id,
                        "budget": 5000.0,
                        # cpcv_usd_fixed is the id product_conversion derives for the
                        # PricingOption seeded above.
                        "pricing_option_id": "cpcv_usd_fixed",
                    }
                ],
                start_time=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_time=(start + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                po_number="SANDBOX-CREATE-PARITY-1",
            )
        return result, built

    @staticmethod
    def _wire_message(result: TransportResult) -> str:
        """The rejection message as the buyer receives it, off the real wire body."""
        envelope = result.wire_error_envelope
        assert envelope is not None, f"REST must capture a real wire error envelope: {result.error!r}"
        return envelope["errors"][0]["message"]

    def test_sandbox_create_of_an_unexecutable_model_is_refused(self, integration_db):
        """The end-to-end bite: the REQUEST is rejected, not merely annotated.

        Before the constraint profile reached the simulator, this create SUCCEEDED — the
        sandbox adapter answered with its own seven models and accepted cpcv on a tenant
        whose declared ad server cannot execute it.
        """
        result, built = self._create_cpcv(sandbox=True)

        assert built and isinstance(built[0], MockAdServer), (
            f"the sandbox account must still execute on the simulator, got {built!r}"
        )
        assert_rejected(result, code="VALIDATION_ERROR", message_contains="cpcv")
        assert self._GAM_MODELS_SENTENCE in self._wire_message(result), (
            "the rejection must carry the DECLARED adapter's models — a message listing "
            f"the simulator's seven means the profile never arrived: {self._wire_message(result)}"
        )

    def test_live_create_of_the_same_model_is_refused_identically(self, integration_db):
        """The "same way as production" half, graded end-to-end rather than per-adapter.

        The same request on the same tenant with a LIVE account builds a real
        ``GoogleAdManager``; the buyer must receive the same rejection. A fix that
        rejected sandbox cpcv with its own wording — or with a different supported-models
        list — passes the test above and fails this one.
        """
        live, built = self._create_cpcv(sandbox=False)
        sandboxed, _ = self._create_cpcv(sandbox=True)

        assert built and isinstance(built[0], GoogleAdManager), (
            f"this test is only meaningful against a real live adapter, got {built!r}"
        )
        assert_rejected(live, code="VALIDATION_ERROR", message_contains="cpcv")
        assert self._wire_message(sandboxed) == self._wire_message(live)


class TestSandboxCatalogAnnotatesTheDeclaredAdaptersSupport:
    """``get_products`` annotates ``pricing_options[].supported`` from the same seam.

    ``products.py`` reads ``get_supported_pricing_models()`` off the adapter
    ``get_adapter`` returned, so before this change a sandbox account received a catalog
    advertising pricing its tenant's ad server cannot execute — the buyer would then be
    rejected at create time for a product the catalog said was fine.

    Driven over a real transport rather than IMPL: ``identity.sandbox`` is set by
    ``enrich_identity_with_account`` at the MCP/A2A/REST boundary, which the in-process
    IMPL path does not run.
    """

    @staticmethod
    def _annotated_options() -> dict[str, dict[str, Any]]:
        """Catalog a sandbox account gets for a GAM tenant, keyed by pricing model.

        The product carries one model GAM CAN execute and one it cannot, so the two
        annotations in a single response are the control for each other: "annotate
        everything unsupported" fails on cpm, "annotate everything supported" fails on
        cpcv.
        """
        # ad_server is declared in BOTH places on purpose: the DB row is what a real
        # deployment reads, while the harness identity carries its own tenant dict built
        # by TenantFactory.make_tenant (whose ad_server defaults to "mock"), and it is
        # identity.tenant that reaches get_adapter.
        with ProductEnv(tenant_id="tgpsbxcap", principal_id="pgpsbxcap", ad_server="google_ad_manager") as env:
            tenant, principal = env.setup_default_data(ad_server="google_ad_manager")
            env.set_policy_approved()
            env.set_ranking_disabled()

            product = ProductFactory(tenant=tenant, product_id="prod-gp-sbx-cap")
            PricingOptionFactory(product=product, pricing_model="cpm")
            PricingOptionFactory(product=product, pricing_model="cpcv")

            AccountFactory(tenant=tenant, account_id="acc_gp_sbx", sandbox=True)
            AgentAccountAccessFactory(
                tenant_id=tenant.tenant_id,
                principal_id=principal.principal_id,
                account_id="acc_gp_sbx",
            )

            result = env.call_via(
                Transport.REST,
                brief="video ads",
                account={"account_id": "acc_gp_sbx"},
            )

        assert not result.is_error, f"dispatch failed: {result.error!r}"
        assert result.wire_response is not None, "REST must capture a real wire response"
        products = result.wire_response["products"]
        assert len(products) == 1, products
        options = {opt["pricing_model"]: opt for opt in products[0]["pricing_options"]}
        assert set(options) == {"cpm", "cpcv"}, options
        return options

    def test_sandbox_catalog_marks_cpcv_unsupported_on_a_gam_tenant(self, integration_db):
        options = self._annotated_options()

        assert options["cpcv"]["supported"] is False, (
            "the tenant declared GAM, which cannot execute cpcv — a sandbox catalog that "
            "advertises it hands the buyer a product create_media_buy will reject"
        )
        assert "CPCV" in options["cpcv"]["unsupported_reason"]
        assert options["cpm"]["supported"] is True, (
            "cpm is within GAM's declared models — annotating it unsupported would be the opposite failure"
        )
