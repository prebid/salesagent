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

Not everything here is a row. Two obligations below are genuinely different
operations that merely lived next door: get_products' IMPL-level dump (no wire by
definition) and sync_creatives' "these fields are arrays, not just non-null".
Collapsing those into the table would mean pretending different assertions are the
same one, which is how the drift above started.

A third pair lived here until 2026-09-03: get_signals / activate_signal, carried as
"the sweep's single documented exemption" because neither was reachable on any
transport. They were unreachable because #826 unregistered them in 2025-12 and left
the implementation behind; the implementation is now deleted, so the exemption is
retired by removal rather than by wiring (GH #1353).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import pytest

from tests.factories import (
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    TenantFactory,
)
from tests.factories.creative_asset import CreativeAssetFactory
from tests.harness.assertions import assert_wire_omits_unset
from tests.harness.capabilities import CapabilitiesEnv
from tests.harness.creative_sync import CreativeSyncEnv
from tests.harness.product import ProductEnv
from tests.harness.transport import Transport

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
def _sync_creatives_env() -> Iterator[tuple[object, dict]]:
    with CreativeSyncEnv() as env:
        # account is in sync-creatives-request.json /required, so the call cannot be built
        # without one. setup_default_account seeds the row and hands back the matching
        # reference; setup_default_data runs inside it, so the tenant the FK names exists.
        account = env.default_account_reference()
        creative = CreativeAssetFactory(creative_id="c_mcp_wire_shape", name="MCP Wire Shape Creative")
        yield env, {"creatives": [creative], "account": account}


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
        # capabilities.py builds MediaBuy(portfolio, features, execution,
        # supported_pricing_models, creative_approval_mode) and sets nothing else, so
        # every field below is structurally unset and must be OMITTED, not null.
        #
        # These three are chosen because they default to None: a field whose default is
        # non-None (buying_modes, supports_proposals, governance_aware,
        # propagation_surfaces) is legitimately present and grades nothing here.
        #
        # This row originally cited media_buy.supported_pricing_models — "the exact field
        # #1710 cited" — which capabilities.py has since implemented (it is now a
        # populated pre-flight buyer signal). A field going from unset to implemented is
        # the expected way this row goes stale: re-point it at another structurally-unset
        # field, never delete the row and never assert the implemented field is absent.
        absent_paths=[
            "media_buy.audience_targeting",
            "media_buy.frequency_capping",
            "media_buy.conversion_tracking",
        ],
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
# coverage while grading nothing. Neither branch fires on any row above — that is
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


def test_every_row_names_a_tool_a_transport_can_reach():
    """A row for a tool with no registry entry has no wire to grade, on any transport.

    ``update_performance_index`` was such a row. It is not in
    ``src/core/tools/registry.py``, so MCP registers no tool for it, the A2A card
    advertises no skill, and no REST route exists -- all three of its parametrisations
    failed on "no such tool", which reads as a wire regression and is not one.

    Derived from ``TOOLS`` rather than restated here, so the next tool to leave the
    registry fails this one test by name instead of three cases by symptom.
    """
    from src.core.tools.registry import TOOLS

    unreachable = sorted({case.tool for case in _CASES} - set(TOOLS))
    assert unreachable == [], (
        f"these rows name tools no transport can reach: {unreachable}. A tool without a "
        f"registry row has no MCP registration, no A2A skill and no REST route, so the "
        f"table cannot dispatch it."
    )
