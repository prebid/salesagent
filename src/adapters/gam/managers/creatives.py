"""
GAM Creatives Manager

Handles creative validation, creation, upload, and association with line items
for Google Ad Manager campaigns.
"""

import base64
import logging
import random
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from src.core.schemas import AssetStatus

from ..utils.validation import GAMValidator

logger = logging.getLogger(__name__)


class GAMCreativesManager:
    """Manages creative operations for Google Ad Manager."""

    def __init__(self, client_manager, advertiser_id: str, dry_run: bool = False, log_func=None, adapter=None):
        """Initialize creatives manager.

        Args:
            client_manager: GAMClientManager instance
            advertiser_id: GAM advertiser ID
            dry_run: Whether to run in dry-run mode
            log_func: Optional logging function from adapter
            adapter: Optional reference to the main adapter for delegation
        """
        self.client_manager = client_manager
        self.advertiser_id = advertiser_id
        self.dry_run = dry_run
        self.validator = GAMValidator()
        self.log_func = log_func
        self.adapter = adapter

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Creates new Creatives in GAM and associates them with LineItems.

        Args:
            media_buy_id: GAM order ID
            assets: List of creative asset dictionaries
            today: Current datetime

        Returns:
            List of AssetStatus objects indicating success/failure for each creative
        """
        logger.info(f"Adding {len(assets)} creative assets for order '{media_buy_id}'")

        if not self.dry_run:
            creative_service = self.client_manager.get_service("CreativeService")
            lica_service = self.client_manager.get_service("LineItemCreativeAssociationService")
            line_item_service = self.client_manager.get_service("LineItemService")

        created_asset_statuses = []

        # Get line item mapping and creative placeholders
        line_item_map, creative_placeholders = self._get_line_item_info(
            media_buy_id, line_item_service if not self.dry_run else None
        )

        # DEBUG: Log what we got from GAM
        logger.info(f"[DEBUG] line_item_map keys: {list(line_item_map.keys())}")
        logger.info(f"[DEBUG] creative_placeholders keys: {list(creative_placeholders.keys())}")

        for asset in assets:
            logger.info(f"[DEBUG] Processing asset {asset.get('creative_id')} with package_assignments: {asset.get('package_assignments', [])}")
            # Validate creative asset against GAM requirements
            # Use adapter's method if available for test compatibility, otherwise use our own
            if self.adapter and hasattr(self.adapter, "_validate_creative_for_gam"):
                validation_issues = self.adapter._validate_creative_for_gam(asset)
            else:
                validation_issues = self._validate_creative_for_gam(asset)

            # Add creative size validation against placeholders
            size_validation_issues = self._validate_creative_size_against_placeholders(asset, creative_placeholders)
            validation_issues.extend(size_validation_issues)

            if validation_issues:
                # Use adapter log function if available, otherwise use logger
                if self.log_func:
                    self.log_func(f"[red]Creative {asset['creative_id']} failed GAM validation:[/red]")
                    for issue in validation_issues:
                        self.log_func(f"[red]  - {issue}[/red]")
                else:
                    # Fallback to logger if no log function provided
                    logger.error(f"Creative {asset['creative_id']} failed GAM validation:")
                    for issue in validation_issues:
                        logger.error(f"  - {issue}")
                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))
                continue

            # Determine creative type using AdCP v1.3+ logic
            # Use adapter's method if available for test compatibility, otherwise use our own
            if self.adapter and hasattr(self.adapter, "_get_creative_type"):
                creative_type = self.adapter._get_creative_type(asset)
            else:
                creative_type = self._get_creative_type(asset)

            if creative_type == "vast":
                # VAST is handled at line item level, not creative level
                logger.info(f"VAST creative {asset['creative_id']} - configuring at line item level")
                self._configure_vast_for_line_items(media_buy_id, asset, line_item_map)
                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="approved"))
                continue

            # Get placeholders for this asset's package assignments
            asset_placeholders = []
            for pkg_id in asset.get("package_assignments", []):
                if pkg_id in creative_placeholders:
                    asset_placeholders.extend(creative_placeholders[pkg_id])

            # Create GAM creative object
            try:
                creative = self._create_gam_creative(asset, creative_type, asset_placeholders)
                if not creative:
                    logger.warning(f"Skipping unsupported creative {asset['creative_id']} with type: {creative_type}")
                    created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))
                    continue

                # Create the creative in GAM
                if self.dry_run:
                    logger.info(f"Would call: creative_service.createCreatives([{creative.get('name', 'unnamed')}])")
                    gam_creative_id = f"mock_creative_{random.randint(100000, 999999)}"
                else:
                    # DEBUG: Log the exact creative being sent to GAM
                    logger.info(f"[DEBUG] Creating creative with fields: {list(creative.keys())}")
                    logger.info(f"[DEBUG] Creative type: {creative.get('xsi_type')}")
                    logger.info(f"[DEBUG] Creative data: {creative}")
                    created_creatives = creative_service.createCreatives([creative])
                    if not created_creatives:
                        logger.error(f"Failed to create creative {asset['creative_id']} - no creatives returned")
                        created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))
                        continue

                    gam_creative_id = created_creatives[0]["id"]
                    logger.info(f"✓ Created GAM Creative ID: {gam_creative_id}")

                # Associate creative with line items
                self._associate_creative_with_line_items(
                    gam_creative_id, asset, line_item_map, lica_service if not self.dry_run else None
                )

                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="approved"))

            except Exception as e:
                logger.error(f"Error creating creative {asset['creative_id']}: {str(e)}")
                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))

        return created_asset_statuses

    def _get_line_item_info(self, media_buy_id: str, line_item_service) -> tuple[dict[str, str], dict[str, list]]:
        """Get line item mapping and creative placeholders for an order.

        Args:
            media_buy_id: GAM order ID
            line_item_service: GAM LineItemService (None for dry run)

        Returns:
            Tuple of (line_item_map, creative_placeholders)
        """
        if not self.dry_run and line_item_service:
            statement = (
                self.client_manager.get_statement_builder()
                .Where("orderId = :orderId")
                .WithBindVariable("orderId", int(media_buy_id))
            )
            response = line_item_service.getLineItemsByStatement(statement.ToStatement())
            # GAM API returns a LineItemPage object (Zeep SOAP), not a dict
            line_items = getattr(response, "results", [])
            line_item_map = {item["name"]: item["id"] for item in line_items}

            # Collect all creative placeholders from line items for size validation
            # Key by BOTH line item name AND extracted package ID for flexible lookup
            creative_placeholders = {}
            for line_item in line_items:
                line_item_name = line_item["name"]
                # Line item is also a Zeep object, use getattr
                placeholders = getattr(line_item, "creativePlaceholders", [])

                # Store by line item name (for backward compatibility)
                creative_placeholders[line_item_name] = placeholders

                # ALSO extract package ID from line item name and store by that
                # NOTE: Line item names are configurable via tenant settings, but by default
                # they end with "- prod_XXXXXX". We extract the product ID pattern.
                # Package IDs are like "pkg_prod_XXXXXX_YYYYYYYY_N"
                # We'll try to extract prod_XXXXXX and match against all package_assignments
                if " - prod_" in line_item_name:
                    product_id = line_item_name.split(" - prod_")[-1].strip()
                    # Store with a "prod_" prefix for lookups
                    creative_placeholders[f"prod_{product_id}"] = placeholders
                    # Log the mapped sizes (Zeep objects - use getattr not .get())
                    sizes = []
                    for p in placeholders:
                        size_obj = getattr(p, 'size', None)
                        if size_obj:
                            width = getattr(size_obj, 'width', 0)
                            height = getattr(size_obj, 'height', 0)
                            sizes.append(f"{width}x{height}")
                    logger.info(f"[DEBUG] Mapped product ID 'prod_{product_id}' to placeholders: {sizes}")
        else:
            # In dry-run mode, create a mock line item map and placeholders
            # Support common test package names
            line_item_map = {
                "mock_package": "mock_line_item_123",
                "package_1": "mock_line_item_456",
                "package_2": "mock_line_item_789",
                "test_package": "mock_line_item_999",
            }
            creative_placeholders = {
                "mock_package": [
                    {"size": {"width": 300, "height": 250}, "creativeSizeType": "PIXEL"},
                    {"size": {"width": 728, "height": 90}, "creativeSizeType": "PIXEL"},
                ],
                "package_1": [
                    {"size": {"width": 300, "height": 250}, "creativeSizeType": "PIXEL"},
                    {"size": {"width": 728, "height": 90}, "creativeSizeType": "PIXEL"},
                ],
                "package_2": [
                    {"size": {"width": 320, "height": 50}, "creativeSizeType": "PIXEL"},
                    {"size": {"width": 970, "height": 250}, "creativeSizeType": "PIXEL"},
                ],
                "test_package": [
                    {"size": {"width": 970, "height": 250}, "creativeSizeType": "PIXEL"},
                    {"size": {"width": 336, "height": 280}, "creativeSizeType": "PIXEL"},
                    {"size": {"width": 300, "height": 250}, "creativeSizeType": "PIXEL"},  # Common default
                ],
            }

        return line_item_map, creative_placeholders

    def _get_creative_type(self, asset: dict[str, Any]) -> str:
        """Determine the creative type based on AdCP v1.3+ fields.

        Args:
            asset: Creative asset dictionary

        Returns:
            Creative type string
        """
        # Check AdCP v1.3+ fields first
        if asset.get("snippet") and asset.get("snippet_type"):
            if asset["snippet_type"] in ["vast_xml", "vast_url"]:
                return "vast"
            else:
                return "third_party_tag"
        elif asset.get("template_variables"):
            return "native"
        elif asset.get("media_url") or asset.get("media_data"):
            # Check if HTML5 based on file extension or format
            media_url = asset.get("media_url", "")
            format_str = asset.get("format", "")
            if (
                media_url.lower().endswith((".html", ".htm", ".html5", ".zip"))
                or "html5" in format_str.lower()
                or "rich_media" in format_str.lower()
            ):
                return "html5"
            else:
                return "hosted_asset"
        else:
            # Auto-detect from legacy patterns for backward compatibility
            url = asset.get("url", "")
            format_str = asset.get("format", "")

            if self._is_html_snippet(url):
                return "third_party_tag"
            elif "native" in format_str:
                return "native"
            elif url and (".xml" in url.lower() or "vast" in url.lower()):
                return "vast"
            elif (
                url.lower().endswith((".html", ".htm", ".html5", ".zip"))
                or "html5" in format_str.lower()
                or "rich_media" in format_str.lower()
            ):
                return "html5"
            else:
                return "hosted_asset"  # Default

    def _validate_creative_for_gam(self, asset: dict[str, Any]) -> list[str]:
        """Validate creative asset against GAM requirements before API submission.

        Args:
            asset: Creative asset dictionary

        Returns:
            List of validation error messages (empty if valid)
        """
        return self.validator.validate_creative_asset(asset)

    def _validate_creative_size_against_placeholders(
        self, asset: dict[str, Any], creative_placeholders: dict[str, list]
    ) -> list[str]:
        """Validate that creative format and asset requirements match available LineItem placeholders.

        Args:
            asset: Creative asset dictionary
            creative_placeholders: Dictionary mapping package names to placeholder lists

        Returns:
            List of validation error messages
        """
        validation_errors = []

        # Helper function to safely get attribute from dict or object
        def _get_attr(obj, name, default=None):
            """Get attribute from dict or object."""
            if isinstance(obj, dict):
                return obj.get(name, default)
            return getattr(obj, name, default)

        # Get asset dimensions
        try:
            asset_width, asset_height = self._get_creative_dimensions(asset, None)
        except Exception as e:
            validation_errors.append(f"Could not determine creative dimensions: {str(e)}")
            return validation_errors

        # Check if asset dimensions match any placeholder in its assigned packages
        package_assignments = asset.get("package_assignments", [])
        if not package_assignments:
            logger.warning(f"Creative {asset.get('creative_id', 'unknown')} has no package assignments")
            return validation_errors

        matching_placeholders_found = False
        for package_id in package_assignments:
            # Try direct lookup first (for backward compatibility with line item names)
            placeholders = creative_placeholders.get(package_id, [])

            # If not found, try matching by product ID extracted from package_id
            # Package IDs are like "pkg_prod_XXXXXX_YYYYYYYY_N", extract "prod_XXXXXX"
            if not placeholders and package_id.startswith("pkg_prod_"):
                # Extract product ID: "pkg_prod_2215c038_63e4864a_1" -> "prod_2215c038"
                parts = package_id.split("_")
                if len(parts) >= 3:  # pkg_prod_XXXXXX_...
                    product_id = f"prod_{parts[2]}"
                    placeholders = creative_placeholders.get(product_id, [])
                    if placeholders:
                        logger.info(f"[DEBUG] Matched package {package_id} to product ID {product_id}")
            for placeholder in placeholders:
                # Placeholder can be a Zeep object or dict (in tests)
                placeholder_size = _get_attr(placeholder, "size", None)
                if not placeholder_size:
                    continue
                placeholder_width = _get_attr(placeholder_size, "width", 0)
                placeholder_height = _get_attr(placeholder_size, "height", 0)

                # 1x1 placeholders are wildcards in GAM (native templates or programmatic)
                # They accept creatives of any size
                if placeholder_width == 1 and placeholder_height == 1:
                    matching_placeholders_found = True
                    template_id = _get_attr(placeholder, "creativeTemplateId", None)
                    if template_id:
                        logger.info(
                            f"Creative {asset_width}x{asset_height} matches 1x1 placeholder "
                            f"with GAM native template {template_id}"
                        )
                    else:
                        logger.info(
                            f"Creative {asset_width}x{asset_height} matches 1x1 wildcard placeholder "
                            f"(programmatic/third-party)"
                        )
                    break

                # Standard placeholders require exact dimension match
                if asset_width == placeholder_width and asset_height == placeholder_height:
                    matching_placeholders_found = True
                    break

            if matching_placeholders_found:
                break

        if not matching_placeholders_found:
            available_sizes = []
            for package_id in package_assignments:
                # Try direct lookup first
                placeholders = creative_placeholders.get(package_id, [])

                # If not found, try matching by product ID extracted from package_id
                if not placeholders and package_id.startswith("pkg_prod_"):
                    parts = package_id.split("_")
                    if len(parts) >= 3:
                        product_id = f"prod_{parts[2]}"
                        placeholders = creative_placeholders.get(product_id, [])
                for placeholder in placeholders:
                    # Placeholder can be a Zeep object or dict (in tests)
                    size = _get_attr(placeholder, "size", None)
                    if size:
                        width = _get_attr(size, "width", 0)
                        height = _get_attr(size, "height", 0)
                        available_sizes.append(f"{width}x{height}")

            validation_errors.append(
                f"Creative size {asset_width}x{asset_height} does not match any LineItem placeholders. "
                f"Available sizes in assigned packages: {', '.join(set(available_sizes))}"
            )

        return validation_errors

    def _create_gam_creative(
        self, asset: dict[str, Any], creative_type: str, placeholders: list[dict] = None
    ) -> dict[str, Any] | None:
        """Create a GAM creative object based on the asset type.

        Args:
            asset: Creative asset dictionary
            creative_type: Type of creative to create
            placeholders: List of creative placeholders for validation

        Returns:
            GAM creative dictionary or None if unsupported
        """
        if creative_type == "third_party_tag":
            return self._create_third_party_creative(asset)
        elif creative_type == "native":
            return self._create_native_creative(asset)
        elif creative_type == "html5":
            return self._create_html5_creative(asset)
        elif creative_type == "hosted_asset":
            return self._create_hosted_asset_creative(asset)
        else:
            logger.warning(f"Unsupported creative type: {creative_type}")
            return None

    def _create_third_party_creative(self, asset: dict[str, Any]) -> dict[str, Any]:
        """Create a third-party creative for GAM."""
        width, height = self._get_creative_dimensions(asset)

        # Use snippet if available (AdCP v1.3+), otherwise fall back to URL
        snippet = asset.get("snippet")
        if not snippet:
            snippet = asset.get("url", "")

        creative = {
            "xsi_type": "ThirdPartyCreative",
            "name": asset.get("name", f"AdCP Creative {asset.get('creative_id', 'unknown')}"),
            "advertiserId": self.advertiser_id,
            "size": {"width": width, "height": height},
            "snippet": snippet,
        }

        self._add_tracking_urls_to_creative(creative, asset)
        return creative

    def _create_native_creative(self, asset: dict[str, Any]) -> dict[str, Any]:
        """Create a native creative for GAM."""
        template_id = self._get_native_template_id(asset)
        template_variables = self._build_native_template_variables(asset)

        creative = {
            "xsi_type": "TemplateCreative",
            "name": asset.get("name", f"AdCP Native Creative {asset.get('creative_id', 'unknown')}"),
            "advertiserId": self.advertiser_id,
            "creativeTemplateId": template_id,
            "creativeTemplateVariableValues": template_variables,
        }

        return creative

    def _create_html5_creative(self, asset: dict[str, Any]) -> dict[str, Any]:
        """Create an HTML5 creative for GAM."""
        width, height = self._get_creative_dimensions(asset)
        html_source = self._get_html5_source(asset)

        creative = {
            "xsi_type": "CustomCreative",
            "name": asset.get("name", f"AdCP HTML5 Creative {asset.get('creative_id', 'unknown')}"),
            "advertiserId": self.advertiser_id,
            "size": {"width": width, "height": height},
            "htmlSnippet": html_source,
        }

        self._add_tracking_urls_to_creative(creative, asset)
        return creative

    def _create_hosted_asset_creative(self, asset: dict[str, Any]) -> dict[str, Any]:
        """Create a hosted asset (image/video) creative for GAM."""
        width, height = self._get_creative_dimensions(asset)

        # Get the creative URL
        url = asset.get("url")
        if not url:
            raise Exception("No URL found for hosted asset creative")

        # Determine asset type
        asset_type = self._determine_asset_type(asset)

        # Get click-through URL (required by GAM for redirect creatives)
        # TODO: Implement proper click-through URL handling per AdCP spec
        # For now, use the asset URL as fallback if no explicit click_url is provided
        click_url = asset.get("clickthrough_url") or asset.get("landing_url") or asset.get("click_url") or url

        if asset_type == "image":
            # ImageRedirectCreative requires both image URL and click-through URL
            # Using asset URL as fallback for click_url (see TODO above)
            if not click_url:
                raise ValueError(
                    f"Image creative {asset.get('creative_id')} missing required click_url. "
                    f"GAM ImageRedirectCreative requires a destination URL."
                )

            # Validate that image URL is an actual URL, not binary data
            if not url or not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise ValueError(
                    f"Image creative {asset.get('creative_id')} has invalid URL: {url}. "
                    f"GAM ImageRedirectCreative requires an HTTP(S) URL, not binary data."
                )

            creative = {
                "xsi_type": "ImageRedirectCreative",
                "name": asset.get("name", f"AdCP Image Creative {asset.get('creative_id', 'unknown')}"),
                "advertiserId": self.advertiser_id,
                "size": {"width": width, "height": height},
                "imageUrl": url,
                "destinationUrl": click_url,
            }
        elif asset_type == "video":
            # For video, we can use VideoRedirectCreative
            # Per AdCP spec, video assets have required duration field
            # https://adcontextprotocol.org/schemas/v1/core/assets/video-asset.json
            duration = asset.get("duration")
            if not duration:
                raise ValueError(f"Video creative {asset.get('creative_id')} missing required duration field")

            creative = {
                "xsi_type": "VideoRedirectCreative",
                "name": asset.get("name", f"AdCP Video Creative {asset.get('creative_id', 'unknown')}"),
                "advertiserId": self.advertiser_id,
                "size": {"width": width, "height": height},
                "videoSourceUrl": url,
                "destinationUrl": click_url,
                "duration": int(duration * 1000),  # GAM expects milliseconds, AdCP provides seconds
            }
        else:
            raise Exception(f"Unsupported asset type: {asset_type}")

        self._add_tracking_urls_to_creative(creative, asset)
        return creative

    def _get_creative_dimensions(self, asset: dict[str, Any], placeholders: list[dict] = None) -> tuple[int, int]:
        """Get creative dimensions from asset or format.

        Args:
            asset: Creative asset dictionary
            placeholders: Optional list of placeholders for validation

        Returns:
            Tuple of (width, height)
        """
        # Try explicit width/height first
        if asset.get("width") and asset.get("height"):
            return int(asset["width"]), int(asset["height"])

        # Try to parse from format string
        format_str = asset.get("format", "")
        if format_str:
            # Extract dimensions from format like "display_300x250"
            parts = format_str.lower().split("_")
            for part in parts:
                if "x" in part:
                    try:
                        width_str, height_str = part.split("x")
                        return int(width_str), int(height_str)
                    except (ValueError, IndexError):
                        continue

        # Default fallback
        logger.warning(
            f"Could not determine dimensions for creative {asset.get('creative_id', 'unknown')}, using 300x250 default"
        )
        return 300, 250

    def _is_html_snippet(self, content: str) -> bool:
        """Check if content appears to be an HTML snippet."""
        if not content:
            return False
        content_lower = content.lower().strip()
        return any(
            [
                content_lower.startswith("<script"),
                content_lower.startswith("<div"),
                content_lower.startswith("<iframe"),
                content_lower.startswith("<!doctype"),
                content_lower.startswith("<html"),
            ]
        )

    def _get_html5_source(self, asset: dict[str, Any]) -> str:
        """Get HTML5 source content for the creative."""
        # Try media_data first (direct HTML content)
        if asset.get("media_data"):
            try:
                # Decode base64 if needed
                content = asset["media_data"]
                if content.startswith("data:"):
                    # Extract base64 part after comma
                    content = content.split(",", 1)[1]
                    content = base64.b64decode(content).decode("utf-8")
                return content
            except Exception as e:
                logger.warning(f"Failed to decode media_data: {e}")

        # Fall back to media_url
        if asset.get("media_url"):
            return f'<iframe src="{asset["media_url"]}" width="100%" height="100%" frameborder="0"></iframe>'

        # Last resort: use URL field
        url = asset.get("url", "")
        if url:
            return f'<iframe src="{url}" width="100%" height="100%" frameborder="0"></iframe>'

        raise Exception("No HTML5 source content found in asset")

    def _upload_binary_asset(self, asset: dict[str, Any]) -> dict[str, Any] | None:
        """Upload binary asset to GAM and return asset info."""
        if self.dry_run:
            logger.info("Would upload binary asset to GAM")
            return {
                "assetId": f"mock_asset_{random.randint(100000, 999999)}",
                "fileName": asset.get("name", "mock_asset.jpg"),
                "fileSize": 12345,
                "mimeType": self._get_content_type(asset),
            }

        # Implementation would handle actual upload to GAM
        # This is a simplified version
        logger.warning("Binary asset upload not fully implemented")
        return None

    def _get_content_type(self, asset: dict[str, Any]) -> str:
        """Determine content type from asset."""
        # Check explicit mime type
        if asset.get("mime_type"):
            return asset["mime_type"]

        # Guess from URL extension
        url = asset.get("media_url") or asset.get("url", "")
        if url:
            parsed = urlparse(url)
            path = parsed.path.lower()
            if path.endswith((".jpg", ".jpeg")):
                return "image/jpeg"
            elif path.endswith(".png"):
                return "image/png"
            elif path.endswith(".gif"):
                return "image/gif"
            elif path.endswith((".mp4", ".mov")):
                return "video/mp4"

        # Default
        return "image/jpeg"

    def _determine_asset_type(self, asset: dict[str, Any]) -> str:
        """Determine if asset is image or video."""
        content_type = self._get_content_type(asset)
        if content_type.startswith("video/"):
            return "video"
        else:
            return "image"

    def _get_native_template_id(self, asset: dict[str, Any]) -> str:
        """Get the GAM native template ID for the asset."""
        # This would need to be configured per network
        return "123456"  # Placeholder

    def _build_native_template_variables(self, asset: dict[str, Any]) -> list[dict[str, Any]]:
        """Build native template variables from asset."""
        variables = []
        template_vars = asset.get("template_variables", {})

        for key, value in template_vars.items():
            variables.append(
                {
                    "uniqueName": key,
                    "value": {
                        "xsi_type": "StringCreativeTemplateVariableValue",
                        "value": str(value),
                    },
                }
            )

        return variables

    def _add_tracking_urls_to_creative(self, creative: dict[str, Any], asset: dict[str, Any]) -> None:
        """Add tracking URLs to the creative if available."""
        tracking_events = asset.get("tracking_events", {})

        # Add impression tracking
        if tracking_events.get("impression"):
            creative["trackingUrls"] = [{"url": url} for url in tracking_events["impression"]]

        # Add click tracking (for supported creative types)
        if tracking_events.get("click") and creative.get("xsi_type") in ["ImageCreative", "ThirdPartyCreative"]:
            creative["destinationUrl"] = tracking_events["click"][0]  # Use first click URL

    def _configure_vast_for_line_items(
        self, media_buy_id: str, asset: dict[str, Any], line_item_map: dict[str, str]
    ) -> None:
        """Configure VAST creative at line item level."""
        if self.dry_run:
            logger.info(f"Would configure VAST for line items in order {media_buy_id}")
            return

        # VAST configuration would be implemented here
        logger.info(f"Configuring VAST creative {asset['creative_id']} for line items")

    def _associate_creative_with_line_items(
        self, gam_creative_id: str, asset: dict[str, Any], line_item_map: dict[str, str], lica_service
    ) -> None:
        """Associate creative with its assigned line items."""
        package_assignments = asset.get("package_assignments", [])

        for package_id in package_assignments:
            # Line item map is keyed by line item name (which ends with "- prod_XXXXXX")
            # Package IDs are like "pkg_prod_XXXXXX_YYYYYYYY_N"
            # We need to match them by product ID
            line_item_id = None

            # Extract product ID from package_id: "pkg_prod_2215c038_..." -> "prod_2215c038"
            if package_id.startswith("pkg_prod_"):
                parts = package_id.split("_")
                if len(parts) >= 3:
                    product_id = f"prod_{parts[2]}"
                    logger.info(f"[DEBUG] Looking for line item ending with ' - {product_id}'")
                    # Find line item that ends with this product ID
                    for line_item_name, item_id in line_item_map.items():
                        logger.info(f"[DEBUG] Checking line item: {line_item_name}")
                        logger.info(f"[DEBUG] Does it end with ' - {product_id}'? {line_item_name.endswith(f' - {product_id}')}")
                        if line_item_name.endswith(f" - {product_id}"):
                            line_item_id = item_id
                            logger.info(f"[DEBUG] MATCH! Package {package_id} -> line item {line_item_name} (ID: {item_id})")
                            break

            if not line_item_id:
                logger.warning(f"Line item not found for package {package_id}. line_item_map has {len(line_item_map)} entries")
                continue

            if self.dry_run:
                logger.info(f"Would associate creative {gam_creative_id} with line item {line_item_id}")
            else:
                # Create Line Item Creative Association
                association = {
                    "creativeId": gam_creative_id,
                    "lineItemId": line_item_id,
                }

                try:
                    lica_service.createLineItemCreativeAssociations([association])
                    logger.info(f"✓ Associated creative {gam_creative_id} with line item {line_item_id}")
                except Exception as e:
                    logger.error(f"Failed to associate creative {gam_creative_id} with line item {line_item_id}: {e}")
                    raise
