"""Direct unit tests for ``pricing_option_is_priced``.

Regression coverage for issue #1246. Previously this helper was named
``pricing_option_has_rate`` and only checked the v2 field name ``rate``,
missing v3 ``fixed_price``/``floor_price``/``price_guidance``. There was zero
direct unit-test coverage of the helper itself — it was tested only
incidentally through ``GetProductsResponse.__str__``, where every test fixture
used the v2 dict shape that happened to match the broken assumption. This file
pins the helper's contract across all five input shapes used by the codebase.
"""

from decimal import Decimal
from typing import Any
from unittest.mock import Mock

import pytest

from src.core.helpers.pricing_helpers import pricing_option_is_priced
from tests.helpers.adcp_factories import (
    create_test_cpm_pricing_option,  # v2 dict shape
    create_test_cpm_pricing_option_auction,  # v3 dict, floor_price + price_guidance
    create_test_cpm_pricing_option_v3,  # v3 dict, fixed_price
    create_test_pricing_option_library,  # adcp library RootModel (production wire)
    create_test_pricing_option_pydantic,  # internal Pydantic PricingOption
)

# ---------------------------------------------------------------------------
# Shape 1: dict (JSON / serialized payload)
# ---------------------------------------------------------------------------


class TestDictShape:
    def test_dict_with_v2_rate_returns_true(self) -> None:
        po = create_test_cpm_pricing_option(rate=10.0)
        assert pricing_option_is_priced(po) is True

    def test_dict_with_v3_fixed_price_returns_true(self) -> None:
        po = create_test_cpm_pricing_option_v3(fixed_price=10.0)
        assert pricing_option_is_priced(po) is True

    def test_dict_with_v3_floor_price_returns_true(self) -> None:
        po = create_test_cpm_pricing_option_auction(floor_price=5.0, price_guidance=None)
        # Strip price_guidance so this case isolates floor_price as the driver.
        po.pop("price_guidance", None)
        assert pricing_option_is_priced(po) is True

    def test_dict_with_v3_price_guidance_only_returns_true(self) -> None:
        # Spec-legal v3 auction shape: percentile hints, no top-level floor.
        po = {
            "pricing_option_id": "cpm_v3_pg_only",
            "pricing_model": "cpm",
            "currency": "USD",
            "price_guidance": {"p25": 4.0, "p50": 6.0, "p75": 8.0},
        }
        assert pricing_option_is_priced(po) is True

    def test_dict_with_no_rate_bearing_fields_returns_false(self) -> None:
        po = {
            "pricing_option_id": "bare",
            "pricing_model": "cpm",
            "currency": "USD",
            # No rate / fixed_price / floor_price / price_guidance.
        }
        assert pricing_option_is_priced(po) is False

    def test_dict_with_currency_only_returns_false(self) -> None:
        po: dict[str, Any] = {"currency": "USD"}
        assert pricing_option_is_priced(po) is False

    def test_dict_with_min_spend_only_returns_false(self) -> None:
        # min_spend_per_package is a non-rate field — buyer doesn't see a price.
        po = {
            "pricing_option_id": "ms_only",
            "pricing_model": "cpm",
            "currency": "USD",
            "min_spend_per_package": 1000,
        }
        assert pricing_option_is_priced(po) is False

    def test_dict_empty_returns_false(self) -> None:
        assert pricing_option_is_priced({}) is False

    def test_dict_with_both_fixed_and_floor_returns_true(self) -> None:
        # Library RootModel allows this even though spec/internal class forbid;
        # any() short-circuits on the first match.
        po = {
            "pricing_option_id": "both",
            "pricing_model": "cpm",
            "currency": "USD",
            "fixed_price": 10.0,
            "floor_price": 5.0,
        }
        assert pricing_option_is_priced(po) is True

    def test_dict_with_decimal_zero_returns_true(self) -> None:
        # Presence semantics: zero price is a price (not absence).
        po = {"pricing_option_id": "z", "pricing_model": "cpm", "currency": "USD", "fixed_price": 0.0}
        assert pricing_option_is_priced(po) is True

    def test_dict_with_explicit_none_field_returns_false(self) -> None:
        po = {
            "pricing_option_id": "n",
            "pricing_model": "cpm",
            "currency": "USD",
            "fixed_price": None,
            "floor_price": None,
            "price_guidance": None,
        }
        assert pricing_option_is_priced(po) is False


# ---------------------------------------------------------------------------
# Shape 2: adcp library PricingOption RootModel (production wire type)
# ---------------------------------------------------------------------------


class TestLibraryRootModelShape:
    def test_library_with_fixed_price_returns_true(self) -> None:
        po = create_test_pricing_option_library(fixed_price=10.0)
        assert pricing_option_is_priced(po) is True

    def test_library_with_floor_price_returns_true(self) -> None:
        po = create_test_pricing_option_library(floor_price=5.0)
        assert pricing_option_is_priced(po) is True

    def test_library_with_price_guidance_only_returns_true(self) -> None:
        po = create_test_pricing_option_library(price_guidance={"p25": 4.0, "p50": 6.0})
        assert pricing_option_is_priced(po) is True

    def test_library_with_no_rate_fields_returns_false(self) -> None:
        # Library doesn't enforce XOR — both rate fields can be None.
        po = create_test_pricing_option_library()
        assert pricing_option_is_priced(po) is False


