"""The proof-of-control challenge goes out through the egress seam, not a raw client.

salesagent-prkv.71: this service called ``httpx.AsyncClient`` directly, which the
TID251 bans in ``ruff-egress.toml`` forbid across ``src/``. The lint proves the
import is gone; these prove the REQUEST actually goes through ``asend`` and that
the seam's failure modes still produce the fail-closed answer.

The POST itself has MOVED (#1291 C2). ``notification_proof_service`` holds no send at
all any more: the one place a challenge leaves is
:func:`src.core.signing.outbound.send_signed_challenge`, which signs and dials through
the same ``asend``. So the seam is patched THERE. Patching it on the service — as this
module did while the service owned the POST — would now patch a name the service does
not import, which is exactly the silent vacuity this module exists to prevent.

Worth a behavioural test rather than trusting the lint: the BDD harness replaces
``get_notification_proof_service`` wholesale, so the in-process suites never execute
this path. ``tests/integration/test_notification_proof_challenge.py`` grades the
challenge DOCUMENT and its signature end to end against a real key; these grade the
seam CALL it rides on — one attempt, the in-request-cycle ceiling, the exact bytes —
and the answers that mean "not proven".
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from adcp.types import NotificationConfig
from adcp.webhook_auth import JwkSignerStrategy

from src.core.security.egress.attempts import OutboundDeliveryFailed
from src.core.security.egress.policy import OutboundRequestBlocked
from src.core.security.egress.response import OutboundResult
from src.services.notification_proof_service import (
    CHALLENGE_TIMEOUT_SECONDS,
    ChallengeSigning,
    NotificationProofService,
)
from tests.helpers.egress_hatches import UNDIALLED_PUBLIC_HTTPS_ORIGIN
from tests.helpers.signing import keypair_for

#: The PUBLISHED ``agents[].url`` a real caller derives from the tenant. Only that it
#: reaches the wire matters here; what a receiver does with it is the integration
#: grader's business.
_SELLER_AGENT_URL = "https://seller.adcp-partner.com/mcp/"

#: An IP LITERAL, for two reasons that both have to hold at once. It is not under a
#: reserved TLD, which the service refuses deterministically BEFORE it dials, so a
#: ``.test``/``.example`` host would make every case below pass for the wrong reason;
#: and it needs no DNS, so the service's fire-time ``validate_url`` pre-flight reaches
#: its verdict without this unit test depending on what the box can resolve.
_CHALLENGE_URL = f"{UNDIALLED_PUBLIC_HTTPS_ORIGIN}/proof"


def _config(url: str = _CHALLENGE_URL) -> NotificationConfig:
    return NotificationConfig(url=url, subscriber_id="sub-1", event_types=["final"])


def _signing() -> ChallengeSigning:
    """A real signer, because ``send_signed_challenge`` accepts no other kind.

    Real rather than a mock even though the ``sign=`` hook never fires under a patched
    seam: the strategy is what makes the dial signable at all, and production reaches
    this service only with one the signing boundary minted.
    """
    private_key, jwks = keypair_for("kid-proof")
    return ChallengeSigning(
        strategy=JwkSignerStrategy(private_key=private_key, key_id="kid-proof", alg=jwks["keys"][0]["alg"]),
        seller_agent_url=_SELLER_AGENT_URL,
    )


def _echoing_seam() -> AsyncMock:
    """A seam answering the way an endpoint that CONTROLS the destination does.

    It echoes the single-use value out of the body it was handed
    (``sync_accounts.mdx`` @ v3.1.1 :223-235). Echoing from the POSTED bytes rather
    than from a value the test also knows is what keeps the success case about
    production's own nonce — a fixed body would grade a bare 2xx, which proves only
    that something out there accepts POSTs.
    """

    async def _answer(_url: str, **kwargs: Any) -> OutboundResult:
        echo = json.dumps({"challenge": json.loads(kwargs["content"])["challenge"]}).encode()
        return OutboundResult(
            http_status=200,
            headers={"content-type": "application/json"},
            content=echo,
            attempts=1,
            duration_seconds=0.0,
        )

    return AsyncMock(side_effect=_answer)


async def _prove_with(seam: AsyncMock) -> bool:
    with patch("src.core.signing.outbound.asend", seam):
        return await NotificationProofService().prove("acct-1", _config(), signing=_signing())


@pytest.mark.asyncio
async def test_challenge_is_sent_through_the_seam():
    """The seam is called, with the challenge bytes and a single attempt."""
    seam = _echoing_seam()

    proven = await _prove_with(seam)

    assert proven is True
    assert seam.await_count == 1
    (url,), kwargs = seam.await_args
    assert url == _CHALLENGE_URL
    # ``content=`` and never ``json=``: ``json=`` re-encodes the payload, so the
    # signature would cover bytes that never went on the wire (#1441's defect class).
    assert kwargs.get("json") is None
    posted = json.loads(kwargs["content"])
    assert posted["account_id"] == "acct-1"
    assert posted["subscriber_id"] == "sub-1"
    assert posted["seller_agent_url"] == _SELLER_AGENT_URL
    # One attempt: this runs inside the buyer's request cycle, so a retry only
    # spends latency the caller budgeted. A default max_attempts would silently
    # triple the worst case.
    assert kwargs["max_attempts"] == 1
    # The ceiling travels WITH the call rather than being inherited: the shared
    # outbound boundary's own default is 10.0s, which is right for a background
    # delivery and wrong for a handshake the buyer is waiting on.
    assert kwargs["timeout"] == CHALLENGE_TIMEOUT_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 400, 404, 500])
async def test_a_non_2xx_challenge_is_not_proven(status):
    """A non-2xx is undelivered at the seam, and the status it carries is still graded."""
    assert await _prove_with(AsyncMock(side_effect=OutboundDeliveryFailed(attempts=1, http_status=status))) is False


@pytest.mark.asyncio
async def test_an_egress_refusal_is_not_proven():
    """The seam owns the address decision; its refusal is fail-closed, not an error."""
    assert await _prove_with(AsyncMock(side_effect=OutboundRequestBlocked())) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        # What the seam raises once its single attempt is spent: no response was read,
        # so there is no status to project back onto the answer.
        OutboundDeliveryFailed(attempts=1, http_status=None),
        # And what escapes mid-attempt — a signer that raises is not in the seam's
        # retryable set, so it reaches the caller unwrapped.
        TimeoutError("timed out"),
    ],
    ids=["seam_gave_up", "raised_mid_attempt"],
)
async def test_a_transport_failure_is_not_proven(failure):
    assert await _prove_with(AsyncMock(side_effect=failure)) is False
