"""Persist product-level GAM pricing and availability guidance.

This sync turns the exploratory GAM placement/country report into cached
product guidance. It intentionally keeps the buyer catalog product-level:
placement/country rows are used as evidence, then folded into one delivery
forecast and pricing-guidance block per product.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from adcp.types import DeliveryForecast

from src.adapters.gam_reporting_service import GAMReportingService
from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption, Product
from src.core.database.repositories.adapter_config import AdapterConfigRepository
from src.core.database.repositories.currency_limit import CurrencyLimitRepository
from src.core.database.repositories.inventory_profile import InventoryProfileRepository
from src.core.database.repositories.product import ProductRepository
from src.core.inventory_profile_projection import (
    inventory_profile_to_product_model,
    is_complete_inventory_profile,
)
from src.services.catalog_sync_helpers import (
    CatalogSyncResult,
    create_running_catalog_sync_job,
    fail_catalog_sync_job,
    finish_catalog_sync_job,
    new_sync_id,
)
from src.services.gam_reporting_sync_helpers import build_gam_reporting_service_for_tenant
from src.services.protocol_change_webhooks import notify_product_catalog_changed

logger = logging.getLogger(__name__)

KIND_PRICING_AVAILABILITY = "pricing_availability"
DEFAULT_DATE_RANGE: Literal["lifetime", "this_month", "today"] = "this_month"
DEFAULT_LINE_ITEM_TYPES = ["PRICE_PRIORITY"]
DEFAULT_MIN_GROUP_IMPRESSIONS = 10_000
DEFAULT_MIN_LINE_ITEM_IMPRESSIONS = 1_000
DEFAULT_BOOKABILITY_SAFETY_FACTOR = 1.0
DEFAULT_MAX_NETWORK_LINE_ITEMS = 600_000
DEFAULT_MONTHLY_LINE_ITEM_SPACE_FRACTION = 0.01
DEFAULT_ESTIMATED_LINE_ITEMS_PER_PACKAGE = 1
GUIDANCE_VALID_FOR = timedelta(hours=6)


@dataclass
class PricingAvailabilitySyncResult(CatalogSyncResult):
    """Summary for one product-level pricing/availability sync."""

    updated_product_ids: list[str] = field(default_factory=list)


def run_gam_pricing_availability_sync(
    *,
    tenant_id: str,
    triggered_by: str = "manual",
    triggered_by_id: str | None = None,
    date_range: Literal["lifetime", "this_month", "today"] = DEFAULT_DATE_RANGE,
    line_item_types: list[str] | None = None,
    min_group_impressions: int = DEFAULT_MIN_GROUP_IMPRESSIONS,
    min_line_item_impressions: int = DEFAULT_MIN_LINE_ITEM_IMPRESSIONS,
    bookability_safety_factor: float = DEFAULT_BOOKABILITY_SAFETY_FACTOR,
    max_network_line_items: int = DEFAULT_MAX_NETWORK_LINE_ITEMS,
    monthly_line_item_space_fraction: float = DEFAULT_MONTHLY_LINE_ITEM_SPACE_FRACTION,
    estimated_line_items_per_package: int = DEFAULT_ESTIMATED_LINE_ITEMS_PER_PACKAGE,
    sync_id: str | None = None,
) -> PricingAvailabilitySyncResult:
    """Run GAM product-level pricing/availability guidance and persist it."""
    sync_id = sync_id or new_sync_id()
    started_at = datetime.now(UTC)
    effective_line_item_types = list(line_item_types or DEFAULT_LINE_ITEM_TYPES)
    create_running_catalog_sync_job(
        tenant_id=tenant_id,
        sync_id=sync_id,
        sync_type=KIND_PRICING_AVAILABILITY,
        triggered_by=triggered_by,
        triggered_by_id=triggered_by_id,
        started_at=started_at,
        date_range=date_range,
        line_item_types=effective_line_item_types,
    )

    try:
        reporting = build_gam_reporting_service_for_tenant(tenant_id)
        updated_product_ids, counts, errors = _sync_product_guidance(
            tenant_id=tenant_id,
            reporting=reporting,
            date_range=date_range,
            line_item_types=effective_line_item_types,
            min_group_impressions=min_group_impressions,
            min_line_item_impressions=min_line_item_impressions,
            bookability_safety_factor=bookability_safety_factor,
            max_network_line_items=max_network_line_items,
            monthly_line_item_space_fraction=monthly_line_item_space_fraction,
            estimated_line_items_per_package=estimated_line_items_per_package,
        )
        succeeded = not errors
        finished_at = datetime.now(UTC)
        progress = {
            "item_count": counts.get("products_updated", 0),
            "counts": counts,
            "errors": errors,
            "updated_product_ids": updated_product_ids,
        }
        summary = {
            "updated_products": len(updated_product_ids),
            "placement_ids_queried": counts.get("placement_ids_queried", 0),
            "ad_unit_ids_queried": counts.get("ad_unit_ids_queried", 0),
        }
        finish_catalog_sync_job(tenant_id, sync_id, succeeded, counts, errors, summary, progress, finished_at)

        for product_id in updated_product_ids:
            notify_product_catalog_changed(
                tenant_id=tenant_id,
                action="updated",
                product_id=product_id,
                data={"changed_fields": ["forecast", "pricing_options.price_guidance"], "sync_id": sync_id},
            )

        return PricingAvailabilitySyncResult(
            sync_id, tenant_id, started_at, finished_at, succeeded, counts, errors, updated_product_ids
        )
    except Exception as exc:
        finished_at, error_message = fail_catalog_sync_job(
            tenant_id=tenant_id,
            sync_id=sync_id,
            exc=exc,
            item_count=0,
        )
        logger.exception("GAM pricing/availability sync failed for tenant=%s", tenant_id)
        return PricingAvailabilitySyncResult(
            sync_id, tenant_id, started_at, finished_at, False, errors={"sync": error_message}
        )


def _sync_product_guidance(
    *,
    tenant_id: str,
    reporting: GAMReportingService,
    date_range: Literal["lifetime", "this_month", "today"],
    line_item_types: list[str],
    min_group_impressions: int,
    min_line_item_impressions: int,
    bookability_safety_factor: float,
    max_network_line_items: int,
    monthly_line_item_space_fraction: float,
    estimated_line_items_per_package: int,
) -> tuple[list[str], dict[str, int], dict[str, str]]:
    with get_db_session() as session:
        session.info["platform_background_worker"] = True
        adapter_config = AdapterConfigRepository(session, tenant_id).get_by_tenant()
        product_repo = ProductRepository(session, tenant_id)
        currency = adapter_config.gam_network_currency or _default_pricing_currency(
            product_repo.get_all_pricing_options()
        )
        currency_limits = {
            limit.currency_code.upper(): limit for limit in CurrencyLimitRepository(session, tenant_id).list_all()
        }
        products = product_repo.list_all_with_inventory()
        existing_product_ids = {product.product_id for product in products}
        inventory_profiles = [
            profile
            for profile in InventoryProfileRepository(session, tenant_id).list_all()
            if profile.profile_id not in existing_product_ids and is_complete_inventory_profile(profile)
        ]
        bundle_products = [
            inventory_profile_to_product_model(profile, default_currency=currency) for profile in inventory_profiles
        ]
        product_specs = [
            *_product_specs(products, source="product"),
            *_product_specs(bundle_products, source="inventory_profile"),
        ]

    placement_ids = sorted({placement_id for spec in product_specs for placement_id in spec["placement_ids"]})
    ad_unit_ids = sorted({ad_unit_id for spec in product_specs for ad_unit_id in spec["ad_unit_ids"]})
    if not placement_ids and not ad_unit_ids:
        return [], _empty_counts(products_seen=len(products) + len(inventory_profiles)), {}
    report_countries = _report_country_filters(product_specs)

    capacity_guidance = reporting.get_line_item_capacity_guidance(
        "this_month",
        max_network_line_items=max_network_line_items,
        monthly_line_item_space_fraction=monthly_line_item_space_fraction,
        estimated_line_items_per_package=estimated_line_items_per_package,
        requested_timezone=reporting.network_timezone,
    )
    report = _get_complete_price_guidance_report(
        reporting,
        date_range=date_range,
        placement_ids=placement_ids,
        ad_unit_ids=ad_unit_ids,
        countries=report_countries,
        line_item_types=line_item_types,
        min_group_impressions=min_group_impressions,
        min_line_item_impressions=min_line_item_impressions,
        bookability_safety_factor=bookability_safety_factor,
        currency=currency,
    )
    line_item_rows = list(report.get("line_item_rows") or [])

    updated_product_ids: list[str] = []
    pricing_options_updated = 0
    products_unbookable = 0
    generated_at = datetime.now(UTC)
    valid_until = generated_at + GUIDANCE_VALID_FOR
    with get_db_session() as session:
        session.info["platform_background_worker"] = True
        repo = ProductRepository(session, tenant_id)
        inventory_repo = InventoryProfileRepository(session, tenant_id)
        for spec in product_specs:
            if spec["source"] == "inventory_profile":
                profile = inventory_repo.get_by_id(spec["product_id"])
                product = (
                    inventory_profile_to_product_model(profile, default_currency=currency)
                    if profile is not None
                    else None
                )
            else:
                product = repo.get_by_id_with_pricing(spec["product_id"])
            if product is None:
                continue
            product_rows = _rows_for_product(line_item_rows, spec)
            guidance = _product_guidance_from_line_items(
                product=product,
                line_item_rows=product_rows,
                currency=currency,
                capacity_guidance=capacity_guidance,
                min_group_impressions=min_group_impressions,
                bookability_safety_factor=bookability_safety_factor,
                generated_at=generated_at,
                valid_until=valid_until,
                report=report,
                currency_limits=currency_limits,
            )
            if spec["source"] == "inventory_profile":
                assert profile is not None
                profile.forecast = guidance["forecast"]
                profile.pricing_availability = _pricing_availability_metadata(guidance)
                updated_for_product = 1 if _has_pricing_guidance(guidance, "cpm") else 0
            else:
                product.forecast = guidance["forecast"]
                _merge_pricing_availability_metadata(product, guidance)
                updated_for_product = _apply_pricing_guidance(product.pricing_options or [], guidance)
            pricing_options_updated += updated_for_product
            if not guidance["bookability"]["bookable"]:
                products_unbookable += 1
            updated_product_ids.append(product.product_id)
        session.commit()

    counts = {
        "products_seen": len(products) + len(inventory_profiles),
        "products_with_placements": sum(1 for spec in product_specs if spec["placement_ids"]),
        "products_with_inventory_targets": len(product_specs),
        "products_updated": len(updated_product_ids),
        "products_unbookable": products_unbookable,
        "pricing_options_updated": pricing_options_updated,
        "placement_ids_queried": len(placement_ids),
        "ad_unit_ids_queried": len(ad_unit_ids),
        "report_rows": int(report.get("raw_rows") or 0),
        "eligible_line_item_rows": int(report.get("eligible_line_item_rows") or 0),
        "report_chunks": int(report.get("chunk_count") or 1),
        "incomplete_report_chunks": int(report.get("incomplete_report_chunks") or 0),
    }
    return updated_product_ids, counts, {}


def _get_complete_price_guidance_report(
    reporting: GAMReportingService,
    *,
    date_range: Literal["lifetime", "this_month", "today"],
    placement_ids: list[str],
    ad_unit_ids: list[str],
    countries: list[str] | None,
    line_item_types: list[str],
    min_group_impressions: int,
    min_line_item_impressions: int,
    bookability_safety_factor: float,
    currency: str,
) -> dict[str, Any]:
    report = _get_price_guidance_report(
        reporting,
        date_range=date_range,
        placement_ids=placement_ids,
        ad_unit_ids=ad_unit_ids,
        countries=countries,
        line_item_types=line_item_types,
        min_group_impressions=min_group_impressions,
        min_line_item_impressions=min_line_item_impressions,
        bookability_safety_factor=bookability_safety_factor,
        currency=currency,
    )
    if not report.get("possibly_truncated"):
        return report
    if _inventory_target_count(placement_ids=placement_ids, ad_unit_ids=ad_unit_ids) <= 1:
        return _mark_single_inventory_report_incomplete(report, placement_ids=placement_ids, ad_unit_ids=ad_unit_ids)

    chunk_reports = _get_complete_price_guidance_report_chunks(
        reporting,
        date_range=date_range,
        placement_ids=placement_ids,
        ad_unit_ids=ad_unit_ids,
        countries=countries,
        line_item_types=line_item_types,
        min_group_impressions=min_group_impressions,
        min_line_item_impressions=min_line_item_impressions,
        bookability_safety_factor=bookability_safety_factor,
        currency=currency,
    )
    logger.info(
        "GAM pricing/availability report was truncated; split tenant report into %s inventory-target chunks",
        len(chunk_reports),
    )
    return _combine_price_guidance_reports(
        report,
        chunk_reports,
        placement_ids=placement_ids,
        ad_unit_ids=ad_unit_ids,
    )


def _get_complete_price_guidance_report_chunks(
    reporting: GAMReportingService,
    *,
    date_range: Literal["lifetime", "this_month", "today"],
    placement_ids: list[str],
    ad_unit_ids: list[str],
    countries: list[str] | None,
    line_item_types: list[str],
    min_group_impressions: int,
    min_line_item_impressions: int,
    bookability_safety_factor: float,
    currency: str,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for chunk_placement_ids, chunk_ad_unit_ids in _split_inventory_targets(
        placement_ids=placement_ids,
        ad_unit_ids=ad_unit_ids,
    ):
        if not chunk_placement_ids and not chunk_ad_unit_ids:
            continue
        report = _get_price_guidance_report(
            reporting,
            date_range=date_range,
            placement_ids=chunk_placement_ids,
            ad_unit_ids=chunk_ad_unit_ids,
            countries=countries,
            line_item_types=line_item_types,
            min_group_impressions=min_group_impressions,
            min_line_item_impressions=min_line_item_impressions,
            bookability_safety_factor=bookability_safety_factor,
            currency=currency,
        )
        if report.get("possibly_truncated"):
            if _inventory_target_count(placement_ids=chunk_placement_ids, ad_unit_ids=chunk_ad_unit_ids) <= 1:
                reports.append(
                    _mark_single_inventory_report_incomplete(
                        report,
                        placement_ids=chunk_placement_ids,
                        ad_unit_ids=chunk_ad_unit_ids,
                    )
                )
                continue
            reports.extend(
                _get_complete_price_guidance_report_chunks(
                    reporting,
                    date_range=date_range,
                    placement_ids=chunk_placement_ids,
                    ad_unit_ids=chunk_ad_unit_ids,
                    countries=countries,
                    line_item_types=line_item_types,
                    min_group_impressions=min_group_impressions,
                    min_line_item_impressions=min_line_item_impressions,
                    bookability_safety_factor=bookability_safety_factor,
                    currency=currency,
                )
            )
        else:
            reports.append(report)
    return reports


def _get_price_guidance_report(
    reporting: GAMReportingService,
    *,
    date_range: Literal["lifetime", "this_month", "today"],
    placement_ids: list[str],
    ad_unit_ids: list[str],
    countries: list[str] | None,
    line_item_types: list[str],
    min_group_impressions: int,
    min_line_item_impressions: int,
    bookability_safety_factor: float,
    currency: str,
) -> dict[str, Any]:
    return reporting.get_placement_country_price_guidance(
        date_range,
        placement_ids=placement_ids,
        ad_unit_ids=ad_unit_ids,
        countries=countries,
        line_item_types=line_item_types,
        min_group_impressions=min_group_impressions,
        min_line_item_impressions=min_line_item_impressions,
        min_package_budget=None,
        bookability_safety_factor=bookability_safety_factor,
        currency=currency,
        requested_timezone=reporting.network_timezone,
        include_eligible_line_items=True,
    )


def _mark_single_inventory_report_incomplete(
    report: dict[str, Any],
    *,
    placement_ids: list[str],
    ad_unit_ids: list[str],
) -> dict[str, Any]:
    placement_id = placement_ids[0] if placement_ids else None
    ad_unit_id = ad_unit_ids[0] if ad_unit_ids else None
    target_kind = "placement_id" if placement_id else "ad_unit_id"
    target_id = placement_id or ad_unit_id or "unknown"
    logger.warning(
        "GAM pricing/availability report for %s=%s was still truncated after splitting; "
        "using capped report rows and marking guidance incomplete",
        target_kind,
        target_id,
    )
    return {
        **report,
        "possibly_truncated": False,
        "incomplete_report": True,
        "incomplete_report_chunks": 1,
        "truncated_single_placement_ids": [placement_id] if placement_id else [],
        "truncated_single_ad_unit_ids": [ad_unit_id] if ad_unit_id else [],
    }


def _combine_price_guidance_reports(
    base_report: dict[str, Any],
    chunk_reports: list[dict[str, Any]],
    *,
    placement_ids: list[str],
    ad_unit_ids: list[str],
) -> dict[str, Any]:
    line_item_rows = [row for report in chunk_reports for row in report.get("line_item_rows") or []]
    filters = dict(base_report.get("filters") or {})
    filters["placement_ids"] = placement_ids
    filters["ad_unit_ids"] = ad_unit_ids
    incomplete_placement_ids = sorted(
        {
            str(placement_id)
            for report in chunk_reports
            for placement_id in report.get("truncated_single_placement_ids") or []
        }
    )
    incomplete_ad_unit_ids = sorted(
        {str(ad_unit_id) for report in chunk_reports for ad_unit_id in report.get("truncated_single_ad_unit_ids") or []}
    )
    return {
        **base_report,
        "filters": filters,
        "possibly_truncated": False,
        "incomplete_report": bool(incomplete_placement_ids or incomplete_ad_unit_ids),
        "incomplete_report_chunks": sum(int(report.get("incomplete_report_chunks") or 0) for report in chunk_reports),
        "truncated_single_placement_ids": incomplete_placement_ids,
        "truncated_single_ad_unit_ids": incomplete_ad_unit_ids,
        "chunked": True,
        "chunk_count": len(chunk_reports),
        "raw_rows": sum(int(report.get("raw_rows") or 0) for report in chunk_reports),
        "eligible_line_item_rows": sum(int(report.get("eligible_line_item_rows") or 0) for report in chunk_reports),
        "line_item_rows": line_item_rows,
        "groups": [],
        "group_count": 0,
        "bookable_group_count": 0,
        "forecast": {},
    }


def _inventory_target_count(*, placement_ids: list[str], ad_unit_ids: list[str]) -> int:
    return len(placement_ids) + len(ad_unit_ids)


def _split_inventory_targets(*, placement_ids: list[str], ad_unit_ids: list[str]) -> list[tuple[list[str], list[str]]]:
    targets = [("placement", value) for value in placement_ids] + [("ad_unit", value) for value in ad_unit_ids]
    midpoint = max(1, len(targets) // 2)
    chunks: list[tuple[list[str], list[str]]] = []
    for chunk_targets in (targets[:midpoint], targets[midpoint:]):
        chunk_placement_ids = [value for kind, value in chunk_targets if kind == "placement"]
        chunk_ad_unit_ids = [value for kind, value in chunk_targets if kind == "ad_unit"]
        chunks.append((chunk_placement_ids, chunk_ad_unit_ids))
    return chunks


def _empty_counts(*, products_seen: int) -> dict[str, int]:
    return {
        "products_seen": products_seen,
        "products_with_placements": 0,
        "products_with_inventory_targets": 0,
        "products_updated": 0,
        "products_unbookable": 0,
        "pricing_options_updated": 0,
        "placement_ids_queried": 0,
        "ad_unit_ids_queried": 0,
        "report_rows": 0,
        "eligible_line_item_rows": 0,
    }


def _default_pricing_currency(pricing_options: list[PricingOption]) -> str:
    for option in pricing_options:
        if option.currency:
            return option.currency.upper()
    return "USD"


def _product_specs(
    products: list[Product],
    *,
    source: Literal["product", "inventory_profile"],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for product in products:
        placement_ids = _product_targeted_placement_ids(product)
        ad_unit_ids = _product_targeted_ad_unit_ids(product)
        if not placement_ids and not ad_unit_ids:
            continue
        country_values = [str(country).strip() for country in product.countries or [] if str(country).strip()]
        specs.append(
            {
                "product_id": product.product_id,
                "source": source,
                "placement_ids": placement_ids,
                "ad_unit_ids": ad_unit_ids,
                "countries": _normalized_country_filters(country_values),
                "report_countries": country_values,
            }
        )
    return specs


def _report_country_filters(product_specs: list[dict[str, Any]]) -> list[str] | None:
    countries: set[str] = set()
    for spec in product_specs:
        spec_countries = {str(country).strip() for country in spec.get("report_countries", []) if str(country).strip()}
        if not spec_countries:
            return None
        countries.update(spec_countries)
    return sorted(countries) if countries else None


def _product_targeted_placement_ids(product: Product) -> list[str]:
    return _product_targeted_ids(product, "targeted_placement_ids")


def _product_targeted_ad_unit_ids(product: Product) -> list[str]:
    return _product_targeted_ids(product, "targeted_ad_unit_ids")


def _product_targeted_ids(product: Product, config_key: str) -> list[str]:
    config = product.effective_implementation_config
    values = config.get(config_key) or []
    return sorted({str(value) for value in values if str(value).strip()})


def _normalized_country_filters(countries: list[str]) -> set[str]:
    return {str(country).strip().lower() for country in countries if str(country).strip()}


def _rows_for_product(line_item_rows: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    placement_ids = set(spec["placement_ids"])
    ad_unit_ids = set(spec["ad_unit_ids"])
    countries = set(spec["countries"])
    rows = []
    for row in line_item_rows:
        matches_placement = str(row.get("placement_id")) in placement_ids
        matches_ad_unit = str(row.get("ad_unit_id")) in ad_unit_ids
        if not (matches_placement or matches_ad_unit):
            continue
        if countries and not _row_matches_country(row, countries):
            continue
        rows.append(row)
    return rows


def _row_matches_country(row: dict[str, Any], countries: set[str]) -> bool:
    return str(row.get("country_code") or "").lower() in countries or str(row.get("country") or "").lower() in countries


def _product_guidance_from_line_items(
    *,
    product: Product,
    line_item_rows: list[dict[str, Any]],
    currency: str,
    capacity_guidance: dict[str, Any],
    min_group_impressions: int,
    bookability_safety_factor: float,
    generated_at: datetime,
    valid_until: datetime,
    report: dict[str, Any],
    currency_limits: dict[str, Any],
) -> dict[str, Any]:
    totals = _delivery_totals(line_item_rows)
    pricing_guidance_by_model = {
        "cpm": GAMReportingService._billable_metric_guidance(
            line_item_rows,
            price_key="cpm",
            weight_key="impressions",
            min_billable_units=min_group_impressions,
        ),
        "vcpm": GAMReportingService._billable_metric_guidance(
            line_item_rows,
            price_key="vcpm",
            weight_key="viewable_impressions",
            min_billable_units=min_group_impressions,
        ),
        "cpc": GAMReportingService._billable_metric_guidance(
            line_item_rows,
            price_key="cpc",
            weight_key="clicks",
            min_billable_units=100,
        ),
        "cpcv": GAMReportingService._billable_metric_guidance(
            line_item_rows,
            price_key="cpcv",
            weight_key="completed_views",
            min_billable_units=100,
        ),
    }
    option_bookability = [
        _bookability_for_pricing_option(
            option=option,
            totals=totals,
            price_guidance=pricing_guidance_by_model.get(option.pricing_model.lower()) or {},
            guidance_currency=currency,
            capacity_guidance=capacity_guidance,
            currency_limits=currency_limits,
            safety_factor=bookability_safety_factor,
        )
        for option in product.pricing_options or []
    ]
    product_bookable = any(row["bookable"] for row in option_bookability) if option_bookability else False
    forecast = _delivery_forecast(
        product_id=product.product_id,
        product_name=product.name,
        currency=currency,
        totals=totals,
        generated_at=generated_at,
        valid_until=valid_until,
        report=report,
        capacity_guidance=capacity_guidance,
        product_bookable=product_bookable,
    )
    return {
        "currency": currency.upper(),
        "forecast": forecast,
        "pricing_guidance_by_model": pricing_guidance_by_model,
        "bookability": {
            "bookable": product_bookable,
            "basis": "product_level_capacity",
            "safety_factor": bookability_safety_factor,
            "options": option_bookability,
        },
        "totals": totals,
    }


def _delivery_totals(line_item_rows: list[dict[str, Any]]) -> dict[str, int | float]:
    totals = {
        "impressions": 0,
        "viewable_impressions": 0,
        "measurable_impressions": 0,
        "clicks": 0,
        "completed_views": 0,
        "spend": 0.0,
    }
    for row in line_item_rows:
        totals["impressions"] += int(row.get("impressions") or 0)
        totals["viewable_impressions"] += int(row.get("viewable_impressions") or 0)
        totals["measurable_impressions"] += int(row.get("measurable_impressions") or 0)
        totals["clicks"] += int(row.get("clicks") or 0)
        totals["completed_views"] += int(row.get("completed_views") or 0)
        totals["spend"] += float(row.get("revenue") or 0.0)
    totals["spend"] = round(float(totals["spend"]), 2)
    return totals


def _bookability_for_pricing_option(
    *,
    option: PricingOption,
    totals: dict[str, int | float],
    price_guidance: dict[str, float | None],
    guidance_currency: str,
    capacity_guidance: dict[str, Any],
    currency_limits: dict[str, Any],
    safety_factor: float,
) -> dict[str, Any]:
    pricing_model = option.pricing_model.lower()
    policy_min_budget = _min_package_budget(option, currency_limits)
    capacity_min_budget = _capacity_min_package_budget(capacity_guidance)
    min_budget = _effective_min_package_budget(policy_min_budget, capacity_min_budget)
    available_units = _available_units_for_model(pricing_model, totals)
    p25 = price_guidance.get("p25")
    base = 1000 if pricing_model in {"cpm", "vcpm"} else 1
    result = {
        "pricing_model": pricing_model,
        "currency": option.currency,
        "minimum_package_budget": min_budget,
        "policy_minimum_package_budget": policy_min_budget,
        "capacity_minimum_package_budget": capacity_min_budget,
        "price_basis": f"{pricing_model}_p25",
        "price": p25,
        "available_units": available_units,
        "safety_factor": safety_factor,
    }
    if option.currency.upper() != guidance_currency.upper():
        return {**result, "bookable": False, "reason": "currency_conversion_unavailable", "required_units": None}
    if min_budget is None:
        return {**result, "bookable": available_units > 0, "reason": "no_min_package_budget"}
    if p25 is None or p25 <= 0:
        return {**result, "bookable": False, "reason": "missing_conservative_price_guidance", "required_units": None}
    required_units = math.ceil((min_budget / p25) * base * safety_factor)
    return {
        **result,
        "bookable": available_units >= required_units,
        "reason": "capacity_meets_minimum_budget" if available_units >= required_units else "insufficient_capacity",
        "required_units": required_units,
    }


def _min_package_budget(option: PricingOption, currency_limits: dict[str, Any]) -> float | None:
    if option.min_spend_per_package is not None:
        return float(option.min_spend_per_package)
    limit = currency_limits.get(option.currency.upper())
    value: Decimal | None = getattr(limit, "min_package_budget", None) if limit is not None else None
    return float(value) if value is not None else None


def _capacity_min_package_budget(capacity_guidance: dict[str, Any]) -> float | None:
    value = capacity_guidance.get("minimum_package_budget")
    return float(value) if value is not None else None


def _effective_min_package_budget(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _available_units_for_model(pricing_model: str, totals: dict[str, int | float]) -> int:
    key = {
        "cpm": "impressions",
        "vcpm": "viewable_impressions",
        "cpc": "clicks",
        "cpcv": "completed_views",
    }.get(pricing_model, "impressions")
    return int(totals.get(key) or 0)


def _delivery_forecast(
    *,
    product_id: str,
    product_name: str,
    currency: str,
    totals: dict[str, int | float],
    generated_at: datetime,
    valid_until: datetime,
    report: dict[str, Any],
    capacity_guidance: dict[str, Any],
    product_bookable: bool,
) -> dict[str, Any]:
    metrics: dict[str, dict[str, float]] = {
        "impressions": {"mid": float(totals["impressions"])},
        "spend": {"mid": float(totals["spend"])},
    }
    if int(totals["clicks"]) > 0:
        metrics["clicks"] = {"mid": float(totals["clicks"])}
    if int(totals["completed_views"]) > 0:
        metrics["completed_views"] = {"mid": float(totals["completed_views"])}

    point: dict[str, Any] = {
        "label": product_name,
        "product_id": product_id,
        "metrics": metrics,
    }
    if int(totals["measurable_impressions"]) > 0 or int(totals["viewable_impressions"]) > 0:
        viewability: dict[str, Any] = {
            "vendor": {"domain": "googleadmanager.com"},
            "standard": "mrc",
        }
        if int(totals["measurable_impressions"]) > 0:
            viewability["measurable_impressions"] = {"mid": float(totals["measurable_impressions"])}
        if int(totals["viewable_impressions"]) > 0:
            viewability["viewable_impressions"] = {"mid": float(totals["viewable_impressions"])}
        if int(totals["measurable_impressions"]) > 0 and int(totals["viewable_impressions"]) > 0:
            viewability["viewable_rate"] = {
                "mid": round(int(totals["viewable_impressions"]) / int(totals["measurable_impressions"]), 6)
            }
        point["viewability"] = viewability

    forecast = {
        "method": "estimate",
        "currency": currency,
        "forecast_range_unit": "availability",
        "generated_at": generated_at,
        "valid_until": valid_until,
        "points": [point],
        "ext": {
            "source": {
                "adapter": "google_ad_manager",
                "sync_kind": KIND_PRICING_AVAILABILITY,
                "method": "historical_reporting",
                "date_range": report.get("date_range"),
                "window_start": report.get("window_start"),
                "window_end": report.get("window_end"),
                "line_item_types": (report.get("filters") or {}).get("line_item_types"),
                "chunked": bool(report.get("chunked")),
                "chunk_count": report.get("chunk_count"),
                "incomplete_report": bool(report.get("incomplete_report")),
                "incomplete_report_chunks": report.get("incomplete_report_chunks"),
                "truncated_single_placement_ids": report.get("truncated_single_placement_ids"),
                "truncated_single_ad_unit_ids": report.get("truncated_single_ad_unit_ids"),
            },
            "bookable": product_bookable,
            "line_item_capacity_guidance": capacity_guidance,
        },
    }
    return DeliveryForecast.model_validate(forecast).model_dump(mode="json", exclude_none=True)


def _merge_pricing_availability_metadata(product: Product, guidance: dict[str, Any]) -> None:
    config = dict(product.implementation_config or {})
    config["pricing_availability"] = _pricing_availability_metadata(guidance)
    product.implementation_config = config


def _pricing_availability_metadata(guidance: dict[str, Any]) -> dict[str, Any]:
    return {
        "bookability": guidance["bookability"],
        "totals": guidance["totals"],
        "pricing_guidance_by_model": guidance["pricing_guidance_by_model"],
    }


def _has_pricing_guidance(guidance: dict[str, Any], pricing_model: str) -> bool:
    by_model = guidance["pricing_guidance_by_model"]
    model_guidance = by_model.get(pricing_model)
    return bool(model_guidance and any(value is not None for value in model_guidance.values()))


def _apply_pricing_guidance(pricing_options: list[PricingOption], guidance: dict[str, Any]) -> int:
    updated = 0
    by_model = guidance["pricing_guidance_by_model"]
    guidance_currency = str(guidance.get("currency") or "").upper()
    for option in pricing_options:
        if guidance_currency and option.currency.upper() != guidance_currency:
            continue
        model = option.pricing_model.lower()
        model_guidance = by_model.get(model)
        if not model_guidance or not any(value is not None for value in model_guidance.values()):
            continue
        current_guidance = option.price_guidance if isinstance(option.price_guidance, dict) else {}
        next_guidance = {**current_guidance, **model_guidance}
        if "floor" not in next_guidance and model_guidance.get("p25") is not None:
            # The persisted auction-pricing constraint still requires a floor.
            # Historical guidance is percentile-based, so preserve any authored
            # floor and only fall back to p25 when legacy data lacks one.
            next_guidance["floor"] = model_guidance["p25"]
        option.price_guidance = next_guidance
        updated += 1
    return updated
