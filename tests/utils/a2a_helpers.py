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


def make_a2a_identity(sample_tenant: dict, sample_principal: dict) -> ResolvedIdentity:
    """Build a ``ResolvedIdentity`` for an A2A test, from the standard fixtures.

    Delegates to the canonical ``tests.harness.make_identity`` (itself a thin
    wrapper over ``PrincipalFactory.make_identity``) rather than reimplementing the
    call, so the A2A suite routes through the one identity home the harness exposes
    and keeps its ``-> ResolvedIdentity`` contract in a single place.

    Shared because the A2A explicit-skill integration tests need the identical
    ``protocol="a2a"`` identity: ``test_a2a_brand_manifest.py`` carried a private
    copy of this exact call (deleted here), and ``test_a2a_error_responses.py``
    inlines the same 5-kwarg shape 16 more times.

    Scope of the follow-up migration — the unit is the whole seam block, not the
    identity third alone. 14 of those 16 sites pair the identity build with the same
    two seam assignments (``_get_auth_token`` / ``_resolve_a2a_identity`` stubbed on a
    per-test fresh handler), which is exactly what ``seamed_a2a_handler``
    (``tests/integration/conftest.py``) now folds into one fixture; the other 2 call
    ``_handle_*_skill`` directly with no seams and take this helper on its own.
    Blocking detail for whoever does it: that file seeds its own ``a2a_error_test``
    tenant and depends on local ``test_tenant`` / ``test_principal`` fixtures, while
    ``seamed_a2a_handler`` is bound to ``sample_tenant`` / ``sample_principal`` — one
    of the two has to give before the seam block can be reused there.

    Explicitly NOT a candidate: ``test_a2a_response_message_fields.py``'s 2 builds are
    a different 4-kwarg shape (no ``auth_token``; one passes a ``LazyTenantContext``
    rather than a tenant dict).
    """
    # Function-local (unlike the module-top imports above) on purpose: tests/utils is a leaf,
    # and importing ``tests.harness`` eagerly loads the whole env hierarchy — its ``__init__``
    # pulls ``product``/``delivery_poll``, which pull ``tests.harness._mixins``, which imports
    # ``_get_products_impl`` and ``_get_media_buy_delivery_impl`` at module level. Pay that
    # production-import cost only when this helper is actually called.
    from tests.harness import make_identity

    return make_identity(
        principal_id=sample_principal["principal_id"],
        tenant_id=sample_tenant["tenant_id"],
        tenant=sample_tenant,
        auth_token=sample_principal["access_token"],
        protocol="a2a",
    )
