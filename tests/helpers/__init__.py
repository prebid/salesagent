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


from tests.helpers.adcp_factories import (
    create_minimal_product,
    create_product_with_empty_pricing,
    create_test_brand_manifest,
    create_test_creative_asset,
    create_test_format,
    create_test_format_id,
    create_test_media_buy_dict,
    create_test_media_buy_request_dict,
    create_test_package,
    create_test_package_request,
    create_test_package_request_dict,
    create_test_pricing_option,
    create_test_product,
    create_test_property,
    create_test_property_dict,
)
from tests.helpers.envelope_assertions import assert_envelope_shape, error_envelope_for_raw_a2a_env
from tests.helpers.idempotency_seeds import (
    make_active_cached_success,
    seed_cached_success,
    seed_media_buy,
    seed_principal,
)

__all__ = [
    # Auth helpers
    "assert_resolve_auth_dep_passes_token",
    # Envelope assertions
    "assert_envelope_shape",
    "error_envelope_for_raw_a2a_env",
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
