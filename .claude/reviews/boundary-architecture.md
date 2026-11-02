# Architecture Review — the one-boundary refactor

**Scope**: `f5ce02e81..HEAD` on `feature/spec-gaps-1210`. Deletes 15 `*_raw` transport
wrappers, adds `src/core/tools/_boundary.py`, moves account resolution and idempotency out of
the implementations into that seam, and repoints MCP / A2A / REST at `invoke_tool`.
Production code only; tests excluded per instruction.
**Date**: 2026-09-06

## Verdict on the claim under test

> "Deleting a large amount of machinery produced a boundary that is simultaneously simpler,
> more stable, and more spec-compliant, out of the box."

**Partly survives, and the qualifier it is missing is "for one half of the request."**

- **Simpler: yes, and it is measurable.** `git diff --numstat f5ce02e81..HEAD -- src/` is
  +408 / −1080, net −672. Fifteen per-tool declarations of "resolve account, hash the
  payload, forward" become ~290 lines in one file. That is not displacement; the deleted
  lines did not reappear anywhere.
- **More stable: for the request → implementation half only.** Between `invoke_tool` and the
  `_impl` there is genuinely one sequence. Between the wire and `invoke_tool` there are still
  three, and the A2A one still runs coercions the other two do not (AR-02).
- **More compliant out of the box: true for two specific things, false for two others.**
  Genuinely fixed: replay was silently dead on MCP and now is not; `account` was accepted and
  dropped by 12 of 15 tools and now resolves for all of them, *before* the idempotency scope
  is computed, so the spec's (agent, account, key) scope is finally right; the digest input is
  one thing for every transport instead of wire-bytes-here / model-there. Genuinely worse:
  the dry-run exclusion from the cache was dropped (AR-04) and 3 of 4 keyed tools now replay
  with no `replayed` marker on the wire (AR-03).

**What would have changed my mind:** if the request-CONSTRUCTION half were derived from the
registry row too — one `spec.validate()` plus one coercion funnel per tool, called by all
three transports — and if `replayed` were stamped on the transport envelope rather than read
off the domain model. Then "one code path cannot disagree with itself" would be a statement
about the whole request rather than about its second half. The refactor names the wrappers as
the divergence and removes them; the coercion that made the wrappers divergent in the first
place is still sitting in the A2A handlers.

All findings are one tier — **Should fix** — ordered by consequence.

---

## Findings

### AR-01: The one seam every transport must pass through is untyped, and answers four questions by `getattr` probe

- **Symptoms**
  - `src/core/tools/_boundary.py:223` — `async def invoke_tool(tool_name: str, req: Any, identity: Any = None, **extra: Any) -> Any`
  - `src/core/tools/_boundary.py:235` — `invoke(...)` same signature plus `impl: Callable[..., Any]`
  - `:241` `account = getattr(req, "account", None)` · `:213` `key = getattr(req, "idempotency_key", None)`
    · `:168` and `:281` `getattr(result, "status", None)` · `:138` `if "replayed" in model.model_fields`
  - `src/core/tools/registry.py` — `ToolSpec.validate()` returns `Any`, documented as deliberate
  - `src/core/database/repositories/media_buy.py:340` — `req: Any`, with three more `getattr(req, ...)` probes
- **Root**: **the type contract was un-drawn, not relocated.** INTRODUCED. Each of the fifteen
  deleted wrappers carried a typed request (`create_media_buy_raw(req: CreateMediaBuyRequest, ...)`),
  so mypy could prove that the A2A handler for `create_media_buy` handed `create_media_buy` its
  own DTO. There is now exactly one function where that could still be proved and it is annotated
  `Any` on both ends. `invoke_tool("create_media_buy", some_get_products_request, identity)`
  type-checks today. The `getattr` probes are the downstream consequence: once `req` is `Any`,
  `req.account` is not expressible, so the seam duck-types.
