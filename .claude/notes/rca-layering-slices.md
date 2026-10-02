# Responsibility map: `sync_creatives` and `create_media_buy`

Repo `.`, branch `feature/spec-gaps-1210`, `adcp==6.6.0`.
Every claim below is a file:line with the code quoted. Layer vocabulary:

- **T** transport — decode wire input, resolve identity, encode wire output, translate errors
- **A** application (use case) — orchestrate one unit of work, decide the transaction boundary
- **D** domain (model) — rules that are properties of an entity (a format matches a product; an approval mode implies a status)
- **I** infrastructure — persistence, ad-server adapters, creative-agent HTTP, Slack, executors

---

# Section 1 — Slice 1: `sync_creatives`, ordered trace

## 1.1 The trace

| # | Site | What it does | Responsibility | Sits in |
|---|------|--------------|----------------|---------|
| 1 | `creatives/sync_wrappers.py:52` `identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None` | read identity off FastMCP state | T | T ✅ |
| 2 | `sync_wrappers.py:57` `identity = enrich_identity_with_account(identity, account)` | resolve `AccountReference` → `account_id`; **opens an `AccountUoW`** (`transport_helpers.py:166-196`) | T orchestrating I | T ✅ (correctly *before* `_impl`) |
| 3 | `sync_wrappers.py:60` `validation_mode_str = enum_value(validation_mode) or "strict"` | enum→str coercion | T ✅ |
| 4 | `sync_wrappers.py:62-72` `response = _sync_creatives_impl(creatives=…, assignments=…, …)` | 9 loose kwargs, **no request model** | T ✅ but see §3.1 |
| 4' | `sync_wrappers.py:110-127` `sync_creatives_raw` | the same 5 steps again for A2A/REST | T — duplicated, §3.1 |
| 5 | `creatives/_sync.py:83-86` `if creative_ids: creatives = [c for c in creatives if _get_field(c, "creative_id") in creative_ids_set]` | AdCP 2.5 scope filter, via a **dict-or-model accessor** | A, but the accessor is a symptom of loose typing (§3.2) |
| 6 | `_sync.py:93-95` `principal_id = require_principal_id(...)` / `require_identity` / `tenant = require_tenant(identity, ...)` | auth gate; `require_tenant` returns `dict[str, Any]` (`auth.py:368-372`) | A ✅ / typing ✗ (§3.6) |
| 7 | `_sync.py:99-114` webhook SSRF gate + `webhook_url_for_log` | registration-time input validation | **T** — sits in A ✗ (§2 mismatch M1) |
| 8 | `_sync.py:136` `approval_mode = tenant.get("approval_mode", "require-human")` | tenant policy read from a dict | **D** — sits in A ✗ (§3.5) |
| 9 | `_sync.py:143-144` `registry = get_creative_agent_registry()` … `all_formats = run_async_in_sync_context(registry.list_all_formats(tenant_id=…))` | **network I/O**, deliberately hoisted *before* the transaction | I, called from A ✅ (comment at `_sync.py:139-140` states the reason) |
| 10 | **`_sync.py:156-157`** `with ExitStack() as stack:` / `uow = stack.enter_context(CreativeUoW(tenant["tenant_id"], dry_run=dry_run))` | **transaction opens**. `dry_run` is a *disposal* decision, not a second code path | A ✅ — this is the correct shape |
| 11 | `_sync.py:162` `provenance_policies = creative_repo.get_provenance_policies()` | read | I via repo ✅ |
| 12 | `_sync.py:174-182` `isinstance(raw_creative, CreativeAsset) / dict / model_validate` | wire-shape normalization **inside** the transaction | **T** — sits in A ✗ (M2, §3.2) |
| 13 | `_sync.py:186` `_validate_creative_input(creative, registry, principal_id)` → `_validation.py:26-141`; `_validation.py:128` `format_spec = fetch_format_spec(agent_url, format_id)` | schema+business validation, **network I/O inside the transaction** | D+I; the fetch is I fired from inside the tx ✗ (M3) |
| 14 | `_sync.py:206` `check_provenance_required(validated_creative, provenance_policies[0])` | EU AI Act rule | D, sits in A (free function over `Creative`) ✗-minor |
| 15 | **`_sync.py:211`** `with creative_repo.savepoint():` → `effects.py:222-232` (`effect_savepoint` + `begin_nested`) | per-creative isolation of **writes and queued effects together** | A ✅ — exemplary |
| 16 | `_sync.py:216` `creative_repo.get_by_id(creative.creative_id, principal_id)` | principal-scoped read | I ✅ |
| 17a | `_sync.py:219` → `_processing.py:207-599` `_update_existing_creative` | mutate row, approval status, agent call, preview extraction, `update_data` | mixed A/D/I ✗ (M4) |
| 17b | `_sync.py:280` → `_processing.py:602-950` `_create_new_creative` | same, for the insert arm | mixed ✗ — **near-clone of 17a**, §3.3 |
| 18 | `_processing.py:283-302` and `:921-940` `if approval_mode == "auto-approve": … elif "ai-powered": … else:` | approval-mode → status | **D** — sits in A, twice ✗ (§3.5) |
| 19 | `_processing.py:379-391`, `:491-499`, `:701-713`, `:791-799` `creative_repo.outbound(lambda: run_async_in_sync_context(registry.build_creative(...)/preview_creative(...)))` | **network I/O inside the transaction, routed through the effect boundary** so a preview suppresses it | I via A boundary ✅ |
| 20 | `_processing.py:204` `creative_repo.after_commit(_submit, label=f"ai_review:{creative_id}")` | background AI review deferred past commit | I via A boundary ✅ |
| 21 | `_sync.py:361-380` `delete_missing` archive loop: `db_creative.status = "archived"` | direct ORM mutation in `_impl` | D/I — sits in A ✗-minor |
| 22 | `_sync.py:389-399` `_create_sync_workflow_steps(..., uow=uow)` → `_workflow.py:51,100,112` `uow.workflows.create_context/create_step/add_mapping` | approval steps **join the caller's transaction** | A ✅ — `_workflow.py:33-39` states why |
| 23 | `_sync.py:401-409` `def _notify(): _send_creative_notifications(...)` + `creative_repo.after_commit(_notify, label="creative_approval_slack")` | Slack deferred past commit | I via A boundary ✅ |
| 24 | **`_sync.py:420-421`** `if not dry_run: stack.close()` | **transaction closes (live)** — the commit/rollback seam itself | A ✅ |
| 25 | `_sync.py:428-435` `_process_assignments(..., uow=uow if dry_run else None)` → `_assignments.py:86-91` `with ExitStack() as stack: if uow is None: uow = stack.enter_context(CreativeUoW(tenant["tenant_id"]))` | join-or-own the transaction | A ✅ — **the correct composition shape**, see §4 |
| 26 | `_assignments.py:127` `_resolve_creative_for_assignment(...)`, `:149` `find_package_with_media_buy`, `:182` `get_product_by_id`, `:263` `get_existing`, `:283` `create` | reads/writes | I ✅ |
| 27 | `_assignments.py:184-254` inline format-vs-product comparison incl. `normalize_url` closure at `:199-202` | **D** rule hand-rolled in A ✗ (M5, §3.4) |
| 28 | `_assignments.py:315-318` `if mb_obj.status == "draft" and mb_obj.approved_at is not None: mb_obj.status = "pending_creatives"` | media-buy lifecycle transition | **D** — sits in A ✗ (§3.5) |
| 29 | `_assignments.py:324-377` result back-fill + synthesized `SyncCreativeResult` entries | response shaping | A/T boundary — acceptable |
| 30 | **`_sync.py:437-451`** `_audit_log_sync(...)` → `_workflow.py:234` `with WorkflowUoW(tenant["tenant_id"]) as uow:` | audit write in **its own** transaction, opened **after** the block closed | I ✅ — no nesting (verified: `_sync.py:437` is at indent 4, outside the `with ExitStack()` body) |
| 31 | `_sync.py:454-455` `log_tool_activity(identity, "sync_creatives", start_time)` | telemetry | I ✅ |
| 32 | `_sync.py:457-482` message string build + `SyncCreativesResponse(...)` | response | A→T ✅ |
| 33 | `sync_wrappers.py:73` `return mcp_result(response)` / `sync_wrappers.py:117` returns the model bare | wire encoding — **MCP wraps, A2A does not** | T ✗-minor asymmetry |

