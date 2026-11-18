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
from pydantic import BaseModel

from src.a2a_server.adcp_a2a_server import restore_a2a_integer_types


def assert_delivery_forwarded_account(mock_delivery, expected_account, **forwarded) -> None:
    """Assert ``core_get_media_buy_delivery_tool`` was called once forwarding ``expected_account``.

    The contract being pinned is that the *validated* ``AccountReference`` reaches the core
    tool, not the raw dict that crashed ``resolve_account`` (``account_ref.root`` on a dict).
    Shared by the handler-level unit tests and the ``on_message_send`` wire test so the
    assertion lives once.

    ``forwarded`` names the OTHER request fields this caller's payload should produce, as
    field-name -> expected-value. The handler builds through the shared builder and hands
    the wrapper ONE ``req``, so the account and its siblings are graded on the request
    rather than on wrapper kwargs. Anything the buyer did not send must still be None on
    the request — that is what proves nothing was manufactured. This used to assert eight
    blanket ``ANY``s, which passed whatever the handler happened to forward.
    """
    from src.core.schemas import GetMediaBuyDeliveryRequest

    # The WHOLE request is the expectation, not a few fields off call_args: every field the
    # caller did not name must come back at its default, which is what proves the handler
    # invented nothing. Building the expected request and comparing it wholesale also keeps
    # this a single assert_called_once_with, the form the weak-mock guard requires.
    expected_req = GetMediaBuyDeliveryRequest(account=expected_account, **forwarded)
    mock_delivery.assert_called_once_with(req=expected_req, identity=ANY)


def extract_data_from_artifact(artifact: Artifact) -> dict[str, Any]:
    """Extract the data dictionary from an A2A artifact.

    A2A responses may contain multiple parts:
    - Part with text: Human-readable message (optional, may be first)
    - Part with data: Structured data (required)

    In a2a-sdk 1.0, Part.data is a protobuf Value, not a plain dict.

    google.protobuf.Value has no integer variant -- json_format.MessageToJson
    widens every number to a double (86400 -> 86400.0), which is exactly what
    the real a2a-sdk wire (jsonrpc_dispatcher.MessageToDict) does too. Passing
    the result through restore_a2a_integer_types keeps this "real A2A wire"
    capture (tests/CLAUDE.md) honest for known integer-typed AdCP fields
    instead of silently diverging from what production (src/app.py's /a2a
    route wrapper) emits.

    Args:
        artifact: A2A Artifact from response

    Returns:
        Dictionary containing the structured response data, or empty dict if not found
    """
    for part in artifact.parts:
        if part.HasField("data"):
            return restore_a2a_integer_types(json.loads(json_format.MessageToJson(part.data)))
    return {}


def _json_default(obj: Any) -> Any:
    """Encode what ``json`` cannot, the way a real A2A client would.

    A pydantic model MUST become its wire shape here. The bare ``default=str``
    this replaces turned a model into ``repr()`` -- a single opaque string where
    the server expected an object -- so a scenario that passed a model instance
    reached the skill with every field gone, and the resulting creative came back
    as ``creative_id='unknown'`` rather than failing loudly (salesagent-kyc89).
    No client can put a python object on the wire; it sends the model's JSON.
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    return str(obj)


def _dict_to_value(d: dict) -> struct_pb2.Value:
    """Convert a Python dict to a protobuf Value for use in Part.data."""
    val = struct_pb2.Value()
    json_format.Parse(json.dumps(d, default=_json_default), val)
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
