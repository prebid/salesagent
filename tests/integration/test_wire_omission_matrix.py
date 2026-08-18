"""One table for "an unset optional field must be omitted from the wire, never null".

Regression cover for the #1710 / #1868 null-leak class. Six near-identical modules
used to encode this, one per tool, and they had already drifted inside a single PR
-- one parametrized ``[MCP, REST, A2A]``, its siblings ``[MCP, A2A, REST]``. The
obligation is the same for every tool; only the environment, the pinned schema and
the field list differ, so those are DATA here and there is one place to add a case.

``WireOmissionCase.__post_init__`` makes two mistakes unconstructible:

* a row that grades nothing (no schema AND no absent paths) -- previously a legal
  call that read as coverage;
* a row narrowed to fewer than every wire transport without saying why -- the
  reason is a required field, so "we quietly dropped REST" cannot be written.

Not everything here is a row. Three obligations below are genuinely different
operations that merely lived next door: get_products' IMPL-level dump (no wire by
definition), sync_creatives' "these fields are arrays, not just non-null", and the
signals pair (unreachable on every transport). Collapsing those into the table
would mean pretending different assertions are the same one, which is how the
drift above started.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import pytest

from tests.factories import (
    MediaBuyFactory,
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    PublisherPartnerFactory,
    TenantFactory,
)
from tests.factories.creative_asset import CreativeAssetFactory
from tests.harness.assertions import assert_wire_omits_unset
from tests.harness.authorized_properties import AuthorizedPropertiesEnv
from tests.harness.capabilities import CapabilitiesEnv
from tests.harness.creative_sync import CreativeSyncEnv
from tests.harness.performance import PerformanceEnv
from tests.harness.product import ProductEnv
from tests.harness.signals import ActivateSignalEnv, GetSignalsEnv
from tests.harness.transport import Transport
from tests.helpers import pinned_schema

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Every transport that has a wire. IMPL has none by definition.
_ALL_WIRE = (Transport.MCP, Transport.A2A, Transport.REST)

_GET_PRODUCTS_SCHEMA = "media-buy/get-products-response.json"


@dataclass(frozen=True)
class WireOmissionCase:
    """One tool's omission obligation, as data."""

    tool: str
    # Yields (env, call_kwargs) — the env is entered and its fixtures built.
    setup: Callable[[], Iterator[tuple[object, dict]]]
    # Pinned schema to validate the full wire body against, or None when no pinned
    # schema matches this response's actual shape (a documented spec-grounding gap).
    schema: str | None
    # Dotted paths that must be ABSENT from the wire.
    absent_paths: Sequence[str] = ()
    transports: Sequence[Transport] = _ALL_WIRE
    # Required when transports is narrower than every wire transport.
    narrowed_because: str | None = None
    # A response key that must be non-empty, so a schema-valid EMPTY body cannot
    # pass as coverage.
    must_be_non_empty: str | None = None

    def __post_init__(self) -> None:
        if self.schema is None and not self.absent_paths:
            raise ValueError(
                f"{self.tool}: a case with no schema AND no absent_paths grades nothing. "
                "Give it a pinned schema, or the fields whose absence is the obligation."
            )
        if set(self.transports) != set(_ALL_WIRE) and not self.narrowed_because:
            raise ValueError(
                f"{self.tool}: grades only {[t.value for t in self.transports]} — "
                "state narrowed_because so a silently-dropped transport is not mistaken for coverage."
            )


@contextmanager
def _get_products_env() -> Iterator[tuple[object, dict]]:
    """One plain product whose nested optional fields are all unset.

    The factory defaults omit exactly the fields the null-leak regressed on:
    format_ids entries carry only agent_url/id, delivery_measurement carries only
    provider, and the pricing option is a fixed rate with no floor_price. A product
    with those sub-fields populated would not exercise strip_none_deep at all.
    """
    with ProductEnv(tenant_id="wire-schema-test", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="wire-schema-test")
        PrincipalFactory(tenant=tenant, principal_id="test_principal")
        product = ProductFactory(
            tenant=tenant,
            product_id="wire_schema_product",
            name="Wire Schema Product",
            description="Product whose nested optional fields are unset",
            delivery_type="guaranteed",
        )
        PricingOptionFactory(product=product, pricing_model="cpm", rate="15.00", is_fixed=True, currency="USD")
        env.set_policy_approved()
        env.set_ranking_disabled()
        yield env, {"brief": "display ads"}


@contextmanager
def _capabilities_env() -> Iterator[tuple[object, dict]]:
    with CapabilitiesEnv(tenant_id="wire-schema-capabilities", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="wire-schema-capabilities")
        PrincipalFactory(tenant=tenant, principal_id="test_principal")
        yield env, {}


