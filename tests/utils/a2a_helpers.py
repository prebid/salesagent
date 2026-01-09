"""
A2A Test Helpers

Reusable utilities for creating A2A protocol messages in tests.
"""

import uuid
from typing import Any

from a2a.types import Artifact, DataPart, Message, Part, Role


def extract_data_from_artifact(artifact: Artifact) -> dict[str, Any]:
    """Extract the data dictionary from an A2A artifact.

    A2A responses may contain multiple parts:
    - TextPart: Human-readable message (optional, may be first)
    - DataPart: Structured data (required)

    This helper finds the DataPart and returns its data.

    Args:
        artifact: A2A Artifact from response

    Returns:
        Dictionary containing the structured response data, or empty dict if not found
    """
    for part in artifact.parts:
        # Check for DataPart with data attribute
        if hasattr(part, "root") and hasattr(part.root, "data"):
            return part.root.data
        # Direct data attribute check (in case structure varies)
        if hasattr(part, "data"):
            return part.data
    return {}


def create_a2a_message_with_skill(skill_name: str, parameters: dict[str, Any]) -> Message:
    """Create an A2A Message with explicit skill invocation.

    This creates a properly formatted A2A Message that triggers the explicit
    skill invocation path in the A2A server (as opposed to natural language
    processing).

    The A2A server expects structured data in DataPart format:
    - part.data["skill"] contains the skill name
    - part.data["parameters"] contains the skill parameters

    Args:
        skill_name: Name of the skill to invoke (e.g., "get_products", "create_media_buy")
        parameters: Dictionary of parameters to pass to the skill

    Returns:
        Message: A properly formatted A2A Message with DataPart containing skill invocation

    Example:
        >>> msg = create_a2a_message_with_skill(
        ...     "get_products",
        ...     {"brief": "video ads", "limit": 10}
        ... )
        >>> # Use with A2A handler
        >>> response = await handler.handle_message(msg)

    See Also:
        - src/a2a_server/adcp_a2a_server.py: Server-side skill parsing logic
        - A2A Spec: https://github.com/anthropics/agent-to-agent-protocol
    """
    return Message(
        message_id=str(uuid.uuid4()),
        role=Role.user,
        parts=[
            Part(
                root=DataPart(
                    data={
                        "skill": skill_name,
                        "parameters": parameters,  # A2A spec also supports "input"
                    }
                )
            )
        ],
    )


def create_a2a_text_message(text: str) -> Message:
    """Create an A2A Message with natural language text.

    This creates an A2A Message that will be processed via natural language
    understanding (NLU) rather than explicit skill invocation.

    Args:
        text: Natural language text for the message

    Returns:
        Message: A properly formatted A2A Message with TextPart

    Example:
        >>> msg = create_a2a_text_message("Show me video ad products")
        >>> response = await handler.handle_message(msg)
    """
    from a2a.types import TextPart

    return Message(
        message_id=str(uuid.uuid4()),
        role=Role.user,
        parts=[Part(root=TextPart(text=text))],
    )
