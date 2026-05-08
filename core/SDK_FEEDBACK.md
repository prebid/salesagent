# SDK feedback — open items

Tracker for adopter friction with `adcp-client-python` (currently pinned to
**v4.4.3**, declares spec version **3.0.5**). The original three rounds of
feedback (most items now merged upstream) live in this file's git history.

## Currently open

### Framework gaps

#### 1. `validate_idempotency_wiring` × `LazyPlatformRouter` composition

The boot validator walks the platform handed to `serve()` looking for
`@IdempotencyStore.wrap` decorators. With `LazyPlatformRouter` the router
shell has none — dedup is wired one indirection deeper, on the per-tenant
platforms the factory produces. Boot fails with `INVALID_REQUEST` even
though every produced platform is correctly wrapped.

**Workaround:** `router._adcp_idempotency_external = True` at
[core/main.py:276](core/main.py).

**Better SDK shape:** validator should detect `LazyPlatformRouter` and
either skip with an info log, probe the factory once at boot for a
representative tenant, or extend the public API so the router can declare
"wrap is wired downstream" without poking a private attribute.

#### 2. MCP DNS-rebinding allowlist needs subdomain wildcards or a callable

`mcp.server.transport_security._validate_host` only matches exact hosts and
`host:*` port wildcards — NOT subdomain wildcards like `*.localhost` or
`*.localtest.me`. Multi-tenant deployments where every tenant is a
subdomain must either enumerate every active tenant in the allowlist on
every boot OR disable DNS-rebinding protection entirely.

**Workaround:** enumerate dev tenant subdomains at
[core/main.py:_allowed_hosts](core/main.py).

**Better SDK shape:** the allowlist should accept either glob-style
subdomain wildcards OR a callable `validate_host(host: str) -> bool` that
gets wired through `serve()`'s `allowed_hosts=` parameter. The actual fix
probably lives in `modelcontextprotocol/python-sdk`.

#### 3. `'submitted'` wire status fails FastMCP output validator

FastMCP doesn't resolve the `oneOf` branches on
`CreateMediaBuyResponse`; it rejects `status='submitted'` even though
the literal is a valid third-branch value per spec.

**Status:** salesagent side fixed in PR #183 (emit submitted envelope
not hybrid). FastMCP-side oneOf resolution still tracked upstream.

#### 4. `ctx.caller_identity` is a composite scope-key, not the bare principal_id

Docs imply `ctx.caller_identity` is the principal ID, but it's actually a
composite scope-key. Bare `principal_id` lives on `current_principal`
ContextVar set by `BearerTokenAuthMiddleware`.

**Status:** open as salesagent #42. Requires SDK doc update.

### Helper-typing gaps (surfaced during cleanup)

These are runtime-correct but mistyped — adopters get IDE friction:

- **`extract_webhook_result_data`** declared as
  `AdcpAsyncResponseData | None` but returns `dict` at runtime
  (see [src/services/protocol_webhook_service.py:200](src/services/protocol_webhook_service.py)).
- **`create_mcp_webhook_payload`** should accept any `BaseModel` for
  `result` (it handles `model_dump` internally) and return
  `McpWebhookPayload`, not `dict[str, Any]`. Three call sites currently
  cast or `.model_construct()` around the type.

### Stretch / nice-to-have

- **`adcp.upstream.gam` helper** — service-account auth + cached client
  (~30 LOC, identical across any salesagent-shaped GAM adopter).
- **`placement_to_product` projection helper** — mechanical fields
  (format_ids from sizes, default pricing_options, etc.) are identical
  across publisher-config-vs-product mapping.

## Closed since prior rounds

Major upstream fixes (all merged):
- #544 `CallableSubdomainTenantRouter`
- #545 `BearerTokenAuthMiddleware` `header_name` + `bearer_prefix_required`
- #555 `IdempotencyStore.PgBackend`
- #560 `inject_context` on `AdcpError` raise path
- #566 `serve(auth=BearerTokenAuth(...))` wires both MCP + A2A
- #567 `@IdempotencyStore.wrap` × arg-projected methods

Plus a long list of public-surface aliasing and codemod improvements
already shipped on `main`.

The full historical record lives in this file's git history.
