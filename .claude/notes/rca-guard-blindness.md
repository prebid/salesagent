# RCA: what the structural-guard corpus can and cannot see

Repo `.`, branch `feature/spec-gaps-1210`, HEAD `9585ace68`.
All counts below are from commands run on this tree; commands are quoted inline.

---

## Section 1 — Guard inventory and detection mechanisms

### 1.1 Corpus size

```
$ ls tests/unit/test_architecture_*.py | wc -l   ->  113
$ ls tests/unit/test_guards*.py | wc -l          ->   30
                                          TOTAL  ->  143 guard files
```

CLAUDE.md:85 says "over 70 guard tests (73 `tests/unit/test_architecture_*.py`)". That number is stale by 70 files.
`salesagent-prkv.10` already records this ("fix CLAUDE.md:85 ('73' is stale after adding ~35 guards under two prefixes)").

Pre-commit: 12 commit-stage hooks, 13 pre-push, 1 manual (`yaml.safe_load` over `.pre-commit-config.yaml`).
Hook scripts: 21 files in `.pre-commit-hooks/`.

### 1.2 Mechanism census

Produced by scanning each guard file for the machinery it imports/uses:

| Mechanism | Files | What it can decide |
|---|---|---|
| `ast.parse`/`ast.walk`/`NodeVisitor` — **syntax-local AST** | 106 | shape of an expression or statement inside one function body |
| `.rglob("*.py")` / `.glob` — **file walk** | 51 | which files exist; a per-file verdict |
| literal `ALLOWLIST`/`KNOWN_`/`_EXEMPT` set — **allowlist diff** | 41 | "the violation set equals this frozen list" |
| `importlib` + `inspect.signature`/`getmembers` — **runtime introspection** | 24 | signatures, MRO, `model_fields` of things it names |
| `re.*` — **regex over text** | 26 | token presence |
| numeric cap / baseline compare — **count ratchet** | 17 | "N did not increase" |
| `yaml.safe_load` / `json.load` — **config parse** | 11 | declared config values |
| `subprocess` — **shell out** | 5 | git/ruff/mypy/uv output |

Every one of the 143 is one or a mix of these eight. **There is no type checker, no dataflow engine, no call graph, and no runtime trace anywhere in the corpus.**

