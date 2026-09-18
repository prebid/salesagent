# RFC 9421 signature handling against the documented request lifecycle — conformance audit

**Scope.** Does this tree's inbound request-signing implementation still satisfy the
normative claims its own documentation makes? Worktree
`/srv/ws/salesagent/a3-9421-on-1721`, branch `feat/rfc9421-on-1721`, at
`64c2cfe13` plus the three uncommitted `tests/harness` files in the working tree.
Yardsticks, in the priority the audit was given: `docs/development/request-lifecycle.md`,
then `docs/development/patterns-reference.md` / `structural-guards.md` /
`docs/security/outbound-egress.md` / `engineering-standards.md`, then
`docs/design/signing-vs-request-boundary.md`, then the pinned AdCP spec
(`adcontextprotocol/adcp@v3.1.1:docs/building/by-layer/L1/security.mdx`, read at the tag —
line numbers below are from `git show v3.1.1:...`, and every one the code cites was
re-checked).

## The answer to the owner's hypothesis

**The hypothesis is mostly wrong, and the parts that are right are not where it was
expected.** The core of the design — one capture that decides nothing, verification inside
`_resolve_identity`, one renderer, the specific taxonomy code surviving byte-for-byte into
`WWW-Authenticate` — holds, and holds on all three transports. I drove it rather than read
it: 401 plus the exact challenge for two distinct codes over REST, MCP and A2A, and the
pinned 40-vector conformance corpus is green (82 passed).

Six defects survived. None is in the shape of the design; all six are in places the design
documents assumed were settled. **V1, V2 and V5 are fixed** — see
[What was fixed](#what-was-fixed) for the commits and the re-run probes.

| # | Defect | Severity | Status |
|---|---|---|---|
| V1 | The capture's surface gate reads `scope["path"]` without stripping ASGI `root_path`, so under a root-path deployment the router reaches every AdCP tool while the capture records nothing and the verifier silently sees an unsigned request | High, latent | **FIXED** `5d87631cb` |
| V2 | A repeated header line resolves **first-wins on REST and A2A, last-wins on MCP** — for `Signature`, for `Authorization` and for `x-adcp-tenant`. `Signature` is not in the single-line list that exists to close exactly this | Medium | **FIXED** `18e2b31c6` |
| V3 | `SignedExchangeCapture` is not the innermost middleware, contradicting its own docstring: a body-rewriting `BaseHTTPMiddleware` sits below it | Low in effect (see V4), claim false | open |
| V4 | That body-rewriting middleware (`a2a_messageid_compatibility_middleware`) has been a no-op since Starlette's `call_next` stopped honouring a rebuilt request's `receive`. It logs a conversion it does not perform | Medium (not a signing bug; it is why V3 is currently harmless) | open |
| V5 | An A2A `SendMessage` registering webhook credentials through `params.configuration.task_push_notification_config` is answered `200` with no `request_signature_required` and no `:1464` log. The other three locations enforce | Medium | **FIXED** `51006ea38` |
| V6 | `docs/development/request-lifecycle.md` is stale on three claims that touch this path (middleware count, the AUTH_MISSING-before-tenant ordering, the A2A context builder) | Doc | open |

Plus one design-doc claim that is false of the code (V7, `posture_for_tenant(None)` is
still reachable) and two test-side defects (T1, T2) that are not production faults.

Every verdict below names the probe that produced it, and for the VIOLATED ones the probe
is shown failing on the tree and passing on a control, or passing on the tree and failing
on a named mutation.

## What was fixed

Three of the six, on this branch, one commit each. The other three (V3, V4, V6) are
recorded and open.

| # | Commit | What changed | Re-run evidence |
|---|---|---|---|
| V1 | `5d87631cb` | `capture.py`'s surface predicate and `src/app.py`'s `/a2a` predicate read `path_from_asgi_scope`; the guard's allowlist row moves to the helper form | P-ROOTPATH: `root_path=/adcp` now `exchange_captured=True`, control `root_path=""` unchanged, old read reddens it |
| V2 | `18e2b31c6` | `joined_headers` is the one header derivation (RFC 9110 §5.3) and `_resolve_identity` reads it whenever the request was captured; `_MALFORMED_VALUE_RULES` gains the `signature` row | P-HEADERS: duplicated line → identical challenge on all three transports, none reaching the checklist; single line → identical value; transport container reddens it |
| V5 | `51006ea38` | `on_message_send` declines `params.configuration.task_push_notification_config`; the harness sends the AdCP field where AdCP declares it | P-A2ACFG: three AdCP locations refuse + log, the declined channel answers `-32003`; `tests/bdd/test_request_signing_enforcement.py` 27 passed |

### Verification of the three fixes

Full-suite offload, `cassini run` id `sa-a101ded1` / run `30466356458349febd9e5072406ec5b5`,
launched 2026-09-17T17:03:06Z, results `test-results/innet_170926_1703/`. Per-suite counts
read from the reports, and the current run resolved by each report's embedded `created`
field — `innet_170926_0547` carries a LATER directory mtime than `innet_170926_0629`
despite being the older run, so neither name nor mtime decides which directory is current.
Baseline is `innet_170926_0926` (earliest report 09:30Z), the last full run before these
fixes.

| suite | baseline `_0926` | this run `_1703` | delta |
|---|---|---|---|
| unit | 6513 pass, 1 fail | 6514 pass, **0 fail** | −1 fail |
| integration | 3049 pass, 24 fail, 0 err | 3066 pass, **4 fail, 3 err** | −20 bad, +3 new err (below) |
| bdd_inprocess | 4254 pass, 16 fail, 114 xpass | 4270 pass, **0 fail**, 114 xpass | −16 fail |
| bdd_e2e | 1238 pass, 4 fail, 14 xpass | 1240 pass, **2 fail**, 14 xpass | −2 fail |
| e2e | 134 pass, 4 fail | 134 pass, 4 fail | — |
| admin / ui / quality | 140 / 5 / 4 pass, 0 fail | identical | — |
| storyboard | 1 pass, 58 fail, 46 xfail | 1 pass, **58 fail**, 46 xfail | **identical set, 0 new, 0 fixed** |

**The xpass set is unchanged.** `bdd_inprocess` 114 → 114 and `bdd_e2e` 14 → 14, with a
node-id set difference of **zero in both directions**. All 114 in-process xpasses are in
`tests/bdd/test_uc026_package_media_buy.py`, routed by `_UC026_XFAIL_TAGS` with
`strict=False` (`tests/bdd/conftest.py:3998-4004`) — the conftest states outright that
tag-level xfailing an outline that fails a minority of its rows "converts the other ten
from passing to xpassed". Their reasons are UC-026 production gaps (REST update dispatch
not wired, keyword-targeting ops not implemented, `creative_assignments` /
`optimization_goals` replacement missing) and none of them touches headers, signing or a
transport. They are present at 114 in all three pre-fix runs. A targeted local run of that
module on the fixed tree reports **420 passed, 195 xfailed, 114 xpassed, 0 failed**, which
is the baseline count exactly. Verdict: pre-existing, non-strict, not graduated by these
fixes.

