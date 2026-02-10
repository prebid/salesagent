"""Broadstreet Advertisement Manager.

Handles advertisement (creative) operations including creation,
updates, and linking to placements.

Broadstreet Ad Types:
- html: HTML/JavaScript ad content
- static: Static image ads (banner images)
- text: Text-only ads

Template-Based Ads:
Special Broadstreet formats (3D Cube, YouTube, Gallery, etc.) use a two-step
creation process:
1. Create base HTML ad
2. Set source with template type and assets
"""

import logging
from collections.abc import Callable
from typing import Any

from src.adapters.broadstreet.client import BroadstreetClient
from src.adapters.broadstreet.config_schema import BROADSTREET_TEMPLATES, get_template_info

logger = logging.getLogger(__name__)


# Map AdCP format types to Broadstreet ad types
FORMAT_TO_AD_TYPE = {
    # Display formats
    "display": "static",
    "image": "static",
    "static": "static",
    "banner": "static",
    # HTML formats
    "html": "html",
    "html5": "html",
    "rich_media": "html",
    "custom": "html",
    # Text formats
    "text": "text",
    "native_text": "text",
}


class AdvertisementInfo:
    """Tracks advertisement state."""

    def __init__(
        self,
        creative_id: str,
        broadstreet_id: str | None,
        name: str,
        ad_type: str,
        status: str = "pending",
    ):
        self.creative_id = creative_id
        self.broadstreet_id = broadstreet_id
        self.name = name
        self.ad_type = ad_type
        self.status = status
        self.placement_ids: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "creative_id": self.creative_id,
            "broadstreet_id": self.broadstreet_id,
            "name": self.name,
            "ad_type": self.ad_type,
            "status": self.status,
            "placement_ids": self.placement_ids,
        }


