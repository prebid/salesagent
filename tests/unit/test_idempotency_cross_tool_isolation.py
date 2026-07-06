"""Cross-tool isolation for the shared verbatim-replay cache.

The idempotency cache scope is ``(tenant_id, principal_id, account_id, idempotency_key)`` —
``tool_name`` is NOT a scope dimension (see ``IdempotencyAttemptRepository``), so a
``create_media_buy`` and a ``sync_accounts`` under one principal CAN land on the same cache
row with the same key. The guarantee that a foreign tool's envelope is never replayed rests
on each tool's response cross-validation: the decoder re-validates the stored envelope against
its OWN response schema and returns ``None`` (a cache miss) on failure.

Because each success response carries a REQUIRED field the other lacks
(``CreateMediaBuySuccess.media_buy_id`` vs ``SyncAccountsResponse.accounts``), the rejection
holds in BOTH schema-extra modes — ``forbid`` in CI and ``ignore`` in production — since a
missing required field fails validation regardless of how extras are treated.

(The other named invariant — RFC-8785 request-hash disjointness — makes the shared-scope
lookup raise ``IDEMPOTENCY_CONFLICT`` before any replay; these tests pin the backstop that
holds even if two tools' request hashes ever collided.)
"""

from src.core.schemas._base import CreateMediaBuySuccess
from src.core.schemas.account import SyncAccountsResponse
from src.core.tools.accounts import _sync_replay_from_envelope
from src.core.tools.media_buy_create import _replay_create_from_envelope


def _envelope(response_model):
    """The shape the verbatim cache stores: ``{"status": ..., "response": <model dump>}``."""
    return {"status": "completed", "response": response_model.model_dump(mode="json")}


def _create_envelope():
    return _envelope(CreateMediaBuySuccess(media_buy_id="mb_x", packages=[]))


def _sync_envelope():
    return _envelope(SyncAccountsResponse(accounts=[]))


def test_a_create_envelope_never_replays_as_a_sync_response():
    # A create's cached success decoded through sync's decoder is a MISS (None), so the shared
    # cache re-executes rather than handing a create response back to a sync caller.
    assert _sync_replay_from_envelope(_create_envelope()) is None
    # ...and sync's decoder DOES decode a genuine sync envelope — proving the None above is
    # rejection of a foreign shape, not a universally-dead decoder.
    assert _sync_replay_from_envelope(_sync_envelope()) is not None


def test_a_sync_envelope_never_replays_as_a_create_response():
    assert _replay_create_from_envelope(_sync_envelope()) is None
    assert _replay_create_from_envelope(_create_envelope()) is not None