- **Correct design**: the registry row already holds the DTO. Type the seam
  `invoke_tool(tool_name: str, req: BaseModel, identity: ResolvedIdentity | None = None)`, and
  answer the account / key questions from `TOOLS[tool_name].dto.model_fields` — which is a
  *declaration*, checkable — instead of `getattr` on an untyped object. `identity: Any` exists
  only to swallow FastMCP's untyped `ctx.get_state("identity")`; cast it once at the MCP call
  site in `src/core/main.py:449` rather than widening the shared seam for one caller.
- **Smallest change**: change the two signatures in `_boundary.py` to `req: BaseModel,
  identity: ResolvedIdentity | None`, add the one `cast` in `main.py:449`, and run
  `uv run mypy src/core/tools src/routes src/a2a_server --config-file=mypy.ini`. Whatever it
  reports is what the fifteen typed wrappers were checking.
- **Reproduction**: `grep -n "getattr(req\|getattr(result\|: Any" src/core/tools/_boundary.py`

---

### AR-02: The request-construction half was never unified — that is where the divergence went, and it is live

- **Symptoms** — A2A runs three coercions no other transport runs:
  - `src/a2a_server/adcp_a2a_server.py:1833` `to_account_reference(params.get("account"))` (create_media_buy)
  - `:1907` same for sync_creatives · `:1795` `to_brand_reference(params["brand"])` · `:1923` `coerce_creative_filters(...)`
  - `src/core/schema_helpers.py:66` — `_coerce_wire_object` returns **`None` for any non-dict value**
  - `src/routes/api_v1.py:83` and `src/core/main.py:446` — REST and MCP validate straight into
    the DTO and call none of the above
  - `src/core/schema_helpers.py:274-276` — the `coerce_creative_filters` docstring still says
    "Single source of truth ... so REST and A2A coerce identically". REST no longer calls it.
- **Root**: **the boundary unified the second half of the request path and left the first half
  as three implementations.** PRE-EXISTING in shape (the A2A handlers predate this diff), but
  the commit's claim — "no transport can reach a different implementation, *or a different
  pre-implementation sequence*, than the others" — is false in the half it does not name, and
  after the wrappers are gone this is the *only* place transports can still disagree. That
  makes it the whole remaining answer to question 3.
- **Consequence, concretely**: `_coerce_wire_object`'s non-dict fallback is a silent drop, and
  `account` decides both authorization scope and the idempotency cache scope. A buyer sending
  `{"account": "acct_1"}` (a string rather than the `AccountReference` object) gets
  `account=None` on A2A — the call runs against the default account — and a `ValidationError`
  on MCP and REST. Same bytes, three transports, two different answers, one of them silent.
- **Correct design**: coercion is a property of the TOOL's shape, so it belongs with the shape.
  Put the funnel on `ToolSpec.validate()` (or as `model_validator(mode="before")` on the DTO,
  which is where `validate_idempotency_key_shape` already correctly went) and delete the
  per-skill coercion from the A2A handlers. Then `validate` is the one entry every transport
  takes, exactly as `invoke_tool` is the one exit.
- **Smallest change**: move the four coercion calls into `ToolSpec.validate` keyed off the DTO's
  declared field types, and make `_coerce_wire_object` **raise** on an unexpected type instead
  of returning `None` — the silent drop is the part that is a defect regardless of where the
  coercion lives.
- **Reproduction**:
  ```
  uv run python -c "from src.core.schema_helpers import to_account_reference as t; \
    from src.core.schemas import SyncCreativesRequest as R; \
    print('A2A  ->', t('acct_1')); \
    print('REST/MCP ->', R.model_validate({'creatives':[], 'account':'acct_1'}))"
  ```
  prints `A2A -> None` then raises.

---

### AR-03: `replayed` is a protocol-envelope property read off the domain model, so 3 of 4 keyed tools replay invisibly

