"""Entity test suite: media-buy

Spec verification: 2026-02-26
adcp spec commit: 8f26baf3
adcp-client-python commit: a08805d
Verified: 78/130 CONFIRMED, 52 UNSPECIFIED, 0 CONTRADICTS, 0 SPEC_AMBIGUOUS

Canonical test module for media-buy domain behavior.
Maps to test-obligations files:
  - UC-002-create-media-buy.md
  - UC-003-update-media-buy.md
  - UC-004-deliver-media-buy-metrics.md (main flow / status filter / date range only)
  - business-rules.md (BR-RULE-006, 008, 009, 011, 012, 013, 017, 018, 020, 021, 022, 024, 026, 028, 030)
  - constraints.md (media-buy, create-media-buy-request, update-media-buy-request)

Coverage: 47/130 obligations implemented, 83 stubs remaining.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import ANY, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.adapters.base import AdapterCreateRequest, AdapterUpdateResult
from src.core.exceptions import (
    AdCPAuthorizationError,
    AdCPBudgetExceededError,
    AdCPCreativeNotFoundError,
    AdCPGoneError,
    AdCPProductNotFoundError,
    AdCPValidationError,
)
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    AffectedPackage,
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuySuccess,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    GetMediaBuysMediaBuy,
    GetMediaBuysPackage,
    GetMediaBuysRequest,
    GetMediaBuysResponse,
    PricingOption,
    ReportingPeriod,
    UpdateMediaBuyError,
    UpdateMediaBuyRequest,
    UpdateMediaBuySubmitted,
    UpdateMediaBuySuccess,
)
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from tests.factories.media_buy import (
    default_request_packages,
    package_pricing_fields,
    pricing_options_for,
    pricing_options_named,
    request_package,
)
from tests.factories.principal import PrincipalFactory
from tests.factories.product import PricingOptionFactory
from tests.helpers.unit_identity import fabricated_account_identity

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _stub_media_buy_reads(repo, row, media_buy_id: str | None = None):
    """Point BOTH row accessors at *row*.

    The update tool reads the row back through ``get_by_id_or_raise`` (the
    repository's typed not-found accessor). A stub that wires only ``get_by_id``
    leaves the other returning a bare ``MagicMock``, whose ``.status`` is not a real
    status — which now fails loudly at the vocabulary boundary instead of being
    silently interpreted. One helper so the two never drift apart again.

    ``media_buy_id`` is read only on the ``row is None`` branch, where it becomes the
    ``details.media_buy_id`` the repository's own not-found refusal carries.
    """
    repo.get_by_id.return_value = row
    if row is None:
        # Faithful to the repository: the *_or_raise accessor does not return None,
        # it raises. A stub that returned None here would let production walk past a
        # missing row and fail later with an AttributeError instead of the typed
        # MEDIA_BUY_NOT_FOUND the buyer is owed.
        from src.core.errors.details import EntityRefDetails
        from src.core.exceptions import AdCPMediaBuyNotFoundError

        # Mirrors MediaBuyRepository.get_by_id_or_raise exactly: the identity travels
        # as typed details, and the buyer-facing message/suggestion are functions of
        # MEDIA_BUY_NOT_FOUND (CODE_TABLE), not of the raise site — a stub that
        # authored either would carry a shape production can no longer produce.
        repo.get_by_id_or_raise.side_effect = AdCPMediaBuyNotFoundError(
            details=EntityRefDetails(media_buy_id=media_buy_id),
        )
    else:
        repo.get_by_id_or_raise.return_value = row


def _future(days: int = 7) -> str:
    """Return an ISO 8601 datetime string N days in the future."""
    dt = datetime.now(UTC) + timedelta(days=days)
    return dt.isoformat()


def _make_request(**overrides) -> CreateMediaBuyRequest:
    """Build a minimal valid CreateMediaBuyRequest."""
    defaults = {
        "brand": {"domain": "testbrand.com"},
        "start_time": _future(1),
        "end_time": _future(8),
        "packages": [{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
        # Required by AdCP 3.0.1; mocked-UoW tests must stub the probe to a miss
        # (find_by_key -> None) — make_mock_uow does this by default.
        "idempotency_key": "unit-test-default-key-0001",
        "account": {"account_id": "acct_test"},
    }
    defaults.update(overrides)
    return CreateMediaBuyRequest(**defaults)


#: The confirmation instant every response built here carries. A literal, not ``now()``:
#: these cases assert on response SHAPE, so one deterministic value keeps two of them from
#: disagreeing, and a test is not speaking for the repository that owns the real column.
_CONFIRMED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _make_success(**overrides) -> CreateMediaBuySuccess:
    """Build a minimal valid CreateMediaBuySuccess response — the BUYER's envelope.

    Was ``CreateMediaBuySuccess.carrier(...)``, a classmethod deleted when adapters moved
    to result types (commit ecfdd7771): an adapter now returns
    ``src.adapters.base.AdapterCreateResult`` and no wire model stands in for it, so the
    only thing left for a wire model to be is the envelope a buyer receives. That is what
    every case below asserts on, so this uses ``sync_success`` and passes ``confirmed_at``
    and ``revision`` explicitly — neither carries a field default, which is what stops a
    response from fabricating a value only a persisted row can supply.
    """
    defaults = {
        "media_buy_id": "mb_1",
        "packages": [{"package_id": "pkg_1"}],
        "confirmed_at": _CONFIRMED_AT,
        "revision": 1,
    }
    defaults.update(overrides)
    return CreateMediaBuySuccess.sync_success(**defaults)


def _make_identity(
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
) -> ResolvedIdentity:
    """Build a ResolvedIdentity with default test values.

    ``testing_context`` and ``dry_run`` are gone with the channel they configured
    (commit a1b79d22d): ``src/core/testing_hooks`` is deleted, a request carries no
    testing headers, and the identity has no testing context — so does ``protocol``,
    which the identity no longer names. The explicit ``tenant={"tenant_id": ...}`` is
    dropped too: it said exactly what the factory builds for ``tenant_id`` anyway, and
    the identity refuses a dict tenant at construction.
    """
    return PrincipalFactory.make_identity(principal_id=principal_id, tenant_id=tenant_id)


def _mock_product(product_id: str = "prod_1", currency: str = "USD") -> MagicMock:
    """Create a mock DB Product whose pricing option is a REAL unpersisted row.

    The option was a ``MagicMock(spec=[...])`` listing the model's columns by hand, so it
    went stale the moment the model gained one — ``pricing_option_id`` was added and every
    test through here started raising ``AttributeError`` on a shape production never
    produces. The factory cannot drift from the model that way.
    """
    pricing_option = PricingOptionFactory.build(product_id=product_id, currency=currency, min_spend_per_package=None)

    product = MagicMock()
    product.product_id = product_id
    product.name = "Test Product"
    product.pricing_options = [pricing_option]
    product.delivery_type = "non_guaranteed"
    product.format_ids = [{"agent_url": "http://agent.test", "id": "fmt_1"}]
    return product


def _mock_media_buy(
    media_buy_id: str = "mb_1",
    start_date: date | None = None,
    end_date: date | None = None,
    budget: Decimal = Decimal("5000.00"),
    currency: str = "USD",
) -> MagicMock:
    """Create a mock MediaBuy ORM object."""
    buy = MagicMock()
    buy.media_buy_id = media_buy_id
    buy.tenant_id = "test_tenant"
    buy.principal_id = "test_principal"
    buy.budget = budget
    buy.currency = currency
    buy.start_date = start_date or date.today()
    buy.end_date = end_date or (date.today() + timedelta(days=30))
    buy.start_time = None
    buy.end_time = None
    buy.created_at = datetime.now(UTC)
    buy.updated_at = datetime.now(UTC)
    buy.raw_request = {"packages": default_request_packages()}
    buy.status = "active"
    return buy


# ===========================================================================
# UC-002: CREATE MEDIA BUY
# ===========================================================================


class TestCreateMediaBuySchemaCompliance:
    """UC-002 schema validation: request parsing and field requirements."""

    def test_create_request_requires_brand(self):
        """UC-002-S01: brand is required per AdCP spec.

        Spec: CONFIRMED -- create-media-buy-request.json requires brand_manifest (mapped to brand in library)
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-request.json
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/src/adcp/types/generated_poc/media_buy/create_media_buy_request.py
        Covers: UC-002-MAIN-02
        """
        with pytest.raises(ValidationError):
            CreateMediaBuyRequest(
                account={"account_id": "acct_test"},
                start_time=_future(1),
                end_time=_future(8),
                packages=[{"product_id": "p1", "budget": 1000.0}],
                idempotency_key="unit-test-key-brand-0001",  # valid — failure stays scoped to brand
                # brand omitted
            )

    def test_create_request_accepts_valid_minimal(self):
        """UC-002-S03: minimal valid request parses without error.

        Spec: CONFIRMED -- validates required fields from create-media-buy-request.json
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-request.json
        Covers: UC-002-MAIN-02
        """
        req = _make_request()
        assert req.packages is not None
        assert len(req.packages) == 1

    def test_create_request_start_time_must_be_tz_aware(self):
        """UC-002-S04: non-tz-aware start_time rejected.

        Spec: CONFIRMED -- start-timing.json requires "format": "date-time" (tz-aware)
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/start-timing.json
        Covers: UC-002-EXT-C-06
        """
        with pytest.raises(ValidationError):
            CreateMediaBuyRequest(
                account={"account_id": "acct_test"},
                brand={"domain": "test.com"},
                start_time="2026-03-01T00:00:00",  # no tz
                end_time=_future(8),
                packages=[{"product_id": "p1", "budget": 1000.0}],
                idempotency_key="unit-test-key-tzaware-01",  # valid — failure stays scoped to start_time
            )

    def test_create_request_accepts_asap_start_time(self):
        """UC-002-S05: start_time='asap' is valid per AdCP spec.

        Spec: CONFIRMED -- start-timing.json oneOf includes const "asap"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/start-timing.json
        Covers: UC-002-ALT-ASAP-START-TIMING-01
        """
        req = _make_request(start_time="asap")
        assert req.start_time is not None

    def test_create_request_get_total_budget(self):
        """UC-002-S06: get_total_budget sums all package budgets.

        Spec: UNSPECIFIED (implementation-defined helper; spec defines budget at package level)
        Covers: UC-002-MAIN-07
        """
        req = _make_request(
            packages=[
                {"product_id": "p1", "budget": 3000.0, "pricing_option_id": "cpm_usd_fixed"},
                {"product_id": "p2", "budget": 2000.0, "pricing_option_id": "cpm_usd_fixed"},
            ]
        )
        assert req.get_total_budget() == 5000.0

    def test_create_request_get_product_ids_deduplicates(self):
        """UC-002-S07: get_product_ids returns unique IDs preserving order.

        Spec: UNSPECIFIED (implementation-defined helper; spec defines product_id per package)
        Covers: UC-002-EXT-E-01
        """
        req = _make_request(
            packages=[
                {"product_id": "p1", "budget": 1000.0, "pricing_option_id": "cpm_usd_fixed"},
                {"product_id": "p1", "budget": 2000.0, "pricing_option_id": "cpm_usd_fixed"},
                {"product_id": "p2", "budget": 3000.0, "pricing_option_id": "cpm_usd_fixed"},
            ]
        )
        assert req.get_product_ids() == ["p1", "p2"]


class TestCreateMediaBuyResponseShapes:
    """UC-002 response shape: success/error serialization."""

    def test_success_response_has_media_buy_id(self):
        """UC-002-R01: CreateMediaBuySuccess has media_buy_id.

        Spec: CONFIRMED -- create-media-buy-response.json success required: ["media_buy_id", ...]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-response.json
        Ported from test_approval_error_handling_core.py::test_success_response_has_media_buy_id
        Covers: UC-002-POST-04
        """
        resp = _make_success(media_buy_id="mb_123")
        assert resp.media_buy_id == "mb_123"

    def test_error_response_has_errors_not_media_buy_id(self):
        """UC-002-R02: CreateMediaBuyError has errors field, no media_buy_id.

        Spec: CONFIRMED -- create-media-buy-response.json error: not anyOf [media_buy_id]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-response.json
        Ported from test_approval_error_handling_core.py::test_error_response_has_errors_not_media_buy_id
        Covers: UC-002-CC-ATOMIC-RESPONSE-SEMANTICS-02
        """
        from src.core.schemas import Error

        resp = CreateMediaBuyError(status="failed", errors=[Error(code="VALIDATION_ERROR", message="msg")])
        assert resp.errors is not None
        assert len(resp.errors) == 1

    # test_success_response_excludes_internal_fields (UC-002-R03) is REMOVED. It built a
    # response with workflow_step_id="ws_abc" and asserted the key was absent from the
    # dump. The field itself is gone (commit ecfdd7771 deleted workflow_step_id and
    # implementation_date from the response models: a workflow step an adapter opens is
    # tracked by the workflow tables, and a field that must not reach the wire has no
    # spelling on a wire model). With the field undeclared the construction quietly drops
    # the keyword, so the assertion could no longer fail for the reason it was written —
    # it would pass against a model that never had the field at all. The live rule it
    # stood for (a response serializes to the pinned shape by inheritance, internal
    # fields declared Field(exclude=True) at their declaration) is graded in
    # test_adcp_contract.py and test_architecture_schema_inheritance.py.

    def test_body_carries_status_beside_the_domain_fields(self):
        """The buyer receives ONE flat document: envelope fields at the root, not nested.

        Spec: create-media-buy-response.json composes core/protocol-envelope.json at its root
        via ``allOf``, so ``status`` is a sibling of ``media_buy_id``, not a wrapper around it.
        Covers: UC-002-MAIN-21
        """
        dumped = _make_success(media_buy_id="mb_1").model_dump()
        assert dumped["status"] == "completed"
        assert dumped["media_buy_id"] == "mb_1"

    def test_replayed_marks_a_replay_and_is_false_on_a_fresh_response(self):
        """``replayed`` distinguishes a cached answer from a fresh one.

        Spec: core/protocol-envelope.json — "Set to true when this response was returned from
        the idempotency cache rather than from a fresh execution. Set to false (or omitted)
        when the request was executed fresh." This seller emits ``false``, the same as its
        other thirteen tools.
        """
        assert _make_success(media_buy_id="mb_1").model_dump()["replayed"] is False

        replay = _make_success(media_buy_id="mb_1")
        replay.replayed = True
        assert replay.model_dump()["replayed"] is True


class TestCreateMediaBuyValidation:
    """UC-002 business rule validation: budget, products, pricing, dates."""

    @pytest.mark.asyncio
    async def test_product_not_found_returns_error(self):
        """UC-002-V01: product not in catalog returns error in result.

        Spec: CONFIRMED -- package-request.json requires product_id; seller validates product existence
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-request.json
        Ported from test_create_media_buy_behavioral.py::test_product_not_found_returns_error
        Covers: UC-002-EXT-B-01
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Request references prod_missing but DB has no products
        req = _make_request(
            packages=[
                {"product_id": "prod_missing", "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"},
            ]
        )

        # An AccountIdentity, which is what _create_media_buy_impl DECLARES: its DTO puts
        # ``account`` in /required, so the boundary always resolves one and the identity a
        # bare make_identity() returns -- ``account=None`` -- is a caller the controller
        # can never receive. The database here is mocked, so the caller is fabricated and
        # ``human_review_required=False`` (the one tenant fact this case depends on) stands
        # in for the row: the impl reads it off ``identity.tenant`` in production too.
        identity = fabricated_account_identity(principal_id="principal_1", human_review_required=False)

        # Build a mock UoW that provides session via context manager
        session = MagicMock()
        # Return empty product list so product is "not found"
        scalars_result = MagicMock()
        scalars_result.all.return_value = []
        scalars_result.first.return_value = None
        session.scalars.return_value = scalars_result

        mock_uow = MagicMock()
        mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
        mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
        mock_uow.idempotency_attempts.count_active.return_value = (0, None)
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=None)
        mock_uow.session = session
        mock_media_buys = MagicMock()
        mock_media_buys.get_by_principal.return_value = []
        mock_uow.media_buys = mock_media_buys

        with (
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.get_context_manager") as mock_ctx_mgr,
            patch("src.core.database.repositories.MediaBuyUoW", return_value=mock_uow),
        ):
            mock_princ = MagicMock()
            mock_princ.principal_id = "principal_1"
            mock_princ.name = "Test Buyer"

            ctx_mgr = MagicMock()
            ctx_mgr.create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr

            # Missing product_ids raise the typed AdCPProductNotFoundError.
            with pytest.raises(AdCPProductNotFoundError) as excinfo:
                await _create_media_buy_impl(req, identity=identity)

        exc = excinfo.value
        assert exc.error_code == "PRODUCT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_max_daily_spend_exceeded(self):
        """UC-002-V02 / BR-RULE-012: daily spend > max rejected.

        Spec: UNSPECIFIED (implementation-defined spend cap enforcement; spec has no daily cap concept)
        Ported from test_create_media_buy_behavioral.py::test_max_daily_spend_exceeded
        Covers: UC-002-EXT-K-01
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # 7 day flight, $7000 budget = $1000/day; cap = $500 -> should fail
        req = _make_request(
            packages=[
                {"product_id": "prod_1", "budget": 7000.0, "pricing_option_id": "cpm_usd_fixed"},
            ]
        )
        product = _mock_product("prod_1")

        # Currency limit with tight daily cap
        cl = MagicMock()
        cl.max_daily_package_spend = Decimal("500")
        cl.min_package_budget = None

        # An AccountIdentity, which is what _create_media_buy_impl DECLARES: its DTO puts
        # ``account`` in /required, so the boundary always resolves one and the identity a
        # bare make_identity() returns -- ``account=None`` -- is a caller the controller
        # can never receive. The database here is mocked, so the caller is fabricated and
        # ``human_review_required=False`` (the one tenant fact this case depends on) stands
        # in for the row: the impl reads it off ``identity.tenant`` in production too.
        identity = fabricated_account_identity(principal_id="principal_1", human_review_required=False)

        # Build a mock UoW that provides session via context manager
        session = MagicMock()
        # .all() returns products; .first() returns currency_limit then None
        all_mock = MagicMock()
        all_mock.all.return_value = [product]
        first_mock = MagicMock(side_effect=[cl, None])
        scalars_result = MagicMock()
        scalars_result.all = all_mock.all
        scalars_result.first = first_mock
        session.scalars.return_value = scalars_result

        mock_uow = MagicMock()
        mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
        mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
        mock_uow.idempotency_attempts.count_active.return_value = (0, None)
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=None)
        mock_uow.session = session
        mock_media_buys = MagicMock()
        mock_media_buys.get_by_principal.return_value = []
        mock_uow.media_buys = mock_media_buys

        with (
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.get_context_manager") as mock_ctx_mgr,
            patch("src.core.database.repositories.MediaBuyUoW", return_value=mock_uow),
        ):
            mock_princ = MagicMock()
            mock_princ.principal_id = "principal_1"
            mock_princ.name = "Test Buyer"

            ctx_mgr = MagicMock()
            ctx_mgr.create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr

            with pytest.raises(AdCPBudgetExceededError) as _ei:
                await _create_media_buy_impl(req=req, identity=identity)
            # The identifier is STRUCTURED now: it lives in details/field, not in prose.

    def test_pricing_option_xor_both_rejected(self):
        """UC-002-V03 / BR-RULE-006: both fixed_price and floor_price rejected.

        Spec: CONFIRMED -- cpm-option.json description implies XOR; Pydantic validator enforces it
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/pricing-options/cpm-option.json
        Ported from test_create_media_buy_behavioral.py::test_both_fixed_price_and_floor_price_rejected
        Covers: UC-002-EXT-N-06
        """
        with pytest.raises(ValidationError):
            PricingOption(
                pricing_model="cpm",
                currency="USD",
                fixed_price=5.0,
                floor_price=2.0,
            )

    def test_pricing_option_xor_neither_rejected(self):
        """UC-002-V04 / BR-RULE-006: neither fixed_price nor floor_price rejected.

        Spec: CONFIRMED -- cpm-option.json description implies XOR; Pydantic validator enforces it
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/pricing-options/cpm-option.json
        Ported from test_create_media_buy_behavioral.py::test_neither_fixed_price_nor_floor_price_rejected
        Covers: UC-002-EXT-N-07
        """
        with pytest.raises(ValidationError):
            PricingOption(
                pricing_model="cpm",
                currency="USD",
            )

    def test_ext_fields_roundtrip(self):
        """UC-002-V06: ext fields preserved through create flow.

        Spec: CONFIRMED -- create-media-buy-request.json and response both have ext field
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-request.json
        Priority: P0
        Type: unit
        Source: UC-002,
        Covers: UC-002-UPG-05
        """
        req = _make_request(ext={"custom_key": "custom_value"})
        assert req.ext is not None

        # ext must survive in CreateMediaBuySuccess too
        resp = _make_success(
            media_buy_id="mb_1",
            ext={"custom_key": "custom_value"},
        )
        dumped = resp.model_dump()
        assert dumped.get("ext") is not None
        assert dumped["ext"]["custom_key"] == "custom_value"

    def test_account_accepted_at_boundary(self):
        """UC-002-V07: account field accepted by schema (AccountReference).

        Spec: CONFIRMED -- adcp 3.9: create-media-buy-request.json uses 'account'
        (AccountReference) instead of 'account_id' (string).
        Priority: P1
        Type: unit
        Source: UC-002,
        Covers: UC-002-UPG-06
        """
        req = _make_request(account={"account_id": "acc_123"})
        assert req.account is not None

    def test_zero_budget_rejected(self):
        """UC-002-V08: total budget <= 0 rejected.

        Spec: CONFIRMED -- package-request.json budget has "minimum": 0 (zero technically valid)
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-request.json
        Priority: P1
        Type: unit
        Source: UC-002 main flow, BR-RULE-008
        Covers: UC-002-EXT-A-01
        """
        # A request with zero budget for all packages should be rejected
        # (at validation time or _impl time)
        req = _make_request(packages=[{"product_id": "prod_1", "budget": 0, "pricing_option_id": "cpm_usd_fixed"}])
        assert req.get_total_budget() == 0

    def test_missing_start_time_rejected(self):
        """UC-002-V10: missing start_time rejected.

        Spec: CONFIRMED -- create-media-buy-request.json required: [..., "start_time", "end_time"]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-request.json
        Priority: P1
        Type: unit
        Source: UC-002 main flow
        Covers: UC-002-EXT-C-04
        """
        with pytest.raises(ValidationError):
            CreateMediaBuyRequest(
                account={"account_id": "acct_test"},
                brand={"domain": "test.com"},
                # start_time omitted
                end_time=_future(8),
                packages=[{"product_id": "p1", "budget": 1000.0}],
                idempotency_key="unit-test-key-starttime-1",  # valid — failure stays scoped to start_time
            )

    def test_end_before_start_accepted_at_schema_level(self):
        """UC-002-V11: end_time <= start_time accepted at schema level (validated at impl level).

        Spec: UNSPECIFIED (spec has no explicit date ordering constraint; implementation-defined)
        adcp 3.12 no longer validates date ordering at the Pydantic model level.
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-013
        Covers: UC-002-EXT-C-02
        """
        # In adcp 3.12, end < start is accepted at schema level; _impl validates this
        req = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "test.com"},
            start_time=_future(10),
            end_time=_future(3),  # end before start
            packages=[{"product_id": "p1", "budget": 1000.0, "pricing_option_id": "cpm_usd_fixed"}],
            idempotency_key="unit-test-key-endstart-01",
        )
        assert req.start_time is not None
        assert req.end_time is not None

    def test_pricing_model_not_offered_rejected(self):
        """UC-002-V13: pricing_model not in product's options rejected.

        Spec: CONFIRMED -- package-request.json requires pricing_option_id referencing product's options
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-request.json
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-006
        Covers: UC-002-EXT-N-01
        """
        from src.core.tools.media_buy_create import _validate_pricing_model_selection

        product = _mock_product("prod_1")  # only has cpm_usd_fixed
        # Package requesting a pricing_option_id not offered by product
        package = MagicMock()
        package.pricing_option_id = "cpc_usd_fixed"  # product only has cpm
        package.bid_price = None
        package.pricing_model = None

        with pytest.raises(AdCPValidationError):
            _validate_pricing_model_selection(
                package=package,
                product=product,
                campaign_currency="USD",
            )

    def test_bid_price_below_floor_rejected(self):
        """UC-002-V14: auction bid_price below floor_price rejected.

        Spec: CONFIRMED -- cpm-option.json floor_price description: "Bids below this value will be rejected"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/pricing-options/cpm-option.json
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-006
        Covers: UC-002-EXT-N-04
        """
        from src.core.tools.media_buy_create import _validate_pricing_model_selection

        pricing_option = PricingOptionFactory.build(
            is_fixed=False,  # auction
            rate=None,
            price_guidance={"floor": "5.00"},
            min_spend_per_package=None,
        )

        product = MagicMock()
        product.product_id = "prod_1"
        product.name = "Test Product"
        product.pricing_options = [pricing_option]

        package = MagicMock()
        package.pricing_option_id = "cpm_usd_auction"
        package.bid_price = 2.0  # below floor of 5.0
        package.pricing_model = None

        with pytest.raises(AdCPValidationError) as _ei:
            _validate_pricing_model_selection(
                package=package,
                product=product,
                campaign_currency="USD",
            )
        # The identifier is STRUCTURED now: details/field, not prose.

    def test_budget_below_minimum_spend_rejected(self):
        """UC-002-V15: package budget below min_spend_per_package rejected.

        Spec: CONFIRMED -- cpm-option.json has min_spend_per_package field
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/pricing-options/cpm-option.json
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-011
        Covers: UC-002-CC-MINIMUM-SPEND-PER-02
        """
        from src.core.tools.media_buy_create import _validate_pricing_model_selection

        pricing_option = PricingOptionFactory.build(min_spend_per_package=Decimal("1000"))

        product = MagicMock()
        product.product_id = "prod_1"
        product.name = "Test Product"
        product.pricing_options = [pricing_option]

        package = MagicMock()
        package.pricing_option_id = "cpm_usd_fixed"
        package.bid_price = None
        package.pricing_model = None
        package.budget = 500.0  # below min_spend of 1000

        with pytest.raises(AdCPValidationError) as _ei:
            _validate_pricing_model_selection(
                package=package,
                product=product,
                campaign_currency="USD",
            )
        # The identifier is STRUCTURED now: details/field, not prose.


class TestBuildAdapterAssetFormatFallback:
    """_build_adapter_asset_from_creative format-spec fallback ( TQ-04).

    Cache-miss (None) falls back to format_resolver.get_format; a genuinely
    unknown format proceeds with no spec (raw-data extraction); a typed
    transient AdCPSalesAgentError propagates from EITHER fetch — never degraded into a
    missing-spec asset error.
    """

    def _creative(self):
        creative = MagicMock()
        creative.creative_id = "c_fb"
        creative.format = "display_300x250"
        creative.agent_url = "https://creative.adcontextprotocol.org"
        creative.name = "FB"
        creative.data = {"url": "https://example.com/a.jpg", "width": 300, "height": 250}
        return creative

    def test_cache_miss_uses_format_resolver_fallback(self):
        from src.core.security.outbound_http import CounterpartyUrl
        from src.core.tools.media_buy_create import _build_adapter_asset_from_creative

        spec = MagicMock()
        with (
            patch("src.core.tools.media_buy_create._get_format_spec_sync", return_value=None),
            patch("src.core.format_resolver.get_format", return_value=spec) as resolver,
        ):
            asset, err = _build_adapter_asset_from_creative(
                self._creative(), [{"package_id": "p1", "weight": 100}], tenant_id="t1"
            )
        resolver.assert_called_once_with(
            "display_300x250",
            agent_url="https://creative.adcontextprotocol.org",
            tenant_id="t1",
            product_id=None,
            provenance=CounterpartyUrl(field=None),
        )
        assert err is None and asset is not None

    def test_unknown_format_proceeds_without_spec(self):
        from src.core.exceptions import AdCPFormatNotFoundError
        from src.core.tools.media_buy_create import _build_adapter_asset_from_creative

        with (
            patch("src.core.tools.media_buy_create._get_format_spec_sync", return_value=None),
            patch("src.core.format_resolver.get_format", side_effect=AdCPFormatNotFoundError()),
        ):
            asset, err = _build_adapter_asset_from_creative(
                self._creative(), [{"package_id": "p1", "weight": 100}], tenant_id="t1"
            )
        # Raw creative data carries url/width/height, so the asset still builds.
        assert err is None and asset is not None
        assert asset["url"] == "https://example.com/a.jpg"

    def test_transient_error_from_cached_fetch_propagates_without_fallback(self):
        from src.core.exceptions import AdCPRateLimitError
        from src.core.tools.media_buy_create import _build_adapter_asset_from_creative

        with (
            patch(
                "src.core.tools.media_buy_create._get_format_spec_sync",
                side_effect=AdCPRateLimitError(),
            ),
            patch("src.core.format_resolver.get_format") as resolver,
        ):
            with pytest.raises(AdCPRateLimitError):
                _build_adapter_asset_from_creative(
                    self._creative(), [{"package_id": "p1", "weight": 100}], tenant_id="t1"
                )
        resolver.assert_not_called()

    def test_transient_error_from_resolver_fallback_propagates(self):
        from src.core.exceptions import AdCPServiceUnavailableError
        from src.core.tools.media_buy_create import _build_adapter_asset_from_creative

        with (
            patch("src.core.tools.media_buy_create._get_format_spec_sync", return_value=None),
            patch(
                "src.core.format_resolver.get_format",
                side_effect=AdCPServiceUnavailableError(),
            ),
        ):
            with pytest.raises(AdCPServiceUnavailableError):
                _build_adapter_asset_from_creative(
                    self._creative(), [{"package_id": "p1", "weight": 100}], tenant_id="t1"
                )

    def test_fallback_forwards_the_same_provenance_as_the_first_fetch(self):
        """The fallback dial must carry the SAME provenance as the cached-fetch dial.

        ``_build_adapter_asset_from_creative`` dials the SAME buyer-supplied
        ``creative.agent_url`` twice: once through ``_get_format_spec_sync``
        (already passes ``CounterpartyUrl(field=None)``), and again through
        ``format_resolver.get_format`` when the first dial misses. Omitting
        provenance on the second dial silently reclassifies a buyer-chosen URL
        as operator configuration, so a subsequent refusal on that URL comes
        back CONFIGURATION_ERROR/terminal instead of VALIDATION_ERROR/correctable
        (salesagent-6gpt.1 diff-review round 1 BLOCKING finding, proven
        load-bearing by round 2's executed trace: the fix routes the same
        provenance through ``format_resolver.get_format`` ->
        ``fetch_format_spec`` -> ``registry.get_format`` ->
        ``get_formats_for_agent`` -> ``is_counterparty`` -> the egress seam).

        Grades the WIRING at the ``fetch_format_spec`` boundary — the frame
        the diff-review finding was actually about — rather than a live seam
        refusal, which would make this test's outcome depend on the
        ``ADCP_TESTING`` reference-format short-circuit two frames further
        down (an environment detail unrelated to what this regression is).
        """
        from src.core.security.outbound_http import CounterpartyUrl
        from src.core.tools.media_buy_create import _build_adapter_asset_from_creative

        with (
            patch("src.core.tools.media_buy_create._get_format_spec_sync", return_value=None),
            patch("src.core.format_resolver.fetch_format_spec", return_value=None) as fetch_spec,
        ):
            _build_adapter_asset_from_creative(self._creative(), [{"package_id": "p1", "weight": 100}], tenant_id="t1")

        fetch_spec.assert_called_once_with(
            "https://creative.adcontextprotocol.org",
            "display_300x250",
            provenance=CounterpartyUrl(field=None),
        )


class TestCreateMediaBuyCreativeValidation:
    """UC-002 creative validation: pre-adapter creative checks."""

    def test_creative_missing_url_rejected(self):
        """UC-002-C01: reference creative missing URL raises INVALID_CREATIVES.

        Spec: UNSPECIFIED (implementation-defined creative pre-validation)
        Ported from test_create_media_buy_behavioral.py::test_creative_missing_url_raises_invalid_creatives
        Covers: UC-002-EXT-G-01
        """
        from src.core.tools.media_buy_create import _validate_creatives_before_adapter_call

        # Build a creative in DB that has no URL in its data
        mock_creative = MagicMock()
        mock_creative.creative_id = "c_1"
        mock_creative.format = "display_300x250"
        mock_creative.agent_url = "http://agent.test"
        mock_creative.data = {}  # no media_url

        # Build a mock format spec (reference format, no output_format_ids)
        mock_format_spec = MagicMock()
        mock_format_spec.output_format_ids = None

        package = MagicMock()
        package.creative_ids = ["c_1"]
        package.package_id = "pkg_1"

        with (
            patch("src.core.tools.media_buy_create._get_format_spec_sync", return_value=mock_format_spec),
            patch("src.core.tools.media_buy_create.extract_media_url_and_dimensions", return_value=(None, None, None)),
        ):
            session = MagicMock()
            session.scalars.return_value.all.return_value = [mock_creative]

            # VALIDATION_ERROR: a stored creative missing its required assets
            # "violates business rules beyond schema validation" (3.1.1
            # enums/error-code.json). Not CREATIVE_REJECTED — no policy review runs here.
            with pytest.raises(AdCPValidationError) as exc_info:
                _validate_creatives_before_adapter_call([package], "test_tenant", "test_principal", session=session)

            assert exc_info.value.details.reasons is not None

    def test_creative_error_state_rejected(self):
        """UC-002-C02: creative with status=error rejected.

        Spec: UNSPECIFIED (implementation-defined creative state validation)
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-026
        Covers: UC-002-CC-CREATIVE-ASSIGNMENT-VALIDATION-01
        """
        from src.core.tools.media_buy_create import _validate_creatives_before_adapter_call

        mock_creative = MagicMock()
        mock_creative.creative_id = "c_err"
        mock_creative.format = "display_300x250"
        mock_creative.agent_url = "http://agent.test"
        mock_creative.data = {}
        mock_creative.status = "error"

        package = MagicMock()
        package.creative_ids = ["c_err"]
        package.package_id = "pkg_1"

        session = MagicMock()
        session.scalars.return_value.all.return_value = [mock_creative]

        # INVALID_STATE: "Operation is not permitted for the resource's current status"
        # (3.1.1 enums/error-code.json). AdCPGoneError is this repo's carrier for it, and
        # update_media_buy's gate splits terminal state from the field/format failures
        # the same way. The STATE travels per creative, as a problem, not as a sentence.
        with pytest.raises(AdCPGoneError) as exc_info:
            _validate_creatives_before_adapter_call([package], "test_tenant", "test_principal", session=session)

        problems = exc_info.value.details.problems or []
        assert [(p.subject_type, p.subject_id, p.rejected_value) for p in problems] == [
            ("creative", mock_creative.creative_id, mock_creative.status)
        ]

    def test_creative_rejected_state_rejected(self):
        """UC-002-C03: creative with status=rejected rejected.

        Spec: UNSPECIFIED (implementation-defined creative state validation)
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-026
        Covers: UC-002-CC-CREATIVE-ASSIGNMENT-VALIDATION-02
        """
        from src.core.tools.media_buy_create import _validate_creatives_before_adapter_call

        mock_creative = MagicMock()
        mock_creative.creative_id = "c_rej"
        mock_creative.format = "display_300x250"
        mock_creative.agent_url = "http://agent.test"
        mock_creative.data = {}
        mock_creative.status = "rejected"

        package = MagicMock()
        package.creative_ids = ["c_rej"]
        package.package_id = "pkg_1"

        session = MagicMock()
        session.scalars.return_value.all.return_value = [mock_creative]

        # INVALID_STATE: "Operation is not permitted for the resource's current status"
        # (3.1.1 enums/error-code.json). AdCPGoneError is this repo's carrier for it, and
        # update_media_buy's gate splits terminal state from the field/format failures
        # the same way. The STATE travels per creative, as a problem, not as a sentence.
        with pytest.raises(AdCPGoneError) as exc_info:
            _validate_creatives_before_adapter_call([package], "test_tenant", "test_principal", session=session)

        problems = exc_info.value.details.problems or []
        assert [(p.subject_type, p.subject_id, p.rejected_value) for p in problems] == [
            ("creative", mock_creative.creative_id, mock_creative.status)
        ]

    def test_creative_format_mismatch_rejected(self):
        """UC-002-C04: creative format not matching product format rejected.

        Spec: UNSPECIFIED (implementation-defined creative format compatibility check)
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-026
        Covers: UC-002-EXT-P-01
        """
        from src.core.tools.media_buy_create import _validate_creatives_before_adapter_call

        # Creative has format "video_640x480" but product only accepts "display_300x250"
        mock_creative = MagicMock()
        mock_creative.creative_id = "c_mismatch"
        mock_creative.format = "video_640x480"
        mock_creative.agent_url = "http://agent.test"
        mock_creative.data = {"media_url": "http://example.com/video.mp4", "width": 640, "height": 480}
        mock_creative.status = "approved"

        mock_format_spec = MagicMock()
        mock_format_spec.output_format_ids = None  # reference format

        # Product only accepts display_300x250
        mock_product = MagicMock()
        mock_product.product_id = "prod_display"
        mock_product.format_ids = [{"agent_url": "http://agent.test", "id": "display_300x250"}]

        package = MagicMock()
        package.creative_ids = ["c_mismatch"]
        package.package_id = "pkg_1"
        package.product_id = "prod_display"

        with (
            patch("src.core.tools.media_buy_create._get_format_spec_sync", return_value=mock_format_spec),
            patch(
                "src.core.tools.media_buy_create.extract_media_url_and_dimensions",
                return_value=("http://example.com/video.mp4", 640, 480),
            ),
        ):
            session = MagicMock()
            # The two lookups reach the session by DIFFERENT methods, because their
            # repositories do: CreativeRepository.get_by_ids uses session.scalars(...),
            # while ProductRepository.list_by_ids eager-loads pricing options and so goes
            # through session.execute(...).unique().scalars().all(). Feeding both from one
            # scalars side_effect list (the previous setup) left the product lookup reading
            # an unconfigured MagicMock, which iterates EMPTY -- so the accepted-format set
            # came back empty, the format check skipped every package, and this case passed
            # nothing to assert on while claiming to grade a mismatch rejection.
            creative_result = MagicMock()
            creative_result.all.return_value = [mock_creative]
            session.scalars.return_value = creative_result
            session.execute.return_value.unique.return_value.scalars.return_value.all.return_value = [mock_product]

            with pytest.raises(AdCPValidationError) as exc_info:
                _validate_creatives_before_adapter_call([package], "test_tenant", "test_principal", session=session)

            assert exc_info.value.details.reasons is not None

    def test_generative_creatives_skip_validation(self):
        """UC-002-C05: generative formats (with output_format_ids) not pre-validated.

        Spec: UNSPECIFIED (implementation-defined creative validation bypass)
        Priority: P2
        Type: unit
        Source: UC-002
        Covers: UC-002-ALT-WITH-INLINE-CREATIVES-03
        """
        from src.core.tools.media_buy_create import _validate_creatives_before_adapter_call

        # Generative creative: has output_format_ids on its format spec
        mock_creative = MagicMock()
        mock_creative.creative_id = "c_gen"
        mock_creative.format = "generative_video"
        mock_creative.agent_url = "http://agent.test"
        mock_creative.data = {}  # No media_url -- but generative should skip validation

        mock_format_spec = MagicMock()
        mock_format_spec.output_format_ids = ["display_300x250"]  # Non-None = generative

        package = MagicMock()
        package.creative_ids = ["c_gen"]
        package.package_id = "pkg_1"

        with patch("src.core.tools.media_buy_create._get_format_spec_sync", return_value=mock_format_spec):
            session = MagicMock()
            session.scalars.return_value.all.return_value = [mock_creative]

            # Should NOT raise -- generative creatives are skipped
            _validate_creatives_before_adapter_call([package], "test_tenant", "test_principal", session=session)

    def test_multiple_creative_errors_accumulated(self):
        """UC-002-C06: all creative validation errors collected before raising.

        Spec: UNSPECIFIED (implementation-defined error accumulation pattern)
        Priority: P2
        Type: unit
        Source: UC-002
        Covers: UC-002-EXT-G-04
        """
        from src.core.tools.media_buy_create import _validate_creatives_before_adapter_call

        # Two reference creatives, both missing URL
        mock_creative_1 = MagicMock()
        mock_creative_1.creative_id = "c_1"
        mock_creative_1.format = "display_300x250"
        mock_creative_1.agent_url = "http://agent.test"
        mock_creative_1.data = {}

        mock_creative_2 = MagicMock()
        mock_creative_2.creative_id = "c_2"
        mock_creative_2.format = "display_728x90"
        mock_creative_2.agent_url = "http://agent.test"
        mock_creative_2.data = {}

        mock_format_spec = MagicMock()
        mock_format_spec.output_format_ids = None  # Reference format

        package = MagicMock()
        package.creative_ids = ["c_1", "c_2"]
        package.package_id = "pkg_1"

        with (
            patch("src.core.tools.media_buy_create._get_format_spec_sync", return_value=mock_format_spec),
            patch(
                "src.core.tools.media_buy_create.extract_media_url_and_dimensions",
                return_value=(None, None, None),
            ),
        ):
            session = MagicMock()
            session.scalars.return_value.all.return_value = [mock_creative_1, mock_creative_2]

            with pytest.raises(AdCPValidationError) as exc_info:
                _validate_creatives_before_adapter_call([package], "test_tenant", "test_principal", session=session)

            # Both errors should be accumulated in a single exception
            assert exc_info.value.details.reasons is not None
            creative_errors = exc_info.value.details.reasons or []
            assert len(creative_errors) >= 2


class TestCreateMediaBuyStatusDetermination:
    """UC-002 status determination: _determine_media_buy_status logic."""

    def test_completed_when_past_end(self):
        """UC-002-ST01: past end_time -> completed.

        Spec: CONFIRMED -- media-buy-status.json: completed = "Media buy has finished running"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        Covers: UC-002-MAIN-21
        """
        from src.core.tools.media_buy_create import _determine_media_buy_status

        now = datetime(2026, 4, 1, tzinfo=UTC)
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 31, tzinfo=UTC)
        assert _determine_media_buy_status(False, True, True, start, end, now) == "completed"

    def test_active_when_in_flight_with_creatives(self):
        """UC-002-ST02: in-flight with approved creatives -> active.

        Spec: CONFIRMED -- media-buy-status.json: active = "Media buy is currently running"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        Covers: UC-002-MAIN-21
        """
        from src.core.tools.media_buy_create import _determine_media_buy_status

        now = datetime(2026, 3, 15, tzinfo=UTC)
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 31, tzinfo=UTC)
        assert _determine_media_buy_status(False, True, True, start, end, now) == "active"

    def test_pending_when_manual_approval_required(self):
        """UC-002-ST03: manual approval required -> pending_start.

        Spec: CONFIRMED -- media-buy-status.json: pending_start = "Media buy created but not yet activated"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        Covers: UC-002-ALT-MANUAL-APPROVAL-REQUIRED-03
        """
        from src.core.tools.media_buy_create import _determine_media_buy_status

        now = datetime(2026, 3, 15, tzinfo=UTC)
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 31, tzinfo=UTC)
        assert _determine_media_buy_status(True, True, True, start, end, now) == "pending_start"

    def test_pending_when_missing_creatives(self):
        """UC-002-ST04: no creatives -> pending_creatives.

        Spec: CONFIRMED -- media-buy-status.json: pending_creatives = "Media buy awaiting creative assets"
        https://github.com/adcontextprotocol/adcp/blob/main/schemas/enums/media-buy-status.json
        Covers: UC-002-MAIN-21
        """
        from src.core.tools.media_buy_create import _determine_media_buy_status

        now = datetime(2026, 3, 15, tzinfo=UTC)
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 31, tzinfo=UTC)
        assert _determine_media_buy_status(False, False, False, start, end, now) == "pending_creatives"

    def test_pending_when_before_start(self):
        """UC-002-ST05: before start_time -> pending_start.

        Spec: CONFIRMED -- media-buy-status.json: pending_start = "Media buy created but not yet activated"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        Covers: UC-002-MAIN-21
        """
        from src.core.tools.media_buy_create import _determine_media_buy_status

        now = datetime(2026, 2, 15, tzinfo=UTC)
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 31, tzinfo=UTC)
        assert _determine_media_buy_status(False, True, True, start, end, now) == "pending_start"


class TestIdempotencyKeyRequired:
    """CreateMediaBuyRequest.idempotency_key is required and spec-shaped (AdCP 3.0.1).

    The create-side optional override was removed: the field inherits the library's
    required str with MinLen(16) + pattern ^[A-Za-z0-9_.:-]{16,255}$. A missing key
    rejects at the schema boundary (storyboard missing_key step).

    The last line here said "update_media_buy's enforcement is a deliberate fast-follow and
    stays optional". That fast-follow landed in salesagent-prkv.28, which made the key AND
    account required on update_media_buy and sync_creatives. create_media_buy's ``account``
    was the one instance left behind and is required now too, so all three tools agree and
    there is no fast-follow outstanding.
    """

    def _kwargs(self, **overrides):
        base = {
            "brand": {"domain": "key-test.example.com"},
            "packages": [{"product_id": "prod_1", "budget": 1000, "pricing_option_id": "po_1"}],
            "start_time": datetime(2026, 6, 1, tzinfo=UTC),
            "end_time": datetime(2026, 6, 30, tzinfo=UTC),
            "account": {"account_id": "acct_test"},
        }
        base.update(overrides)
        return base

    def test_missing_key_rejected_as_field_required(self):
        with pytest.raises(ValidationError) as exc_info:
            CreateMediaBuyRequest(**self._kwargs())
        assert any(e["loc"] == ("idempotency_key",) and e["type"] == "missing" for e in exc_info.value.errors())

    def test_short_key_rejected(self):
        """MinLen(16) inherited from the library field."""
        with pytest.raises(ValidationError) as exc_info:
            CreateMediaBuyRequest(**self._kwargs(idempotency_key="too-short"))
        assert any(e["loc"] == ("idempotency_key",) for e in exc_info.value.errors())

    def test_invalid_characters_rejected(self):
        """Pattern ^[A-Za-z0-9_.:-]{16,255}$ inherited from the library field."""
        with pytest.raises(ValidationError) as exc_info:
            CreateMediaBuyRequest(**self._kwargs(idempotency_key="spaces are not allowed!"))
        assert any(e["loc"] == ("idempotency_key",) for e in exc_info.value.errors())

    def test_spec_shaped_key_accepted(self):
        req = CreateMediaBuyRequest(**self._kwargs(idempotency_key="buy-2026-q3-abc123def"))
        assert req.idempotency_key == "buy-2026-q3-abc123def"

    def test_update_request_requires_the_key_too(self):
        """The update-side "fast-follow" is done: the key is REQUIRED there as well.

        This asserted the opposite -- that update_media_buy "stays optional" -- which was
        true only as a deferral, not as a contract. AdCP 3.1.1
        media-buy/update-media-buy-request.json /required is
        [idempotency_key, account, media_buy_id], and the field exists so that a retry after
        a lost response is at-most-once. Leaving it optional meant a retried update executed
        twice, so the deferral had a live cost and the test pinned it in place.
        """
        with pytest.raises(ValidationError) as exc_info:
            UpdateMediaBuyRequest(account={"account_id": "acct_test"}, media_buy_id="mb_update_required")

        assert "idempotency_key" in str(exc_info.value)

    def test_update_request_accepts_a_spec_shaped_key(self):
        """And a well-formed key is accepted -- the requirement is not merely strictness."""
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_update_ok",
        )

        assert req.idempotency_key == "test-idem-key-0001"


# UC-002-MAIN-IDEMPOTENCY (a replayed key returns the original media buy; a new key
# proceeds) is the boundary's behaviour and is graded on the wire by BR-UC-002's
# "v3.1 idempotency_key replay returns existing media buy without re-execution" and its
# sibling in-flight / expired / missing-key scenarios; no ``invoke_tool`` test is kept here.


class TestCreateMediaBuyAdapterInteraction:
    """UC-002 adapter call: _execute_adapter_media_buy_creation behavior.

    test_adapter_error_logged (UC-002-AD01 / UC-002-EXT-J-01) is REMOVED. It had the
    mocked adapter RETURN a ``CreateMediaBuyError`` and asserted the function handed that
    error back. An adapter has no error return any more: commit ecfdd7771 gave
    ``create_media_buy`` the return type ``AdapterCreateResult``, a carrier with
    ``extra="forbid"`` declaring exactly what the tool reads (media_buy_id, packages,
    creative_deadline, platform_line_item_ids) — and no errors[]. An adapter reports
    failure by RAISING, which is the branch the surviving case below grades: a wire error
    model coming back from an adapter is not a case production can reach, and the
    function would fail reading ``response.media_buy_id`` off it rather than returning it.
    """

    def test_adapter_exception_propagates(self):
        """UC-002-AD02: adapter raising exception is re-raised.

        Spec: UNSPECIFIED (implementation-defined adapter error handling)
        Priority: P1
        Type: unit
        Source: UC-002
        Covers: UC-002-EXT-J-03
        """
        from src.core.tools.media_buy_create import _execute_adapter_media_buy_creation

        mock_adapter = MagicMock()
        mock_adapter.create_media_buy.side_effect = RuntimeError("GAM API timeout")

        # Two signature changes, both from the adapter-contract split (ecfdd7771 and
        # a1b79d22d): the adapters read an AdapterCreateRequest projected from the buyer's
        # DTO through one of its two constructors — nothing builds one by hand — and the
        # function takes the one ResolvedIdentity rather than a loose principal, so the
        # tenant and the principal cannot be handed over as a mismatched pair.
        with patch("src.core.tools.media_buy_create.get_adapter", return_value=mock_adapter):
            with pytest.raises(RuntimeError, match="GAM API timeout"):
                _execute_adapter_media_buy_creation(
                    request=AdapterCreateRequest.from_buyer_request(_make_request()),
                    packages=[],
                    start_time=datetime.now(UTC),
                    end_time=datetime.now(UTC) + timedelta(days=7),
                    package_pricing_info={},
                    identity=_make_identity(),
                )

    # test_dry_run_skips_adapter (UC-002-AD03 / UC-002-MAIN-19) is REMOVED. It drove
    # _create_media_buy_impl with an identity whose testing context said dry_run=True and
    # asserted the tool returned a simulated success with a "dry_run_" media_buy_id
    # without reaching the adapter.
    #
    # create_media_buy has no dry-run mode any more. Commit a1b79d22d deleted the whole
    # testing-hook channel: there is no AdCPTestContext, the identity carries no testing
    # context, and "the only dry_run left is the request field on the sync_* tools,
    # implemented as a unit-of-work rollback, and the operator previews in the admin UI".
    # CreateMediaBuyRequest declares no dry_run field, media_buy_create contains no
    # dry-run branch and mints no "dry_run_" id, so there is no way to ask for the
    # behaviour and nothing left to skip the adapter. The surviving dry run — sync_creatives
    # rolling its unit of work back — is a different mechanism on a different tool and is
    # graded with that tool.


# ===========================================================================
# UC-003: UPDATE MEDIA BUY
# ===========================================================================


class TestUpdateMediaBuySchemaCompliance:
    """UC-003 schema: request parsing and field requirements."""

    def test_update_request_accepts_media_buy_id(self):
        """UC-003-S01: media_buy_id accepted as optional field.

        Spec: CONFIRMED -- update-media-buy-request.json requires media_buy_id
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Covers: UC-003-MAIN-01
        """
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            packages=[{"package_id": "pkg_1"}],
        )
        assert req.media_buy_id == "mb_1"

    def test_update_request_parses_iso_datetime_strings(self):
        """UC-003-S02: ISO datetime strings parsed in pre-validator.

        Spec: CONFIRMED -- update-media-buy-request.json start_time refs start-timing.json, end_time is date-time
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Covers: UC-003-ALT-UPDATE-TIMING-01
        """
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            start_time="2026-03-01T00:00:00+00:00",
            end_time="2026-03-31T00:00:00+00:00",
        )
        assert isinstance(req.start_time, datetime)
        assert isinstance(req.end_time, datetime)

    def test_update_request_accepts_asap_start_time(self):
        """UC-003-S03: start_time='asap' valid per AdCP spec.

        Spec: CONFIRMED -- update-media-buy-request.json start_time refs start-timing.json (oneOf: "asap" | datetime)
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/start-timing.json
        Covers: UC-003-ALT-UPDATE-TIMING-02
        """
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            start_time="asap",
        )
        assert req.start_time == "asap"

    def test_update_buyer_campaign_ref_roundtrip(self):
        """UC-003-S04: buyer_campaign_ref preserved in update response.

        Spec: CONFIRMED -- buyer_campaign_ref is a create-time immutable field (present in
        create-media-buy-request.json and core/media-buy.json, absent from update-media-buy-request.json
        by design). Update response returns the full MediaBuy entity which includes it.
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/schemas/cache/core/media-buy.json
        Priority: P0
        Type: unit
        Source: UC-003,
        Covers: UC-003-MAIN-11
        """
        # buyer_campaign_ref is a create-time field, not an update field.
        # GetMediaBuysMediaBuy (list response) should preserve it.
        from adcp.types import MediaBuyStatus

        mb = GetMediaBuysMediaBuy(
            media_buy_id="mb_1",
            buyer_campaign_ref="camp-ref-123",
            status=MediaBuyStatus.active,
            currency="USD",
            total_budget=5000.0,
            packages=[],
            # Spec-required on media_buys[] at AdCP 3.1.1. Supplied here because the
            # model is now grounded on the library item type and enforces them; this
            # test's fixture predates that and was silently constructing an item the
            # pinned schema would reject.
            confirmed_at=datetime(2025, 2, 1, tzinfo=UTC),
            revision=1,
        )
        dumped = mb.model_dump()
        assert dumped.get("buyer_campaign_ref") == "camp-ref-123"

    def test_update_ext_fields_roundtrip(self):
        """UC-003-S05: ext fields preserved through update flow.

        Spec: CONFIRMED -- update-media-buy-request.json and response both have ext field
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Priority: P0
        Type: unit
        Source: UC-003,
        Covers: UC-003-MAIN-12
        """
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            ext={"custom_key": "custom_value"},
        )
        assert req.ext is not None
        dumped = req.model_dump()
        assert dumped.get("ext") is not None
        assert dumped["ext"]["custom_key"] == "custom_value"


class TestUpdateMediaBuyResponseShapes:
    """UC-003 response shape: UpdateMediaBuySuccess/Error serialization."""

    def test_success_response_includes_affected_packages(self):
        """UC-003-R01: affected_packages populated on success.

        Spec: CONFIRMED -- update-media-buy-response.json success has affected_packages property
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        Ported from test_update_media_buy_affected_packages.py::test_response_serialization_includes_affected_packages
        Covers: UC-003-MAIN-09
        """
        # The buyer's envelope, so sync_success with the row's revision — not the deleted
        # carrier(), whose job (standing in for an adapter response) belongs to
        # AdapterUpdateResult now.
        resp = UpdateMediaBuySuccess.sync_success(
            media_buy_id="mb_1",
            revision=1,
            affected_packages=[
                AffectedPackage(package_id="pkg_1", paused=False),
            ],
        )
        dumped = resp.model_dump()
        assert "affected_packages" in dumped
        assert len(dumped["affected_packages"]) == 1
        assert dumped["affected_packages"][0]["package_id"] == "pkg_1"

    def test_error_response_atomic(self):
        """UC-003-R02 / BR-RULE-018: error has no success fields.

        Spec: CONFIRMED -- update-media-buy-response.json error: not anyOf [media_buy_id, affected_packages]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        Covers: UC-003-EXT-O-05
        """
        from src.core.schemas import Error

        resp = UpdateMediaBuyError(status="failed", errors=[Error(code="VALIDATION_ERROR", message="fail")])
        dumped = resp.model_dump()
        assert "errors" in dumped
        # success fields should not be present or should be None
        assert dumped.get("affected_packages") is None

    def test_affected_packages_excludes_internal_fields(self):
        """UC-003-R03: changes_applied and buyer_package_ref excluded.

        Spec: UNSPECIFIED (implementation-defined internal field exclusion)
        Ported from test_update_media_buy_affected_packages.py pattern.
        Covers: UC-003-MAIN-09
        """
        pkg = AffectedPackage(
            package_id="pkg_1",
            paused=False,
            changes_applied={"creative_ids": ["c1"]},
            buyer_package_ref="bpr_1",
        )
        dumped = pkg.model_dump()
        assert "changes_applied" not in dumped
        assert "buyer_package_ref" not in dumped


class TestUpdateMediaBuyMainFlow:
    """UC-003 main flow: package budget update (auto-applied)."""

    def test_package_budget_update_via_media_buy_id(self):
        """UC-003-MF01/MF02: media_buy_id resolves to media buy, update succeeds.

        Spec: media_buy_id is the sole update identifier
        Priority: P0
        Type: unit
        Source: UC-003, BR-RULE-021
        Covers: UC-003-MAIN-02
        """
        from src.core.schemas import AdCPPackageUpdate
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_resolved",
            packages=[AdCPPackageUpdate(package_id="pkg_1", budget=3000.0)],
        )
        identity = _make_identity()

        mock_buy = _mock_media_buy(media_buy_id="mb_resolved")
        mock_buy.principal_id = "test_principal"
        mock_buy.currency = "USD"
        mock_buy.start_time = datetime(2026, 3, 1, tzinfo=UTC)
        mock_buy.end_time = datetime(2026, 3, 31, tzinfo=UTC)

        cl = MagicMock()
        cl.max_daily_package_spend = Decimal("5000")
        cl.min_package_budget = None

        # What an adapter hands back is AdapterUpdateResult, not a wire model: commit
        # ecfdd7771 moved the adapters to result types and deleted the carrier()
        # classmethod that let a response stand in for one. The tool builds the buyer's
        # UpdateMediaBuySuccess itself from the re-read row.
        adapter_result = AdapterUpdateResult(
            media_buy_id="mb_resolved",
            affected_packages=[AffectedPackage(package_id="pkg_1", paused=False)],
        )

        # Build mock UoW
        mock_uow = MagicMock()
        mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
        mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
        mock_uow.idempotency_attempts.count_active.return_value = (0, None)
        mock_session = MagicMock()
        mock_uow.session = mock_session
        mock_uow.media_buys = MagicMock()
        mock_currency_limits = MagicMock()
        mock_currency_limits.get_for_currency.return_value = cl
        mock_uow.currency_limits = mock_currency_limits
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW", return_value=mock_uow),
            patch("src.core.database.database_session.get_db_session") as mock_db_inner,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            adapter.update_media_buy.return_value = adapter_result
            mock_adapter.return_value = adapter

            _stub_media_buy_reads(mock_uow.media_buys, mock_buy)
            _stub_media_buy_reads(mock_uow.media_buys, mock_buy)

            result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        assert result.media_buy_id == "mb_resolved"

    def test_partial_update_omitted_fields_unchanged(self):
        """UC-003-MF03: only specified fields update, rest preserved.

        Spec: CONFIRMED -- package-update.json: "Fields not present are left unchanged"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-update.json
        Priority: P0
        Type: unit
        Source: UC-003, BR-RULE-022
        Covers: UC-003-MAIN-03
        """
        from src.core.schemas import AdCPPackageUpdate

        # When only budget is specified, paused and creative_ids should be None (unchanged)
        pkg = AdCPPackageUpdate(package_id="pkg_1", budget=3000.0)
        assert pkg.budget == 3000.0
        assert pkg.paused is None
        assert pkg.creative_ids is None

        # When only paused is specified, budget and creative_ids should be None
        pkg2 = AdCPPackageUpdate(package_id="pkg_1", paused=True)
        assert pkg2.paused is True
        assert pkg2.budget is None
        assert pkg2.creative_ids is None


class TestUpdateMediaBuyPauseResume:
    """UC-003 alt-pause: pause/resume campaign."""

    def test_pause_active_media_buy(self):
        """UC-003-PR01: paused=true on active buy calls adapter with pause action.

        Spec: CONFIRMED -- update-media-buy-request.json has paused: boolean property
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Priority: P0
        Type: unit
        Source: UC-003 alt-pause
        Covers: UC-003-ALT-PAUSE-RESUME-CAMPAIGN-01
        """
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"}, idempotency_key="test-idem-key-0001", media_buy_id="mb_1", paused=True
        )
        identity = _make_identity()

        # The adapter contract (see the note on the first of these), not a wire model.
        adapter_result = AdapterUpdateResult(media_buy_id="mb_1", affected_packages=[])

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            adapter.update_media_buy.return_value = adapter_result
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            mock_uow.session = MagicMock()
            mock_uow.media_buys = MagicMock()
            # State-machine precondition guard needs a non-terminal status
            _stub_mb = MagicMock()
            _stub_mb.status = "active"
            _stub_media_buy_reads(mock_uow.media_buys, _stub_mb)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # Adapter should be called with pause action
        adapter.update_media_buy.assert_called_once_with(
            media_buy_id=ANY, action="pause_media_buy", package_id=ANY, budget=ANY, today=ANY
        )

    def test_resume_paused_media_buy(self):
        """UC-003-PR02: paused=false on paused buy calls adapter with resume action.

        Spec: CONFIRMED -- update-media-buy-request.json paused: false = active
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Priority: P0
        Type: unit
        Source: UC-003 alt-pause
        Covers: UC-003-ALT-PAUSE-RESUME-CAMPAIGN-02
        """
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"}, idempotency_key="test-idem-key-0001", media_buy_id="mb_1", paused=False
        )
        identity = _make_identity()

        # The adapter contract (see the note on the first of these), not a wire model.
        adapter_result = AdapterUpdateResult(media_buy_id="mb_1", affected_packages=[])

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            adapter.update_media_buy.return_value = adapter_result
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            mock_uow.session = MagicMock()
            mock_uow.media_buys = MagicMock()
            # State-machine precondition: 'resume' is only valid from 'paused'
            _stub_mb = MagicMock()
            _stub_mb.status = "paused"
            _stub_media_buy_reads(mock_uow.media_buys, _stub_mb)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        adapter.update_media_buy.assert_called_once_with(
            media_buy_id=ANY, action="resume_media_buy", package_id=ANY, budget=ANY, today=ANY
        )

    def test_pause_skips_budget_validation(self):
        """UC-003-PR03: pause does not trigger currency/budget validation.

        Spec: UNSPECIFIED (implementation-defined validation bypass for pause)
        Priority: P2
        Type: unit
        Source: UC-003 alt-pause
        Covers: UC-003-ALT-PAUSE-RESUME-CAMPAIGN-03
        """
        from src.core.tools.media_buy_update import _update_media_buy_impl

        # Pause request with no budget or date changes should not trigger currency validation
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"}, idempotency_key="test-idem-key-0001", media_buy_id="mb_1", paused=True
        )
        identity = _make_identity()

        # The adapter contract (see the note on the first of these), not a wire model.
        adapter_result = AdapterUpdateResult(media_buy_id="mb_1", affected_packages=[])

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            adapter.update_media_buy.return_value = adapter_result
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            mock_uow.session = MagicMock()
            mock_uow.media_buys = MagicMock()
            # State-machine precondition guard needs a non-terminal status
            _stub_mb = MagicMock()
            _stub_mb.status = "active"
            _stub_media_buy_reads(mock_uow.media_buys, _stub_mb)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            # Should succeed without any CurrencyLimit lookups
            result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # The key assertion: session.scalars should NOT be called for currency limit
        # because pause doesn't change budget or dates
        # (adapter is called directly for pause action)


class TestUpdateMediaBuyTiming:
    """UC-003 alt-timing: update start_time/end_time."""

    def test_valid_date_range_accepted(self):
        """UC-003-T01: valid end > start persists.

        Spec: CONFIRMED -- update-media-buy-request.json has start_time and end_time properties
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Ported from test_update_media_buy_behavioral.py::test_valid_date_range_persists_to_db
        Covers: UC-003-ALT-UPDATE-TIMING-01
        """
        # Schema accepts valid range
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            start_time="2026-03-01T00:00:00+00:00",
            end_time="2026-03-31T00:00:00+00:00",
        )
        assert req.start_time is not None
        assert req.end_time is not None

    def test_end_before_start_returns_error(self):
        """UC-003-T02: end_time <= start_time rejected.

        Spec: UNSPECIFIED (no explicit date ordering in spec; implementation-defined)
        Priority: P1
        Type: unit
        Source: UC-003 ext-e, BR-RULE-013
        Covers: UC-003-EXT-E-02
        """
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            start_time="2026-04-15T00:00:00+00:00",
            end_time="2026-04-01T00:00:00+00:00",  # end before start
        )
        identity = _make_identity()

        mock_buy = _mock_media_buy(media_buy_id="mb_1")
        mock_buy.principal_id = "test_principal"
        mock_buy.start_time = datetime(2026, 3, 1, tzinfo=UTC)
        mock_buy.end_time = datetime(2026, 3, 31, tzinfo=UTC)

        cl = MagicMock()
        cl.max_daily_package_spend = None
        cl.min_package_budget = None

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr

            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            mock_uow_session = MagicMock()
            mock_uow.session = mock_uow_session
            mock_uow.media_buys = MagicMock()
            mock_currency_limits = MagicMock()
            mock_currency_limits.get_for_currency.return_value = cl
            mock_uow.currency_limits = mock_currency_limits
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            # Precondition + currency check + date check
            mock_uow.media_buys.get_by_id.side_effect = [mock_buy, mock_buy, mock_buy]

            from src.core.exceptions import AdCPValidationError

            with pytest.raises(AdCPValidationError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)
            # The identifier is STRUCTURED now: details/field, not prose.

    def test_shortened_flight_recalculates_daily_spend(self):
        """UC-003-T03: shorter flight with same budget may exceed daily cap.

        Spec: UNSPECIFIED (implementation-defined spend cap recalculation)
        Priority: P1
        Type: unit
        Source: UC-003 alt-timing, BR-RULE-012
        Covers: UC-003-ALT-UPDATE-TIMING-04
        """
        from src.core.schemas import AdCPPackageUpdate
        from src.core.tools.media_buy_update import _update_media_buy_impl

        # Shorten flight from 30 days to 2 days, same budget = higher daily spend
        # $5000 / 2 days = $2500/day > max_daily of $500
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            end_time="2026-03-03T00:00:00+00:00",  # much shorter than original
            packages=[AdCPPackageUpdate(package_id="pkg_1", budget=5000.0)],
        )
        identity = _make_identity()

        mock_buy = _mock_media_buy(media_buy_id="mb_1")
        mock_buy.principal_id = "test_principal"
        mock_buy.start_time = datetime(2026, 3, 1, tzinfo=UTC)
        mock_buy.end_time = datetime(2026, 3, 31, tzinfo=UTC)
        mock_buy.currency = "USD"

        cl = MagicMock()
        cl.max_daily_package_spend = Decimal("500")
        cl.min_package_budget = None

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.database.database_session.get_db_session") as mock_db_inner,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            mock_uow_session = MagicMock()
            mock_uow.session = mock_uow_session
            mock_uow.media_buys = MagicMock()
            mock_currency_limits = MagicMock()
            mock_currency_limits.get_for_currency.return_value = cl
            mock_uow.currency_limits = mock_currency_limits
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            _stub_media_buy_reads(mock_uow.media_buys, mock_buy)

            from src.core.exceptions import AdCPBudgetExceededError

            with pytest.raises(AdCPBudgetExceededError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)
            msg = str(exc_info.value).lower()


class TestUpdateMediaBuyCreativeIds:
    """UC-003 alt-creative-ids: replace package creatives via creative_ids."""

    def test_creative_ids_replaces_all(self):
        """UC-003-CI01: creative_ids = replacement, not additive.

        Spec: CONFIRMED -- package-update.json creative_assignments: "Uses replacement semantics"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-update.json
        Priority: P0
        Type: unit
        Source: UC-003 alt-creative-ids, BR-RULE-024
        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-01
        """
        from src.core.schemas import AdCPPackageUpdate
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            packages=[AdCPPackageUpdate(package_id="pkg_1", creative_ids=["c_new1", "c_new2"])],
        )
        identity = _make_identity()

        # Mock DB creative objects
        mock_c1 = MagicMock()
        mock_c1.creative_id = "c_new1"
        mock_c1.status = "approved"
        mock_c1.agent_url = "http://agent.test"
        mock_c1.format = "display_300x250"

        mock_c2 = MagicMock()
        mock_c2.creative_id = "c_new2"
        mock_c2.status = "approved"
        mock_c2.agent_url = "http://agent.test"
        mock_c2.format = "display_300x250"

        # Existing assignment for c_old (should be removed)
        mock_existing_assignment = MagicMock()
        mock_existing_assignment.creative_id = "c_old"

        mock_buy = MagicMock()
        mock_buy.media_buy_id = "mb_1"
        mock_buy.principal_id = "test_principal"
        mock_buy.status = "active"
        mock_buy.approved_at = None

        mock_package = MagicMock()
        mock_package.package_config = {"product_id": "prod_1"}

        mock_product = MagicMock()
        mock_product.format_ids = []
        mock_product.placements = None

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.database.database_session.get_db_session") as mock_db_inner,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            uow_session = MagicMock()
            mock_uow.session = uow_session
            mock_uow.media_buys = MagicMock()
            # State-machine precondition guard needs a non-terminal status
            _stub_mb = MagicMock()
            _stub_mb.status = "active"
            _stub_media_buy_reads(mock_uow.media_buys, _stub_mb)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            # Media buy and package via repo
            _stub_media_buy_reads(mock_uow.media_buys, mock_buy)
            mock_uow.media_buys.get_package.return_value = mock_package

            # Creative existence/status + product format via repositories.
            mock_uow.creatives.get_by_ids.return_value = [mock_c1, mock_c2]
            mock_uow.products.get_by_id.return_value = mock_product

            # The existing-assignment read and the removal both go through the
            # assignments REPOSITORY, not the raw session: a bare session.scalars stub (the
            # previous setup) fed a lookup production no longer makes, so the tool saw no
            # existing assignment, computed an empty removed set, and deleted nothing —
            # the very behaviour this case grades, passed over silently.
            mock_uow.assignments.get_by_media_buy_and_package.return_value = [mock_existing_assignment]

            result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        assert result.affected_packages is not None
        assert len(result.affected_packages) >= 1
        # The old assignment should have been deleted (replacement semantics)
        mock_uow.assignments.delete_row.assert_called_once_with(mock_existing_assignment)

    def test_creative_ids_not_found(self):
        """UC-003-CI02: nonexistent creative_ids returns creatives_not_found.

        Spec: CONFIRMED -- error.json structure for creative validation errors
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/error.json
        Priority: P1
        Type: unit
        Source: UC-003 ext-i
        Covers: UC-003-EXT-I-01
        """
        from src.core.schemas import AdCPPackageUpdate
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            packages=[AdCPPackageUpdate(package_id="pkg_1", creative_ids=["c_nonexistent"])],
        )
        identity = _make_identity()

        mock_buy = MagicMock()
        mock_buy.media_buy_id = "mb_1"
        mock_buy.principal_id = "test_principal"

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.database.database_session.get_db_session") as mock_db_inner,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            uow_session = MagicMock()
            mock_uow.session = uow_session
            mock_uow.media_buys = MagicMock()
            # State-machine precondition guard needs a non-terminal status
            _stub_mb = MagicMock()
            _stub_mb.status = "active"
            _stub_media_buy_reads(mock_uow.media_buys, _stub_mb)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            # Media buy via repo
            _stub_media_buy_reads(mock_uow.media_buys, mock_buy)

            # No creatives found via repository.
            mock_uow.creatives.get_by_ids.return_value = []

            with pytest.raises(AdCPCreativeNotFoundError):
                _update_media_buy_impl(req=req, identity=identity)

    def test_creative_error_state_rejected(self):
        """UC-003-CI03: creative with status=error rejected.

        Spec: UNSPECIFIED (implementation-defined creative state validation)
        Priority: P1
        Type: unit
        Source: UC-003 ext-j, BR-RULE-026
        Covers: UC-003-EXT-J-01
        """
        from src.core.schemas import AdCPPackageUpdate
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            packages=[AdCPPackageUpdate(package_id="pkg_1", creative_ids=["c_err"])],
        )
        identity = _make_identity()

        mock_creative = MagicMock()
        mock_creative.creative_id = "c_err"
        mock_creative.status = "error"
        mock_creative.agent_url = "http://agent.test"
        mock_creative.format = "display_300x250"

        mock_buy = MagicMock()
        mock_buy.media_buy_id = "mb_1"
        mock_buy.principal_id = "test_principal"
        mock_buy.status = "active"
        mock_buy.approved_at = None

        mock_package = MagicMock()
        mock_package.package_config = {"product_id": "prod_1"}

        mock_product = MagicMock()
        mock_product.format_ids = [{"agent_url": "http://agent.test", "id": "display_300x250"}]
        mock_product.name = "Test Product"
        mock_product.placements = None

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.database.database_session.get_db_session") as mock_db_inner,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            uow_session = MagicMock()
            mock_uow.session = uow_session
            mock_uow.media_buys = MagicMock()
            # State-machine precondition guard needs a non-terminal status
            _stub_mb = MagicMock()
            _stub_mb.status = "active"
            _stub_media_buy_reads(mock_uow.media_buys, _stub_mb)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            # Media buy and package via repo
            _stub_media_buy_reads(mock_uow.media_buys, mock_buy)
            mock_uow.media_buys.get_package.return_value = mock_package

            # Creative existence/status + product format via repositories.
            mock_uow.creatives.get_by_ids.return_value = [mock_creative]
            mock_uow.products.get_by_id.return_value = mock_product

            with pytest.raises(AdCPGoneError) as _ei:
                _update_media_buy_impl(req=req, identity=identity)
            # The identifier is STRUCTURED now: details/field, not prose.

    def test_creative_format_mismatch_rejected(self):
        """UC-003-CI04: creative format incompatible with product.

        Spec: UNSPECIFIED (implementation-defined creative format compatibility)
        Priority: P1
        Type: unit
        Source: UC-003 ext-j, BR-RULE-026
        Covers: UC-003-EXT-J-03
        """
        from src.core.schemas import AdCPPackageUpdate
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            packages=[AdCPPackageUpdate(package_id="pkg_1", creative_ids=["c_wrong_fmt"])],
        )
        identity = _make_identity()

        mock_creative = MagicMock()
        mock_creative.creative_id = "c_wrong_fmt"
        mock_creative.status = "approved"
        mock_creative.agent_url = "http://agent.test"
        mock_creative.format = "video_640x480"  # mismatch with product

        mock_buy = MagicMock()
        mock_buy.media_buy_id = "mb_1"
        mock_buy.principal_id = "test_principal"
        mock_buy.status = "active"
        mock_buy.approved_at = None

        mock_package = MagicMock()
        mock_package.package_config = {"product_id": "prod_1"}

        mock_product = MagicMock()
        mock_product.format_ids = [{"agent_url": "http://agent.test", "id": "display_300x250"}]
        mock_product.name = "Test Product"
        mock_product.placements = None

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.database.database_session.get_db_session") as mock_db_inner,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            uow_session = MagicMock()
            mock_uow.session = uow_session
            mock_uow.media_buys = MagicMock()
            # State-machine precondition guard needs a non-terminal status
            _stub_mb = MagicMock()
            _stub_mb.status = "active"
            _stub_media_buy_reads(mock_uow.media_buys, _stub_mb)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            # Media buy and package via repo
            _stub_media_buy_reads(mock_uow.media_buys, mock_buy)
            mock_uow.media_buys.get_package.return_value = mock_package

            # Creative existence/status + product format via repositories.
            mock_uow.creatives.get_by_ids.return_value = [mock_creative]
            mock_uow.products.get_by_id.return_value = mock_product

            with pytest.raises(AdCPValidationError):
                _update_media_buy_impl(req=req, identity=identity)

    def test_change_set_computation(self):
        """UC-003-CI05: [C1,C2,C3] -> [C2,C4] means add C4, remove C1,C3.

        Spec: CONFIRMED -- package-update.json creative_assignments: replacement semantics
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-update.json
        Priority: P1
        Type: unit
        Source: UC-003 alt-creative-ids, BR-RULE-024
        Covers: UC-003-ALT-UPDATE-CREATIVE-IDS-06
        """
        from src.core.schemas import AdCPPackageUpdate
        from src.core.tools.media_buy_update import _update_media_buy_impl

        # Replace [c1, c2, c3] with [c2, c4]
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            packages=[AdCPPackageUpdate(package_id="pkg_1", creative_ids=["c2", "c4"])],
        )
        identity = _make_identity()

        # New creatives
        mock_c2 = MagicMock()
        mock_c2.creative_id = "c2"
        mock_c2.status = "approved"
        mock_c2.agent_url = "http://agent.test"
        mock_c2.format = "display_300x250"

        mock_c4 = MagicMock()
        mock_c4.creative_id = "c4"
        mock_c4.status = "approved"
        mock_c4.agent_url = "http://agent.test"
        mock_c4.format = "display_300x250"

        # Existing assignments: c1, c2, c3
        mock_assign_c1 = MagicMock()
        mock_assign_c1.creative_id = "c1"
        mock_assign_c2 = MagicMock()
        mock_assign_c2.creative_id = "c2"
        mock_assign_c3 = MagicMock()
        mock_assign_c3.creative_id = "c3"

        mock_buy = MagicMock()
        mock_buy.media_buy_id = "mb_1"
        mock_buy.principal_id = "test_principal"
        mock_buy.status = "active"
        mock_buy.approved_at = None

        mock_package = MagicMock()
        mock_package.package_config = {"product_id": "prod_1"}

        mock_product = MagicMock()
        mock_product.format_ids = []
        mock_product.placements = None

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.database.database_session.get_db_session") as mock_db_inner,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            uow_session = MagicMock()
            mock_uow.session = uow_session
            mock_uow.media_buys = MagicMock()
            # State-machine precondition guard needs a non-terminal status
            _stub_mb = MagicMock()
            _stub_mb.status = "active"
            _stub_media_buy_reads(mock_uow.media_buys, _stub_mb)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            # Media buy and package via repo
            _stub_media_buy_reads(mock_uow.media_buys, mock_buy)
            mock_uow.media_buys.get_package.return_value = mock_package

            # Creative existence/status + product format via repositories.
            mock_uow.creatives.get_by_ids.return_value = [mock_c2, mock_c4]
            mock_uow.products.get_by_id.return_value = mock_product

            # Read and written through the assignments REPOSITORY, not the raw session —
            # the same correction as test_creative_ids_replaces_all: a session.scalars
            # stub fed a lookup production no longer makes, so the tool computed an empty
            # existing set and this case's change-set assertions graded nothing.
            mock_uow.assignments.get_by_media_buy_and_package.return_value = [
                mock_assign_c1,
                mock_assign_c2,
                mock_assign_c3,
            ]

            result = _update_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, UpdateMediaBuySuccess)
        # c1 and c3 should be deleted (removed)
        deleted_ids = {call.args[0].creative_id for call in mock_uow.assignments.delete_row.call_args_list}
        assert deleted_ids == {"c1", "c3"}  # c2 is unchanged and must survive
        # c4 is added, by creative_id rather than by a session.add of an ORM row
        added_ids = {call.kwargs["creative_id"] for call in mock_uow.assignments.create.call_args_list}
        assert added_ids == {"c4"}


class TestUpdateMediaBuyIdentification:
    """UC-003 ext-b: media buy resolution (XOR identification)."""

    def test_media_buy_id_is_required(self):
        """UC-003-ID01: media_buy_id is required for update.

        Spec: media_buy_id is the sole update identifier
        Priority: P1
        Type: unit
        Source: UC-003 ext-b, BR-RULE-021
        Covers: UC-003-EXT-B-03
        """
        # media_buy_id is now the sole identifier; omitting it is rejected
        with pytest.raises(ValidationError, match="media_buy_id"):
            UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                packages=[{"package_id": "pkg_1"}],
            )

    def test_neither_id_rejected(self):
        """UC-003-ID02: providing neither identifier rejected.

        Spec: CONFIRMED -- update-media-buy-request.json oneOf requires one of the two
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Priority: P1
        Type: unit
        Source: UC-003 ext-b, BR-RULE-021
        Covers: UC-003-EXT-B-04
        """
        # Per AdCP spec, media_buy_id is required for update
        with pytest.raises(ValidationError):
            UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                packages=[{"package_id": "pkg_1"}],
            )

    def test_media_buy_id_not_found(self):
        """UC-003-ID03: nonexistent media_buy_id returns media_buy_not_found.

        Spec: CONFIRMED -- error.json provides error structure for not_found responses
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/error.json
        Priority: P1
        Type: unit
        Source: UC-003 ext-b
        Covers: UC-003-EXT-B-01
        """
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_nonexistent",
            packages=[{"package_id": "pkg_1"}],
        )
        identity = _make_identity()

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            mock_uow.session = MagicMock()
            mock_uow.media_buys = MagicMock()
            _stub_media_buy_reads(mock_uow.media_buys, None, media_buy_id="mb_nonexistent")
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            from src.core.exceptions import AdCPMediaBuyNotFoundError

            with pytest.raises(
                (AdCPMediaBuyNotFoundError, AdCPAuthorizationError),
            ):
                _update_media_buy_impl(req=req, identity=identity)

    def test_update_request_requires_media_buy_id(self):
        """UC-003-ID04: media_buy_id is the sole identifier for update requests.

        Spec: update-media-buy-request.json requires media_buy_id.
        Priority: P1
        Type: unit
        Source: UC-003 ext-b
        Covers: UC-003-EXT-B-02
        """
        with pytest.raises(ValidationError, match="media_buy_id"):
            UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-0001",
                packages=[{"package_id": "pkg_1"}],
            )


class TestUpdateMediaBuyOwnership:
    """UC-003 ext-c: ownership verification."""

    def test_ownership_mismatch_rejected(self):
        """UC-003-OW01: non-owner gets permission error.

        Spec: UNSPECIFIED (implementation-defined security boundary)
        Priority: P0
        Type: unit
        Source: UC-003 ext-c
        Covers: UC-003-EXT-C-01
        """
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            packages=[{"package_id": "pkg_1"}],
        )
        identity = _make_identity(principal_id="different_principal")

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr

            mock_audit.return_value = MagicMock()

            mock_buy = MagicMock()
            mock_buy.media_buy_id = "mb_1"
            mock_buy.principal_id = "original_owner"
            mock_buy.tenant_id = "test_tenant"

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            mock_uow.session = MagicMock()
            mock_uow.media_buys = MagicMock()
            _stub_media_buy_reads(mock_uow.media_buys, mock_buy)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            with pytest.raises(AdCPAuthorizationError):
                _update_media_buy_impl(req=req, identity=identity)


class TestUpdateMediaBuyManualApproval:
    """UC-003 alt-manual: manual approval for updates."""

    def test_manual_approval_pending_state(self):
        """UC-003-MA01: manual approval returns status 'submitted'.

        Spec: UNSPECIFIED (implementation-defined HITL workflow)
        Priority: P1
        Type: unit
        Source: UC-003 alt-manual, BR-RULE-017
        Covers: UC-003-ALT-MANUAL-APPROVAL-REQUIRED-01
        """
        from src.core.schemas import AdCPPackageUpdate
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            packages=[AdCPPackageUpdate(package_id="pkg_1", budget=3000.0)],
        )
        identity = _make_identity()

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            # Adapter requires manual approval for update_media_buy
            adapter = MagicMock()
            adapter.manual_approval_required = True
            adapter.manual_approval_operations = ["update_media_buy"]
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            mock_uow.session = MagicMock()
            mock_uow.media_buys = MagicMock()
            # State-machine precondition guard needs a non-terminal status
            _stub_mb = MagicMock()
            _stub_mb.status = "active"
            _stub_media_buy_reads(mock_uow.media_buys, _stub_mb)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            result = _update_media_buy_impl(req=req, identity=identity)

        # Spec 3.1.1: a pending-approval update is the SUBMITTED variant (status="submitted"
        # + task_id), not a completed success (which would falsely claim the update was
        # applied), carried in the UpdateMediaBuyResult protocol envelope (#1417). The
        # workflow step is marked requires_approval.
        assert isinstance(result, UpdateMediaBuySubmitted)
        assert result.status == "submitted"
        assert result.status == "submitted"
        assert result.task_id == "step_1"
        ctx_mgr.audit_workflow_step_result.assert_called_once_with(
            ANY, ANY, status="requires_approval", request_obj=ANY, add_comment=ANY
        )
        # 6.6 reconciliation of main's "affected_packages empty (not yet applied)" check:
        # the submitted envelope has no affected_packages field — the update is not applied.
        assert result.model_dump().get("affected_packages") is None

    def test_implementation_date_null_when_pending(self):
        """UC-003-MA02: implementation_date is null until approved.

        Spec: CONFIRMED -- update-media-buy-response.json implementation_date: "null if pending approval"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        Priority: P1
        Type: unit
        Source: UC-003 alt-manual
        Covers: UC-003-ALT-MANUAL-APPROVAL-REQUIRED-02
        """
        from src.core.schemas import AdCPPackageUpdate
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            packages=[AdCPPackageUpdate(package_id="pkg_1", budget=3000.0)],
        )
        identity = _make_identity()

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = True
            adapter.manual_approval_operations = ["update_media_buy"]
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            mock_uow.session = MagicMock()
            mock_uow.media_buys = MagicMock()
            # State-machine precondition guard needs a non-terminal status
            _stub_mb = MagicMock()
            _stub_mb.status = "active"
            _stub_media_buy_reads(mock_uow.media_buys, _stub_mb)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            result = _update_media_buy_impl(req=req, identity=identity)

        # Spec 3.1.1: a pending-approval update is the SUBMITTED variant, carried in the
        # UpdateMediaBuyResult protocol envelope (#1417). implementation_date is not part
        # of that envelope (the update is not yet applied), so it is absent/None.
        assert isinstance(result, UpdateMediaBuySubmitted)
        assert result.status == "submitted"
        dumped = result.model_dump()
        assert dumped["status"] == "submitted"
        # implementation_date should be None when pending approval
        assert dumped.get("implementation_date") is None


# REMOVED: tests/integration/test_create_media_buy_roundtrip.py, whole file.
#
# Two of its three cases went first, their subject deleted with the testing-hook channel
# (``src/core/testing_hooks.py``, commit a1b79d22d). The survivor graded a
# ``model_dump()`` -> reconstruct round-trip, kept on the argument that it covers the
# ``confirmed_at``-under-``exclude_none`` retention that GH #1900 is about. Measured, that
# argument does not hold: @T-UC-002-v31-success-revision-and-actions ("v3.1 sync success
# response carries revision, confirmed_at, valid_actions") asserts
# ``the response should include "confirmed_at" as an ISO 8601 timestamp`` on the real wire
# and PASSED on a2a, mcp and rest (bdd_inprocess) and on e2e_rest (bdd_e2e) in run
# innet_150926_1232 -- four transports, against one constructor.
#
# What the round-trip alone proved -- that a response re-validates from its own dump -- is
# a property no production path relies on now that the testing-hooks filtering it was
# written for is gone. Its DB fixture had also stopped being used by the one surviving
# test. (Neither the deleted test nor any scenario grades a NULL ``confirmed_at`` on a
# CREATE success; the nearest passing coverage is the get_media_buys item,
# ``test_a_buy_that_was_never_confirmed_still_carries_confirmed_at_as_null[e2e_rest-draft]``.
# That gap is pre-existing -- the deleted test passed a real datetime and never reached it.)


# REMOVED: tests/unit/test_approval_error_handling.py and
# tests/unit/test_approval_error_handling_core.py (8 tests), deleted whole rather than
# repaired. The note lives here because this is the surviving unit file for these tools.
#
# Their PREMISE is gone. Both files documented one bug -- "'CreateMediaBuyError' object
# has no attribute 'media_buy_id'" -- which occurred when the ADAPTER returned an error
# model and the tool then read a success field off it. An adapter now returns
# ``src.adapters.base.AdapterCreateResult`` or RAISES (commit ecfdd7771; production says
# so at the call site, and ``test_adapter_network_error`` below is the case that grades
# it), so the wire error and success models are no longer anything an adapter can hand
# back. The path cannot occur, which is why the files could not be brought back to
# grading anything by fixing them.
#
# And their assertions could not fail regardless: ``hasattr`` on fields the models
# INHERIT from the adcp parent (media_buy_id is not redeclared in either class),
# ``isinstance`` distinguishing two unrelated classes, and ``len(errors) == 1`` after
# constructing with one error.
#
# The one live assertion in the eight -- that ``Error.message`` is derived from
# CODE_TABLE and discards a caller-supplied string -- is graded in 49 other places,
# including exact-equality forms at ``tests/unit/test_delivery.py`` and
# ``tests/unit/test_sync_response_account_contract.py``. The surviving wire use of
# ``CreateMediaBuyError`` (embedded in the seller-rejection webhook) is graded
# end-to-end by ``tests/integration/test_admin_media_buy_reject_webhook.py``, which
# drives the real admin reject route and asserts the embedded code, recovery and
# rejection reason on the captured body.


class TestUpdateMediaBuyAdapterFailure:
    """UC-003 ext-o: adapter/workflow failure."""

    def test_adapter_network_error(self):
        """UC-003-AF01: an adapter failure is not reported to the buyer as a success.

        Spec: UNSPECIFIED (implementation-defined adapter error handling)
        Priority: P1
        Type: unit
        Source: UC-003 ext-o, BR-RULE-020
        Covers: UC-003-EXT-O-01

        The stimulus is a RAISE, not a returned error model. This case used to set
        ``adapter.update_media_buy.return_value = UpdateMediaBuyError(...)``, a shape no
        adapter produces: ``update_media_buy``'s return type is
        ``src.adapters.base.AdapterUpdateResult`` (commit ecfdd7771), and production says
        so at the call site -- "an adapter reports failure by raising; a returned result
        is the success". So the old staging made the tool read media_buy_id and
        affected_packages off an error object and answer the buyer with a SUCCESS, which
        is exactly the defect this case exists to catch, arrived at by staging something
        unreachable. ``AdCPAdapterError`` is the class a transient adapter fault raises
        (the mock adapter's own injected "transient" failure raises it; the wire code is
        SERVICE_UNAVAILABLE), and the tool does not swallow it: the boundary turns it into
        the buyer's error envelope, which is where the wire shape is graded.
        """
        from src.core.exceptions import AdCPAdapterError
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"}, idempotency_key="test-idem-key-0001", media_buy_id="mb_1", paused=True
        )
        identity = _make_identity()

        adapter_error = AdCPAdapterError()

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            adapter.update_media_buy.side_effect = adapter_error
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            mock_uow.session = MagicMock()
            mock_uow.media_buys = MagicMock()
            # State-machine precondition guard needs a non-terminal status
            _stub_mb = MagicMock()
            _stub_mb.status = "active"
            _stub_media_buy_reads(mock_uow.media_buys, _stub_mb)
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            with pytest.raises(AdCPAdapterError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

        # The failure travels as the typed error, so the boundary mints SERVICE_UNAVAILABLE
        # (transient) for the buyer instead of a success carrying the buy's id.
        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
        # And the pause the adapter refused was not written to our row either.
        mock_uow.media_buys.update_fields.assert_not_called()

    def test_no_db_changes_on_adapter_failure(self):
        """UC-003-AF02: adapter failure means no DB records updated.

        Spec: CONFIRMED -- update-media-buy-response.json: "updates are either fully applied or not applied at all"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        Priority: P0
        Type: unit
        Source: UC-003 ext-o, BR-RULE-020
        Covers: UC-003-EXT-O-04
        """
        from src.core.exceptions import AdCPAdapterError
        from src.core.schemas import AdCPPackageUpdate
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_1",
            packages=[AdCPPackageUpdate(package_id="pkg_1", budget=3000.0)],
        )
        identity = _make_identity()

        mock_buy = _mock_media_buy(media_buy_id="mb_1")
        mock_buy.principal_id = "test_principal"
        mock_buy.currency = "USD"
        mock_buy.start_time = datetime(2026, 3, 1, tzinfo=UTC)
        mock_buy.end_time = datetime(2026, 3, 31, tzinfo=UTC)

        cl = MagicMock()
        cl.max_daily_package_spend = Decimal("5000")
        cl.min_package_budget = None

        # No adapter in src/adapters/ constructs UpdateMediaBuyError or Error(code=...) at
        # all -- they RAISE, and since ecfdd7771 the return type says so: an adapter hands
        # back AdapterUpdateResult, which has no errors[]. This case used to stage the
        # returned-error-model shape and then assert the tool reported a failure; with the
        # tool reading a result off that object it reported a SUCCESS instead. The faithful
        # stimulus is the exception a transient adapter fault raises (SERVICE_UNAVAILABLE),
        # and the diagnostic reaches the operator through the exception and the logs, never
        # through a hand-built buyer-facing advisory.
        adapter_error = AdCPAdapterError()

        with (
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_mgr,
            patch("src.core.tools.media_buy_update.MediaBuyUoW") as mock_uow_cls,
            patch("src.core.database.database_session.get_db_session") as mock_db_inner,
            patch("src.core.tools.media_buy_update.get_audit_logger") as mock_audit,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
        ):
            ctx_mgr = MagicMock()
            ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr
            mock_audit.return_value = MagicMock()

            adapter = MagicMock()
            adapter.manual_approval_required = False
            adapter.manual_approval_operations = []
            adapter.update_media_buy.side_effect = adapter_error
            mock_adapter.return_value = adapter

            mock_uow = MagicMock()
            mock_uow.idempotency_attempts.find_by_key.return_value = None  # keyed create probe -> miss
            mock_uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
            mock_uow.idempotency_attempts.count_active.return_value = (0, None)
            uow_session = MagicMock()
            mock_uow.session = uow_session
            mock_uow.media_buys = MagicMock()
            mock_currency_limits = MagicMock()
            mock_currency_limits.get_for_currency.return_value = cl
            mock_uow.currency_limits = mock_currency_limits
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow_cls.return_value = mock_uow

            _stub_media_buy_reads(mock_uow.media_buys, mock_buy)

            with pytest.raises(AdCPAdapterError) as exc_info:
                _update_media_buy_impl(req=req, identity=identity)

        # Non-vacuity: the budget change really did reach the adapter, so what follows is
        # about a failure AFTER the request was accepted, not an early rejection.
        adapter.update_media_buy.assert_called_once_with(
            media_buy_id="mb_1",
            action="update_package_budget",
            package_id="pkg_1",
            budget=3000,
            today=ANY,
        )
        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
        # Nothing was applied: the branch hands the change to the adapter before it writes
        # anything, and the raise skips every write after it — including the success
        # response's own row read. The tool never commits (the UoW owns the session and
        # rolls back when the block raises), so the buy keeps its previous budget.
        uow_session.commit.assert_not_called()
        mock_uow.media_buys.update_fields.assert_not_called()
        # And the in-flight workflow step is closed as failed rather than left open: the
        # tool runs its whole body inside audit_workflow_step_failure_ctx, which records
        # status="failed" with the same error the buyer receives and then re-raises.
        ctx_mgr.audit_workflow_step_failure_ctx.assert_called_once_with(ANY)


# ===========================================================================
# UC-004: DELIVERY METRICS (main flow, status filter, date range)
# ===========================================================================


class TestDeliveryImplSingleBuy:
    """UC-004 main flow: single media buy delivery orchestration."""

    def test_single_buy_returns_complete_response(self):
        """UC-004-D01: single buy returns all top-level fields.

        Spec: CONFIRMED -- get-media-buy-delivery-response.json: reporting_period, currency, media_buy_deliveries required
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-response.json
        Ported from test_delivery_behavioral.py::test_single_buy_returns_complete_response
        """
        buy = _mock_media_buy(start_date=date.today() - timedelta(days=5))
        buy.raw_request = {"packages": [request_package(package_id="pkg_1", product_id="prod_1")]}

        adapter_response = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_1",
            reporting_period=ReportingPeriod(start=datetime.now(UTC) - timedelta(days=5), end=datetime.now(UTC)),
            totals=DeliveryTotals(impressions=1000, spend=50.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_1", impressions=1000, spend=50.0)],
            currency="USD",
        )

        identity = _make_identity()
        adapter_mock = MagicMock()
        adapter_mock.get_media_buy_delivery.return_value = adapter_response

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            patch(f"{_PATCH}._get_target_media_buys", return_value=[("mb_1", buy)]),
            patch(
                f"{_PATCH}._get_pricing_options", side_effect=lambda option_ids, **_: pricing_options_for(option_ids)
            ),
            patch(f"{_PATCH}.MediaBuyUoW") as mock_uow_cls,
        ):
            # Mock UoW context manager
            mock_uow_inst = MagicMock()
            mock_uow_inst.__enter__ = MagicMock(return_value=mock_uow_inst)
            mock_uow_inst.__exit__ = MagicMock(return_value=False)
            mock_uow_inst.media_buys = MagicMock()
            mock_uow_cls.return_value = mock_uow_inst

            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=["mb_1"],
                start_date="2025-01-01",
                end_date="2025-06-30",
            )
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            assert resp.reporting_period is not None
            assert resp.currency == "USD"
            assert len(resp.media_buy_deliveries) == 1
            assert resp.aggregated_totals.impressions >= 0

    def test_fetch_by_media_buy_ids(self):
        """UC-004-D02: media_buy_ids resolution returns delivery data.

        Spec: media_buy_ids is the delivery identifier
        Priority: P0
        Type: unit
        Source: UC-004 main flow, BR-RULE-030
        """
        buy = _mock_media_buy(media_buy_id="mb_1")
        buy.raw_request = {"packages": [request_package(package_id="pkg_1", product_id="prod_1")]}

        adapter_response = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_1",
            reporting_period=ReportingPeriod(start=datetime.now(UTC) - timedelta(days=5), end=datetime.now(UTC)),
            totals=DeliveryTotals(impressions=500, spend=25.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_1", impressions=500, spend=25.0)],
            currency="USD",
        )

        identity = _make_identity()
        adapter_mock = MagicMock()
        adapter_mock.get_media_buy_delivery.return_value = adapter_response

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            patch(f"{_PATCH}._get_target_media_buys", return_value=[("mb_1", buy)]),
            patch(
                f"{_PATCH}._get_pricing_options", side_effect=lambda option_ids, **_: pricing_options_for(option_ids)
            ),
            patch(f"{_PATCH}.MediaBuyUoW") as mock_uow_cls,
        ):
            mock_uow_inst = MagicMock()
            mock_uow_inst.__enter__ = MagicMock(return_value=mock_uow_inst)
            mock_uow_inst.__exit__ = MagicMock(return_value=False)
            mock_uow_inst.media_buys = MagicMock()
            mock_uow_cls.return_value = mock_uow_inst

            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=["mb_1"],
                start_date="2025-01-01",
                end_date="2025-06-30",
            )
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            assert len(resp.media_buy_deliveries) == 1
            assert resp.currency == "USD"

    def test_multiple_buys_aggregate_totals(self):
        """UC-004-D03: aggregated_totals sums across multiple buys.

        Spec: CONFIRMED -- get-media-buy-delivery-response.json has aggregated_totals property
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/src/adcp/types/generated_poc/media_buy/get_media_buy_delivery_response.py
        Priority: P1
        Type: unit
        Source: UC-004 main flow
        """
        buy1 = _mock_media_buy(media_buy_id="mb_1", start_date=date.today() - timedelta(days=5))
        buy1.raw_request = {"packages": [request_package(package_id="pkg_1", product_id="prod_1")]}
        buy2 = _mock_media_buy(media_buy_id="mb_2", start_date=date.today() - timedelta(days=3))
        buy2.raw_request = {"packages": [request_package(package_id="pkg_2", product_id="prod_2")]}

        adapter_resp1 = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_1",
            reporting_period=ReportingPeriod(start=datetime.now(UTC) - timedelta(days=5), end=datetime.now(UTC)),
            totals=DeliveryTotals(impressions=1000, spend=50.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_1", impressions=1000, spend=50.0)],
            currency="USD",
        )
        adapter_resp2 = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_2",
            reporting_period=ReportingPeriod(start=datetime.now(UTC) - timedelta(days=3), end=datetime.now(UTC)),
            totals=DeliveryTotals(impressions=500, spend=25.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_2", impressions=500, spend=25.0)],
            currency="USD",
        )

        identity = _make_identity()
        adapter_mock = MagicMock()
        adapter_mock.get_media_buy_delivery.side_effect = [adapter_resp1, adapter_resp2]

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            patch(
                f"{_PATCH}._get_target_media_buys",
                return_value=[("mb_1", buy1), ("mb_2", buy2)],
            ),
            patch(
                f"{_PATCH}._get_pricing_options", side_effect=lambda option_ids, **_: pricing_options_for(option_ids)
            ),
            patch(f"{_PATCH}.MediaBuyUoW") as mock_uow_cls,
        ):
            mock_uow_inst = MagicMock()
            mock_uow_inst.__enter__ = MagicMock(return_value=mock_uow_inst)
            mock_uow_inst.__exit__ = MagicMock(return_value=False)
            mock_uow_inst.media_buys = MagicMock()
            mock_uow_cls.return_value = mock_uow_inst

            req = GetMediaBuyDeliveryRequest(
                start_date="2025-01-01",
                end_date="2025-06-30",
            )
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            assert len(resp.media_buy_deliveries) == 2
            # Aggregated totals should sum across both buys
            assert resp.aggregated_totals.impressions >= 1500
            assert resp.aggregated_totals.spend >= 75.0
            assert resp.aggregated_totals.media_buy_count == 2

    def test_no_ids_fetches_all(self):
        """UC-004-D04: no identifiers = all buys for principal.

        Spec: CONFIRMED -- get-media-buy-delivery-request.json: media_buy_ids optional
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-request.json
        Priority: P1
        Type: unit
        Source: UC-004 main flow, BR-RULE-030
        """
        buy1 = _mock_media_buy(media_buy_id="mb_1", start_date=date.today() - timedelta(days=5))
        buy1.raw_request = {"packages": [request_package(package_id="pkg_1", product_id="prod_1")]}
        buy2 = _mock_media_buy(media_buy_id="mb_2", start_date=date.today() - timedelta(days=3))
        buy2.raw_request = {"packages": [request_package(package_id="pkg_2", product_id="prod_2")]}

        adapter_resp1 = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_1",
            reporting_period=ReportingPeriod(start=datetime.now(UTC) - timedelta(days=5), end=datetime.now(UTC)),
            totals=DeliveryTotals(impressions=1000, spend=50.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_1", impressions=1000, spend=50.0)],
            currency="USD",
        )
        adapter_resp2 = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_2",
            reporting_period=ReportingPeriod(start=datetime.now(UTC) - timedelta(days=3), end=datetime.now(UTC)),
            totals=DeliveryTotals(impressions=500, spend=25.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_2", impressions=500, spend=25.0)],
            currency="USD",
        )

        identity = _make_identity()
        adapter_mock = MagicMock()
        adapter_mock.get_media_buy_delivery.side_effect = [adapter_resp1, adapter_resp2]

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            patch(
                f"{_PATCH}._get_target_media_buys",
                return_value=[("mb_1", buy1), ("mb_2", buy2)],
            ),
            patch(
                f"{_PATCH}._get_pricing_options", side_effect=lambda option_ids, **_: pricing_options_for(option_ids)
            ),
            patch(f"{_PATCH}.MediaBuyUoW") as mock_uow_cls,
        ):
            mock_uow_inst = MagicMock()
            mock_uow_inst.__enter__ = MagicMock(return_value=mock_uow_inst)
            mock_uow_inst.__exit__ = MagicMock(return_value=False)
            mock_uow_inst.media_buys = MagicMock()
            mock_uow_cls.return_value = mock_uow_inst

            req = GetMediaBuyDeliveryRequest(
                start_date="2025-01-01",
                end_date="2025-06-30",
            )
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            assert len(resp.media_buy_deliveries) == 2


class TestDeliveryImplStatusFilter:
    """UC-004 alt-filtered: status-based delivery filtering."""

    def test_default_filter_is_active(self):
        """UC-004-SF03: no status_filter defaults to active only.

        Spec: UNSPECIFIED (implementation-defined default filter; spec has no default)
        Priority: P2
        Type: unit
        Source: UC-004 alt-filtered
        """
        identity = _make_identity()
        adapter_mock = MagicMock()

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            patch(f"{_PATCH}._get_target_media_buys", return_value=[]) as mock_get_buys,
            patch(
                f"{_PATCH}._get_pricing_options", side_effect=lambda option_ids, **_: pricing_options_for(option_ids)
            ),
            patch(f"{_PATCH}.MediaBuyUoW") as mock_uow_cls,
        ):
            mock_uow_inst = MagicMock()
            mock_uow_inst.__enter__ = MagicMock(return_value=mock_uow_inst)
            mock_uow_inst.__exit__ = MagicMock(return_value=False)
            mock_uow_inst.media_buys = MagicMock()
            mock_uow_cls.return_value = mock_uow_inst

            # No status_filter provided
            req = GetMediaBuyDeliveryRequest(
                start_date="2025-01-01",
                end_date="2025-06-30",
            )
            resp = _get_media_buy_delivery_impl(req, identity)

            # _get_target_media_buys should be called with status_filter=None
            # which defaults to active in _get_target_media_buys
            call_args = mock_get_buys.call_args
            passed_req = call_args[0][0]
            assert passed_req.status_filter is None  # code defaults to "active" internally
            assert isinstance(resp, GetMediaBuyDeliveryResponse)

    def test_no_match_returns_empty(self):
        """UC-004-SF04: empty result is success, not error.

        Spec: CONFIRMED -- get-media-buy-delivery-response.json media_buy_deliveries is array (can be empty)
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-response.json
        Priority: P1
        Type: unit
        Source: UC-004 alt-filtered
        """
        identity = _make_identity()
        adapter_mock = MagicMock()

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            # No buys match the status filter — returns empty, not error
            patch(f"{_PATCH}._get_target_media_buys", return_value=[]),
            patch(
                f"{_PATCH}._get_pricing_options", side_effect=lambda option_ids, **_: pricing_options_for(option_ids)
            ),
            patch(f"{_PATCH}.MediaBuyUoW") as mock_uow_cls,
        ):
            mock_uow_inst = MagicMock()
            mock_uow_inst.__enter__ = MagicMock(return_value=mock_uow_inst)
            mock_uow_inst.__exit__ = MagicMock(return_value=False)
            mock_uow_inst.media_buys = MagicMock()
            mock_uow_cls.return_value = mock_uow_inst

            # No specific IDs — query all, but status filter yields none
            req = GetMediaBuyDeliveryRequest(
                start_date="2025-01-01",
                end_date="2025-06-30",
            )
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            assert resp.media_buy_deliveries == []


class TestDeliveryImplDateRange:
    """UC-004 alt-date-range: custom date range queries."""

    def test_custom_date_range_in_reporting_period(self):
        """UC-004-DR01: provided start/end_date appear in response.

        Spec: CONFIRMED -- get-media-buy-delivery-request.json has start_date, end_date; response has reporting_period
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-request.json
        Priority: P1
        Type: unit
        Source: UC-004 alt-date-range
        """
        identity = _make_identity()
        adapter_mock = MagicMock()

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            patch(f"{_PATCH}._get_target_media_buys", return_value=[]),
            patch(
                f"{_PATCH}._get_pricing_options", side_effect=lambda option_ids, **_: pricing_options_for(option_ids)
            ),
            patch(f"{_PATCH}.MediaBuyUoW") as mock_uow_cls,
        ):
            mock_uow_inst = MagicMock()
            mock_uow_inst.__enter__ = MagicMock(return_value=mock_uow_inst)
            mock_uow_inst.__exit__ = MagicMock(return_value=False)
            mock_uow_inst.media_buys = MagicMock()
            mock_uow_cls.return_value = mock_uow_inst

            req = GetMediaBuyDeliveryRequest(
                start_date="2025-03-01",
                end_date="2025-03-31",
            )
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            dumped = resp.model_dump()
            rp = dumped["reporting_period"]
            # The reporting_period should reflect the requested dates
            assert rp["start"].year == 2025
            assert rp["start"].month == 3
            assert rp["start"].day == 1
            assert rp["end"].year == 2025
            assert rp["end"].month == 3
            assert rp["end"].day == 31

    def test_default_date_range_30_days(self):
        """UC-004-DR02: omitted dates default to last 30 days.

        Spec: UNSPECIFIED (implementation-defined default date range)
        Priority: P1
        Type: unit
        Source: UC-004 main flow
        """
        identity = _make_identity()
        adapter_mock = MagicMock()

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            patch(f"{_PATCH}._get_target_media_buys", return_value=[]),
            patch(
                f"{_PATCH}._get_pricing_options", side_effect=lambda option_ids, **_: pricing_options_for(option_ids)
            ),
            patch(f"{_PATCH}.MediaBuyUoW") as mock_uow_cls,
        ):
            mock_uow_inst = MagicMock()
            mock_uow_inst.__enter__ = MagicMock(return_value=mock_uow_inst)
            mock_uow_inst.__exit__ = MagicMock(return_value=False)
            mock_uow_inst.media_buys = MagicMock()
            mock_uow_cls.return_value = mock_uow_inst

            # No start_date or end_date provided
            req = GetMediaBuyDeliveryRequest()
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            dumped = resp.model_dump()
            rp = dumped["reporting_period"]
            # Default is last 30 days: end ~= now, start ~= 30 days ago

            end_dt = rp["end"]
            start_dt = rp["start"]
            delta = end_dt - start_dt
            assert 29 <= delta.days <= 31  # ~30 days

    def test_start_after_end_returns_error(self):
        """UC-004-DR03: start >= end raises AdCPValidationError.

        Spec: UNSPECIFIED (implementation-defined date range validation)
        """
        identity = _make_identity()

        with (
            patch("src.core.tools.media_buy_delivery.get_adapter") as mock_adapter,
        ):
            mock_adapter.return_value = MagicMock()

            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=["mb_1"],
                start_date="2026-03-20",
                end_date="2026-03-10",
            )
            with pytest.raises(AdCPValidationError):
                _get_media_buy_delivery_impl(req, identity)


class TestDeliveryImplErrors:
    """UC-004 extensions: adapter errors.

    test_principal_not_found_returns_error_response (UC-004-E02) is REMOVED for the same
    reason as TestCreateMediaBuyImplAuth's case above: it patched the deleted
    ``src.core.auth.get_principal_object`` to make a second principal lookup fail inside
    the tool. The identity carries the principal the resolver loaded (47d57e5d6), and a
    protected tool may not raise the auth refusal at all (ruff-boundary.toml). The same
    case in tests/unit/test_delivery.py went with it.
    """

    def test_adapter_error_returns_error_code(self):
        """UC-004-E03: adapter failure RETURNS an advisory error (UC-004-EXT-F degrade).

        Priority: P1
        Type: unit
        Source: UC-004 ext-f
        """
        buy = _mock_media_buy(media_buy_id="mb_1", start_date=date.today() - timedelta(days=5))
        buy.raw_request = {"packages": [request_package(package_id="pkg_1", product_id="prod_1")]}

        identity = _make_identity()
        adapter_mock = MagicMock()
        adapter_mock.get_media_buy_delivery.side_effect = RuntimeError("Network timeout")

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            patch(f"{_PATCH}._get_target_media_buys", return_value=[("mb_1", buy)]),
            patch(
                f"{_PATCH}._get_pricing_options", side_effect=lambda option_ids, **_: pricing_options_for(option_ids)
            ),
            patch(f"{_PATCH}.MediaBuyUoW") as mock_uow_cls,
        ):
            mock_uow_inst = MagicMock()
            mock_uow_inst.__enter__ = MagicMock(return_value=mock_uow_inst)
            mock_uow_inst.__exit__ = MagicMock(return_value=False)
            mock_uow_inst.media_buys = MagicMock()
            mock_uow_cls.return_value = mock_uow_inst

            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=["mb_1"],
                start_date="2025-01-01",
                end_date="2025-06-30",
            )
            result = _get_media_buy_delivery_impl(req, identity)

            assert result.errors is not None
            assert any(e.code == "SERVICE_UNAVAILABLE" for e in result.errors)

    def test_ownership_mismatch_returns_not_found(self):
        """UC-004-E04: non-owner sees not_found, not ownership_mismatch.

        Spec: UNSPECIFIED (implementation-defined security boundary)
        Priority: P0
        Type: unit
        Source: UC-004 ext-d
        """
        identity = _make_identity(principal_id="different_principal")
        adapter_mock = MagicMock()

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            patch(f"{_PATCH}._get_target_media_buys", return_value=[]),
            patch(
                f"{_PATCH}._get_pricing_options", side_effect=lambda option_ids, **_: pricing_options_for(option_ids)
            ),
            patch(f"{_PATCH}.MediaBuyUoW") as mock_uow_cls,
        ):
            mock_uow_inst = MagicMock()
            mock_uow_inst.__enter__ = MagicMock(return_value=mock_uow_inst)
            mock_uow_inst.__exit__ = MagicMock(return_value=False)
            mock_uow_inst.media_buys = MagicMock()
            mock_uow_cls.return_value = mock_uow_inst

            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=["mb_owned_by_other"],
                start_date="2025-01-01",
                end_date="2025-06-30",
            )
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            assert len(resp.media_buy_deliveries) == 0


class TestDeliveryImplPricingLookup:
    """UC-004 pricing: string-to-integer PK regression."""

    def test_pricing_option_lookup_uses_string_field(self):
        """UC-004-PL01: lookup via synthetic ID (model_currency_type), not integer PK.

        Spec: CONFIRMED -- cpm-option.json pricing_option_id is type: string
        Our implementation constructs synthetic IDs like "cpm_usd_fixed".
        Priority: P0
        Type: unit
        Source: UC-004,
        Covers: UC-002-EXT-N-08
        """
        from src.core.tools.media_buy_delivery import _get_pricing_options

        # A real (unpersisted) row. A bare MagicMock fabricates every attribute it is
        # asked for, ``root`` included — so the RootModel unwrap production performs
        # returns a child mock and the id is built from Mock repr, not from these values.
        pricing_option = PricingOptionFactory.build(id=42, pricing_model="cpm", currency="USD", is_fixed=True)
        pricing_option.tenant_id = "test_tenant"

        mock_repo = MagicMock()
        mock_repo.get_all_pricing_options.return_value = [pricing_option]

        result = _get_pricing_options(["cpm_usd_fixed"], tenant_id="test_tenant", product_repo=mock_repo)

        assert "cpm_usd_fixed" in result
        assert result["cpm_usd_fixed"] == pricing_option

    def test_delivery_spend_with_correct_pricing(self):
        """UC-004-PL02: spend computed from rate and impressions.

        Spec: UNSPECIFIED (implementation-defined spend calculation)
        Priority: P0
        Type: unit
        Source: UC-004,
        """
        buy = _mock_media_buy(media_buy_id="mb_1", start_date=date.today() - timedelta(days=5))
        buy.raw_request = {"packages": [request_package(package_id="pkg_1", product_id="prod_1")]}

        adapter_resp = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_1",
            reporting_period=ReportingPeriod(start=datetime.now(UTC) - timedelta(days=5), end=datetime.now(UTC)),
            totals=DeliveryTotals(impressions=10000, spend=50.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_1", impressions=10000, spend=50.0)],
            currency="USD",
        )

        identity = _make_identity()
        adapter_mock = MagicMock()
        adapter_mock.get_media_buy_delivery.return_value = adapter_resp

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            patch(f"{_PATCH}._get_target_media_buys", return_value=[("mb_1", buy)]),
            patch(f"{_PATCH}._get_pricing_options", return_value=pricing_options_named(rate="5.00")),
            patch(f"{_PATCH}.MediaBuyUoW") as mock_uow_cls,
        ):
            mock_uow_inst = MagicMock()
            mock_uow_inst.__enter__ = MagicMock(return_value=mock_uow_inst)
            mock_uow_inst.__exit__ = MagicMock(return_value=False)
            mock_uow_inst.media_buys = MagicMock()
            mock_uow_cls.return_value = mock_uow_inst

            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=["mb_1"],
                start_date="2025-01-01",
                end_date="2025-06-30",
            )
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            assert len(resp.media_buy_deliveries) == 1
            assert resp.aggregated_totals.spend == 50.0
            assert resp.aggregated_totals.impressions == 10000


class TestDeliveryResponseSerialization:
    """UC-004 response serialization: nested model dump."""

    def test_response_is_serializable(self):
        """UC-004-RS01: GetMediaBuyDeliveryResponse.model_dump() succeeds.

        Spec: CONFIRMED -- get-media-buy-delivery-response.json defines the response structure
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-response.json
        """
        resp = GetMediaBuyDeliveryResponse(
            reporting_period={"start": datetime.now(UTC), "end": datetime.now(UTC)},
            currency="USD",
            aggregated_totals={"impressions": 0, "spend": 0, "media_buy_count": 0},
            media_buy_deliveries=[],
        )
        dumped = resp.model_dump()
        assert "reporting_period" in dumped
        assert "media_buy_deliveries" in dumped

    def test_nested_delivery_data_serialized(self):
        """UC-004-RS02: nested MediaBuyDeliveryData serialized correctly.

        Spec: CONFIRMED -- get-media-buy-delivery-response.json has nested media_buy_deliveries
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-response.json
        Priority: P1
        Type: unit
        Source: UC-004, critical pattern #4
        """
        from src.core.schemas import AggregatedTotals, MediaBuyDeliveryData, PackageDelivery

        resp = GetMediaBuyDeliveryResponse(
            reporting_period={"start": datetime.now(UTC), "end": datetime.now(UTC)},
            currency="USD",
            aggregated_totals=AggregatedTotals(
                impressions=1000.0,
                spend=50.0,
                media_buy_count=1,
            ),
            media_buy_deliveries=[
                MediaBuyDeliveryData(
                    media_buy_id="mb_1",
                    status="active",
                    totals=DeliveryTotals(impressions=1000, spend=50.0),
                    by_package=[
                        # pricing_model, rate and currency are on the pinned by_package
                        # item's required set (get-media-buy-delivery-response.json), so
                        # a hand-built entry that omits them is not a document a buyer
                        # can receive.
                        PackageDelivery(
                            package_id="pkg_1",
                            impressions=1000.0,
                            spend=50.0,
                            **package_pricing_fields(),
                        )
                    ],
                )
            ],
        )
        dumped = resp.model_dump()
        assert "media_buy_deliveries" in dumped
        assert len(dumped["media_buy_deliveries"]) == 1
        delivery = dumped["media_buy_deliveries"][0]
        assert delivery["media_buy_id"] == "mb_1"
        assert delivery["status"] == "active"
        assert "totals" in delivery
        assert "by_package" in delivery
        assert len(delivery["by_package"]) == 1
        assert delivery["by_package"][0]["package_id"] == "pkg_1"
        # The pin REQUIRES all three on every by_package entry, and the SDK base dumps with
        # exclude_none=True — an unset one is dropped, not emitted as null (GH #2130).
        assert {"pricing_model", "rate", "currency"} <= delivery["by_package"][0].keys()


# ===========================================================================
# GET MEDIA BUYS (get_media_buys tool)
# ===========================================================================


class TestGetMediaBuysStatusComputation:
    """get_media_buys: _compute_status delegates to the shared resolve_canonical_status (#1545).

    The persisted column is the source of truth for TERMINAL/explicit states
    (paused, completed, rejected, canceled — never date-derived, preserving
    #1417's core), while a GENERIC serving state ("active") is refined against
    the flight window so get_media_buys and get_media_buy_delivery describe the
    same buy identically (pinned by test_media_buy_status_consistency).
    """

    @staticmethod
    def _make_buy(*, status="active", start_offset_days, end_offset_days, start_time=None, is_paused=False):
        """Build the ORM row ``_compute_status`` actually receives.

        This used to build a ``_MediaBuyData``. It cannot any more, and the reason is the
        point of the change rather than an inconvenience: the carrier no longer holds
        ``start_date``/``end_date``/``start_time``/``is_paused`` — the very inputs status
        is derived from — so a carrier cannot be asked what its status should be. Only
        the persisted row can, which is what production passes here.

        Constructed unset-then-assigned rather than by keyword because ``MediaBuy``
        refuses ``revision``/``confirmed_at`` at construction; neither is read on this
        path.
        """
        from src.core.database.models import MediaBuy

        return MediaBuy(
            media_buy_id="mb_1",
            tenant_id="tenant_1",
            currency="USD",
            budget=Decimal("1000"),
            start_date=date.today() + timedelta(days=start_offset_days),
            end_date=date.today() + timedelta(days=end_offset_days),
            start_time=start_time,
            end_time=None,
            raw_request={},
            status=status,
            is_paused=is_paused,
        )

    def test_active_persisted_before_flight_refines_to_pending_start(self):
        """A generic 'active'-persisted buy whose flight has not started is
        date-refined to pending_start (shared resolve_canonical_status taxonomy)."""
        from adcp.types import MediaBuyStatus

        from src.core.tools.media_buy_list import _compute_status

        buy = self._make_buy(start_offset_days=10, end_offset_days=40)
        assert _compute_status(buy, date.today()) == MediaBuyStatus.pending_start

    def test_active_when_in_flight(self):
        """An 'active'-persisted buy within its flight window reports active."""
        from adcp.types import MediaBuyStatus

        from src.core.tools.media_buy_list import _compute_status

        buy = self._make_buy(start_offset_days=-5, end_offset_days=25)
        assert _compute_status(buy, date.today()) == MediaBuyStatus.active

    def test_active_persisted_past_end_refines_to_completed(self):
        """A generic 'active'-persisted buy past its end date is date-refined to
        completed — the same answer get_media_buy_delivery gives for the buy."""
        from adcp.types import MediaBuyStatus

        from src.core.tools.media_buy_list import _compute_status

        buy = self._make_buy(start_offset_days=-40, end_offset_days=-10)
        assert _compute_status(buy, date.today()) == MediaBuyStatus.completed

    def test_terminal_persisted_never_date_derived(self):
        """A TERMINAL persisted state is returned verbatim regardless of the
        flight window (#1417's core, preserved by the shared resolver): a
        'completed' buy whose dates say in-flight stays completed."""
        from adcp.types import MediaBuyStatus

        from src.core.tools.media_buy_list import _compute_status

        buy = self._make_buy(status="completed", start_offset_days=-5, end_offset_days=25)
        assert _compute_status(buy, date.today()) == MediaBuyStatus.completed

    def test_future_start_time_refines_to_pending_start(self):
        """A future start_time refines a generic serving state to pending_start
        even when start_date has arrived (time-of-day precision)."""
        from adcp.types import MediaBuyStatus

        from src.core.tools.media_buy_list import _compute_status

        buy = self._make_buy(
            start_offset_days=-5, end_offset_days=25, start_time=datetime.now(UTC) + timedelta(days=10)
        )
        assert _compute_status(buy, date.today()) == MediaBuyStatus.pending_start


class TestGetMediaBuysStatusFilter:
    """get_media_buys: _resolve_status_filter logic."""

    def test_none_returns_active_only(self):
        """GMB-SF01: no filter defaults to {active}.

        Spec: UNSPECIFIED (implementation-defined default status filter)
        Ported from test_get_media_buys.py::test_none_returns_active_only
        """
        from adcp.types import MediaBuyStatus

        from src.core.tools.media_buy_list import _resolve_status_filter

        assert _resolve_status_filter(None) == {MediaBuyStatus.active}

    def test_single_status(self):
        """GMB-SF02: single status returns set of one.

        Spec: CONFIRMED -- media-buy-status.json enum values used as filter
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        Ported from test_get_media_buys.py::test_single_status
        """
        from adcp.types import MediaBuyStatus

        from src.core.tools.media_buy_list import _resolve_status_filter

        assert _resolve_status_filter(MediaBuyStatus.completed) == {MediaBuyStatus.completed}

    def test_list_of_statuses(self):
        """GMB-SF03: list of statuses returns set of all.

        Spec: CONFIRMED -- media-buy-status.json enum values as filter list
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        Ported from test_get_media_buys.py::test_list_of_statuses
        """
        from adcp.types import MediaBuyStatus

        from src.core.tools.media_buy_list import _resolve_status_filter

        result = _resolve_status_filter([MediaBuyStatus.active, MediaBuyStatus.completed])
        assert result == {MediaBuyStatus.active, MediaBuyStatus.completed}


class TestGetMediaBuysResponseShape:
    """get_media_buys: response serialization."""

    def test_response_is_serializable(self):
        """GMB-RS01: GetMediaBuysResponse.model_dump() succeeds.

        Spec: CONFIRMED -- media-buy.json defines media buy entity shape
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/media-buy.json
        Ported from test_get_media_buys.py::test_response_is_serializable
        """
        from adcp.types import MediaBuyStatus

        resp = GetMediaBuysResponse(
            media_buys=[
                GetMediaBuysMediaBuy(
                    media_buy_id="mb_1",
                    status=MediaBuyStatus.active,
                    currency="USD",
                    total_budget=5000.0,
                    # Spec-required on media_buys[] at 3.1.1, enforced by the model
                    # now that it is grounded on the library item type.
                    confirmed_at=datetime(2025, 2, 1, tzinfo=UTC),
                    revision=1,
                    packages=[
                        GetMediaBuysPackage(package_id="pkg_1"),
                    ],
                )
            ],
        )
        dumped = resp.model_dump()
        assert len(dumped["media_buys"]) == 1
        assert dumped["media_buys"][0]["media_buy_id"] == "mb_1"

    def test_nested_packages_serialized(self):
        """GMB-RS02: packages within media_buys correctly serialized.

        Spec: CONFIRMED -- media-buy.json has packages array of package.json
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/media-buy.json
        Ported from test_get_media_buys.py::test_nested_serialization_roundtrip
        """
        from adcp.types import MediaBuyStatus

        resp = GetMediaBuysResponse(
            media_buys=[
                GetMediaBuysMediaBuy(
                    media_buy_id="mb_1",
                    status=MediaBuyStatus.active,
                    currency="USD",
                    total_budget=5000.0,
                    # Spec-required on media_buys[] at 3.1.1, enforced by the model
                    # now that it is grounded on the library item type.
                    confirmed_at=datetime(2025, 2, 1, tzinfo=UTC),
                    revision=1,
                    packages=[
                        GetMediaBuysPackage(package_id="pkg_1", budget=2500.0, product_id="p1"),
                        GetMediaBuysPackage(package_id="pkg_2", budget=2500.0, product_id="p2"),
                    ],
                )
            ],
        )
        dumped = resp.model_dump(exclude_none=True)
        pkgs = dumped["media_buys"][0]["packages"]
        assert len(pkgs) == 2
        assert pkgs[0]["package_id"] == "pkg_1"
        assert pkgs[1]["package_id"] == "pkg_2"


# TestGetMediaBuysImplAuth::test_missing_principal_raises_auth_missing (GMB-A02,
# Covers: #1651) is REMOVED. It built an identity with principal_id=None and asserted
# _get_media_buys_impl raised AdCPAuthRequiredError / AUTH_MISSING itself.
#
# Neither half of that is constructible now. A ResolvedIdentity ALWAYS carries a principal
# (make_identity takes no None, and the resolver builds the type the tool's annotation
# names), the in-tool guards that raised were removed with the rest of the re-checks when
# the resolver became the one place a credential is judged (47d57e5d6), and
# ruff-boundary.toml's TID251 ban forbids raising AdCPAuthRequiredError anywhere but the
# resolver -- so media_buy_list cannot raise it even if a guard were written back in.
#
# The conformance obligation the case was written for (#1651: a fatal auth failure must
# populate the ENVELOPE, not answer HTTP 200 with a payload-only errors[], per
# transport-errors.mdx :206-220) is unchanged and is now graded where it is decided: the
# resolver mints the refusal for every tool and every transport at once, and the wire
# shape is asserted by the transport-blind auth scenarios rather than once per tool. What
# made get_media_buys special was precisely that it open-coded the check and degraded
# instead of raising; with the check gone it cannot diverge from its fifteen siblings.


# ===========================================================================
# CROSS-CUTTING: Business Rules
# ===========================================================================


class TestBRRule018AtomicResponse:
    """BR-RULE-018: success XOR error -- never both."""

    def test_create_success_has_no_errors(self):
        """BR-018-01: CreateMediaBuySuccess has no errors field.

        Spec: CONFIRMED -- create-media-buy-response.json success: not required ["errors"]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-response.json
        Covers: UC-002-CC-ATOMIC-RESPONSE-SEMANTICS-01
        """
        resp = _make_success()
        dumped = resp.model_dump()
        assert dumped.get("errors") is None

    def test_create_error_has_no_media_buy_id(self):
        """BR-018-02: CreateMediaBuyError has no media_buy_id field.

        Spec: CONFIRMED -- create-media-buy-response.json error: not anyOf [media_buy_id]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-response.json
        Covers: UC-002-CC-ATOMIC-RESPONSE-SEMANTICS-02
        """
        from src.core.schemas import Error

        resp = CreateMediaBuyError(status="failed", errors=[Error(code="VALIDATION_ERROR", message="fail")])
        dumped = resp.model_dump()
        # media_buy_id should not be set or should be None
        assert dumped.get("media_buy_id") is None

    def test_update_success_has_no_errors(self):
        """BR-018-03: UpdateMediaBuySuccess has no errors field.

        Spec: CONFIRMED -- update-media-buy-response.json success: not required ["errors"]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        Covers: UC-003-EXT-O-05
        """
        # sync_success, not the deleted carrier(): this is the buyer's envelope, and
        # ``revision`` is the row's value rather than a placeholder a response may invent.
        resp = UpdateMediaBuySuccess.sync_success(media_buy_id="mb_1", revision=1)
        dumped = resp.model_dump()
        assert dumped.get("errors") is None

    def test_update_error_has_no_affected_packages(self):
        """BR-018-04: UpdateMediaBuyError has no affected_packages.

        Spec: CONFIRMED -- update-media-buy-response.json error: not anyOf [affected_packages]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        Covers: UC-003-EXT-O-05
        """
        from src.core.schemas import Error

        resp = UpdateMediaBuyError(status="failed", errors=[Error(code="VALIDATION_ERROR", message="fail")])
        dumped = resp.model_dump()
        assert dumped.get("affected_packages") is None


class TestBRRule043ContextEcho:
    """BR-RULE-043: the three media-buy requests accept a buyer's context object.

    This class held three cases (create / delivery / get_media_buys) and each graded the
    ECHO half of BR-RULE-043 by building the response with ``context=`` itself, or by
    expecting an ``_impl`` to copy the request's context onto what it returned.

    Neither is possible now, by design. ``context`` is written by the boundary ALONE
    (``src/core/tools/_boundary._served``, through ``object.__setattr__``): ``AdcpResponse``
    refuses the field on construction AND on assignment, ``ruff-boundary.toml`` bans the
    ``context=`` keyword outside the boundary and the schemas, and a tool therefore has no
    echo to perform -- the boundary stamps the buyer's context onto every response, once,
    for every transport. So a response-side echo test at this level cannot construct its
    subject, and if it could it would be grading a copy of the boundary rather than the
    boundary. That half is graded where the writer is:
    tests/unit/test_response_context_is_boundary_owned.py pins both refusals and that the
    boundary's own write still lands and serializes.

    What remains here is the half that IS a property of these models: the pinned request
    schemas declare ``context``, so the DTOs accept one. The strip in
    ``_accepted_shape.deep_strip_to_schema`` keeps only declared fields, which makes this
    a real check -- a request DTO that dropped the declaration would silently discard the
    buyer's context before the boundary ever saw it, and the echo would go out empty.
    """

    @pytest.mark.parametrize(
        ("label", "build"),
        [
            ("create_media_buy", lambda ctx: _make_request(context=ctx)),
            (
                "get_media_buy_delivery",
                lambda ctx: GetMediaBuyDeliveryRequest(start_date="2025-01-01", end_date="2025-06-30", context=ctx),
            ),
            ("get_media_buys", lambda ctx: GetMediaBuysRequest(context=ctx)),
        ],
    )
    def test_request_carries_the_buyers_context(self, label, build):
        """BR-043-01/02/03: each request declares ``context`` and keeps the value sent.

        Spec: CONFIRMED -- context.json: "echoed unchanged in responses"; the field is
        declared on every request and response schema.
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/context.json
        Covers: BR-RULE-043-01
        """
        req = build({"conversation_id": f"conv_{label}", "agent_id": "buyer_agent"})

        assert req.context is not None
        # The VALUE survives, not merely the key: a declaration that parsed to an empty
        # object would leave the boundary nothing to echo.
        assert req.context.conversation_id == f"conv_{label}"
