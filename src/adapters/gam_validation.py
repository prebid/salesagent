"""
GAM-specific creative validation utilities.

This module provides validation functions to check creative assets against
Google Ad Manager's size limits, content policies, and technical requirements
before submitting to the GAM API.
"""

import re
from typing import Any
from urllib.parse import urlparse


class GAMValidationError(Exception):
    """Exception raised when creative fails GAM validation."""

    pass


class GAMValidator:
    """Validator for GAM creative assets and content."""

    # GAM creative size limits (in bytes)
    # Note: HTML5 creative file size limits are not enforced here - GAM API handles validation
    MAX_FILE_SIZES = {
        "display": 150_000,  # 150KB for display creatives
        "video": 2_200_000,  # 2.2MB for video creatives
        "rich_media": 2_200_000,  # 2.2MB for rich media
        "native": 150_000,  # 150KB for native creatives
        # "html5": removed - let GAM API handle file size validation
    }

    # GAM maximum creative dimensions
    MAX_DIMENSIONS = {
        "width": 1800,
        "height": 1500,
    }

    # Allowed file extensions by creative type
    ALLOWED_EXTENSIONS = {
        "display": [".jpg", ".jpeg", ".png", ".gif", ".webp"],
        "video": [".mp4", ".webm", ".mov", ".avi"],
        "rich_media": [".swf", ".html", ".zip"],
        "html5": [".html", ".htm", ".html5", ".zip"],
        "native": [".jpg", ".jpeg", ".png", ".gif", ".webp"],
    }

    # Prohibited JavaScript functions in third-party tags
    PROHIBITED_JS_FUNCTIONS = [
        "eval(",
        "document.write(",
        "document.writeln(",
        "innerHTML =",
        "outerHTML =",
        "setTimeout(",
        "setInterval(",
        "Function(",
        "new Function(",
    ]

    # Required HTTPS patterns
    HTTPS_PATTERNS = [
        r"^https://",  # URLs must start with https://
    ]

    def validate_creative_size(
        self, width: int | None, height: int | None, file_size: int | None = None, creative_type: str = "display"
    ) -> list[str]:
        """
        Validate creative dimensions and file size against GAM limits.

        Note: HTML5 creatives skip file size validation as Google's actual limits
        are not clearly documented. GAM API will handle file size validation.

        Args:
            width: Creative width in pixels
            height: Creative height in pixels
            file_size: File size in bytes
            creative_type: Type of creative ("display", "video", "rich_media", "native", "html5")

        Returns:
            List of validation error messages (empty if valid)
        """
        issues = []

        # Validate dimensions (applies to all creative types)
        if width and width > self.MAX_DIMENSIONS["width"]:
            issues.append(f"Creative width {width}px exceeds GAM maximum of {self.MAX_DIMENSIONS['width']}px")

        if height and height > self.MAX_DIMENSIONS["height"]:
            issues.append(f"Creative height {height}px exceeds GAM maximum of {self.MAX_DIMENSIONS['height']}px")

        # Validate file size (skip for HTML5 - let GAM API handle it)
        if file_size and creative_type != "html5" and creative_type in self.MAX_FILE_SIZES:
            max_size = self.MAX_FILE_SIZES[creative_type]
            if file_size > max_size:
                issues.append(f"File size {file_size:,} bytes exceeds GAM {creative_type} limit of {max_size:,} bytes")

        return issues

    def validate_media_data(self, asset: dict[str, Any]) -> list[str]:
        """
        Validate media_data field for binary asset upload.

        Args:
            asset: Creative asset dictionary

        Returns:
            List of validation error messages (empty if valid)
        """
        issues = []

        media_data = asset.get("media_data")
        if media_data is None:
            return issues  # media_data is optional

        # Validate data type
        if isinstance(media_data, str):
            # Should be valid base64
            import base64

            try:
                decoded = base64.b64decode(media_data, validate=True)
                if len(decoded) == 0:
                    issues.append("media_data cannot be empty when provided")
            except Exception:
                issues.append("media_data must be valid base64 when provided as string")
        elif isinstance(media_data, bytes):
            if len(media_data) == 0:
                issues.append("media_data cannot be empty when provided")
        else:
            issues.append("media_data must be bytes or base64-encoded string")

        # Validate file format based on creative type and filename
        if media_data and not issues:
            creative_type = self._get_creative_type_from_asset(asset)
            filename = asset.get("filename", "").lower()

            if creative_type in self.ALLOWED_EXTENSIONS:
                allowed_exts = self.ALLOWED_EXTENSIONS[creative_type]
                if filename:
                    file_ext = "." + filename.split(".")[-1] if "." in filename else ""
                    if file_ext and file_ext not in allowed_exts:
                        issues.append(
                            f"File extension {file_ext} not allowed for {creative_type} creatives. Allowed: {', '.join(allowed_exts)}"
                        )

        return issues

    def validate_content_policy(self, asset: dict[str, Any]) -> list[str]:
        """
        Validate creative content against GAM content policies.

        Args:
            asset: Creative asset dictionary

        Returns:
            List of validation error messages (empty if valid)
        """
        issues = []

        # Validate HTTPS requirement for URLs
        for url_field in ["url", "media_url", "click_url"]:
            url = asset.get(url_field)
            if url and not url.startswith("https://"):
                issues.append(f"{url_field} must use HTTPS: {url}")

        # Validate third-party snippet content
        snippet = asset.get("snippet")
        if snippet:
            issues.extend(self._validate_snippet_content(snippet))

        # Validate file extensions
        media_url = asset.get("media_url") or asset.get("url")
        if media_url:
            issues.extend(self._validate_file_extension(media_url, asset.get("format", "display")))

        return issues

    def validate_technical_requirements(self, asset: dict[str, Any]) -> list[str]:
        """
        Validate technical requirements for GAM creatives.

        Args:
            asset: Creative asset dictionary

        Returns:
            List of validation error messages (empty if valid)
        """
        issues = []

        # Validate snippet and snippet_type fields specifically
        snippet = asset.get("snippet")
        snippet_type = asset.get("snippet_type")

        # Handle VAST-specific validation first (since it has special rules)
        if snippet_type in ["vast_xml", "vast_url"]:
            if not snippet and not asset.get("url"):
                issues.append("VAST creative requires either 'snippet' or 'url' field")
            elif snippet_type and snippet_type not in ["html", "javascript", "vast_xml", "vast_url"]:
                issues.append(f"Invalid snippet_type: {snippet_type}")

        # If either snippet or snippet_type is present (but not VAST), validate both are present and valid
        elif snippet or snippet_type:
            if snippet and not snippet_type:
                issues.append("Third-party tag creative requires 'snippet_type' field")
            elif snippet_type and not snippet:
                issues.append("Third-party tag creative requires 'snippet' field")
            elif snippet_type and snippet_type not in ["html", "javascript", "vast_xml", "vast_url"]:
                issues.append(f"Invalid snippet_type: {snippet_type}")

        # Get creative type for other validations
        creative_type = self._get_creative_type_from_asset(asset)

        if creative_type == "native":
            if not asset.get("template_variables"):
                issues.append("Native creative requires 'template_variables' field")

        # Validate aspect ratios for video creatives
        if creative_type in ["vast", "video"]:
            width = asset.get("width")
            height = asset.get("height")
            if width and height:
                aspect_ratio = width / height
                valid_ratios = [16 / 9, 4 / 3, 1 / 1, 9 / 16]  # Common video aspect ratios
                if not any(abs(aspect_ratio - ratio) < 0.01 for ratio in valid_ratios):
                    issues.append(
                        f"Video aspect ratio {aspect_ratio:.2f} is not standard. "
                        f"Recommended: 16:9, 4:3, 1:1, or 9:16"
                    )

        return issues

    def validate_creative_asset(self, asset: dict[str, Any]) -> list[str]:
        """
        Comprehensive validation of a creative asset for GAM compliance.

        Args:
            asset: Creative asset dictionary

        Returns:
            List of validation error messages (empty if valid)
        """
        all_issues = []

        # Size validation
        width = asset.get("width")
        height = asset.get("height")
        file_size = asset.get("file_size")

        # Calculate file size from media_data if available
        if not file_size and asset.get("media_data"):
            media_data = asset["media_data"]
            if isinstance(media_data, str):
                # Base64 encoded - estimate size (base64 is ~33% larger than binary)
                file_size = int(len(media_data) * 0.75)
            elif isinstance(media_data, bytes):
                file_size = len(media_data)

        creative_type = self._get_creative_type_from_asset(asset)

        all_issues.extend(self.validate_creative_size(width, height, file_size, creative_type))

        # Media data validation
        all_issues.extend(self.validate_media_data(asset))

        # Content policy validation
        all_issues.extend(self.validate_content_policy(asset))

        # Technical requirements validation
        all_issues.extend(self.validate_technical_requirements(asset))

        return all_issues

    def _validate_snippet_content(self, snippet: str) -> list[str]:
        """Validate JavaScript/HTML snippet content for security issues."""
        issues = []

        # Check for prohibited JavaScript functions
        for prohibited_func in self.PROHIBITED_JS_FUNCTIONS:
            if prohibited_func in snippet:
                issues.append(f"Prohibited JavaScript function detected: {prohibited_func.strip('(')}")

        # Check for potentially unsafe patterns
        if "javascript:" in snippet.lower():
            issues.append("javascript: protocol URLs are not allowed")

        if "data:" in snippet.lower() and "script" in snippet.lower():
            issues.append("Data URLs with script content are not allowed")

        # Check for external script sources (should be HTTPS)
        script_src_pattern = r'src\s*=\s*["\']([^"\']+)["\']'
        for match in re.finditer(script_src_pattern, snippet, re.IGNORECASE):
            src_url = match.group(1)
            if src_url.startswith("http://"):
                issues.append(f"Script source must use HTTPS: {src_url}")

        return issues

    def _validate_file_extension(self, url: str, format_type: str) -> list[str]:
        """Validate file extension against allowed extensions for the format."""
        issues = []

        parsed_url = urlparse(url)
        file_path = parsed_url.path.lower()

        # Skip validation for non-file URLs (e.g., API endpoints)
        if not any(file_path.endswith(ext) for ext_list in self.ALLOWED_EXTENSIONS.values() for ext in ext_list):
            return issues  # Assume it's a valid API endpoint or will be validated elsewhere

        # Determine creative type from format
        creative_type = "display"  # default
        if any(file_path.endswith(ext) for ext in self.ALLOWED_EXTENSIONS["html5"]):
            creative_type = "html5"
        elif any(file_path.endswith(ext) for ext in self.ALLOWED_EXTENSIONS["video"]):
            creative_type = "video"
        elif format_type and "video" in format_type.lower():
            creative_type = "video"
        elif format_type and ("html5" in format_type.lower() or "rich_media" in format_type.lower()):
            creative_type = "html5"

        allowed_extensions = self.ALLOWED_EXTENSIONS.get(creative_type, [])
        if not any(file_path.endswith(ext) for ext in allowed_extensions):
            issues.append(
                f"File extension not allowed for {creative_type} creatives. "
                f"Allowed: {', '.join(allowed_extensions)}"
            )

        return issues

    def _get_creative_type_from_asset(self, asset: dict[str, Any]) -> str:
        """Determine creative type from asset properties."""
        if asset.get("snippet") and asset.get("snippet_type"):
            snippet_type = asset["snippet_type"]
            if snippet_type in ["vast_xml", "vast_url"]:
                return "vast"
            else:
                return "third_party_tag"
        elif asset.get("template_variables"):
            return "native"
        elif asset.get("media_url") or asset.get("url"):
            # Determine type based on URL or format
            url = asset.get("media_url") or asset.get("url")
            format_type = asset.get("format", "")

            # Check for HTML5 first
            if (
                any(url.lower().endswith(ext) for ext in self.ALLOWED_EXTENSIONS["html5"])
                or "html5" in format_type.lower()
                or "rich_media" in format_type.lower()
            ):
                return "html5"
            elif any(url.lower().endswith(ext) for ext in self.ALLOWED_EXTENSIONS["video"]):
                return "video"
            elif "video" in format_type.lower():
                return "video"
            else:
                return "display"
        else:
            return "display"


# Convenience function for easy import
def validate_gam_creative(asset: dict[str, Any]) -> list[str]:
    """
    Convenience function to validate a creative asset against GAM requirements.

    Args:
        asset: Creative asset dictionary

    Returns:
        List of validation error messages (empty if valid)

    Raises:
        GAMValidationError: If validation fails with critical errors
    """
    validator = GAMValidator()
    issues = validator.validate_creative_asset(asset)

    if issues:
        # For now, just return issues. Callers can decide whether to raise exceptions
        pass

    return issues