## 1.2 Mismatches, with the code

**M1 — SSRF registration gate is transport input validation living in `_impl`.**
`_sync.py:99-103`:
```python
if isinstance(push_notification_config, dict):
    webhook_url = push_notification_config.get("url")
else:
    webhook_url = str(push_notification_config.url) if push_notification_config.url else None
reject_unsafe_webhook_registration_url(webhook_url, field="push_notification_config.url", context=context)
```
The `isinstance` fork exists only because the wrappers hand `_impl` either a model (`sync_wrappers.py:69`) or a dict (`sync_wrappers.py:124` accepts `PushNotificationConfig | None` but `_sync.py:47` declares `PushNotificationConfig | dict | None`). `create_media_buy` runs the identical gate at `media_buy_create.py:2057-2072` — see §3.7.

**M2 — wire-shape normalization inside the transaction.** `_sync.py:174-182`:
```python
if isinstance(raw_creative, CreativeAsset):
    creative = raw_creative
elif isinstance(raw_creative, dict):
    creative_data = raw_creative.copy()
    creative_data.setdefault("assets", {})
    creative = CreativeAsset(**creative_data)
else:
    creative = CreativeAsset.model_validate(raw_creative, from_attributes=True)
```
Declared by the parameter type `creatives: Sequence[CreativeAsset | BaseModel | dict[str, Any]]` (`_sync.py:41`). A transport concern executing under an open `CreativeUoW`.

**M3 — network I/O inside the transaction, *not* routed through the effect boundary.** `_validation.py:128` `format_spec = fetch_format_spec(agent_url, format_id)` runs inside `creative_repo.savepoint()` (`_sync.py:211` → `:186`). Every *other* agent call in this slice goes through `creative_repo.outbound(...)` (`_processing.py:379`, `:491`, `:701`, `:791`); this one does not, so a `dry_run` preview still fires it. The comment at `_sync.py:139-140` ("Fetch creative formats ONCE before processing loop (outside any transaction) … avoids async HTTP calls inside database savepoints") describes the hoist that `list_all_formats` got and `fetch_format_spec` did not.

**M4 — `_processing.py` functions do persistence, domain rules, and I/O in one body.** `_update_existing_creative` mutates the ORM row (`_processing.py:263`, `:274-277`, `:284`, `:290`, `:301`), decides approval policy (`:283-302`), makes HTTP calls (`:379`, `:491`), shapes the `data` JSON (`:395-537`), and writes (`:584`). 393 lines, four layers.

**M5 — format-vs-product compatibility hand-rolled.** `_assignments.py:186-219`:
```python
supported_formats: set[tuple[str, str]] = set()
for fmt in product.format_ids:
    if isinstance(fmt, dict):
        agent_url_val = fmt.get("agent_url")
        format_id_val = fmt.get("id") or fmt.get("format_id")
        ...
def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.rstrip("/").removesuffix("/mcp")
```
A shared implementation of exactly this rule already exists: `src/core/helpers/creative_helpers.py:411` `validate_creative_format_against_product`. See §3.4 — three implementations, three semantics.

---

# Section 2 — Slice 2: `create_media_buy`, ordered trace

## 2.1 The trace