@contextmanager
def _authorized_properties_env() -> Iterator[tuple[object, dict]]:
    with AuthorizedPropertiesEnv(tenant_id="wire-shape-properties", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="wire-shape-properties")
        PrincipalFactory(tenant=tenant, principal_id="test_principal")
        # A verified publisher with no advertising_policy — exercises the "has
        # publishers" branch where every optional field but publisher_domains is unset.
        PublisherPartnerFactory(tenant=tenant, publisher_domain="example.com")
        yield env, {}


@contextmanager
def _performance_env() -> Iterator[tuple[object, dict]]:
    with PerformanceEnv(tenant_id="wire-shape-performance", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="wire-shape-performance")
        principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
        media_buy = MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_perf_wire_shape")
        yield (
            env,
            {
                "media_buy_id": media_buy.media_buy_id,
                "performance_data": [{"product_id": "prod_1", "performance_index": 1.0}],
            },
        )


@contextmanager
def _sync_creatives_env() -> Iterator[tuple[object, dict]]:
    with CreativeSyncEnv() as env:
        env.setup_default_data()
        creative = CreativeAssetFactory(creative_id="c_mcp_wire_shape", name="MCP Wire Shape Creative")
        yield env, {"creatives": [creative]}


_CASES = [
    WireOmissionCase(
        tool="get_products",
        setup=_get_products_env,
        # The full enveloped schema, whose products.items $refs core/product.json —
        # a strict superset of validating each product on its own.
        schema=_GET_PRODUCTS_SCHEMA,
        must_be_non_empty="products",
    ),
    WireOmissionCase(
        tool="get_adcp_capabilities",
        setup=_capabilities_env,
        schema="protocol/get-adcp-capabilities-response.json",
        # capabilities.py builds MediaBuy(portfolio=..., features=..., execution=...),
        # leaving supported_pricing_models unset — the exact field #1710 cited.
        absent_paths=["media_buy.supported_pricing_models"],
    ),
    WireOmissionCase(
        tool="list_authorized_properties",
        setup=_authorized_properties_env,
        # No pinned schema validates this response: ListAuthorizedPropertiesResponse is a
        # v2.4 shape and the pinned 3.1 tree has no list-authorized-properties-response.json
        # (2.5 had one, for a different shape). The field list carries the obligation.
        schema=None,
        absent_paths=[
            "primary_channels",
            "primary_countries",
            "portfolio_description",
            "advertising_policies",
            "last_updated",
            "errors",
        ],
    ),
    WireOmissionCase(
        tool="update_performance_index",
        setup=_performance_env,
        # No task for this tool resolves in the pinned 3.1 tree (it is listed in
        # _KNOWN_MISSING_SCHEMA_SKILLS). The pinned provide-performance-feedback-response
        # is a structurally different task — validating against it would fail on grounds
        # unrelated to null-omission. Pre-existing spec-grounding gap.
        schema=None,
        absent_paths=["context"],
    ),
    WireOmissionCase(
        tool="sync_creatives",
        setup=_sync_creatives_env,
        schema="sync-creatives-response.json",
        transports=(Transport.MCP,),
        narrowed_because=(
            "MCP is the transport with the to_jsonable_python bypass this grades: FastMCP "
            "serializes structured_content around model_dump() overrides, so A2A and REST "
            "never had the leak. Their coverage is the null-omission suite for those wires."
        ),
        must_be_non_empty="creatives",
    ),
]


# One test per (tool, transport) — NOT a loop inside one test, so a single
# transport regressing is one red test naming it, not one red test for the tool.
_CASE_TRANSPORTS = [(case, transport) for case in _CASES for transport in case.transports]


@pytest.mark.parametrize(
    ("case", "transport"), _CASE_TRANSPORTS, ids=lambda v: v.tool if isinstance(v, WireOmissionCase) else v.value
)
def test_wire_omits_unset_optional_fields(integration_db, case, transport):
    """The literal wire body omits every unset optional field."""
    with case.setup() as (env, call_kwargs):
        result = env.call_via(transport, **call_kwargs)

        if case.must_be_non_empty:
            values = (result.wire_response or {}).get(case.must_be_non_empty)
            assert isinstance(values, list) and values, (
                f"{case.tool}/{transport}: expected a non-empty '{case.must_be_non_empty}' list — "
                f"an empty body would pass the checks below without grading anything. Got {values!r}"
            )

        assert_wire_omits_unset(
            result,
            schema=case.schema,
            absent_paths=case.absent_paths,
            transport=transport,
        )


# ── The table's own two guardrails ──────────────────────────────────────────
# __post_init__ is what makes this table safe to add rows to: it is the only
# thing standing between a future row and the two failure modes that read as
# coverage while grading nothing. Neither arm fires on any row above — that is
# the point of them — so without these two tests both are dead code, and
# deleting either one leaves the whole file green. Construction only: no env,
# no wire, no DB (the module-level requires_db mark is inherited, harmlessly).


