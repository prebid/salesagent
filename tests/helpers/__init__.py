"""Test helpers for creating AdCP-compliant test objects."""

from __future__ import annotations


def assert_resolve_auth_dep_passes_token(auth_token: str = "pre-extracted-token") -> None:
    """Assert _resolve_auth_dep passes auth_ctx.auth_token to resolve_identity().

    Shared assertion used by multiple test files to verify the token passthrough
    contract: the pre-extracted token must be forwarded without redundant
    re-extraction from headers.
    """
    from unittest.mock import patch

    from src.core.auth_context import AuthContext, _resolve_auth_dep
    from tests.factories.principal import PrincipalFactory

    auth_ctx = AuthContext(
        auth_token=auth_token,
        headers={"authorization": f"Bearer {auth_token}"},
    )
    mock_identity = PrincipalFactory.make_identity(
        principal_id="test_principal",
        tenant_id="default",
        tenant={"tenant_id": "default"},
        protocol="rest",
    )
    expected_headers = {"authorization": f"Bearer {auth_token}"}
    with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
        _resolve_auth_dep(auth_ctx)

    mock_resolve.assert_called_once_with(
        headers=expected_headers,
        auth_token=auth_token,
        require_valid_token=False,
        protocol="rest",
        testing_context=None,
    )


def assert_effective_properties_normalized(
    effective: list[dict],
    raw: list[dict],
    expected_selection_type: str,
) -> None:
    """Assert effective_properties is a non-destructive superset of raw profile data.

    Verifies:
    1. Every key/value from the raw profile dict is preserved in the output
    2. selection_type was added with the expected value
    3. Length matches (no entries dropped or added)
    """
    assert len(effective) == len(raw), f"Length mismatch: {len(effective)} != {len(raw)}"
    for i, (eff, orig) in enumerate(zip(effective, raw, strict=True)):
        for key, value in orig.items():
            assert key in eff, f"[{i}] Missing key {key!r} from original"
            assert eff[key] == value, f"[{i}] {key!r}: {eff[key]!r} != {value!r}"
        assert eff.get("selection_type") == expected_selection_type, (
            f"[{i}] selection_type: {eff.get('selection_type')!r} != {expected_selection_type!r}"
        )


# Lazily re-exported. An eager block here pulled ``adcp_factories`` — and with it
# factory-boy — into every importer of this package, including the leaf helpers
# that need nothing but the stdlib. That made an e2e service container exit on
# import before it ever bound a socket, and made COPYING a helper the cheapest
# correct move rather than importing it. A leaf that cannot be imported without
# its heaviest sibling is what manufactures duplication.
_LAZY_EXPORTS: dict[str, str] = {
    "SIGNATURE_HEADER": "tests.helpers.hmac_assertions",
    "TIMESTAMP_HEADER": "tests.helpers.hmac_assertions",
    "assert_backoff_schedule": "tests.helpers.backoff_assertions",
    "assert_envelope_shape": "tests.helpers.envelope_assertions",
    "assert_no_raw_validation_leak": "tests.helpers.envelope_assertions",
    "assert_delivered_unsigned": "tests.helpers.hmac_assertions",
    "assert_signature_verifies_over_wire_body": "tests.helpers.hmac_assertions",
    "create_minimal_product": "tests.helpers.adcp_factories",
    "create_product_with_empty_pricing": "tests.helpers.adcp_factories",
    "create_test_brand_manifest": "tests.helpers.adcp_factories",
    "create_test_creative_asset": "tests.helpers.adcp_factories",
    "create_test_format": "tests.helpers.adcp_factories",
    "create_test_format_id": "tests.helpers.adcp_factories",
    "create_test_media_buy_dict": "tests.helpers.adcp_factories",
    "create_test_media_buy_request_dict": "tests.helpers.adcp_factories",
    "create_test_package": "tests.helpers.adcp_factories",
    "create_test_package_request": "tests.helpers.adcp_factories",
    "create_test_package_request_dict": "tests.helpers.adcp_factories",
    "create_test_pricing_option": "tests.helpers.adcp_factories",
    "create_test_product": "tests.helpers.adcp_factories",
    "create_test_property": "tests.helpers.adcp_factories",
    "create_test_property_dict": "tests.helpers.adcp_factories",
    "load_ledger_nodeids": "tests.helpers.ledger",
    "make_active_cached_success": "tests.helpers.idempotency_seeds",
    "rendered_log_calls": "tests.helpers.log_assertions",
    "seed_cached_success": "tests.helpers.idempotency_seeds",
    "seed_media_buy": "tests.helpers.idempotency_seeds",
    "seed_principal": "tests.helpers.idempotency_seeds",
}


def __getattr__(name: str):
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


__all__ = [
    # Auth helpers
    "assert_resolve_auth_dep_passes_token",
    # Backoff schedule assertions
    "assert_backoff_schedule",
    # Envelope assertions
    "assert_envelope_shape",
    "assert_no_raw_validation_leak",
    # HMAC signature assertions
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "assert_signature_verifies_over_wire_body",
    "assert_delivered_unsigned",
    # Known-failures ledger parsing
    "load_ledger_nodeids",
    # Log-call assertions
    "rendered_log_calls",
    # Idempotency cache seeding
    "make_active_cached_success",
    "seed_cached_success",
    "seed_media_buy",
    "seed_principal",
    # Product factories
    "create_test_product",
    "create_minimal_product",
    "create_product_with_empty_pricing",
    # Format factories
    "create_test_format_id",
    "create_test_format",
    # Property factories
    "create_test_property_dict",
    "create_test_property",
    # Package factories
    "create_test_package",
    "create_test_package_request",
    "create_test_package_request_dict",
    # Media buy factories (dict-based due to schema duplication issues)
    "create_test_media_buy_request_dict",
    "create_test_media_buy_dict",
    # Other object factories
    "create_test_creative_asset",
    "create_test_brand_manifest",
    "create_test_pricing_option",
]
