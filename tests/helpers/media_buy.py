"""Shared media-buy read-back helpers for tests."""

from __future__ import annotations

from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas._base import GetMediaBuysMediaBuy, GetMediaBuysRequest
from src.core.tools.media_buy_list import _get_media_buys_impl


def read_back_media_buy(identity: ResolvedIdentity, media_buy_id: str) -> GetMediaBuysMediaBuy:
    """Read exactly one media buy through the production list implementation."""
    response = _get_media_buys_impl(
        req=GetMediaBuysRequest(media_buy_ids=[media_buy_id]),
        identity=identity,
        include_snapshot=False,
    )
    assert len(response.media_buys) == 1, (
        f"expected exactly one media buy for {media_buy_id!r}, got {len(response.media_buys)}"
    )
    return response.media_buys[0]