**`storyboard`'s 58 failures are pre-existing and byte-identical to the baseline** — every
`security_transport::signed_requests` check on the `a2a` and `mcp` transports, all failing
in the storyboard runner's own probe (`request_signing_probe threw: MCP initialize
precondition failed … Error POSTing to endpoint: <!doctype html`). The suite is not in
cassini's declared roster either. Not this change's blast radius, and not diagnosed here.

**The 3 new integration errors are box contention, not a defect.** All three are
`test_mcp_tool_roundtrip_minimal.py` setup errors reading
`RuntimeError: MCP server failed to start on port <n> within 60s. STDOUT: N/A STDERR: N/A`
— a spawned server subprocess that never came up, three in a row on one xdist worker
(`gw13`/`gw8`) with three different ports. Re-run locally on the fixed tree: **11 passed**,
the whole module. The remaining 4 integration failures are all pre-existing: T1 and T2
below, plus two `test_template_url_validation` rows that are `XPASS(strict)` against
`tests/integration/known_failures.txt` and were already failing in the baseline.

## Method, and how to re-run any of it

```bash
cd /srv/ws/salesagent/a3-9421-on-1721
source .agent-db.env          # agent-pg-a3-9421-on-1721, port 50325
```

Sixty-one normative claims were extracted from the six yardstick documents
(`MUST` / `MUST NOT` / `never` / `always` / `only` / `the one …`). Forty-four touch
signing, identity resolution, error emission or the request boundary and are verdicted
below. Seventeen are out of scope (idempotency digest rules, repository/ORM patterns,
allowlist ratchets, admin-UI routing) and are listed as such at the end.

Three verdicts are used. **TRUE** — still true of the code, with a probe.
**VIOLATED** — false of the code; a defect, with file:line and the failure path.
**SUPERSEDED** — true of PR #1721 and deliberately changed by the signing work, with the
document or commit that authorises it. An uncitable SUPERSEDED is reported as VIOLATED.

## Summary table

### `docs/development/request-lifecycle.md`

| # | Claim (source) | Verdict | Probe |
|---|---|---|---|
| RL-1 | ":126 `src/app.py` registers **three** HTTP middlewares" | **VIOLATED** | P-STACK — four, `src/app.py:636` added a fourth |
| RL-2 | ":126 none of them reads a credential or resolves an identity" | TRUE | P-CAPTURE(a) — 0 `raise`, 0 `send()`, 0 status literals in `SignedExchangeCapture` |
| RL-3 | ":138 `a2a_messageid_compatibility_middleware` … rewrites a numeric `message.messageId` or JSON-RPC `id` to a string" | **VIOLATED** | P-REWRITE — the rewrite never reaches the app |
| RL-4 | ":129 `AuthChallengeResponder` lifts the status to 401 and attaches the challenge" | TRUE | P-CHALLENGE — 401 + exact bytes on all three transports |
| RL-5 | ":154 on flush it strips any inbound `WWW-Authenticate` and re-derives it from the body" | TRUE | `auth_middleware.py:250` unconditional strip; P-CHALLENGE mutation shows the header is re-derived, never passed through |
| RL-6 | ":159 No middleware decides auth … the resolver behind it is the one reader of a credential" | TRUE (doc incomplete — see V6) | P-BOUNDARY(1); the resolver now reads two credentials and the doc does not say so |
| RL-7 | ":200 Every transport makes one call: `serve(...)`" | TRUE | `grep -rn "await serve("` → 3 transports, 0 others |
| RL-8 | ":210-224 order = validate, echo, version negotiate, resolve identity, idempotency, tool" | TRUE | `_boundary.py:296-368` read in order; P-BOUNDARY(1) pins `_invoke` after `_resolve_identity` |
| RL-9 | ":230 nothing between entry and stamp reads, passes or writes the echo" | TRUE | `ruff check --config ruff-boundary.toml` + `ast-grep scan` clean |
| RL-10 | ":234 version negotiation runs before anything touches the database" | TRUE | `_boundary.py:314` precedes `:343` |
| RL-11 | ":241 identity resolves once, in a worker thread" | TRUE | `_boundary.py:343 asyncio.to_thread(_resolve_identity, …)`, single call site |
| RL-12 | ":287 `_resolve_identity(headers, *, require_valid_token, account_ref, credential_required_for)`" | **SUPERSEDED** | gains `signature_subject`; authorised by `signing-vs-request-boundary.md` Decision 1 + "CORRECTION from implementation" |
| RL-13 | ":292 the one identity resolution in the tree; `invoke_tool` is its only caller; `ruff-boundary` bans importing it" | TRUE | `grep` → one call site (`_boundary.py:344`); `ruff-boundary.toml:109-110`; `tests/unit/test_ruff_boundary_bans.py` green |
| RL-14 | ":306 `Authorization: Bearer` only; `x-adcp-auth` not recognised" | TRUE | `resolved_identity.py:169-172`, no other reader |
| RL-15 | ":314 AUTH_MISSING "runs **before** tenant detection, so an anonymous caller costs no database lookups"" | **SUPERSEDED** | tenant load now first (`resolved_identity.py:436`), authorised by the step-2 comment at `:422-435` citing security.mdx :1268/:1224 and by conformance vectors `negative/001`/`027`; P-ORDER mutation reddens exactly those two. The doc text is stale (V6) |
| RL-16 | ":318 four tenant strategies, first match wins, in that order" | TRUE | `resolved_identity.py:249-273` |
| RL-17 | ":332 3b the seller's brand policy, asked once the tenant is loaded" | TRUE | `resolved_identity.py:443-444` |
| RL-18 | ":338 the token is looked up inside the detected tenant, never globally" | TRUE | `resolved_identity.py:452-453` |
| RL-20 | ":367 a presented credential that resolves to no principal is AUTH_INVALID on every row" | TRUE | `resolved_identity.py:454` latch + `:486-489`; graded by `negative/027` |
| RL-21 | ":374 the identity is built once; nothing copies or amends it afterwards" | TRUE | `ast-grep scan --config sgconfig.yml` clean |
| RL-23 | ":409 `TransportProtocol` is a label; nothing under `src/` branches on it" | TRUE | `grep -rn "TransportProtocol\." src/` → construction and the label parameter only |
| RL-24 | ":428 no transport calls `identity_of`" | TRUE | `grep -rn "identity_of("` → 4 call sites, all server-initiated |
| RL-25 | ":434 the boundary sets `require_valid_token` true whenever the request carries `account`" | TRUE | `_boundary.py:346` |
| RL-28 | ":500 `_served` is the one writer of `context`" | TRUE | `ast-grep` rule `context-is-written-by-the-boundary-alone` clean |
| RL-30 | ":530 `failure_response` builds it once; `AdcpFailure` is the one exception a transport catches" | TRUE | P-CHALLENGE — one envelope shape on all three transports |
| RL-31 | ":541 `http_status` reads the status from `CODE_TABLE` by the wire code string" | TRUE | `signature_codes.py` sets `status=401` for all 28; P-CHALLENGE observes 401 |
| RL-32 | ":544 the body carries no exception text" | TRUE | P-CHALLENGE bodies carry only `CODE_TABLE` text; the SDK's `step=`/diagnostic sentence appears in the log only |
| RL-33 | ":552 each transport adds exactly one thing: its own wire marker" | TRUE | P-CHALLENGE — REST status, MCP `isError` text, A2A artifact DataPart; identical `adcp_error` |
| RL-34 | ":566 the REST handler takes `Request`, so FastAPI validates nothing ahead of `validated_request`" | TRUE | `src/routes/api_v1.py:52-111` |
| RL-35 | ":584 MCP reads headers with `get_http_headers(include_all=True)`; outside an HTTP request it returns `{}`" | TRUE | `inspect.getsource(get_http_headers)` — `include_all=True` sets `exclude_headers = set()` |
| RL-36 | ":602 "The SDK's **default** context builder places `dict(request.headers)` on `state["headers"]`"" | **SUPERSEDED** | replaced by `AdCPCallContextBuilder` (`src/app.py:366`), authorised by Decision 1's CORRECTION; doc stale (V6) |
| RL-37 | ":609 `_dispatch_skill` answers `MethodNotFoundError` for any skill that is not a registry row with `a2a=True`" | TRUE | `adcp_a2a_server.py:467-492` |
| RL-39 | ":638 `_impl` is called as `impl(req=…, identity=…)`, never with a Context or raw headers" | TRUE | P-BOUNDARY(1) — one `impl(...)` call, `_boundary.py:143` |
| RL-40 | ":650 `ruff-boundary` bans importing each `_impl` name across `src/` and `scripts/`" | TRUE | `ruff check --config ruff-boundary.toml` clean; ban liveness graded by `test_ruff_boundary_bans.py` (green) |
| RL-42 | ":699 change how an error looks on the wire → `failure_response` / `AdcpErrorResponse`; the 401 handshake in `AuthChallengeResponder`" | TRUE | P-CHALLENGE mutation — editing `_challenge_for_code` alone changes all three transports and reddens 28 conformance vectors |

### `docs/design/signing-vs-request-boundary.md`

| # | Claim | Verdict | Probe |
|---|---|---|---|
| SD-1 | "Landed: … The stack is still **three** middlewares and none of them reads a credential" | **VIOLATED** (count) / TRUE (credential) | P-STACK |
| SD-2 | Decision 1 — verification moves into `_resolve_identity` | TRUE | `resolved_identity.py:475`; P-ORDER |
| SD-3 | ":104 ONE capture, one derivation, three readers … it still decides nothing — no `raise`, no `401`, no `status_code`, no early `send`" | TRUE | P-CAPTURE(a) |
| SD-4 | ":110 the capture is scoped by an **allowlist** of AdCP surfaces … an allowlist cannot forget" | ~~VIOLATED~~ → TRUE (`5d87631cb`) | P-ROOTPATH — the allowlist now reads `path_from_asgi_scope`, so it selects the router's own request set under any `root_path` |
| SD-5 | ":115 the buffer must be lossless on **every** exit" | TRUE | P-CAPTURE(b) — six exits, two named mutations redden it |
| SD-6 | ":120 the SPECIFIC signature code must survive byte-for-byte into the envelope and reach `_challenge_for_code`" | TRUE | P-CHALLENGE (2 codes × 3 transports) + mutation |
| SD-7 | ":138 the `posture_for_tenant(None)` case "should become unreachable, since the resolver always has a tenant by then"" | **VIOLATED** | `resolved_identity.py:437` yields `None` for a Host naming no tenant; `posture.py:267-269` documents the case as reachable |
| SD-8 | ":182 the namespace split has one home: the registry is the authority, enforced at config load | TRUE | `tests/unit/test_request_signing_namespace_split.py` — 11 passed |
| SD-9 | ":204 a `protocol_methods_required_for` membership must not be satisfied by a `tools/call` body | TRUE | `verifier.py:256-274` never passes `protocol_method`; there is no value at that boundary that could be matched |
| SD-10 | ":212 the protocol-method namespace is not a gap: all four `pushNotificationConfig` methods are declined | TRUE | P-BOUNDARY(2) region — `adcp_a2a_server.py:433,441,449,457` all `raise PushNotificationNotSupportedError()` |
| SD-11 | ":243 one request, one call — batching is refused | TRUE | `adcp_a2a_server.py:234-241` raises `InvalidRequestError` on a second skill |
| SD-12 | Decision 3 — `CODE_TABLE` is the sole authority for buyer-facing text | TRUE | P-CHALLENGE bodies carry `CODE_TABLE` strings verbatim |
| SD-13 | Decision 4 "Landed" — none of `Transport.IMPL`, `ImplDispatcher`, `synthesized_error_envelope` exists | TRUE | `grep -rn` → only comments recording the deletion |
| SD-14 | Staging "Landed" — request-lifecycle.md "does not yet name the RFC 9421 signature as one of the credentials the resolver reads … the remaining piece of stage 6" | TRUE (still outstanding) | `grep -in "signature" docs/development/request-lifecycle.md` → no hit |

### Module-level claims in the signing code

| # | Claim | Verdict | Probe |
|---|---|---|---|
| C-1 | `capture.py:260` "It must be registered **INNERMOST** of the app's middlewares" | **VIOLATED** | P-STACK — index 2 of 4 |
| C-2 | `capture.py:87` the header list is kept as received, not a collapsed dict | TRUE | `tests/unit/test_signed_exchange_capture.py::test_the_raw_header_list_survives_a_repeated_header` |
| C-3 | `capture.py:101` `presents_signature()` answers "either header present" | TRUE | code + `negative/002` (one header without the other → `header_malformed`) |
| V-1 | `verifier.py:823` "Headers that **MUST** arrive on exactly one line when a signature covers the request" | ~~VIOLATED~~ → TRUE (`18e2b31c6`) | P-HEADERS — `signature` is in the list, and one `joined_headers` view removes the per-transport collapse the list was guarding against |
| V-2 | `verifier.py:37` "What the boundary cannot see" enumerates two classes of unverified request | TRUE but incomplete | P-VARIANTS / P-A2ACFG — one further class stands (the wrong-method / unrouted case under T1). The A2A envelope class is no longer unverified-but-served: it is DECLINED (`51006ea38`) |

### Patterns, guards, egress

| # | Claim | Verdict | Probe |
|---|---|---|---|
| P-1 | `outbound-egress.md` — nothing under `src/` writes its own address policy; no `ipaddress` / `socket.gethostbyname` / hostname blocklist outside the gateway | TRUE | `grep -rnE '^\s*(import|from)\s+(httpx\|requests\|aiohttp\|socket\|ipaddress)\b' src/` → 5 hits, all the gateway, the egress policy module, the vendored canonicalizer and the MCP client seam. Zero under `src/core/signing/`. `ruff check --config ruff-egress.toml` clean; `test_ruff_egress_bans.py` green |
| P-2 | `patterns-reference.md:346` transports never resolve an identity and never call an implementation directly | TRUE | P-BOUNDARY(1) |
| P-3 | `patterns-reference.md:354` `_impl` raises `AdCPSalesAgentError`, never `ToolError`; imports nothing from fastmcp/a2a/starlette/fastapi | TRUE | `ruff-boundary` clean; `tests/unit/test_transport_agnostic_impl.py` green |
| P-4 | `patterns-reference.md:407` a raise site names a code and supplies structured facts; it never authors buyer-facing text | TRUE | `verifier.py:457` passes `error_code=` + `internal_detail=`, no message |
| P-5 | `structural-guards.md:119` allowlists shrink, never grow | Out of scope for this audit | not measured |
| T-1 | `tests/helpers/signing.py:170` "nothing rewrites a request body between the socket and the signature subject … the collision is structurally absent" | **VIOLATED as stated** | P-STACK + `src/app.py:546-580`. True in effect only because that rewriter is a no-op (V4) |

### Spec obligations exercised end to end

| Spec | Obligation | Verdict | Probe |
|---|---|---|---|
| security.mdx @ v3.1.1 :1224 | unsigned + no acceptable credential on a `required_for` operation → `request_signature_required` | TRUE | P-CHALLENGE(`required`) on all three transports; `negative/001`, `negative/027` |
| :1225 | one signature header without the other → `request_signature_header_malformed` | TRUE | `negative/002` in the conformance run |
| :1226 | a malformed `Signature-Input` is refused even outside `required_for`, and blocks bearer fallback | TRUE | P-MALFORMED — POST with `MALFORMED_SIGNATURE_HEADERS` and a valid bearer, `supported` bucket → `401 Signature error="request_signature_header_malformed"` |
| :1269 | unsigned but bearer-authenticated on a `required_for` operation MUST NOT be rejected | TRUE | `verifier.py:301`; `tests/unit/test_request_signature_composition_rule.py` (6 passed) |
| :1053 | verifiers MUST NOT cross-namespace match | TRUE | SD-9 |
| :1375 / :1465 | a payload carrying webhook `authentication` requires a signature regardless of bucket | ~~VIOLATED on 1 of 4~~ → TRUE (`51006ea38`) | P-A2ACFG — the three AdCP locations refuse; the fourth was never an AdCP location and is now declined outright |
| :1464 | sellers MUST log every request arriving with a non-empty `authentication` block | ~~VIOLATED on 1 of 4~~ → TRUE (`51006ea38`) | P-A2ACFG — same three locations log; the declined channel never becomes a registration to log |
| "`WWW-Authenticate` format" | `Signature error="<code>"`, no `realm`, no other parameters | TRUE | `challenge_for` at `signature_codes.py`; `tests/unit/test_signature_challenge_string.py` pins all 28 against the SDK helper |

---

## VIOLATED findings

### V1 — the capture's surface gate ignores ASGI `root_path`, so a root-path deployment runs with the verifier silently off

> **FIXED, `5d87631cb`.** The predicate reads `path_from_asgi_scope(scope)`, the published
> route-table rule that had no callers; `src/app.py`'s `/a2a` predicate was blind the same
> way and reads it too. `_signed_path` still reads `raw_path` WITH the prefix — the two
> rules are opposites and both sites say so. The guard's allowlist row moved from the
> `scope.get("path")` form to the `path_from_asgi_scope()` form: same site, same count, and
> its stated reason is now satisfied by the helper rather than asserted over a read that did
> not satisfy it. P-ROOTPATH re-run: `root_path=/adcp` reports `exchange_captured=True`
> where it reported `False`, with `root_path=""` as the control; restoring the old read
> reddens it again.

**Site.** `src/core/signing/capture.py:269`

```python
if scope.get("type") != "http" or not is_adcp_surface(str(scope.get("path", ""))):
    await self.app(scope, receive, send)
    return
