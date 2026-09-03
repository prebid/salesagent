"""Contract tests for the long-lived webhook-capture compose service (GH #1802).

``tests/e2e/webhook_capture_service.py`` does not exist yet — this is the TDD
red step for GH #1802's implementation plan. It pins the SERVICE'S OWN
request-handling contract in-process (no Docker, no compose network, no
tls-proxy): per-key isolation, atomic drain-on-DELETE, and thread-safety under
concurrent writers. Those are claims about this module's own logic, not about
Docker/nginx wiring — the wiring itself (docker-compose.e2e.yml, the
webhooks.adcp.test nginx alias, dynamic port allocation) is re-verified by the
existing e2e suites in-network per acceptance criterion #5 ("no new unit
tests" for that part), not pinned here.

Runs with plain ``pytest tests/e2e/test_webhook_capture_service.py`` — it does
NOT use the ``live_server``/``docker_services_e2e`` fixtures, so it needs no
Docker stack.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPResponse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

# This import is the TDD red: tests/e2e/webhook_capture_service.py does not
# exist yet (it is created by the implementation atom, GH #1802).
from tests.e2e.webhook_capture_service import run_capture_service

_TIMEOUT_SECONDS = 5.0


def _request(base_url: str, method: str, path: str, *, body: dict | None = None) -> tuple[int, dict]:
    """Issue one HTTP request against the running capture service and decode the JSON reply."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(f"{base_url}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        resp: HTTPResponse = urlopen(req, timeout=_TIMEOUT_SECONDS)  # noqa: S310 - test-only, loopback origin
        status = resp.status
        raw = resp.read()
    except HTTPError as exc:
        status = exc.code
        raw = exc.read()
    return status, (json.loads(raw) if raw else {})


def _post(base_url: str, key: str, payload: dict) -> tuple[int, dict]:
    return _request(base_url, "POST", f"/webhook/{key}", body=payload)


def _get(base_url: str, key: str) -> tuple[int, dict]:
    return _request(base_url, "GET", f"/webhook/{key}")


def _delete(base_url: str, key: str) -> tuple[int, dict]:
    return _request(base_url, "DELETE", f"/webhook/{key}")


@pytest.fixture
def capture_service(monkeypatch) -> Iterator[str]:
    """Run the capture service on an ephemeral loopback port for the duration of one test."""
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "amht3-test-stack")
    with run_capture_service(host="127.0.0.1", port=0) as base_url:
        yield base_url


class TestHealthReportsOwnStack:
    """GET /health identifies which compose stack this instance belongs to.

    Per amht.3's plan step 1: "GET /health (returns 200 plus the service's
    COMPOSE_PROJECT_NAME so a readback client can assert it is talking to ITS
    OWN stack, not a cross-wired sibling)".
    """

    def test_health_returns_this_process_compose_project_name(self, capture_service):
        status, body = _request(capture_service, "GET", "/health")

        assert status == 200
        assert body["compose_project_name"] == "amht3-test-stack"


class TestPerKeyIsolation:
    """Captures are keyed by the URL path segment, never a single global list.

    Per the Findings section of amht.3: "a dict-of-lists keyed by <key>
    server-side, never a single global list" — this is the strict isolation
    requirement four concurrent e2e modules under xdist depend on.
    """

    def test_get_on_fresh_key_is_empty(self, capture_service):
        key = uuid.uuid4().hex

        status, body = _get(capture_service, key)

        assert status == 200
        assert body["received"] == []

    def test_post_then_get_returns_the_posted_payload(self, capture_service):
        key = uuid.uuid4().hex
        payload = {"event": "delivery_report", "media_buy_id": "mb_1"}

        post_status, _ = _post(capture_service, key, payload)
        get_status, body = _get(capture_service, key)

        assert post_status == 200
        assert get_status == 200
        assert body["received"] == [payload]

    def test_two_keys_never_see_each_others_captures(self, capture_service):
        key_a = uuid.uuid4().hex
        key_b = uuid.uuid4().hex

        _post(capture_service, key_a, {"for": "a"})
        _post(capture_service, key_b, {"for": "b"})
        _post(capture_service, key_a, {"for": "a-again"})

        _, body_a = _get(capture_service, key_a)
        _, body_b = _get(capture_service, key_b)

        assert body_a["received"] == [{"for": "a"}, {"for": "a-again"}]
        assert body_b["received"] == [{"for": "b"}]