- **Symptoms**
  - `src/core/tools/_boundary.py:138-141` — `if "replayed" in model.model_fields: setattr(result, "replayed", True)  # noqa: B010`
  - `CreateMediaBuyResult` is the only one of the four keyed tools' response models that
    declares the field; `UpdateMediaBuyResult`, `SyncCreativesResponse`, `SyncAccountsResponse`
    do not.
  - The pinned SDK puts `replayed` on `adcp/types/generated_poc/core/protocol_envelope.py:58`,
    described as applying to "responses to **any** request that resolved via the idempotency
    cache".
- **Root**: **a transport-envelope field was given a home on the domain model.** INTRODUCED —
  the boundary is the first place in this codebase that knows "this answer came from the
  cache" for every tool at once, and it spends that knowledge on a `setattr` into whichever
  model happens to declare the field. The `noqa: B010` and the `"replayed" in model_fields`
  probe are the design telling you it is in the wrong layer: no statically-known type declares
  the field because the field does not belong to any of them. The docstring at `:130-137` says
  it exactly — "it is a property of the REPLAY and never of the stored body" — and then writes
  it onto the body.
- **Correct design**: `invoke` already knows. Have it return the replay flag beside the result
  (or set a contextvar the serializers read) and stamp `replayed` where every other envelope
  marker is stamped: `mcp_result` (`src/core/tools/_mcp.py`),
  `_stamp_a2a_protocol_fields` (`adcp_a2a_server.py:1539`) and the REST `model_dump` in
  `api_v1.py:84`. Those three functions already exist and already add `message`/`success`/
  content — this is one more marker on a path that is built for markers.
- **Smallest change**: return `(result, replayed)` from `invoke`, drop `CreateMediaBuyResult.replayed`,
  and add the key in the three serializers. Coverage goes 1/4 → 4/4 with one fewer model field.
- **Reproduction**:
  ```
  uv run python -c "from src.core.tools.registry import TOOLS; \
    from src.core.tools._boundary import _response_model_for as m, _spec_declares_idempotency_key as k; \
    [print(n, 'replayed' in m(s.impl).model_fields) for n,s in TOOLS.items() if k(s.dto)]"
  ```

---

### AR-04: The dry-run exclusion from the idempotency cache was deleted with the wrapper, not moved with it

- **Symptoms**
  - Deleted from `src/core/tools/creatives/_sync.py` in `37134f56f`: the probe guard
    `if request_hash is not None and req.idempotency_key and not dry_run:` and the write guard
    `if req.idempotency_key and not dry_run and request_hash is not None:`
  - `grep -n dry_run src/core/tools/_boundary.py src/core/idempotency_replay.py` → **no matches**
  - `sync_creatives` and `sync_accounts` both declare `dry_run`; both are keyed tools
  - `dry_run` is not on the spec's closed exclusion list, so it is inside `canonical_request_hash`
- **Root**: **an exclusion rule that was per-tool state got lost when the per-tool code went
  away.** INTRODUCED. The deleted comment stated the reason and it is still correct: "a dry run
  performs no write, so there is no side effect to deduplicate, and caching one would let a dry
  run answer a subsequent real sync carrying the same key." The commit message enumerates what
  moved to the boundary and does not mention this; nothing replaced it.
- **Consequence**: `sync_creatives(dry_run=true, idempotency_key=K)` now writes a cache row.
  The natural follow-up `sync_creatives(dry_run=false, idempotency_key=K)` is a different
  canonical payload under the same key → **IDEMPOTENCY_CONFLICT**. Preview-then-commit with one
  key worked before this diff and does not now. The buyer's only escape is to mint a second key,
  which is exactly the at-most-once guarantee they were using the key to get.
- **Correct design**: "does this request perform a side effect worth deduplicating" is a
  property of the REQUEST, which is the boundary's own stated admission test — so it belongs in
  `_keyed_scope` beside the two conditions already there, not back in the tool.
- **Smallest change**: one line in `_keyed_scope` (`src/core/tools/_boundary.py:212`):
  `if getattr(req, "dry_run", False): return None` — or, per AR-01, `if "dry_run" in
  type(req).model_fields and req.dry_run`.