```
$ grep -ln "call_graph\|callgraph\|transitive\|reachab\|fixpoint\|worklist\|interprocedural" \
      tests/unit/test_architecture_*.py tests/unit/test_guards*.py .pre-commit-hooks/*.py
```
returns 14 files — but every hit is the *word* in a docstring or a **one-level** name resolution
(`tests/unit/test_architecture_bdd_wire_discipline.py:438-450` explicitly says a one-level walk is
insufficient and that it scans every function instead *because* it has no call graph:
"Scanning every function is both simpler than a call-graph walk and strictly more complete
(a helper calling a helper, or one reached from a non-@then entry point, still slips a one-level walk)").

### 1.3 Per-guard lines (representative; full mechanism table above)

Assertion + mechanism, one line each, for the guards this report touches:

| Guard | Asserts | Mechanism |
|---|---|---|
| `test_architecture_schema_inheritance.py` | for each `from adcp… import X as LibraryX`, local `X` has `LibraryX` in its MRO | runtime introspection, **subject set derived from import aliases** |
| `test_architecture_local_schema_imports.py` | src/ files don't import an SDK type that a local subclass shadows | AST import scan + MRO check; allowlist 10 |
| `test_architecture_boundary_completeness.py` | each of 13 hardcoded `_impl`s has its MCP/A2A wrapper forwarding every param | `IMPL_REGISTRY` literal list + `inspect.signature` + call-site AST |
| `test_architecture_rest_body_completeness.py` | 6 hardcoded `(Body, raw_fn)` pairs: Body declares every raw param | `_PAIRS` literal + `inspect.signature` set-difference |
| `test_guards_no_dead_path_raw_calls.py` | a `*_raw` called from `tests/` has ≥1 caller in `src/` | AST name+alias scan of two trees |
| `test_architecture_error_message_provenance.py` | a typed `AdCPSalesAgentError`'s wire args don't interpolate tainted text | AST **intra-function taint** (3 clauses) + allowlist 16 |
| `test_guards_no_raw_exception_message.py` | bare `AdCPSalesAgentError(str(exc))` + A2A JSON-RPC ctors, in 3 named files | AST, file-list-scoped |
| `test_architecture_bdd_wire_discipline.py` | step functions use `ctx['result'].assert_wire_error`, not hand-rolled envelope reads | AST per-function; allowlist 15 |
| `test_architecture_creative_status_vocabulary.py` | every *string literal* written to a creative `status` is an SDK `CreativeStatus` member | AST literal match; allowlist **empty by policy** |
| `test_architecture_query_type_safety.py` | `.in_()`/`filter_by()` arg type matches column type | AST at the call expression, over a hardcoded `QUERY_FILES` list |
| `.pre-commit-hooks/check_tenant_context_order.py` | auth read precedes `get_current_tenant()` inside `_*_impl` | **regex**, single file, one hardcoded pair |

---

## Section 2 — Capability map: what is structurally invisible

The eight mechanisms decide exactly one class of property: **whether a given syntactic shape occurs
inside a subject the guard already knows how to name.** Two consequences follow, and they are the
whole of this section:

- **(A) Subject discovery is by name or by literal list.** A guard grades `IMPL_REGISTRY`,
  `_PAIRS`, `QUERY_FILES`, `Library*`-aliased imports, `@then`-decorated functions. Code that does
  not answer to the naming convention is not "passing" the guard — it is *not a subject of it*.
- **(B) Predicates are single-frame.** A property that requires knowing what happens in the callee,
  or two frames up, or in a different module, is undecidable by every mechanism present.

Answers to the five questions, each with a name or "none":

| Property | Any guard? | Evidence |
|---|---|---|
| **A value's TYPE flowing across 3+ function boundaries** | **none** | No dataflow engine in the corpus. The tool that could do it is disabled: `mypy.ini` sets `disallow_untyped_defs = False`, `disallow_incomplete_defs = False`, `check_untyped_defs = False` globally — mypy skips the bodies of untyped functions entirely. Only 3 modules opt in (`src.core.tools.accounts`, `src.core.tools.capabilities`, `src.core.helpers.adapter_helpers`). `.mypy-untyped-defs-baseline` = `212`: the count of type errors that *would* appear with `--check-untyped-defs`, permanently permitted. `test_architecture_query_type_safety.py` is the nearest thing and grades a single call expression (`.in_()`, `filter_by()`) in a hardcoded file list — zero boundaries crossed. |
| **PROVENANCE of a string (did this text come from a caught third-party exception?)** | **partially — `test_architecture_error_message_provenance.py`, one frame only** | It implements three taint clauses (W1 `except … as n` binding, W2 exception-typed parameter, W3 external-payload free-text read) and reads them *within the enclosing function*. Its own docstring names the limit: `normalize_to_adcp_error()` returns typed errors unchanged, so "THE RAISE SITE IS THE WIRE" — the guard has to grade at construction and cannot follow the string anywhere. It landed **2 commits ago** (`9167b4a47`, 2026-08-19), i.e. as the fix, not as the detector. |
| **Whether a decision is made in the layer that owns the data** | **none** | The layering guards (`test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py`, `test_architecture_repository_pattern.py`, `test_architecture_uow_effect_boundary.py`) all grade *what a file imports or calls*, never *who decided*. `repository_pattern` asks "does this file call `get_db_session()`/`session.add()`" — a decision can be made anywhere as long as the DB call happens elsewhere. |
| **Whether two pieces of state that must agree can be written out of order** | **essentially none — one hardcoded pair** | The corpus contains exactly one ordering check: `.pre-commit-hooks/check_tenant_context_order.py`, a **regex** over `_*_impl` bodies for one hardcoded pair (auth read before `get_current_tenant()`). There is no general "write-after-decision" or "two fields must agree" mechanism. Nothing grades `status` against `needs_approval`. |
| **Whether a scenario/test actually EXECUTES the production path it names** | **no — four guards approximate it statically, none observes execution** | `test_architecture_obligation_test_quality.py` (does the test text *contain a call* to production), `test_guards_no_dead_path_raw_calls.py` (does the callee have a src caller), `test_architecture_bdd_no_pass_steps.py` / `_no_trivial_assertions.py` (does the Then body contain a comparison). All four are static shape checks on the *test* source. None runs anything, none knows which production frames a scenario entered, and none can tell that a green scenario drove a different route than its name claims (`salesagent-ka79t` records exactly that: "it also falsifies the universal claim the new Then step's NAME makes … even though the scenario itself passes — because that scenario drives the strict-mode route only"). |

**Summary of the invisible class.** The corpus can see *local shape in a named subject*. It cannot see:
cross-frame value identity or type; who owns a decision; temporal ordering of two writes;
what a passing test actually executed; and — the one that produced three of the four defects below —
**the existence of a subject it was never told to look for.**

---

## Section 3 — Four post-mortems

### 3.1 The 25 raise sites interpolating caught-exception text

**Which guard should have caught it:** none existed. `test_architecture_error_message_provenance.py`
(1116 lines) and `test_guards_no_raw_exception_message.py` are the *output* of the review, not its detector:

```
$ git log --oneline --diff-filter=A -- tests/unit/test_architecture_error_message_provenance.py
9167b4a47 fix: keep third-party exception text off the buyer-facing wire      # HEAD~1
$ git log --oneline --diff-filter=A -- tests/unit/test_guards_no_raw_exception_message.py
d00707a7f fix: stop leaking raw exception text onto the buyer-facing wire
```

**What the nearest pre-existing predicate matched instead.** Before `d00707a7f` the only thing in this
area was error-*code* grading. `dist/compliance/3.1.1/universal/error-compliance.yaml` grades the error
CODE; the guard's own docstring states "The conformance storyboard grades the error CODE, not the
message CONTENT, so this obligation rests on that normative prose." So the entire graded surface for
errors was the code enum — `test_architecture_error_code_compliance.py`,
`test_architecture_error_recovery_enum_conformance.py`,
`test_architecture_error_suggestion_enum_conformance.py`. A site emitting the correct code with a
laundered message is *conformant* to every one of them.

**Why the shape was invisible even in principle to the mechanisms available.** The disease is
provenance across a wrap: `except Exception as e:` → `error_msg = f"...{e}"` → `raise AdCPAdapterError(error_msg)`.
Detecting it needs taint from a bind to a constructor argument. The corpus had no taint machinery until
`9167b4a47` wrote one — and even that one works only inside a single function
(see the three-clause discussion at `tests/unit/test_architecture_error_message_provenance.py:1-130`).

**Measured today.** The guard is green with a 16-row `ALLOWLIST`
(`tests/unit/test_architecture_error_message_provenance.py:264-338`), split **11 OPEN DEFECT / 5 SPEC-COMPLIANT**.
Its header records that the fix migrated a further 11 sites ("11 migrated sites, 10 distinct keys"),
so the pre-fix detector count was ~26 keys. Sanctioned form count:

```
$ grep -rn --include='*.py' "internal_detail=" src/ | grep -v "def " | wc -l   ->  12
```
across 7 files. The 11 open rows are already filed as `salesagent-dvx2y` (8 sites),
`salesagent-udff5` (5 sites), `salesagent-j6dp8` — all P2/P3, all OPEN.

---

### 3.2 A2A `GetMediaBuysRequest` — the local model that is not the SDK model (`salesagent-hg1lu`)

**The code.** `src/core/schemas/_base.py:2797`:
```python
class GetMediaBuysRequest(SalesAgentBaseModel):
    """Matches the adcp 3.6.0 GetMediaBuysRequest spec.
    Defined locally because adcp 3.6.0 is not yet required."""
    media_buy_ids: list[str] | None = ...
    status_filter: Any | None = ...
    account_id: str | None = ...
    account: LibraryAccountReference | None = ...
    context: ContextObject | None = ...
```
5 fields. The pinned SDK has 12:
```
$ uv run python -c "from adcp.types import GetMediaBuysRequest as G; print(sorted(G.model_fields))"
['account', 'adcp_major_version', 'adcp_version', 'context', 'ext', 'include_history',
 'include_snapshot', 'include_webhook_activity', 'media_buy_ids', 'pagination',
 'status_filter', 'webhook_activity_limit']
```
`src/a2a_server/adcp_a2a_server.py:2148-2160` validates the wire dict straight into it and calls
`_get_media_buys_impl` directly, skipping `get_media_buys_raw` entirely.

**Guard 1 that should have caught it: `test_architecture_schema_inheritance.py`.** Its subject set is
built at `_get_library_type_mapping()` (`:38-72`):
> "Scans src.core.schemas for all imports aliased as `Library*`. For each such import, the local class
> with the un-prefixed name should inherit from it."

```
$ grep -rn --include='*.py' "LibraryGetMediaBuys" src/     ->  (no output)
```
No `Library*` alias exists for this type, so the class is not in `mapping` and is never graded.
The predicate then double-guards itself at `:145-147`:
```python
local_cls = local_classes.get(local_name)
if local_cls is None:
    # No local class with this name — might be used directly
    continue
```
**What the guard actually asserts is "every `Library*` alias that was imported has a subclass", not
"every schema has an SDK parent."** Writing the class *without* the import is the way through it.

**Guard 2: `test_architecture_local_schema_imports.py`.** Direction is inverted. It fires when src/
imports an SDK type *that a local subclass shadows*. Here a local class exists and the SDK type is
never imported — the opposite arrangement. Zero hits.

**Guard 3: `test_architecture_boundary_completeness.py`.** `IMPL_REGISTRY` (`:27-41`) is a literal list
of 13 pairs and `_find_wrapper_info` (`:72-97`) derives the A2A wrapper **by name**:
`a2a_name = f"{base_name}_raw"`. So it grades `get_media_buys_raw` against `_get_media_buys_impl` —
and both are fine. `_handle_get_media_buys_skill`, the function A2A actually dispatches to, is not a
`*_raw`, is in a different package, and is not in the registry. The guard grades a function with zero
production callers:
```
$ grep -rn --include='*.py' "get_media_buys_raw" src/
src/core/tools/media_buy_list.py:363:def get_media_buys_raw(
src/core/tools/__init__.py:19:from src.core.tools.media_buy_list import get_media_buys_raw
src/core/tools/__init__.py:39:    "get_media_buys_raw",
```
Definition + re-export. No call sites.

**Guard 4: `test_guards_no_dead_path_raw_calls.py`** — the guard whose docstring names this exact
function (`:1-27`): "the request-validation suggestion-parity test asserted 'every transport' but drove
`get_media_buys_raw` — a wrapper with ZERO production callers." Its predicate (`:98-100`) is:
*a `*_raw` **called from `tests/`** must have ≥1 caller in `src/`.* The remediation was to re-point the
harness (`tests/harness/media_buy_list.py::call_a2a`) at the real dispatch. That deleted the test-side
call — and with it the guard's only trigger. The dead wrapper and the divergent A2A path both survive,
and the guard is green because the evidence was removed, not the defect.

```
$ uv run pytest tests/unit/test_architecture_schema_inheritance.py \
    tests/unit/test_architecture_boundary_completeness.py \
    tests/unit/test_guards_no_dead_path_raw_calls.py \
    tests/unit/test_architecture_local_schema_imports.py \
    tests/unit/test_architecture_creative_status_vocabulary.py \
    tests/unit/test_architecture_bdd_wire_discipline.py -q
35 passed in 10.17s
```
All four are green while `salesagent-hg1lu` is live and P1.

---

### 3.3 `_error_details` walking through the envelope guard added in the same diff (`salesagent-prkv.10`)

**The code, as introduced** (`git show 4a30e9cf2:tests/bdd/steps/domain/uc010_capabilities.py`, lines 87-94):
```python
def _error_details(ctx: dict) -> dict:
    """details block of the wire error envelope (errors[0] preferred)."""
    envelope = ctx.get("wire_error_envelope") or ctx.get("synthesized_error_envelope")
    assert isinstance(envelope, dict), f"no wire error envelope captured (error={ctx.get('error')!r})"
    errors = envelope.get("errors") or [{}]
    details = errors[0].get("details") or envelope.get("adcp_error", {}).get("details")
    assert isinstance(details, dict), f"error envelope carries no details block: {envelope}"
    return details
```
Called from `@then` steps at `:660`, `:668`, `:674`.

**The guard: `test_architecture_bdd_wire_discipline.py`.** At the time, `_find_hand_rolled_envelope_parsing`
iterated only functions passing `_is_then` (`:142-147`):
```python
def _is_then(func) -> bool:
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "then":
            return True
    return False
```
`_error_details` has no decorator. The `@then` steps that called it contained no `ctx.get("wire_error_envelope")`
of their own — the hand-roll sat exactly one frame below the decorator. The guard's allowlist read empty
and the guard was green in the same PR that introduced the violation.

The guard has since been widened, and its current docstring is the post-mortem in the guard's own words
(`:443-450`):
> "EVERY function in the step tree is scanned, not only `@then`-decorated ones. **The decorator was never
> the mechanism**: a `@then` delegating to a module-local `_envelope(ctx)` hand-rolls exactly as much as
> one that inlines it, and **binding enforcement to the decorator is what let the disease relocate one
> call frame down while this guard's allowlist read empty.**"

`salesagent-prkv.10` generalizes it: *"every blind spot found in this review is one shape — the guard
resolves its subject by DERIVING an identifier instead of IMPORTING the artifact, so it fails silently
in exactly the case it was written for."* That is the same fault as 3.2 (`Library*` alias, `IMPL_REGISTRY`,
`_raw` suffix) stated from the other end.

