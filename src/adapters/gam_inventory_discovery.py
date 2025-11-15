"""
Google Ad Manager Inventory Discovery and Synchronization.

This module provides:
- Ad unit tree discovery
- Placement discovery
- Creative template discovery
- Label discovery for targeting
- Custom targeting key/value discovery
- Audience segment discovery
- First-party data integration discovery
- Automated sync with local cache
"""

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import pytz
from googleads import ad_manager
from zeep.helpers import serialize_object

from src.adapters.gam.utils.error_handler import with_retry
from src.adapters.gam.utils.logging import logger
from src.adapters.gam.utils.timeout_handler import timeout


class AdUnitStatus(Enum):
    """Ad unit status in GAM."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


@dataclass
class AdUnit:
    """Represents a GAM ad unit."""

    id: str
    name: str
    ad_unit_code: str
    parent_id: str | None
    status: AdUnitStatus
    description: str | None
    target_window: str | None
    effective_applied_labels: list[str]
    explicitly_targeted: bool
    has_children: bool
    path: list[str]  # Full path from root
    sizes: list[dict[str, int]]  # List of {width, height}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_gam_object(cls, gam_ad_unit: dict[str, Any]) -> "AdUnit":
        """Create from GAM API response."""
        # Extract sizes from ad unit sizes
        sizes = []
        if "adUnitSizes" in gam_ad_unit:
            for size_obj in gam_ad_unit["adUnitSizes"]:
                if "size" in size_obj:
                    sizes.append({"width": size_obj["size"]["width"], "height": size_obj["size"]["height"]})

        # Build path from parent path + current name
        path = []
        if "parentPath" in gam_ad_unit:
            path = [node["name"] for node in gam_ad_unit["parentPath"]]
        path.append(gam_ad_unit["name"])

        return cls(
            id=str(gam_ad_unit["id"]),
            name=gam_ad_unit["name"],
            ad_unit_code=gam_ad_unit["adUnitCode"],
            parent_id=str(gam_ad_unit.get("parentId")) if gam_ad_unit.get("parentId") else None,
            status=AdUnitStatus.ACTIVE,  # Default all ad units to active
            description=gam_ad_unit.get("description"),
            target_window=gam_ad_unit.get("targetWindow"),
            effective_applied_labels=[str(label["labelId"]) for label in gam_ad_unit.get("appliedLabels", [])],
            explicitly_targeted=gam_ad_unit.get("explicitlyTargeted", False),
            has_children=gam_ad_unit.get("hasChildren", False),
            path=path,
            sizes=sizes,
        )


@dataclass
class Placement:
    """Represents a GAM placement."""

    id: str
    name: str
    description: str | None
    placement_code: str
    status: str
    is_ad_sense_targeting_enabled: bool
    ad_unit_ids: list[str]
    targeting_description: str | None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)

    @classmethod
    def from_gam_object(cls, gam_placement: dict[str, Any]) -> "Placement":
        """Create from GAM API response."""
        return cls(
            id=str(gam_placement["id"]),
            name=gam_placement["name"],
            description=gam_placement.get("description"),
            placement_code=gam_placement["placementCode"],
            status=gam_placement["status"],
            is_ad_sense_targeting_enabled=gam_placement.get("isAdSenseTargetingEnabled", False),
            ad_unit_ids=[str(id) for id in gam_placement.get("targetedAdUnitIds", [])],
            targeting_description=gam_placement.get("targetingDescription"),
        )


@dataclass
class Label:
    """Represents a GAM label for targeting."""

    id: str
    name: str
    description: str | None
    is_active: bool
    ad_category: str | None
    label_type: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)

    @classmethod
    def from_gam_object(cls, gam_label: dict[str, Any]) -> "Label":
        """Create from GAM API response."""
        return cls(
            id=str(gam_label["id"]),
            name=gam_label["name"],
            description=gam_label.get("description"),
            is_active=gam_label["isActive"],
            ad_category=gam_label.get("adCategory"),
            label_type=gam_label["types"][0] if gam_label.get("types") else "UNKNOWN",
        )


@dataclass
class CustomTargetingKey:
    """Represents a GAM custom targeting key."""

    id: str
    name: str
    display_name: str
    type: str  # PREDEFINED or FREEFORM
    status: str
    reportable_type: str | None  # ON, OFF, or CUSTOM_DIMENSION

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)

    @classmethod
    def from_gam_object(cls, gam_key: dict[str, Any]) -> "CustomTargetingKey":
        """Create from GAM API response."""
        return cls(
            id=str(gam_key["id"]),
            name=gam_key["name"],
            display_name=gam_key.get("displayName", gam_key["name"]),
            type=gam_key["type"],
            status=gam_key.get("status", "ACTIVE"),
            reportable_type=gam_key.get("reportableType"),
        )


@dataclass
class CustomTargetingValue:
    """Represents a GAM custom targeting value."""

    id: str
    custom_targeting_key_id: str
    name: str
    display_name: str
    match_type: str  # EXACT, BROAD, PREFIX, etc.
    status: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)

    @classmethod
    def from_gam_object(cls, gam_value: dict[str, Any]) -> "CustomTargetingValue":
        """Create from GAM API response."""
        return cls(
            id=str(gam_value["id"]),
            custom_targeting_key_id=str(gam_value["customTargetingKeyId"]),
            name=gam_value["name"],
            display_name=gam_value.get("displayName", gam_value["name"]),
            match_type=gam_value.get("matchType", "EXACT"),
            status=gam_value.get("status", "ACTIVE"),
        )


@dataclass
class AudienceSegment:
    """Represents a GAM audience segment (first-party or third-party)."""

    id: str
    name: str
    description: str | None
    category_ids: list[str]
    type: str  # FIRST_PARTY, THIRD_PARTY
    size: int | None
    data_provider_name: str | None
    status: str
    segment_type: str  # RULE_BASED, SHARED, etc.

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)

    @classmethod
    def from_gam_object(cls, gam_segment: dict[str, Any]) -> "AudienceSegment":
        """Create from GAM API response."""
        return cls(
            id=str(gam_segment["id"]),
            name=gam_segment["name"],
            description=gam_segment.get("description"),
            category_ids=[str(cat_id) for cat_id in gam_segment.get("categoryIds", [])],
            type=gam_segment.get("type", "UNKNOWN"),
            size=gam_segment.get("size"),
            data_provider_name=gam_segment.get("dataProviderName"),
            status=gam_segment.get("status", "ACTIVE"),
            segment_type=gam_segment.get("segmentType", "UNKNOWN"),
        )


class GAMInventoryDiscovery:
    """Discovers and syncs GAM inventory configuration."""

    def __init__(self, client: ad_manager.AdManagerClient, tenant_id: str):
        self.client = client
        self.tenant_id = tenant_id
        self.ad_units: dict[str, AdUnit] = {}
        self.placements: dict[str, Placement] = {}
        self.labels: dict[str, Label] = {}
        self.custom_targeting_keys: dict[str, CustomTargetingKey] = {}
        self.custom_targeting_values: dict[str, list[CustomTargetingValue]] = {}
        self.audience_segments: dict[str, AudienceSegment] = {}
        self.last_sync: datetime | None = None

    @timeout(seconds=600)  # 10 minute timeout for ad units (same as placements)
    @with_retry(operation_name="discover_ad_units")
    def discover_ad_units(self, since: datetime | None = None) -> list[AdUnit]:
        """
        Discover ad units in the GAM network using flat pagination (no recursion).

        Args:
            since: Optional datetime to fetch only items modified since this time (incremental sync)

        Returns:
            List of discovered ad units (active only - excludes ARCHIVED)
        """
        logger.info(f"Discovering ad units (incremental={since is not None})")

        inventory_service = self.client.GetService("InventoryService")
        discovered_units = []

        # Build statement to query ACTIVE ad units only (excludes ARCHIVED to reduce sync time)
        statement_builder = ad_manager.StatementBuilder(version="v202505")
        statement_builder = statement_builder.Where("status != :archived").WithBindVariable("archived", "ARCHIVED")

        # Add incremental sync filter if requested
        if since:
            # Ensure timezone-aware datetime for GAM API (use pytz for GAM compatibility)
            if since.tzinfo is None:
                since = pytz.utc.localize(since)
            else:
                # Convert to pytz timezone if using datetime.timezone.UTC
                since = since.astimezone(pytz.utc)

            statement_builder = (
                statement_builder.Where("lastModifiedDateTime > :since AND status != :archived")
                .WithBindVariable("since", since)
                .WithBindVariable("archived", "ARCHIVED")
            )
            logger.info(f"Incremental sync: fetching ad units modified since {since} (active only)")

        # Page through results
        while True:
            response = inventory_service.getAdUnitsByStatement(statement_builder.ToStatement())

            if "results" in response and response["results"]:
                for gam_ad_unit in response["results"]:
                    # Convert SUDS object to dictionary
                    gam_ad_unit_dict = serialize_object(gam_ad_unit)
                    ad_unit = AdUnit.from_gam_object(gam_ad_unit_dict)
                    discovered_units.append(ad_unit)
                    self.ad_units[ad_unit.id] = ad_unit

                statement_builder.offset += len(response["results"])
            else:
                break

        logger.info(f"Discovered {len(discovered_units)} ad units")
        return discovered_units

    @timeout(seconds=600)  # 10 minute timeout for placements (AccuWeather has large dataset)
    @with_retry(operation_name="discover_placements")
    def discover_placements(self, since: datetime | None = None) -> list[Placement]:
        """Discover placements in the GAM network.

        Args:
            since: Optional datetime to fetch only items modified since this time (incremental sync)

        Returns:
            List of placements (active only - excludes ARCHIVED)
        """
        logger.info(f"Discovering placements (incremental={since is not None})")

        placement_service = self.client.GetService("PlacementService")
        discovered_placements = []

        # Filter out ARCHIVED placements
        statement_builder = ad_manager.StatementBuilder(version="v202505")
        statement_builder = statement_builder.Where("status != :archived").WithBindVariable("archived", "ARCHIVED")

        if since:
            # Ensure timezone-aware datetime for GAM API (use pytz for GAM compatibility)
            if since.tzinfo is None:
                since = pytz.utc.localize(since)
            else:
                # Convert to pytz timezone if using datetime.timezone.UTC
                since = since.astimezone(pytz.utc)

            statement_builder = (
                statement_builder.Where("lastModifiedDateTime > :since AND status != :archived")
                .WithBindVariable("since", since)
                .WithBindVariable("archived", "ARCHIVED")
            )

        while True:
            response = placement_service.getPlacementsByStatement(statement_builder.ToStatement())

            if "results" in response and response["results"]:
                for gam_placement in response["results"]:
                    # Convert SUDS object to dictionary
                    gam_placement_dict = serialize_object(gam_placement)
                    placement = Placement.from_gam_object(gam_placement_dict)
                    discovered_placements.append(placement)
                    self.placements[placement.id] = placement

                statement_builder.offset += len(response["results"])
            else:
                break

        logger.info(f"Discovered {len(discovered_placements)} placements")
        return discovered_placements

    @timeout(seconds=300)  # 5 minute timeout for labels
    @with_retry(operation_name="discover_labels")
    def discover_labels(self, since: datetime | None = None) -> list[Label]:
        """Discover labels (for competitive exclusion, etc.).

        Args:
            since: Optional datetime to fetch only items modified since this time (incremental sync)
                   Note: GAM LabelService doesn't support lastModifiedDateTime filtering,
                   so we always fetch all labels (they're usually few and rarely change)
        """
        logger.info(f"Discovering labels (incremental={since is not None}, note: always fetches all labels)")

        label_service = self.client.GetService("LabelService")
        discovered_labels = []

        statement_builder = ad_manager.StatementBuilder(version="v202505")

        # Note: LabelService doesn't support lastModifiedDateTime filtering in GAM API
        # So we always fetch all labels, even during incremental sync
        # This is acceptable because:
        # 1. Labels don't change frequently
        # 2. There are usually few labels per network
        # 3. The fetch is fast

        try:
            while True:
                response = label_service.getLabelsByStatement(statement_builder.ToStatement())

                if "results" in response and response["results"]:
                    for gam_label in response["results"]:
                        # Convert SUDS object to dictionary
                        gam_label_dict = serialize_object(gam_label)
                        label = Label.from_gam_object(gam_label_dict)
                        discovered_labels.append(label)
                        self.labels[label.id] = label

                    statement_builder.offset += len(response["results"])
                else:
                    break
        except TypeError as e:
            # Handle googleads library bug with error parsing when no labels exist
            # Error: "argument should be integer or bytes-like object, not 'str'"
            # This happens when GAM returns a SOAP fault and googleads library fails to parse it
            if "argument should be integer or bytes-like object" in str(e):
                logger.info("No labels found in GAM account (or empty result set)")
                return []
            else:
                raise

        logger.info(f"Discovered {len(discovered_labels)} labels")
        return discovered_labels

    @timeout(seconds=600)  # 10 minute timeout for custom targeting (can have 10k+ values per key)
    @with_retry(operation_name="discover_custom_targeting")
    def discover_custom_targeting(
        self, max_values_per_key: int | None = None, fetch_values: bool = True, since: datetime | None = None
    ) -> dict[str, Any]:
        """Discover custom targeting keys and optionally their values.

        Args:
            max_values_per_key: Optional limit on number of values to fetch per key (only if fetch_values=True)
            fetch_values: Whether to fetch values for each key. Set to False for faster sync (lazy load values later)
            since: Optional datetime to fetch only items modified since this time (incremental sync)
                   Note: GAM CustomTargetingService doesn't support lastModifiedDateTime filtering,
                   so we always fetch all keys (acceptable as keys are few and small)
        """
        logger.info(
            "Discovering custom targeting keys"
            + (" and values" if fetch_values else " (values will be lazy loaded)")
            + (f" (max {max_values_per_key} values per key)" if fetch_values and max_values_per_key else "")
            + (" (note: always fetches all keys)" if since else "")
        )

        custom_targeting_service = self.client.GetService("CustomTargetingService")
        discovered_keys = []

        # Discover keys first
        statement_builder = ad_manager.StatementBuilder(version="v202505")

        # Note: CustomTargetingService doesn't support lastModifiedDateTime filtering in GAM API
        # So we always fetch all keys, even during incremental sync
        # This is acceptable because:
        # 1. Custom targeting keys don't change frequently
        # 2. There are usually a modest number of keys per network (<1000)
        # 3. The fetch is reasonably fast
        # 4. Keys are small objects

        while True:
            response = custom_targeting_service.getCustomTargetingKeysByStatement(statement_builder.ToStatement())

            if "results" in response and response["results"]:
                for gam_key in response["results"]:
                    # Convert SUDS object to dictionary
                    gam_key_dict = serialize_object(gam_key)
                    key = CustomTargetingKey.from_gam_object(gam_key_dict)
                    discovered_keys.append(key)
                    self.custom_targeting_keys[key.id] = key
                    self.custom_targeting_values[key.id] = []

                statement_builder.offset += len(response["results"])
            else:
                break

        logger.info(f"Discovered {len(discovered_keys)} custom targeting keys")

        # Optionally discover values for each key
        total_values = 0
        if fetch_values:
            for key in discovered_keys:
                values = self._discover_custom_targeting_values(key.id, max_values=max_values_per_key)
                self.custom_targeting_values[key.id] = values
                total_values += len(values)

            logger.info(f"Discovered {total_values} total custom targeting values")
        else:
            logger.info("Skipping value discovery - values will be lazy loaded on demand")

        return {"keys": discovered_keys, "total_values": total_values, "values_fetched": fetch_values}

    def discover_custom_targeting_values_for_key(
        self, key_id: str, max_values: int | None = None
    ) -> list[CustomTargetingValue]:
        """Discover values for a specific custom targeting key (public method for lazy loading).

        Args:
            key_id: The custom targeting key ID
            max_values: Optional maximum number of values to fetch

        Returns:
            List of discovered custom targeting values
        """
        logger.info(
            f"Lazy loading custom targeting values for key {key_id}" + (f" (max {max_values})" if max_values else "")
        )

        values = self._discover_custom_targeting_values(key_id, max_values)

        # Update the internal cache
        self.custom_targeting_values[key_id] = values

        logger.info(f"Loaded {len(values)} values for key {key_id}")
        return values

    def _discover_custom_targeting_values(
        self, key_id: str, max_values: int | None = None
    ) -> list[CustomTargetingValue]:
        """Discover values for a specific custom targeting key (internal method).

        Args:
            key_id: The custom targeting key ID
            max_values: Optional maximum number of values to fetch
        """
        custom_targeting_service = self.client.GetService("CustomTargetingService")
        discovered_values = []

        statement_builder = (
            ad_manager.StatementBuilder(version="v202505")
            .Where("customTargetingKeyId = :keyId")
            .WithBindVariable("keyId", int(key_id))
        )

        # Apply limit if specified
        if max_values:
            statement_builder.limit = max_values

        while True:
            response = custom_targeting_service.getCustomTargetingValuesByStatement(statement_builder.ToStatement())

            if "results" in response and response["results"]:
                for gam_value in response["results"]:
                    # Convert SUDS object to dictionary
                    gam_value_dict = serialize_object(gam_value)
                    value = CustomTargetingValue.from_gam_object(gam_value_dict)
                    discovered_values.append(value)

                    # Check if we've hit the limit
                    if max_values and len(discovered_values) >= max_values:
                        logger.info(f"Reached limit of {max_values} values for key {key_id}")
                        return discovered_values

                statement_builder.offset += len(response["results"])
            else:
                break

        return discovered_values

    @with_retry(operation_name="discover_audience_segments")
    def discover_audience_segments(
        self, max_segments: int | None = None, since: datetime | None = None
    ) -> list[AudienceSegment]:
        """Discover audience segments (first-party only - skips 3rd party to reduce sync time).

        Args:
            max_segments: Optional maximum number of segments to fetch
            since: Optional datetime to fetch only items modified since this time (incremental sync)

        Returns:
            List of first-party audience segments only (3rd party segments excluded)
        """
        logger.info(
            f"Discovering audience segments (first-party only, incremental={since is not None})"
            + (f" (max {max_segments})" if max_segments else "")
        )

        # Note: The exact service and method names may vary based on GAM API version
        # This is a representative implementation
        audience_segment_service = self.client.GetService("AudienceSegmentService")
        discovered_segments = []

        # Only fetch FIRST_PARTY segments (skip THIRD_PARTY to massively reduce sync time)
        # Google makes all 3rd party segments available to everyone, so they're huge and not tenant-specific
        statement_builder = ad_manager.StatementBuilder(version="v202505")
        statement_builder = statement_builder.Where("type = :type").WithBindVariable("type", "FIRST_PARTY")

        if since:
            # Ensure timezone-aware datetime for GAM API (use pytz for GAM compatibility)
            if since.tzinfo is None:
                since = pytz.utc.localize(since)
            else:
                # Convert to pytz timezone if using datetime.timezone.UTC
                since = since.astimezone(pytz.utc)

            statement_builder = (
                statement_builder.Where("lastModifiedDateTime > :since AND type = :type")
                .WithBindVariable("since", since)
                .WithBindVariable("type", "FIRST_PARTY")
            )

        # Apply limit if specified
        if max_segments:
            statement_builder.limit = max_segments

        while True:
            try:
                response = audience_segment_service.getAudienceSegmentsByStatement(statement_builder.ToStatement())

                if "results" in response and response["results"]:
                    for gam_segment in response["results"]:
                        # Convert SUDS object to dictionary
                        gam_segment_dict = serialize_object(gam_segment)
                        segment = AudienceSegment.from_gam_object(gam_segment_dict)
                        discovered_segments.append(segment)
                        self.audience_segments[segment.id] = segment

                        # Check if we've hit the limit
                        if max_segments and len(discovered_segments) >= max_segments:
                            logger.info(f"Reached limit of {max_segments} audience segments")
                            return discovered_segments

                    statement_builder.offset += len(response["results"])
                else:
                    break
            except Exception as e:
                logger.warning(f"Could not discover audience segments: {e}")
                # Some GAM networks may not have audience segments enabled
                break

        logger.info(f"Discovered {len(discovered_segments)} audience segments")
        return discovered_segments

    def build_ad_unit_tree(self) -> dict[str, Any]:
        """Build hierarchical tree structure of ad units."""
        # Find root units (no parent)
        root_units = [unit for unit in self.ad_units.values() if unit.parent_id is None]

        def build_node(unit: AdUnit) -> dict[str, Any]:
            children: list[dict[str, Any]] = []
            node: dict[str, Any] = {
                "id": unit.id,
                "name": unit.name,
                "code": unit.ad_unit_code,
                "status": unit.status.value,
                "sizes": unit.sizes,
                "explicitly_targeted": unit.explicitly_targeted,
                "children": children,
            }

            # Find children
            for child_unit in self.ad_units.values():
                if child_unit.parent_id == unit.id:
                    children.append(build_node(child_unit))

            return node

        tree = {
            "root_units": [build_node(unit) for unit in root_units],
            "total_units": len(self.ad_units),
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
        }

        return tree

    def get_targetable_ad_units(
        self, include_inactive: bool = False, min_sizes: list[dict[str, int]] | None = None
    ) -> list[AdUnit]:
        """
        Get ad units suitable for targeting.

        Args:
            include_inactive: Include inactive units
            min_sizes: Minimum sizes required (e.g., [{'width': 300, 'height': 250}])

        Returns:
            List of targetable ad units
        """
        targetable = []

        for unit in self.ad_units.values():
            # Skip archived units
            if unit.status == AdUnitStatus.ARCHIVED:
                continue

            # Skip inactive if not requested
            if not include_inactive and unit.status != AdUnitStatus.ACTIVE:
                continue

            # Check size requirements
            if min_sizes:
                has_required_size = False
                for required_size in min_sizes:
                    for unit_size in unit.sizes:
                        if (
                            unit_size["width"] >= required_size["width"]
                            and unit_size["height"] >= required_size["height"]
                        ):
                            has_required_size = True
                            break
                    if has_required_size:
                        break

                if not has_required_size:
                    continue

            targetable.append(unit)

        return targetable

    def get_placements_for_ad_units(self, ad_unit_ids: list[str]) -> list[Placement]:
        """Get placements that target specific ad units."""
        matching_placements = []

        ad_unit_set = set(ad_unit_ids)

        for placement in self.placements.values():
            # Check if placement targets any of the specified ad units
            if any(unit_id in ad_unit_set for unit_id in placement.ad_unit_ids):
                matching_placements.append(placement)

        return matching_placements

    def suggest_ad_units_for_product(
        self, creative_sizes: list[dict[str, int]], keywords: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """
        Suggest ad units based on product requirements.

        Args:
            creative_sizes: List of creative sizes the product supports
            keywords: Optional keywords to match in ad unit names/paths

        Returns:
            List of suggested ad units with relevance scores
        """
        suggestions: list[dict[str, Any]] = []

        targetable_units = self.get_targetable_ad_units(min_sizes=creative_sizes)

        for unit in targetable_units:
            score: int = 0
            reasons: list[str] = []

            # Score based on explicit targeting
            if unit.explicitly_targeted:
                score += 10
                reasons.append("Explicitly targeted unit")

            # Score based on size match
            for creative_size in creative_sizes:
                for unit_size in unit.sizes:
                    if unit_size["width"] == creative_size["width"] and unit_size["height"] == creative_size["height"]:
                        score += 5
                        reasons.append(f"Exact size match: {unit_size['width']}x{unit_size['height']}")

            # Score based on keyword match
            if keywords:
                unit_text = " ".join([unit.name.lower()] + [p.lower() for p in unit.path])
                for keyword in keywords:
                    if keyword.lower() in unit_text:
                        score += 3
                        reasons.append(f"Keyword match: {keyword}")

            # Score based on path depth (prefer specific over generic)
            if len(unit.path) > 2:
                score += 2
                reasons.append("Specific placement (deep in hierarchy)")

            if score > 0:
                suggestions.append(
                    {"ad_unit": unit.to_dict(), "score": score, "reasons": reasons, "path": " > ".join(unit.path)}
                )

        # Sort by score descending (score is guaranteed to be int)
        suggestions.sort(key=lambda x: x["score"], reverse=True)  # type: ignore[arg-type]

        return suggestions

    def sync_all(
        self, fetch_custom_targeting_values: bool = False, max_custom_targeting_values_per_key: int = 1000
    ) -> dict[str, Any]:
        """
        Sync all inventory data from GAM.

        Args:
            fetch_custom_targeting_values: Whether to fetch custom targeting values during sync.
                                           Default False (lazy load values on demand).
                                           Set to True only if you need all values immediately.
            max_custom_targeting_values_per_key: Maximum number of values to fetch per key (only if fetch_custom_targeting_values=True).
                                                 Default 1000 to prevent timeouts with large datasets.

        Returns:
            Summary of synced data
        """
        logger.info(f"Starting full inventory sync for tenant {self.tenant_id}")
        if not fetch_custom_targeting_values:
            logger.info("Custom targeting: Keys only (values will be lazy loaded on demand)")
        else:
            logger.info(
                f"Custom targeting: Keys + values (limit: {max_custom_targeting_values_per_key} values per key)"
            )

        start_time = datetime.now()

        # Clear existing data
        self.ad_units.clear()
        self.placements.clear()
        self.labels.clear()
        self.custom_targeting_keys.clear()
        self.custom_targeting_values.clear()
        self.audience_segments.clear()

        # Discover all inventory - custom targeting values are lazy loaded by default
        ad_units = self.discover_ad_units()
        placements = self.discover_placements()
        labels = self.discover_labels()
        custom_targeting = self.discover_custom_targeting(
            max_values_per_key=max_custom_targeting_values_per_key, fetch_values=fetch_custom_targeting_values
        )
        audience_segments = self.discover_audience_segments()

        self.last_sync = datetime.now()

        summary = {
            "tenant_id": self.tenant_id,
            "sync_time": self.last_sync.isoformat(),
            "duration_seconds": (self.last_sync - start_time).total_seconds(),
            "ad_units": {
                "total": len(ad_units),
                "active": len([u for u in ad_units if u.status == AdUnitStatus.ACTIVE]),
                "with_children": len([u for u in ad_units if u.has_children]),
            },
            "placements": {"total": len(placements), "active": len([p for p in placements if p.status == "ACTIVE"])},
            "labels": {"total": len(labels), "active": len([l for l in labels if l.is_active])},
            "custom_targeting": {
                "total_keys": len(self.custom_targeting_keys),
                "total_values": custom_targeting.get("total_values", 0),
                "predefined_keys": len([k for k in self.custom_targeting_keys.values() if k.type == "PREDEFINED"]),
                "freeform_keys": len([k for k in self.custom_targeting_keys.values() if k.type == "FREEFORM"]),
                "values_fetched": custom_targeting.get("values_fetched", False),
                "note": (
                    "Values lazy loaded on demand"
                    if not custom_targeting.get("values_fetched")
                    else f"Values fetched (limit: {max_custom_targeting_values_per_key} per key)"
                ),
            },
            "audience_segments": {
                "total": len(audience_segments),
                "first_party": len([s for s in audience_segments if s.type == "FIRST_PARTY"]),
                "third_party": len([s for s in audience_segments if s.type == "THIRD_PARTY"]),
            },
        }

        logger.info(f"Inventory sync completed: {summary}")
        return summary

    def sync_selective(
        self,
        sync_types: list[str],
        custom_targeting_limit: int | None = None,
        audience_segment_limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Sync only specified inventory types from GAM.

        Args:
            sync_types: List of types to sync: "ad_units", "placements", "labels",
                       "custom_targeting", "audience_segments"
            custom_targeting_limit: Maximum number of custom targeting values to sync per key
            audience_segment_limit: Maximum number of audience segments to sync

        Returns:
            Summary of synced data
        """
        logger.info(f"Starting selective inventory sync for tenant {self.tenant_id}: {sync_types}")

        start_time = datetime.now()
        summary: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "sync_time": datetime.now().isoformat(),
            "sync_types": sync_types,
        }

        # Sync ad units
        if "ad_units" in sync_types:
            self.ad_units.clear()
            ad_units = self.discover_ad_units()
            summary["ad_units"] = {
                "total": len(ad_units),
                "active": len([u for u in ad_units if u.status == AdUnitStatus.ACTIVE]),
                "with_children": len([u for u in ad_units if u.has_children]),
            }

        # Sync placements
        if "placements" in sync_types:
            self.placements.clear()
            placements = self.discover_placements()
            summary["placements"] = {
                "total": len(placements),
                "active": len([p for p in placements if p.status == "ACTIVE"]),
            }

        # Sync labels
        if "labels" in sync_types:
            self.labels.clear()
            labels = self.discover_labels()
            summary["labels"] = {"total": len(labels), "active": len([l for l in labels if l.is_active])}

        # Sync custom targeting with optional limit
        if "custom_targeting" in sync_types:
            self.custom_targeting_keys.clear()
            self.custom_targeting_values.clear()
            custom_targeting = self.discover_custom_targeting(max_values_per_key=custom_targeting_limit)
            summary["custom_targeting"] = {
                "total_keys": len(self.custom_targeting_keys),
                "total_values": custom_targeting.get("total_values", 0),
                "predefined_keys": len([k for k in self.custom_targeting_keys.values() if k.type == "PREDEFINED"]),
                "freeform_keys": len([k for k in self.custom_targeting_keys.values() if k.type == "FREEFORM"]),
            }
            if custom_targeting_limit:
                summary["custom_targeting"]["limit_applied"] = custom_targeting_limit

        # Sync audience segments with optional limit
        if "audience_segments" in sync_types:
            self.audience_segments.clear()
            audience_segments = self.discover_audience_segments(max_segments=audience_segment_limit)
            summary["audience_segments"] = {
                "total": len(audience_segments),
                "first_party": len([s for s in audience_segments if s.type == "FIRST_PARTY"]),
                "third_party": len([s for s in audience_segments if s.type == "THIRD_PARTY"]),
            }
            if audience_segment_limit:
                summary["audience_segments"]["limit_applied"] = audience_segment_limit

        self.last_sync = datetime.now()
        summary["duration_seconds"] = (self.last_sync - start_time).total_seconds()

        logger.info(f"Selective inventory sync completed: {summary}")
        return summary

    def save_to_cache(self, cache_dir: str) -> None:
        """Save discovered inventory to cache files."""
        cache_path = os.path.join(cache_dir, f"gam_inventory_{self.tenant_id}.json")

        cache_data = {
            "tenant_id": self.tenant_id,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "ad_units": {id: unit.to_dict() for id, unit in self.ad_units.items()},
            "placements": {id: placement.to_dict() for id, placement in self.placements.items()},
            "labels": {id: label.to_dict() for id, label in self.labels.items()},
            "custom_targeting_keys": {id: key.to_dict() for id, key in self.custom_targeting_keys.items()},
            "custom_targeting_values": {
                key_id: [value.to_dict() for value in values] for key_id, values in self.custom_targeting_values.items()
            },
            "audience_segments": {id: segment.to_dict() for id, segment in self.audience_segments.items()},
        }

        with open(cache_path, "w") as f:
            json.dump(cache_data, f, indent=2)

        logger.info(f"Saved inventory cache to {cache_path}")

    def load_from_cache(self, cache_dir: str) -> bool:
        """
        Load inventory from cache if available and fresh.

        Returns:
            True if loaded successfully, False otherwise
        """
        cache_path = os.path.join(cache_dir, f"gam_inventory_{self.tenant_id}.json")

        if not os.path.exists(cache_path):
            return False

        try:
            with open(cache_path) as f:
                cache_data = json.load(f)

            # Check cache age
            if cache_data.get("last_sync"):
                last_sync = datetime.fromisoformat(cache_data["last_sync"])
                if datetime.now() - last_sync > timedelta(hours=24):
                    logger.info("Cache is older than 24 hours, skipping")
                    return False

            # Load data
            self.ad_units.clear()
            for id, unit_data in cache_data.get("ad_units", {}).items():
                unit_data["status"] = AdUnitStatus(unit_data["status"])
                self.ad_units[id] = AdUnit(**unit_data)

            self.placements.clear()
            for id, placement_data in cache_data.get("placements", {}).items():
                self.placements[id] = Placement(**placement_data)

            self.labels.clear()
            for id, label_data in cache_data.get("labels", {}).items():
                self.labels[id] = Label(**label_data)

            self.custom_targeting_keys.clear()
            for id, key_data in cache_data.get("custom_targeting_keys", {}).items():
                self.custom_targeting_keys[id] = CustomTargetingKey(**key_data)

            self.custom_targeting_values.clear()
            for key_id, values_list in cache_data.get("custom_targeting_values", {}).items():
                self.custom_targeting_values[key_id] = [
                    CustomTargetingValue(**value_data) for value_data in values_list
                ]

            self.audience_segments.clear()
            for id, segment_data in cache_data.get("audience_segments", {}).items():
                self.audience_segments[id] = AudienceSegment(**segment_data)

            self.last_sync = datetime.fromisoformat(cache_data["last_sync"]) if cache_data.get("last_sync") else None

            logger.info(
                f"Loaded inventory from cache: {len(self.ad_units)} ad units, "
                f"{len(self.placements)} placements, {len(self.labels)} labels, "
                f"{len(self.custom_targeting_keys)} custom targeting keys, "
                f"{len(self.audience_segments)} audience segments"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to load cache: {e}")
            return False


def create_inventory_sync_tool(app, get_gam_client_func):
    """
    Create Flask endpoints for inventory discovery.

    Args:
        app: Flask application
        get_gam_client_func: Function to get GAM client for a tenant
    """

    @app.route("/api/tenant/<tenant_id>/gam/inventory/sync", methods=["POST"])
    def sync_gam_inventory(tenant_id):
        """Trigger full inventory sync."""
        try:
            client = get_gam_client_func(tenant_id)
            if not client:
                return {"error": "GAM not configured for tenant"}, 404

            discovery = GAMInventoryDiscovery(client, tenant_id)
            summary = discovery.sync_all()

            # Save to cache
            discovery.save_to_cache("/tmp/gam_cache")

            return summary

        except Exception as e:
            logger.error(f"Inventory sync failed: {e}", exc_info=True)
            return {"error": str(e)}, 500

    @app.route("/api/tenant/<tenant_id>/gam/inventory/tree")
    def get_ad_unit_tree(tenant_id):
        """Get hierarchical ad unit tree."""
        try:
            client = get_gam_client_func(tenant_id)
            if not client:
                return {"error": "GAM not configured for tenant"}, 404

            discovery = GAMInventoryDiscovery(client, tenant_id)

            # Try to load from cache first
            if not discovery.load_from_cache("/tmp/gam_cache"):
                # If no cache, do a sync
                discovery.sync_all()
                discovery.save_to_cache("/tmp/gam_cache")

            return discovery.build_ad_unit_tree()

        except Exception as e:
            logger.error(f"Failed to get ad unit tree: {e}", exc_info=True)
            return {"error": str(e)}, 500

    @app.route("/api/tenant/<tenant_id>/gam/inventory/suggest")
    def suggest_ad_units(tenant_id):
        """Suggest ad units for a product."""
        from flask import request  # Import here - only used in Flask route context

        creative_sizes = request.args.getlist("sizes")
        keywords = request.args.getlist("keywords")

        # Parse sizes
        parsed_sizes = []
        for size in creative_sizes:
            if "x" in size:
                width, height = size.split("x")
                parsed_sizes.append({"width": int(width), "height": int(height)})

        if not parsed_sizes:
            return {"error": "No creative sizes specified"}, 400

        try:
            client = get_gam_client_func(tenant_id)
            if not client:
                return {"error": "GAM not configured for tenant"}, 404

            discovery = GAMInventoryDiscovery(client, tenant_id)

            # Load from cache or sync
            if not discovery.load_from_cache("/tmp/gam_cache"):
                discovery.sync_all()
                discovery.save_to_cache("/tmp/gam_cache")

            suggestions = discovery.suggest_ad_units_for_product(parsed_sizes, keywords)

            return {"suggestions": suggestions[:20], "total_evaluated": len(discovery.ad_units)}  # Top 20 suggestions

        except Exception as e:
            logger.error(f"Failed to suggest ad units: {e}", exc_info=True)
            return {"error": str(e)}, 500
