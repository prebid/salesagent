"""
GAM Targeting Manager

Handles targeting validation, translation from AdCP targeting to GAM targeting,
and geo mapping operations for Google Ad Manager campaigns.
"""

import csv
import json
import logging
from pathlib import Path
from typing import Any

from adcp.types.generated_poc.enums.metro_system import MetroAreaSystem

from src.core.errors.details import CapabilityRefusalDetails
from src.core.exceptions import (
    AdCPAdapterError,
    AdCPCapabilityNotSupportedError,
    AdCPConfigurationError,
    AdCPSalesAgentError,
)
from src.services.targeting_capabilities import (
    extract_simple_values,
    extract_system_values,
    geo_inclusion_cut,
    nielsen_dma_codes,
    region_country,
)

logger = logging.getLogger(__name__)

#: GAM numbers its DMA_REGION geo targets 200000 plus the Nielsen DMA code (200501 is
#: "New York NY"), as ``SELECT Id, Name FROM Geo_Target WHERE Type = 'DMA_REGION'`` shows on
#: any network. Google's published geo target file carries no DMA rows, so the ids are
#: computed from the accepted Nielsen codes rather than stored.
GAM_DMA_ID_OFFSET = 200000


def _only(matches: list[dict[str, str]], what: str) -> dict[str, str]:
    """The one row in *matches*; ``LookupError`` naming *what* when there are none or several."""
    if len(matches) != 1:
        raise LookupError(f"{what}: {len(matches)} rows in geo_targets.csv, expected exactly one")
    return matches[0]


def _load_gam_geo_locations() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Derive every accepted geo code's GAM id from ``geo_targets.csv`` and ``geo_codes.json``.

    Returns ``(countries, regions, dmas)``: ISO 3166-1 code, full ISO 3166-2 code and Nielsen
    DMA code, each mapped to its GAM geo target id. Geo target ids are global, not per network.

    Countries and regions are rows copied from Google's published geo target file; a region
    is the one row under its country whose name and target type ``geo_codes.json`` states.
    DMA ids follow GAM's numbering (``GAM_DMA_ID_OFFSET``). Ids stay the strings the file
    holds. Raises ``OSError`` (a missing file), ``ValueError`` (JSON that does not parse),
    ``csv.Error``, or ``LookupError`` naming the code or row that cannot be derived.
    """
    data_dir = Path(__file__).resolve().parents[1]
    with (data_dir / "geo_targets.csv").open(encoding="utf-8", newline="") as rows_file:
        rows = list(csv.DictReader(rows_file))
    codes = json.loads((data_dir / "geo_codes.json").read_text(encoding="utf-8"))

    country_rows: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if row["Target Type"] == "Country":
            country_rows.setdefault(row["Country Code"], []).append(row)
    countries = {code: _only(matches, f"country {code}")["Criteria ID"] for code, matches in country_rows.items()}

    regions: dict[str, str] = {}
    for code, region in codes["regions"].items():
        country = region_country(code)
        if country not in countries:
            raise LookupError(f"region {code}: no Country row for {country}")
        matches = [
            row
            for row in rows
            if row["Country Code"] == country
            and row["Parent ID"] == countries[country]
            and row["Name"] == region["name"]
            and row["Target Type"] == region["type"]
        ]
        regions[code] = _only(matches, f"region {code} ({region['name']}, {region['type']})")["Criteria ID"]

    dmas = {code: str(GAM_DMA_ID_OFFSET + int(code)) for code in codes["nielsen_dma"]}
    return countries, regions, dmas


def _gam_dma_codes(metros: list | None) -> set[str]:
    """The Nielsen DMA codes in a ``geo_metros`` field; any other metro system is refused."""
    by_system = extract_system_values(metros or [])
    others = sorted(set(by_system) - {MetroAreaSystem.nielsen_dma.value})
    if others:
        raise AdCPCapabilityNotSupportedError(
            details=CapabilityRefusalDetails(
                capability="geo_system", rejected_value=others, accepted_values=[MetroAreaSystem.nielsen_dma.value]
            )
        )
    return nielsen_dma_codes(metros)


def _location_ids(field: str, codes: set[str], ids: dict[str, str]) -> dict[str, str]:
    """Each code's GAM id; a code with none is refused, naming *field* and every such code."""
    unmapped = sorted(code for code in codes if code not in ids)
    if unmapped:
        raise AdCPCapabilityNotSupportedError(
            details=CapabilityRefusalDetails(capability=field, rejected_value=unmapped)
        )
    return {code: ids[code] for code in codes}


