"""
A2A Test Helpers

Reusable utilities for creating A2A protocol messages in tests.
Updated for a2a-sdk 1.0 (protobuf API).
"""

import json
import uuid
from typing import Any
from unittest.mock import ANY

from a2a.types import Artifact, Message, Part, Role
from google.protobuf import json_format, struct_pb2


def assert_delivery_forwarded_account(mock_delivery, expected_account) -> None:
    """Assert ``core_get_media_buy_delivery_tool`` was called once forwarding ``expected_account``.

    Every other kwarg is ``ANY`` — the contract being pinned is that the *validated*
    ``AccountReference`` reaches the core tool, not the raw dict that crashed
    ``resolve_account`` (``account_ref.root`` on a dict). Shared by the handler-level
    unit tests and the ``on_message_send`` wire test so the 10-kwarg assertion lives once.
    """
    mock_delivery.assert_called_once_with(
        media_buy_ids=ANY,
        status_filter=ANY,
        start_date=ANY,
        end_date=ANY,
        reporting_dimensions=ANY,
        attribution_window=ANY,
        include_package_daily_breakdown=ANY,
        account=expected_account,
        context=ANY,
        identity=ANY,
    )


def extract_data_from_artifact(artifact: Artifact) -> dict[str, Any]:
    """Extract the data dictionary from an A2A artifact.

    A2A responses may contain multiple parts:
    - Part with text: Human-readable message (optional, may be first)
    - Part with data: Structured data (required)

    In a2a-sdk 1.0, Part.data is a protobuf Value, not a plain dict.

    Args:
        artifact: A2A Artifact from response

    Returns:
        Dictionary containing the structured response data, or empty dict if not found
    """
    for part in artifact.parts:
        if part.HasField("data"):
            return json.loads(json_format.MessageToJson(part.data))
    return {}


def _dict_to_value(d: dict) -> struct_pb2.Value:
    """Convert a Python dict to a protobuf Value for use in Part.data."""
    val = struct_pb2.Value()
    json_format.Parse(json.dumps(d, default=str), val)
    return val


def create_a2a_message_with_skill(skill_name: str, parameters: dict[str, Any]) -> Message:
    """Create an A2A Message with explicit skill invocation.

    This creates a properly formatted A2A Message that triggers the explicit
    skill invocation path in the A2A server (as opposed to natural language
    processing).

    The A2A server expects structured data in Part.data format:
    - data["skill"] contains the skill name
    - data["parameters"] contains the skill parameters

    Args:
        skill_name: Name of the skill to invoke (e.g., "get_products", "create_media_buy")
        parameters: Dictionary of parameters to pass to the skill

    Returns:
        Message: A properly formatted A2A Message with data Part containing skill invocation
    """
    msg = Message(
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_USER,
    )
    msg.parts.append(
        Part(
            data=_dict_to_value(
                {
                    "skill": skill_name,
                    "parameters": parameters,  # A2A spec also supports "input"
                }
            )
        )
    )
    return msg


def create_a2a_text_message(text: str) -> Message:
    """Create an A2A Message with natural language text.

    This creates an A2A Message that will be processed via natural language
    understanding (NLU) rather than explicit skill invocation.

    Args:
        text: Natural language text for the message

    Returns:
        Message: A properly formatted A2A Message with text Part
    """
    msg = Message(
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_USER,
    )
    msg.parts.append(Part(text=text))
    return msg


def make_a2a_identity(sample_tenant, sample_principal):
    """Build a ``ResolvedIdentity`` for an A2A test, from the standard fixtures.

    Shared because the A2A integration tests all need the identical
    ``protocol="a2a"`` identity and were each carrying a byte-identical private
    copy. (Only the A2A shape is consolidated here; the wider ``_make_identity``
    spread across the integration suite uses differing signatures and is a
    separate cleanup.)
    """
    from tests.factories.principal import PrincipalFactory

    return PrincipalFactory.make_identity(
        principal_id=sample_principal["principal_id"],
        tenant_id=sample_tenant["tenant_id"],
        tenant=sample_tenant,
        auth_token=sample_principal["access_token"],
        protocol="a2a",
    )