```

**Failure path.** In ASGI, `scope["path"]` carries the mount prefix; the router strips it
(`starlette.routing.get_route_path`) before matching. Deployed under a root path — uvicorn
`--root-path=/adcp`, `FastAPI(root_path=...)`, or a proxy that sets one — the router still
dispatches `POST /adcp/api/v1/products` to `get_products`, while
`is_adcp_surface("/adcp/api/v1/products")` is `False`. No `HttpExchange` is recorded, so
`SignatureSubject.exchange` is `None`, `_carries_signature` is `False`, and
`verify_inbound_signature` (`src/core/signing/verifier.py:236`) takes the
"this request presented no signature" branch: it records `record_request_unsigned(op,
"absent")` and returns `None`. Every signed request is treated as unsigned, and on a
`required_for` operation a bearer-authenticated caller sails through under the composition
rule. Nothing logs a fault. This is the silent-unverified shape the verifier's own
docstring says the area exists to remove.

**Why it is a rule violation and not just an oversight.** `src/core/http_utils.py:18-34`
publishes the rule — "anything matching a path against a route table **or a surface
allowlist** has to strip it first" — and `path_from_asgi_scope` has **zero callers under
`src/`**. `tests/unit/test_architecture_signed_target_uri_raw_path.py:53-57` allowlists this
exact line by name with the reason "it must select the same requests the router dispatches
to `/mcp`, `/a2a` and `/api/v1`". The intent is written down twice; the code implements
neither.

**Reachability today: latent.** `config/nginx/nginx-development.conf` and
`nginx-multi-tenant.conf` proxy with `$request_uri` and set no root path, and nothing in
`src/` passes `root_path=`. The defect fires the first time the app is mounted under a
prefix.

**Probe (P-ROOTPATH).** Raw-ASGI drive of the real `src.app.app`, spying on
`_boundary.invoke_tool` to see the `exchange` argument:

```
root_path=''       path='/api/v1/capabilities'        HTTP 200 reached_boundary=True exchange_captured=True  -> consistent
root_path='/adcp'  path='/adcp/api/v1/capabilities'   HTTP 200 reached_boundary=True exchange_captured=False -> ROUTED BUT UNCAPTURED
```

The `root_path=''` row is the positive control: the same probe reports "consistent" when
the two predicates agree, so the second row is a measurement rather than a tautology.

**Fix.** One line, and it is the call the helper exists for:

```python
from src.core.http_utils import path_from_asgi_scope
...
if scope.get("type") != "http" or not is_adcp_surface(path_from_asgi_scope(scope)):
```

Then remove the by-name allowlist row in
`tests/unit/test_architecture_signed_target_uri_raw_path.py` (its reason is now satisfied by
the helper rather than by an exemption) and add a guard case pinning that the capture's
predicate and `starlette.routing.get_route_path` select the same path. Note that
`_signed_path` must keep reading `raw_path` **with** the prefix: the client signed the URL
it dialled. The two rules are opposites and the module already says so at `capture.py:197-203`.

`src/app.py:549` (`request.url.path == "/a2a"`) has the same root-path blindness. It is the
compat shim of V4 and matters less, but fix it in the same pass.

### V2 — a repeated header line resolves first-wins on REST and A2A and last-wins on MCP

> **FIXED, `18e2b31c6`,** with the structural half rather than the allowlist row alone.
> `joined_headers` (`src/core/signing/capture.py`) is one derivation from the captured
> LINES, per RFC 9110 §5.3 — repeated lines are one comma-joined value — and
> `_resolve_identity` reads it whenever the request was captured, so the bearer, the tenant
> hint, `_parse_keyid` and the SDK checklist all share it. `_target_uri` goes through it
> too, having previously built its own last-wins dict. A joined value discards no line, so
> the ambiguity now reaches a rule that can refuse it; where no rule exists it fails closed
> identically everywhere. `_MALFORMED_VALUE_RULES` gains the `signature` row it was missing,
> so a duplicate `Signature` is `request_signature_header_malformed` at checklist step 1 on
> every transport. P-HEADERS re-run through the real app: duplicated, all three answer the
> identical challenge and none reaches the checklist; sent once, all three read the same
> value. Restoring the transport's own container reddens it.

**Sites.**
- `src/routes/api_v1.py:100` passes `request.headers` — a Starlette `Headers`, whose
  `__getitem__` returns the **first** matching line.
- `src/core/main.py:409` passes `get_http_headers(include_all=True)` — a dict built by
  iterating `.items()`, which yields every repeated line and therefore keeps the **last**.
- `src/a2a_server/context_builder.py` inherits `state["headers"] = dict(request.headers)`
  from the SDK builder — `dict()` goes through `keys()` + `__getitem__`, so **first**.

Both readers downstream take the first match over `.items()`
(`adcp.signing.verifier._lookup`, `src/core/http_utils.get_header_case_insensitive`), so the
container decides the answer.

**Failure path.** One identical HTTP message produces two different verification inputs
depending on which surface it arrives on. `src/core/signing/verifier.py:823-827` is the gate
built to close exactly this — "every dict view of ASGI headers LAST-WINS on a repeated name
rather than joining it, so a proxy-inserted second line rewrites a covered value with nothing
anywhere to notice" — and its list is derived from `_MALFORMED_VALUE_RULES`, which covers
`signature-input`, `content-type`, `content-digest` and `host`. `signature`, the header that
carries the credential, is not in it. Neither is `authorization`, and `_strict_header_precheck`
runs only when a signature is present and `posture.supported` is true, so an unsigned request
is never checked at all.

**What this does and does not buy an attacker.** I demonstrated no authentication bypass. A
second `Signature` line makes a legitimate signed request verify on REST/A2A and fail on MCP
(or the reverse), which is a denial of service and a conformance divergence. A second
`Authorization` line is resolved to a different principal on MCP than on REST/A2A, and a
second `x-adcp-tenant` selects a different tenant. Since both credentials are looked up
inside the tenant the same mapping selected, the mismatch does not cross a tenant boundary.
By the owner's stated rule — #1721 eliminated transport divergence, so any divergence is a
defect — it is a defect regardless.

**Probe (P-HEADERS).** The three production expressions applied verbatim to one raw ASGI
header list:

```
Authorization  -> _extract_auth_token:
    rest   'Bearer TOKEN-VICTIM'      mcp 'Bearer TOKEN-ATTACKER'   a2a 'Bearer TOKEN-VICTIM'    -> DIVERGENT