| # | Site | What it does | Responsibility | Sits in |
|---|------|--------------|----------------|---------|
| 1 | `media_buy_create.py:4469` `req = _build_create_media_buy_request(brand=…, packages=…, …)` (MCP) and `:4547` (A2A/REST) | **one shared request builder**, `:4339-4388` | T ✅ — the shape slice 1 lacks |
| 2 | `:4484-4486` `identity = await ctx.get_state("identity")`, `_ctx_id`, `raw_wire_payload` | T ✅ |
| 3 | `:4491` `identity = enrich_identity_with_account(identity, req.account)` (opens `AccountUoW`) — repeated at `:4570` | T ✅, duplicated (§3.1) |
| 4 | `:4497` / `:4573-4577` `pnc_dict = push_notification_config.model_dump(mode="json") if …` | model→dict for `_impl` | T; duplicated (§3.1) |
| 5 | `:4498` / `:4581` `await _create_media_buy_impl(req=req, push_notification_config=pnc_dict, identity=…, context_id=…, raw_wire_payload=…)` | T ✅ |
| 6 | `:2043-2052` `require_identity` / `require_principal_id` / `tenant = require_tenant(...)` | A ✅, returns a dict (§3.6) |
| 7 | `:2057-2072` `reject_unsafe_webhook_registration_url(str(rw_url) …, field="reporting_webhook.url", …)` and `:2064-2072` the same for `push_notification_config` | **T** in A ✗ — third and fourth copy of M1 |
| 8 | `:2074-2076` setup check; `:2097-2100` `request_hash = canonical_payload_hash(raw_wire_payload) if … else canonical_request_hash(req)` | A ✅ |
| 9 | `:2101-2119` `_lookup_cached_replay(...)`; returns early on hit | A; opens its own UoW (`:1780`) ✅ |
| 10 | `:2125-2148` `ctx_manager.create_context(...)` / `ctx_manager.create_workflow_step(...)` | **workflow bookkeeping through `ContextManager`, which commits and closes the thread-local session** (`context_manager.py:215,235,246`) | A, but on a *hidden second* transaction ✗ (M6, §4) |
| 11 | `:2177-2190` `with PushNotificationConfigUoW(tenant["tenant_id"]) as pnc_uow: pnc_uow.push_notification_configs.upsert(...)` | **transaction #1** (of ≥6) | A; but see §3.7 — `sync_creatives` never persists this at all |
| 12 | `:2194-2280` budget / start / end resolution | D in A ✗-minor |
| 13 | **`:2311`** `with MediaBuyUoW(tenant["tenant_id"]) as validation_uow:` … closes `:2635` | **transaction #2** — read-only validation (products, currency, package budgets) | A; a whole transaction for reads |
| 14 | `:2657-2673` `except (AdCPSalesAgentError, ValueError, PermissionError) as e:` | error translation | A ✅ |
| 15 | `:2693` `updated_packages, uploaded_ids = process_and_upload_package_creatives(req.packages, identity, testing_ctx)` → `creative_helpers.py:565` `_sync_creatives_impl(...)` | **the whole of slice 1 runs here as a nested use case** | A calling A. Verified **not** inside an open UoW (`:2680` `try:` at indent 4; `validation_uow` closed at `:2635`) ✅ |
| 16 | `:2711` `adapter = get_adapter(principal, dry_run=testing_ctx.dry_run, …)` | I ✅ |
| 17 | `:2742` `if not testing_ctx.dry_run and manual_approval_required and "create_media_buy" in manual_approval_operations:` — the manual arm, `:2742-3113` | A |
| 17a | `:2753` `_pre_validate_package_creatives(req.packages, …)` → `:527` `with MediaBuyUoW(tenant_id) as pre_validate_uow:` → `:509` `_validate_creatives_before_adapter_call(..., session=pre_validate_uow.session)` | **transaction #3**, read-only | A |
| 17b | `:2754-2758` `ctx_manager.update_workflow_step(...)`; `:2769` `ctx_manager.add_message(...)` | hidden transaction (M6) |
| 17c | `:2779-2790` `slack_notifier.notify(...)` — **outbound HTTP fired inline**, before any of the buy is persisted | I in A ✗ (M7) |
| 17d | `:2871` `with MediaBuyUoW(...) as pending_uow: pending_uow.media_buys.create_from_request(...)` | **transaction #4** |
| 17e | `:2901-2916` activity feed; `:2919-2935` audit log | I ✅ (own transports) |
| 17f | **`:2939`** `with MediaBuyUoW(...) as pkg_uow:` `assert pkg_uow.session is not None; session = pkg_uow.session` | **transaction #5** — raw `session.add(DBMediaPackage(...))` at `:3018` | A doing I ✗ (M8) |
| 17g | `:3026-3032` `ctx_manager.link_workflow_to_object(...)` | hidden transaction (M6) |
| 17h | **`:3038`** `with MediaBuyUoW(...) as assign_uow:` … `:3090-3098` `assignment = DBAssignment(assignment_id=f"assign_{uuid.uuid4().hex[:12]}", …); session.add(assignment)` | **transaction #6** — raw ORM construction in `_impl` | A doing I ✗ (M8) |
| 17i | `:3113` `return _cache_and_return(_submitted_approval_result(step, req, adapter), req, identity, request_hash)` → `:1822` opens another `MediaBuyUoW` | **transaction #7** |
| 18 | `:3124-3184` GAM `implementation_config` auto-generation | I config in A ✗-minor |
| 19 | `:3260-3507` build `MediaPackage` list, pricing resolution | A/D |
| 20 | `:3529` `_pre_validate_package_creatives(packages, …)` — same as 17a, **auto arm** | own transaction |
| 21 | `:3532` `pre_creation_errors = adapter.validate_media_buy_request(req, packages, start_time, end_time, package_pricing_info)` | I ✅ |
| 22 | **`:3548-3581`** `if testing_ctx.dry_run:` → builds `simulated_packages` / `CreateMediaBuySuccess.sync_success(media_buy_id=f"dry_run_{uuid.uuid4().hex[:12]}", …)` and **returns before the adapter and before every write** | **a shadow preview path** — the exact thing `BaseUoW`'s `dry_run` exists to abolish ✗ (M9, §3.8) |
| 23 | **`:3586`** `response = _execute_adapter_media_buy_creation(...)` → `:571` `get_adapter(...)`, `:574` `adapter.create_media_buy(...)` | **the irreversible external effect — fired with nothing yet persisted** | I ✗ (M10) |
| 24 | `:3594-3601` `if isinstance(response, CreateMediaBuyError): return CreateMediaBuyResult(response=response, status=failed)` | A ✅ |
| 25 | `:3635-3641` `media_buy_status = _determine_media_buy_status(manual_approval_required=False, has_creatives=…, creatives_approved=…, start_time=…, end_time=…, now=now)` | **D**, correctly extracted to `:254` ✅ |
| 26 | **`:3651`** `with MediaBuyUoW(...) as create_uow: create_uow.media_buys.create_from_request(...)` | **transaction #8** — the buy is persisted *after* the ad server already has the order | A ✗ (M10) |
| 27 | `:3669-3680` `except IntegrityError as exc: return _resolve_idempotency_race_or_raise(...)` → `:1882` another UoW | **transaction #9** |
| 28 | **`:3683`** `with MediaBuyUoW(...) as auto_pkg_uow:` … `:3752` `session.add(db_package)`, `:3755` `session.flush()`, `:3767` `_persist_adapter_package_ids(auto_pkg_uow.media_buys, …)` | **transaction #10** ✗ (M8) |
| 29 | **`:3779`** `with MediaBuyUoW(...) as creative_uow:` (runs to `:4040`) | **transaction #11** |
| 29a | `:3798` `creative_uow.creatives.get_by_ids(all_creative_ids, principal_id)` | I ✅ |
| 29b | `:3807`, `:3866` `ctx_manager.update_workflow_step(step.step_id, status="failed", …)` — **called while `creative_uow` is open** | ✗ **nested-session defect**, §4 |
| 29c | `:3849-3852` `validate_creative_format_against_product(creative_format_id=…, product=product_format_check)` | D ✅ (the one site that uses the shared helper) |
| 29d | `:3956` `upload_result = adapter.add_creative_assets(...)` — **HTTP inside the open transaction**, no `outbound()` | I ✗ (M11) |
| 29e | `:3988-4001` `assignment = DBAssignment(...); session.add(assignment)` | ✗ (M8) — clone of 17h |
| 29f | `:4003` `session.flush()` then `:4013` `association_results = adapter.associate_creatives([platform_line_item_id], platform_creative_ids)` — **HTTP inside the open transaction** | ✗ (M11) |
| 29g | `:4032-4038` `except Exception as e: logger.error(...)` with `# FIXME(#1566): silent per-item failure` | ✗ acknowledged |
| 30 | `:4055-4127` build `response_packages` | A→T ✅ |
| 31 | `:4130` `with MediaBuyUoW(...) as log_uow:` | **transaction #12** |
| 32 | `:4188` `with MediaBuyUoW(...) as slack_uow:` | **transaction #13** |
| 33 | `:4257-4271` `except AdCPSalesAgentError` / `except Exception` | A ✅ |

The post-approval sibling `execute_approved_media_buy` (`:728-1257`) repeats the pattern: `:759` UoW, `:1044` `adapter.create_media_buy`, `:1071` UoW, `:1085` UoW containing `:1176` `adapter.creatives_manager.add_creative_assets(...)` **inside the transaction**, `:1219` `adapter.orders_manager.approve_order(...)` outside, `:1243` UoW for the status flip.

## 2.2 Mismatches, with the code

**M6 — `ContextManager` runs a *hidden* second transaction on the same session.** `database_session.py:301-307`:
```python
@property
def session(self) -> Session:
    if self._session is None:
        scoped = get_scoped_session()
        self._session = scoped()
    return self._session
```
and `context_manager.py:215-246`:
```python
session = self.session
try:
    step = build_workflow_step(session, ...)
    ...
    session.commit()
    session.refresh(step)
    session.expunge(step)
    ...
finally:
    session.close()
```
`get_scoped_session()` is the same `scoped_session` registry `get_db_session` uses (`database_session.py:151`, `:225`), so `ContextManager.session` **is the session an open `MediaBuyUoW` is holding**. Eight `ContextManager` methods call `session.close()` (`:130, 246, 347, 548, 603, 641, 695, 723, 773`) and six call `session.commit()`. `get_context_manager()` (`:957-962`) is a process-wide singleton that caches `self._session`, so the object it commits may also be a *stale* session from an earlier request.

**M7 — outbound Slack before the buy exists.** `:2779-2790`, inside the manual arm, before `pending_uow` at `:2871`. If `create_from_request` then raises, a Slack message announces a buy that was never persisted. The repo already has the primitive for this: `repo.after_commit(...)` (`effects.py:218-220`), used correctly at `_sync.py:409`.

**M8 — raw ORM construction in `_impl`.** `:3018`, `:3097`, `:4001`, `:3752` all do `session.add(ModelClass(...))` behind
```python
# FIXME(salesagent-9f2): package creation should use repository methods
assert pkg_uow.session is not None
session = pkg_uow.session
```
(`:2940-2942`, `:3039-3042`, `:3684-3686`, `:3780-3783`). `uow.session` is a deprecated property that emits a `DeprecationWarning` (`uow.py:91-103`).

