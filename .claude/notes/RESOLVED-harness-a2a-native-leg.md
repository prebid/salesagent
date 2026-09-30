# RESOLVED: the signed A2A leg spoke a protocol `/a2a` does not serve

**Status:** done. 13 failing scenarios → 2, and the 2 that remain are a production
defect this repo had already recorded, not a harness one.

This note was written as a root-cause brief before the work started. Its headline
diagnosis — "one cause: the legs raise past the httpx response" — was wrong, and it is
rewritten here as a record of what the evidence actually showed. The original prediction
is kept below under "What the brief got wrong", because the way it was wrong is the
useful part.

## What was actually broken

Three defects, not one. All three are in `tests/`; `src/` was not touched.

### 1. The signed A2A leg named a JSON-RPC method nothing serves (9 of 13)

`src/app.py` builds `/a2a` with `create_jsonrpc_routes` and deliberately does NOT pass
`enable_v0_3_compat` — the comment there records why (the 0.3 adapter's catch-all rebuilt
every raised exception as `CoreInternalError`, discarding the AdCP envelope, so an auth
refusal answered 200 instead of 401). So `/a2a` speaks native a2a-sdk 1.0 only:
`SendMessage` / `CreateTaskPushNotificationConfig`, with an `A2A-Version: 1.0` header.

`_a2a_message_send_body` built a v0.3 `message/send` envelope. `JsonRpcDispatcher` routes
by method name before any version check, so every signed A2A dispatch was answered
`-32601 Method not found` at HTTP 200. No handler ran, `invoke_tool` was never reached,
`_resolve_identity` never ran, and the verifier never saw the request. A signing scenario
reads that as a seller that declined to refuse.

The leg's docstring justified `message/send` on the grounds that `SendMessage` is
unrepresentable in a `protocol_methods_*` bucket (the pinned capabilities schema
constrains those with `pattern: ^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$`) and so could only land
in `none`. That reasoning described a verifier reading the JSON-RPC method off an ASGI
middleware. **#1721 has no such middleware.** `RequestSignatureMiddleware` does not exist
in `src/` — the verifier runs inside `_resolve_identity`, and `verifier._bucket_for`
deliberately passes no `protocol_method` at all, because the operation graded against the
posture is the SKILL. Several harness docstrings still describe the old middleware; the
ones on the paths touched here were corrected, the rest are stale.

### 2. The webhook `authentication` block was in the A2A spelling, not AdCP's (4 of 13)

`given_request_registers_authenticated_webhook` wrote
`{"scheme": "HMAC-SHA256", "credentials": ...}`. The pinned `adcp.types.Authentication`
declares `schemes: [<AuthenticationScheme>]` — plural, `minItems`/`maxItems` 1, under
`additionalProperties: false`. The singular `scheme` is the A2A PROTOBUF spelling
(`a2a.types.a2a_pb2.AuthenticationInfo`).

On MCP and REST that dict goes into the AdCP request body, so both transports refused the
request `INVALID_REQUEST` at body validation — above the boundary, so the escalation never
ran and the scenario graded a schema rejection while claiming to grade a signature
refusal. The step now writes the AdCP shape and the singular spelling is produced in one
place, `_a2a_task_push_notification_config`, which is the only translator left (it
replaced two that disagreed).

Note this is NOT the parked malformed-body ordering question. The body was malformed
because the harness built it wrong.

### 3. The JSON-RPC legs discarded the response — the defect this note was named for

Real, and it is what the remaining two failures needed in order to report themselves
honestly. It was not the cause of any of the 13 on its own.

`_a2a_jsonrpc_result` raised a reconstructed `AdCPSalesAgentError` on a JSON-RPC `error`
frame and dropped the httpx response; `_run_mcp_over_http` did the same through
`_mcp_wire_error`. An envelope is not the whole refusal — `WWW-Authenticate: Signature
error="<code>"` is a HEADER — so `assert_signature_challenge` had nothing to read and
refused to grade, reporting a real wire refusal as "no wire". That is the lossy
reconstruction tests/CLAUDE.md § "Error Verification Policy" rules out.

