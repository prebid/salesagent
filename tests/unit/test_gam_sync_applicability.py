"""Unit coverage for GAM-derived sync applicability predicates."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.services.gam_sync_applicability import (
    gam_pricing_availability_applicable,
    gam_reporting_applicable,
    gam_signal_coverage_applicable,
    tenant_has_custom_key_value_signals,
    tenant_has_pricing_availability_targets,
)


def _product(config: dict) -> SimpleNamespace:
    return SimpleNamespace(effective_implementation_config=config)


def _signal(adapter_config: dict) -> SimpleNamespace:
    return SimpleNamespace(adapter_config=adapter_config)


def test_signal_coverage_is_applicable_for_custom_key_value_signals() -> None:
    with patch("src.services.gam_sync_applicability.TenantSignalRepository") as repo:
        repo.return_value.list_all.return_value = [
            _signal({"kind": "audience_segment", "segment_id": "123"}),
            _signal({"kind": "custom_key_value", "key_id": "456"}),
        ]

        assert tenant_has_custom_key_value_signals(SimpleNamespace(), "tenant_1") is True


def test_signal_coverage_is_not_applicable_without_custom_key_value_signals() -> None:
    with patch("src.services.gam_sync_applicability.TenantSignalRepository") as repo:
        repo.return_value.list_all.return_value = [
            _signal({"kind": "audience_segment", "segment_id": "123"}),
            _signal({}),
        ]

        assert tenant_has_custom_key_value_signals(SimpleNamespace(), "tenant_1") is False


def test_pricing_availability_is_applicable_for_ad_unit_only_products() -> None:
    with patch("src.services.gam_sync_applicability.ProductRepository") as repo:
        repo.return_value.list_all_with_inventory.return_value = [_product({"targeted_ad_unit_ids": ["23313239368"]})]

        assert tenant_has_pricing_availability_targets(SimpleNamespace(), "tenant_1") is True


def test_pricing_availability_is_applicable_for_placement_products() -> None:
    with patch("src.services.gam_sync_applicability.ProductRepository") as repo:
        repo.return_value.list_all_with_inventory.return_value = [
            _product({"targeted_ad_unit_ids": ["23313239368"], "targeted_placement_ids": ["31999908"]})
        ]

        assert tenant_has_pricing_availability_targets(SimpleNamespace(), "tenant_1") is True


def test_pricing_availability_is_not_applicable_without_inventory_targets() -> None:
    with patch("src.services.gam_sync_applicability.ProductRepository") as repo:
        repo.return_value.list_all_with_inventory.return_value = [_product({})]

        assert tenant_has_pricing_availability_targets(SimpleNamespace(), "tenant_1") is False


def test_gam_derived_streams_are_applicable_for_non_gam_adapters() -> None:
    session = SimpleNamespace()

    assert gam_signal_coverage_applicable(session, tenant_id="tenant_1", adapter_type="freewheel") is True
    assert gam_pricing_availability_applicable(session, tenant_id="tenant_1", adapter_type="freewheel") is True


def test_reporting_is_not_applicable_for_gam() -> None:
    assert gam_reporting_applicable(adapter_type="google_ad_manager") is False


def test_reporting_is_applicable_for_non_gam_adapters() -> None:
    assert gam_reporting_applicable(adapter_type="freewheel") is True
    assert gam_reporting_applicable(adapter_type="springserve") is True