**M9 — a shadow `dry_run` path.** `MediaBuyUoW` is **never** constructed with `dry_run=` anywhere in `media_buy_create.py` (grep: the only `dry_run=` occurrences are `get_adapter(...)` at `:571, 1168, 1219, 1316, 2711` and `TestingContext(dry_run=False…)` at `:1032`). Instead `:3548-3581` returns a hand-built response. `uow.py:62-68` states the invariant this violates verbatim:
> "``dry_run`` makes the whole unit a PREVIEW: the identical write path runs … Preview/live parity therefore holds by construction -- there is no second 'simulated' write path to keep in sync, which is the class of bug a shadow preview state machine reintroduces every time it drifts from the real one."

**M10 — the external effect precedes the record of it.** `:3586` `adapter.create_media_buy(...)` → `:3651` `with MediaBuyUoW(...) as create_uow: create_uow.media_buys.create_from_request(...)`. Between those two lines there is no transaction. A crash or an `IntegrityError` that `_resolve_idempotency_race_or_raise` re-raises leaves a live GAM order with no row.

**M11 — HTTP inside an open transaction, unrouted.** `:3956` and `:4013` sit inside `with MediaBuyUoW(...) as creative_uow:` (`:3779`), after `session.flush()` at `:4003`. `media_buy_create.py`, `media_buy_update.py` and `creative_helpers.py` contain **zero** occurrences of `after_commit(`, `outbound(`, `savepoint()` or `is_preview` (verified by grep). Slice 1 uses all four.

---

# Section 3 — Duplicate groups, each with a diagnosed cause

## 3.1 Transport prologue: identity → account → serialize → call

**Copy A** `sync_wrappers.py:52-72`:
```python
identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
from src.core.transport_helpers import enrich_identity_with_account
identity = enrich_identity_with_account(identity, account)
validation_mode_str = enum_value(validation_mode) or "strict"
response = _sync_creatives_impl(creatives=creatives, assignments=assignments, creative_ids=creative_ids, delete_missing=delete_missing, dry_run=dry_run, validation_mode=validation_mode_str, push_notification_config=push_notification_config, context=context, identity=identity)
```
**Copy B** `sync_wrappers.py:110-127`:
```python
identity = resolve_identity_if_not_provided(identity, ctx)
from src.core.transport_helpers import enrich_identity_with_account
identity = enrich_identity_with_account(identity, account)
return _sync_creatives_impl(creatives=creatives, assignments=assignments, creative_ids=creative_ids, delete_missing=delete_missing, dry_run=dry_run, validation_mode=validation_mode, push_notification_config=push_notification_config, context=context, identity=identity)
```
**Copy C** `media_buy_create.py:4484-4505`, **Copy D** `:4547-4587` — same four steps plus `pnc_dict = push_notification_config.model_dump(mode="json") …`, written twice verbatim (`:4497` and `:4573-4577`).

**Cause (b): the behavior belongs at a transport boundary that does not exist as a single place.**
Evidence that this is (b) and not (a): `create_media_buy` *did* extract the half of the prologue that concerns the **request** — `_build_create_media_buy_request` (`:4339`), whose docstring says "One home for the field list … a future request field lands here once instead of in wrapper lockstep." The **identity** half has no such home, so it is still copied four times. `sync_creatives` has neither half extracted, because it has no request model at all: `_sync_creatives_impl(creatives, assignments, creative_ids, delete_missing, dry_run, validation_mode, push_notification_config, context, identity)` (`_sync.py:40-49`) is nine loose parameters, so there is nothing for a builder to build.

## 3.2 dict-or-model access, four sites

`_sync.py:85` `_get_field(c, "creative_id")`; `:343` `_get_field(raw_creative, "creative_id", "unknown")`; `:346` `_get_field(raw_creative, "name")`; `:363` `_get_field(c, "creative_id")` — plus the three-branch normalizer at `:174-182` and the two-branch `push_notification_config` fork at `:100-103`.

`_validation.py:16-23`:
```python
def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    """Get a field from a model or dict (transitional helper for Phase 1a).
    Removed in Phase 1b when all callers pass typed models."""
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)
```

**Cause (d): a type is too loose, so each site re-validates.** The declared parameter is `creatives: Sequence[CreativeAsset | BaseModel | dict[str, Any]]` (`_sync.py:41`) and `push_notification_config: PushNotificationConfig | dict | None` (`:47`). `_sync_creatives_impl` is the *only* `_impl` in these two slices that accepts unvalidated wire shapes; `_create_media_buy_impl` takes `req: CreateMediaBuyRequest` (`:2009`) and needs no `_get_field` anywhere. The helper's own docstring names the cause and the fix.

## 3.3 `_create_new_creative` vs `_update_existing_creative`

`_processing.py:638-892` (create) and `:310-582` (update) are the same 250-line algorithm.

Create, `:644-652`:
```python
format_obj = None
for fmt in all_formats:
    if fmt.format_id == creative_format:
        format_obj = fmt
        break
if format_obj and format_obj.agent_url:
    is_generative = bool(getattr(format_obj, "output_format_ids", None))
```
Update, `:316-324`: byte-identical.

Create, `:791-799`:
```python
preview_result = creative_repo.outbound(
    lambda: run_async_in_sync_context(
        registry.preview_creative(agent_url=format_obj.agent_url, format_id=format_id_str, creative_manifest=creative_manifest)))
```
Update, `:491-499`: identical.

Create, `:874-892` / update, `:562-582`: the same two `except` arms with the same `recovery="terminal"` / `recovery="transient"` messages.

The copies have **already drifted**. `:839-857` exists only in the create arm:
```python
elif creative_repo.is_preview:
    # The boundary SUPPRESSED the call ... a falsy result here means "we did not ask",
    # NOT "the agent had none".
```
The update arm has no such branch, so under `dry_run` `_update_existing_creative` falls into `:546-560` and can return `_failed_sync_result(existing_creative.creative_id, "Preview generation failed …")` for a preview that never asked. The update arm also carries `changes.append(...)` at `:398, 408, 419, 425, 506, 518, 531, 534, 537` which the create arm omits.

**Cause (a) + (e), in that order.** The *outer* difference is genuine (e): an insert and an update are different operations. But the ~250 lines between them are neither insert nor update — they are "resolve the format, ask the creative agent, fold its answer into `data`", which is one operation over a `CreativeAsset` + a format registry and is (a): there is no object that owns "render this creative through its agent", so each arm re-derives it. The drift at `:839` is the proof: a fix applied to one copy was not applied to the other.

## 3.4 Format-vs-product compatibility — three implementations, three semantics

**Copy 1** `creative_helpers.py:411-505`, matches on the **pair**, normalizing with `rstrip("/")`:
```python
def normalize_url(url_val: Any) -> str:
    if not url_val: return ""
    return str(url_val).rstrip("/")
...
if normalize_url(creative_agent_url) == normalize_url(product_agent_url) and creative_id == product_fmt_id:
    return True, None
```

**Copy 2** `media_buy_create.py:449-478`, inside `_validate_creatives_before_adapter_call`, matches on the **id string only** — `agent_url` is never read:
```python
accepted_formats: set[str] = set()
if product.format_ids:
    for fmt in product.format_ids:
        fmt_id = fmt.get("id")
        if fmt_id:
            accepted_formats.add(str(fmt_id))
...
if creative_fmt and creative_fmt not in accepted:
    validation_errors.append(f"Creative {cid} has format '{creative_fmt}' which is not accepted by product {package.product_id} …")
```

**Copy 3** `_assignments.py:186-219`, matches on the pair with a **different** normalization:
```python
def normalize_url(url: str | None) -> str | None:
    if not url: return None
    return url.rstrip("/").removesuffix("/mcp")
```

