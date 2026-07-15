"""sync_creatives idempotency: replay + conflict + fresh-key re-execution (AdCP 3.1.1).

Until #1546 the sync_creatives ``idempotency_key`` was validated but INERT — a
same-key retry re-executed. These tests pin the wired reservation:

- an identical retry (same key + payload) replays the ORIGINAL success verbatim
  (``replayed=True``) — the cached "created" is returned, NOT re-derived to
  "unchanged";
- the same key with a DIFFERENT canonical payload is ``IDEMPOTENCY_CONFLICT``;
- a DIFFERENT key with an identical payload executes fresh (no cross-key replay),
  observably re-deriving the creative to "unchanged".

sync_creatives reserves in its own committed transaction and completes
best-effort (the body spans several units of work), so the verbatim cache is the
replay authority exactly as it is for sync_accounts.
"""

from __future__ import annotations

import uuid

import pytest

from src.core.exceptions import AdCPIdempotencyConflictError, build_two_layer_error_envelope
from tests.harness import CreativeSyncEnv
from tests.helpers import assert_envelope_shape
from tests.helpers.creative_test_helpers import creative_payload

DEFAULT_AGENT_URL = "https://creative.test.example.com"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _creative(creative_id: str = "c_idem_1", name: str = "Idempotent Creative") -> dict:
    return creative_payload(
        creative_id=creative_id,
        name=name,
        format_id={"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL},
    )


def _action(a) -> str:
    return a.value if hasattr(a, "value") else str(a)


class TestSyncCreativesIdempotency:
    def test_identical_retry_replays_verbatim(self, integration_db):
        with CreativeSyncEnv(tenant_id="cre_replay", principal_id="agent_cre_replay") as env:
            env.setup_default_data()
            key = f"cre-replay-{uuid.uuid4().hex}"

            first = env.call_impl(creatives=[_creative()], idempotency_key=key)
            assert first.replayed is False
            assert _action(first.creatives[0].action) == "created"

            second = env.call_impl(creatives=[_creative()], idempotency_key=key)

        # The spec's top-level marker is present only on the replay, and the cached
        # "created" is replayed verbatim (NOT re-executed to "unchanged").
        assert second.replayed is True
        assert _action(second.creatives[0].action) == "created"
        assert second.creatives[0].creative_id == first.creatives[0].creative_id

    def test_same_key_different_payload_conflicts(self, integration_db):
        with CreativeSyncEnv(tenant_id="cre_conflict", principal_id="agent_cre_conflict") as env:
            env.setup_default_data()
            key = f"cre-conflict-{uuid.uuid4().hex}"

            first = env.call_impl(creatives=[_creative(name="Original")], idempotency_key=key)
            assert first.replayed is False

            with pytest.raises(AdCPIdempotencyConflictError) as excinfo:
                env.call_impl(creatives=[_creative(name="Changed Name")], idempotency_key=key)

        assert_envelope_shape(
            build_two_layer_error_envelope(excinfo.value),
            "IDEMPOTENCY_CONFLICT",
            recovery="correctable",
        )

    def test_fresh_key_re_executes(self, integration_db):
        """A DIFFERENT key with an identical payload executes fresh (no cross-key replay)."""
        with CreativeSyncEnv(tenant_id="cre_fresh", principal_id="agent_cre_fresh") as env:
            env.setup_default_data()

            first = env.call_impl(creatives=[_creative()], idempotency_key=f"k1-{uuid.uuid4().hex}")
            assert first.replayed is False
            assert _action(first.creatives[0].action) == "created"

            second = env.call_impl(creatives=[_creative()], idempotency_key=f"k2-{uuid.uuid4().hex}")

        # Different key -> real re-execution: no replay marker, and the identical
        # creative is re-derived to "unchanged"/"updated" (NOT the verbatim
        # "created" a cross-key replay would have echoed).
        assert second.replayed is False
        assert _action(second.creatives[0].action) in ("unchanged", "updated")