# ---------------------------------------------------------------------------
# Shape 3: adcp library typed option (concrete CpmPricingOption etc., no RootModel wrap)
# ---------------------------------------------------------------------------


class TestLibraryTypedShape:
    def test_typed_with_fixed_price_returns_true(self) -> None:
        po = create_test_pricing_option_library(fixed_price=10.0)
        # Pass the inner concrete type directly (no RootModel wrap).
        assert pricing_option_is_priced(po.root) is True

    def test_typed_with_floor_price_returns_true(self) -> None:
        po = create_test_pricing_option_library(floor_price=5.0)
        assert pricing_option_is_priced(po.root) is True

    def test_typed_with_no_rate_fields_returns_false(self) -> None:
        po = create_test_pricing_option_library()
        assert pricing_option_is_priced(po.root) is False


# ---------------------------------------------------------------------------
# Shape 4: internal Pydantic PricingOption (XOR-validated)
# ---------------------------------------------------------------------------


class TestInternalPydanticShape:
    def test_internal_with_fixed_price_returns_true(self) -> None:
        po = create_test_pricing_option_pydantic(fixed_price=10.0)
        assert pricing_option_is_priced(po) is True

    def test_internal_with_floor_price_returns_true(self) -> None:
        po = create_test_pricing_option_pydantic(floor_price=5.0)
        assert pricing_option_is_priced(po) is True

    # The XOR validator forbids "neither" — the "no rate fields" edge case
    # cannot be constructed through the internal Pydantic class. That case is
    # covered via the dict and library-RootModel shapes above.


# ---------------------------------------------------------------------------
# Shape 5: SQLAlchemy ORM-style direct attribute access
# ---------------------------------------------------------------------------


class TestOrmStyleDirectAttribute:
    def test_orm_with_rate_attribute_returns_true(self) -> None:
        # ORM column is literally named ``rate`` (legacy v2 name); the helper
        # must keep recognizing it for ORM-row callers.
        po = Mock(spec=["pricing_model", "currency", "rate", "is_fixed", "price_guidance"])
        po.rate = Decimal("5.00")
        po.is_fixed = True
        po.price_guidance = None
        assert pricing_option_is_priced(po) is True

    def test_orm_auction_with_rate_none_and_price_guidance_returns_true(self) -> None:
        # Legacy auction ORM row: rate=None, price_guidance dict carries the floor.
        po = Mock(spec=["pricing_model", "currency", "rate", "is_fixed", "price_guidance"])
        po.rate = None
        po.is_fixed = False
        po.price_guidance = {"floor": 5.0, "p50": 8.0}
        assert pricing_option_is_priced(po) is True

    def test_orm_with_all_rate_fields_none_returns_false(self) -> None:
        po = Mock(spec=["pricing_model", "currency", "rate", "is_fixed", "price_guidance"])
        po.rate = None
        po.is_fixed = False
        po.price_guidance = None
        assert pricing_option_is_priced(po) is False


# ---------------------------------------------------------------------------
# Defensive / edge-case shapes
# ---------------------------------------------------------------------------


class TestDefensiveShapes:
    def test_none_input_returns_false(self) -> None:
        # Defensive: should not crash on None.
        assert pricing_option_is_priced(None) is False

    def test_unrelated_object_returns_false(self) -> None:
        # Plain object with no rate-bearing attributes — graceful False.
        class Empty:
            pass

        assert pricing_option_is_priced(Empty()) is False

    def test_object_with_unrelated_rate_attribute_returns_false_when_none(self) -> None:
        class Holder:
            rate = None
            fixed_price = None
            floor_price = None
            price_guidance = None

        assert pricing_option_is_priced(Holder()) is False

    def test_object_with_fixed_price_attribute_returns_true(self) -> None:
        class Holder:
            fixed_price = 10.0

        assert pricing_option_is_priced(Holder()) is True


# ---------------------------------------------------------------------------
# Cross-shape parametrized sanity check — same field, different shape, same answer.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make_priced",
    [
        pytest.param(lambda: create_test_cpm_pricing_option(rate=10.0), id="v2-dict-rate"),
        pytest.param(lambda: create_test_cpm_pricing_option_v3(fixed_price=10.0), id="v3-dict-fixed_price"),
        pytest.param(
            lambda: create_test_cpm_pricing_option_auction(floor_price=5.0),
            id="v3-dict-floor_price",
        ),
        pytest.param(lambda: create_test_pricing_option_library(fixed_price=10.0), id="library-rootmodel-fixed"),
        pytest.param(lambda: create_test_pricing_option_library(floor_price=5.0), id="library-rootmodel-floor"),
        pytest.param(lambda: create_test_pricing_option_library(fixed_price=10.0).root, id="library-typed-fixed"),
        pytest.param(lambda: create_test_pricing_option_pydantic(fixed_price=10.0), id="internal-pydantic-fixed"),
        pytest.param(lambda: create_test_pricing_option_pydantic(floor_price=5.0), id="internal-pydantic-floor"),
    ],
)
def test_priced_options_return_true_across_shapes(make_priced) -> None:
    assert pricing_option_is_priced(make_priced()) is True
