"""
GAM Targeting Manager

Handles targeting validation, translation from AdCP targeting to GAM targeting,
and geo mapping operations for Google Ad Manager campaigns.
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


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

    def __init__(self, tenant_id: str, gam_client: Any | None = None):
        """Initialize targeting manager.

        Args:
            tenant_id: Tenant ID for loading adapter configuration
            gam_client: Optional GAM client for syncing custom targeting keys
        """
        self.tenant_id = tenant_id
        self.gam_client = gam_client
        self.geo_country_map: dict[str, str] = {}
        self.geo_region_map: dict[str, dict[str, str]] = {}
        self.geo_metro_map: dict[str, str] = {}
        self.axe_include_key: str | None = None
        self.axe_exclude_key: str | None = None
        self.axe_macro_key: str | None = None
        self.custom_targeting_key_ids: dict[str, str] = {}  # Maps key names → GAM key IDs
        self._load_geo_mappings()
        self._load_axe_keys()
        self._load_custom_targeting_key_ids()

    def _load_geo_mappings(self):
        """Load static geo mappings from JSON file on disk.

        Loads AdCP country codes → GAM geo IDs from gam_geo_mappings.json.
        This is static data that doesn't change per tenant.
        """
        try:
            # Look for the geo mappings file relative to the adapters directory
            mapping_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gam_geo_mappings.json")
            with open(mapping_file) as f:
                geo_data = json.load(f)

            self.geo_country_map = geo_data.get("countries", {})
            self.geo_region_map = geo_data.get("regions", {})
            self.geo_metro_map = geo_data.get("metros", {}).get("US", {})  # Currently only US metros

            logger.info(
                f"Loaded GAM geo mappings: {len(self.geo_country_map)} countries, "
                f"{sum(len(v) for v in self.geo_region_map.values())} regions, "
                f"{len(self.geo_metro_map)} metros"
            )
        except Exception as e:
            logger.warning(f"Could not load geo mappings file: {e}")
            logger.warning("Using empty geo mappings - geo targeting will not work properly")
            self.geo_country_map = {}
            self.geo_region_map = {}
            self.geo_metro_map = {}

    def _load_axe_keys(self):
        """Load tenant-specific AXE configuration from database.

        Per AdCP spec, three separate keys are required:
        - axe_include_key: For axe_include_segment targeting
        - axe_exclude_key: For axe_exclude_segment targeting
        - axe_macro_key: For creative macro substitution

        These are adapter-agnostic and work with all ad server adapters.

        **Why Standalone Function:**
        This is separate from _load_geo_mappings() because:
        1. Different data sources: Database (tenant-specific) vs. File (static)
        2. Different lifecycles: Per-tenant config vs. one-time initialization
        3. Different loading patterns: SQL query vs. JSON file read
        4. Clear separation of concerns: Tenant config vs. static mappings

        While this could be part of a generic "load_tenant_config" function,
        keeping it separate makes it easier to understand, test, and maintain.
        Each method has a single, clear responsibility.
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
            raise ValueError("GAM client required for syncing custom targeting keys")

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig

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

            # Store mapping in database
            with get_db_session() as session:
                stmt = select(AdapterConfig).filter_by(tenant_id=self.tenant_id)
                adapter_config = session.scalars(stmt).first()

                if adapter_config:
                    adapter_config.custom_targeting_keys = key_name_to_id
                    session.commit()

                    # Update in-memory cache
                    self.custom_targeting_key_ids = key_name_to_id

                    logger.info(
                        f"Synced {len(key_name_to_id)} custom targeting keys from GAM for tenant {self.tenant_id}"
                    )
                else:
                    raise ValueError(f"No adapter config found for tenant {self.tenant_id}")

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
            ValueError: If key name not found in mapping
        """
        if key_name not in self.custom_targeting_key_ids:
            raise ValueError(
                f"Custom targeting key '{key_name}' not found in GAM key mappings. "
                f"Available keys: {list(self.custom_targeting_key_ids.keys())}. "
                "Run sync_custom_targeting_keys() to update the mapping."
            )

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
            raise ValueError("GAM client required for custom targeting value operations")

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

            raise ValueError(f"Failed to create custom targeting value '{value_name}' for key ID {key_id}")

        except Exception as e:
            logger.error(f"Failed to get/create custom targeting value '{value_name}': {e}", exc_info=True)
            raise ValueError(f"Custom targeting value lookup/creation failed for '{value_name}': {e}")

    def _build_custom_targeting_structure(self, custom_targeting_dict: dict[str, str]) -> dict[str, Any]:
        """Convert simple custom targeting dict to GAM CustomCriteria structure.

        GAM API expects custom targeting in this format:
        {
            "logicalOperator": "AND",
            "children": [
                {
                    "keyId": "123456",
                    "operator": "IS",
                    "valueIds": [],
                    "valueNames": ["value_name"]
                }
            ]
        }

        Our input dict format: {'key_id': 'value_name', 'NOT_key_id': 'value_name'}

        Args:
            custom_targeting_dict: Dict mapping GAM key IDs to value names
                Keys can have "NOT_" prefix for negative targeting

        Returns:
            GAM CustomCriteria structure
        """
        children = []

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
            "logicalOperator": "AND",
            "children": children,
        }

    def _lookup_region_id(self, region_code: str) -> str | None:
        """Look up region ID across all countries.

        Args:
            region_code: The region code to look up

        Returns:
            GAM region ID if found, None otherwise
        """
        # First check if we have country context (not implemented yet)
        # For now, search across all countries
        for _country, regions in self.geo_region_map.items():
            if region_code in regions:
                return regions[region_code]
        return None

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

        # Check device types
        if targeting_overlay.device_type_any_of:
            for device in targeting_overlay.device_type_any_of:
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

        # City and postal targeting require GAM API lookups (not implemented)
        if targeting_overlay.geo_city_any_of or targeting_overlay.geo_city_none_of:
            unsupported.append("City targeting requires GAM geo service integration (not implemented)")
        if targeting_overlay.geo_zip_any_of or targeting_overlay.geo_zip_none_of:
            unsupported.append("Postal code targeting requires GAM geo service integration (not implemented)")

        # GAM supports all other standard targeting dimensions

        return unsupported

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

        # Geographic targeting
        geo_targeting: dict[str, Any] = {}

        # Build targeted locations - only for supported geo features
        if any(
            [
                targeting_overlay.geo_country_any_of,
                targeting_overlay.geo_region_any_of,
                targeting_overlay.geo_metro_any_of,
            ]
        ):
            geo_targeting["targetedLocations"] = []

            # Map countries
            if targeting_overlay.geo_country_any_of:
                for country in targeting_overlay.geo_country_any_of:
                    if country in self.geo_country_map:
                        geo_targeting["targetedLocations"].append({"id": self.geo_country_map[country]})
                    else:
                        logger.warning(f"Country code '{country}' not in GAM mapping")

            # Map regions
            if targeting_overlay.geo_region_any_of:
                for region in targeting_overlay.geo_region_any_of:
                    region_id = self._lookup_region_id(region)
                    if region_id:
                        geo_targeting["targetedLocations"].append({"id": region_id})
                    else:
                        logger.warning(f"Region code '{region}' not in GAM mapping")

            # Map metros (DMAs)
            if targeting_overlay.geo_metro_any_of:
                for metro in targeting_overlay.geo_metro_any_of:
                    if metro in self.geo_metro_map:
                        geo_targeting["targetedLocations"].append({"id": self.geo_metro_map[metro]})
                    else:
                        logger.warning(f"Metro code '{metro}' not in GAM mapping")

        # City and postal code targeting not supported - fail loudly
        if targeting_overlay.geo_city_any_of:
            raise ValueError(
                f"City targeting requested but not supported. "
                f"Cannot fulfill buyer contract for cities: {targeting_overlay.geo_city_any_of}. "
                f"Use geo_metro_any_of for metropolitan area targeting instead."
            )
        if targeting_overlay.geo_zip_any_of:
            raise ValueError(
                f"Postal code targeting requested but not supported. "
                f"Cannot fulfill buyer contract for postal codes: {targeting_overlay.geo_zip_any_of}. "
                f"Use geo_metro_any_of for metropolitan area targeting instead."
            )

        # Build excluded locations - only for supported geo features
        if any(
            [
                targeting_overlay.geo_country_none_of,
                targeting_overlay.geo_region_none_of,
                targeting_overlay.geo_metro_none_of,
            ]
        ):
            geo_targeting["excludedLocations"] = []

            # Map excluded countries
            if targeting_overlay.geo_country_none_of:
                for country in targeting_overlay.geo_country_none_of:
                    if country in self.geo_country_map:
                        geo_targeting["excludedLocations"].append({"id": self.geo_country_map[country]})

            # Map excluded regions
            if targeting_overlay.geo_region_none_of:
                for region in targeting_overlay.geo_region_none_of:
                    region_id = self._lookup_region_id(region)
                    if region_id:
                        geo_targeting["excludedLocations"].append({"id": region_id})

            # Map excluded metros
            if targeting_overlay.geo_metro_none_of:
                for metro in targeting_overlay.geo_metro_none_of:
                    if metro in self.geo_metro_map:
                        geo_targeting["excludedLocations"].append({"id": self.geo_metro_map[metro]})

        # City and postal code exclusions not supported - fail loudly
        if targeting_overlay.geo_city_none_of:
            raise ValueError(
                f"City exclusion requested but not supported. "
                f"Cannot fulfill buyer contract for excluded cities: {targeting_overlay.geo_city_none_of}."
            )
        if targeting_overlay.geo_zip_none_of:
            raise ValueError(
                f"Postal code exclusion requested but not supported. "
                f"Cannot fulfill buyer contract for excluded postal codes: {targeting_overlay.geo_zip_none_of}."
            )

        if geo_targeting:
            gam_targeting["geoTargeting"] = geo_targeting

        # Technology/Device targeting - NOT SUPPORTED, MUST FAIL LOUDLY
        if targeting_overlay.device_type_any_of:
            raise ValueError(
                f"Device targeting requested but not supported. "
                f"Cannot fulfill buyer contract for device types: {targeting_overlay.device_type_any_of}."
            )

        if targeting_overlay.os_any_of:
            raise ValueError(
                f"OS targeting requested but not supported. "
                f"Cannot fulfill buyer contract for OS types: {targeting_overlay.os_any_of}."
            )

        if targeting_overlay.browser_any_of:
            raise ValueError(
                f"Browser targeting requested but not supported. "
                f"Cannot fulfill buyer contract for browsers: {targeting_overlay.browser_any_of}."
            )

        # Content targeting - NOT SUPPORTED, MUST FAIL LOUDLY
        if targeting_overlay.content_cat_any_of:
            raise ValueError(
                f"Content category targeting requested but not supported. "
                f"Cannot fulfill buyer contract for categories: {targeting_overlay.content_cat_any_of}."
            )

        if targeting_overlay.keywords_any_of:
            raise ValueError(
                f"Keyword targeting requested but not supported. "
                f"Cannot fulfill buyer contract for keywords: {targeting_overlay.keywords_any_of}."
            )

        # Custom key-value targeting
        custom_targeting = {}

        # Platform-specific custom targeting
        if targeting_overlay.custom and "gam" in targeting_overlay.custom:
            custom_targeting.update(targeting_overlay.custom["gam"].get("key_values", {}))

        # AEE signal integration via key-value pairs (managed-only)
        if targeting_overlay.key_value_pairs:
            logger.info("Adding AEE signals to GAM key-value targeting")
            for key_name, value in targeting_overlay.key_value_pairs.items():
                # Resolve key name to GAM key ID
                try:
                    key_id = self.resolve_custom_targeting_key_id(key_name)
                    custom_targeting[key_id] = value
                    logger.info(f"  {key_name} (ID: {key_id}): {value}")
                except ValueError as e:
                    logger.error(f"Failed to resolve custom targeting key '{key_name}': {e}")
                    raise

        # AXE segment targeting (AdCP 3.0.3 axe_include_segment/axe_exclude_segment)
        # Per AdCP spec, three separate keys are required for include, exclude, and macro segments
        if targeting_overlay.axe_include_segment:
            if not self.axe_include_key:
                raise ValueError(
                    "AXE include segment targeting requested but axe_include_key not configured. "
                    "Configure AXE keys in tenant adapter settings to support this targeting."
                )
            # Resolve key name to GAM key ID
            try:
                key_id = self.resolve_custom_targeting_key_id(self.axe_include_key)
                custom_targeting[key_id] = targeting_overlay.axe_include_segment
                logger.info(
                    f"Adding AXE include segment targeting: {self.axe_include_key} (ID: {key_id})={targeting_overlay.axe_include_segment}"
                )
            except ValueError as e:
                logger.error(f"Failed to resolve AXE include key '{self.axe_include_key}': {e}")
                raise ValueError(
                    f"AXE include key '{self.axe_include_key}' not found in GAM. "
                    "Create the custom targeting key in GAM UI and sync using 'Sync Custom Targeting Keys' button."
                ) from e

        if targeting_overlay.axe_exclude_segment:
            if not self.axe_exclude_key:
                raise ValueError(
                    "AXE exclude segment targeting requested but axe_exclude_key not configured. "
                    "Configure AXE keys in tenant adapter settings to support this targeting."
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
            except ValueError as e:
                logger.error(f"Failed to resolve AXE exclude key '{self.axe_exclude_key}': {e}")
                raise ValueError(
                    f"AXE exclude key '{self.axe_exclude_key}' not found in GAM. "
                    "Create the custom targeting key in GAM UI and sync using 'Sync Custom Targeting Keys' button."
                ) from e

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

            raise ValueError(
                f"Audience/signal targeting requested but GAM audience segment mapping not configured. "
                f"Cannot fulfill buyer contract for: {', '.join(audience_list)}. "
                f"Configure audience segment ID mappings in tenant adapter config to support this targeting."
            )

        # Media type targeting - map to GAM environmentType
        # This should be set on line items, not in targeting dict
        # We'll store it for the line item creation logic to use
        if targeting_overlay.media_type_any_of:
            # Validate only one media type (GAM line items have single environmentType)
            if len(targeting_overlay.media_type_any_of) > 1:
                raise ValueError(
                    f"Multiple media types requested but GAM supports only one environmentType per line item. "
                    f"Requested: {targeting_overlay.media_type_any_of}. "
                    f"Create separate packages for each media type."
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
                raise ValueError(
                    f"Media type '{media_type}' is not supported in GAM. "
                    f"Supported types: {', '.join(media_type_map.keys())}"
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
