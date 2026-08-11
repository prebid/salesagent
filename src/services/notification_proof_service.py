"""Proof-of-control challenge for account-level notification subscribers (#1592 T2).

AdCP requires a seller to verify endpoint control BEFORE persisting or exposing a
notification subscriber as ``active: true``. This service performs that check.

## Why this runs in the request cycle (a narrow, explicitly-granted exception)

The project's architecture direction is that outbound I/O does NOT belong in the
HTTP request cycle: accept, validate against Postgres, return pending, let a worker
do the network call. This service is a deliberate exception, granted by
KonstantinMirin on 2026-07-27 and recorded in
``.claude/notes/async-sync-architecture.md``.

The reason it is an exception rather than a precedent: the spec forbids the async
shape here. There is no per-subscriber pending state -- ``active`` is a boolean and
the per-entry action enum is created/updated/unchanged/failed -- and the response
echoes state *after* activation-proof checks. So echoing ``active: true`` before the
proof violates a MUST, echoing ``active: false`` misstates the applied state, and a
background flip behind a synchronous "updated" is non-conformant. If the response is
synchronous, the proof outcome must be in it.

Containment, so this stays one bounded handshake and not the beginning of adapter
I/O in the request path:
  - ONE POST, no retries;
  - a hard per-challenge timeout, and a per-request budget in the caller;
  - fired ONLY for an entry declaring ``active: true`` whose proof tuple is not
    already persisted (the spec's proof-reuse allowance);
  - fail-closed: anything other than a clear success is "not proven".

RFC 9421 signing of the challenge is NOT implemented. Under the STRICT capability
policy that means we also declare no signing capability -- see FIXME(#1291).
"""

import logging

import httpx
from adcp.types import NotificationConfig

from src.core.security.url_validator import check_url_ssrf, is_reserved_tld_host

logger = logging.getLogger(__name__)

#: Hard ceiling for a single challenge. Deliberately small: this runs inside the
#: request cycle, so the buyer's latency budget is the constraint, not the
#: endpoint's convenience.
CHALLENGE_TIMEOUT_SECONDS = 2.0


class NotificationProofService:
    """Performs the proof-of-control challenge for a notification subscriber."""

    async def prove(self, account_id: str, config: NotificationConfig) -> bool:
        """Whether endpoint control over ``config.url`` is proven. Fail-closed.

        Returns False -- never raises -- so a proof failure becomes a per-account
        rejection inside a transport-level success, which is what the spec asks
        for, rather than a transport error.
        """
        url = str(config.url or "")
        if not url:
            return False

        from urllib.parse import urlparse

        hostname = urlparse(url).hostname or ""

        # RFC 2606/6761 reserved TLDs can never be proven. Deciding this WITHOUT a
        # DNS lookup is what makes the outcome deterministic: relying on
        # `buyer.example` returning NXDOMAIN would make the result depend on
        # whether the local resolver hijacks unknown names, which many do.
        if is_reserved_tld_host(hostname):
            logger.info(
                "Notification proof for account %s refused: %s is under a reserved TLD and can never be proven",
                account_id,
                hostname,
            )
            return False

        # Full SSRF check at FIRE time (not write time): we are about to send a
        # request, so where the name actually resolves now matters.
        safe, reason = check_url_ssrf(url, require_https=True)
        if not safe:
            logger.info("Notification proof for account %s refused: %s", account_id, reason)
            return False

        # FIXME(#1291): the challenge MUST be RFC 9421-signed. Signing is not
        # implemented, so the seller declares no signing capability (STRICT policy)
        # and this POST goes out unsigned. A buyer cannot yet verify the challenge
        # originated from us.
        try:
            async with httpx.AsyncClient(timeout=CHALLENGE_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url,
                    json={
                        "type": "adcp.notification.proof_of_control",
                        "account_id": account_id,
                        "subscriber_id": config.subscriber_id,
                    },
                )
        except Exception as exc:
            # Any transport failure -- timeout, TLS, connection refused -- is "not
            # proven". Broad by intent: the question is binary and the safe answer
            # to every unexpected condition is False.
            logger.info("Notification proof for account %s failed for %s: %s", account_id, url, exc)
            return False

        proven = 200 <= response.status_code < 300
        if not proven:
            logger.info(
                "Notification proof for account %s failed for %s: status %s",
                account_id,
                url,
                response.status_code,
            )
        return proven


_service: NotificationProofService | None = None


def get_notification_proof_service() -> NotificationProofService:
    """Module-level singleton -- and the ONE injection seam tests patch.

    Mirrors ``get_protocol_webhook_service``. Tests override this getter rather
    than the class, so production always holds a real prover: a test-scoped
    auto-pass prover would persist ``active: true`` without proof, which is the
    exact violation this service exists to prevent.
    """
    global _service
    if _service is None:
        _service = NotificationProofService()
    return _service
