# Access control / authorization architecture — current state and target design

Investigation done 2026-08-04 while triaging PR #1838 (salesagent-1zq3). Two
questions drove it: (1) can this codebase check whether a calling party has
access to a specific tool, and vary tool access by caller — the AdCP-ACL
question; (2) is `get_adcp_capabilities`' `specialisms` field enforced
anywhere, or purely descriptive.

## 1. What exists today

| Mechanism | Gates | Varies by | Enforced consistently? |
|---|---|---|---|
| `AUTH_OPTIONAL_SKILLS` | tool call (binary) | valid token, not identity | scattered across transports — see salesagent-3c4m |
| Tenant isolation (`tenant_id`) | data rows | tenant | yes, pervasive |
| `Product.allowed_principal_ids` | product visibility in `get_products` | buyer principal | **no — read-path only.** `create_media_buy` never re-checks it. **Filed as salesagent-7kwq / [GH #1849](https://github.com/prebid/salesagent/issues/1849), P0.** Not an isolated instance — see §1a: this is a recurring disease, not a single bug. |
| Admin UI `User.role` (`admin`/`manager`/`viewer`) | admin routes | human user + tenant | **no** — two correct decorators exist (`require_auth(admin_only=...)`, `require_tenant_access()` in `src/admin/utils/helpers.py:259,299`), but several blueprints hand-roll their own inline `session.get("role")` check instead: `src/admin/blueprints/policy.py:29,146,149,226`, `inventory.py:562`, `core.py:273,277`. Same "one correct pattern, several hand-rolled duplicates" disease as the transport auth-policy scatter. **Not yet filed as a ticket — see §3.** Adjacent: **[GH #1805](https://github.com/prebid/salesagent/issues/1805)** — `require_tenant_access()` sits outside `log_admin_action`'s decorator, so a correct denial from the decorator itself goes unaudited. Different bug (audit gap, not a missing check), same decorator-stack neighborhood. |
| `SUPER_ADMIN_DOMAINS`/`_EMAILS` | cross-tenant admin access | human email domain | yes, routed through the same admin_only checks |
| `WorkflowStep.assigned_to` | — | — | **not a permission at all** — a queue label; nothing restricts who can act on an assigned step |
| Webhook HMAC signing | outbound message integrity | — | different category (authenticity, not access control) |
| GAM adapter | — | — | **no native concept** — flattened to "tenant's service account can do whatever GAM allows" |
| Creative approval | — | status transition, not identity | any principal owning the buy can act; not actor-gated |
| `Account.account_scope` | — | — | pass-through AdCP spec field, not internally enforced |
| `specialisms` (`get_adcp_capabilities`) | — | seller/tenant (declared, not buyer) | **no — purely descriptive.** See §2. |

## 1a. The `allowed_principal_ids` gap is one instance of a recurring disease

`#1849` is not isolated. The same shape — a scoping check that exists on the
read path but is never re-checked on a write/action path, surfacing as a 200
(sometimes with an empty body) instead of a 403 — recurs across resources:

- **[GH #1318](https://github.com/prebid/salesagent/issues/1318)** (2026-05-18,
  predates this investigation) — `MediaBuyRepository.get_by_principal` filters
  silently by `principal_id`; a request for a media buy the caller doesn't own
  returns an empty deliveries list, not a denial. The earliest known instance
  of this exact pattern.
- **[GH #1808](https://github.com/prebid/salesagent/issues/1808)** — MCP task
  tools are tenant-scoped only; a sibling principal can read and terminalize
  another principal's task. Partially closed by #1812 (`get_task`/
  `complete_task`); `list_tasks`/`list_by_tenant` still open.
- **[GH #1702](https://github.com/prebid/salesagent/issues/1702)** — A2A
  `tasks/get`'s in-memory task-map fast path has no identity check at all
  (the durable path added by #1544 does check). Same subsystem as #1808, same
  underlying gap.

None of these three are filed under `salesagent-0krw` — they were each found
independently, in their own review threads, before this note existed. Listed
here because §3's root cause (`ResolvedIdentity` carries no scoping beyond
`tenant_id`; nothing enforces per-principal ownership on non-read paths) is
exactly what all four bugs share. A structural fix — one ownership-check
helper every mutating/task-reading path calls, instead of each repository
method re-deriving its own scoping — would close all four at once instead of
four independent patches. Not scoped as its own epic yet.

## 2. `specialisms` is a self-report, not a gate

`specialisms` is a seller-wide (tenant-axis, not buyer-axis) array declared
once — e.g. `src/core/tools/capabilities.py:100,272` hardcodes
`specialisms=[AdcpSpecialism.sales_non_guaranteed]`. Grepped all of `src/`
for every specialism value outside those two declaration sites — zero
results. It's written once, serialized into the capabilities response, and
never read anywhere else in the codebase.

Confirmed concretely: whether a buy requires manual approval is decided by
`tenant_approval_required or adapter_approval_required`
(`media_buy_create.py:2707-2709`) — completely disconnected from
`specialisms`. A tenant can declare only `sales-non-guaranteed` while its
config forces every buy through guaranteed-style approval anyway, and
nothing catches the divergence. `sync_governance`/`check_governance` don't
even exist as tools in this codebase, so the `governance-*` specialisms
aren't just unenforced — there's nothing to enforce.

**Risk**: a tenant's declared specialisms and its actual runtime behavior
can silently diverge. Low urgency (no financial exposure, unlike
salesagent-7kwq) but a real compliance-integrity gap — the AAO compliance
badge is only as honest as this self-report.

## 3. Per-tool ACL by principal: doesn't exist, and isn't spec-mandated

Confirmed: `ResolvedIdentity` (`src/core/resolved_identity.py:24-44`) carries
only `principal_id`, `tenant_id`, `tenant`, `auth_token`, `protocol` — no
roles or scopes. Once a token is valid, that principal can call every tool
any other valid principal can call. There is no mechanism to vary
tool/skill *access* by caller.

But this is **not spec-mandated**. Read the pinned 3.1 schema
(`get-adcp-capabilities-response.json`) directly: `capabilities` is a single
global declaration, identical to every caller, no per-principal field
anywhere. AdCP's actual per-buyer variance mechanism is **data-catalog
filtering** (`get_products` returning a different set per buyer via
`allowed_principal_ids`), not tool-access gating — and the codebase already
implements that correctly in principle (product.py's filter), just doesn't
enforce it past the read path (§1, salesagent-7kwq).

### Choke-point mapping, per transport

- **MCP: one already exists.** `mcp.add_middleware(MCPAuthMiddleware())`
  (`src/core/main.py:174`) runs `on_call_tool` before every tool call — a
  per-principal-per-tool check could slot in here with no restructuring.
- **A2A: partial.** One dispatcher (`_handle_explicit_skill`), but auth is
  checked at ~10 independent raise sites feeding it, only one of which
  builds the correct two-layer envelope (see salesagent-3c4m, R3-4/R3-32).
- **REST: none.** 13 routes each independently pick `require_auth` vs
  `resolve_auth` as a per-route `Depends` default
  (`src/routes/api_v1.py:224-486`) — no shared dependency all routes pass
  through.

### Target shape (if this is ever built)

One typed `AuthPolicy` declared once per skill, consumed through a single
accessor by all three transports — REST derives its route dependency from
it instead of a hand-typed subset map, A2A's wire-translation seam is fixed
so no raise site can bypass the envelope. This is the same fix already
scoped for salesagent-3c4m (the AuthPolicy centralization epic) — a real
per-buyer *tool* ACL (as opposed to policy-by-skill) would be an **additive
axis on top of that**, not a replacement, and would need REST's missing
choke point built regardless of whether the ACL layer itself gets built.

**Not currently scheduled.** Legitimate future improvement, not required by
spec, not blocking anything currently in flight.

## 4. Tracking

- salesagent-7kwq / [GH #1849](https://github.com/prebid/salesagent/issues/1849) — P0, the confirmed exploitable gap (§1, `allowed_principal_ids`).
- [GH #1318](https://github.com/prebid/salesagent/issues/1318), [GH #1808](https://github.com/prebid/salesagent/issues/1808), [GH #1702](https://github.com/prebid/salesagent/issues/1702) — same disease as #1849 on other resources (§1a). Each filed and tracked independently, not under `salesagent-0krw`; listed here for the shared-root-cause context.
- [GH #1805](https://github.com/prebid/salesagent/issues/1805) — admin-RBAC-audit-adjacent (§1 admin row). Independent bug, same code neighborhood as salesagent-0krw.2.
- salesagent-3c4m — AuthPolicy centralization epic; the choke-point/target-shape work in §3 is additive on top of it, not part of it yet.
- salesagent-0krw — epic for this note. Children: salesagent-0krw.1 (specialisms non-enforcement, §2), salesagent-0krw.2 (admin RBAC duplicated checks, §1). Separate from the PR #1838 lineage (salesagent-1zq3 and its 4 successors) since neither finding came from that review.

### Related but distinct: auth-boundary status-code / error-classification bugs

Not access-control-decision bugs — the underlying auth/ACL logic here is
correct; the defect is in how a decision gets translated to a wire response.
Listed because they surfaced from the same general area (auth path
correctness) and are easy to conflate with the ACL-enforcement findings
above:

- [GH #1859](https://github.com/prebid/salesagent/issues/1859) — unauthenticated MCP/A2A calls to protected tools return HTTP 200 instead of 401 (transport never maps the correctly-raised `AdCPAuthenticationError` to a status code).
- [GH #1861](https://github.com/prebid/salesagent/issues/1861) — DB/infra errors get caught and misclassified as auth-deny decisions; `google_callback` fails open on one such path.
- [GH #1743](https://github.com/prebid/salesagent/issues/1743) — a rejected credential is classified `AUTH_MISSING` (retryable) instead of `AUTH_INVALID` (terminal), telling the agent to retry a revoked token.
