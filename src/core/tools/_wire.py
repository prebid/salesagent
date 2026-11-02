"""The one function that turns a response model into the body a buyer receives."""

from __future__ import annotations

from typing import Any

from adcp.types import ProtocolEnvelope


def to_wire(response: ProtocolEnvelope) -> dict[str, Any]:
    """Serialize a tool's response into the body every transport sends.

    One function, three callers. MCP puts the result in ``ToolResult.structured_content``,
    A2A in an artifact ``DataPart``, REST returns it as the HTTP body -- those three
    containers are the only thing that legitimately differs, because the transports really
    do have different envelopes. What goes INSIDE is the same bytes for all of them.

    There is nothing per-transport to add here: a transport that stamped its own key into the
    body would make one response object into a different document per transport.

    Envelope fields need no help from this function. ``status``, ``task_id``, ``message``,
    ``replayed`` and the rest are declared on the response model, so they serialize like any
    other field and a field added to the envelope reaches all three transports without anyone
    editing a transport.

    The parameter is bound to ``ProtocolEnvelope``, not ``BaseModel``, because that inheritance
    IS the guarantee above. A plain pydantic model routed through here would type-check against
    the wider bound and produce a body with no envelope at all -- the same reasoning that binds
    ``mcp_result`` to ``AdCPBaseModel`` rather than to ``BaseModel``.

    ``adcp_version`` and ``context`` are two of those declared envelope fields and need no help
    here either. They are set on the MODEL at the boundary (``_boundary._served``), on every
    outcome, and serialize from there like ``status`` or ``message``.
    """
    return response.model_dump(mode="json")