- **Reproduction**: `grep -n "dry_run" src/core/tools/_boundary.py src/core/idempotency_replay.py`
  (empty), against `git show 37134f56f^:src/core/tools/creatives/_sync.py | grep -n "not dry_run"`

---

### AR-05: The rule-3 "never cache an error" enforcement is inert for half the tools it covers

- **Symptoms**
  - `src/core/tools/_boundary.py:168` — `return getattr(result, "status", None) in _FAILED_STATUSES`
  - `SyncCreativesResponse.status` and `SyncAccountsResponse.status` are both
    `Literal["completed"]` — a compile-time constant, so `_is_error_result` can never return
    True for them
  - Those are 2 of the 4 tools `_spec_declares_idempotency_key` selects
  - `sync_creatives` reports failure per item (`SyncCreativeResult.action == "failed"`,
    `src/core/tools/creatives/_sync.py:282,342`), never through `status`
- **Root**: **the generic seam can only ask a question every tool answers the same way, and
  "did this fail" is not such a question.** INTRODUCED as a *stated* guarantee — this is not a
  behavioural regression (the old per-tool code cached completed-with-item-failures too). What
  is new is the claim: `b1b336999`'s commit message and the `_boundary` module docstring say
  "the save also checks the protocol status the response carries," and for half the keyed tools
  that check is provably a no-op. That is the category question of the review: the per-tool
  wrappers could express a per-tool success predicate; the seam cannot, and papered over the
  gap with a field two of its four tools hardcode.
- **Correct design**: make the predicate part of the tool's declaration rather than inferred
  from the response. Either a `ToolSpec` field (`succeeded: Callable[[Any], bool] | None`,
  defaulting to the status test) or — cleaner, and consistent with `_is_task_envelope` — a
  `succeeded()` method the response models implement, so the boundary asks the model instead of
  guessing from one field name.
- **Smallest change**: if the answer is genuinely "an all-items-failed sync is still a completed
  task and SHOULD be cached", then delete the claim rather than the code — say in the
  `_is_error_result` docstring that it covers task-envelope responses only, and name
  sync_creatives / sync_accounts as deliberately out of its reach. A guarantee that is inert
  for half its subjects is worse than one that states its scope.
- **Reproduction**:
  ```
  uv run python -c "from src.core.schemas import SyncCreativesResponse as S; \
    print(S.model_fields['status'].annotation)"   # Literal['completed']
  ```

---

### AR-06: The idempotency digest has two owners in two layers, over an object that is mutated between them

- **Symptoms**
  - `src/core/tools/_boundary.py:252` — `request_hash = canonical_request_hash(req)` (the cache row)
  - `src/core/database/repositories/media_buy.py:416` — `"payload_hash": canonical_request_hash(req) if getattr(req, "idempotency_key", None) else None` (the durable column)
  - `src/core/tools/media_buy_create.py:2692` — `req.packages = cast(...)` — the implementation
    mutates the request in place, *between* those two computations
  - The repository now imports `src.core.idempotency_canonical` and decides who is enrolled in
    idempotency, on a parameter typed `req: Any`
- **Root**: **one protocol rule acquired two implementations at two layers.** INTRODUCED by
  `b1b336999`, which correctly identified that threading the hash through the `_impl` was the
  original sin and then solved it by computing the value a *second* time somewhere else. Both
  digests are supposed to be "the canonical hash of this request"; nothing asserts they are.
- **Status, checked**: they agree today. `process_and_upload_package_creatives` writes only
  `creative_ids` (`src/core/helpers/creative_helpers.py:648`), and
  `PackageRequest.creative_ids` carries `exclude=True`, so the mutation is invisible to
  `model_dump(mode="json")`. **The invariant that keeps the durable conflict signal correct is
  therefore "the impl only ever mutates `exclude=True` fields", and it is stated nowhere and
  checked by nothing.** Dropping that `exclude=True`, or adding one non-excluded field to the
  mutation at `:2692`, makes `media_buys.payload_hash` a hash of a request the buyer never sent
  — and rule 5's degraded path (`_raise_degraded_replay_outcome`, `media_buy_create.py:1938`)
  would then answer IDEMPOTENCY_CONFLICT to a faithful retry.
