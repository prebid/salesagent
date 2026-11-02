# Webhook testing architecture

This document tells you how to write a test that grades outbound delivery —
webhooks, protocol notifications, approval callbacks, and MCP calls to another
agent. Read it before writing a test that otherwise patches an HTTP client.

The one rule behind everything here: **a test never patches the outbound
transport.** Delivery runs through
`src/core/security/outbound_http.py` (`send` / `asend`), and the test stands up
a real HTTP server on loopback for it to talk to. The assertions — how many
requests arrived, with which headers, carrying which bytes — then mean the same
thing whichever client library the seam uses, and they keep meaning it after the
seam changes. That neutrality is why the fronts exist:
`tests/harness/_mixins.py:423-451` records the two envs that used to patch
`requests.post` and `httpx.Client` instead, and what that cost.

## Where the pieces live

| File | What it gives you |
|---|---|
| `tests/helpers/local_http_origin.py` | The programmable HTTP origin: `run_local_origin()`, `LocalOrigin`, the response-mode factories |
| `tests/helpers/local_mcp_origin.py` | `run_mcp_origin()` — a real `fastmcp` server over TLS, with a per-tool invocation counter |
| `tests/helpers/test_tls_material.py` | The only way to reach the TLS generator: `load_gen_test_tls()`, `server_ssl_context()` |
| `scripts/dev/gen_test_tls.py` | Generates and refreshes the one certificate authority (CA) and leaf every in-process TLS front reuses |
| `tests/integration/conftest.py:85-144` | The three fixtures: `local_origin`, `local_origin_tls`, `mcp_origin_tls` |
| `tests/harness/_mixins.py:423-617` | `LocalOriginMixin` — the origin as an env capability, plus the webhook-test surface |
| `tests/harness/_mixins.py:619-671` | `WebhookOutcomeRowsMixin` — reading back the delivery-log rows a sender wrote |
| `tests/harness/_mixins.py:753-1060` | `CircuitBreakerMixin` — driving `WebhookDeliveryService` and its breaker |
| `tests/harness/egress.py` | `EgressHatchMixin` (hatch posture) and `FastOutboundBackoffMixin` (shorter backoff base) |
| `tests/helpers/egress_hatches.py` | `ADCP_OUTBOUND_ALLOW_PRIVATE`, spelled once |
| `tests/helpers/hmac_assertions.py` | Signature grading over the raw received bytes |
| `tests/helpers/backoff_assertions.py` | Retry-spacing grading against one `(1.0, 2.0, 4.0)` constant |
| `tests/e2e/_webhook_capture.py`, `tests/e2e/webhook_capture_service.py` | The e2e receiver: a long-lived compose service with per-scenario capture keys |

## The programmable loopback origin

`run_local_origin()` (`tests/helpers/local_http_origin.py:579-601`) is a context
manager that binds a socket on loopback, serves `ProgrammableOriginHandler` on a
daemon thread, and yields a `LocalOrigin` — one object carrying both the control
surface (what the origin answers) and the request log (what it received).

```python
from tests.helpers.local_http_origin import run_local_origin

with run_local_origin() as origin:
    origin.respond_with(200)
    ...                                  # drive production against origin.base_url
    assert origin.hits == 1
    assert origin.last_request.json() == {"event": "delivery.update"}
```

### The port is always ephemeral

`serve_in_thread` defaults `port=0` and reads the kernel-assigned value back off
`server.server_address` (`local_http_origin.py:88-92`, `:119`, `:600`). It never
probes for a free port and rebinds it: the integration and BDD suites run under
xdist, and the probe-close-rebind form races another worker between the close and
the rebind. A fixed port is for a long-lived service with a well-known address —
the e2e capture service, not a test origin.

Two more details exist so the origin does not manufacture failures unrelated to
what you are grading:

- **Address family follows the first resolution of the listen host**
  (`:58-73`). The origin chooses the server class with the same
  `getaddrinfo(host, None)` first-answer call the egress IP pin makes. On a
  resolver that orders `::1` first, an `AF_INET`-only server sits on
  `127.0.0.1` while the pinned client dials `[::1]` and gets a connection
  refused.
- **The origin passes a listen backlog of 128 as a constructor parameter**
  (`:98-105`, `:114-118`). `socketserver` reads `request_queue_size` at bind
  time, so a post-construction `setattr` is too late; its default of 5 produces
  `ConnectionResetError` under a burst of concurrent writers on a many-core
  machine and passes on a quiet one.

### The five response modes

You program the origin through the `LocalOrigin` methods below. The handler
dispatches on `mode` through a table (`:562-568`), so an unrecognized mode raises
at the origin instead of falling through to `fixed` and quietly grading the wrong
behavior.

| Mode | Program it with | What it produces, and what it is for |
|---|---|---|
| `fixed` | `respond_with(status, body=..., content_type=..., headers=...)` (`:303-322`) | One complete, length-declared response. `headers` is how a fixed origin says what a status cannot — `{"Retry-After": "5"}` on a 429 is the origin instructing the caller |
| `redirect` | `redirect_to(location, status=302)` (`:411-415`) | A `Location` to an arbitrary URL, a blocked one included. The seam never follows one — it relies on httpx's `follow_redirects=False` default and the guarded client factory sets it explicitly (`src/core/security/outbound_http.py:105`, `:773`) — so `hits == 1` is the grade for "the redirect was not followed" |
| `chunked` | `respond_chunked(total_bytes, chunk_size=...)` (`:417-427`) | `Transfer-Encoding: chunked` of `total_bytes`. An undeclared body length is what forces a reader to accumulate and then stop reading, so this is the mode that grades the seam's 10 MiB response cap (`src/core/security/outbound_http.py:256`) |
| `malformed` | `sends_malformed_body()` (`:246-255`) as a sequence entry | Valid headers, then a chunk-size line that is not a number. Clients classify this differently from a connection failure, so a retry policy that only handles connection failures drops it |
| `error` | `set_http_error()` on an env, or `close_without_responding()` (`:400-409`) | The origin records the hit, then closes the connection before a status line. The client raises a genuine transport error of its own making |

`respond_with` and `responds(...)` refuse any header the origin writes for
itself — `content-type`, `content-length`, `connection`, `transfer-encoding`,
`location` (`:35-42`, `:187-197`). Emitting one twice is a framing error the
client reports as a protocol failure, so the origin refuses it at programming
time and you use the matching `OriginResponse` field instead.

### Program a sequence, and why the last entry repeats

`respond_in_sequence(responses)` (`:324-353`) answers each request with the next
entry and repeats the last one forever. An entry is either a `(status, body)`
pair or a full `OriginResponse` built by `responds(...)`, `hangs_up()`,
`sends_chunked_body(...)`, or `sends_malformed_body()` (`:200-255`) — so a
sequence can express recovery from a real fault, not only from an unhappy
status.

The repeating last entry is what lets you program the recovery point without
knowing how many attempts the caller makes:

```python
from tests.helpers.local_http_origin import hangs_up, responds

origin.respond_in_sequence([hangs_up(), responds(200)])
# whatever the retry policy does, attempt 1 gets a real transport fault and
# every later attempt gets a 200 — and `origin.hits` says how many there were.
```

### Read back what arrived

The origin records the hit **before** it stalls or fails (`:474-484`,
`:400-409`), so a request that times out or gets no answer still counts. Exact
counts are the grade for the claims no value assertion can make — "the redirect
was not followed", "a 404 was not retried", "no further attempt arrived".

| Accessor | Answers |
|---|---|
| `origin.hits` (`:294-297`) | How many requests arrived |
| `origin.paths` (`:299-301`) | The path of each, in order |
| `origin.requests` | Every `OriginRequest`, oldest first |
| `origin.last_request` (`:442-446`) | The most recent one, or a failed assertion naming the silence |
| `origin.payloads()` (`:448-450`) | Every body decoded as JSON |
| `request.json()` (`:148-156`) | The body that crossed the socket, decoded — not the object a caller passed |
| `request.headers` (`:133-141`) | The raw `http.client.HTTPMessage`, so lookups are case-insensitive as on the wire |
| `request.body` | The raw bytes, which is what you must recompute a signature over |