x-adcp-tenant  -> _detect_tenant:
    rest   'tenant-a'                 mcp 'tenant-b'                a2a 'tenant-a'               -> DIVERGENT
Signature      -> SDK _lookup:
    rest   'sig1=:FIRST:'             mcp 'sig1=:LAST:'             a2a 'sig1=:FIRST:'           -> DIVERGENT
```

and the same claim driven through the **real app** on all three transports, spying on what
`verify_request_signature` was handed:

```
=== repeat=True ===                      === repeat=False (control) ===
  rest  'sig1=:RVJTVA==:'                  rest  'sig1=:RVJTVA==:'
  mcp   'sig1=:TEFTVA==:'                  mcp   'sig1=:RVJTVA==:'
  a2a   'sig1=:RVJTVA==:'                  a2a   'sig1=:RVJTVA==:'
  VERDICT: DIVERGENT                       VERDICT: consistent
```

The `repeat=False` run is the positive control: the probe passes when the header arrives on
one line, so the failure is caused by the repeat and not by the harness.

**Fix.** Two parts.

1. Minimal, in the module that already owns the rule: add a `signature` row to
   `_MALFORMED_VALUE_RULES` (`src/core/signing/verifier.py:816`), which also adds it to the
   derived `_SINGLE_LINE_SIGNED_HEADERS`. Give it the RFC 8941 §3.2 duplicate-key rule
   `_duplicate_signature_input_label` already implements for the companion header — a
   `Signature` value is a Dictionary keyed by label, so a repeated label there is the same
   ambiguity. That refuses the duplicate with `request_signature_header_malformed` at step 1
   on every transport, which is the outcome the four existing rows produce.
2. Structural, for the bearer and the tenant hint: the divergence is in the container, not in
   the signing code, so the durable fix is for `invoke_tool` to hand `_resolve_identity` one
   header view. The capture already holds `raw_headers` for the AdCP surfaces; deriving the
   mapping there (one function, latin-1 decode, explicit first-wins or explicit refusal on a
   repeat) removes the per-transport container entirely. This is the same "one derivation,
   three readers" argument the capture was built on, applied to headers as well as to
   `@target-uri`.

Either fix needs a guard: a test that asserts the three transports produce the same mapping
from one raw header list would have caught this and is the near-miss form for the existing
`test_architecture_signed_target_uri_raw_path.py`.

### V3 — `SignedExchangeCapture` is not innermost

**Sites.** `src/core/signing/capture.py:260` claims "It must be registered INNERMOST of the
app's middlewares". `src/app.py:625` repeats it in the stack comment
("3. SignedExchangeCapture (innermost …)"). `src/app.py:546` registers
`a2a_messageid_compatibility_middleware` via the `@app.middleware("http")` decorator, which
is `add_middleware(BaseHTTPMiddleware, …)`, at import time **before** `:636`. Last registered
is outermost, so the capture is third of four.

**Probe (P-STACK).**

```
0 outermost->innermost: AuthChallengeResponder
1 outermost->innermost: CORSMiddleware
2 outermost->innermost: SignedExchangeCapture
3 outermost->innermost: BaseHTTPMiddleware <function a2a_messageid_compatibility_middleware>
```

**Effect.** The safety property the docstring is protecting is that no body rewriter runs
*above* the capture, and that holds — the rewriter is below it, so the capture records the
wire bytes and the digest covers what the client sent. What does not hold is the placement
rule as written, and a rewriter *below* the capture is its own problem: the seller would act
on a body the signature does not cover. Today it does not, only because that rewriter is
broken (V4).

**Fix.** Register `a2a_messageid_compatibility_middleware` with `add_middleware` above the
capture, or state the real rule in both places: the capture must sit **above every body
rewriter and below nothing that reads a credential**, and no middleware may rewrite a body
below it. Then add the guard the claim deserves — an import-time assertion or a unit test
over `app.user_middleware` that fails if any `BaseHTTPMiddleware` is registered below
`SignedExchangeCapture`. Correct the count in `request-lifecycle.md:126` and in the design
doc's "Landed" note at the same time.

### V4 — the A2A `messageId` compatibility rewrite has never run on this Starlette

**Site.** `src/app.py:547-580`. The middleware reads the body, rewrites a numeric
`message.messageId` or JSON-RPC `id` to a string, rebuilds
`StarletteRequest(request.scope, receive=_receive)` and calls `call_next(request)`.

**Failure path.** Starlette 1.3.1's `BaseHTTPMiddleware.__call__` builds a `_CachedRequest`
from the original `receive` and closes over `wrapped_receive`; `call_next` ignores the
`request` argument's `receive` entirely. The rewritten bytes are discarded. The middleware
still logs "Converting numeric messageId 1 to string for compatibility" — so the server logs
a conversion it did not perform.

**Probe (P-REWRITE).** Isolated, no app, no capture:

```
starlette 1.3.1
sent      : {"id": 7, "hello": "world"}
rewritten : {"id": "7", "hello": "world"}
downstream: {"id": 7, "hello": "world"}
VERDICT: the rewrite is a NO-OP — call_next ignores the rebuilt request's receive
```

and against the real app:

```
POST /a2a  {"id": 7, ..., "messageId": 1, ...}
log:  Converting numeric messageId 1 to string for compatibility
log:  Converting numeric JSON-RPC id 7 to string for compatibility
wire: 200 {"error":{"code":-32602,"message":"Invalid params",
           "data":"Failed to parse messageId field: expected string or bytes-like object, got 'int'.."}}
