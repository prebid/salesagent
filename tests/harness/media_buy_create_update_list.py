"""MediaBuyCreateUpdateListEnv — one env for all three producers of a buy's revision.

AdCP 3.1.1 names the buy's ``revision`` as interchangeable across producers:
``dist/schemas/3.1.1/media-buy/update-media-buy-request.json`` says of ``revision``
"Obtain from get_media_buys or the most recent create/update response", and
``update-media-buy-response.json`` defines the response's own ``revision`` as
"Revision number after this update". Grading that agreement needs create, update
and get_media_buys in ONE environment, on ONE identity, against ONE row — which is
exactly what this composite provides.

No routing logic of its own: ``MediaBuyCreateListEnv`` already routes
``req=GetMediaBuysRequest`` to the list dispatch, and ``MediaBuyDualEnv`` already
routes ``req=UpdateMediaBuyRequest`` to the update dispatch and falls through to
create. Inheriting both linearizes to list -> update -> create, so a third copy of
either discriminator would be duplication (CLAUDE.md DRY invariant).

REST is routed for all three arms: create collection, update per-id PUT, and
get_media_buys ``POST /api/v1/media-buys/query`` (via ``MediaBuyCreateListEnv``).
Use the A2A and MCP wire transports when grading envelope bytes that those
pipelines stash as ``wire_response``.

GH #1941 / #1830
"""

from __future__ import annotations

from tests.harness.media_buy_create_list import MediaBuyCreateListEnv
from tests.harness.media_buy_dual import MediaBuyDualEnv


class MediaBuyCreateUpdateListEnv(MediaBuyCreateListEnv, MediaBuyDualEnv):
    """create_media_buy + update_media_buy + get_media_buys on one identity.

    MRO: ``MediaBuyCreateListEnv`` -> ``MediaBuyListDispatchMixin`` ->
    ``MediaBuyDualEnv`` -> ``MediaBuyCreateEnv``. Each ``call_*`` checks its own
    discriminator and delegates upward, so a ``GetMediaBuysRequest`` reaches the
    list dispatch, an ``UpdateMediaBuyRequest`` reaches the update dispatch, and
    anything else reaches create. The update-module patches come from
    ``MediaBuyDualEnv.__enter__``; get_media_buys needs none (pure DB read).
    """