Three answers to one question. Copy 2 accepts a format id served by a *different* agent; copy 3 accepts a `…/mcp` suffix that copy 1 rejects. All three run in the create/sync flow: copy 2 at `media_buy_create.py:3529`, copy 1 at `:3849`, copy 3 at `_assignments.py:184` — and copies 1 and 2 both run inside a single `create_media_buy` call, on the same creative and product, with different rules.

**Cause (a): the behavior belongs on a model that has no methods.** The rule is a property of `Product` ("does this product accept this `FormatId`"), and `FormatId` is a real Pydantic type. `Product` exposes only the raw `format_ids` column, whose elements are `dict | LibraryFormatId | anything` — `creative_helpers.py:463-475` has to `isinstance`-fork over three shapes. Every call site therefore reimplements both the shape handling and the comparison.

## 3.5 Status/approval-mode decisions re-derived per call site

`_processing.py:283-302` and `:921-940`:
```python
if approval_mode == "auto-approve":
    existing_creative.status = CreativeStatusEnum.approved.value
    needs_approval = False
elif approval_mode == "ai-powered":
    existing_creative.status = CreativeStatusEnum.pending_review.value
    needs_approval = True
    _defer_ai_review(creative_repo, creative_id=existing_creative.creative_id, tenant=tenant, webhook_url=webhook_url, principal_id=principal_id)
else:  # require-human
    existing_creative.status = CreativeStatusEnum.pending_review.value
    needs_approval = True
```
```python
if approval_mode == "auto-approve":
    db_creative.status = CreativeStatusEnum.approved.value
    needs_approval = False
elif approval_mode == "ai-powered":
    db_creative.status = CreativeStatusEnum.pending_review.value
    needs_approval = True
    _defer_ai_review(creative_repo, creative_id=db_creative.creative_id, tenant=tenant, webhook_url=webhook_url, principal_id=principal_id)
else:
    db_creative.status = CreativeStatusEnum.pending_review.value
    needs_approval = True
```
Identical modulo the variable name. The mode is re-branched again at `_sync.py:262` (`approval_mode == "ai-powered"` to decide whether to attach `ai_review_reason`), `_workflow.py:64` (to pick a comment string), `_workflow.py:139` (`approval_mode == "require-human"` to decide whether to Slack). The literal `"draft" → "pending_creatives"` transition appears at `_assignments.py:315-318`, `media_buy_update.py:940-947` and `media_buy_update.py:1178-1186`.

**Cause (a).** `_determine_media_buy_status` (`media_buy_create.py:254`) proves the codebase knows how to do this — that one status rule *is* extracted, and is called from exactly one place (`:3635`). The creative-approval rule and the draft→pending_creatives rule were not, so they are re-branched at seven sites.

## 3.6 Tenant as `dict[str, Any]`

`auth.py:368-372` `def require_tenant(...) -> dict[str, Any]`. Re-derivation counts (`tenant["tenant_id"]` or `tenant.get(`): `media_buy_create.py` 54, `_workflow.py` 8, `_sync.py` 5, `_processing.py` 2, `_assignments.py` 1. Sample defaulting that is re-stated rather than shared — `_sync.py:136` `approval_mode = tenant.get("approval_mode", "require-human")`; `_workflow.py:139` `tenant.get("slack_webhook_url")`; `_processing.py:193` `slack_webhook_url=tenant.get("slack_webhook_url")`; `media_buy_create.py:2775-2778` builds `{"features": {"slack_webhook_url": tenant.get("slack_webhook_url"), "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url")}}` and `_workflow.py:145` builds `{"features": {"slack_webhook_url": tenant["slack_webhook_url"]}}` — the same notifier config, two shapes, one using `.get` and one subscripting.

**Cause (d): the type is too loose.** A dict has no defaults, so each reader supplies its own; it has no methods, so `slack_notifier_config()` cannot live on it.

## 3.7 Webhook registration gate + logging, four copies

`_sync.py:99-114`, `media_buy_create.py:2057-2063` (reporting_webhook), `media_buy_create.py:2064-2072` (push_notification_config), `media_buy_create.py:2160-2166` (log again at persist time). Each is `reject_unsafe_webhook_registration_url(url, field=…, context=…)` followed by `if url is not None and str(url).strip(): logger.info(…, webhook_url_for_log(str(url)))`. `_sync.py:100-103` additionally forks on `isinstance(push_notification_config, dict)` while `media_buy_create.py:2064-2072` does not, because the create wrappers already serialized to a dict (`:4497`) and the sync wrappers did not (`:69`).

Beyond the gate, the *persistence* of the same object diverges outright: `media_buy_create.py:2177-2190` upserts it via `PushNotificationConfigUoW`; `sync_creatives` never persists it, it stashes it in the workflow step's JSON at `_workflow.py:90-91`:
```python
if push_notification_config:
    request_data_for_workflow["push_notification_config"] = push_notification_config
```

**Cause (b) for the gate, (e) for the persistence.** The gate is one transport-boundary obligation ("a registration URL must be SSRF-checked and logged safely, once, before anything stores it") with no single place to live, so each `_impl` re-runs it. The persistence divergence is genuinely two different things — a buy-scoped subscription versus a step-scoped echo target — and collapsing them would be wrong.

## 3.8 Preview/dry-run: one mechanism vs two

`_sync.py:157` `CreativeUoW(tenant["tenant_id"], dry_run=dry_run)` — one write path, transaction disposal decides.
`media_buy_create.py:3548-3581` — a hand-built `CreateMediaBuySuccess.sync_success(media_buy_id=f"dry_run_{uuid.uuid4().hex[:12]}", packages=simulated_packages, …)` returned before any write.

**Cause (b): the boundary exists but only one caller uses it.** `BaseUoW.__init__(self, tenant_id: str, dry_run: bool = False)` (`uow.py:85`) is available to `MediaBuyUoW` identically. Nothing prevents `create_media_buy` from using it; nothing requires it either, and the simulated branch is cheaper to write than restructuring the twelve transactions into one.

## 3.9 MediaPackage row construction — pending arm vs auto arm

`media_buy_create.py:2993-3019` (from request packages) and `:3713-3752` (from adapter response packages). Both build a `package_config` dict with the same keys, then the same dual-write extraction:
```python
budget_total = None
if budget_value:
    if isinstance(budget_value, dict):
        budget_total = budget_value.get("total")
    elif isinstance(budget_value, (int, float)):
        budget_total = float(budget_value)
bid_price_value = None
pacing_value = None
if pricing_info_for_package:
    bid_price_value = pricing_info_for_package.get("bid_price")
if budget_value and isinstance(budget_value, dict):
    pacing_value = budget_value.get("pacing")
db_package = DBMediaPackage(media_buy_id=…, package_id=…, package_config=package_config,
    budget=Decimal(str(budget_total)) if budget_total is not None else None,
    bid_price=Decimal(str(bid_price_value)) if bid_price_value is not None else None,
    pacing=pacing_value)
session.add(db_package)
```
(`:2994-3018` vs `:3729-3752`, differing only in `budget_value`/`budget_data` and the source object.)

**Cause (a).** There is no `MediaPackage.from_request_package(...)` / `.from_adapter_package(...)` factory, and no `MediaPackageRepository.create_from(...)`. `MediaBuyRepository.create_from_request` (`:3653`) exists for the parent row, which is exactly why the parent is *not* duplicated — the child has no equivalent, so both arms hand-roll it under `# FIXME(salesagent-9f2)`.

## 3.10 Creative assignment row creation — four copies

`_assignments.py:283-288` `assignment = assignment_repo.create(media_buy_id=…, package_id=…, creative_id=…, principal_id=…)` — the repository version.
`media_buy_create.py:3089-3098`:
```python
assignment_id = f"assign_{uuid.uuid4().hex[:12]}"
assignment = DBAssignment(assignment_id=assignment_id, tenant_id=tenant["tenant_id"], principal_id=principal_id,
                          media_buy_id=media_buy_id, package_id=pkg_id, creative_id=creative_id)
session.add(assignment)
```
`media_buy_create.py:3988-4001`: identical, with `media_buy_id=response.media_buy_id, package_id=response_package_id`.
`media_buy_update.py:936` `session.add(assignment)` (and `:1170`).

