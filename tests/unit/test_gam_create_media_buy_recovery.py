"""GAM create_media_buy mutation-boundary recovery + deterministic naming (#1637).

The mutation boundary starts AT ``orders_manager.create_order`` — the actual remote
mutation point — not at the later ``_finish_create_media_buy`` stage. A failure raised
from ``create_order`` is ambiguous (createOrders may have COMMITTED before a timeout
severed the response), so ``create_media_buy`` runs a recovery lookup by the DETERMINISTIC
order name:

* lookup finds the order  → mutation happened; recover its id and finish normally,
* lookup finds nothing     → NOT proof of pre-mutation. GAM createOrders is not
  read-your-writes consistent, so a committed order can be briefly invisible; a bounded
  retry absorbs that lag, and a persistent miss FAILS SAFE to
  ``AdapterPostMutationIncomplete`` (remote state unknown → manual reconciliation),
* lookup itself fails      → ambiguous; raise ``AdapterPostMutationIncomplete``.

The order name is deterministic (the idempotency key, or a stable hash of
``(tenant_id, buyer_ref)``) so a retry searches for the SAME name and never mints a
second order.
"""

import re
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from src.adapters.base import AdapterIdempotencyUncertain, AdapterPostMutationIncomplete
from src.adapters.google_ad_manager import GoogleAdManager

_START = datetime(2024, 1, 1, tzinfo=UTC)
_END = datetime(2024, 12, 31, tzinfo=UTC)


def _make_db_session():
    """Mock db session covering create_media_buy's scalars() calls (products/inventory,
    adapter config, tenant). Mirrors tests/unit/test_gam_products_map_delivery_type.py."""
    product = Mock()
    product.product_id = "prod_abc"
    product.delivery_type = "guaranteed"
    product.implementation_config = {"targeted_ad_unit_ids": ["12345"]}

    call_count = 0

    def scalars_side_effect(stmt):
        nonlocal call_count
        call_count += 1
        result = Mock()
        if call_count == 1:
            result.first.return_value = product  # Product lookup
        elif call_count == 2:
            result.all.return_value = []  # ProductInventoryMapping
        else:
            result.first.return_value = None  # AdapterConfig / Tenant
            result.all.return_value = []
        return result

    session = Mock()
    session.scalars.side_effect = scalars_side_effect
    return session


def _make_request(idempotency_key="stable-idempotency-key"):
    req_package = Mock()
    req_package.package_id = "pkg_prod_abc_001"
    request = Mock()
    request.packages = [req_package]
    request.idempotency_key = idempotency_key
    request.get_total_budget = Mock(return_value=10000)
    request.push_notification_config = None
    return request


def _make_package():
    package = Mock()
    package.package_id = "pkg_prod_abc_001"
    package.product_id = "prod_abc"
    package.targeting_overlay = None
    return package


_PRICING = {
    "pkg_prod_abc_001": {
        "pricing_model": "cpm",
        "rate": 5.0,
        "currency": "USD",
        "is_fixed": True,
        "bid_price": None,
    }
}


def _make_adapter():
    """ONE shared fake: a spec'd GoogleAdManager with a mocked orders_manager and the
    REAL create_media_buy / _finish_create_media_buy bound so the boundary logic runs."""
    adapter = Mock(spec=GoogleAdManager)
    adapter.tenant_id = "tenant_test"
    adapter.advertiser_id = "adv_123"
    adapter.trafficker_id = "traff_123"
    adapter.log = Mock()
    adapter.orders_manager = Mock()
    adapter.workflow_manager = Mock()
    adapter.targeting_manager = Mock()
    adapter._requires_manual_approval = Mock(return_value=False)
    adapter._validate_targeting = Mock(return_value=[])
    adapter._check_order_has_guaranteed_items = Mock(return_value=(False, []))
    adapter._placement_targeting_map = {}
    adapter._order_name_template = None
    adapter._line_item_name_template = None
    adapter.orders_manager.create_line_items = Mock(return_value=["li_001"])
    adapter.orders_manager.approve_order = Mock(return_value=True)
    adapter._finish_create_media_buy = GoogleAdManager._finish_create_media_buy.__get__(adapter)
    adapter._recover_order_id_after_create_failure = GoogleAdManager._recover_order_id_after_create_failure.__get__(
        adapter
    )
    return adapter


def _run(adapter, request, packages):
    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_db.return_value.__enter__.return_value = _make_db_session()
        return GoogleAdManager.create_media_buy(
            adapter,
            request=request,
            packages=packages,
            start_time=_START,
            end_time=_END,
            package_pricing_info=_PRICING,
        )


def test_create_failure_with_lookup_hit_recovers_id_with_single_remote_create():
    """Timeout AFTER commit + recovery lookup finds the order → recover its id and finish,
    with exactly ONE remote create attempt (never a second createOrders)."""
    adapter = _make_adapter()
    adapter.orders_manager.create_order = Mock(side_effect=TimeoutError("post-commit timeout"))
    adapter.orders_manager.find_order_by_name = Mock(return_value="999")

    finalizer_order_ids: list[str] = []

    def capture_line_items(**kwargs):
        finalizer_order_ids.append(kwargs["order_id"])
        return ["li_001"]

    adapter.orders_manager.create_line_items = Mock(side_effect=capture_line_items)

    _run(adapter, _make_request(), [_make_package()])

    assert adapter.orders_manager.create_order.call_count == 1  # NO duplicate remote create
    assert adapter.orders_manager.find_order_by_name.call_count == 1
    # The recovered id must flow into the post-mutation finalizer.
    assert finalizer_order_ids == ["999"]


