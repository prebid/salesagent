"""Server-side validation utilities for form inputs."""

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when validation fails."""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class FormValidator:
    """Form validation utility class."""

    @staticmethod
    def validate_email(email: str) -> str | None:
        """Validate email address format."""
        if not email:
            return "Email is required"

        email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_regex, email):
            return "Invalid email address format"

        return None

    @staticmethod
    def validate_url(url: str, required: bool = False) -> str | None:
        """Validate URL format."""
        if not url and required:
            return "URL is required"

        if not url:
            return None

        try:
            result = urlparse(url)
            if not all([result.scheme, result.netloc]):
                return "Invalid URL format"

            if result.scheme not in ["http", "https"]:
                return "URL must use http or https protocol"

            return None
        except Exception as e:
            logger.debug(f"URL validation error: {e}")
            return "Invalid URL format"

    @staticmethod
    def validate_webhook_url(url: str) -> str | None:
        """Validate webhook URL (more specific than general URL)."""
        if not url:
            return None  # Webhooks are optional

        # First do general URL validation
        url_error = FormValidator.validate_url(url)
        if url_error:
            return url_error

        # Check for known webhook patterns
        if "hooks.slack.com/services/" in url:
            # Validate Slack webhook format
            parts = url.split("/")
            if len(parts) < 7:
                return "Invalid Slack webhook URL format"

        return None

    @staticmethod
    def validate_json(json_str: str, required: bool = True) -> str | None:
        """Validate JSON string."""
        if not json_str and required:
            return "JSON configuration is required"

        if not json_str:
            return None

        try:
            json.loads(json_str)
            return None
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {str(e)}"

    @staticmethod
    def validate_principal_id(principal_id: str) -> str | None:
        """Validate principal ID format."""
        if not principal_id:
            return "Principal ID is required"

        if not re.match(r"^[a-zA-Z0-9_-]+$", principal_id):
            return "Principal ID can only contain letters, numbers, underscores, and hyphens"

        if len(principal_id) < 3:
            return "Principal ID must be at least 3 characters long"

        if len(principal_id) > 50:
            return "Principal ID must be less than 50 characters"

        return None

    @staticmethod
    def validate_network_id(network_id: str) -> str | None:
        """Validate network ID (should be numeric)."""
        if not network_id:
            return "Network ID is required"

        if not network_id.isdigit():
            return "Network ID must be numeric"

        return None

    @staticmethod
    def validate_required(value: str, field_name: str = "Field") -> str | None:
        """Validate that a field is not empty."""
        if not value or not value.strip():
            return f"{field_name} is required"
        return None

    @staticmethod
    def validate_length(
        value: str, min_length: int | None = None, max_length: int | None = None, field_name: str = "Field"
    ) -> str | None:
        """Validate string length."""
        if not value:
            return None

        if min_length and len(value) < min_length:
            return f"{field_name} must be at least {min_length} characters"

        if max_length and len(value) > max_length:
            return f"{field_name} must be less than {max_length} characters"

        return None

    @staticmethod
    def validate_tenant_name(name: str) -> str | None:
        """Validate tenant name."""
        if error := FormValidator.validate_required(name, "Tenant name"):
            return error

        if error := FormValidator.validate_length(name, min_length=3, max_length=100, field_name="Tenant name"):
            return error

        return None

    @staticmethod
    def validate_subdomain(subdomain: str) -> str | None:
        """Validate subdomain format."""
        if not subdomain:
            return "Subdomain is required"

        # Allow localhost for development
        if subdomain == "localhost":
            return None

        if not re.match(r"^[a-z0-9-]+$", subdomain):
            return "Subdomain can only contain lowercase letters, numbers, and hyphens"

        if subdomain.startswith("-") or subdomain.endswith("-"):
            return "Subdomain cannot start or end with a hyphen"

        if len(subdomain) < 3:
            return "Subdomain must be at least 3 characters long"

        if len(subdomain) > 63:
            return "Subdomain must be less than 63 characters"

        return None

    @staticmethod
    def validate_role(role: str) -> str | None:
        """Validate user role."""
        valid_roles = ["admin", "manager", "viewer"]
        if role not in valid_roles:
            return f"Invalid role. Must be one of: {', '.join(valid_roles)}"
        return None


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

    Note: input normalization only (no host-case/port canonicalization) — for
    IDENTITY comparison use ``canonical_agent_url``/``format_id_identity``
    (src.core.schemas), which apply the same suffix stripping on top of the
    spec canonical form.
    """
    from src.core.schemas._base import strip_transport_suffixes

    return strip_transport_suffixes(url)


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