Critically, only the repository copy does the upsert: `_assignments.py:263-280` calls `assignment_repo.get_existing(...)` first and reuses the row. The three raw copies insert unconditionally.

**Cause (a).** `CreativeAssignmentRepository.create` exists (`_assignments.py:283`); the other three sites hold a bare `session` obtained through the deprecated `uow.session` property and construct the model directly because the repository was never threaded to them.

## 3.11 Creative batch-load + map, three copies

`media_buy_create.py:3047-3055`, `:3792-3798`, `:1119-1123`:
```python
creatives_list = assign_uow.creatives.get_by_ids(all_creative_ids, principal_id)
creatives_map = {str(c.creative_id): c for c in creatives_list}
```
```python
creatives_list = creative_uow.creatives.get_by_ids(all_creative_ids, principal_id)
creatives_by_id = {str(c.creative_id): c for c in creatives_list}
```
```python
creatives = CreativeRepository(session, tenant_id).get_by_ids(all_creative_ids, buy_principal_id)
creative_map = {c.creative_id: c for c in creatives}
```
plus a fourth at `media_buy_create.py:565` inside `_validate_creatives_before_adapter_call`.

**Cause (c): two operations that are one unit of work are separate, so each carries its own setup.** This is not (a) — the repository method already exists and is being called. The map is rebuilt because `_pre_validate_package_creatives` (`:509`), the assignment stage (`:3038`/`:3779`) and the adapter-upload stage (`:1085`) each open their **own** `MediaBuyUoW`, and an ORM instance cannot cross a transaction. They are one logical unit of work — validate, assign, upload — split into three, so each pays the load again.

## 3.12 "Upload creative to the ad server and fold the result back"

`media_buy_create.py:3944-3985` (auto arm), `:1143-1194` (`execute_approved_media_buy`), `:1332-…` (`push_creative_to_existing_buy`). All three: `_build_adapter_asset_from_creative(creative, package_assignments, tenant_id=…)` → `adapter.add_creative_assets(...)` → `_apply_creative_enrichment(creative, status)` → `CreativeRepository(session, …).update_data(creative, merged)`. `:1178-1180` even names it: *"This is the manual-approval push path — the third of three GAM push sites."*

**Cause (a), partially remediated.** The two leaf operations were already extracted (`_build_adapter_asset_from_creative` at `:656`, `_apply_creative_enrichment` at `:227`); the *sequence* was not, because it spans a network call and a write, and there is no object that owns "push these creatives to the ad server for this buy". The comment shows the extraction was recognized and stopped one level short.

---

# Section 4 — Unit-of-work composition

## 4.1 What happens when a UoW opens inside an open UoW

`BaseUoW.__enter__` (`uow.py:110-115`):
```python
def __enter__(self) -> Self:
    self._session_cm = get_db_session()
    self._session = self._session_cm.__enter__()
    begin_effects(self._session, preview=self._dry_run)
    self._init_repos()
    return self
```
`get_db_session` (`database_session.py:224-241`):
```python
scoped = get_scoped_session()
session = scoped()
try:
    yield session
...
finally:
    session.close()
    scoped.remove()
```
`_scoped_session = scoped_session(_session_factory)` (`database_session.py:151`) — a **thread-local registry with no `scopefunc`**, so `scoped()` returns the *same* `Session` object for the whole thread until `remove()` is called.

`BaseUoW.__exit__` (`uow.py:117-155`) then, on a clean inner exit, runs `session.commit()` (`:135`) and `self._session_cm.__exit__(...)` (`:141`) → `session.close()` + `scoped.remove()`.

So the inner unit **commits the outer unit's pending writes, then closes the outer unit's session**. Verified directly against these exact primitives (`sqlalchemy`, `scoped_session`, `sessionmaker` from this venv):

```
same object: True
row in outer session after inner close: False
outer identity map: []
after outer commit, id=1 value: outer-uncommitted     # the post-close mutation was silently lost
info after close: {'k': ['outer-scope']}              # session.info survives
new scoped() is same as outer: False
```

Consequences, precisely:

1. **The outer transaction commits early.** Anything the outer had written and flushed is durable at the inner's `commit()`, whether or not the outer later succeeds. Atomicity is gone.
2. **The outer's identity map is emptied.** `Session.close()` expunges everything. Every ORM object the outer loaded before the nested call becomes detached.
3. **Mutations to those detached objects after the nested call are silently dropped** — no exception, no log, no `UPDATE`. This is the third line of the output above.
4. **`scoped.remove()`** means the outer's `self._session` is no longer the registry's session; a later `get_db_session()` in the outer path opens a *different* session, so reads and writes split across two connections.
5. **Effect scopes survive** — `session.info` is not cleared by `close()` (last line of the output), and `effects.py:91-101` deliberately uses a **stack** for exactly this reason:
   > "``get_db_session`` hands out a ``scoped_session``, so a unit of work opened while another is already open on this thread gets the SAME ``Session`` object -- and a single slot would let the inner one overwrite the outer's queue on entry and pop it on exit".
   The effects module therefore *documents* that nesting occurs and makes the effect queue survive it. Nothing makes the **transaction** survive it.

`BaseUoW.__exit__:145-155` places `drain_after_commit(session)` **after** `self._session_cm.__exit__`, with the comment "Every one of them opens its own unit of work, and the session is scoped: draining while this one was still open would let an inner unit close and de-register the session out from under this exit." That is the same hazard, correctly avoided for effects only.

## 4.2 Call-site census