Second recorded blind spot in the same guard, still current: it matches the `ctx["result"]` **Subscript**,
not the bare name `result` (`_reads_ctx_result`, `:157-175`) — with two meta-tests at `:640` and `:660`
pinning that *as intended behaviour*.

---

### 3.4 Creative `status` / `needs_approval` disagreement (`salesagent-ka79t`)

**UPDATE arm, `src/core/tools/creatives/_sync.py`:**
```
:253   if needs_approval:
:254-266     creative_info = {... "status": existing_creative.status}
:267         creatives_needing_approval.append(creative_info)
:269   if provenance_warning and update_result.action != "failed":
:270       _append_warning(update_result, provenance_warning)
:272       # Flag for review when provenance is missing
:273       existing_creative.status = "pending_review"
:274       needs_approval = True
```
The decision at `:253` runs *before* the write at `:273`. Auto-approve tenant + `provenance_required`
product + creative with no provenance ⇒ `needs_approval=False`, nothing appended, then `:273` commits
`status="pending_review"` with no workflow step naming it — the GH #1987 orphan shape.

**CREATE arm, same file:**
```
:311   if needs_approval:
:312-318     creatives_needing_approval.append(creative_info)
:321   if provenance_warning and create_result.action != "failed":
:322       _append_warning(create_result, provenance_warning)
:324       needs_approval = True        # dead store: rebound next iteration
```
`needs_approval = True` at `:324` is a dead store and the status is never flipped, so the warning says
"flagged for review" while the row commits `approved`. The inverse inconsistency, in the same block.

