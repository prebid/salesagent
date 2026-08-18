"""Shared AdCP schema validation for A2A skill responses.

Both A2A validators -- the in-process one in
``tests/integration/test_a2a_skill_invocation.py`` and the over-HTTP one in
``tests/e2e/test_a2a_adcp_compliance.py`` -- validate the same thing against
the same pinned index, differing only in how they get the payload out of the
transport (a protobuf artifact vs a JSON-RPC dict). That extraction stays at
the call sites; everything downstream of it lives here, once.

The AdCP task name is DERIVED from the skill id (``_`` -> ``-``) rather than
looked up in a stored map. A stored map is a second copy of production's skill
roster, and the copy this replaced had already gone stale in both directions:
it listed a skill production had deleted and omitted five it ships. Deriving
means a skill added to ``create_agent_card()`` is graded the same day with no
test edit. If AdCP ever ships a task whose key is not the transform of our
skill id, the derived name fails to resolve and the roster test flags the skill
as missing -- a loud false positive, which is the correct failure direction.
"""

from __future__ import annotations

from typing import Any

from tests.helpers.adcp_schema_validator import AdCPSchemaValidator, SchemaValidationError


async def validate_a2a_skill_payload(
    validator: AdCPSchemaValidator,
    skill_name: str,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate one extracted A2A payload against the skill's pinned response schema.

    Args:
        validator: An entered ``AdCPSchemaValidator``.
        skill_name: The A2A skill id (e.g. ``"get_products"``).
        payload: The AdCP payload already extracted from the transport.

    Returns:
        ``{"valid": bool, "errors": [...], "warnings": [...], "schema_tested": str | None}``.
        ``schema_tested`` is ``None`` exactly when no response schema for the
        derived task exists in the pinned index -- i.e. when nothing was graded.
    """
    errors: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {
        "valid": True,
        "errors": errors,
        "warnings": warnings,
        "schema_tested": None,
    }

    task = skill_name.replace("_", "-")
    if await validator._find_schema_ref_for_task(task, "response") is None:
        warnings.append(f"No AdCP response schema for skill '{skill_name}' (task '{task}') in pinned index")
        return result

    result["schema_tested"] = task

    try:
        await validator.validate_response(task, payload)
        warnings.append("AdCP schema validation passed")
    except SchemaValidationError as e:
        # str(e) is only the top-level message ("Schema validation failed for X
        # response") -- the field-level errors live on e.validation_errors, and
        # dropping them made a real production bug take much longer to diagnose
        # than it should have. This is the only place that join exists.
        errors.append(f"AdCP schema validation failed: {'; '.join(e.validation_errors)}")
        result["valid"] = False
    except Exception as e:
        errors.append(f"Validation error: {e}")
        result["valid"] = False

    return result
