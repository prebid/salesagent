"""Shared test helpers for A2A handler tests.

Provides make_a2a_context() to build a ServerCallContext the same way
AdCPCallContextBuilder.build() does in production, but without needing
a Starlette request object, and extract_processing_error_envelope() to
read the two-layer AdCP error envelope off a failed Task returned by
on_message_send's outer error handler.
"""

import json

from a2a.server.context import ServerCallContext
from google.protobuf import json_format

from src.core.auth_context import AUTH_CONTEXT_STATE_KEY, AuthContext


def make_a2a_context(
    auth_token: str | None = None,
    headers: dict[str, str] | None = None,
) -> ServerCallContext:
    """Build a ServerCallContext for A2A handler tests.

    Mirrors AdCPCallContextBuilder.build() — populates state["auth_context"]
    with an AuthContext containing the given token and headers.

    Args:
        auth_token: Bearer token (None for unauthenticated).
        headers: HTTP headers dict (e.g., {"host": "acme.example.com"}).

    Returns:
        ServerCallContext ready to pass to handler.on_message_send(params, context=ctx).
    """
    auth_ctx = AuthContext(auth_token=auth_token, headers=headers or {})
    return ServerCallContext(state={AUTH_CONTEXT_STATE_KEY: auth_ctx})


def extract_processing_error_envelope(task) -> dict:
    """Read the two-layer AdCP envelope from a failed Task's processing_error artifact.

    ``on_message_send``'s outer error handler attaches the envelope built by
    ``AdCPRequestHandler._build_error_envelope`` to the failed Task as a
    single ``processing_error`` artifact with one DataPart (AdCP 3.1.x
    transport-errors.mdx "Layer Separation": application failures ride in
    the task body, not JSON-RPC errors).
    """
    assert task.artifacts, "failed Task must carry the error envelope artifact"
    artifact = task.artifacts[0]
    assert artifact.name == "processing_error", f"expected processing_error artifact, got {artifact.name!r}"
    part = artifact.parts[0]
    assert part.HasField("data"), "envelope artifact part must be a DataPart"
    return json.loads(json_format.MessageToJson(part.data))


def make_mock_a2a_identity():
    """Standard mock ResolvedIdentity for A2A handler unit tests."""
    from tests.factories import PrincipalFactory

    return PrincipalFactory.make_identity(
        principal_id="test-principal",
        tenant_id="test-tenant",
        tenant={"tenant_id": "test-tenant"},
        protocol="a2a",
    )


def make_nl_send_message_request(text: str):
    """Build a minimal A2A SendMessageRequest carrying NL text (no skills)."""
    import uuid

    from a2a.types import Message, Part, Role, SendMessageRequest

    message = Message(
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_USER,
    )
    message.parts.append(Part(text=text))
    return SendMessageRequest(message=message)