**Which guard should have caught it: `test_architecture_creative_status_vocabulary.py`.** It is the only
guard in the corpus that reads creative `status` writes, its allowlist is empty by policy, and it has a
detector self-test. Its predicate (`:1-33`):
> "every string LITERAL production assigns to a creative's `status` attribute or passes as `status=`…"

`existing_creative.status = "pending_review"` is precisely that shape — and `"pending_review"` **is** a
valid `adcp.types.CreativeStatus` member. The guard asks *is this string in the enum*. It says nothing
about *when* the assignment happens relative to `:253`, and nothing about whether `status` and
`needs_approval` agree afterwards. It is green, correctly, on a defect it structurally cannot express.

**Nothing else is closer.** The only ordering machinery in the tree is
`.pre-commit-hooks/check_tenant_context_order.py`, a regex for one hardcoded pair.
`test_architecture_no_silent_loop_failures.py` (1083 lines) grades exception handling inside loops, not
value agreement. The BDD side does not cover it either: `salesagent-ka79t` records that the new Then step
whose *name* makes the universal claim ("every committed creative awaiting approval has a committed
workflow step") passes, "because that scenario drives the strict-mode route only" — capability-map row 5.

---

## Section 4 — Ratchets and allowlists: totals and trend

### 4.1 How many guards carry an allowlist

```
files carrying >=1 allowlist-shaped literal : 41 of 143
TOTAL entries in those literals             : 1446
```
Top holders: `test_architecture_repository_pattern.py` **831**, `test_architecture_no_raw_select.py` **270**,
`test_architecture_weak_mock_assertions.py` **84**, `test_architecture_schema_inheritance.py` 41,
`test_architecture_harness_realize_e2e_coverage.py` 23, `test_architecture_error_message_provenance.py` 21.

Only **13 of 143** route their allowlist through the shared exact-match helper
`assert_violations_match_allowlist` (which fails on *stale* entries as well as new ones), totalling **418**
allowlisted violations. The other 28 allowlist-carrying guards hand-roll a set-difference — meaning a fixed
violation silently rots in the list. `test_architecture_no_handrolled_allowlist_diff.py` exists to force
migration and itself carries 5 exemptions. `test_architecture_rest_body_completeness.py:44-61` is a plain
subtraction with no staleness test.

External allowlists: `tests/unit/obligation_coverage_allowlist.json` **301** entries,
`tests/unit/obligation_test_quality_allowlist.json` 0, `tests/bdd/e2e_rest_known_failures.txt` 17 lines,
45 `XFAIL` references in `tests/bdd/conftest.py`.

### 4.2 Baseline files and their current values

| File | Value |
|---|---|
| `.duplication-baseline` | `{"src": 35, "tests": 72, "scripts": 0}` |
| `.fixme-citation-baseline` | `{"src_fixme_beads": 19, "tests_fixme_beads": 119, "src_quoted_beads": 0, "tests_quoted_beads": 0}` |
| `.type-ignore-baseline` | `63` |
| `.ruff-complexity-baseline` | `{"C901": 183, "PLR0912": 132, "PLR0915": 107, "F841": 37}` |
| `.mypy-untyped-defs-baseline` | `212` |
| `.admin-raw-session-baseline` | `{"admin_get_db_session": 190, "admin_session_add": 43}` |
| `.coverage-baseline` | `46.5` |

Sum of debt held by numeric ratchets alone: **35 + 72 + 19 + 119 + 63 + 183 + 132 + 107 + 37 + 212 + 190 + 43 = 1212**.
Plus 1446 allowlist rows plus 301 obligation rows = **~2959 individually-permitted violations**.

### 4.3 Trend over the last 40 commits

Window `8f7821068` (2026-08-05) → `9585ace68` (2026-08-19), 14 days, first-parent:

| Metric | 40 commits ago | now | Δ |
|---|---|---|---|
| duplication `src` | 35 | 35 | **0** |
| duplication `tests` | 74 | 72 | −2 |
| `type-ignore` | 64 | 63 | −1 |
| `C901` | 183 | 183 | **0** |
| `PLR0912` | 133 | 132 | −1 |
| `PLR0915` | 107 | 107 | **0** |
| `F841` | 37 | 37 | **0** |
| mypy untyped-defs | 212 | 212 | **0** |
| `fixme_src_beads` | (file created 2026-08-06 at 19) | 19 | **0** |
| `fixme_tests_beads` | 120 | 119 | −1 |
| `no_raw_select` allowlist | 270 | 270 | **0** |
| `weak_mock_assertions` allowlist | 84 | 84 | **0** |
| obligation-coverage allowlist | 301 | 301 | **0** |

**Net movement across 40 commits: −5 out of ~2959.** Nine of thirteen tracked ratchets did not move at all.

### 4.4 Full lifetime — does any of them reach zero?

From `git log --follow -p` on each baseline:

- `.type-ignore-baseline`: `42` (2026-02-25) → `60` (2026-05-21) → `69` (2026-06-16) → `63` (2026-08-06, unchanged for 13 days). **Net +21 over six months.** This ratchet has grown, not shrunk.
- `.duplication-baseline` `src`: `35` since `b259007a8` (2026-07-14) — **36 days without a single movement**.
- `.duplication-baseline` `tests`: `98` (2026-06-13) → `72` (2026-08-11). −26 in 59 days ≈ 0.44/day → zero in ~163 further days *if that rate held*; it has been flat for the 8 days since.
- `.ruff-complexity-baseline` `C901`: `186` (2026-07-16) → `183` (2026-07-30), then flat for **20 days**. At its own best observed rate (3 per 14 days) reaching zero takes **854 days**; at the observed last-20-day rate, never.
- `.mypy-untyped-defs-baseline`: `227` (2026-07-16) → `212` (2026-07-31), flat for **19 days**.
- `.fixme-citation-baseline` `src_fixme_beads`: `19` since the file was created on 2026-08-06. Has **never** moved.

**Verdict on the ratchet question, with numbers.** Only one of thirteen tracked counters
(`duplication.tests`) has a movement rate that extrapolates to zero on any horizon, and it is currently
stalled. `type-ignore` has *grown 50%* over its history. `C901` at its own best rate needs 2.3 years.
Nine counters were unchanged across the entire 40-commit window. The ratchets are functioning as a
**cap on growth**, not as a path to zero — and for the type-ignore counter they have not even done that.
The 1212 numeric + 1446 allowlist + 301 obligation entries are, on the observed trend, permanent.

Structural corroboration that this is understood and accepted: `mypy.ini:9-11` states outright that the
strictness flags are "left off as hard gates (ADR-009 / #1228 F2). Count-ratcheted via
`.mypy-untyped-defs-baseline` (#1611): mypy `--check-untyped-defs` may not grow; **flag stays False here
so day-to-day `mypy` is unchanged.**" The ratchet is explicitly a substitute for turning the check on.

---

## Section 5 — Are the guards themselves graded?

### 5.1 Corpus-wide

```
guard files                                                     : 143
files with ZERO test functions (collect nothing)                :   1
files containing a negative fixture / detector self-test signal :  72
files containing none                                           :  70
```
(signal = `assert_detector_catches_ast_snippets`, a synthetic `tmp_path` module, `monkeypatch`, or a
test named `*not_vacuous*` / `*detector_catches*` / `*_is_detected*` / `positive_*`.)

**49% of the corpus has no mutation self-test of any kind.**

`salesagent-prkv.10`'s acceptance criterion already states the standard and that it is unmet:
> "every guard touched ships a MUTATION self-test — remove the predicate or add the violating fixture,
> and the guard reddens. **A guard without one is not done.**"

### 5.2 Sampled assessment (10 guards read line-by-line)

| # | Guard | Verdict if its predicate were deleted (returns "no violations") |
|---|---|---|
| 1 | `test_architecture_production_session_add.py` | **Vacuous now.** The whole file is a 5-line docstring: "Guard moved to test_architecture_repository_pattern.py". Zero test functions. It collects nothing and counts toward the corpus total. |
| 2 | `test_architecture_no_base_notfound_raise.py` | **Vacuous under deletion.** `KNOWN_VIOLATIONS: set = set()` (`:38`) and `_find_base_notfound_raises()` returns `[]` today. Replace `_raises_base_notfound` with `return False` and **both** tests (`:78`, `:97`) still pass. No synthetic fixture. |
| 3 | `test_architecture_rest_body_completeness.py` | **Vacuous under deletion.** `test_rest_bodies_forward_all_raw_wrapper_params` computes `missing = _raw_param_names(raw_fn) - body_fields - allow`; make `_raw_param_names` return `set()` and it passes. No staleness test on `_ALLOWLIST`, no fixture. |
| 4 | `test_architecture_schema_inheritance.py` | **Vacuous under subject-set collapse.** `test_all_library_types_have_local_subclass` iterates `mapping`. If `_get_library_type_mapping()` returned `{}` — a rename of the `Library*` convention would do it — the test passes with zero assertions performed. Nothing asserts the mapping is non-empty. |
| 5 | `test_architecture_boundary_completeness.py` | **Vacuous under subject-set collapse.** `IMPL_REGISTRY` is a 13-entry literal (`:27-41`); emptying it makes all three tests pass. `KNOWN_VIOLATIONS: set[str] = set()`. |
| 6 | `test_architecture_local_schema_imports.py` | **Vacuous under deletion.** 2 tests, both driven by a scan that finds allowlisted-only hits; no synthetic fixture. |
| 7 | `test_architecture_uc010_dormancy_citations.py` | **One test is a literal tautology.** `:158-162`: `reverted = 'if not (...): pytest.xfail("dormant, never graded")'` then `assert MAP_NAME not in reverted` — it asserts a hardcoded string does not contain a constant, and **never calls the guard's own predicate**. Already recorded in `salesagent-prkv.10`: "Rewrite the tautological meta-test (test_architecture_uc010_dormancy_citations.py:159-162)". Its three siblings at `:145`, `:150`, `:155` *do* drive the real detector. |
| 8 | `test_architecture_no_tenant_config.py` | **Not vacuous.** `test_tenant_config_detector_catches_known_bad_snippets` drives `find_tenant_config_violations` over 4 synthetic bad snippets. Deleting the predicate reddens it. |
| 9 | `test_architecture_jsontype_columns.py` | **Not vacuous.** Same shape, 2 synthetic snippets. |
| 10 | `test_architecture_bdd_wire_discipline.py` | **Not vacuous.** 8 positive/negative fixture tests (`:568`, `:583`, `:600`, `:618`, `:640`, `:660`, `:680`) drive the detector on synthetic modules, plus `test_primitive_function_exemptions_are_not_stale` (`:518`). This is the strongest-graded guard in the sample — and it is also the one that let 3.3 through, on subject *scope*, not predicate correctness. |

### 5.3 Count

**Assessed as potentially vacuous: 71 of 143** — the 70 files with no negative-fixture signal, plus
`test_architecture_production_session_add.py` which is vacuous outright.
Named in the sample above: `test_architecture_production_session_add.py` (vacuous now),
`test_architecture_no_base_notfound_raise.py`, `test_architecture_rest_body_completeness.py`,
`test_architecture_schema_inheritance.py`, `test_architecture_boundary_completeness.py`,
`test_architecture_local_schema_imports.py`, `test_architecture_uc010_dormancy_citations.py`
(one tautological test of nine).

Others in the 70 with a single test function and no fixture, i.e. the same risk profile:
`test_architecture_a2a_test_uses_factory.py`, `test_architecture_auth_helper_context_wiring.py`,
`test_architecture_bdd_feature_parse.py`, `test_architecture_bdd_no_dict_registry.py`,
`test_architecture_bdd_no_response_subscript.py`, `test_architecture_bdd_no_shadowed_steps.py`,
`test_architecture_bdd_no_trivial_assertions.py`, `test_architecture_bdd_step_module_reachability.py`,
`test_architecture_healthcheck_start_period.py`, `test_architecture_workflow_tenant_isolation.py`,
`test_architecture_wrapper_field_descriptions.py`, `test_guards_error_code_fixture_pin.py`,
`test_guards_no_beads_ids.py`.

**The distinction that matters:** of the four defects, only 3.3 involves a *wrong predicate*. 3.1, 3.2 and
3.4 involve guards whose predicates are correct and whose self-tests would have passed — they simply were
not looking at the thing. Mutation-testing all 143 guards would not have found any of 3.1, 3.2 or 3.4.

---

## Section 6 — Detect, or make unwritable? Per defect.

### 6.1 The 25 laundered raise sites — **make unwritable**

A guard *can* detect this, and one now does — but only intra-function, and it needs 1116 lines and a
16-row allowlist to say what a signature could say in one line. What makes it writable:

`src/core/exceptions.py:436-449`:
```python
def __init__(
    self,
    message: str = "",
    *,
    ...
    internal_detail: BaseException | str | None = None,
) -> None:
```
`message: str = ""` accepts any `str`. An f-string over a caught exception is a `str`. The type system
cannot distinguish "prose the seller authored" from "text `googleads` produced", so the 1116-line guard
exists to recover a distinction the signature erased. `internal_detail=` is optional and additive —
it is the *safe alternative*, never the *only* way to attach third-party text.

The unwritable form is a distinct type for the wire slot — `message: SellerAuthoredText` where
`SellerAuthoredText` is constructible only from a literal or a first-party template, and where
`BaseException` has no path to it. Then `AdCPAdapterError(f"upload failed: {e}")` is a type error at
the raise site, `AdCPAdapterError("upload failed", internal_detail=e)` is the only thing that compiles,
and the guard, its allowlist, and the three open tickets all become unnecessary. The obstacle is
mechanical, not conceptual: `mypy.ini` has `disallow_untyped_defs = False` and `check_untyped_defs = False`,
so most of `src/` is not type-checked at all — a typed wire slot would not be enforced anywhere it matters.

### 6.2 A2A `GetMediaBuysRequest` — **make unwritable**

A guard could detect this, and the mechanism is cheap: enumerate `adcp.types.*Request` and assert each
has a local subclass — the *converse* of what `test_architecture_schema_inheritance.py` computes. But the
enabling code is a base class that permits the shape:

`src/core/schemas/_base.py:292`:
```python
class SalesAgentBaseModel(LibraryAdCPBaseModel):
    model_config = ConfigDict(extra=get_pydantic_extra_mode())
```
and `:2797`:
```python
class GetMediaBuysRequest(SalesAgentBaseModel):
```
`SalesAgentBaseModel` is a **permissive base**: it inherits the adcp library's *root* base, so a class
descending from it is "AdCP-shaped" by construction while having no relationship to the protocol type it
is named after. That is the whole defect. Nothing in the type system connects a class named
`GetMediaBuysRequest` to `adcp.types.GetMediaBuysRequest`; the only connection is a docstring —
*"Matches the adcp 3.6.0 GetMediaBuysRequest spec. Defined locally because adcp 3.6.0 is not yet required."* —
which is now false (the pin is adcp 6.6.0 / spec 3.1.1, and the SDK type exists with 12 fields).

The unwritable form: the A2A handler should not be able to name a request type at all. Today it does:
```python
async def _handle_get_media_buys_skill(self, parameters: dict, identity: ResolvedIdentity) -> Any:
    from src.core.schemas import GetMediaBuysRequest          # adcp_a2a_server.py:2150
    from src.core.tools.media_buy_list import _get_media_buys_impl
    ...
    req = GetMediaBuysRequest.model_validate(params)          # :2158
    response = _get_media_buys_impl(req, identity=identity, ...)
```
Three enabling facts in five lines: `parameters: dict` (a dict where a type belongs); a free import of a
request model into the transport layer; and a direct call to `_impl`, bypassing the `*_raw` seam the
project's own Pattern #5 defines. If A2A dispatched through `get_media_buys_raw` — the way
`test_architecture_boundary_completeness.py` *assumes* it does — the request type would be chosen once,
in `src/core/tools/`, and the transport would have nothing to hand-roll. `get_media_buys_raw` exists,
is exported, and has zero callers; the shape is not missing, it is bypassable.

Note the causal ordering, because it decides the instrument: making the bypass unwritable also makes the
`IMPL_REGISTRY`/`_raw`-name guard *true* instead of merely green. Adding a converse-direction schema guard
would report the symptom while leaving the bypass in place.

### 6.3 `_error_details` — **detect; the predicate was simply mis-scoped**

This is the one case where a guard is the right instrument and the fix is a predicate correction, already
made. The guard's own current docstring states it (`test_architecture_bdd_wire_discipline.py:443-450`):
binding enforcement to `@then` was the error; scanning every function in the step tree is correct and is
now what it does.

What still makes the shape writable is secondary but real — the step context is an untyped bag:
```python
def _error_details(ctx: dict) -> dict:
    envelope = ctx.get("wire_error_envelope") or ctx.get("synthesized_error_envelope")
```
`ctx: dict` means any step can reach any key by string. `assert_envelope_shape` /
`ctx["result"].assert_wire_error(...)` are the sanctioned readers, but they are *alternatives*, not the
only access path — the raw keys stay reachable. The unwritable form is a typed context object whose
envelope is exposed only through the guarded reader, with no `.get("wire_error_envelope")` to find.
`salesagent-prkv.10`'s own prescription is exactly this — "one guarded primitive per region; one reader
module" — i.e. the ticket already prefers the seam over the scan. The guard-widening was the stopgap.

### 6.4 Creative `status`/`needs_approval` — **make unwritable; a guard is the wrong frame**

For a guard to detect this it would need to decide "field X is written after the branch that reads
derived-value Y, in a loop, across two arms" — temporal ordering plus value agreement plus loop-carried
liveness. That is dataflow, not AST shape, and it would have to be written per field pair. No mechanism
in Section 1 supports it, and the one ordering check in the tree
(`.pre-commit-hooks/check_tenant_context_order.py`) is a regex for a single hardcoded pair. A guard here
would be a bespoke, unmaintainable one-off.

What makes it writable is that **two representations of one fact are separately assignable**:

`src/core/tools/creatives/_sync.py:253` (the read):
```python
if needs_approval:
    creative_info: dict[str, Any] = {..., "status": existing_creative.status}
    creatives_needing_approval.append(creative_info)
```
`:269-274` (the later, contradicting write):
```python
if provenance_warning and update_result.action != "failed":
    _append_warning(update_result, provenance_warning)
    # Flag for review when provenance is missing
    existing_creative.status = "pending_review"
    needs_approval = True
```
Three enabling properties, all quotable:
1. `existing_creative.status` is a **freely assignable ORM attribute** — a plain string column with no
   transition method. Any line in the function may set it, at any point, to any spec-valid value.
2. `needs_approval` is a **loop-local `bool`, rebound in place**. On the CREATE arm the mirror write at
   `:324` (`needs_approval = True`, after the append at `:311-318`) is a dead store — the variable is
   rebound on the next iteration and nothing reads it again.
3. The queue entry is a **`dict[str, Any]` snapshot** (`creative_info`, `:254`) taken at decision time,
   so the list and the row diverge silently the moment the row is mutated afterwards.

The unwritable form is the one `salesagent-ka79t` already prescribes: "make the `needs_approval` decision
the single source of truth — compute it BEFORE the append point and never mutate status afterwards, so
status and list membership cannot disagree." Structurally that means `status` is *derived* from the
approval decision rather than assigned alongside it — one value, one write, no second representation to
fall out of sync. There is then nothing for a guard to check, because there is no second field.

### 6.5 Summary

| Defect | Instrument | What makes it writable today |
|---|---|---|
| 3.1 laundered exception text | make unwritable (typed wire slot) | `message: str = ""` in `AdCPSalesAgentError.__init__` (`exceptions.py:437`); `internal_detail=` optional, not exclusive; mypy strictness off |
| 3.2 local `GetMediaBuysRequest` | make unwritable (transport cannot name a request type) | `class GetMediaBuysRequest(SalesAgentBaseModel)` (`_base.py:2797`) — a permissive base with no SDK link; `parameters: dict` + free `_impl` call at `adcp_a2a_server.py:2148-2160` bypassing the `*_raw` seam |
| 3.3 `_error_details` | **detect** — predicate mis-scope, already fixed | `_is_then` gating (`bdd_wire_discipline.py:142`); secondary: `ctx: dict` leaves raw envelope keys reachable |
| 3.4 status/needs_approval | make unwritable (derive one from the other) | freely-assignable `existing_creative.status`; loop-local `needs_approval` bool; `dict[str, Any]` snapshot in `creatives_needing_approval` (`_sync.py:253-274`, `:311-325`) |

Three of four are shape problems, not detection problems. In 3.2 and 3.4 the relevant guards are green and
*correctly* green — they are asking a question the defect does not answer to. In 3.1 the guard that now
exists is a 1116-line reconstruction of information a type signature discarded. Only 3.3 was a guard bug.
