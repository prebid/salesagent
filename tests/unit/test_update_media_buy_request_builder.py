"""Lock the wire-shape invariants on ``build_update_media_buy_request``.

adcp 4.4 made ``account`` and ``idempotency_key`` required on
``UpdateMediaBuyRequest`` (see
``adcp/types/generated_poc/bundled/media_buy/update_media_buy_request.py:7544``
and ``:7685``). The e2e builder must inject both so tests behave like
real buyers and don't get rejected at the SDK validation boundary.

These tests live at unit-tier so they run in every CI cycle —
breakages get caught before the slow e2e suite even starts.
"""

from __future__ import annotations

from tests.e2e.adcp_request_builder import (
    _build_reporting_webhook,
    _inject_wire_required_fields,
    build_adcp_media_buy_request,
    build_update_media_buy_request,
)


def test_request_has_required_account_field():
    req = build_update_media_buy_request(media_buy_id="mb_test")
    account = req.get("account")
    assert account is not None, "AdCP 4.4 requires `account` on UpdateMediaBuyRequest"
    # Natural-key shape: brand + operator. account_id form is also valid
    # but not what the builder synthesises by default.
    assert "brand" in account
    assert "operator" in account


def test_request_has_required_idempotency_key():
    req = build_update_media_buy_request(media_buy_id="mb_test")
    key = req.get("idempotency_key")
    assert isinstance(key, str), "AdCP 4.4 requires `idempotency_key` on UpdateMediaBuyRequest"
    assert len(key) >= 16, "idempotency_key spec: minLength 16"
    # Pattern allowed chars per spec: ^[A-Za-z0-9_.:-]{16,255}$
    import re

    assert re.match(r"^[A-Za-z0-9_.:-]{16,255}$", key), f"idempotency_key '{key}' must match the spec's character set"


def test_each_call_generates_a_fresh_idempotency_key():
    """Idempotency keys must be unique per-request — buyers retrying the
    SAME logical request reuse one key (handled at the test layer above).
    The builder must not memoise."""
    a = build_update_media_buy_request(media_buy_id="mb_test")
    b = build_update_media_buy_request(media_buy_id="mb_test")
    assert a["idempotency_key"] != b["idempotency_key"]


def test_brand_override_propagates_to_account():
    """When the test passes a custom brand, the synthesised ``account``
    natural key uses it — keeps the account/brand pair consistent across
    request and downstream lookups."""
    req = build_update_media_buy_request(
        media_buy_id="mb_test",
        brand={"domain": "acme.com", "brand_id": "spark"},
    )
    assert req["account"]["brand"] == {"domain": "acme.com", "brand_id": "spark"}
    assert req["account"]["operator"] == "acme.com"


def test_optional_fields_only_present_when_passed():
    """Bare-minimum request: only the required fields. No phantom None
    values for active / budget / packages / push_notification_config."""
    req = build_update_media_buy_request(media_buy_id="mb_test")
    assert "active" not in req
    assert "budget" not in req
    assert "packages" not in req
    assert "push_notification_config" not in req
    assert "context" not in req


def test_passes_webhook_with_correct_authentication_shape():
    """AdCP 4.4 Authentication requires ``schemes`` (Bearer or HMAC-SHA256)
    and ``credentials`` (min length 32). The legacy ``{"type": "none"}``
    shape was rejected by SDK validation post-4.4."""
    req = build_update_media_buy_request(
        media_buy_id="mb_test",
        webhook_url="https://example.com/cb",
    )
    pnc = req["push_notification_config"]
    assert pnc["url"] == "https://example.com/cb"
    auth = pnc["authentication"]
    assert auth["schemes"] == ["Bearer"]
    assert isinstance(auth["credentials"], str) and len(auth["credentials"]) >= 32


# ---------------------------------------------------------------------------
# _inject_wire_required_fields helper — shared by create + update + (future) sync
# ---------------------------------------------------------------------------


def test_helper_injects_account_and_idempotency_key_with_prefix():
    """The shared helper must add both required fields with the
    requested prefix on the idempotency_key."""
    request: dict = {}
    _inject_wire_required_fields(request, brand={"domain": "acme.com"}, idempotency_prefix="e2e-test")
    assert request["account"] == {"brand": {"domain": "acme.com"}, "operator": "acme.com"}
    assert request["idempotency_key"].startswith("e2e-test-")


def test_helper_falls_back_to_default_brand_when_brand_is_none():
    """``brand=None`` triggers the default ``{"domain": "testbrand.com"}`` —
    keeps callers that don't care about brand from having to construct one."""
    request: dict = {}
    _inject_wire_required_fields(request, brand=None, idempotency_prefix="x")
    assert request["account"]["brand"] == {"domain": "testbrand.com"}
    assert request["account"]["operator"] == "testbrand.com"


def test_helper_build_reporting_webhook_is_4_4_compliant():
    """``_build_reporting_webhook`` must produce a dict that satisfies
    AdCP 4.4's Authentication schema: ``schemes`` array of size 1 with
    Bearer/HMAC-SHA256, ``credentials`` of length >= 32."""
    block = _build_reporting_webhook("https://example.com/cb")
    assert block["url"] == "https://example.com/cb"
    auth = block["authentication"]
    assert auth["schemes"] == ["Bearer"]
    assert len(auth["credentials"]) >= 32
    # ``reporting_frequency`` is opt-in (only relevant on
    # create_media_buy.reporting_webhook, not update.push_notification_config).
    assert "reporting_frequency" not in block


def test_helper_build_reporting_webhook_includes_frequency_when_supplied():
    block = _build_reporting_webhook("https://example.com/cb", reporting_frequency="daily")
    assert block["reporting_frequency"] == "daily"


def test_create_and_update_builders_use_consistent_account_shape():
    """Regression: a future drift between the create and update builders'
    account-synthesis would break tenant lookups during e2e flows where
    the same brand is used to create then update a media buy."""
    create_req = build_adcp_media_buy_request(
        product_ids=["p1"],
        total_budget=1000.0,
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-31T00:00:00Z",
        brand={"domain": "shared-brand.com"},
    )
    update_req = build_update_media_buy_request(
        media_buy_id="mb_test",
        brand={"domain": "shared-brand.com"},
    )
    assert create_req["account"] == update_req["account"]
