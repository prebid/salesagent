"""get_adcp_capabilities — one rendering of this seller's capabilities.

The derivation lives in :mod:`src.services.seller_capabilities`, because the A2A
agent card answers the same question about the same tenant and reads the same
service. What is left here is what only a TOOL CALL has: a caller to log, a
response model to fill, and the request's own protocol filter.

This module follows the MCP/A2A shared implementation pattern from CLAUDE.md.
"""

import logging
from datetime import UTC, datetime

from adcp.types.generated_poc.protocol.get_adcp_capabilities_request import Protocol as RequestProtocol

from src.core.helpers import enum_value
from src.core.helpers.activity_helpers import log_tool_activity
from src.core.resolved_identity import PublicIdentity
from src.core.schemas import GetAdcpCapabilitiesRequest, GetAdcpCapabilitiesResponse
from src.services.seller_capabilities import describe_seller

logger = logging.getLogger(__name__)

# The response sections a buyer may filter down to, taken from the REQUEST's own
# Protocol enum rather than a hand-copied tuple: the two are the same five names
# today, and a literal list would silently stop filtering a domain the spec later
# adds — the response would then carry a section the buyer did not ask for.
_PROTOCOL_DOMAIN_SECTIONS: frozenset[str] = frozenset(p.value for p in RequestProtocol)


def _get_adcp_capabilities_impl(
    req: GetAdcpCapabilitiesRequest | None, identity: PublicIdentity
) -> GetAdcpCapabilitiesResponse:
    """Render this seller's capabilities for *identity*, as the pinned response.

    A controller: it reads who is calling off the identity, delegates, and renders.
    The three things it does that the service must not know about are the three that
    are not facts about the seller — the caller to log, the wire shape to fill, and
    the buyer's own section filter.

    Version negotiation is NOT here. It runs at the boundary, before this or any
    other implementation is called, so every tool answers a bad pin the same way and
    none of them can forget to ask. It stays un-tenant-gated by construction: the
    boundary rejects before an identity is enriched, let alone a tenant read.
    """
    if identity.tenant:
        log_tool_activity(identity, "get_adcp_capabilities")

    seller = describe_seller(identity)

    response = GetAdcpCapabilitiesResponse(
        adcp=seller.adcp,
        supported_protocols=seller.supported_protocols,
        specialisms=seller.specialisms,
        measurement=seller.measurement,
        experimental_features=seller.experimental_features,
        media_buy=seller.media_buy,
        account=seller.account,
        webhook_signing=seller.webhook_signing,
        request_signing=seller.request_signing,
        errors=seller.advisories or None,
        # Absent on the minimal (no-tenant) description, where there is no stored
        # state whose freshness the stamp would describe.
        last_updated=datetime.now(UTC) if identity.tenant else None,
    )

    # Purely subtractive, and deliberately applied AFTER the derivation rather than
    # passed into it. The service takes only the identity, so both it and the agent
    # card make the same call and cannot be handed different arguments — the card can
    # never see a section the tool would have hidden, only more of them. Paying for
    # sections a filtered request discards is the price of that guarantee, and a
    # discovery call is not a hot path.
    #
    # model_copy rather than setattr so the filtered response is built, not mutated
    # after validation.
    if req and req.protocols:
        requested = {enum_value(p) for p in req.protocols}
        dropped = {name: None for name in _PROTOCOL_DOMAIN_SECTIONS if name not in requested}
        if dropped:
            response = response.model_copy(update=dropped)

    return response
