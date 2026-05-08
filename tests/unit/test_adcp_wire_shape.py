"""Wire-shape contract tests against the adcp library validators.

These tests catch the class of schema-drift bug that shipped as #71: a field
that became required upstream silently disappears from our wire output, and
the SDK rejects the response only at the buyer's edge.

Three layers of defense:

1. ``TestRequiredFieldParity`` — for every model where we extend a library
   type, every field the library marks ``is_required()`` must also be required
   on our local override (or have a default that always produces a value).
   Catches "library bumped X to required, we kept it optional."

2. ``TestWireShapeValidatesAgainstLibrary`` — every top-level response wrapper
   round-trips through the library's own validator after going through the
   production ``_to_wire`` path (``model_dump(mode="json", exclude_none=True)``).
   Catches "wire output omits a required field."

3. ``TestConvertProductHandlesNullableColumns`` — for every nullable Product
   column, set it to ``None`` and verify the converted Pydantic schema's wire
   shape still validates against the library Product. Catches "nullable ORM
   column → required wire field" mismatches (this is exactly #71).

Storyboard tests (#85) catch the same class of bug end-to-end. These run at
``make quality`` time, in seconds, with no Docker.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import inspect as sa_inspect

# ---------------------------------------------------------------------------
# 1. Required-field parity
# ---------------------------------------------------------------------------


# Each entry: (local_class_factory, library_class_factory, allowed_loosened, why)
#
# ``allowed_loosened`` lists field names that the library marks required but
# our local override deliberately makes optional. Every entry must have a
# documented reason. The set should shrink over time, never grow.
def _build_required_field_parity_pairs() -> list[tuple[str, type, type, set[str], str]]:
    from adcp import (
        CreateMediaBuyRequest as LibCreateMediaBuyRequest,
    )
    from adcp import (
        ListCreativeFormatsRequest as LibListCreativeFormatsRequest,
    )
    from adcp import (
        ListCreativesRequest as LibListCreativesRequest,
    )
    from adcp import (
        SyncCreativesRequest as LibSyncCreativesRequest,
    )
    from adcp.types import (
        Creative as LibCreative,
    )
    from adcp.types import (
        Format as LibFormat,
    )
    from adcp.types import (
        FormatId as LibFormatId,
    )
    from adcp.types import (
        FrequencyCap as LibFrequencyCap,
    )
    from adcp.types import (
        GetMediaBuyDeliveryRequest as LibGetMediaBuyDeliveryRequest,
    )
    from adcp.types import (
        Measurement as LibMeasurement,
    )
    from adcp.types import (
        Package as LibPackage,
    )
    from adcp.types import (
        PackageRequest as LibPackageRequest,
    )
    from adcp.types import (
        Placement as LibPlacement,
    )
    from adcp.types import (
        Product as LibProduct,
    )
    from adcp.types import (
        UpdateMediaBuyRequest as LibUpdateMediaBuyRequest,
    )

    from src.core.schemas import (
        CreateMediaBuyRequest,
        Creative,
        Format,
        FormatId,
        FrequencyCap,
        GetMediaBuyDeliveryRequest,
        ListCreativeFormatsRequest,
        ListCreativesRequest,
        Measurement,
        Package,
        PackageRequest,
        Product,
        SyncCreativesRequest,
        UpdateMediaBuyRequest,
    )
    from src.core.schemas.product import Placement

    return [
        # (id, local, library, allowed_loosened, why)
        ("Product", Product, LibProduct, set(), "Local extends library; no documented loosening."),
        ("Placement", Placement, LibPlacement, set(), "Local makes description and format_ids stricter."),
        ("FormatId", FormatId, LibFormatId, set(), "Pass-through extension."),
        ("Format", Format, LibFormat, set(), "Pass-through extension."),
        ("Package", Package, LibPackage, set(), "Pass-through extension."),
        ("PackageRequest", PackageRequest, LibPackageRequest, set(), "Pass-through extension."),
        ("FrequencyCap", FrequencyCap, LibFrequencyCap, set(), "Pass-through extension."),
        ("Measurement", Measurement, LibMeasurement, set(), "Pass-through extension."),
        (
            "Creative",
            Creative,
            LibCreative,
            # Library v4.4.0 made variants required and dropped tags. Salesagent's
            # creative ORM column predates the change; we default variants=None so
            # legacy rows still serialize. Documented at src/core/schemas/creative.py:166.
            {"variants"},
            "Legacy creative rows lack variants; documented override with default=None.",
        ),
        (
            "CreateMediaBuyRequest",
            CreateMediaBuyRequest,
            LibCreateMediaBuyRequest,
            # Library v4.4.0 made idempotency_key + account required. Salesagent
            # accepts requests without them for backward compat; documented at
            # src/core/schemas/_base.py:1398-1400.
            {"idempotency_key", "account"},
            "Backward-compat for clients that don't send idempotency_key/account.",
        ),
        (
            "UpdateMediaBuyRequest",
            UpdateMediaBuyRequest,
            LibUpdateMediaBuyRequest,
            {"idempotency_key", "account"},
            "Backward-compat for clients that don't send idempotency_key/account.",
        ),
        (
            "GetMediaBuyDeliveryRequest",
            GetMediaBuyDeliveryRequest,
            LibGetMediaBuyDeliveryRequest,
            set(),
            "Pass-through extension.",
        ),
        (
            "ListCreativesRequest",
            ListCreativesRequest,
            LibListCreativesRequest,
            set(),
            "Pass-through extension.",
        ),
        (
            "ListCreativeFormatsRequest",
            ListCreativeFormatsRequest,
            LibListCreativeFormatsRequest,
            set(),
            "Pass-through extension.",
        ),
        (
            "SyncCreativesRequest",
            SyncCreativesRequest,
            LibSyncCreativesRequest,
            # Same backward-compat override as CreateMediaBuyRequest: account
            # is resolved at the transport layer (ResolvedIdentity) and
            # idempotency_key is optional. Documented at
            # src/core/schemas/creative.py:339-346.
            {"account", "idempotency_key"},
            "Identity resolved at transport layer; idempotency_key optional for compat.",
        ),
    ]


_REQUIRED_FIELD_PARITY_PAIRS = _build_required_field_parity_pairs()


def _required_fields(cls: type) -> set[str]:
    """Return the set of field names a Pydantic model marks as required."""
    return {name for name, info in cls.model_fields.items() if info.is_required()}


def _has_static_default(cls: type, field_name: str) -> bool:
    """True if the local field will always produce a value at construction time.

    A field with a non-callable default of ``None`` does NOT count: ``None`` is
    serialized away by ``exclude_none=True`` and the wire shape still drops the
    field. A factory or non-None default does count.
    """
    info = cls.model_fields.get(field_name)
    if info is None:
        return False
    if info.default_factory is not None:
        return True
    if info.default is None:
        return False
    # PydanticUndefined means "no default" → field is required
    return info.default is not info.default if False else (info.default is not None)


class TestRequiredFieldParity:
    """Catch library→local divergence on required fields.

    The rule: every field the library marks required must either be required
    on our local override OR have a default that always produces a value
    (default_factory or non-None default). A nullable optional with default
    ``None`` does NOT satisfy the rule — ``exclude_none=True`` will drop it
    and the wire shape will be missing a required field.
    """

    @pytest.mark.parametrize(
        "name,local,library,allowed_loosened,why",
        _REQUIRED_FIELD_PARITY_PAIRS,
        ids=[p[0] for p in _REQUIRED_FIELD_PARITY_PAIRS],
    )
    def test_lib_required_subset_of_local(
        self,
        name: str,
        local: type,
        library: type,
        allowed_loosened: set[str],
        why: str,
    ) -> None:
        """Every library-required field must be required (or always produced) on local."""
        lib_required = _required_fields(library)
        local_required = _required_fields(local)

        # Fields the library requires but local does not.
        loosened = lib_required - local_required

        # Fields where local has a default that always produces a value.
        always_produced = {f for f in loosened if _has_static_default(local, f)}

        # The remaining drift = library-required fields that local makes optional
        # AND whose default is None or missing → wire shape will drop them.
        true_drift = loosened - always_produced - allowed_loosened

        assert not true_drift, (
            f"{name}: library requires {sorted(true_drift)} but local makes them "
            f"optional with no static default. With exclude_none=True the wire "
            f"shape will omit them and the library validator will reject. "
            f"Either inherit the requirement, add a non-None default factory, "
            f"or document the override in the parametrize allowlist with rationale."
        )


# ---------------------------------------------------------------------------
# 2. Wire-shape validation against library
# ---------------------------------------------------------------------------


def _build_minimal_product() -> Any:
    from src.core.schemas.product import Product as LocalProduct
    from tests.helpers.adcp_factories import create_test_product

    base = create_test_product()
    return LocalProduct.model_validate(base.model_dump())


def _build_minimal_creative() -> Any:
    from src.core.schemas import Creative
    from tests.helpers.adcp_factories import create_test_format_id

    return Creative(
        creative_id="cr_test",
        name="Test Creative",
        format_id=create_test_format_id("display_300x250"),
    )


def _build_get_products_response() -> tuple[Any, type]:
    from adcp import GetProductsResponse as LibResponse

    from src.core.schemas import GetProductsResponse

    return GetProductsResponse(products=[_build_minimal_product()]), LibResponse


def _build_create_media_buy_success() -> tuple[Any, type]:
    """Sync-success path: wrapper produces variant-1 of the create_media_buy union.

    Validate against the discriminated union (not just the success variant)
    so the wire shape is checked against the same contract the SDK enforces.
    """
    from adcp.types import CreateMediaBuyResponse as LibResponse
    from adcp.types import MediaBuyStatus

    from src.core.schemas import CreateMediaBuySuccess
    from src.core.schemas._base import CreateMediaBuyResult

    inner = CreateMediaBuySuccess(media_buy_id="mb_test", packages=[], status=MediaBuyStatus.pending_start)
    return CreateMediaBuyResult(response=inner, status="completed"), LibResponse


def _build_create_media_buy_success_pending_creatives() -> tuple[Any, type]:
    """Sync-success / pending_creatives path: variant-1 with the buy minted
    but creatives still owed. Pins the wire shape for the storyboard scenarios
    where the buyer creates a media buy without including creatives and is
    expected to call ``sync_creatives`` next.

    This is the inverse of the over-correction in PR #183 — the seller has
    minted ``media_buy_id`` synchronously, so withholding it via variant-3
    (``status='submitted'``, no ``media_buy_id``) is wrong. The buyer expects
    the buy id and ``MediaBuyStatus.pending_creatives`` so the next call is
    unambiguous.
    """
    from adcp.types import CreateMediaBuyResponse as LibResponse
    from adcp.types import MediaBuyStatus

    from src.core.schemas import CreateMediaBuySuccess, Package
    from src.core.schemas._base import CreateMediaBuyResult

    inner = CreateMediaBuySuccess(
        media_buy_id="mb_pending_creatives",
        packages=[Package(package_id="pkg_test")],
        status=MediaBuyStatus.pending_creatives,
        workflow_step_id="step_internal_only",
    )
    return CreateMediaBuyResult(response=inner, status="completed"), LibResponse


def _build_create_media_buy_submitted() -> tuple[Any, type]:
    """Async-pending-approval path: wrapper produces variant-3 (submitted envelope).

    Per AdCP spec ``create_media_buy_response``, the async-submitted shape has
    ``status='submitted'`` (a TaskStatus literal) plus ``task_id``, and does
    NOT carry ``media_buy_id`` or ``packages`` — those belong to variant-1.

    This builder exercises the wrapper's ``_serialize`` path that injects the
    top-level ``status`` field; previously the test bypassed the wrapper and
    missed a wire-shape regression where the bug emitted a hybrid (media_buy_id
    + packages + status='submitted') that the discriminated union rejects.
    """
    from adcp.types import CreateMediaBuyResponse as LibResponse

    from src.core.schemas._base import CreateMediaBuyResult, CreateMediaBuySubmitted

    inner = CreateMediaBuySubmitted(task_id="task_test", message="Awaiting approval")
    return CreateMediaBuyResult(response=inner, status="submitted"), LibResponse


def _build_create_media_buy_error() -> tuple[Any, type]:
    """Error path: wrapper produces variant-2 of the create_media_buy union."""
    from adcp import Error
    from adcp.types import CreateMediaBuyResponse as LibResponse

    from src.core.schemas import CreateMediaBuyError
    from src.core.schemas._base import CreateMediaBuyResult

    inner = CreateMediaBuyError(errors=[Error(code="invalid_input", message="bad")])
    return CreateMediaBuyResult(response=inner, status="failed"), LibResponse


def _build_sync_creatives_response() -> tuple[Any, type]:
    from adcp.types import SyncCreativesSuccessResponse as LibResponse

    from src.core.schemas import SyncCreativesResponse

    return SyncCreativesResponse(creatives=[]), LibResponse


def _build_get_media_buy_delivery_response() -> tuple[Any, type]:
    from adcp.types import GetMediaBuyDeliveryResponse as LibResponse

    from src.core.schemas import GetMediaBuyDeliveryResponse

    now = datetime.now(UTC)
    return (
        GetMediaBuyDeliveryResponse(
            aggregated_totals={"impressions": 0, "media_buy_count": 0, "spend": 0.0},
            media_buy_deliveries=[],
            reporting_period={"start": now, "end": now},
            currency="USD",
        ),
        LibResponse,
    )


def _build_list_creatives_response() -> tuple[Any, type]:
    from adcp import ListCreativesResponse as LibResponse

    from src.core.schemas import ListCreativesResponse
    from src.core.schemas.creative import Pagination, QuerySummary

    return (
        ListCreativesResponse(
            creatives=[],
            # Library nests its own QuerySummary requiring total_matching+returned;
            # build a dict so Pydantic validates against the nested type.
            query_summary=QuerySummary(total_matching=0, returned=0),
            pagination=Pagination(has_more=False),
        ),
        LibResponse,
    )


def _build_list_creative_formats_response() -> tuple[Any, type]:
    from adcp import ListCreativeFormatsResponse as LibResponse

    from src.core.schemas import ListCreativeFormatsResponse

    return ListCreativeFormatsResponse(formats=[]), LibResponse


def _build_get_media_buys_response_with_list_targeting() -> tuple[Any, type]:
    """get_media_buys response carrying ``targeting_overlay`` with
    ``PropertyListReference`` and ``CollectionListReference``.

    Pins the wire shape that the
    ``media_buy_seller/inventory_list_targeting/get_after_create`` storyboard
    validates: per AdCP spec ``Package.targeting_overlay``, sellers claiming
    the property-lists / collection-lists specialisms MUST echo back the
    persisted list references. This case caused
    ``Platform method 'get_media_buys' raised AdCPValidationError`` in
    production before our fix.
    """
    from adcp.types import GetMediaBuysResponse as LibResponse
    from adcp.types import MediaBuyStatus

    from src.core.schemas import (
        GetMediaBuysMediaBuy,
        GetMediaBuysPackage,
        GetMediaBuysResponse,
        Targeting,
    )

    overlay = Targeting.model_validate(
        {
            "property_list": {
                "agent_url": "https://governance.pinnacle-agency.example",
                "list_id": "acme_outdoor_allowlist_v1",
            },
            "collection_list": {
                "agent_url": "https://governance.pinnacle-agency.example",
                "list_id": "acme_outdoor_collections_v1",
            },
        }
    )
    return (
        GetMediaBuysResponse(
            media_buys=[
                GetMediaBuysMediaBuy(
                    media_buy_id="mb_inventory_lists",
                    status=MediaBuyStatus.active,
                    currency="USD",
                    total_budget=20000.0,
                    packages=[
                        GetMediaBuysPackage(
                            package_id="pkg_inventory_lists",
                            targeting_overlay=overlay,
                        ),
                    ],
                ),
            ],
        ),
        LibResponse,
    )


_WIRE_SHAPE_BUILDERS = [
    ("GetProductsResponse", _build_get_products_response),
    # Cover all three variants of the create_media_buy_response union.
    # The wrapper ``CreateMediaBuyResult`` is what the production wire path
    # actually serializes — testing the inner ``CreateMediaBuySuccess`` alone
    # bypasses the ``_serialize`` step that injects the top-level status.
    ("CreateMediaBuyResult.success", _build_create_media_buy_success),
    ("CreateMediaBuyResult.success_pending_creatives", _build_create_media_buy_success_pending_creatives),
    ("CreateMediaBuyResult.submitted", _build_create_media_buy_submitted),
    ("CreateMediaBuyResult.error", _build_create_media_buy_error),
    ("SyncCreativesResponse", _build_sync_creatives_response),
    ("GetMediaBuyDeliveryResponse", _build_get_media_buy_delivery_response),
    ("GetMediaBuysResponse.with_list_targeting", _build_get_media_buys_response_with_list_targeting),
    ("ListCreativesResponse", _build_list_creatives_response),
    ("ListCreativeFormatsResponse", _build_list_creative_formats_response),
]


class TestWireShapeValidatesAgainstLibrary:
    """Top-level responses round-trip through the library validator.

    The wire path in production is ``response.model_dump(mode="json",
    exclude_none=True)`` (see ``core/platforms/_delegate.py:_to_wire``). After
    that, the resulting dict must validate against the library's own response
    type — that's the contract the SDK enforces at the buyer's edge.

    Note: ``ListAuthorizedPropertiesResponse`` is local-only (the type was
    removed from the adcp library in 3.2.0), so it has no library validator
    to round-trip against and is excluded.
    """

    @pytest.mark.parametrize(
        "name,builder",
        _WIRE_SHAPE_BUILDERS,
        ids=[name for name, _ in _WIRE_SHAPE_BUILDERS],
    )
    def test_wire_shape_validates(self, name: str, builder) -> None:
        from pydantic import TypeAdapter

        response, library_cls = builder()
        wire = response.model_dump(mode="json", exclude_none=True)
        # Raises ValidationError if our wire output is missing a required
        # field, has wrong types, or otherwise fails the library's contract.
        # ``TypeAdapter`` handles both concrete BaseModel classes and
        # PEP-604 union types (``A | B | C``) — the create_media_buy_response
        # is a discriminated union of success / error / submitted variants.
        TypeAdapter(library_cls).validate_python(wire)

    def test_create_media_buy_variant1_pending_creatives_validates_per_spec(self) -> None:
        """Spec contract: variant-1 with ``status='pending_creatives'`` is valid.

        Independent of our implementation, the discriminated union must accept
        ``{media_buy_id, packages, status: 'pending_creatives'}`` as the
        sync-success shape for "buy minted, awaiting creatives sync." This is
        the storyboard contract that PR #183 inadvertently broke by routing
        the case through variant-3 (``submitted``).
        """
        from adcp.types import CreateMediaBuyResponse as LibResponse
        from pydantic import TypeAdapter

        wire = {
            "media_buy_id": "mb_test",
            "packages": [{"package_id": "pkg_test", "paused": False}],
            "status": "pending_creatives",
        }
        # Raises ValidationError on spec drift; success means the discriminated
        # union resolves cleanly to the sync-success variant.
        TypeAdapter(LibResponse).validate_python(wire)

    def test_create_media_buy_variant3_without_media_buy_id_validates_per_spec(self) -> None:
        """Spec contract: variant-3 (no ``media_buy_id``) is still valid.

        Counterpart to the variant-1 test: the submitted envelope is reserved
        for cases where the seller hasn't minted a buy. ``media_buy_id`` is
        absent by design — the buyer polls via ``task_id``.
        """
        from adcp.types import CreateMediaBuyResponse as LibResponse
        from pydantic import TypeAdapter

        wire = {"task_id": "task_test", "status": "submitted"}
        TypeAdapter(LibResponse).validate_python(wire)

    def test_create_media_buy_hybrid_shape_is_rejected_by_spec(self) -> None:
        """The async-pending shape must not be a sync-success body with status='submitted'.

        Regression guard: a previous bug emitted ``{media_buy_id, packages,
        status: 'submitted'}`` from the manual-approval path. The spec's
        ``create_media_buy_response`` is a discriminated union — sync-success
        carries a ``MediaBuyStatus`` (no 'submitted'), while the async
        envelope carries a TaskStatus literal ``'submitted'`` and identifies
        the work via ``task_id`` (no ``media_buy_id``/``packages``).

        This test pins the spec contract independent of our implementation,
        so future code that reaches for ``CreateMediaBuySuccess(...)`` paired
        with ``status='submitted'`` will be caught at the wire boundary.
        """
        import pytest as _pytest
        from adcp.types import CreateMediaBuyResponse as LibResponse
        from pydantic import TypeAdapter, ValidationError

        hybrid = {"media_buy_id": "mb_test", "packages": [], "status": "submitted"}
        with _pytest.raises(ValidationError):
            TypeAdapter(LibResponse).validate_python(hybrid)


# ---------------------------------------------------------------------------
# 3. Convert Product handles nullable ORM columns
# ---------------------------------------------------------------------------


def _product_nullable_columns() -> list[str]:
    """Nullable columns on the Product ORM model.

    Read from SQLAlchemy at import time so the test stays in sync with the
    schema automatically.
    """
    from src.core.database.models import Product as ProductModel

    return [c.name for c in sa_inspect(ProductModel).columns if c.nullable]


def _make_product_model_mock(**overrides: Any) -> SimpleNamespace:
    """Build a stand-in for the Product ORM model with sane defaults.

    Only fields read by ``convert_product_model_to_schema`` matter; everything
    else is ignored. Using ``SimpleNamespace`` over ``MagicMock`` keeps attribute
    access strict — typos surface as ``AttributeError`` instead of silent magic.
    """
    pricing_option = SimpleNamespace(
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
        rate=Decimal("10.00"),
        price_guidance=None,
        parameters=None,
        min_spend_per_package=None,
    )

    defaults: dict[str, Any] = {
        # Required fields per the conversion path
        "product_id": "prod_test",
        "name": "Test Product",
        "description": "Test description",
        "delivery_type": "guaranteed",
        "is_custom": False,
        "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        "delivery_measurement": {"provider": "test_provider"},
        "pricing_options": [pricing_option],
        "targeting_template": {},
        # The conversion calls these via ``effective_*`` properties
        "effective_format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        "effective_properties": [
            {
                "publisher_domain": "test.example.com",
                "property_tags": ["all_inventory"],
                "selection_type": "by_tag",
            }
        ],
        "effective_implementation_config": None,
        # Required scalar with default
        "property_targeting_allowed": False,
        # Nullable columns — all default to None unless overridden
        "measurement": None,
        "creative_policy": None,
        "price_guidance": None,
        "expires_at": None,
        "countries": None,
        "channels": None,
        "implementation_config": None,
        "properties": None,
        "property_ids": None,
        "property_tags": None,
        "inventory_profile_id": None,
        "product_card": None,
        "product_card_detailed": None,
        "placements": None,
        # adcp 4.4: required on the wire; mirror the column server_default
        # (the conversion path can no longer rely on a schema-side
        # default_factory — see PR #110).
        "reporting_capabilities": {
            "available_reporting_frequencies": ["daily"],
            "expected_delay_minutes": 0,
            "timezone": "UTC",
            "supports_webhooks": False,
            "available_metrics": ["impressions"],
            "date_range_support": "date_range",
        },
        "signal_targeting_allowed": None,
        "catalog_match": None,
        "catalog_types": None,
        "conversion_tracking": None,
        "data_provider_signals": None,
        "forecast": None,
        "parent_product_id": None,
        "signals_agent_ids": None,
        "variant_name_template": None,
        "variant_description_template": None,
        "activation_key": None,
        "signal_metadata": None,
        "last_synced_at": None,
        "archived_at": None,
        "variant_ttl_days": None,
        "allowed_principal_ids": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestConvertProductHandlesNullableColumns:
    """Every nullable Product column → valid wire shape after conversion.

    For each nullable ORM column, build a Product model with that column set
    to ``None``, run ``convert_product_model_to_schema``, dump via the
    production wire path, and validate against the library Product. This is
    the test that would have caught #71 directly: ``reporting_capabilities``
    is nullable in the DB but required on the wire — the local Product schema
    must produce a default that satisfies the library validator.
    """

    @pytest.mark.parametrize("nullable_column", _product_nullable_columns())
    def test_null_orm_column_produces_valid_wire_output(self, nullable_column: str) -> None:
        from adcp.types import Product as LibProduct

        from src.core.product_conversion import convert_product_model_to_schema

        # Some columns are required for the conversion to succeed at all
        # (e.g. ``properties``/``property_ids``/``property_tags`` are XOR — at
        # least one of them must drive ``effective_properties``). The mock
        # provides ``effective_properties`` directly, so nulling the underlying
        # columns is harmless. Skip columns that are not actually consumed by
        # the conversion path.
        unconsumed_columns = {
            # Dynamic-template/variant columns — never read by the conversion
            "is_dynamic",
            "is_dynamic_variant",
            "parent_product_id",
            "signals_agent_ids",
            "variant_name_template",
            "variant_description_template",
            "max_signals",
            "activation_key",
            "signal_metadata",
            "last_synced_at",
            "archived_at",
            "variant_ttl_days",
            # DB-only metadata
            "inventory_profile_id",
            "expires_at",
            "price_guidance",
            "targeting_template",
        }
        if nullable_column in unconsumed_columns:
            pytest.skip(f"{nullable_column} is not consumed by the wire-shape conversion path.")

        model = _make_product_model_mock(**{nullable_column: None})
        schema = convert_product_model_to_schema(model, adapter_type="mock")
        wire = schema.model_dump(mode="json", exclude_none=True)

        # Drop internal-only fields that the local Product carries with
        # exclude=True; they're already absent under exclude_none, but be
        # defensive in case future overrides change that.
        for internal in ("implementation_config", "countries", "device_types", "allowed_principal_ids"):
            wire.pop(internal, None)

        LibProduct.model_validate(wire)

    def test_reporting_capabilities_present_on_wire_when_populated(self) -> None:
        """When the ORM ``reporting_capabilities`` column is set, it survives
        ``model_dump(mode='json', exclude_none=True)`` to the wire.

        Issue #71 was the inverse case (column null → field stripped → SDK
        rejects). Migration c8404b483cf3 made the column non-null with a
        ``server_default`` so the conversion path can never see ``None``
        coming from the DB. This test verifies the populated path still
        emits the field — the regression guard for #71.
        """
        from src.core.product_conversion import convert_product_model_to_schema

        # Default fixture supplies a populated reporting_capabilities block;
        # exercise that path explicitly.
        model = _make_product_model_mock()
        schema = convert_product_model_to_schema(model, adapter_type="mock")
        wire = schema.model_dump(mode="json", exclude_none=True)

        assert "reporting_capabilities" in wire, (
            "reporting_capabilities is required on the wire (adcp 4.4.0+); "
            "exclude_none=True must not strip a populated value."
        )
        rc = wire["reporting_capabilities"]
        # Library requires these keys on the nested object.
        for required_key in (
            "available_metrics",
            "available_reporting_frequencies",
            "date_range_support",
            "expected_delay_minutes",
            "supports_webhooks",
            "timezone",
        ):
            assert required_key in rc, (
                f"reporting_capabilities default must include '{required_key}' "
                f"per adcp ReportingCapabilities spec; got keys {sorted(rc.keys())}"
            )
