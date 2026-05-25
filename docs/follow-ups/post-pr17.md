# Follow-ups after PR #17 (delete legacy FastAPI/A2A/REST stack)

These are real bugs uncovered while getting CI green on
`bokelley/wrapper-webhook-fix`. None block PR #17 landing — they're
either pre-existing, schema-spec issues, or deferred architectural
work.

## P1 — Security regression

### `test_cross_tenant_token_rejected` no longer rejects

**File**: `tests/e2e/test_tenant_isolation.py::TestMultiTenantIsolation::test_cross_tenant_token_rejected`

**Symptom**: A request bearing `x-adcp-auth: <ci-test-token>` (token
belongs to `ci-test` tenant) plus `x-adcp-tenant: iso-test` (header
asks for the `iso-test` tenant) returns 200 instead of an
auth/authorization error.

**Why it matters**: Cross-tenant token-injection is the classic
multi-tenant escalation attack. The test was added precisely to guard
against it; the fact that it now passes means a buyer with a token
issued for tenant A could read/write tenant B's resources by flipping
one header.

**Likely root cause**: One of:

- The bearer-token middleware no longer cross-checks the resolved
  Principal's `tenant_id` against the `x-adcp-tenant` header.
- The new `DEV_TENANT_SUBDOMAINS` or `SubdomainTenantMiddleware` wiring
  resolves tenant before the auth middleware sees the token.
- The Principal lookup happens against the header-supplied tenant
  rather than against the token's bound tenant.

**Suggested next step**: trace request through `core/main.py`
middleware stack for the failing path; compare with the legacy stack
that this PR deleted; add a unit test against the bearer-token
middleware exercising the mismatch.

---

## P1 — Auth regression

### A2A surface rejects valid bearer token

**Files**:
- `tests/e2e/test_a2a_webhook_payload_types.py::TestA2AWebhookPayloadTypes::test_completed_status_sends_task_payload`
- `tests/e2e/test_a2a_webhook_payload_types.py::TestA2AWebhookPayloadTypes::test_submitted_status_sends_task_status_update_event`

**Symptom**: A2A POST returns 401 with
`{"error": "invalid_token", "error_description": "*** missing or invalid"}`
even when sending the `ci-test-token` that authenticates fine against
the MCP surface.

**Likely root cause**: The legacy-stack deletion removed the path that
validated `Authorization: Bearer <token>` against
`Principal.access_token`. The new A2A surface (`adcp.server.serve()` at
host root) may use a different auth chain that doesn't share lookup
logic with the MCP middleware.

**Suggested next step**: confirm both transports go through a single
shared bearer-token validator (or wire the A2A surface to the MCP
middleware's resolver).

---

## P2 — Output-schema gap

### `reporting_period` missing from `get_media_buy_delivery` response

**Files**:
- `tests/e2e/test_adcp_full_lifecycle.py::TestAdCPFullLifecycle::test_four_phase_lifecycle`
- `tests/e2e/test_adcp_reference_implementation.py::TestAdCPReferenceImplementation::test_complete_campaign_lifecycle_with_webhooks`
- `tests/e2e/test_delivery_webhooks_e2e.py::TestDailyDeliveryWebhookFlow::test_daily_delivery_webhook_end_to_end`

**Symptom**:
```
fastmcp.exceptions.ToolError: Output validation error: 'reporting_period' is a required property
```

**Why**: adcp 4.4 made `reporting_period` required on the delivery
response. Our response builder isn't including it. This is
seller-side output, not buyer-side input — the middleware can't paper
over it.

**Suggested next step**: update the response builder in
`src/core/tools/delivery.py` (or wherever
`get_media_buy_delivery_impl` returns) to populate
`reporting_period` from the actual query window.

---

## P2 — Compat middleware swallowing what it shouldn't

### `test_unknown_field_rejected` no longer raises in dev mode

**File**: `tests/integration/test_mcp_unknown_field_handling.py::TestMcpDevMode::test_unknown_field_rejected`

**Symptom**: Dev mode is supposed to reject unknown fields loudly so
schema drift surfaces immediately. Currently a request with
`nonsense_field` succeeds — the test asserts ToolError and gets none.

**Why**: One of our compat layers (`pre_validation_hooks` or the
FastMCP/typed-dispatcher level upstream of it) is silently
stripping unknown fields even outside production mode. That breaks
the strict-dev contract documented in CLAUDE.md pattern #7
("Development/CI: Default → `extra='forbid'` (strict validation)").

**Suggested next step**: walk the request lifecycle for a payload
with an unknown field; identify which layer drops it; restore the
dev-mode reject path.

---

## P2 — Architectural cleanup (typed-fixture migration)

The current branch reduced our reliance on request compat hooks by
updating `tests/e2e/adcp_request_builder.py` to provide proper
shapes for `idempotency_key`, `account`, `asset_type`, `format_id`,
and `assignments`. The middleware still has setdefault paths for
callers that bypass these builders.

**Goal**: delete remaining local schema-compat shims such as
`src/core/schemas/_asset_type_compat.py`. Replace dict-literal protocol
payloads in tests with typed `*Request` Pydantic models so that
construction-time validation catches every shape error and mypy +
pydantic-plugin flag schema drift before runtime.

**Why this matters**: every adcp version bump tightens N fields.
With dict-literal fixtures, finding all N takes N CI rounds. With
typed fixtures, finding all N takes one `python -c "import tests.factories"`.
The middleware is masking schema drift, not protecting against it.

**Scope**:
- 16 test files touch `client.call_tool` (~83 sites total, 38 in e2e)
- 42 files already use typed `*Request` models (prior art)

**Suggested next step**: build `tests/factories/protocol/` with typed
factories; replace `_make_creative_asset` and the dict-builder helpers
with typed builders; add a structural guard that fails CI if a test
hands a raw dict to a harness wire method.

---

## P3 — Upstream PR to `adcp` SDK

The genuinely-protocol-level patches (not seller-specific) belong in
the SDK itself as a `RequestCompatLayer`:

- `buying_mode='brief'` default for pre-v3 clients (spec says SHOULD)
- `format_id` string → `FormatReferenceStructuredObject` (4.x → 4.4)
- `asset_type` discriminator inference for missing-field payloads (3.x → 4.4)

Seller-specific patches such as missing-account placeholders or
`idempotency_key` autogen DO NOT belong upstream — they violate the spec's
intent.

**Suggested next step**: file an issue against the `adcp` Python SDK
repo describing `RequestCompatLayer(target_version=..., min_buyer_version=...)`
as a TypeAdapter pre-validator hook, with our middleware as the seed.
