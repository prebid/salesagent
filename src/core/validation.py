"""Server-side validation utilities for form inputs."""

import json
import logging
import re
from typing import Any

from src.core.signing import reject_malformed_target

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when validation fails."""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def validate_form_data(data: dict[str, Any], validators: dict[str, list] | list[str]) -> tuple[bool, list[str]]:
    """
    Validate form data using specified validators.

    Args:
        data: Form data dictionary
        validators: Either a dictionary mapping field names to list of validator functions,
                   or a list of required field names for simple presence validation

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors: list[str] = []

    # Handle simple required field validation when passed a list
    if isinstance(validators, list):
        for field in validators:
            if not data.get(field, "").strip():
                errors.append(f"{field.title()} is required")
        return (len(errors) == 0, errors)

    # Handle dictionary of validators
    for field, field_validators in validators.items():
        value = data.get(field, "")

        for validator in field_validators:
            if not callable(validator):
                continue

            error = validator(value)
            if error:
                errors.append(f"{field.title()}: {error}")
                break  # Stop on first error for this field

    return (len(errors) == 0, errors)


def sanitize_json(json_str: str) -> str:
    """Sanitize and format JSON string."""
    try:
        # Parse and re-serialize to ensure valid JSON
        parsed = json.loads(json_str)
        return json.dumps(parsed, indent=2)
    except json.JSONDecodeError:
        return json_str  # Return as-is if not valid JSON


def sanitize_url(url: str) -> str:
    """Sanitize URL by ensuring proper format."""
    if not url:
        return url

    # Ensure URL has a scheme
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Remove trailing slashes
    return url.rstrip("/")


def normalize_agent_url(url: str) -> str:
    """Normalize agent URL to base form for consistent comparison.

    Strips common path suffixes that users might include:
    - /mcp
    - /a2a
    - /.well-known/adcp/sales
    - Trailing slashes

    This ensures all variations of an agent URL normalize to the same base URL:
        "https://creative.adcontextprotocol.org/" -> "https://creative.adcontextprotocol.org"
        "https://creative.adcontextprotocol.org/mcp" -> "https://creative.adcontextprotocol.org"
        "https://creative.adcontextprotocol.org/a2a" -> "https://creative.adcontextprotocol.org"
        "https://publisher.com/.well-known/adcp/sales" -> "https://publisher.com"

    Args:
        url: Agent URL to normalize

    Returns:
        Normalized base URL

    Raises:
        TargetUriMalformedError: If the URL's authority is malformed per the
            RFC 9421 signing layer's canonicalization gate (src.core.signing_contract.canonical)
            -- e.g. empty authority, unterminated/zone-id IPv6, raw non-ASCII host,
            port-but-no-host. Shares the predicate with the signing verifier so this
            validation-layer path can never accept a URL the verifier would reject
            (#1291).
    """
    if not url:
        return url

    reject_malformed_target(url)

    # First, remove trailing slashes
    normalized = url.rstrip("/")

    # Common path suffixes to strip (order matters - longest first)
    suffixes_to_strip = [
        "/.well-known/adcp/sales",
        "/mcp",
        "/a2a",
    ]

    # Strip each suffix (check multiple times in case of multiple trailing slashes)
    for suffix in suffixes_to_strip:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            # Remove any trailing slashes that remain
            normalized = normalized.rstrip("/")
            break  # Only strip one suffix

    return normalized


def sanitize_form_data(data: dict[str, Any]) -> dict[str, Any]:
    """Sanitize form data before saving."""
    sanitized = {}

    for key, value in data.items():
        if isinstance(value, str):
            # Trim whitespace
            value = value.strip()

            # Sanitize specific field types
            if "url" in key.lower():
                value = sanitize_url(value)
            elif key == "config" or "json" in key.lower():
                value = sanitize_json(value)

        sanitized[key] = value

    return sanitized


# GAM-specific validation functions
def validate_gam_network_code(network_code: str) -> str | None:
    """Validate GAM network code format."""
    if not network_code:
        return None  # Optional field

    # Network codes should be numeric and reasonable length
    if not re.match(r"^\d{1,20}$", network_code):
        return "Network code must be numeric and up to 20 digits"

    return None


def validate_gam_trafficker_id(trafficker_id: str) -> str | None:
    """Validate GAM trafficker ID format."""
    if not trafficker_id:
        return None  # Optional field

    # Trafficker IDs should be numeric and reasonable length
    if not re.match(r"^\d{1,20}$", trafficker_id):
        return "Trafficker ID must be numeric and up to 20 digits"

    return None


def validate_gam_refresh_token(refresh_token: str) -> str | None:
    """Validate GAM refresh token format and length."""
    if not refresh_token:
        return "Refresh token is required"

    # Basic length validation (refresh tokens are typically long)
    if len(refresh_token) < 20:
        return "Refresh token appears to be invalid (too short)"

    if len(refresh_token) > 1000:
        return "Refresh token is too long (max 1000 characters)"

    # Check for common invalid patterns
    if refresh_token.startswith("Bearer "):
        return "Do not include 'Bearer ' prefix in refresh token"

    return None


def validate_gam_config(data: dict[str, Any]) -> dict[str, str | None]:
    """Validate all GAM configuration fields."""
    errors: dict[str, str | None] = {}

    # Validate network code
    if "network_code" in data:
        error = validate_gam_network_code(str(data["network_code"]))
        if error:
            errors["network_code"] = error

    # Validate trafficker ID
    if "trafficker_id" in data:
        error = validate_gam_trafficker_id(str(data["trafficker_id"]))
        if error:
            errors["trafficker_id"] = error

    # Validate refresh token
    if "refresh_token" in data:
        error = validate_gam_refresh_token(data["refresh_token"])
        if error:
            errors["refresh_token"] = error

    return errors