One caution if your production code patches its own `time.sleep`: the origin
binds `sleep` at import (`:29`, `:44-49`). `import time` yields one shared
module object, so a call-time lookup lets a caller's patch cancel the
origin's stall — and a stall that does not stall cannot trip a timeout.

## The TLS sibling and which CA to trust

The egress seam requires https unconditionally, so every front that production
dials in a test serves real TLS. All of them reuse **one** generated CA and leaf,
produced by `scripts/dev/gen_test_tls.py` into `.test-tls/` and reached only
through `tests/helpers/test_tls_material.py` — `load_gen_test_tls()` imports the
script by file path (it lives outside any package) and `server_ssl_context()`
builds a `PROTOCOL_TLS_SERVER` context from `SERVER_CERT` / `SERVER_KEY`
(`test_tls_material.py:21-36`). Never generate a second certificate.

The leaf covers `adcp.test`, `*.adcp.test`, `localhost`, `agent.localhost`,
`*.localhost`, and the IP subject alternative names (SANs) `127.0.0.1`,
`127.0.0.2`, and `::1` (`gen_test_tls.py:86-97`). The second loopback address is deliberate: two origins
differing only by port share one pinned address, so proving a per-host IP pin
pins per host needs two different addresses.

### Choose the bundle by what the client is checking

There are two trust anchors, and using the wrong one breaks things that have
nothing to do with your test (`gen_test_tls.py:57-73`):

- **`COMBINED_CERT` (system bundle + private CA) wherever you set
  `SSL_CERT_FILE`.** That variable replaces the process's entire default
  cafile, so a private-CA-only bundle breaks every real HTTPS connection the
  same process makes.
- **`CA_CERT` (private CA alone) for `--cacert` flags and `E2E_CA_BUNDLE`.** A
  caller checking one of this stack's own endpoints should trust only this
  stack's leaf.

### The material refreshes itself

`ensure_test_tls()` (`:265-283`) is idempotent and regenerates on three
independent conditions, each checked because "the file exists" is not "the file
works" (`:188-206`): missing or unparseable material, expiry within 7 days, or a
SAN set that no longer matches `SAN_DNS_NAMES`. The whole check-and-regenerate
sequence runs under an `fcntl.flock` file lock and every write is a temp file
plus `os.replace` (`:156-173`, `:239-262`) — xdist workers are separate
processes, and either a torn read or an interleaved regeneration yields a leaf
that no longer chains to the CA on disk.

## The MCP-over-TLS origin

An HTTP origin grades address, redirect, and pinning behavior, because the
client makes those decisions before it exchanges a single MCP frame. Grading
what happens **after** a successful handshake takes more: a tool-level failure
only exists once the client has initialized a session and issued `tools/call`,
which means the origin has to speak MCP for real.
`run_mcp_origin()` (`tests/helpers/local_mcp_origin.py:73-117`)
serves a real `fastmcp` server through uvicorn over TLS on an ephemeral loopback
port, using the same generated leaf.

Two properties are worth knowing:

- **The base URL ends in `/mcp`** (`:112`, `:80-85`), so the MCP seam
  synthesizes no fallback candidate — one URL means one attempt budget, and an
  attempt count means what the test says it means.
- **The origin records each invocation before the handler can fail** (`:53-70`),
  and `origin.invocations` / `origin.invocations_of(tool)` expose it. That
  counter is what separates "the client redialed" from "the client re-executed
  the tool body" on a retry; no assertion about the returned value can tell
  those apart.

## The three integration fixtures

