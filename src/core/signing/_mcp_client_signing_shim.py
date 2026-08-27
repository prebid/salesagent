"""Workaround for adcontextprotocol/adcp-client-python#1017 (#1291 C3).

``ADCPClient``/``ADCPMultiAgentClient``'s own RFC 9421 auto-signing is a
verified no-op on the MCP transport (streamable-HTTP), which is the only
transport either creative or signals agent registry uses. Root cause, traced
against the installed ``adcp==6.6.0`` + ``mcp==1.28.1``: ``adcp/protocols/mcp.py``
calls ``self._get_session()`` (which lazily connects and spawns the ``mcp``
package's persistent ``post_writer`` background task via ``anyio``'s
``start_soon``) BEFORE it sets the ``current_operation`` ContextVar around
``session.call_tool(...)``. ``asyncio``/``anyio`` copy the spawning task's
context at ``start_soon`` time, so ``post_writer``'s frozen copy never sees the
operation name the SDK sets afterward in the calling task -- every message
``post_writer`` later dispatches (itself spawned via ``start_soon`` FROM
``post_writer``'s own task) inherits that same frozen, operation-less context.
``ADCPClient._sign_outgoing_request`` therefore always reads
``current_operation() is None`` and returns without signing.

The fix needs no SDK patch: wrap the ENTIRE outbound call -- including the
lazy connect -- in :func:`adcp.signing.client.signing_operation` from OUTSIDE
the SDK's own (too-late) internal ``.set()``. Both registries already
construct a FRESH ``ADCPMultiAgentClient`` per call (one call = one connection
= one ``post_writer`` task), so our outer context manager is active DURING the
lazy connect and ``post_writer``'s task-spawn snapshot correctly captures the
operation name. Verified empirically against a real local FastMCP counterparty
(see ``tests/unit/test_mcp_client_signing_shim.py``): wrapped calls carry a
valid ``Signature``/``Signature-Input``/``Content-Digest``; unwrapped calls on
the same transport never do.

Callers MUST pre-resolve ``get_adcp_capabilities`` on the client instance
(``client._capabilities`` / ``client._capabilities_fetched_at``) via
:func:`bootstrap_capabilities_for_signed_call` BEFORE calling
:func:`sign_scoped_mcp_call`: because ``post_writer``'s frozen context lasts
for the whole session, the SDK's own recursive ``fetch_capabilities()`` call
inside ``_sign_outgoing_request`` would otherwise also be mis-tagged with the
outer operation name (never ``"get_adcp_capabilities"``), defeating the SDK's
own bootstrap carve-out and hanging indefinitely. Kept as a SEPARATE function
(not folded into ``sign_scoped_mcp_call``) because it needs its own throwaway
client/connection and its own failure handling (missing/broken counterparty
capabilities must degrade to unsigned, never hang or raise) -- see
``tests/unit/test_mcp_client_signing_shim.py`` for the isolation rationale.

DELETE this module once adcontextprotocol/adcp-client-python#1017 is fixed
upstream and ``adcp`` is repinned to a version containing the fix -- inline
the plain, unwrapped call at every current caller instead.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from adcp import ADCPClient
from adcp.exceptions import ADCPError
from adcp.signing.client import signing_operation

if TYPE_CHECKING:
    from adcp import AgentConfig

logger = logging.getLogger(__name__)


async def sign_scoped_mcp_call[T](operation: str, call: Callable[[], Awaitable[T]]) -> T:
    """Run *call* with ``current_operation`` scoped around its ENTIRE lifetime.

    *call* must perform the full outbound MCP round trip itself (including
    any lazy session connect) -- not just the final ``call_tool`` -- so the
    signing hook's operation-name ContextVar is set before the transport's
    background send-loop task is spawned. See module docstring.
    """
    with signing_operation(operation):
        return await call()


async def bootstrap_capabilities_for_signed_call(client: ADCPClient, agent_config: AgentConfig) -> None:
    """Pre-populate *client*'s capabilities cache so a later signed call never recurses.

    Fetches ``get_adcp_capabilities`` via a SEPARATE, throwaway client for the
    same agent (never *client* itself -- ``get_adcp_capabilities`` is exempt
    from signing anyway, so this fetch is always safe/unsigned regardless of
    transport) and copies the result onto *client*'s own cache attributes.
    *client* must not have made any request yet (its ``post_writer`` task, if
    any, must not have spawned) -- call this before the first
    :func:`sign_scoped_mcp_call`.

    A missing or broken counterparty ``get_adcp_capabilities`` degrades to
    "proceed unsigned" (never raises, never hangs): signing is strictly
    additive, so a counterparty we cannot even ask about its posture must not
    break a call that works unsigned today. Callers should check
    ``client.signing is None`` afterward only to decide whether to still wrap
    the real call in :func:`sign_scoped_mcp_call` -- this function itself
    never mutates ``client.signing``.
    """
    if client.signing is None:
        return

    bootstrap = ADCPClient(agent_config, signing=None)
    try:
        caps = await bootstrap.fetch_capabilities()
    except (ADCPError, OSError) as exc:
        logger.warning(
            "Could not fetch get_adcp_capabilities for %r before a signed call; "
            "this call will proceed UNSIGNED rather than risk a hang or a broken request: %s",
            agent_config.id,
            exc,
        )
        client.signing = None
        return
    finally:
        await bootstrap.close()

    client._capabilities = caps
    client._capabilities_fetched_at = time.monotonic()


async def signed_agent_call[T](client: ADCPClient, operation: str, call: Callable[[], Awaitable[T]]) -> T:
    """Run *call* against *client*, signed when the client has a signing config.

    THE ONE OPERATION this module exports. It composes what were two exported halves
    plus a prose ordering contract, because a caller that composed them in the wrong
    order signed nothing and raised nothing — and both call sites re-assembled the same
    four steps (the ``client.signing is None`` check, the bootstrap, the wrap, the
    unsigned else branch), so a third call site written from either one inherited the
    chance to get the order wrong.

    The ordering the module docstring mandates is now structural: the bootstrap happens
    before the wrap because this function does it in that order, not because a caller
    read a paragraph. ``client.signing`` is re-read AFTER the bootstrap, since the
    bootstrap clears it when a counterparty's capabilities cannot be fetched — signing
    is strictly additive, so that degrades to an unsigned call rather than raising.
    """
    if client.signing is None:
        return await call()

    await bootstrap_capabilities_for_signed_call(client, client.agent_config)
    if client.signing is None:
        return await call()
    return await sign_scoped_mcp_call(operation, call)