def _locations(location_ids: dict[str, str], codes: set[str]) -> list[dict[str, str]]:
    """GAM ``Location`` objects for *codes*, sorted by code so the payload is deterministic."""
    return [{"id": location_ids[code]} for code in sorted(codes)]


class GAMTargetingManager:
    """Manages targeting operations for Google Ad Manager."""

    # Supported device types and their GAM numeric device category IDs
    # These are GAM's standard device category IDs that work across networks
    DEVICE_TYPE_MAP = {
        "mobile": 30000,  # Mobile devices
        "desktop": 30001,  # Desktop computers
        "tablet": 30002,  # Tablet devices
        "ctv": 30003,  # Connected TV / Streaming devices
        "dooh": 30004,  # Digital out-of-home / Set-top box
    }

    # Supported media types
    SUPPORTED_MEDIA_TYPES = {"video", "display", "native"}

    def __init__(
        self,
        tenant_id: str,
        gam_client: Any | None = None,
        targeting_config: dict[str, Any] | None = None,
    ):
        """Initialize targeting manager.

        Args:
            tenant_id: Tenant ID for loading adapter configuration
            gam_client: Optional GAM client for syncing custom targeting keys
            targeting_config: Pre-loaded targeting config from AdapterConfigRepository.
                If provided, skips DB queries. Dict keys: axe_include_key,
                axe_exclude_key, axe_macro_key, custom_targeting_keys.
        """
        self.tenant_id = tenant_id
        self.gam_client = gam_client
        # Load and check the geo data now, so a broken data file refuses the adapter at
        # construction rather than at the first geo overlay.
        self.geo_country_map, self.geo_region_map, self.geo_metro_map = _load_gam_geo_locations()
        self.axe_include_key: str | None = None
        self.axe_exclude_key: str | None = None
        self.axe_macro_key: str | None = None
        self.custom_targeting_key_ids: dict[str, str] = {}  # Maps key names → GAM key IDs
        if targeting_config:
            # Use pre-loaded config (eliminates adapter→DB circular dependency)
            self.axe_include_key = targeting_config.get("axe_include_key")
            self.axe_exclude_key = targeting_config.get("axe_exclude_key")
            self.axe_macro_key = targeting_config.get("axe_macro_key")
            self.custom_targeting_key_ids = targeting_config.get("custom_targeting_keys", {})
            logger.info(
                f"Loaded AXE keys for tenant {tenant_id} from injected config: "
                f"include={self.axe_include_key}, exclude={self.axe_exclude_key}, macro={self.axe_macro_key}"
            )
            logger.info(
                f"Loaded {len(self.custom_targeting_key_ids)} custom targeting key IDs for tenant {tenant_id} from injected config"
            )
        else:
            # Fallback: load from DB (backward compat for callers that don't pass config)
            self._load_axe_keys()
            self._load_custom_targeting_key_ids()

    def _load_axe_keys(self):
        """Load tenant-specific AXE configuration from database.

        Per AdCP spec, three separate keys are required:
        - axe_include_key: For axe_include_segment targeting
        - axe_exclude_key: For axe_exclude_segment targeting
        - axe_macro_key: For creative macro substitution

        These are adapter-agnostic and work with all ad server adapters.
        """
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig

        try:
            with get_db_session() as session:
                stmt = select(AdapterConfig).filter_by(tenant_id=self.tenant_id)
                adapter_config = session.scalars(stmt).first()

                if adapter_config:
                    self.axe_include_key = adapter_config.axe_include_key
                    self.axe_exclude_key = adapter_config.axe_exclude_key
                    self.axe_macro_key = adapter_config.axe_macro_key

                    logger.info(
                        f"Loaded AXE keys for tenant {self.tenant_id}: "
                        f"include={self.axe_include_key}, "
                        f"exclude={self.axe_exclude_key}, "
                        f"macro={self.axe_macro_key}"
                    )
                else:
                    logger.warning(f"No adapter config found for tenant {self.tenant_id}")
        except Exception as e:
            logger.warning(f"Failed to load AXE keys from config: {e}")

    def _load_custom_targeting_key_ids(self):
        """Load custom targeting key ID mappings from database.

        This loads the cached mapping of key names → GAM key IDs from adapter_config.custom_targeting_keys.
        The mapping must be synced from GAM using sync_custom_targeting_keys() before use.
        """
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig

        try:
            with get_db_session() as session:
                stmt = select(AdapterConfig).filter_by(tenant_id=self.tenant_id)
                adapter_config = session.scalars(stmt).first()

                if adapter_config and adapter_config.custom_targeting_keys:
                    self.custom_targeting_key_ids = adapter_config.custom_targeting_keys
                    logger.info(
                        f"Loaded {len(self.custom_targeting_key_ids)} custom targeting key ID mappings for tenant {self.tenant_id}"
                    )
                else:
                    logger.warning(
                        f"No custom targeting key mappings found for tenant {self.tenant_id}. "
                        "Run sync_custom_targeting_keys() to fetch from GAM."
                    )
        except Exception as e:
            logger.warning(f"Failed to load custom targeting key IDs: {e}")

    def sync_custom_targeting_keys(self) -> dict[str, Any]:
        """Sync custom targeting keys from GAM and store in database.

        Fetches all custom targeting keys from GAM API and creates a mapping
        of key names → key IDs. This mapping is stored in adapter_config.custom_targeting_keys.

        Returns:
            Dict with sync results:
            {
                "synced_keys": {"key_name": "key_id", ...},
                "count": int,
                "errors": [...]
            }

        Raises:
            ValueError: If GAM client not configured
        """
        if not self.gam_client:
            raise AdCPConfigurationError()

        from src.core.database.database_session import get_db_session

        try:
            # Fetch all custom targeting keys from GAM
            custom_targeting_service = self.gam_client.GetService("CustomTargetingService")

            # Create statement to get all keys
            statement = {"query": ""}  # Empty query gets all keys

            key_name_to_id: dict[str, str] = {}

            # Page through results
            offset = 0
            limit = 500

            while True:
                statement["query"] = f"LIMIT {limit} OFFSET {offset}"
                response = custom_targeting_service.getCustomTargetingKeysByStatement(statement)

                # GAM API returns zeep objects, not dicts - check for 'results' attribute
                if hasattr(response, "results") and response.results:
                    for key in response.results:
                        key_name_to_id[key.name] = str(key.id)

                    total_results = getattr(response, "totalResultSetSize", 0)
                    if offset + limit >= total_results:
                        break
                    offset += limit
                else:
                    break

            # Store mapping in database via repository
            from src.core.database.database_session import get_db_session
            from src.core.database.repositories.adapter_config import AdapterConfigRepository

            with get_db_session() as session:
                repo = AdapterConfigRepository(session, self.tenant_id)
                repo.update_custom_targeting_keys(key_name_to_id)
                session.commit()

                # Update in-memory cache
                self.custom_targeting_key_ids = key_name_to_id

                logger.info(f"Synced {len(key_name_to_id)} custom targeting keys from GAM for tenant {self.tenant_id}")

            return {
                "synced_keys": key_name_to_id,
                "count": len(key_name_to_id),
                "errors": [],
            }

        except Exception as e:
            logger.error(f"Failed to sync custom targeting keys from GAM: {e}", exc_info=True)
            return {
                "synced_keys": {},
                "count": 0,
                "errors": [str(e)],
            }

    def resolve_custom_targeting_key_id(self, key_name: str) -> str:
        """Resolve a custom targeting key name to its GAM key ID.

        Args:
            key_name: The custom targeting key name (e.g., "axe_include_segment")

        Returns:
            The GAM key ID as a string

        Raises:
            AdCPConfigurationError: If the key is absent from this seller's GAM
                key mapping. Seller-side: the buyer never supplied this name, and
                the remedy is running sync_custom_targeting_keys().
        """
        if key_name not in self.custom_targeting_key_ids:
            raise AdCPConfigurationError()

        return self.custom_targeting_key_ids[key_name]

    def _get_or_create_custom_targeting_value(self, key_id: str, value_name: str) -> int:
        """Get or create a custom targeting value in GAM.

        Args:
            key_id: The GAM custom targeting key ID
            value_name: The value name to look up or create

        Returns:
            The GAM custom targeting value ID

        Raises:
            ValueError: If GAM API call fails
        """
        if not self.gam_client:
            raise AdCPConfigurationError()

        try:
            custom_targeting_service = self.gam_client.GetService("CustomTargetingService")

            # First, try to find existing value
            # SECURITY: Escape single quotes to prevent SQL-style injection in SOAP query
            escaped_value_name = value_name.replace("'", "\\'")
            statement = {"query": f"WHERE customTargetingKeyId = {key_id} AND name = '{escaped_value_name}'"}
            response = custom_targeting_service.getCustomTargetingValuesByStatement(statement)

            if hasattr(response, "results") and response.results:
                # Found existing value
                value_id = int(response.results[0].id)
                logger.info(f"Found existing custom targeting value: {value_name} (ID: {value_id})")
                return value_id

            # Value doesn't exist, create it
            value = {
                "customTargetingKeyId": int(key_id),
                "name": value_name,
                "displayName": value_name,
                "matchType": "EXACT",  # Exact match for AXE segment values
            }

            created_values = custom_targeting_service.createCustomTargetingValues([value])
            if created_values:
                value_id = int(created_values[0]["id"])
                logger.info(f"Created custom targeting value: {value_name} (ID: {value_id})")
                return value_id

            raise AdCPAdapterError()

        except AdCPSalesAgentError:
            raise
        except Exception as e:
            logger.error(f"Failed to get/create custom targeting value '{value_name}': {e}", exc_info=True)
            raise AdCPAdapterError(internal_detail=e) from e

    def _build_custom_targeting_structure(
        self, custom_targeting_dict: dict[str, Any], logical_operator: str = "AND"
    ) -> dict[str, Any]:
        """Convert custom targeting dict to GAM CustomCriteria structure.

        GAM API expects custom targeting in this format:
        {
            "logicalOperator": "AND" | "OR",
            "children": [
                {
                    "xsi_type": "CustomCriteria",
                    "keyId": "123456",
                    "operator": "IS" | "IS_NOT",
                    "valueIds": [value_id1, value_id2]
                }
            ]
        }

        Supports three input formats:

        Legacy format (dict[str, str]):
            {'key_id': 'value_name', 'NOT_key_id': 'value_name'}

        Enhanced format (dict with include/exclude):
            {
                'include': {'key_id': ['value1', 'value2']},
                'exclude': {'key_id': ['value3']},
                'operator': 'AND' | 'OR'
            }

        Groups format (GAM-style nested groups):
            {
                'groups': [
                    {
                        'criteria': [
                            {'keyId': '123', 'values': ['v1', 'v2']},
                            {'keyId': '456', 'values': ['v3'], 'exclude': True}
                        ]
                    }
                ]
            }
            Groups are OR'd together; criteria within groups are AND'd.

        Args:
            custom_targeting_dict: Custom targeting configuration in any format
            logical_operator: Default operator for combining criteria (AND/OR)

        Returns:
            GAM CustomCriteria structure
        """
        children = []

        # Check if this is the groups format (GAM-style nested)
        if "groups" in custom_targeting_dict:
            return self._build_groups_custom_targeting_structure(custom_targeting_dict)

        # Check if this is the enhanced format with include/exclude
        if "include" in custom_targeting_dict or "exclude" in custom_targeting_dict:
            return self._build_enhanced_custom_targeting_structure(custom_targeting_dict)

        # Legacy format: {'key_id': 'value_name', 'NOT_key_id': 'value_name'}
        for key, value_name in custom_targeting_dict.items():
            # Check for negative targeting (NOT_ prefix)
            is_negative = key.startswith("NOT_")
            key_id = key[4:] if is_negative else key  # Remove NOT_ prefix

            # Build custom criteria object
            # For custom targeting values, GAM requires value IDs (not names)
            # We need to create or lookup the value ID for the value name
            value_id = self._get_or_create_custom_targeting_value(key_id, value_name)

            criteria = {
                "xsi_type": "CustomCriteria",  # Explicit type for zeep SOAP serialization
                "keyId": int(key_id),  # GAM expects integer key ID
                "operator": "IS_NOT" if is_negative else "IS",
                "valueIds": [value_id],  # Custom targeting value ID
            }
            children.append(criteria)

        return {
            "xsi_type": "CustomCriteriaSet",  # Explicit type for zeep
            "logicalOperator": logical_operator,
            "children": children,
        }

    def _build_enhanced_custom_targeting_structure(self, targeting_config: dict[str, Any]) -> dict[str, Any]:
        """Build GAM targeting structure from enhanced format with include/exclude.

        Enhanced format:
            {
                'include': {'key_id': ['value1', 'value2']},  # Multiple values = OR within key
                'exclude': {'key_id': ['value3']},
                'operator': 'AND' | 'OR'  # How to combine different keys
            }

        Values can be either:
        - Numeric GAM value IDs (e.g., "451005167391") - used directly
        - Value names (e.g., "sports") - looked up via _get_or_create_custom_targeting_value

        Args:
            targeting_config: Enhanced targeting configuration

        Returns:
            GAM CustomCriteria structure with proper nesting for OR/AND logic
        """
        include_dict = targeting_config.get("include", {})
        exclude_dict = targeting_config.get("exclude", {})
        operator = targeting_config.get("operator", "AND")

        children = []

        # Process include criteria
        for key_id, values in include_dict.items():
            if not values:
                continue

            # Multiple values for same key are OR'd together (IS operator with multiple valueIds)
            value_ids = []
            for value in values:
                # Check if value is already a numeric GAM ID
                if str(value).isdigit():
                    value_ids.append(int(value))
                else:
                    # Value is a name, need to look up or create
                    value_id = self._get_or_create_custom_targeting_value(key_id, value)
                    value_ids.append(value_id)

            criteria = {
                "xsi_type": "CustomCriteria",
                "keyId": int(key_id),
                "operator": "IS",
                "valueIds": value_ids,  # Multiple values = OR logic in GAM
            }
            children.append(criteria)

        # Process exclude criteria
        for key_id, values in exclude_dict.items():
            if not values:
                continue

            # For exclusions, each value gets IS_NOT
            # Multiple excluded values for same key means "NOT value1 AND NOT value2"
            value_ids = []
            for value in values:
                # Check if value is already a numeric GAM ID
                if str(value).isdigit():
                    value_ids.append(int(value))
                else:
                    # Value is a name, need to look up or create
                    value_id = self._get_or_create_custom_targeting_value(key_id, value)
                    value_ids.append(value_id)

            criteria = {
                "xsi_type": "CustomCriteria",
                "keyId": int(key_id),
                "operator": "IS_NOT",
                "valueIds": value_ids,
            }
            children.append(criteria)

        if not children:
            return {}

        return {
            "xsi_type": "CustomCriteriaSet",
            "logicalOperator": operator,
            "children": children,
        }

    def _build_groups_custom_targeting_structure(self, targeting_config: dict[str, Any]) -> dict[str, Any]:
        """Build GAM targeting structure from groups format (GAM-style nested).

        Groups format supports nested targeting with OR between groups and AND within groups:
            {
                'groups': [
                    {
                        'criteria': [
                            {'keyId': '123', 'values': ['v1', 'v2']},
                            {'keyId': '456', 'values': ['v3'], 'exclude': True}
                        ]
                    },
                    {
                        'criteria': [
                            {'keyId': '789', 'values': ['v4']}
                        ]
                    }
                ]
            }

        This translates to: (key123 IS v1|v2 AND key456 IS_NOT v3) OR (key789 IS v4)

        Values can be either:
        - Numeric GAM value IDs (e.g., "451005167391") - used directly
        - Value names (e.g., "sports") - looked up via _get_or_create_custom_targeting_value

        Args:
            targeting_config: Groups targeting configuration

        Returns:
            GAM CustomCriteria structure with nested CustomCriteriaSets
        """
        groups = targeting_config.get("groups", [])
        group_children = []

        for group in groups:
            criteria_list = group.get("criteria", [])
            criteria_children = []

            for criterion in criteria_list:
                key_id = criterion.get("keyId")
                values = criterion.get("values", [])
                is_exclude = criterion.get("exclude", False)

                if not key_id or not values:
                    logger.warning(f"Skipping malformed criterion in groups targeting: keyId={key_id}, values={values}")
                    continue

                # Resolve values to GAM value IDs
                value_ids = []
                for value in values:
                    if str(value).isdigit():
                        value_ids.append(int(value))
                    else:
                        value_id = self._get_or_create_custom_targeting_value(key_id, value)
                        value_ids.append(value_id)

                if not value_ids:
                    continue

                criteria_children.append(
                    {
                        "xsi_type": "CustomCriteria",
                        "keyId": int(key_id),
                        "operator": "IS_NOT" if is_exclude else "IS",
                        "valueIds": value_ids,
                    }
                )

            # Only add groups that have criteria
            if criteria_children:
                group_children.append(
                    {
                        "xsi_type": "CustomCriteriaSet",
                        "logicalOperator": "AND",  # Criteria within group are AND'd
                        "children": criteria_children,
                    }
                )

        if not group_children:
            return {}

        return {
            "xsi_type": "CustomCriteriaSet",
            "logicalOperator": "OR",  # Groups are OR'd together
            "children": group_children,
        }

    def validate_targeting(self, targeting_overlay) -> list[str]:
        """Validate targeting and return unsupported features.

        Args:
            targeting_overlay: AdCP targeting overlay object

        Returns:
            List of unsupported feature descriptions
        """
        unsupported: list[str] = []

        if not targeting_overlay:
            return unsupported

        # Geo codes and systems GAM cannot place are refused here, before any order exists.
        self._translate_geo(targeting_overlay)

        # Check device types
        if targeting_overlay.device_form_factors:
            for device in targeting_overlay.device_form_factors:
                if device not in self.DEVICE_TYPE_MAP:
                    unsupported.append(f"Device type '{device}' not supported")

        # Check media types
        if targeting_overlay.media_type_any_of:
            for media in targeting_overlay.media_type_any_of:
                if media not in self.SUPPORTED_MEDIA_TYPES:
                    unsupported.append(f"Media type '{media}' not supported")

        # Audio-specific targeting not supported
        if targeting_overlay.media_type_any_of and "audio" in targeting_overlay.media_type_any_of:
            unsupported.append("Audio media type not supported by Google Ad Manager")

        # Postal code targeting requires GAM geo service integration (not implemented)
        if targeting_overlay.geo_postal_areas or targeting_overlay.geo_postal_areas_exclude:
            unsupported.append("Postal code targeting requires GAM geo service integration (not implemented)")

        # GAM supports all other standard targeting dimensions

        return unsupported

    def _translate_geo(self, targeting_overlay) -> dict[str, Any]:
        """GAM ``geoTargeting`` for the overlay's geo fields: every code placed or refused.

        Every code of all six fields is mapped first, so a code GAM has no location for is
        refused even where the AND would drop it. Inclusion fields AND
        (docs/reference/migration/geo-targeting.mdx at v3.1.1) and GAM ORs one location
        list, so ``targetedLocations`` is the finest inclusion field present, cut to the
        listed countries by the core ``geo_inclusion_cut``. Exclusions AND NOT, which is
        what GAM's ``excludedLocations`` does with any listed location.
        """
        countries = _location_ids(
            "geo_countries", extract_simple_values(targeting_overlay.geo_countries or []), self.geo_country_map
        )
        regions = _location_ids(
            "geo_regions", extract_simple_values(targeting_overlay.geo_regions or []), self.geo_region_map
        )
        dmas = _location_ids("geo_metros", _gam_dma_codes(targeting_overlay.geo_metros), self.geo_metro_map)
        excluded = [
            _location_ids(
                "geo_countries_exclude",
                extract_simple_values(targeting_overlay.geo_countries_exclude or []),
                self.geo_country_map,
            ),
            _location_ids(
                "geo_regions_exclude",
                extract_simple_values(targeting_overlay.geo_regions_exclude or []),
                self.geo_region_map,
            ),
            _location_ids(
                "geo_metros_exclude", _gam_dma_codes(targeting_overlay.geo_metros_exclude), self.geo_metro_map
            ),
        ]

        # A state and a DMA overlap only in part, and GAM's OR list cannot express that part.
        if regions and dmas:
            raise AdCPCapabilityNotSupportedError(
                details=CapabilityRefusalDetails(capability="geo_metros", rejected_value=sorted(dmas))
            )

        geo_targeting: dict[str, Any] = {}
        regions_kept, dmas_kept = geo_inclusion_cut(targeting_overlay)
        if regions:
            geo_targeting["targetedLocations"] = _locations(regions, regions_kept)
        elif dmas:
            geo_targeting["targetedLocations"] = _locations(dmas, dmas_kept)
        elif countries:
            geo_targeting["targetedLocations"] = _locations(countries, set(countries))
        excluded_locations = [location for ids in excluded for location in _locations(ids, set(ids))]
        if excluded_locations:
            geo_targeting["excludedLocations"] = excluded_locations
        return geo_targeting

    def build_targeting(self, targeting_overlay) -> dict[str, Any]:
        """Build GAM targeting criteria from AdCP targeting.

        Args:
            targeting_overlay: AdCP targeting overlay object

        Returns:
            Dictionary containing GAM targeting configuration

        Raises:
            ValueError: If unsupported targeting is requested (no quiet failures)
        """
        if not targeting_overlay:
            return {}

        gam_targeting: dict[str, Any] = {}

        # Postal code targeting not implemented in static mapping - fail loudly.
        # The capability NAME travels as structured detail; the buyer-facing sentence is
        # the code's own.
        if targeting_overlay.geo_postal_areas:
            raise AdCPCapabilityNotSupportedError(details=CapabilityRefusalDetails(capability="geo_postal_areas"))
        if targeting_overlay.geo_postal_areas_exclude:
            raise AdCPCapabilityNotSupportedError(
                details=CapabilityRefusalDetails(capability="geo_postal_areas_exclude")
            )

        geo_targeting = self._translate_geo(targeting_overlay)
        if geo_targeting:
            gam_targeting["geoTargeting"] = geo_targeting

        # Technology/Device targeting - NOT SUPPORTED, MUST FAIL LOUDLY. Named by the
        # field the buyer actually sent: the seller extension, or the spec's device_platform.
        if targeting_overlay.device_form_factors:
            raise AdCPCapabilityNotSupportedError(
                details=CapabilityRefusalDetails(
                    capability="device_type_any_of" if targeting_overlay.device_type_any_of else "device_platform",
                    rejected_value=targeting_overlay.device_form_factors,
                )
            )

        if targeting_overlay.os_any_of:
            raise AdCPCapabilityNotSupportedError(
                details=CapabilityRefusalDetails(capability="os_any_of", rejected_value=targeting_overlay.os_any_of)
            )

        if targeting_overlay.browser_any_of:
            raise AdCPCapabilityNotSupportedError(
                details=CapabilityRefusalDetails(
                    capability="browser_any_of", rejected_value=targeting_overlay.browser_any_of
                )
            )

        # Content targeting - NOT SUPPORTED, MUST FAIL LOUDLY
        if targeting_overlay.content_cat_any_of:
            raise AdCPCapabilityNotSupportedError(
                details=CapabilityRefusalDetails(
                    capability="content_cat_any_of", rejected_value=targeting_overlay.content_cat_any_of
                )
            )

        if targeting_overlay.keywords_any_of:
            raise AdCPCapabilityNotSupportedError(
                details=CapabilityRefusalDetails(
                    capability="keywords_any_of", rejected_value=targeting_overlay.keywords_any_of
                )
            )

        # Custom key-value targeting
        custom_targeting = {}

        # Platform-specific custom targeting
        if targeting_overlay.custom and "gam" in targeting_overlay.custom:
            custom_targeting.update(targeting_overlay.custom["gam"].get("key_values", {}))

        # AXE segment targeting (AdCP pre-3.0 axe_include_segment/axe_exclude_segment;
        # deprecated in 3.0.x in favor of TMP provider fields)
        # Per AdCP spec, three separate keys are required for include, exclude, and macro segments
        if targeting_overlay.axe_include_segment:
            if not self.axe_include_key:
                raise AdCPCapabilityNotSupportedError(
                    details=CapabilityRefusalDetails(capability="axe_include_segment")
                )
            # Resolve key name to GAM key ID
            try:
                key_id = self.resolve_custom_targeting_key_id(self.axe_include_key)
                custom_targeting[key_id] = targeting_overlay.axe_include_segment
                logger.info(
                    f"Adding AXE include segment targeting: {self.axe_include_key} (ID: {key_id})={targeting_overlay.axe_include_segment}"
                )
            except AdCPConfigurationError:
                logger.error(f"Failed to resolve AXE include key '{self.axe_include_key}'")
                raise

        if targeting_overlay.axe_exclude_segment:
            if not self.axe_exclude_key:
                raise AdCPCapabilityNotSupportedError(
                    details=CapabilityRefusalDetails(capability="axe_exclude_segment")
                )
            # Resolve key name to GAM key ID
            try:
                key_id = self.resolve_custom_targeting_key_id(self.axe_exclude_key)
                # GAM supports negative targeting via NOT_ prefix on the KEY ID
                exclude_key_id = f"NOT_{key_id}"
                custom_targeting[exclude_key_id] = targeting_overlay.axe_exclude_segment
                logger.info(
                    f"Adding AXE exclude segment targeting: {self.axe_exclude_key} (ID: {key_id}, negated)={targeting_overlay.axe_exclude_segment}"
                )
            except AdCPConfigurationError:
                logger.error(f"Failed to resolve AXE exclude key '{self.axe_exclude_key}'")
                raise

        if custom_targeting:
            # Convert simple dict to GAM CustomCriteria structure
            # GAM expects: {logicalOperator, children: [{keyId, operator, valueIds, valueNames}]}
            # Our dict: {'key_id': 'value_name', 'NOT_key_id': 'value_name'}
            gam_targeting["customTargeting"] = self._build_custom_targeting_structure(custom_targeting)

        # Audience segment targeting
        # Map AdCP audiences_any_of and signals to GAM audience segment IDs
        if targeting_overlay.audiences_any_of or targeting_overlay.signals:
            # Note: This requires GAM audience segment ID mapping configured per tenant
            # For now, we fail loudly to indicate it's not fully implemented
            audience_list = []
            if targeting_overlay.audiences_any_of:
                audience_list.extend(targeting_overlay.audiences_any_of)
            if targeting_overlay.signals:
                audience_list.extend(targeting_overlay.signals)

            raise AdCPCapabilityNotSupportedError(
                details=CapabilityRefusalDetails(capability="audiences", rejected_value=audience_list)
            )

        # Media type targeting - map to GAM environmentType
        # This should be set on line items, not in targeting dict
        # We'll store it for the line item creation logic to use
        if targeting_overlay.media_type_any_of:
            # Validate only one media type (GAM line items have single environmentType)
            if len(targeting_overlay.media_type_any_of) > 1:
                raise AdCPCapabilityNotSupportedError(
                    details=CapabilityRefusalDetails(
                        capability="media_type_any_of", rejected_value=targeting_overlay.media_type_any_of
                    )
                )

            media_type = targeting_overlay.media_type_any_of[0]
            # Map AdCP media types to GAM environmentType
            media_type_map = {
                "video": "VIDEO_PLAYER",
                "display": "BROWSER",
                "native": "BROWSER",
                # audio and dooh not directly supported by GAM
            }

            if media_type in media_type_map:
                # Store for line item creation - will be picked up by orders manager
                environment_type = media_type_map[media_type]
                gam_targeting["_media_type_environment"] = environment_type
                logger.info(f"Media type '{media_type}' mapped to GAM environmentType: {environment_type}")
            else:
                raise AdCPCapabilityNotSupportedError(
                    details=CapabilityRefusalDetails(
                        capability="media_type",
                        rejected_value=media_type,
                        accepted_values=sorted(media_type_map),
                    )
                )

        logger.info(f"Applying GAM targeting: {list(gam_targeting.keys())}")
        return gam_targeting

    def add_inventory_targeting(
        self,
        targeting: dict[str, Any],
        targeted_ad_unit_ids: list[str] | None = None,
        targeted_placement_ids: list[str] | None = None,
        include_descendants: bool = True,
    ) -> dict[str, Any]:
        """Add inventory targeting to GAM targeting configuration.

        Args:
            targeting: Existing GAM targeting configuration
            targeted_ad_unit_ids: Optional list of ad unit IDs to target
            targeted_placement_ids: Optional list of placement IDs to target
            include_descendants: Whether to include descendant ad units

        Returns:
            Updated targeting configuration with inventory targeting
        """
        inventory_targeting = {}

        if targeted_ad_unit_ids:
            inventory_targeting["targetedAdUnits"] = [
                {"adUnitId": ad_unit_id, "includeDescendants": include_descendants}
                for ad_unit_id in targeted_ad_unit_ids
            ]

        if targeted_placement_ids:
            inventory_targeting["targetedPlacements"] = [
                {"placementId": placement_id} for placement_id in targeted_placement_ids
            ]

        if inventory_targeting:
            targeting["inventoryTargeting"] = inventory_targeting

        return targeting

    def add_custom_targeting(self, targeting: dict[str, Any], custom_keys: dict[str, Any]) -> dict[str, Any]:
        """Add custom targeting keys to GAM targeting configuration.

        Args:
            targeting: Existing GAM targeting configuration
            custom_keys: Dictionary of custom targeting key-value pairs

        Returns:
            Updated targeting configuration with custom targeting
        """
        if custom_keys:
            if "customTargeting" not in targeting:
                targeting["customTargeting"] = {}
            targeting["customTargeting"].update(custom_keys)

        return targeting