- **Correct design**: the request the boundary hashed is the request; nothing downstream should
  edit it. Replace the in-place assignment at `:2692` with a local variable used by the code
  below it, so the digest is a function of an object that does not change during the call.
  Two computations of an immutable input are safe; two of a mutable one are a coincidence.
- **Smallest change**: `packages = cast(list[AdcpPackageRequest], updated_packages)` at
  `media_buy_create.py:2692` and thread the local through the ~10 downstream uses of
  `req.packages` in that block. If that is too large for this PR, the interim is a
  `model_config = ConfigDict(frozen=True)` experiment or one assertion in
  `create_from_request` comparing against the hash the boundary already computed.
- **Reproduction**:
  ```
  grep -n "req.packages = " src/core/tools/media_buy_create.py
  grep -n "canonical_request_hash" src/core/tools/_boundary.py src/core/database/repositories/media_buy.py
  ```

---

### AR-07: The in-process bypass is sound, but it is a convention with no enforcement and no local evidence

This is the direct answer to question 5.

- **Symptoms** — four production sites call an `_impl` without passing the boundary:
  - `src/core/helpers/creative_helpers.py:606` — `_sync_creatives_impl` (create_media_buy's inline creatives)
  - `src/core/tools/media_buy_update.py:1053` — `_sync_creatives_impl` (update_media_buy's inline creatives)
  - `src/services/delivery_webhook_scheduler.py:239` — `_get_media_buy_delivery_impl`, with a
    hand-built `ResolvedIdentity(protocol="rest")`
  - `src/core/tools/properties.py:261` and `performance.py:154` — MCP wrappers for two tools
    that are not registry rows (see N3)
  - `tests/unit/test_guards_no_dead_path_raw_calls.py` (179 lines) was **deleted in `37134f56f`**;
    `test_architecture_bdd_no_direct_call_impl.py` scans `tests/bdd/steps/` only. Nothing scans `src/`.
- **Root**: **a load-bearing invariant was converted from a positive in-band signal into an
  unwritten rule.** INTRODUCED. It IS sound today, and for the reason claimed: the two
  `_sync_creatives_impl` callers borrow the outer request's `idempotency_key`, and because they
  do not enter `invoke`, that key never reaches the shared (agent, account, key) scope — where
  it would collide with the outer create's own row and raise IDEMPOTENCY_CONFLICT on a perfectly
  good nested sync. The problem is how that soundness is now encoded. Before, the callee took
  `request_hash: str | None` and the absence of a hash *was* the statement "no transmission
  happened" — visible at the call site, visible in the signature, and graded by a guard. Now
  `_sync_creatives_impl(req=..., identity=...)` is indistinguishable from what a transport would
  call, and the only thing keeping it out of the cache is that the author did not type
  `invoke_tool`. A future in-process caller who reaches for the tidier-looking `invoke_tool`
  gets nested idempotency back, silently, and the borrowed key turns a working nested call into
  a conflict.
  What such a caller would *also* bypass, and should know it is bypassing: account resolution
  (`enrich_identity_with_account`), the replay probe and the insert ceiling. The scheduler at
  `delivery_webhook_scheduler.py:239` already bypasses account resolution — harmlessly, because
  the request it builds names no account, which is a fact about today's code, not a guarantee.
- **Correct design**: make in-process entry a *named* thing rather than the absence of one.
  A `call_internal(impl, req, identity)` in `_boundary.py` — trivially `await _run(...)` — costs
  three lines, reads at the call site as a deliberate choice, is greppable, and gives a guard
  something to match on.
- **Smallest change**: add that function, repoint the three `src/` call sites, and add one
  AST guard asserting that no module under `src/` calls a `_*_impl` name directly. That is the
  guard `test_guards_no_dead_path_raw_calls.py` used to be, restated for the seam that replaced
  the raw wrappers.
- **Reproduction**: `grep -rn "_impl(" src --include=*.py | grep -v "^src/core/tools/registry" | grep -v def`

---

### AR-08: `**extra` is an unaudited per-transport channel, and its single occupant disproves the "one sequence" claim

- **Symptoms**
  - `src/core/main.py:454-456` — MCP fills `extra["context_id"]` from `ctx.get_state("context_id")`
  - `src/routes/api_v1.py:87` — `await invoke_tool(tool_name, body, identity)`, no extra, no header read
  - `src/a2a_server/adcp_a2a_server.py:1834` etc. — same, no extra — while the A2A server has a
    context id in hand at `:693` (`params.message.context_id or msg_id or f"ctx_{task_id}"`)
    and uses it for its own Task
  - `src/core/tools/media_buy_create.py:2114-2124` — `context_id` decides whether the create
    joins an existing conversation context or mints a new one
  - `_boundary.py:235` — `**extra: Any`, "forwarded untouched"; the `accepted_kwargs` filter
    that bounds it lives in `main.py:453`, i.e. in one transport, not at the seam
- **Root**: **the seam kept a per-transport channel and then described its one occupant as
  underivable.** PRE-EXISTING behaviour (the old `create_media_buy_raw` also read `context_id`
  from FastMCP `Context` only), INTRODUCED framing. `main.py:451` calls it "the one value MCP
  genuinely knows that the shared path cannot derive" — but A2A computes one three lines of
  scrolling away and does not forward it, and REST reads no `x-context-id` header at all. So the
  same `create_media_buy`, same buyer, same conversation, attaches to the existing workflow
  context over MCP and mints a fresh one over A2A and REST. That is precisely a transport
  disagreeing with another transport about the pre-implementation sequence, surviving inside the
  mechanism built to end that.
- **Correct design**: `context_id` is a property of the REQUEST's conversation, on exactly the
  same footing as `account` and `idempotency_key` — which is the boundary's own admission test
  for what belongs to it. Resolve it in `invoke` from a single conversation-id accessor each
  transport populates (the MCP middleware already sets state; A2A has `params.message.context_id`;
  REST needs an `x-context-id` dependency), and delete `**extra`. Then the seam's parameter list
  is closed, which is what makes "nothing else can be supplied" (CLAUDE.md pattern #5) a
  property of the seam rather than of one transport's wrapper.
- **Smallest change**: if closing `**extra` is too large here, move the `accepted_kwargs` filter
  from `main.py:453` into `invoke` so the bound applies to every caller, and file the
  A2A/REST `context_id` gap — do not leave `main.py:451`'s comment asserting the other two
  transports cannot know it.
- **Reproduction**: `grep -n "context_id" src/core/main.py src/routes/api_v1.py src/a2a_server/adcp_a2a_server.py`

---

### AR-09: There is one seam for the request and none for the response, and the auth axis still has three declarations that disagree

- **Symptoms**
  - Three response serializers, no common seam: `src/core/tools/_mcp.py:26` (`mcp_result`),
    `src/a2a_server/adcp_a2a_server.py:1539` (`_stamp_a2a_protocol_fields`, adds
    `message`/`success`), `src/routes/api_v1.py:88` (`response.model_dump(mode="json")`, bare)
  - `apply_version_compat` runs on REST (`api_v1.py:93`) and A2A (`adcp_a2a_server.py:1751`),
    for `get_products` only, and **never on MCP** — `grep -rn apply_version_compat src/core/main.py`
    is empty. The code says so: "Version compat runs where it ran before and nowhere else."
  - Auth is declared three times and disagrees for `list_accounts`: `auth="required"` in the
    registry row (so REST uses `require_auth`), but it is in `AUTH_OPTIONAL_TOOLS`
    (`src/core/mcp_auth_middleware.py:25`) and in `DISCOVERY_SKILLS`
    (`adcp_a2a_server.py:302`). `src/core/tools/registry.py`'s own `ToolSpec.auth` docstring
    admits this. `invoke` does not consult `spec.auth` at all.
- **Root**: **the boundary was drawn under the request and not under the response or the auth
  gate**, so those two axes still carry the per-transport declarations the wrappers used to
  carry for the request. PRE-EXISTING; this diff neither created nor extended it. It is here
  because it bounds the claim: a buyer asking "does this seller behave the same on all
  transports" gets yes for what reaches the implementation and no for what comes back, and no
  for whether they need a token.
- **Correct design**: the same move, applied to the return half — one `serialize(tool_name,
  result, adcp_version)` in `_boundary.py` that applies version compat and the envelope markers
  (including `replayed`, per AR-03), with each transport adding only its own framing. And
  `spec.auth` should be the single declaration the MCP middleware and the A2A discovery gate
  both read, rather than a third list beside their two.
- **Smallest change**: for auth, derive `AUTH_OPTIONAL_TOOLS` and `DISCOVERY_SKILLS` from
  `{n for n, s in TOOLS.items() if s.auth == "optional"}` and resolve the `list_accounts`
  disagreement in the row. Three lines, and it removes a live 401-on-REST / 200-on-MCP split.
- **Reproduction**:
  ```
  uv run python -c "from src.core.tools.registry import TOOLS; \
    from src.core.mcp_auth_middleware import AUTH_OPTIONAL_TOOLS as M; \
    from src.a2a_server.adcp_a2a_server import DISCOVERY_SKILLS as A; \
    [print(n, s.auth=='optional', n in M, n in A) for n,s in TOOLS.items() \
     if len({s.auth=='optional', n in M, n in A})>1]"
  ```

---

## Notes (out of scope — separate boundaries, recorded with the reason)

- **N1 — the A2A agent card advertises three skills that cannot be invoked.** `_derived_skills`
  (`adcp_a2a_server.py:2182`) emits a skill for every row with `a2a=True`, which is all 14; the
  dispatch table at `:1690` filters on `hasattr(self, f"_handle_{name}_skill")`, which is 11.
  `list_tasks`, `get_task_status` and `complete_task` are on the published card and answer
  `MethodNotFoundError`. Introduced at `b21ca88a1`, before this range — same epic, different
  commit. The `hasattr` filter is the per-tool probe this design set out to delete: `a2a=True`
  is the declaration, and a silent `hasattr` overrides it.
- **N2 — the registry cites a guard that no longer exists.** `src/core/tools/registry.py`'s
  module docstring says the rows are kept honest by
  `tests/unit/test_architecture_tool_registry_agrees.py`, which was deleted at `59aa10fb7`
  (2026-09-04, before this range) along with five other architecture guards. The registry's
  stated safety mechanism is prose.
- **N3 — two AdCP tools are unreachable on every transport while two lists still name one of
  them.** `list_authorized_properties` and `update_performance_index` are not registry rows, so
  they are not MCP-registered, have no REST route and no A2A handler; their `*_raw` wrappers
  were the last transport-agnostic entry point and this diff deleted them.
  `list_authorized_properties` is still listed in `AUTH_OPTIONAL_TOOLS`
  (`mcp_auth_middleware.py:28`) and `DISCOVERY_SKILLS` (`adcp_a2a_server.py:303`).
- **N4 — docstrings describing the deleted machinery.**
  `src/core/idempotency_canonical.py:26-29` still says "transport wrappers thread the raw wire
  payload to `canonical_payload_hash`, the spec's equivalence input" — `canonical_payload_hash`
  has no production callers left. `cache_success` (`idempotency_replay.py:99`) says rule-3
  enforcement "lives in the callers' error paths"; it lives in `_boundary._is_error_result` now.
  `coerce_creative_filters` (`schema_helpers.py:274`) claims REST calls it; REST does not.
  These matter more than usual here because in this codebase the docstring is the design record.

---

## Summary

- Should fix: 9
- Out-of-scope notes: 4
- Net production change measured: `+408 / −1080` in `src/` (`git diff --numstat f5ce02e81..HEAD -- src/`)