| Fixture | Scheme | Notes |
|---|---|---|
| `local_origin` | http | Depends on no database — an egress test needs a remote, not a DB. Use it for refusal cases, where the point is that nothing ever dials the URL |
| `local_origin_tls` | https | Sets `SSL_CERT_FILE` to `COMBINED_CERT` by monkeypatch (function-scoped) and serves the generated leaf with verification on |
| `mcp_origin_tls` | https | Same trust, but yields a **factory**, not a started origin: only the test knows whether its tool succeeds, fails, or fails once |

All three are in `tests/integration/conftest.py:85-144`.

Loopback is a private-range address, which the seam refuses by default, so a
test using these fixtures opens the hatch explicitly:
`set_flags(monkeypatch, private=True)` (`tests/integration/test_outbound_http.py:113-129`),
which writes the one name spelled in `tests/helpers/egress_hatches.py:29`.
`set_flags` always writes the value, the off case included — a hatch left unset
is a hatch decided by whatever exported it into the shell.

There is no scheme hatch. `ADCP_OUTBOUND_ALLOW_INSECURE` is inert: production
reads it nowhere, and `egress_hatch_env()` has no `insecure` parameter because
there is nothing left to relax (`egress_hatches.py:18-24`, `:46-58`). A test that
needs to reach an origin needs a TLS one.

### A worked MCP retry test

```python
async def test_transient_tool_failure_is_retried(self, mcp_origin_tls, monkeypatch, recorded_retry_sleeps):
    set_flags(monkeypatch, private=True)

    outcomes = iter([ValueError("transient tool failure"), {"served_on_attempt": 2}])

    def flaky() -> dict:
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    origin = mcp_origin_tls(flaky=flaky)

    result = await call_mcp_tool(agent_url=origin.base_url, tool="flaky", arguments={}, timeout=10, max_attempts=3)

    assert result.structured_content == {"served_on_attempt": 2}
    assert origin.invocations == ["flaky", "flaky"]   # the body re-ran, not just the dial
    assert len(recorded_retry_sleeps) == 1
```

The live version is `tests/integration/test_mcp_client_util.py:459-496`;
`recorded_retry_sleeps` (`:252-280`) records the sleeps of both modules that
could hold the wait, so the grade is about the durations wherever the sleep
executes.

## Drive a delivery from an env

`LocalOriginMixin` (`tests/harness/_mixins.py:423-617`) makes the origin a
capability of a harness env. Compose it and your env gets a running TLS origin, a
`webhook_url` that really answers, the programming setters, and the read-back
accessors.

### What it acquires, in order

`_enter_pre` (`:490-525`) runs **before** the base binds the database and
configures mocks, because `CircuitBreakerEnv._configure_mocks` programs the
origin. The order is: `ensure_test_tls()`, patch `SSL_CERT_FILE` to
`COMBINED_CERT`, start the TLS origin, open the private-range hatch. `_enter_pre`
registers each resource with `_guard` (`tests/harness/_base.py:1372-1382`) on the
line that acquires it, so a failure part-way through releases exactly what
started, newest first — a failed `__enter__` leaves nothing behind.

The mixin scopes `SSL_CERT_FILE` to the origin's lifetime rather than leaving it
ambient, and points it at the combined bundle so other outbound work in the same
scenario keeps trusting real public roots (`:440-450`).

### The webhook-test surface

| Member | Does |
|---|---|
| `env.webhook_url` (`:538-546`) | The endpoint every webhook test targets — `https://127.0.0.1:<port>/webhook` in process |
| `env.set_http_status(code, text="")` (`:568-571`) | Answer every attempt with `code` |
| `env.set_http_sequence([...])` (`:573-587`) | Answer attempts in order, last entry repeating; entries are `(status, text)` pairs or full `OriginResponse` values |
| `env.set_http_error()` (`:589-591`) | Accept every attempt and drop the connection without answering |
| `env.reject_next(count, status=418)` (`:550-564`) | Fail the next `count` deliveries, then succeed |
| `env.delivery_attempts` (`:595-604`) | How many requests the endpoint received |
| `env.delivered_requests` (`:606-610`) | Every request, oldest first |
| `env.last_delivery` (`:612-616`) | The most recent request |
| `env.origin` (`:455-479`) | The `LocalOrigin` itself — in process only; under e2e it raises a named error pointing at the accessors above |