Both carriers now hold the response: `WireRefusal` (no envelope existed) and `WireError`
(one did). `dispatchers._refusal_response` matches on those two types — not on
`getattr(exc, "response", None)`, which would also pick up whatever an httpx or SDK
exception hangs off that name. `assert_signature_challenge` was not touched.

## What remains, and why it is not the harness

```
tests/bdd/test_request_signing_enforcement.py::test_a_registration_carrying_webhook_authentication_is_refused_unless_signed[a2a]
tests/bdd/test_request_signing_enforcement.py::test_a_signedbutinvalid_registration_carrying_credentials_is_refused_under_warn[a2a]
```

Each fails at both A2A credential locations, and both are recorded production gaps —
`src/core/signing/verifier.py` § "What the boundary cannot see" names them, and
`docs/design/signing-lifecycle-conformance-audit.md` (landed independently while this work
was in flight) measures the first one as **V5**:

* `SendMessage params.configuration.task_push_notification_config` — ACCEPTED, HTTP 200.
  `on_message_send` reads `params.message.parts` and never touches `params.configuration`
  (V5's AST probe confirms: zero `configuration` reads), so the config never joins the
  validated request, `registers_credentials` stays False, the :1465 escalation never fires.
  V5's own three-row positive control shows REST, MCP and the A2A skill-input location all
  answering 401 in the same run.
* `CreateTaskPushNotificationConfig` — HTTP 200 JSON-RPC error.
  `AdCPRequestHandler.on_create_task_push_notification_config` raises
  `PushNotificationNotSupportedError` without ever calling `invoke_tool`.

V5 states the consequence of fixing only this side: *"Fixing the harness without fixing
production would move the failures rather than remove them."* That is exactly what
happened, and it is the intended outcome — before this change the second location reported
"carries no raw HTTP response to read WWW-Authenticate from", which reads like a harness
bug; it now reports `got None (HTTP 200, WWW-Authenticate=None)`, which is the measurement.

These graduate when V5's fix lands in `on_message_send`, or when the agent declines the
channel explicitly instead of answering 200 for a registration it did not perform.

## What the brief got wrong, and why it matters

It asserted one cause for all 13 and cited an identical diagnostic line as the tell. Four
of the 13 never produced that line at all (they were HTTP 400 `INVALID_REQUEST`), and the
a2a nine produced it for a reason the line does not name: the request was answered
`-32601` because the harness spoke 0.3 to a 1.0-only route. Reading the full failure text
of every listed node id before starting, rather than the brief's summary of it, is what
separated the three causes.

## Verification

* `tests/bdd/test_request_signing_enforcement.py` — 13 failed / 14 passed → 2 failed / 25 passed
* `tests/bdd` (full, serial) — 2 failed, 4268 passed, 4164 xfailed, 114 xpassed
* `tests/unit` — 10 failed, 6212 passed; all 10 fail identically at HEAD (storyboard ledger)
* `tests/integration` — fixes `test_harness_signed_dispatch`'s two `[a2a]` cases; no regressions

Two integration failures are stable and PRE-EXISTING at HEAD, unaffected by this change:

* `test_harness_signed_dispatch.py::test_a_failure_realization_reaches_the_operation_frame_not_the_handshake`
  asserts the refused frame carries an `mcp-session-id`. #1721's `/mcp` mount is
  `stateless_http=True` and mints none — `_mcp_open_session`'s own docstring says so. The
  lock's other assertion (`method == "tools/call"`) still passes and still catches the
  mutation the docstring names; the session-id half needs a different discriminator, which
  is a call for whoever owns the lock.
* `test_request_signature_discovery.py::test_a_malformed_signature_outranks_an_unresolvable_counterparty`
  — "Got None", expected `request_signature_header_malformed`.

`tests/integration/test_creative_agent_live.py` (12 errors) and, intermittently,
`test_creative_lifecycle_mcp.py` / `test_mcp_client_util.py` depend on reaching
`https://creative.adcontextprotocol.org` and flake with the box's outbound access. They
vary run to run on an unchanged tree.