class TestAtomicDrainOnDelete:
    """DELETE drains and returns the list in ONE round trip, not list-then-clear as two calls.

    Per amht.3's plan step 1 and the architect review's concurrency finding:
    "make DELETE return the drained list so read-and-clear is one atomic round
    trip". test_adcp_reference_implementation.py's drain-to-quiescence loop
    depends on this: a POST landing between a GET and a separate clear call
    must never be silently lost.
    """

    def test_delete_returns_exactly_what_was_captured(self, capture_service):
        key = uuid.uuid4().hex
        _post(capture_service, key, {"n": 1})
        _post(capture_service, key, {"n": 2})

        status, body = _delete(capture_service, key)

        assert status == 200
        assert body["received"] == [{"n": 1}, {"n": 2}]

    def test_get_after_delete_is_empty(self, capture_service):
        key = uuid.uuid4().hex
        _post(capture_service, key, {"n": 1})
        _delete(capture_service, key)

        _, body = _get(capture_service, key)

        assert body["received"] == []

    def test_delete_on_a_key_with_no_captures_returns_empty_not_error(self, capture_service):
        key = uuid.uuid4().hex

        status, body = _delete(capture_service, key)

        assert status == 200
        assert body["received"] == []

    def test_post_after_delete_starts_a_fresh_list(self, capture_service):
        """Draining a key must not poison it for subsequent captures within the same test."""
        key = uuid.uuid4().hex
        _post(capture_service, key, {"n": 1})
        _delete(capture_service, key)

        _post(capture_service, key, {"n": 2})
        _, body = _get(capture_service, key)

        assert body["received"] == [{"n": 2}]


class TestThreadSafetyUnderConcurrentWriters:
    """No lost writes under concurrent POSTs, and no lost writes racing a DELETE drain.

    Per the architect review's [MEDIUM] concurrency finding: ThreadingHTTPServer
    serves each request on its own thread, so ``d.setdefault(k, []).append(v)``
    must not be the only safety net — an explicit ``threading.Lock`` is
    required. This test is the one genuinely at risk of flaking if the
    eventual implementation relies on GIL atomicity alone under real socket
    I/O (which, unlike a pure-Python append, actually interleaves).
    """

    def test_concurrent_posts_to_the_same_key_are_all_recorded(self, capture_service):
        key = uuid.uuid4().hex
        num_writers = 20
        posts_per_writer = 10
        expected_total = num_writers * posts_per_writer

        def _write_one_worker(worker_id: int) -> list[int]:
            statuses = []
            for i in range(posts_per_writer):
                status, _ = _post(capture_service, key, {"worker": worker_id, "seq": i})
                statuses.append(status)
            return statuses

        with ThreadPoolExecutor(max_workers=num_writers) as pool:
            results = list(pool.map(_write_one_worker, range(num_writers)))

        assert all(status == 200 for statuses in results for status in statuses)

        _, body = _get(capture_service, key)
        assert len(body["received"]) == expected_total
        # Every (worker, seq) pair must appear exactly once - a lost or duplicated
        # write under concurrency would show up as a missing/extra tuple here.
        seen = {(entry["worker"], entry["seq"]) for entry in body["received"]}
        assert seen == {(w, i) for w in range(num_writers) for i in range(posts_per_writer)}

    def test_concurrent_posts_across_different_keys_do_not_cross_contaminate(self, capture_service):
        num_keys = 10
        keys = [uuid.uuid4().hex for _ in range(num_keys)]

        def _write_one_key(key: str) -> None:
            for i in range(5):
                _post(capture_service, key, {"tag": key, "seq": i})

        with ThreadPoolExecutor(max_workers=num_keys) as pool:
            list(pool.map(_write_one_key, keys))

        for key in keys:
            _, body = _get(capture_service, key)
            assert len(body["received"]) == 5
            assert all(entry["tag"] == key for entry in body["received"])