def test_create_failure_with_persistent_lookup_miss_fails_safe_to_post_mutation_incomplete():
    """Recovery lookup finds nothing on EVERY bounded attempt → still NOT proof of
    pre-mutation (GAM createOrders is not read-your-writes consistent, so a committed
    order can stay invisible), so FAIL SAFE to AdapterPostMutationIncomplete — the SAME
    conservative outcome as the lookup-failed branch, never a plain retryable re-raise.

    Contract change (#1637): the previous test asserted a lookup miss re-raised the
    original error as a plain PRE-mutation failure. That encoded the WRONG contract — a
    miss could be a committed-but-invisible order, so treating it as pre-mutation risks a
    duplicate remote create on retry. Updated to pin the corrected fail-safe behaviour.
    """
    adapter = _make_adapter()
    boom = RuntimeError("createOrders ambiguous timeout")
    adapter.orders_manager.create_order = Mock(side_effect=boom)
    adapter.orders_manager.find_order_by_name = Mock(return_value=None)

    with patch("src.adapters.google_ad_manager.time.sleep") as mock_sleep:
        with pytest.raises(AdapterPostMutationIncomplete):
            _run(adapter, _make_request(), [_make_package()])

    # Bounded retry: the deterministic-name lookup is attempted the full budget before the
    # miss is concluded, absorbing GAM's create-visibility lag (backoff between attempts).
    assert adapter.orders_manager.find_order_by_name.call_count == 3
    assert mock_sleep.call_count == 2  # backoff between the 3 attempts, none after the last
    adapter.orders_manager.create_line_items.assert_not_called()


def test_create_failure_with_lookup_failure_raises_post_mutation_incomplete():
    """Recovery lookup itself fails → remote state unknown, a mutation MAY exist →
    AdapterPostMutationIncomplete (never AdapterIdempotencyUncertain)."""
    adapter = _make_adapter()
    adapter.orders_manager.create_order = Mock(side_effect=TimeoutError("post-commit timeout"))
    adapter.orders_manager.find_order_by_name = Mock(side_effect=RuntimeError("lookup RPC down"))

    with pytest.raises(AdapterPostMutationIncomplete):
        _run(adapter, _make_request(), [_make_package()])


def test_pre_create_idempotency_uncertain_reraises_without_recovery_lookup():
    """AdapterIdempotencyUncertain from create_order's PRE-create lookup is provably
    pre-mutation → propagate unchanged; no post-failure recovery lookup is attempted."""
    adapter = _make_adapter()
    adapter.orders_manager.create_order = Mock(side_effect=AdapterIdempotencyUncertain("pre-create lookup failed"))
    adapter.orders_manager.find_order_by_name = Mock(return_value="should-not-be-used")

    with pytest.raises(AdapterIdempotencyUncertain):
        _run(adapter, _make_request(), [_make_package()])

    adapter.orders_manager.find_order_by_name.assert_not_called()


def test_two_invocations_same_request_produce_byte_identical_order_names():
    """Deterministic naming: two invocations of the SAME request (same idempotency_key,
    no explicit key arg) produce byte-identical order names, so a retry's pre-create
    lookup matches and reuses the existing order."""
    captured: list[str] = []

    def capture_order_name(**kwargs):
        captured.append(kwargs["order_name"])
        return "order_1"

    request = _make_request(idempotency_key="ref-determinism")
    packages = [_make_package()]

    for _ in range(2):
        adapter = _make_adapter()
        adapter.orders_manager.create_order = Mock(side_effect=capture_order_name)
        _run(adapter, request, packages)

    assert len(captured) == 2
    assert captured[0] == captured[1]


def test_idempotency_key_drives_deterministic_order_name():
    """When an idempotency key is supplied (approval-replay path), the order name embeds
    it deterministically and is identical across attempts."""
    captured: list[str] = []

    def capture_order_name(**kwargs):
        captured.append(kwargs["order_name"])
        return "order_1"

    request = _make_request()
    packages = [_make_package()]

    for _ in range(2):
        adapter = _make_adapter()
        adapter.orders_manager.create_order = Mock(side_effect=capture_order_name)
        with patch("src.core.database.database_session.get_db_session") as mock_db:
            mock_db.return_value.__enter__.return_value = _make_db_session()
            GoogleAdManager.create_media_buy(
                adapter,
                request=request,
                packages=packages,
                start_time=_START,
                end_time=_END,
                package_pricing_info=_PRICING,
                idempotency_key="mb_persisted_123",
            )

    assert captured[0] == captured[1]
    assert "mb_mb_persisted_123" in captured[0]


def test_fallback_anchor_uses_128_bit_hash_for_collision_resistance():
    """Collision resistance (#1637): the no-idempotency-key fallback anchor embeds 128 bits
    (32 hex chars) of the SHA-256 digest, NOT 32 bits. At 32 bits the birthday bound gives
    ~50% collision near ~77k buys/tenant, so two DISTINCT idempotency keys could hash to one
    order name and the second buy would silently reuse the first's order. Pin the width."""
    captured: list[str] = []

    def capture_order_name(**kwargs):
        captured.append(kwargs["order_name"])
        return "order_1"

    adapter = _make_adapter()
    adapter.orders_manager.create_order = Mock(side_effect=capture_order_name)
    _run(adapter, _make_request(idempotency_key="anchor-width"), [_make_package()])

    assert len(captured) == 1
    match = re.search(r"\[mb_gam_([0-9a-f]+)\]", captured[0])
    assert match is not None, f"deterministic anchor suffix not found in {captured[0]!r}"
    assert len(match.group(1)) == 32  # 128 bits — not the old 8-hex (32-bit) anchor