def test_case_with_no_schema_and_no_absent_paths_is_rejected():
    """A row that grades nothing must be unconstructible, not merely useless."""
    with pytest.raises(ValueError, match="grades nothing"):
        WireOmissionCase(tool="ungraded", setup=_get_products_env, schema=None)


def test_case_narrowed_without_a_reason_is_rejected():
    """Dropping a wire transport must cost a written reason."""
    with pytest.raises(ValueError, match="narrowed_because"):
        WireOmissionCase(
            tool="quietly_narrowed",
            setup=_get_products_env,
            schema=_GET_PRODUCTS_SCHEMA,
            transports=(Transport.MCP,),
        )


# ── Obligations that are NOT this table's operation ─────────────────────────
# Each asserts something structurally different from "these paths are absent
# from the wire". Folding them into a row would mean the row no longer means
# one thing.


def test_get_products_impl_payload_is_schema_valid(integration_db):
    """IMPL has no wire: this grades ``Product.model_dump`` directly, not a boundary.

    Kept because the null-leak lives in ``Product.model_dump`` itself, so a direct dump
    is the tightest failure signal. It is NOT a substitute for the three wire transports
    in the table and must never be the reason any of them is dropped.
    """
    with _get_products_env() as (env, call_kwargs):
        result = env.call_via(Transport.IMPL, **call_kwargs)
        assert result.is_success, f"IMPL get_products failed: {result.error}"

        response = result.payload.model_dump(mode="json")
        products = response.get("products")
        assert isinstance(products, list) and products, (
            f"IMPL: expected a non-empty 'products' list in the response, got {products!r}"
        )
        pinned_schema.validate_against_pinned_schema(_GET_PRODUCTS_SCHEMA, response)


def test_sync_creatives_mcp_wire_changes_and_warnings_are_arrays(integration_db):
    """Per-creative changes/warnings/errors are LISTS on the MCP wire, never null.

    A different obligation from absence: these three fields may legitimately be
    present, and when present the pinned 3.1.1 sync-creatives-response.json types
    them ``array`` — ``null`` is not a valid value. All three were redeclared with
    default_factory=list in creative.py for the same structured_content risk.
    Mutation check: revert any one redeclaration to inherit the parent's None
    default and this goes red on that field.
    """
    with _sync_creatives_env() as (env, call_kwargs):
        result = env.call_via(Transport.MCP, **call_kwargs)
        assert result.is_success, f"Expected success but got error: {result.error}"
        assert result.wire_response is not None, "MCP dispatch must stash the real structured_content wire"

        creatives = result.wire_response.get("creatives")
        assert isinstance(creatives, list) and creatives, f"MCP wire must carry the creatives array, got {creatives!r}"
        for i, item in enumerate(creatives):
            for name in ("changes", "warnings", "errors"):
                if name in item:
                    assert isinstance(item[name], list), (
                        f"creatives[{i}].{name} must be an array on the MCP wire "
                        f"(spec 3.1.1 types it array), got {item[name]!r}"
                    )


# get_signals / activate_signal are unreachable on EVERY transport: neither is
# registered as an MCP tool, A2A intentionally excludes signals, and no REST route
# exists — so there is no wire for a buyer to observe and no live-dispatch coverage
# is possible. This is the sweep's single documented exemption. Whether the dead code
# is removed or wired is tracked at
# https://github.com/prebid/salesagent/issues/1353; that issue retires the exemption
# either way. Until then these grade the typed model_dump() as defense-in-depth at the
# model layer, and are NOT wire coverage.


def test_get_signals_impl_payload_omits_signal_ref(integration_db):
    """Production never sets signal_ref on the mock signals it returns."""
    with GetSignalsEnv(tenant_id="wire-shape-signals", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="wire-shape-signals")
        PrincipalFactory(tenant=tenant, principal_id="test_principal")

        result = env.call_via(Transport.IMPL)
        assert result.is_success, f"get_signals failed: {result.error}"

        payload = result.payload.model_dump(mode="json")
        assert len(payload["signals"]) > 0, "expected non-empty signals list"
        assert "signal_ref" not in payload["signals"][0], (
            f"expected signals[0].signal_ref absent, got {payload['signals'][0].get('signal_ref')!r}"
        )


def test_activate_signal_impl_payload_omits_unset_fields(integration_db):
    """errors is explicitly None on success and context is unset — both must be absent."""
    with ActivateSignalEnv(tenant_id="wire-shape-activate-signal", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="wire-shape-activate-signal")
        PrincipalFactory(tenant=tenant, principal_id="test_principal")

        result = env.call_via(Transport.IMPL, signal_agent_segment_id="auto_intenders_q1_2025")
        assert result.is_success, f"activate_signal failed: {result.error}"

        payload = result.payload.model_dump(mode="json")
        for name in ("errors", "context"):
            assert name not in payload, f"expected '{name}' absent, got {payload.get(name)!r}"