| Site | Nested? | Status |
|---|---|---|
| `_sync.py:428` `_process_assignments(..., uow=uow if dry_run else None)` → `_assignments.py:86-91` | **No** | ✅ **Correct exemplar.** Verified: `_sync.py:420-421` `if not dry_run: stack.close()` closes the `CreativeUoW` on the live arm *before* the call; on the dry arm the open `uow` is **passed in**, and `_assignments.py:89-90` `if uow is None: uow = stack.enter_context(CreativeUoW(tenant["tenant_id"]))` opens one only when it was given none. Never two at once, on either arm. |
| `_sync.py:438` `_audit_log_sync(...)` → `_workflow.py:234` `with WorkflowUoW(...)` | **No** | ✅ `_sync.py:437` is at indent 4 — outside the `with ExitStack()` body, which closed at `:435`. |
| `_sync.py:390` `_create_sync_workflow_steps(..., uow=uow)` → `_workflow.py:51,100,112` | **No** | ✅ Takes `uow` as a required keyword (`_workflow.py:26-27`) and opens nothing. |
| `_processing.py:204` `after_commit(_submit)` → the AI-review job's own `AdminCreativeUoW` | **No** | ✅ Runs from `drain_after_commit`, after the outer session closed. |
| `sync_wrappers.py:57` / `:115` `enrich_identity_with_account` → `AccountUoW` | **No** | ✅ At the transport boundary, before `_impl`. |
| **`media_buy_update.py:982`** `_sync_creatives_impl(...)` inside `with MediaBuyUoW(tenant["tenant_id"]) as uow:` (`:390`) | **YES — currently occurs** | ✗ **Confirmed defect.** `_sync_creatives_impl` opens `CreativeUoW` at `_sync.py:157`. `media_buy_update.py:356` claims *"Uses a single MediaBuyUoW for the entire operation — one session, one transaction"* — false whenever `pkg_update.creatives` is non-empty. Concretely: `:881` loads `media_buy_obj`, `:943` mutates its `status`, `:953` `session.flush()`, then `:982` nests → that mutation is **committed by the inner unit** and `media_buy_obj` is detached; the later mutations at `:1182` `media_buy_obj.status = "pending_creatives"` and `:1222` `media_package.package_config["targeting_overlay"] = …` + `:1224` `session.flush()` land on detached instances and are silently lost. On the live arm `_sync.py:428` then opens a *third* `CreativeUoW` (`_assignments.py:90`) on the now-removed registry, producing a fresh session. |
| **`media_buy_create.py:3807`, `:3866`** `ctx_manager.update_workflow_step(...)` inside `with MediaBuyUoW(...) as creative_uow:` (`:3779`) | **YES — currently occurs** | ✗ `ContextManager` uses the same scoped session (`database_session.py:301-307`) and does `session.commit()` + `session.close()` (`context_manager.py:330,347`). Reached on the creative-rejection paths — where the transaction is about to be abandoned anyway, so the practical damage is the premature commit of the assignment/enrichment writes already made at `:3982`, `:4001`. |
| **`media_buy_update.py:446, 462, 593, 712, 756, 781, 849, 1417`** `ctx_manager.*` inside `MediaBuyUoW` (`:390`) | **YES — currently occurs** | ✗ Same mechanism. `:462` `ctx_manager.create_workflow_step(...)` fires early in the block and calls `session.commit()`/`session.close()` (`context_manager.py:235,246`), so every `update_media_buy` starts by committing and detaching its own unit of work. |
| **`src/admin/blueprints/operations.py:459`** `execute_approved_media_buy(media_buy_id, tenant_id)` inside `with get_db_session() as db_session:` (`:332`) | **YES — currently occurs** | ✗ `execute_approved_media_buy` opens four `MediaBuyUoW`s (`:759`, `:1071`, `:1085`, `:1243`). The first commits and closes `db_session`. `:451` `db_session.commit()` happens first, which limits the loss, but `:463`'s `with get_db_session() as error_session:` then runs on a *different* session. |
| **`src/admin/blueprints/workflows.py:233`** same call inside `with get_db_session() as db:` (`:158`) | **YES — currently occurs** | ✗ Same. |
| `src/admin/blueprints/creatives.py:646` same call | **No** | ✅ Placed after `# UoW auto-commits here` (`:629`) and `# --- Post-commit side effects (outside transaction) ---` (`:631`). The correct shape, in the same file family as the two broken ones. |
| `media_buy_create.py:2693` `process_and_upload_package_creatives` → `_sync_creatives_impl` | **No** | ✅ Verified: `:2680` `try:` is at indent 4; `validation_uow` (`:2311`) closed at `:2635`. |
| `media_buy_create.py:2753`, `:3529` `_pre_validate_package_creatives` → `:527` `MediaBuyUoW` | **No** | ✅ Neither call site is lexically inside a UoW. |
| `media_buy_create.py:3113` `_cache_and_return(...)` → `:1822` `MediaBuyUoW` | **No** | ✅ At `:3113`, `pkg_uow`/`assign_uow` have closed. |
| `media_buy_create.py:3669` `_resolve_idempotency_race_or_raise` → `:1882` `MediaBuyUoW` | **No** | ✅ Raised out of `create_uow`'s `with`, so the inner opens after the outer exits. |

**Nothing detects any of this.** `BaseUoW.__enter__` (`uow.py:110-115`) does not check whether a scope is already open, and `effects.py:91-101` treats an already-open scope as a supported condition rather than an error.

---

# Section 5 — The smallest set of structural changes that makes each duplication impossible

Ordered by how many findings each closes.

### 5.1 `BaseUoW.__enter__` refuses to nest — closes §4 entirely

**File: `src/core/database/repositories/uow.py:110-115`.** `effects.py` already keeps the per-session scope stack; make `__enter__` read it:

```python
def __enter__(self) -> Self:
    self._session_cm = get_db_session()
    self._session = self._session_cm.__enter__()
    if _scopes(self._session):            # effects.py already tracks this
        raise RuntimeError(
            "A unit of work is already open on this thread. Pass the open unit in "
            "(see _process_assignments(uow=...)) instead of opening a second one — "
            "the inner commit would commit the outer's writes and detach its rows."
        )
    ...
```
This makes nesting **impossible**, not discouraged. It converts the seven confirmed sites in §4.2 into immediate, loud failures instead of silent lost writes, and it is the single change that most strongly bounds the rest.

The same guard belongs in `DatabaseManager.session` (`src/core/database/database_session.py:301-307`) — return the session only if no effect scope is open, otherwise raise. That closes the `ContextManager` half (M6) without touching its nine call sites individually.

The fixes the guard then forces:
- `src/core/tools/media_buy_update.py:982` — change `_sync_creatives_impl(...)` to accept and join the open unit, the way `_process_assignments` already does. See 5.2.
- `src/core/tools/media_buy_update.py:446-1417` and `src/core/tools/media_buy_create.py:3807,3866` — the `ContextManager` calls must move to `uow.workflows.*` (the repository `_workflow.py:51,100,112` already uses) or out of the block.
- `src/admin/blueprints/operations.py:459`, `src/admin/blueprints/workflows.py:233` — hoist the call out of the `with get_db_session()` block, matching `src/admin/blueprints/creatives.py:646`.

### 5.2 `_sync_creatives_impl` takes an optional `uow` — closes §3.11 and unblocks 5.1

**File: `src/core/tools/creatives/_sync.py:40-49`.** Add `uow: CreativeUoW | None = None`, and at `:156-157` use the join-or-own shape `_assignments.py:86-91` already proves:

```python
with ExitStack() as stack:
    if uow is None:
        uow = stack.enter_context(CreativeUoW(tenant["tenant_id"], dry_run=dry_run))
```
The signature is then the *only* legal way to call it from inside a transaction, and `media_buy_update.py:982` becomes `_sync_creatives_impl(..., uow=uow)` — except that the caller holds a `MediaBuyUoW`, which forces the real question below.

### 5.3 One `MediaBuyUoW` per `create_media_buy` — closes §3.11, M8, M10, M11, M7

**File: `src/core/tools/media_buy_create.py`.** Today `_create_media_buy_impl` opens ≥12 transactions (`:2177, 2311, 527(×2), 2871, 2939, 3038, 3651, 3683, 3779, 1822, 1882, 4130, 4188`). Collapse the write-side ones (`2871/2939/3038` on the manual arm; `3651/3683/3779` on the auto arm) into the single unit each arm already logically is, and route the two adapter calls through the effect boundary that exists:

- `:3956` `adapter.add_creative_assets(...)` → `creative_uow.creatives.outbound(lambda: adapter.add_creative_assets(...))`
- `:4013` `adapter.associate_creatives(...)` → `repo.after_commit(lambda: adapter.associate_creatives(...), label=...)`
- `:2779-2790` Slack → `repo.after_commit(...)`

Once each arm is one unit, §3.11's three creative-map rebuilds collapse to one load because the ORM instances no longer have to cross a transaction — that duplication becomes *unwritable*, since there is no second transaction to reload into. `:3586` `adapter.create_media_buy(...)` cannot be deferred (its result builds the response), so it becomes `repo.outbound(...)`, which also gives `dry_run` for free — see 5.5.

**M10 cannot be made impossible, only narrowed.** A synchronous external booking followed by a local commit is a two-phase commit across systems; no signature prevents the window between `:3586` and `:3651`. It can only be *caught*: persist an intent row before the adapter call and reconcile it after, which is a different design, not a boundary. Say so plainly rather than pretending a type fixes it.

### 5.4 `Product.accepts_format(FormatId) -> bool` — closes §3.4