class BroadstreetAdvertisementManager:
    """Manages advertisement operations for Broadstreet.

    Advertisements in Broadstreet are the actual ad creative content.
    They are linked to zones via placements within campaigns.
    """

    def __init__(
        self,
        client: BroadstreetClient | None,
        advertiser_id: str,
        dry_run: bool = False,
        log_func: Callable[[str], None] | None = None,
    ):
        """Initialize the advertisement manager.

        Args:
            client: Broadstreet API client (None for dry-run mode)
            advertiser_id: Broadstreet advertiser ID
            dry_run: Whether to simulate operations
            log_func: Optional logging function
        """
        self.client = client
        self.advertiser_id = advertiser_id
        self.dry_run = dry_run
        self.log = log_func or (lambda msg: logger.info(msg))

        # Track advertisements by media buy
        # Structure: {media_buy_id: {creative_id: AdvertisementInfo}}
        self._ad_cache: dict[str, dict[str, AdvertisementInfo]] = {}

    def _get_ad_type(self, asset: dict[str, Any]) -> str:
        """Determine Broadstreet ad type from asset data.

        Args:
            asset: Asset data dictionary

        Returns:
            Broadstreet ad type (html, static, text)
        """
        # Check format field
        format_type = asset.get("format", "").lower()
        if format_type in FORMAT_TO_AD_TYPE:
            return FORMAT_TO_AD_TYPE[format_type]

        # Check for HTML content
        if asset.get("html") or asset.get("snippet"):
            return "html"

        # Check for image URL
        if asset.get("media_url") or asset.get("image_url"):
            url = asset.get("media_url") or asset.get("image_url", "")
            # Check file extension
            if any(url.lower().endswith(ext) for ext in [".html", ".htm", ".zip"]):
                return "html"
            return "static"

        # Check for text content
        if asset.get("default_text") or asset.get("headline"):
            return "text"

        # Default to static
        return "static"

    def _build_ad_params(self, asset: dict[str, Any], ad_type: str) -> dict[str, Any]:
        """Build Broadstreet API parameters for ad creation.

        Args:
            asset: Asset data dictionary
            ad_type: Broadstreet ad type

        Returns:
            API parameters dictionary
        """
        params: dict[str, Any] = {}

        if ad_type == "html":
            # HTML ad - use html or snippet content
            html_content = asset.get("html") or asset.get("snippet", "")
            if html_content:
                params["html"] = html_content
            elif asset.get("media_url"):
                # Load from URL (Broadstreet may support this)
                params["html"] = f'<iframe src="{asset["media_url"]}" width="100%" height="100%"></iframe>'

        elif ad_type == "static":
            # Static image ad
            if asset.get("media_url"):
                params["image"] = asset["media_url"]
            elif asset.get("image_url"):
                params["image"] = asset["image_url"]
            elif asset.get("image_base64"):
                params["image_base64"] = asset["image_base64"]

            # Add click URL if provided
            if asset.get("click_url"):
                params["url"] = asset["click_url"]

        elif ad_type == "text":
            # Text ad
            if asset.get("default_text"):
                params["default_text"] = asset["default_text"]
            elif asset.get("headline"):
                # Build text from structured fields
                text_parts = []
                if asset.get("headline"):
                    text_parts.append(asset["headline"])
                if asset.get("description"):
                    text_parts.append(asset["description"])
                params["default_text"] = "\n".join(text_parts)

        return params

    def _build_template_source_params(self, template_type: str, asset: dict[str, Any]) -> dict[str, Any]:
        """Build parameters for template source API.

        Maps asset fields to Broadstreet template parameters.

        Args:
            template_type: Template type (e.g., 'cube_3d', 'youtube_video')
            asset: Asset data dictionary

        Returns:
            Parameters for setAdvertisementSource API
        """
        params: dict[str, Any] = {}

        if template_type == "cube_3d":
            # 3D Cube needs 6 face images + optional captions
            # First try explicit face_image fields, then fall back to images array
            images_list = asset.get("images", [])
            faces = ["front", "back", "left", "right", "top", "bottom"]

            for i, face in enumerate(faces):
                image_key = f"{face}_image"
                # Use explicit field if provided
                if asset.get(image_key):
                    params[image_key] = asset[image_key]
                # Fall back to images array if available
                elif isinstance(images_list, list) and len(images_list) > i:
                    img = images_list[i]
                    params[image_key] = (
                        img if isinstance(img, str) else img.get("url") if isinstance(img, dict) else None
                    )

            # Optional captions
            for face in faces:
                caption_key = f"{face}_caption"
                if asset.get(caption_key):
                    params[caption_key] = asset[caption_key]

            # Optional settings
            if asset.get("click_url"):
                params["url"] = asset["click_url"]
            if asset.get("logo"):
                params["logo"] = asset["logo"]
            if asset.get("auto_rotate_ms"):
                params["timeout"] = asset["auto_rotate_ms"]

        elif template_type == "youtube_video":
            # YouTube video embed
            params["url"] = asset.get("youtube_url") or asset.get("video_url") or asset.get("media_url")
            if asset.get("headline"):
                params["headline"] = asset["headline"]
            if asset.get("body") or asset.get("description"):
                params["body"] = asset.get("body") or asset.get("description")
            if asset.get("autoplay"):
                params["autoplay"] = asset["autoplay"]

        elif template_type == "gallery":
            # Image gallery/slideshow
            images = asset.get("images", [])
            if isinstance(images, list):
                for i, img in enumerate(images):
                    if isinstance(img, str):
                        params[f"image_{i + 1}"] = img
                    elif isinstance(img, dict):
                        params[f"image_{i + 1}"] = img.get("url") or img.get("media_url")
            # Captions
            captions = asset.get("captions", [])
            if isinstance(captions, list):
                for i, caption in enumerate(captions):
                    if caption:
                        params[f"caption_{i + 1}"] = caption
            if asset.get("auto_rotate_ms"):
                params["timeout"] = asset["auto_rotate_ms"]

        elif template_type == "push_pin":
            # Push pin photo
            params["image"] = asset.get("image") or asset.get("media_url")
            if asset.get("caption"):
                params["caption"] = asset["caption"]
            if asset.get("pin_color"):
                params["color"] = asset["pin_color"]
            if asset.get("click_url"):
                params["url"] = asset["click_url"]

        elif template_type == "native":
            # Native ad
            params["headline"] = asset.get("headline")
            params["image"] = asset.get("image") or asset.get("media_url")
            if asset.get("body") or asset.get("description"):
                params["body"] = asset.get("body") or asset.get("description")
            if asset.get("sponsor"):
                params["sponsor"] = asset["sponsor"]
            if asset.get("cta_text"):
                params["cta"] = asset["cta_text"]
            if asset.get("click_url"):
                params["url"] = asset["click_url"]

        # Filter out None values
        return {k: v for k, v in params.items() if v is not None}

    def is_template_ad(self, asset: dict[str, Any]) -> tuple[bool, str | None]:
        """Check if asset should use a template-based ad.

        Args:
            asset: Asset data dictionary

        Returns:
            Tuple of (is_template, template_type)
        """
        # Check explicit template_type field
        template_type = asset.get("template_type")
        if template_type and template_type in BROADSTREET_TEMPLATES:
            return True, template_type

        # Auto-detect from content
        # 3D Cube: Has 6 face images
        face_images = ["front_image", "back_image", "left_image", "right_image", "top_image", "bottom_image"]
        if all(asset.get(f) for f in face_images):
            return True, "cube_3d"

        # YouTube: Has youtube_url or video_url with youtube in it
        video_url = asset.get("youtube_url") or asset.get("video_url") or ""
        if "youtube" in video_url.lower() or "youtu.be" in video_url.lower():
            return True, "youtube_video"

        # Gallery: Has 'images' list with multiple items
        images = asset.get("images", [])
        if isinstance(images, list) and len(images) > 1:
            return True, "gallery"

        return False, None

    def create_advertisement(
        self,
        media_buy_id: str,
        asset: dict[str, Any],
        template_type: str | None = None,
    ) -> AdvertisementInfo:
        """Create an advertisement in Broadstreet.

        Supports both basic ads (html/static/text) and template-based ads
        (3D Cube, YouTube, Gallery, etc.).

        For template-based ads, uses a two-step process:
        1. Create base HTML ad
        2. Set source with template type and assets

        Args:
            media_buy_id: Media buy ID for tracking
            asset: Asset data dictionary containing creative info
            template_type: Optional explicit template type (overrides auto-detection)

        Returns:
            AdvertisementInfo with created ad details
        """
        creative_id = asset.get("creative_id", f"creative_{id(asset)}")
        name = asset.get("name", f"Ad {creative_id}")

        # Check if this is a template-based ad
        is_template, detected_template = self.is_template_ad(asset)
        use_template = template_type or (detected_template if is_template else None)

        if use_template:
            return self._create_template_advertisement(
                media_buy_id=media_buy_id,
                asset=asset,
                template_type=use_template,
                creative_id=creative_id,
                name=name,
            )

        # Standard ad creation (html/static/text)
        ad_type = self._get_ad_type(asset)
        params = self._build_ad_params(asset, ad_type)

        self.log(f"Creating {ad_type} advertisement: {name}")

        if self.dry_run:
            self.log(f"  Creative ID: {creative_id}")
            self.log(f"  Type: {ad_type}")
            if ad_type == "html":
                html_len = len(params.get("html", ""))
                self.log(f"  HTML length: {html_len} chars")
            elif ad_type == "static":
                self.log(f"  Image URL: {params.get('image', 'N/A')}")
            elif ad_type == "text":
                text = params.get("default_text", "")[:50]
                self.log(f"  Text: {text}...")

            # Mock Broadstreet ID
            broadstreet_id = f"bs_ad_{creative_id}"
            status = "approved"
        else:
            if not self.client:
                raise RuntimeError("Client not available")

            try:
                result = self.client.create_advertisement(
                    advertiser_id=self.advertiser_id,
                    name=name,
                    ad_type=ad_type,
                    params=params,
                )
                broadstreet_id = str(result.get("id", result.get("Id", "")))
                status = "approved"
                self.log(f"Created advertisement {broadstreet_id}")
            except Exception as e:
                logger.error(f"Error creating advertisement: {e}", exc_info=True)
                self.log(f"Error creating advertisement: {e}")
                broadstreet_id = None
                status = "failed"

        # Track the advertisement
        info = AdvertisementInfo(
            creative_id=creative_id,
            broadstreet_id=broadstreet_id,
            name=name,
            ad_type=ad_type,
            status=status,
        )

        if media_buy_id not in self._ad_cache:
            self._ad_cache[media_buy_id] = {}
        self._ad_cache[media_buy_id][creative_id] = info

        return info

    def _create_template_advertisement(
        self,
        media_buy_id: str,
        asset: dict[str, Any],
        template_type: str,
        creative_id: str,
        name: str,
    ) -> AdvertisementInfo:
        """Create a template-based advertisement.

        Uses two-step process:
        1. Create base HTML ad
        2. Set source with template type and assets

        Args:
            media_buy_id: Media buy ID for tracking
            asset: Asset data dictionary
            template_type: Template type (e.g., 'cube_3d', 'youtube_video')
            creative_id: Creative ID
            name: Advertisement name

        Returns:
            AdvertisementInfo with created ad details
        """
        template_info = get_template_info(template_type)
        if not template_info:
            logger.warning(f"Unknown template type: {template_type}, falling back to standard ad")
            return self.create_advertisement(media_buy_id, asset, template_type=None)

        api_source_type = template_info.get("api_source_type", template_type)
        source_params = self._build_template_source_params(template_type, asset)

        self.log(f"Creating template advertisement: {name}")
        self.log(f"  Template: {template_info['name']} ({template_type})")
        self.log(f"  API Source Type: {api_source_type}")

        if self.dry_run:
            self.log(f"  Creative ID: {creative_id}")
            self.log(f"  Source params: {list(source_params.keys())}")

            # Mock Broadstreet ID
            broadstreet_id = f"bs_template_{creative_id}"
            status = "approved"
            ad_type = f"template:{template_type}"
        else:
            if not self.client:
                raise RuntimeError("Client not available")

            try:
                # Step 1: Create base HTML ad
                self.log("  Step 1: Creating base HTML ad...")
                result = self.client.create_advertisement(
                    advertiser_id=self.advertiser_id,
                    name=name,
                    ad_type="html",
                    params={},  # Empty - source will provide content
                )
                broadstreet_id = str(result.get("id", result.get("Id", "")))
                self.log(f"  Created base ad: {broadstreet_id}")

                # Step 2: Set source with template
                self.log(f"  Step 2: Setting source to {api_source_type}...")
                self.client.set_advertisement_source(
                    advertiser_id=self.advertiser_id,
                    advertisement_id=broadstreet_id,
                    source_type=api_source_type,
                    params=source_params,
                )
                self.log("  Template source set successfully")

                status = "approved"
                ad_type = f"template:{template_type}"

            except Exception as e:
                logger.error(f"Error creating template advertisement: {e}", exc_info=True)
                self.log(f"Error creating template advertisement: {e}")
                broadstreet_id = None
                status = "failed"
                ad_type = f"template:{template_type}"

        # Track the advertisement
        info = AdvertisementInfo(
            creative_id=creative_id,
            broadstreet_id=broadstreet_id,
            name=name,
            ad_type=ad_type,
            status=status,
        )

        if media_buy_id not in self._ad_cache:
            self._ad_cache[media_buy_id] = {}
        self._ad_cache[media_buy_id][creative_id] = info

        return info

    def create_advertisements(
        self,
        media_buy_id: str,
        assets: list[dict[str, Any]],
    ) -> list[AdvertisementInfo]:
        """Create multiple advertisements.

        Args:
            media_buy_id: Media buy ID
            assets: List of asset dictionaries

        Returns:
            List of AdvertisementInfo objects
        """
        results = []
        for asset in assets:
            info = self.create_advertisement(media_buy_id, asset)
            results.append(info)
        return results

    def get_advertisement(self, media_buy_id: str, creative_id: str) -> AdvertisementInfo | None:
        """Get advertisement info by creative ID.

        Args:
            media_buy_id: Media buy ID
            creative_id: Creative ID

        Returns:
            AdvertisementInfo if found, None otherwise
        """
        return self._ad_cache.get(media_buy_id, {}).get(creative_id)

    def get_all_advertisements(self, media_buy_id: str) -> list[AdvertisementInfo]:
        """Get all advertisements for a media buy.

        Args:
            media_buy_id: Media buy ID

        Returns:
            List of AdvertisementInfo objects
        """
        return list(self._ad_cache.get(media_buy_id, {}).values())

    def get_broadstreet_ids(self, media_buy_id: str) -> list[str]:
        """Get all Broadstreet advertisement IDs for a media buy.

        Args:
            media_buy_id: Media buy ID

        Returns:
            List of Broadstreet advertisement IDs
        """
        return [info.broadstreet_id for info in self.get_all_advertisements(media_buy_id) if info.broadstreet_id]

    def update_advertisement(
        self,
        media_buy_id: str,
        creative_id: str,
        updates: dict[str, Any],
    ) -> bool:
        """Update an advertisement.

        Args:
            media_buy_id: Media buy ID
            creative_id: Creative ID
            updates: Update parameters

        Returns:
            True if successful
        """
        info = self.get_advertisement(media_buy_id, creative_id)
        if not info:
            self.log(f"[yellow]Advertisement {creative_id} not found[/yellow]")
            return False

        if not info.broadstreet_id:
            self.log(f"[yellow]Advertisement {creative_id} has no Broadstreet ID[/yellow]")
            return False

        if self.dry_run:
            self.log(f"Would update advertisement {creative_id}: {updates}")
            return True

        if self.client:
            try:
                self.client.update_advertisement(
                    advertiser_id=self.advertiser_id,
                    advertisement_id=info.broadstreet_id,
                    params=updates,
                )
                self.log(f"Updated advertisement {creative_id}")
                return True
            except Exception as e:
                logger.error(f"Error updating advertisement {creative_id}: {e}", exc_info=True)
                self.log(f"Error updating advertisement: {e}")
                return False

        return False

    def delete_advertisement(self, media_buy_id: str, creative_id: str) -> bool:
        """Delete an advertisement.

        Args:
            media_buy_id: Media buy ID
            creative_id: Creative ID

        Returns:
            True if successful
        """
        info = self.get_advertisement(media_buy_id, creative_id)
        if not info:
            self.log(f"[yellow]Advertisement {creative_id} not found[/yellow]")
            return False

        if not info.broadstreet_id:
            self.log(f"[yellow]Advertisement {creative_id} has no Broadstreet ID[/yellow]")
            return False

        if self.dry_run:
            self.log(f"Would delete advertisement {creative_id}")
        else:
            if self.client:
                try:
                    self.client.delete_advertisement(
                        advertiser_id=self.advertiser_id,
                        advertisement_id=info.broadstreet_id,
                    )
                    self.log(f"Deleted advertisement {creative_id}")
                except Exception as e:
                    logger.error(f"Error deleting advertisement {creative_id}: {e}", exc_info=True)
                    self.log(f"Error deleting advertisement: {e}")
                    return False

        # Remove from cache
        if media_buy_id in self._ad_cache:
            self._ad_cache[media_buy_id].pop(creative_id, None)

        return True

    def get_delivery_report(
        self,
        media_buy_id: str,
        creative_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get delivery report for an advertisement.

        Args:
            media_buy_id: Media buy ID
            creative_id: Creative ID
            start_date: Report start date (ISO 8601)
            end_date: Report end date (ISO 8601)

        Returns:
            List of report records
        """
        info = self.get_advertisement(media_buy_id, creative_id)
        if not info or not info.broadstreet_id:
            return []

        if self.dry_run:
            # Return simulated data
            return [
                {
                    "date": start_date or "2024-01-01",
                    "impressions": 1000,
                    "clicks": 10,
                    "ctr": 1.0,
                }
            ]

        if self.client:
            try:
                return self.client.get_advertisement_report(
                    advertiser_id=self.advertiser_id,
                    advertisement_id=info.broadstreet_id,
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception as e:
                logger.error(f"Error getting delivery report for {creative_id}: {e}", exc_info=True)
                self.log(f"Error getting report: {e}")
                return []

        return []

    def validate_asset(self, asset: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate an asset can be created as an advertisement.

        Args:
            asset: Asset data dictionary

        Returns:
            Tuple of (is_valid, error_message)
        """
        ad_type = self._get_ad_type(asset)
        params = self._build_ad_params(asset, ad_type)

        if ad_type == "html" and not params.get("html"):
            return False, "HTML ad requires 'html' or 'snippet' content"

        if ad_type == "static" and not (params.get("image") or params.get("image_base64")):
            return False, "Static ad requires 'media_url', 'image_url', or 'image_base64'"

        if ad_type == "text" and not params.get("default_text"):
            return False, "Text ad requires 'default_text' or 'headline'"

        return True, None