`reject_next` defaults to a terminal, non-retryable 418 on purpose. A retryable
5xx (`_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}`,
`src/core/security/egress/attempts.py:44`) multiplies the request count by the
attempt schedule and adds real backoff per failure, so "the endpoint received
exactly 5 attempts" stops being countable.

### Which env for which production entry point

Pick the env by the function you mean to drive. None of them patches the
outbound transport.

| Env | Drives | Patches |
|---|---|---|
| `WebhookEnv` (`tests/harness/delivery_webhook.py:35-59`) | `deliver_webhook_with_retry` (`src/core/webhook_delivery.py:80`), through `env.call_deliver(...)` | The seam's `time.sleep` only |
| `CircuitBreakerEnv` (`tests/harness/delivery_circuit_breaker.py:56-172`) | `WebhookDeliveryService.send_delivery_webhook` (`env.call_send`) and `_send_webhook_enhanced` (`env.call_send_enhanced`) | The seam's `time.sleep` and the attempts module's `random.uniform` |
| `ProtocolWebhookEnv` (`tests/harness/protocol_webhook.py:76-216`) | `ProtocolWebhookService.send_notification`, through `await env.send(...)` | Nothing |
| `OrderApprovalWebhookEnv` (`tests/harness/order_approval_webhook.py:48-101`) | `_send_approval_webhook`, through `env.call_send_approval_webhook(...)` | Nothing |
| `MediaBuyPushRegistrationEnv` (`tests/harness/webhook_registration.py:75-259`) | The whole path from a buyer's `create_media_buy` / `update_media_buy` registration to the workflow step's delivery, driven by `env.complete_step(step)` | Inherited from the media-buy envs |

`ProtocolWebhookEnv` keeps **one** service instance per env (`:107-119`) because
"one service, two destinations" is only a statement a test can make if the
deliveries share an instance.

### Two composition rules

- **`FastOutboundBackoffMixin`** (`tests/harness/egress.py:89-129`) shortens the
  seam's backoff **base** to 0.01s for the env's lifetime, overriding neither the
  shape nor the jitter. Only an env whose tests do not observe the seam's sleep
  may compose it. `ProtocolWebhookEnv` and `OrderApprovalWebhookEnv` do; the two
  delivery envs must not, because they mock the seam's `time.sleep` and grade
  magnitudes against the `(1.0, 2.0, 4.0)` constant, so a shortened base
  silently invalidates those assertions rather than failing them.
- **`EgressHatchMixin.set_egress_hatches(private=...)`**
  (`tests/harness/egress.py:70-86`) is how an env that grades a refusal states
  its posture. It patches `get_settings().limits.adcp_outbound_allow_private` on
  the settings object, not the environment: the settings loader reads the
  environment once at startup, so an env-level `os.environ` patch is invisible
  mid-run.

### The shape of a delivery test

Enter the env, create the rows the delivery needs, program the origin, call
production, then assert on the returned verdict, the attempt count, and facts
read off the request that arrived.

```python
@pytest.mark.requires_db
async def test_exhausted_delivery_records_every_attempt(self, integration_db):
    with ProtocolWebhookEnv() as env:
        buy = env.make_media_buy()                    # the delivery log's FK target
        env.set_http_status(500, "boom")

        delivered = await env.send(media_buy_id=buy.media_buy_id)

        assert delivered is False
        assert env.delivery_attempts == MAX_ATTEMPTS
        rows = env.delivery_logs(buy.media_buy_id)
        assert rows[0].attempt_count == env.delivery_attempts
```

`make_media_buy` (`_mixins.py:634-645`) matters more than it looks:
`webhook_delivery_log.media_buy_id` references `media_buys`, and the writers
swallow the integrity error and log it — so a delivery-log assertion against a
media buy that does not exist grades zero rows and no exception.