```

**Fix.** Either delete the middleware (and `request-lifecycle.md:138-140` with it), or make
it effective by rewriting the ASGI `receive` on the scope rather than on a rebuilt `Request`
— which means it becomes a pure-ASGI middleware, and it must then be registered **above**
`SignedExchangeCapture` so the capture still records the bytes the client signed. Deleting is
the smaller change and matches this agent's stated A2A posture (native 1.0 only, no 0.3
compatibility, `src/app.py:346-358`); a numeric `messageId` is malformed against the proto
schema and refusing it is a defensible answer. Whichever is chosen, the log line must stop
claiming a conversion.

### V5 — an A2A `SendMessage` registering webhook credentials in the protocol envelope is neither logged nor refused

> **FIXED, `51006ea38`,** by the owner's ruling: refuse, do not merge. AdCP defines no
> protocol-envelope registration channel, so #1721 declined the A2A push-notification
> capability wholesale (`push_notifications=False` on the agent card, four
> `tasks/pushNotificationConfig/*` handlers refusing). This envelope field was the fifth
> entry point to that same capability and the only one that neither served nor refused;
> `AdCPRequestHandler._refuse_envelope_push_config` refuses it now, beside the other four.
> Merging it into the skill parameters would have built an AdCP registration channel out of
> a transport-specific one, which is what declining the capability decided against.
>
> The harness half mattered more than the drop. It was sending the AdCP field to that
> envelope on the stated grounds that production read it there, so the AdCP location on A2A
> — the DataPart, where an A2A buyer really does register a webhook — was graded on no
> transport but MCP and REST. It travels in the skill parameters now, as it already did on
> the in-process leg. P-A2ACFG re-run: the three AdCP locations answer
> `401 Signature error="request_signature_required"` with the `:1464` log, and the envelope
> answers `-32003 Push Notification is not supported` instead of a completed operation.
>
> **What this cost.** `CreateTaskPushNotificationConfig` is no longer a credential location
> in `credential_registrations` — a place a buyer cannot register at is not a place to grade
> a signature challenge — and its dispatch chain is deleted rather than left dead. Nothing
> now exercises that route, so a regression that started SERVING it would go unnoticed.
> That is a real loss and it is recorded rather than papered over; the seam for adding a
> second SERVED location is unchanged.
>
> Both BDD scenarios that reported "got None (HTTP 200)" are green:
> `tests/bdd/test_request_signing_enforcement.py` is 27 passed.

**Site.** `src/a2a_server/adcp_a2a_server.py:212-311`. `on_message_send` reads
`params.message.parts` and nothing else; `params.configuration` is never touched.

**Failure path.** security.mdx @ v3.1.1 :1464 makes the log duty per **request** and
unqualified; :1465 makes a signature mandatory for a signing-capable seller when
`authentication` is present. `registers_webhook_credentials`
(`src/core/signing/webhook_credentials.py`) reads the **validated request**, which is the
right place for the three locations that reach it. A webhook config sent where the A2A 1.0
envelope carries it never becomes part of that request, so neither obligation runs, and the
buyer receives `200` for a registration the seller did not perform.

**Probe (P-A2ACFG).** One tenant, `request_signing.supported: true`, `get_products` in no
bucket, so only the :1465 escalation can refuse. Unsigned, bearer-authenticated, four
locations:

```
location                                                  HTTP  WWW-Authenticate                                  :1464 logged
rest  body.push_notification_config                        401  Signature error="request_signature_required"      True
mcp   tools/call arguments                                 401  Signature error="request_signature_required"      True
a2a   skill input.push_notification_config                 401  Signature error="request_signature_required"      True
a2a   params.configuration.task_push_notification_config   200  None                                              False   <-- NOT ENFORCED
VERDICT: NOT ENFORCED at: ['a2a   params.configuration.task_push_notification_config']
```

The first three rows are the positive control in the same run: the probe reports enforcement
where it exists, so the fourth row is a measurement.

Static confirmation (P-BOUNDARY(2)), an AST walk of `on_message_send`:

```
on_message_send attribute reads: ['A2A','CopyFrom','HasField','MessageToDict', ... ,'parts','status', ...]
'configuration' attribute reads: NONE | string literal 'configuration': NONE
positive control — 'parts' attribute reads found at: [227]
```

**Note on the harness.** `tests/harness/_base.py:429-435` states that production "takes it
from `params.configuration.task_push_notification_config` and threads it into the skill
handler", and `_run_a2a_over_http` therefore sends every A2A webhook registration there. That
statement is false of `src/`, which means the A2A leg of every webhook-registration scenario
is dispatching to a location production ignores. Fixing the harness without fixing production
would move the failures rather than remove them.

**Fix.** In `on_message_send`, read `params.configuration.task_push_notification_config` and
merge it into the skill parameters before `_dispatch_skill`, so the config lands on the
validated DTO exactly as the MCP and REST locations do and both :1464 and :1465 follow with no
new decision anywhere. If the deliberate answer is instead that this agent declines the
channel — consistent with the four `pushNotificationConfig/*` methods that already
`raise PushNotificationNotSupportedError()` — then refuse it explicitly rather than dropping
it, and record the choice beside those four. Answering `200` for a registration that did not
happen is the one option that is wrong either way.

### V6 — `docs/development/request-lifecycle.md` is stale on three claims in this path

1. `:126` "registers three HTTP middlewares" — four (V3/P-STACK).
2. `:314-316` "AUTH_MISSING … runs before tenant detection, so an anonymous caller costs no
   database lookups" — the tenant is loaded first (`resolved_identity.py:436-437`). The change
   is correct and deliberate: security.mdx :1268 makes `request_signature_required` the answer
   an unauthenticated caller earns on a `required_for` operation, and which operations are in
   that bucket is seller data that cannot be known before the tenant row is read. The code
   comment at `resolved_identity.py:422-435` says so and names the vectors that caught the
   merge getting it backwards. The doc still describes the old ordering, including the cost
   claim, which is now false.
3. `:602-606` "The SDK's **default** context builder places `dict(request.headers)` …" — it is
   `AdCPCallContextBuilder` (`src/app.py:366`), which adds the capture.

The design doc's own staging note already records a fourth: the file "does not yet name the
RFC 9421 signature as one of the credentials the resolver reads", and calls amending it "the
remaining piece of stage 6". `grep -in signature docs/development/request-lifecycle.md`
returns nothing, so that is still outstanding. All four belong in one edit.

### V7 — `posture_for_tenant(None)` is still reachable

`signing-vs-request-boundary.md` Decision 2 says the `None` case "should become unreachable,
since the resolver always has a tenant by then". `resolved_identity.py:437` yields `None`
whenever `_detect_tenant` identifies no tenant — a `Host` that matches no virtual host or
subdomain, no `x-adcp-tenant`, no `Apx-Incoming-Host`, and not localhost — and
`verify_inbound_signature` calls `posture_for_tenant(tenant)` unconditionally at
`verifier.py:231`. The landed code documents the case as reachable and inert
(`posture.py:267-269`), which is a correct description of what it does; the design doc's
prediction is simply not true of the tree. Severity is low — such a request resolves no
principal either — but the design doc should be corrected rather than left as a claim a
reader will check the code against.

## Test-side defects (production is exonerated for both)

### T1 — `test_a_malformed_signature_outranks_an_unresolvable_counterparty` probes with a method the route does not serve

`tests/integration/test_request_signature_discovery.py:182` sends
`client.get(BODYLESS_ADCP_PATH, …)`. Every AdCP REST binding is `POST` (or `PUT`), so
`GET /api/v1/capabilities` is `405` in the router and never reaches `_resolve_identity`.
`rejection_code` reads `None` and the ordering canary — described in its own module docstring
as "the single most important test in this module" — grades nothing.

Production is correct. The same request as a `POST`:

```
PROBE GET  status=405 www-auth=None
PROBE POST status=401 www-auth=Signature error="request_signature_header_malformed"
```

The step-1 pre-check does outrank the step-7 discovery failure, exactly as
`_FailedDiscoveryJwksResolver` intends. Change the probe to `POST` with a body.

This does surface a third class for the verifier's "What the boundary cannot see" list: a
request that does not resolve to a registry row — wrong method, unknown path — is answered by
the router and never verified. That is defensible (it names no AdCP operation) but it is not
currently written down beside the two classes that are.

### T2 — `test_a_failure_realization_reaches_the_operation_frame_not_the_handshake` asserts a session id a stateless mount never mints

`tests/integration/test_harness_signed_dispatch.py:485` requires the refused frame to carry
`mcp-session-id`. `src/app.py:128` builds the mount with `stateless_http=True`, so
`initialize` answers without one — which `tests/harness/_base.py:2297-2309` documents in
detail. The test's first assertion (the refused frame is `tools/call`) passes, so the property
it exists to protect holds; only its chosen evidence has evaporated. Replace the session-id
assertion with one that distinguishes the frames on this mount — for example, that the
refusing response answers the third POST and that the two handshake POSTs answered `200`.

### Baseline for the record

- `tests/integration/test_signing_conformance_vectors.py` — **82 passed**.
- `tests/integration/test_request_signature_{middleware,operations}.py`,
  `test_harness_signed_dispatch.py`, `test_request_signature_discovery.py` — 64 passed,
  2 failed (T1, T2) with the working-tree harness fixes applied. At committed `HEAD` the same
  set is 4 failed: the two above plus both A2A legs of
  `TestSignedDispatchAcrossTransports`, which the uncommitted `tests/harness` changes repair.
- `tests/unit` — 6212 passed, 10 failed. All ten are one environmental cause: a stale
  `test-results/bdd_scenario_liveness.json` written by a narrowed BDD run
  (`-k 'registration and a2a'`, 2 collected). Moving that file aside turns the ten into
  **66 passed**; it is untracked and gitignored. No code defect.
- `ruff check` with `ruff-egress.toml`, `ruff-boundary.toml`, `ruff-ownership.toml`, and
  `ast-grep scan --config sgconfig.yml` — all clean.

## Known and parked, recorded so it is not re-discovered

**Body validation precedes signature verification.** `serve` evaluates
`validated_request(tool_name, raw, protocol)` as an argument to `invoke_tool`
(`src/core/tools/_boundary.py:258`), so a request whose body the DTO refuses is answered
`400 INVALID_REQUEST` with the verifier checklist never run — even when the operation is in
`required_for` and the request is unsigned. security.mdx's checklist is written as a gate in
front of the operation, so the spec-conformant answer for that request is
`401 request_signature_required`. I observed this directly while building P-A2ACFG (an
invalid `get_products` body answered `INVALID_REQUEST` at 400 on all three transports). The
owner has ruled this parked as a conformance-runner concern
(`.claude/notes/BRIEF-harness-wire-carrier.md:88-94`). Recorded here with its citation so the
next audit does not file it again; it is a real ordering divergence from the spec, not a
test artefact.

**Over-cap bodies are refused as `request_signature_header_malformed` at step 1.**
`src/core/signing/verifier.py:336-345`. Because step-1 refusals bypass the warn arm
(`_handle_rejection`, `is_precheck`), a signed request whose body exceeds
`max_signed_body_bytes` (default 10 MiB) is refused with 401 even in a `warn_for` bucket,
where warn semantics say a signed-but-invalid request completes. The spec has no code for
"body too large to digest", so some invention is unavoidable; the choice of a step-1 code
carries the warn-bypass with it. Not filed as a defect — the cap is 10 MiB and the reasoning
is written at the site — but worth a sentence there saying the warn bypass is intended.

## What I could not verify, and why

- **Whether a fronting proxy lets a duplicate header through.** V2 is demonstrated at the
  application boundary, driven through `src.app.app`. nginx rejects a duplicate `Host`; I did
  not test what `config/nginx/*.conf` does with a duplicate `Signature`, `Authorization` or
  `x-adcp-tenant`, which needs the compose stack. The defect is in the app either way, but
  its exploitability behind this deployment's proxy is unmeasured.
- **The e2e and BDD legs.** Everything here ran in-process against `src.app.app` with the
  worktree's agent-db. `tests/e2e/test_request_signature_{required,accepted}_e2e.py` and
  `tests/bdd/test_request_signing_enforcement.py` need the Docker stack, and
  `.claude/notes/BRIEF-harness-wire-carrier.md` records 13 BDD failures with a known harness
  cause that was still being fixed in the working tree. I did not run them, so I am making no
  claim about the e2e_rest leg or about `e2e_rest_known_failures.txt`.
- **Whether V5's dropped config was stored anywhere.** Moot since `51006ea38` — the channel
  is refused before any handler runs — but it was never traced: the original finding was
  scoped to what was observed on the wire (`200`, no log, no refusal), not to whether the
  a2a-sdk persisted the config somewhere before dispatch.
- **That the declined A2A push-config route stays declined.** Deleting the second credential
  location took the only dispatcher that could send `CreateTaskPushNotificationConfig` with
  it. The five declines are asserted by reading the code, not by driving the route.
- **`protocol_methods_*` enforcement.** Ungraded by construction and already recorded as such
  in `src/core/signing/verifier.py:37-59`. I confirmed the premise that makes it acceptable
  (all four `pushNotificationConfig/*` handlers decline, the namespace has zero registry rows)
  but did not attempt to reach `bucket_for` with a protocol method, because no code path does.
- **Cryptographic correctness of the checklist itself.** Out of scope by Pattern #9 — the SDK
  owns it. I verified only that nothing under `src/core/signing/` re-derives address
  validation, IP pinning or signature verification, and that the one vendored component
  (`src/vendor/adcp_canonical`, upstream 7.0.2 verbatim) is a copy rather than a second
  derivation, which `src/core/signing/canonical.py:29-34` states and the ban configs enforce.
- **Seventeen out-of-scope claims** were extracted and not verdicted: the idempotency digest
  and exclusion list (`request-lifecycle.md:449-485`), repository/ORM and UoW rules
  (`patterns-reference.md:24-132`), factory-based fixtures, the allowlist ratchets
  (`structural-guards.md:119-126`), the token-hash migration (`request-lifecycle.md:343-359`),
  and the admin-UI mount ordering. None of them touches signing, identity resolution, error
  emission or the request boundary.

## Probe index

Every probe below was run against this worktree with `source .agent-db.env`. Probes marked
(pytest) were written as a temporary module under `tests/integration/` and removed
afterwards; nothing but this document is committed.

| Id | What it establishes | How |
|---|---|---|
| P-STACK | the middleware stack, outermost first | `uv run python -c "from src.app import app; [print(i, m.cls.__name__) for i, m in enumerate(app.user_middleware)]"` |
| P-CAPTURE(a) | `SignedExchangeCapture` contains no `raise`, no direct `send()`, no status literal, and both `__call__` exits await `self.app` | AST walk of `src/core/signing/capture.py` |
| P-CAPTURE(b) | `_buffer_body` replays losslessly on all six exits (one chunk, three chunks, cap-on-last, cap-mid-body, disconnect mid-body, immediate disconnect); the downstream disconnect is not swallowed | drives `_buffer_body` with a receive channel that returns a `<CHANNEL-EXHAUSTED>` sentinel once drained. **Mutations that redden it:** replacing `pending.extend(trailing)` with `pending.extend([])`; forcing `more_body=False` on the replayed message |
| P-CHALLENGE | 401 + byte-exact `WWW-Authenticate` for `request_signature_header_malformed` and `request_signature_required` on REST, MCP and A2A | (pytest) real app + `TestClient`. **Mutation that reddens it:** `_challenge_for_code` returning `_CHALLENGE_BY_CODE["AUTH_INVALID"]` for signature codes — which also reddens 28 of the 82 conformance vectors |
| P-ORDER | signature verification precedes both bearer refusals | **Mutation:** move the `_signature_credential(...)` call below the `AdCPAuthRequiredError` raise → `negative/001` and `negative/027` fail, 80 pass |
| P-HEADERS | the three transports' header containers disagree on a repeated `Signature`, `Authorization` and `x-adcp-tenant` | the three production expressions over one raw header list, plus (pytest) the real app on all three transports with a single-line control |
| P-VARIANTS | no path variant (trailing slash, case, `//`, `..`, `%2f`) is routed to a tool without being captured | (pytest) 11 variants, comparing `is_adcp_surface` against whether `invoke_tool` received an exchange |
| P-ROOTPATH | under `root_path=/adcp` the router reaches a tool the capture never recorded | (pytest) raw-ASGI drive with a `root_path=''` control |
| P-MALFORMED | a malformed `Signature-Input` with an unresolvable counterparty is refused at step 1, not step 7 | (pytest) POST probe; the GET form of the same probe is T1 |
| P-A2ACFG | :1464 and :1465 fire on three credential locations and not on the A2A protocol envelope | (pytest) four locations in one run |
| P-BOUNDARY | (1) exactly one call site invokes a registry implementation, inside `_invoke`, reached only from `invoke_tool` after `_resolve_identity`; (2) `on_message_send` never reads `configuration`, with `parts` as the positive control | AST walk of `src/` |
| P-REWRITE | Starlette 1.3.1's `call_next` ignores a rebuilt request's `receive` | 30-line isolated Starlette app |
| P-EGRESS | no address policy under `src/` outside the gateway | `grep -rnE '^\s*(import\|from)\s+(httpx\|requests\|aiohttp\|socket\|ipaddress)\b' src/`, with a two-line positive control file proving the pattern matches |
