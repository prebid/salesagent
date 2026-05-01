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

    @validates("authorized_emails", "authorized_domains", "auto_approve_format_ids")
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