## Assert a delivery

### Signatures, over the bytes that arrived

Recompute the HMAC over `request.body` — the raw bytes off the socket — never
over a fresh serialization of the payload dict. A recompute from the dict uses
whatever formula the test happens to pick, and can agree with a sender that
signed one serialization and transmitted another; that was a real defect, and a
local origin that does not check signatures cannot see it. Production
serializes once and transmits those exact bytes via `content=`
(`src/core/security/webhook_egress.py:176-226`).

`tests/helpers/hmac_assertions.py` holds the check once:

- `assert_signature_verifies_over_wire_body(request, secret)` (`:28-77`) —
  verifies `sha256=<hex>` over `f"{timestamp}." + request.body`. It names a
  missing header explicitly (`:59-63`) rather than raising `AttributeError` on
  `None`, so a delivery that went out entirely unsigned reports as the security
  failure it is.
- `assert_delivered_unsigned(env)` (`:80-105`) — the negative twin. It asserts
  `delivery_attempts == 1` as well, because "no signature header" is vacuously
  true of a request that never happened.
- `SIGNATURE_HEADER` / `TIMESTAMP_HEADER` (`:24-25`) are the two header names
  AdCP 3.1.1 pins. Use the constants: a second string literal keeps passing
  after a header rename, which is the one way an absence assertion stops
  grading.

A non-ASCII payload is the honest input for this property: compact-but-
`ensure_ascii=False` serialization matches the signature for ASCII input and
diverges here, so only a non-ASCII body grades "transmit exactly the signed
bytes" rather than coincidental agreement
(`tests/integration/test_protocol_webhook_egress.py`, `TestSignedBodyIntegrity`).

### Retry spacing, by magnitude

`tests/helpers/backoff_assertions.py` grades the 1s/2s/4s schedule from one
constant, `BR_RULE_029_BASE_DELAYS` (`:19`). Magnitudes, not ratios: a ratio
check passes for any geometric schedule, so 0.1/0.2/0.4 satisfies it while
breaking the invariant.

`assert_backoff_schedule(durations, jitter=...)` (`:29-78`) takes the jitter
treatment from the caller:

- `jitter=<float>` when the env pins `random.uniform` — each delay must equal its
  base plus exactly that offset;
- `jitter=None` when the draw is live — each delay must fall in
  `[base, base + 1)` **and** at least one must differ from its base, which is
  what proves randomization applies.

`assert_backoff_schedule` grades a short run (two sleeps for three attempts)
against the matching prefix, never padding it.

### What the sender wrote down

`WebhookOutcomeRowsMixin.recorded_outcomes(media_buy_id, task_type=..., status=None)`
(`_mixins.py:647-671`) reads the `webhook_delivery_log` rows through
`DeliveryRepository` rather than a raw `select()`. Two things are not optional:

- **`task_type` is required.** `media_buy_delivery.py` also writes
  `task_type="delivery_poll"` success rows on the same `media_buy_id`, so an
  unfiltered read lets "the sender recorded a success" pass on a row no sender
  wrote.
- **The read calls `session.expire_all()` first**, because senders commit through
  their own `get_db_session()` and the env-bound session otherwise answers
  from its identity map. `ProtocolWebhookEnv.delivery_logs` (`:207-215`) does the
  same.

### Payload fields live under `result`

For a delivery report, read the report through
`CircuitBreakerMixin.delivered_result(request)` (`_mixins.py:758-773`), not off
the top level of the body: AdCP 3.1.1 puts the report under `result` and says it
is not valid as the top-level POST body by itself.

## Exercise the circuit breaker

