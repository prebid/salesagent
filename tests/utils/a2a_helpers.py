"""
A2A Test Helpers

Reusable utilities for creating A2A protocol messages in tests.
Updated for a2a-sdk 1.0 (protobuf API).
"""

import json
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import ANY

from a2a.types import Artifact, Message, Part, Role, SendMessageRequest, Task, TaskState
from google.protobuf import json_format, struct_pb2

from tests.helpers.envelope_assertions import assert_envelope_shape

if TYPE_CHECKING:
    from src.core.resolved_identity import ResolvedIdentity


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


def _read_failed_task_artifact(task: Task, artifact_name: str) -> dict[str, Any]:
    """THE single strict reader of a failed Task's error artifact.

    Pins the artifact NAME, exactly one authoritative DataPart, AND exactly one
    human-readable TextPart (the A2A error binding: a FAILED artifact carries the
    error message as its TextPart, never a DataPart alone), then decodes the
    DataPart. Both public entries (``extract_processing_error_envelope`` and
    ``assert_failed_task_envelope``) route through here so the shape contract has
    exactly one home.
    """
    assert task.artifacts, "failed Task must carry the error envelope artifact"
    artifact = task.artifacts[0]
    assert artifact.name == artifact_name, f"expected {artifact_name!r} artifact, got {artifact.name!r}"
    data_parts = [p for p in artifact.parts if p.HasField("data")]
    text_parts = [p for p in artifact.parts if p.HasField("text")]
    assert len(data_parts) == 1, f"error artifact must carry exactly one authoritative DataPart, got {len(data_parts)}"
    assert len(text_parts) == 1, (
        f"error artifact must carry a human-readable TextPart (A2A error binding), got {len(text_parts)}"
    )
    return extract_data_from_artifact(artifact)


def extract_processing_error_envelope(task: Task) -> dict[str, Any]:
    """Read the two-layer AdCP envelope from a failed Task's ``processing_error`` artifact.

    ``on_message_send``'s outer error handler attaches the envelope built by
    ``AdCPRequestHandler._build_error_envelope`` to the failed Task as a
    ``processing_error`` artifact. Thin wrapper over the shared strict reader.
    """
    return _read_failed_task_artifact(task, "processing_error")


def assert_failed_task_envelope(
    task: Task, *, code: str, recovery: str, artifact_name: str = "error_result"
) -> dict[str, Any]:
    """Assert a synchronously-returned failed A2A Task carries the two-layer AdCP envelope with the
    given wire ``code`` and ``recovery``.

    Pins the FAILED state and the A2A ARTIFACT framing (name, single DataPart, single TextPart)
    via the shared strict reader, then delegates the ENVELOPE assertion to the canonical
    ``assert_envelope_shape`` — so both layers (``adcp_error`` and ``errors[0]``) are required to
    agree here exactly as they are at every other transport boundary. Asserting only
    ``adcp_error`` would let a divergence between the two layers pass every call site.
    ``recovery`` is REQUIRED: silent drift between a typed exception's recovery and the wire is
    exactly the regression this helper exists to catch. The artifact NAME differs by path: a
    per-skill failure emits ``error_result`` (the default), while a top-level rejection (e.g. the
    multi-skill guard) emits ``processing_error`` — pass ``artifact_name`` for the latter.
    Returns the decoded envelope for any test-specific follow-up assertions.
    """
    assert isinstance(task, Task), f"expected a failed Task, got {type(task).__name__}"
    assert task.status.state == TaskState.TASK_STATE_FAILED, f"expected FAILED task, got {task.status.state!r}"
    envelope = _read_failed_task_artifact(task, artifact_name)
    assert_envelope_shape(envelope, code, recovery=recovery)
    return envelope


def make_test_a2a_identity() -> "ResolvedIdentity":
    """Standard factory-built ResolvedIdentity for A2A handler unit tests.

    Not a ``unittest.mock`` object — a real identity from
    ``PrincipalFactory.make_identity`` with canned A2A test values.
    """
    from tests.factories import PrincipalFactory

    return PrincipalFactory.make_identity(
        principal_id="test-principal",
        tenant_id="test-tenant",
        tenant={"tenant_id": "test-tenant"},
        protocol="a2a",
    )


def make_nl_send_message_request(text: str) -> SendMessageRequest:
    """Build a minimal A2A SendMessageRequest carrying NL text (no skills)."""
    return SendMessageRequest(message=create_a2a_text_message(text))


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
