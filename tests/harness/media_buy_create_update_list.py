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

REST needs no routing logic here either, for the same reason: ``MediaBuyCreateListEnv``
switches the endpoint, body builder and response parser for the list branch, and
``MediaBuyDualEnv`` does the same for the update branch. The MRO below linearizes them in
that order, so all three verbs reach their own route — which they must, now that
``_NO_REST_UC_TAG_PREFIXES`` is empty and every UC-019 scenario is parametrized on
rest and e2e_rest.

GH #1941
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
