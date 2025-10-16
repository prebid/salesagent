"""
JSON field validators for database models.

This module provides Pydantic models and SQLAlchemy validators
to ensure JSON fields contain valid, properly structured data.
"""

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import validates

# Pydantic models for JSON field validation


class CommentModel(BaseModel):
    """Model for a single comment in workflow_steps.comments."""

    user: str = Field(..., min_length=1, description="User who made the comment")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    text: str = Field(..., min_length=1, description="Comment text")

    @field_validator("user", "text")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class PlatformMappingModel(BaseModel):
    """Model for principal.platform_mappings."""

    google_ad_manager: dict[str, Any] | None = None
    kevel: dict[str, Any] | None = None
    mock: dict[str, Any] | None = None

    @model_validator(mode="after")
    def at_least_one_platform(self):
        if not any([self.google_ad_manager, self.kevel, self.mock]):
            raise ValueError("At least one platform mapping is required")
        return self


class CreativeFormatModel(BaseModel):
    """Model for product.formats array items."""

    format_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    description: str | None = Field(None, min_length=1)
    width: int | None = Field(None, gt=0)
    height: int | None = Field(None, gt=0)
    duration: int | None = Field(None, gt=0)
    assets: list[dict[str, Any]] = Field(default_factory=list)
    delivery_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate format type with flexible handling for legacy values."""
        if not v or not v.strip():
            raise ValueError("Format type cannot be empty")

        v = v.strip().lower()

        # Standard AdCP format types
        standard_types = {"display", "video", "audio", "native"}

        # Legacy/alternative format types that should be mapped to standard types
        type_mapping = {
            "banner": "display",
            "image": "display",
            "static": "display",
            "advertisement": "display",
            "rich_media": "display",
            "expandable": "display",
            "interstitial": "display",
            "popup": "display",
            "overlay": "display",
            "streaming": "video",
            "preroll": "video",
            "midroll": "video",
            "postroll": "video",
            "podcast": "audio",
            "radio": "audio",
            "sponsored": "native",
            "content": "native",
            "article": "native",
            "feed": "native",
        }

        # If it's already a standard type, return as-is
        if v in standard_types:
            return v

        # If it's a mappable legacy type, map it to standard type
        if v in type_mapping:
            return type_mapping[v]

        # If it's not recognized, default to 'display' but allow it
        # This prevents deletion failures for products with non-standard format types
        return "display"


class TargetingTemplateModel(BaseModel):
    """Model for product.targeting_template."""

    geo_targets: list[str] | None = None
    device_targets: list[str] | None = None
    audience_segments: list[str] | None = None
    content_categories: list[str] | None = None
    custom_parameters: dict[str, Any] | None = None


class PolicySettingsModel(BaseModel):
    """Model for tenant.policy_settings."""

    enabled: bool = Field(default=False)
    require_approval: bool = Field(default=False)
    max_daily_budget: float | None = Field(None, gt=0)
    blocked_categories: list[str] = Field(default_factory=list)
    allowed_advertisers: list[str] = Field(default_factory=list)
    custom_rules: dict[str, Any] = Field(default_factory=dict)


class DeliveryDataModel(BaseModel):
    """Model for gam_line_items.delivery_data."""

    impressions: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    ctr: float = Field(default=0.0, ge=0.0, le=100.0)
    spend: float = Field(default=0.0, ge=0.0)
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC))


# SQLAlchemy validator mixins


class JSONValidatorMixin:
    """Mixin to add JSON validation to SQLAlchemy models."""

    @validates("authorized_emails", "authorized_domains", "auto_approve_formats")
    def validate_json_array_fields(self, key, value):
        """Validate that these fields are JSON arrays."""
        return ensure_json_array(value, default=[])

    @validates("request_data", "response_data", "transaction_details")
    def validate_json_object_fields(self, key, value):
        """Validate that these fields are JSON objects or None."""
        if value is None:
            return None  # Allow NULL for these fields
        if isinstance(value, str):
            if value == "null":
                return None  # Convert string 'null' to actual None
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise ValueError(f"{key} must be valid JSON")
        if not isinstance(value, dict):
            # If it's not a dict and not None, make it an empty dict
            return {}
        return value

    @validates("comments")
    def validate_comments(self, key, value):
        """Validate comments field is a list of proper comment objects."""
        if value is None:
            return []

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise ValueError(f"{key} must be valid JSON")

        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list")

        validated_comments = []
        for comment in value:
            if isinstance(comment, dict):
                # Validate and normalize using Pydantic
                validated = CommentModel(**comment)
                validated_comments.append(validated.model_dump(mode="json"))
            else:
                raise ValueError("Each comment must be a dictionary")

        return validated_comments

    @validates("platform_mappings")
    def validate_platform_mappings(self, key, value):
        """Validate platform_mappings contains at least one platform."""
        if value is None:
            raise ValueError(f"{key} cannot be None")

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise ValueError(f"{key} must be valid JSON")

        if not isinstance(value, dict):
            raise ValueError(f"{key} must be a dictionary")

        # Validate using Pydantic
        validated = PlatformMappingModel(**value)
        return validated.model_dump(mode="json", exclude_none=True)

    @validates("formats")
    def validate_formats(self, key, value):
        """Validate formats field is a list of format IDs (strings)."""
        if value is None:
            return []

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                # For deletion operations, don't fail on invalid JSON - just return as-is
                # SQLAlchemy may trigger validators even during deletion
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(f"Invalid JSON in formats field during validation: {value[:100]}")
                return []

        if not isinstance(value, list):
            # During deletion, be lenient with validation
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Expected list for formats field, got {type(value).__name__}")
            return []

        validated_formats = []
        for fmt in value:
            try:
                if isinstance(fmt, dict):
                    # Legacy: Extract format_id from Format object for backward compatibility
                    format_id = fmt.get("format_id", "unknown")
                    if not format_id or format_id == "unknown":
                        # Skip invalid format objects instead of failing
                        import logging

                        logger = logging.getLogger(__name__)
                        logger.warning(f"Skipping format object without valid format_id: {fmt}")
                        continue
                    validated_formats.append(format_id)
                elif isinstance(fmt, str):
                    # Current approach: Store format IDs as strings
                    if not fmt.strip():
                        # Skip empty format IDs instead of failing
                        continue
                    validated_formats.append(fmt.strip())
                else:
                    # Skip unrecognized format types instead of failing
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.warning(f"Skipping unrecognized format type: {type(fmt).__name__}")
                    continue
            except Exception as e:
                # Be extra defensive - don't let validation errors block deletion
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(f"Error validating individual format: {e}")
                continue

        return validated_formats

    @validates("targeting_template")
    def validate_targeting_template(self, key, value):
        """Validate targeting_template field structure."""
        if value is None:
            return {}

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise ValueError(f"{key} must be valid JSON")

        if not isinstance(value, dict):
            raise ValueError(f"{key} must be a dictionary")

        # Validate using Pydantic
        validated = TargetingTemplateModel(**value)
        return validated.model_dump(mode="json", exclude_none=True)

    @validates("policy_settings")
    def validate_policy_settings(self, key, value):
        """Validate policy_settings field structure."""
        if value is None:
            return {}

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise ValueError(f"{key} must be valid JSON")

        if not isinstance(value, dict):
            raise ValueError(f"{key} must be a dictionary")

        # Validate using Pydantic
        validated = PolicySettingsModel(**value)
        return validated.model_dump(mode="json")

    @validates("delivery_data")
    def validate_delivery_data(self, key, value):
        """Validate delivery_data field structure."""
        if value is None:
            return None

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise ValueError(f"{key} must be valid JSON")

        if not isinstance(value, dict):
            raise ValueError(f"{key} must be a dictionary")

        # Validate using Pydantic
        validated = DeliveryDataModel(**value)
        return validated.model_dump(mode="json")


# Utility functions for JSON handling


def ensure_json_array(value: str | list | None, default: list | None = None) -> list:
    """
    Ensure a value is a JSON array (list).

    Args:
        value: The value to check/convert
        default: Default value if input is None

    Returns:
        A list
    """
    if value is None:
        return default or []

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON string")

    if not isinstance(value, list):
        raise ValueError("Value must be a list")

    return value


def ensure_json_object(value: str | dict | None, default: dict | None = None) -> dict:
    """
    Ensure a value is a JSON object (dict).

    Args:
        value: The value to check/convert
        default: Default value if input is None

    Returns:
        A dictionary
    """
    if value is None:
        return default or {}

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON string")

    if not isinstance(value, dict):
        raise ValueError("Value must be a dictionary")

    return value


def validate_json_schema(value: Any, schema: type[BaseModel]) -> dict:
    """
    Validate a value against a Pydantic schema.

    Args:
        value: The value to validate
        schema: The Pydantic model class to validate against

    Returns:
        The validated and normalized dictionary
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON string")

    validated = schema(**value)
    return validated.model_dump(mode="json")
