"""Comprehensive A2A response validation framework.

This module provides utilities for validating A2A responses to catch
AttributeError bugs, missing fields, and protocol violations.

Usage:
    from tests.helpers.a2a_response_validator import A2AResponseValidator

    validator = A2AResponseValidator()
    validator.validate_skill_response(response_dict, "create_media_buy")
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class ValidationResult:
    """Result of a validation check."""

    is_valid: bool
    errors: list[str]
    warnings: list[str]

    def __bool__(self) -> bool:
        """Allow boolean checks: if validation_result: ..."""
        return self.is_valid


class A2AResponseValidator:
    """Validator for A2A skill responses.

    This validator ensures that A2A responses:
    1. Have all required fields
    2. Message fields are properly formatted
    3. No AttributeErrors occurred during construction
    4. Conform to A2A protocol expectations
    """

    # Required fields for all A2A skill responses
    REQUIRED_FIELDS = {"success", "message"}

    # Optional common fields (not required but expected in many responses)
    COMMON_OPTIONAL_FIELDS = {
        "status",
        "data",
        "error",
        "warning",
        "metadata",
    }

    # Skill-specific required fields (domain fields only - protocol fields added by handlers)
    SKILL_REQUIRED_FIELDS = {
        "create_media_buy": set(),  # media_buy_id is optional per schema, removed as required
        "sync_creatives": {"creatives"},  # Removed 'status' (protocol), 'results' -> 'creatives' per spec
        "get_products": {"products"},
        "list_creatives": {"creatives", "query_summary", "pagination"},  # Per AdCP spec structure
        "list_creative_formats": {"formats"},  # Removed 'total_count' (not in spec)
        # Note: get_signals removed — signals is dead code (UC-008)
    }

    def validate_skill_response(self, response: dict[str, Any], skill_name: str | None = None) -> ValidationResult:
        """Validate an A2A skill response.

        Args:
            response: The response dict returned by _handle_*_skill method
            skill_name: Name of the skill (e.g., "create_media_buy")

        Returns:
            ValidationResult with errors and warnings
        """
        errors = []
        warnings = []

        # 1. Check required common fields
        for field in self.REQUIRED_FIELDS:
            if field not in response:
                errors.append(f"Missing required field: {field}")

        # 2. Validate message field specifically (this catches AttributeError bugs)
        if "message" in response:
            if not isinstance(response["message"], str):
                errors.append(f"Field 'message' must be a string, got {type(response['message']).__name__}")
            elif len(response["message"]) == 0:
                warnings.append("Field 'message' is empty")
        else:
            # Message is required but missing
            errors.append("Missing required field: message")

        # 3. Check skill-specific required fields
        if skill_name and skill_name in self.SKILL_REQUIRED_FIELDS:
            required = self.SKILL_REQUIRED_FIELDS[skill_name]
            for field in required:
                if field not in response:
                    errors.append(f"Missing required field for {skill_name}: {field}")

        # 4. Validate success field
        if "success" in response:
            if not isinstance(response["success"], bool):
                errors.append(f"Field 'success' must be a boolean, got {type(response['success']).__name__}")

        # 5. Check for error information if success=False
        if response.get("success") is False:
            if "error" not in response and "message" not in response:
                warnings.append("Response indicates failure but has no error or message field")

        return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings)

    def validate_all_skills_have_message(self, handler: Any) -> ValidationResult:
        """Check that all skill handler methods properly return message fields.

        This is a meta-validation: we're checking that the handler has all the
        necessary skill methods and that they're structured correctly.

        Args:
            handler: AdCPRequestHandler instance

        Returns:
            ValidationResult
        """
        errors = []
        warnings = []

        # List of skill handler methods that should exist
        expected_methods = [
            "_handle_create_media_buy_skill",
            "_handle_sync_creatives_skill",
            "_handle_get_products_skill",
            "_handle_list_creatives_skill",
            "_handle_list_creative_formats_skill",
            # Note: _handle_get_signals_skill removed — signals is dead code (UC-008)
        ]

        for method_name in expected_methods:
            if not hasattr(handler, method_name):
                errors.append(f"Handler missing skill method: {method_name}")
            elif not callable(getattr(handler, method_name)):
                errors.append(f"Handler method not callable: {method_name}")

        return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings)

    def check_response_type_safety(self, response_class: type) -> ValidationResult:
        """Check if a response type can be safely used in A2A.

        A response type is safe if it has either:
        - A __str__ method (for generating human-readable messages)
        - A .message field

        Args:
            response_class: Response class to check (e.g., CreateMediaBuyResponse)

        Returns:
            ValidationResult
        """
        errors = []
        warnings = []

        class_name = response_class.__name__

        # Check for __str__ method
        has_str_method = hasattr(response_class, "__str__") and response_class.__str__ is not object.__str__

        # Check for message field
        has_message_field = False
        if hasattr(response_class, "model_fields"):
            has_message_field = "message" in response_class.model_fields

        if not has_str_method and not has_message_field:
            errors.append(
                f"{class_name} has neither __str__() method nor .message field. "
                f"Cannot safely extract human-readable messages for A2A responses."
            )
        elif not has_str_method:
            warnings.append(
                f"{class_name} only has .message field, no __str__() method. Consider adding __str__() for consistency."
            )

        return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings)

    def validate_artifact_structure(self, artifact: Any) -> ValidationResult:
        """Validate an A2A artifact structure.

        Args:
            artifact: Artifact object from A2A response

        Returns:
            ValidationResult
        """
        errors = []
        warnings = []

        # Check required artifact fields
        if not hasattr(artifact, "artifactId"):
            errors.append("Artifact missing required field: artifactId")
        if not hasattr(artifact, "name"):
            errors.append("Artifact missing required field: name")
        if not hasattr(artifact, "parts"):
            errors.append("Artifact missing required field: parts")

        # Check parts structure
        if hasattr(artifact, "parts"):
            if not isinstance(artifact.parts, list):
                errors.append(f"Artifact parts must be a list, got {type(artifact.parts).__name__}")
            elif len(artifact.parts) == 0:
                warnings.append("Artifact has no parts")
            else:
                for i, part in enumerate(artifact.parts):
                    if not hasattr(part, "type"):
                        errors.append(f"Artifact part {i} missing type field")
                    if not hasattr(part, "data"):
                        errors.append(f"Artifact part {i} missing data field")

        return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings)


# Global validator instance for convenience
validator = A2AResponseValidator()


def validate_skill_response(response: dict[str, Any], skill_name: str | None = None) -> ValidationResult:
    """Convenience function for validating skill responses."""
    return validator.validate_skill_response(response, skill_name)


def assert_valid_skill_response(response: dict[str, Any], skill_name: str | None = None):
    """Assert that a skill response is valid, raising AssertionError if not."""
    result = validate_skill_response(response, skill_name)

    if not result.is_valid:
        error_msg = f"Invalid A2A skill response for {skill_name or 'unknown skill'}:\n"
        error_msg += "\n".join(f"  - {error}" for error in result.errors)
        if result.warnings:
            error_msg += "\n\nWarnings:\n"
            error_msg += "\n".join(f"  - {warning}" for warning in result.warnings)
        raise AssertionError(error_msg)

    return result
