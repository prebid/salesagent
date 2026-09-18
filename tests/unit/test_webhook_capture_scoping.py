"""The outbound-webhook capture records THIS test's traffic and nothing else.

GH #2055. ``capture_outbound_webhooks`` replaces the socket, so the rebind is
process-global and sees every outbound POST — including one made by a background thread
belonging to a test that has already finished. That made the helper structurally unable
to distinguish "my sender delivered twice" from "someone else's delivery landed in my
list", and every assertion that COUNTS captures inherited the blindness.

It reddened the CI matrix twice, on two different tests in
``tests/unit/test_order_approval_service.py``:

    run 32495625542  seed 174142508  test_webhook_notification_sent_on_success  2 == 1
    run 32508361535  seed 13760353   test_webhook_retries_on_failure            2 == 1

The second is the one that names the mechanism: it asserts ONE idempotency key across a
retry ladder and saw two DISTINCT keys. The key is minted ABOVE the retries and outside
the bytes they replay — ``order_approval_service._send_approval_webhook`` puts it in the
payload, ``webhook_egress.deliver_webhook`` serializes that payload ONCE via
``prepare_signed_request`` into a single ``content=`` byte string, and
``outbound_http.send`` hands those same bytes to every attempt of the ``Attempts`` ladder.
A retry therefore cannot mint a second key, so the extra key could only have arrived with
a delivery the test did not make.

The suite had already bent around this rather than fixing it: the same test carried
``"may see 4 calls ... 3 + 1 pollution"`` and a widened ``<= 4`` bound. A count assertion
can absorb an intruder; a key-set assertion cannot, which is why that one kept failing.
Both of those tests have since been retired or regraded onto the egress seam (the ledger
at the top of ``tests/unit/test_order_approval_service.py`` says where each claim went),
but ``capture_outbound_webhooks`` is shared by every webhook suite — including the
outbound-signing ones — so the scoping it grew and this file's grading of it both stay.
"""

from __future__ import annotations

import threading

import httpx

from tests.helpers.webhook_wire import capture_outbound_webhooks


def _post(url: str, body: bytes) -> None:
    with httpx.Client() as client:
        client.post(url, headers={"Content-Type": "application/json"}, content=body)


class TestCaptureRecordsOnlyThisTestsTraffic:
    """Provenance, not timing — so it does not depend on how slow the runner is."""

    def test_a_pre_existing_thread_s_delivery_is_not_captured(self):
        """The regression: a foreign background thread posting into the window.

        The thread is started BEFORE the capture opens, which is what makes it foreign —
        exactly the shape of a background thread outliving an earlier test. Without the
        scoping this captures 2 and the idempotency-key set has 2 members, reproducing
        the CI failure in-process.
        """
        release = threading.Event()
        started = threading.Event()

        def _foreign() -> None:
            started.set()
            release.wait(timeout=5.0)
            _post("https://intruder.example.com/hook", b'{"idempotency_key": "whk_foreign"}')

        intruder = threading.Thread(target=_foreign, daemon=True)
        intruder.start()
        started.wait(timeout=5.0)

        with capture_outbound_webhooks() as captured:
            release.set()
            intruder.join(timeout=5.0)
            _post("https://mine.example.com/hook", b'{"idempotency_key": "whk_mine"}')

        assert [request.url for request in captured] == ["https://mine.example.com/hook"], (
            "the capture must record only deliveries this test caused; a POST from a "
            f"thread that existed before the block opened is someone else's. Got: "
            f"{[request.url for request in captured]}"
        )
        assert {request.payload["idempotency_key"] for request in captured} == {"whk_mine"}

    def test_a_thread_the_test_spawns_inside_the_block_is_captured(self):
        """The other half, or the fix would be a silent under-count.

        Scoping to "the capturing thread only" would drop deliveries a test legitimately
        makes from a worker it started itself — which several webhook tests do. Ours is
        any thread that did not exist when the block opened.
        """
        with capture_outbound_webhooks() as captured:
            worker = threading.Thread(
                target=_post, args=("https://mine.example.com/hook", b'{"idempotency_key": "whk_worker"}')
            )
            worker.start()
            worker.join(timeout=5.0)

        assert {request.payload["idempotency_key"] for request in captured} == {"whk_worker"}
