"""Doubles for the outbound TMP-provider calls, at the egress seam.

Both TMP services reach providers through ``src.core.security.outbound_http``
— the package sync synchronously (``send``) and the health scheduler
asynchronously (``asend``). #1802 moved every outbound call in the application
onto that seam, so these builders returns the seam's OWN types rather than mock
``httpx`` clients: the thing a test doubles is the one function the production
code calls, not the three layers of client/context-manager/response that used
to sit under it.

The values are REAL ``OutboundResult`` / ``OutboundDeliveryFailed`` instances,
not ``MagicMock``s shaped like them. A mock would accept ``.status_code``,
``.ok``, or any other spelling the production code might drift to; the real
types accept only ``http_status``, so a rename at the seam fails these tests
instead of passing them vacuously.

Tests that want the sync path graded end-to-end over a real socket should use
``tests.harness._mixins.TMPSyncMixin`` instead; these builders are for the
unit-level tests of a single function's request shape.
"""

from __future__ import annotations

from src.core.security.egress.attempts import OutboundDeliveryFailed
from src.core.security.egress.response import OutboundResult


def make_seam_result(status_code: int = 200, *, content: bytes = b"") -> OutboundResult:
    """A delivered response the seam would hand back for *status_code*.

    ``headers`` is empty and ``duration_seconds`` is 0.0 — neither TMP call site
    reads them, and inventing values would suggest a test depends on them.
    """
    return OutboundResult(
        http_status=status_code,
        headers={},
        content=content,
        attempts=1,
        duration_seconds=0.0,
    )


def make_delivery_failed(status_code: int | None, *, attempts: int = 1) -> OutboundDeliveryFailed:
    """What the seam raises when the destination was reached but not delivered.

    ``status_code=None`` is the transport-failure case (nothing answered), which
    is the distinction the health probe's "unhealthy" vs "error" answer rests
    on — so a test can state which one it means instead of relying on a mock's
    default.
    """
    return OutboundDeliveryFailed(attempts=attempts, http_status=status_code)
