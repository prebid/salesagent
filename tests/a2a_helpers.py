"""Shared test helpers for A2A handler tests.

Provides make_a2a_context() to build a ServerCallContext the same way
AdCPCallContextBuilder.build() does in production, but without needing
a Starlette request object, and extract_processing_error_envelope() to
read the two-layer AdCP error envelope off a failed Task returned by
on_message_send's outer error handler.
"""

from a2a.server.context import ServerCallContext
from a2a.types import SendMessageRequest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.auth_context import AUTH_CONTEXT_STATE_KEY, AuthContext
from src.core.resolved_identity import ResolvedIdentity
from tests.factories import PrincipalFactory
from tests.utils.a2a_helpers import create_a2a_text_message, extract_data_from_artifact


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


def make_a2a_handler(
    auth_token: str | None = "test-token",
    headers: dict[str, str] | None = None,
) -> tuple[AdCPRequestHandler, ServerCallContext]:
    """Handler + authenticated call context for driving on_message_send.

    The token lives in the context state where the real ``_get_auth_token`` reads
    it, so token extraction runs for real — the unit does not stub it. Single
    source for the handler+context setup shared across the A2A handler unit tests.
    """
    handler = AdCPRequestHandler()
    ctx = make_a2a_context(auth_token=auth_token, headers=headers or {"host": "test.example.com"})
    return handler, ctx


def extract_processing_error_envelope(task) -> dict:
    """Read the two-layer AdCP envelope from a failed Task's processing_error artifact.

    ``on_message_send``'s outer error handler attaches the envelope built by
    ``AdCPRequestHandler._build_error_envelope`` to the failed Task as a
    ``processing_error`` artifact carrying the adcp_error DataPart plus a
    recommended human-readable TextPart (AdCP 3.1.1 a2a-response-format.mdx
    "Where the Error Lives": a Task-execution failure rides in the task body).
    Reads the DataPart via the shared ``extract_data_from_artifact`` scanner, so
    a leading TextPart does not shift it.
    """
    assert task.artifacts, "failed Task must carry the error envelope artifact"
    artifact = task.artifacts[0]
    assert artifact.name == "processing_error", f"expected processing_error artifact, got {artifact.name!r}"
    data = extract_data_from_artifact(artifact)
    assert data, "processing_error artifact must carry a DataPart"
    return data


def make_mock_a2a_identity() -> ResolvedIdentity:
    """Standard mock ResolvedIdentity for A2A handler unit tests."""
    return PrincipalFactory.make_identity(
        principal_id="test-principal",
        tenant_id="test-tenant",
        tenant={"tenant_id": "test-tenant"},
        protocol="a2a",
    )


def make_nl_send_message_request(text: str) -> SendMessageRequest:
    """Build a minimal A2A SendMessageRequest carrying NL text (no skills)."""
    return SendMessageRequest(message=create_a2a_text_message(text))