Production keys a breaker per endpoint, `f"{tenant_id}:{config.url}"`
(`src/services/webhook_delivery_service.py:388-392`), with three states
(`:74-80`) and thresholds read from settings by `_configured_breaker()`
(`:64-71`; the class defaults are 5 failures, 2 successes, 60s). `can_attempt()`
is not a pure read: an OPEN breaker whose timeout has elapsed becomes HALF_OPEN
inside it (`:108-132`). `record_failure` opens a CLOSED breaker at the failure
threshold and re-opens a HALF_OPEN one immediately (`:149-166`);
`record_success` closes a HALF_OPEN breaker after `success_threshold` successes
(`:134-147`).

The harness owns **one** private-state touch, `_breaker_for(endpoint_key)`
(`_mixins.py:845-854`), and every seeding helper goes through it, so there is one
line to audit:

| Helper | Does | Why that form |
|---|---|---|
| `env.endpoint_key(tenant_id=None)` (`:822-829`) | Builds production's key for this origin | The port is only known at runtime, so a test cannot spell the key as a literal |
| `env.seed_breaker_failures(key, n)` (`:856-866`) | Calls `record_failure` n times | The breaker decides what n failures mean; assigning `failure_count` asserts against the test's own arithmetic |
| `env.set_breaker_state(key, "OPEN")` (`:868-875`) | Forces a starting state | A Given that names a state describes where the scenario starts |
| `env.elapse_breaker_timeout(key, seconds=61)` (`:877-885`) | Ages the last failure | Moves the clock, not the state, so production still performs the transition |
| `env.drive_breaker_transition(key)` (`:887-907`) | Calls `can_attempt()` for its side effect and discards the verdict | Asserting the verdict grades the gate's opinion of itself, which is unfalsifiable across a process boundary |
| `env.breaker_snapshot(url=None)` (`:909-917`) | `(state, failure_count)` through `get_circuit_breaker_state` | The only read path: a private-dict read could report state production cannot surface |

Then observe the consequence at the endpoint, not the breaker: with the breaker
OPEN, a further send returns `False` and `env.delivery_attempts` does not move
(`tests/integration/test_delivery_service_behavioral.py`,
`test_service_skips_delivery_when_circuit_open`).

A BDD step may not touch breaker internals at all.
`tests/unit/test_architecture_bdd_wire_discipline.py:909-924`
(`test_no_private_circuit_breaker_state_in_steps`) forbids any step under
`tests/bdd/steps/` from indexing `service._circuit_breakers`, with a permanently
empty allowlist: breaker state is process-local and therefore unfalsifiable
across a process boundary, so a scenario claiming deliveries happen asserts the
delivery effect instead.

## The same scenario over e2e

A webhook scenario stays transport-independent because `@realize_e2e`
(`tests/harness/_realize.py:60-91`) wraps `LocalOriginMixin`'s members and
dispatches on `env.is_e2e` (`tests/harness/_base.py:434-444`) to a
per-method e2e realization instead of branching inside a step.

Under e2e no local origin starts — such an origin listens on the runner's
loopback, which the Docker server cannot reach. The env claims a fresh
per-scenario capture key instead (`_mixins.py:502-511`), and the endpoint
becomes the compose stack's long-lived `webhook-capture` service
(`docker-compose.e2e.yml:391-407`), fronted by the shared TLS proxy at the
`webhooks.adcp.test` alias (`:343-353`).

**Two traffic patterns, never conflated** (`tests/e2e/_webhook_capture.py:9-18`):

- delivery goes to `https://webhooks.adcp.test:8443/webhook/<key>` through the
  TLS front (`:39`, `:70-76`);
- readback and rejection programming go over the service's plain-HTTP control
  plane — `GET`/`DELETE /webhook/<key>` and `POST /control/<key>`
  (`:79-90`, `:105-134`).

Isolation is a per-scenario opaque key. `register_capture_key()` (`:137-149`)
asserts that the readback port belongs to this `COMPOSE_PROJECT_NAME` before
handing back a fresh uuid key and a drained `ReceivedView`. `ReceivedView` holds
no local cache, so every read is a fresh round trip, and `clear()` drains the key
server-side atomically (`webhook_capture_service.py:115-118`) so a capture
landing between two calls is never lost. A readback transport error raises
`WebhookReadbackError` (`tests/e2e/_webhook_capture.py:42-49`) rather than
degrading to an empty list, which reads as "no webhook arrived".

