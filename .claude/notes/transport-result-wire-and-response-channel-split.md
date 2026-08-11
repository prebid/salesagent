# TransportResult.wire + the ctx["response"] channel split

> Design doc for salesagent-oyiv.3 (parent epic: UC-004 test-seam rework). Reviewed by an
> independent architect pass (salesagent-4z2u.4); scope narrowed per that review's HIGH
> finding and the ticket owner's decision (see "Scope and successors" below).

## Core Invariant

The harness object that produced a response owns every rule for reading it: a step reaches
the buyer's serialized body ONLY through an accessor on the `TransportResult` that carries
its own transport (never by re-deriving a guard from loose `ctx` keys, and never by writing
those keys itself), and reaches the typed reconstruction ONLY through one named accessor
over a channel that carries exactly ONE shape.

## Scope and successors

This design covers pieces **1a, 1b, 1c, 1d, and 2a** only. Two pieces were split out during
review:

- The **implementation** of 1a-1d + 2a is `salesagent-oyiv.29` (this doc authorizes it; no
  implementation starts before this doc is reviewed).
- The **typed_payload(ctx) seeding decision** (originally 2b/2c: how to enforce "no new
  typed-payload reads" once the channel is split) is `salesagent-oyiv.30`, gated on oyiv.29
  landing and re-measuring the residue. The seeding arithmetic is meaningless before the
  channel split ships — deciding it now would be planning against a number that doesn't
  exist yet.

Why split rather than defer inside one ticket: pieces 1a/1b/1c/1d/2a each stand on their own
merits — a DRY tier-1 duplicate-dispatcher fix, a live wire-forgery hole, a genuinely wrong
request-model-in-a-response-slot bug, and a six-shape overloaded channel — none of them need
the seeding decision to be correct or valuable. Bundling them with an undecided seeding
mechanism would have made this ticket's own acceptance ("a design doc covering both pieces +
the seeding story") impossible to satisfy honestly, since the seeding story's arithmetic
depends on code this ticket hadn't scoped.

## The problem

1. **`TransportResult` doesn't carry its transport.** The value lives stringly in
   `envelope["transport"]`, set separately by each of 5 dispatchers
   (`tests/harness/dispatchers.py:109,136,156,226,277`), and is absent on several error
   early-returns. `ctx["transport"]` is *also* two-shaped: `given_auth.py:27` writes the
   string `"mcp"`; `conftest.py:2978` writes a `Transport` enum. This is the same "channel
   carries two shapes" disease the rest of this doc addresses, just one level up.

2. **`wire_dict`/`wire_field` re-derive their guard from two loose `ctx` keys**
   (`ctx["wire_response"]` + `ctx["transport"]`) instead of reading it off the
   `TransportResult` that already has both. Because either key can be written by *any* step
   — not just the dispatcher — a test-side helper can fabricate a "wire" that never crossed
   a wire (`uc011_accounts.py:29-39`, `_stash_bypass_wire_response`, shared disease with
   `salesagent-oyiv.15`).

3. **GH #1744**: `wire_dict`/`wire_field` skip their loud guard whenever `ctx["transport"]`
   is `None` — which is *every* `@a2a`/`@mcp`/`@rest`-tagged scenario, because
   `_TRANSPORT_SPECIFIC_TAGS` makes `pytest_generate_tests` return early. Those scenarios
   silently fall through to `model_dump` and grade a client-side reconstruction while
   looking wire-graded.

4. **`ctx["response"]` carries six incompatible shapes**: the AdCP typed payload (the only
   legitimate use), an Admin-UI Flask HTML response (8 writers, `admin_accounts.py`), a raw
   `_impl` return under TRANSPORT-BYPASS, a raw `env.call_a2a`/`call_mcp` return, a
   `CreateMediaBuyRequest` stored where a response belongs (`uc004_delivery.py:3316` — a
   genuine bug, not a shape mismatch), and a list-tasks result. Any guard keyed on "who
   writes `ctx['response']`" is meaningless while it means six different things.

5. **`when_request.py::_call_via` is a near-verbatim second dispatcher** that calls
   `env.call_via` but drops the `TransportResult` on the floor — `ctx["result"]` is absent
   for every scenario it serves (uc005 formats, `uc005_format_id_roundtrip.py:91`,
   `test_uc018_list_creatives.py`). Standing DRY tier-1 violation, and it blocks any fix
   that reads `ctx["result"]`.

## The fix

### 1a — `TransportResult.transport`, and fix the string/enum split in the same step

Extend the frozen dataclass (`tests/harness/transport.py:98`) with
`transport: Transport | None = None`. Stamp it in the one place that knows it,
`BaseTestEnv.call_via` (`tests/harness/_base.py:554`), via
`dataclasses.replace(result, transport=transport)` — this covers the error early-returns
that set no envelope at all. Delete the 5 per-dispatcher `envelope={"transport": "..."}`
literals; re-point their 2 readers (`tests/harness/assertions.py:24`,
`tests/harness/test_harness_base.py:267`) at the typed field.

In the same step: fix `ctx["transport"]`'s own two-shape problem (~5 lines).
`given_auth.py:27` writes `Transport.MCP` (the enum), not the string `"mcp"`; `_dispatch.py`'s
str-normalization branch (`:66-79`) is deleted once no writer produces a bare string. This
makes the invariant the rest of this design rests on *true*, not *nearly* true.

### 1b — `TransportResult.wire`, sibling of `assert_wire_error`

A property that owns the rule: `wire_response is None and self.transport not in (None,
Transport.IMPL)` → raise; otherwise return the wire body (`IMPL`/no-wire falls back to
`payload.model_dump(mode="json")`). Copy `assert_wire_error`'s shape
(`transport.py:144-181`) — the exemplar with zero step-side reimplementations.

**Acceptance criterion, not a side effect: GH #1744 is closed**, proven by a regression test
on an `@a2a`/`@mcp`/`@rest`-**tagged** scenario — the hole lives exactly where
`ctx["transport"]` is `None`, so a default-parametrized test cannot falsify it.

Falsifiability constraint (review finding): **no transport-tagged scenario reaches a wire
reader through a dispatcher that sets `ctx["result"]` today** — the two live tagged
scenarios touching a wire-reading module (`T-UC-026-main-mcp`/`-rest`) are xfailed at SETUP
and never dispatch. Piece 1d (below) is therefore a **precondition** of this piece, not a
successor. Ground the regression test on either a newly-wired tagged scenario's harness, or
on the `_call_via` consumers that run *today* (`uc005_format_id_roundtrip.py:91` →
`wire_field`, `test_uc018_list_creatives.py`).

### 1c — ergonomics, minimal

`.wire` returns the dict (or the thinnest read-only `Mapping`) directly. **Not** a
path-query DSL (`result.wire.at("media_buy_deliveries.*.by_package")`). That glob evaluator
has exactly one named caller — `wire_packages`, re-expressed — and CLAUDE.md's DRY rule
explicitly excludes this case ("not an excuse to create deep abstraction hierarchies for
one-time code"). Leave `wire_packages` in `_outcome_helpers.py` as the one-line
comprehension it already is:

```python
[pkg for d in wire_list(ctx, "media_buy_deliveries") for pkg in d.get("by_package") or []]
```

No AdCP field names land on the harness type either way — 9csh already confirmed no sibling
UC reads `media_buy_deliveries`/`by_package`, so a shared AdCP-shape module stays deferred.
Revisit a path accessor only when a **third** consumer appears.

### 1d — collapse the second dispatcher, migrate the safety net first

Fold `when_request.py::_call_via` (`:41-75`) into `_dispatch.py::_dispatch` — DRY tier-1, and
the precondition for 1b. Then reduce `wire_dict`/`wire_field`/`wire_list` to
`ctx["result"].wire` adapters, and delete `uc011_accounts.py:29-39`
(`_stash_bypass_wire_response`, forged wire, now impossible — shared scope with
salesagent-oyiv.15, coordinate before touching).

Ordering constraint (review finding): the 7 unit + 2 integration synthetic-wire modules are
the *only current proof* the dormant wire-reading oracles behave correctly. Convert them to
construct a real `TransportResult(payload=..., wire_response=..., transport=...)` **first**,
in their own commit, verified against the *current* `wire_dict`/`wire_field` (must still
pass unchanged) — **then** re-point `wire_dict`/`wire_field` at `ctx["result"].wire` in a
second commit. Two commits; the second is falsifiable by the first.

### 2a — split the channel

`ctx["response"]` gets one writer, one shape. Move every non-payload writer to its own key:

| Writer | New key | Reason |
|---|---|---|
| `admin_accounts.py` (8 sites, `env.get_*_page()`/`post_*()`) | `ctx["admin_response"]` | An `_AdminResponse` HTML page is not an AdCP payload — pure false positive in any typed-payload census. Removes 18 `@then` / 26 fn from any future seed. |
| `uc011_accounts.py:397,499` (TRANSPORT-BYPASS raw `_impl` return) | bypass-specific key | Not a response reconstruction at all. |
| `uc019_query_media_buys.py:1177,1191` (raw `env.call_a2a`/`call_mcp` return) | bypass-specific key | Same reasoning. |
| `uc002_task_query.py:269` (list-tasks result) | `ctx["task_list_result"]` | Already written alongside — redundant key, not a new one. |
| `uc004_delivery.py:3316` (`CreateMediaBuyRequest` stored where a response belongs) | — | **Fix outright.** This is a bug, not a shape mismatch. |

After this, `ctx["response"]` has exactly one writer (`_dispatch.py:95`, once 1d has merged
the second dispatcher) and one shape.

## Re-measurement (this design's final step, not a decision point)

Re-run GH #1778's detector at the implementation branch's HEAD **after** 2a lands. State the
number's shelf life explicitly: it moved from 243 (pre-9csh) → 180/139 (this ticket's
research) → 186/143 (independently re-verified by the codebase-scan atom) → will move again
once 2a ships and again with every subsequent migration wave in this epic. A seeded
allowlist is stale on arrival — which is itself an argument for extending Check C's
read-keyed, empty-allowlist detector (`test_architecture_bdd_wire_discipline.py:424-461`)
over a seeded census, independent of the exact arithmetic. Hand the number and this argument
to `salesagent-oyiv.30`; do not decide the seeding mechanism here.

## Verification bar

- **Mutation test**: a boolean wire flag emitted as the JSON string `"true"` must redden
  `.wire`'s consumer *after* the fix and stay green *before* it — on an
  `@a2a`/`@mcp`/`@rest`-**tagged** scenario, not only the default parametrization (this is
  the GH #1744 falsifiability requirement).
- No allowlist anywhere grows. Check C's allowlist must not gain an entry.
- `make quality` + `saci test bdd` over the full uc004, uc005 format-id, uc018
  list_creatives, uc011, and uc006 modules (1a/1d/2a touch all five), then
  `saci run --detach` as the final gate.

## References

- GH #1744 — wire-discipline guard disabled when `ctx["transport"]` is unset.
- GH #1778 — the typed-payload-read census + spec grounding
  (`dist/compliance/3.1.1/universal/schema-validation.yaml`, check `response_schema`).
- `tests/harness/transport.py:98-181` — `TransportResult` + `assert_wire_error`, the
  exemplar `.wire` copies.
- `tests/unit/test_architecture_bdd_wire_discipline.py:424-461` — Check C's read-keyed
  detector, the working precedent for a non-defeatable predicate needing no field-name list.
- `salesagent-oyiv.15` — shares the `_stash_bypass_wire_response` deletion; coordinate.
- `salesagent-oyiv.29` — implementation ticket authorized by this design.
- `salesagent-oyiv.30` — successor design for the typed_payload seeding decision.