class TestProgrammedRejections:
    """POST /control/{key} makes the service reject the next N deliveries to that key.

    THIS IS A SUB-CLAIM, NOT THE REGRESSION TEST FOR GH #1802. It grades
    the INSTRUMENT — whether the capture service can be made to fail on demand — and
    an instrument that works is not evidence that the thing it measures works. It
    earns its place for the opposite reason: a BROKEN instrument manufactures false
    greens, which is the exact disease this ticket exists to kill, so the instrument
    is pinned before anything is measured with it.

    What the circuit breaker actually does on the deployed server is graded by the
    e2e_rest leg of @T-UC-004-webhook-circuit-open, and whether that grading is real
    is settled by scripts/mutation-check-webhook-breaker.sh. Nothing in this file
    speaks to either.

    GH #1802 plan step 2. Nothing in the compose stack can currently
    make the deployed server's webhook delivery FAIL: this service answers every
    delivery 200, so the server's circuit breaker never records a failure and the
    two circuit-breaker scenarios have no way to open it for real. A per-key
    failure programme is what turns "the endpoint has failed 5 consecutive
    delivery attempts" from a dictionary seeded in the test process into
    something the deployed server actually experiences.

    Two invariants are load-bearing for the scenarios that will use it, and both
    are graded below:

    * A REJECTED request is still RECORDED. The Then steps count what the
      endpoint received; a rejection that vanished from the captures would make
      the five real failures indistinguishable from five suppressed deliveries.
    * The programme is CONSUMED PER REQUEST, so the key falls back to 200 once
      it is spent. That fallback is what "the webhook endpoint has recovered and
      returns 200" becomes.

    The status used here is 418 rather than a 5xx on purpose (plan step 3): a
    terminal 4xx costs exactly one request per breaker failure, while a
    retryable 5xx multiplies the count by the attempt schedule and adds seconds
    of backoff per failure (``_RETRYABLE_STATUSES``,
    ``src/core/security/egress/attempts.py``). The route itself takes whatever
    status it is given -- the choice belongs to the caller.
    """

    def _control(self, base_url: str, key: str, *, status: int, count: int) -> tuple[int, dict]:
        return _request(base_url, "POST", f"/control/{key}", body={"status": status, "count": count})

    def test_programmed_status_is_answered_exactly_count_times_then_200(self, capture_service):
        key = uuid.uuid4().hex

        control_status, _ = self._control(capture_service, key, status=418, count=3)
        answers = [_post(capture_service, key, {"n": i})[0] for i in range(5)]

        assert control_status == 200
        assert answers == [418, 418, 418, 200, 200]

    def test_a_rejected_delivery_is_still_recorded_as_a_capture(self, capture_service):
        key = uuid.uuid4().hex
        self._control(capture_service, key, status=418, count=2)

        answers = [_post(capture_service, key, {"n": n})[0] for n in (1, 2, 3)]

        # Both halves are asserted together on purpose: "everything was recorded"
        # is trivially true of a service that rejected nothing.
        assert answers == [418, 418, 200]
        _, body = _get(capture_service, key)
        assert body["received"] == [{"n": 1}, {"n": 2}, {"n": 3}]

    def test_one_keys_programme_never_answers_for_another_key(self, capture_service):
        programmed = uuid.uuid4().hex
        untouched = uuid.uuid4().hex
        self._control(capture_service, programmed, status=418, count=2)

        programmed_answers = [_post(capture_service, programmed, {"for": "programmed"})[0] for _ in range(2)]
        untouched_answers = [_post(capture_service, untouched, {"for": "untouched"})[0] for _ in range(2)]

        assert programmed_answers == [418, 418]
        assert untouched_answers == [200, 200]

    def test_the_control_request_is_not_itself_recorded_as_a_capture(self, capture_service):
        """``do_POST`` routes every POST to the webhook handler; the control route must be matched first.

        Otherwise ``POST /control/<key>`` lands in the store as a delivery and
        corrupts the very count the circuit-breaker Then steps read back.
        """
        key = uuid.uuid4().hex

        control_status, _ = self._control(capture_service, key, status=418, count=1)
        _, body = _get(capture_service, key)

        # The 200 is asserted here too: an unrouted control POST answers 404 and
        # records nothing either, which would let this pass while proving nothing.
        assert control_status == 200
        assert body["received"] == []

    def test_reprogramming_a_key_replaces_the_remaining_programme(self, capture_service):
        """A scenario that opens the breaker and then lets the endpoint recover reprograms the same key."""
        key = uuid.uuid4().hex
        self._control(capture_service, key, status=418, count=5)
        spent_under_the_first_programme, _ = _post(capture_service, key, {"n": 1})

        self._control(capture_service, key, status=200, count=0)
        answers = [_post(capture_service, key, {"n": i})[0] for i in range(2)]

        # Without the first assertion, a service that never rejected anything
        # would satisfy "the replacement programme answers 200".
        assert spent_under_the_first_programme == 418
        assert answers == [200, 200]

    def test_a_drained_key_keeps_its_unspent_programme(self, capture_service):
        """DELETE drains captures. It is a readback of what arrived, not a reset of how the endpoint answers."""
        key = uuid.uuid4().hex
        self._control(capture_service, key, status=418, count=3)
        _post(capture_service, key, {"n": 1})

        _delete(capture_service, key)
        answers = [_post(capture_service, key, {"n": i})[0] for i in range(3)]

        assert answers == [418, 418, 200]