### What the control plane can and cannot express

The e2e realizations state exactly what the capture service can do and refuse to
approximate the rest (`_mixins.py:79-130`):

| In-process call | Over e2e |
|---|---|
| `set_http_status(2xx)` | A rejection run of length zero |
| `set_http_status(failing)` | A run of 1000 — finite but longer than any scenario's schedule |
| `set_http_sequence([(500, ""), (200, "")])` | N copies of one failing status then a success |
| `set_http_sequence` with two different failure statuses, or ending on a failure | `NotImplementedError` |
| `set_http_sequence` with a full `OriginResponse` (hang-up, malformed body, delay) | `NotImplementedError` |
| `set_http_error()` | No realization: reading `env.origin` raises the named error |

An approximated endpoint grades a scenario nobody wrote, so the refusals are
the point. The same holds for read-back: the capture service records the parsed
payload only, so `_CapturedDelivery.headers` and `.body` raise a named
`AttributeError` (`_mixins.py:133-165`) telling you that signature and
byte-equality assertions belong in process — a read that returns an empty dict
lets a signature assertion pass on absent evidence.

For an intent with no live surface at all, declare it at the env method with
`e2e_unsupported(reason)` (`_realize.py:94-105`). The BDD
`pytest_runtest_makereport` hook turns that declaration into a non-strict xfail
carrying the reason (`tests/bdd/conftest.py:320-326`), so the in-process
transports of the same scenario still run. Two such declarations live on `CircuitBreakerMixin`
(`_mixins.py:1021-1060`): the seam's sleep count and the in-process breaker state
are both process-local, and neither is observable across the Docker boundary.

One hermetic e2e module keeps an in-process loopback receiver
(`tests/e2e/_webhook_capture_loopback.py`) because it runs without the compose
stack and cannot resolve the alias.

## Write a webhook test

1. **Pick the production entry point** your test drives.
2. **Choose the env that drives it** from the table above. Add an env only if
   none drives your function.
3. **Compose `LocalOriginMixin`** if you are adding an env, and let `_enter_pre`
   acquire the origin. Register anything else you acquire with `_guard` on the
   line you acquire it.
4. **Point the configuration at `env.webhook_url`.** Build the row through the
   env — `make_webhook_config(...)` on `CircuitBreakerEnv`
   (`tests/harness/delivery_circuit_breaker.py:114-152`) or `make_config(...)` on
   `ProtocolWebhookEnv` (`tests/harness/protocol_webhook.py:123-144`). For an
   HMAC row that is `auth_type="HMAC-SHA256", auth_token=<secret>`; there is no
   `secret=` parameter, because the column it used to write has no writer in
   `src/` and graded a signing branch production abandoned.
5. **Program the origin** with `set_http_status`, `set_http_sequence`,
   `set_http_error`, or `reject_next` — program the recovery point rather
   than counting attempts yourself.
6. **Call production** through the env method, and keep its return value: a
   sender's own verdict is part of the contract.
7. **Assert three things**: the verdict, `env.delivery_attempts`, and a fact off
   `env.last_delivery` (or the rows through `recorded_outcomes`). Use the shared
   helpers for signatures and backoff; do not hand-roll either.
8. **If the scenario runs over e2e too**, check the control-plane table: program
   it with `reject_next` or a single-status sequence, and read it back through
   the realize-aware accessors, never `env.origin`.

## Related documents

- [Outbound egress](../security/outbound-egress.md) — the one gateway, what it
  refuses, and how to add a call.
- [Egress gateway and the SDK boundary](egress-sdk-boundary.md) — what the `adcp`
  SDK owns and what this repo carries.
- [Test architecture](../../tests/CLAUDE.md) — the harness, the factories, and
  the wire-envelope assertion policy.