**File: `src/core/schemas/` (the `Product` model) with the shape handling moved off `creative_helpers.py:463-475`.** One method on the entity that owns the rule. Then:
- delete the loop at `src/core/tools/creatives/_assignments.py:186-219` (including its `normalize_url` closure at `:199-202`),
- delete the id-only comparison at `src/core/tools/media_buy_create.py:449-478`,
- reduce `src/core/helpers/creative_helpers.py:411-505` to a thin error-message formatter over the method.

Impossible-not-discouraged requires the second half: make `Product.format_ids` return `list[FormatId]` rather than `list[dict | LibraryFormatId | Any]`. While the column deserializes to raw dicts, a call site can still `fmt.get("id")` and re-derive. With a typed accessor, `fmt.get(...)` does not compile.

### 5.5 `MediaBuyUoW(..., dry_run=...)` instead of the simulated branch — closes §3.8 / M9

**File: `src/core/tools/media_buy_create.py:3548-3581`.** Delete the branch; pass `dry_run=testing_ctx.dry_run` at the (now single) `MediaBuyUoW` construction from 5.3. `uow.py:62-68` and `uow.py:128-135` already implement it. This is only possible *after* 5.3 — with twelve transactions, `dry_run` on any one of them is meaningless, which is precisely why the shadow branch was written. That ordering is the causal story: **the shadow preview is a symptom of the fragmented transaction, not an independent shortcut.**

Impossible-not-discouraged is available here: give `_execute_adapter_media_buy_creation` (`:539`) a required repository parameter and call the adapter through `repo.outbound(...)`. A preview then physically cannot reach the ad server, and a hand-built simulated response has nothing left to simulate.

### 5.6 `SyncCreativesRequest` model + `_build_sync_creatives_request` — closes §3.1 (sync half) and §3.2

**Files: `src/core/schemas/` (new request model), `src/core/tools/creatives/_sync.py:40-49` (signature), `src/core/tools/creatives/sync_wrappers.py`.** Replace the nine loose parameters with `req: SyncCreativesRequest`, mirroring `_create_media_buy_impl(req: CreateMediaBuyRequest, ...)` (`:2009`) and `_build_create_media_buy_request` (`:4339`). Consequences:

- `_sync.py:174-182` (the three-branch normalizer) becomes unreachable — Pydantic validated `creatives` at construction.
- `_validation.py:16-23` `_get_field` has no remaining caller and is **deletable**; its own docstring says "Removed in Phase 1b when all callers pass typed models."
- `_sync.py:100-103`'s `isinstance(push_notification_config, dict)` fork disappears: the field has one declared type.

This is the (d)→impossible conversion: with `creatives: list[CreativeAsset]` there is no dict to `.get` from.

### 5.7 `resolve_transport_identity(...)` — closes §3.1 (identity half)

**File: `src/core/transport_helpers.py`,** one function performing: get-or-resolve identity, `enrich_identity_with_account`, and (given the request model from 5.6) return the identity. The four wrappers — `sync_wrappers.py:52-57`, `:110-115`, `media_buy_create.py:4484-4491`, `:4570` — each become one call. Paired with 5.6, `pnc_dict = push_notification_config.model_dump(mode="json")` (`:4497`, `:4573-4577`) moves into the request model as a field validator and stops being wrapper code at all.

**This one can only be caught, not made impossible.** Nothing in the type system stops a fifth wrapper from open-coding the three steps again. The catch already exists in kind: `tests/unit/test_architecture_boundary_completeness.py` asserts wrappers forward every `_impl` parameter; the analogous guard is "no wrapper calls `enrich_identity_with_account` directly."

### 5.8 `Creative.render_through_agent(...)` — closes §3.3

**File: `src/core/tools/creatives/_processing.py`.** Extract `:310-582` ∩ `:638-892` — format resolution, generative-vs-static dispatch, the two `outbound` calls, preview extraction into `data`, and the two `except` arms — into one function returning `(data, agent_derived_changes, failure_result | None)`. `_update_existing_creative` and `_create_new_creative` keep only what genuinely differs: the prior-state snapshot and `changes` accumulation on one side, `creative_repo.create(...)` on the other.

This is (a), so the change is a method with an owner, not a helper: the operation is "resolve this creative's format and ask its agent to render it", which belongs to `Creative` + the registry. Extraction alone leaves the door open for a third arm to re-derive; **the `elif creative_repo.is_preview:` branch at `:839-857` existing in only one copy is the standing proof that extraction-by-discipline already failed once here.**

### 5.9 `CreativeApprovalPolicy` value object — closes §3.5

**File: `src/core/schemas/`,** constructed once from `approval_mode`, exposing `initial_status()`, `needs_approval()`, `defers_ai_review()`, `notifies_slack_immediately()`. Deletes the identical branch pairs at `_processing.py:283-302` and `:921-940`, and the ad-hoc re-branches at `_sync.py:262`, `_workflow.py:64`, `_workflow.py:139`. `_determine_media_buy_status` (`media_buy_create.py:254`) is the in-repo precedent that this shape works.

### 5.10 `Tenant` as a model, not `dict[str, Any]` — closes §3.6

**File: `src/core/auth.py:368-372`,** change the return annotation to a `TenantContext` model and let `ResolvedIdentity.tenant` carry it. Defaults (`approval_mode`, `slack_webhook_url`) live on the model with one declared value, so `tenant.get("approval_mode", "require-human")` (`_sync.py:136`) has nowhere to restate the default; `slack_notifier_config()` becomes a method, deleting the two divergent dict literals at `media_buy_create.py:2775-2778` and `_workflow.py:145`. This is the widest-blast-radius item (70 subscript sites across the two slices) and the one with the least behavior risk, since every site reads the same keys.

### 5.11 `MediaPackage` factory + repository create — closes §3.9, §3.10

**File: `src/core/database/repositories/` (a `MediaPackageRepository.create_from_request_package(...)` / `.create_from_adapter_package(...)`), plus `src/core/database/models.py`** for the `Decimal` dual-write. Deletes `media_buy_create.py:2994-3018` and `:3729-3752`. Likewise `CreativeAssignmentRepository.upsert(...)` — `_assignments.py:263-288` already contains the get-or-create logic the three raw copies (`media_buy_create.py:3089-3098`, `:3988-4001`, `media_buy_update.py:936`) omit.

Made impossible by the guard that already exists: `test_architecture_no_raw_media_package_select.py` and `test_architecture_repository_pattern.py` allowlist these sites under `# FIXME(salesagent-9f2)` (`media_buy_create.py:2940, 3039, 3684, 3780`; `media_buy_update.py:392`). Removing the allowlist entries after the repository methods land makes a fourth copy fail `make quality`.

### 5.12 `fetch_format_spec` through the effect boundary — closes M3

**File: `src/core/tools/creatives/_validation.py:128`.** `_validate_creative_input` currently has no repository. Give it the `creative_repo` the caller already holds (`_sync.py:159`) and wrap the call: `creative_repo.outbound(lambda: fetch_format_spec(agent_url, format_id))`. The pattern is `_processing.py:379`. This is the one remaining agent call in slice 1 that a `dry_run` still fires.

---

## What the map says, in one line

Slice 1 has the correct architecture — one transaction, an effect boundary for what a rollback cannot reach, savepoints that own their effects, and a join-or-own composition seam — and its duplications are (a) and (d): rules with no model to live on, and types loose enough that each site re-validates. Slice 2 has the same primitives available and uses **none** of them (zero `after_commit`/`outbound`/`savepoint`/`is_preview` occurrences), so its unit of work is split across twelve transactions; every duplication in it is downstream of that split — (c) for the reloads, (a) for the row construction the split forced out of the repositories, and (b) for the shadow `dry_run` the split made necessary.
