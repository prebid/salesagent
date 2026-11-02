# ADR-010: A graded wire field is a function of the error code, never authored at a raise site

**Status:** Accepted
**Date:** 2026-08-21
**Context:** epic `salesagent-3dawm` (derive every buyer-facing error from one code table)

## Context

AdCP 3.1.1 defines an error's buyer-facing fields as properties **of the code**.
`enums/error-code.json` carries `enumMetadata.recovery` and `enumMetadata.suggestion` per
code, and the pinned bundle is normative — its own text says SDKs MUST consume that block and
that the recovery classification embedded in the prose MUST match the value there. This repo
augments the published set with its own platform codes in `CODE_TABLE`, which supplies the
same three fields for those.

So for any code there is exactly one correct message, one correct recovery, one correct
suggestion. They are not decisions a raise site gets to make.

The codebase disagreed with that in three places at once, and each cost something measurable:

- **Message.** Raise sites interpolated caught exceptions into buyer text —
  `raise AdCPAdapterError(str(e))`, `AdCPServiceUnavailableError(f"Request timed out: {mcp_url}")`.
  Third-party exception strings and internal URLs reached the buyer-facing wire.
- **Recovery.** 14 raise sites passed a `recovery=` that contradicts the pinned classification
  for their own code — telling a buyer "terminal, do not retry" where the pin says the condition
  is transient or correctable. A buyer dispatches on this field.
- **Suggestion.** 220 of 294 construction sites passed none, so the field was simply absent from
  those envelopes; 71 authored one, and 9 codes ended up emitting two different strings depending
  on which raise site fired.

Each was policed by a guard rather than prevented: guards asserting that a class agreed with the
pin, that an advisory site remembered to call a normalizer, that recovery matched. Guards are
hand-maintained correspondences too, and they grow.

## Decision

**A graded wire field is derived from the code. A raise site cannot author one.**

Concretely:

1. `message` has **no constructor parameter** and is a read-only property resolved from
   `CODE_TABLE`. (Landed: `salesagent-3dawm.3`.)
2. `recovery` and `suggestion` follow the same rule. The `recovery=` and `suggestion=`
   parameters are to be **deleted**, not merely defaulted. A class may still override
   *visibly* via a `ClassVar`, but silence means the table owns it.
3. **A different retry semantic is a different code, therefore a different error class.**
   Overriding recovery at a call site is not the escape hatch; declaring the right code is.
4. **An advisory error** (an `errors[]` entry inside a success response) is constructible
   **only from a typed exception**, via `build_error_object(exc)`. Field-by-field `Error(...)`
   construction is not available.
5. Specifics travel as **data, not prose**: `details=` for structured, buyer-visible facts;
   `field=` for the offending field; `internal_detail=` for the caught exception, which is
   **server-log only and must never be added to a serializer**.

The test of a correct fix is that the defect becomes **unrepresentable**, not that a guard
detects it. Where a guard exists only to police a correspondence that this makes impossible,
the guard is deleted with it.

## Consequences

**Good.** One code means one thing on every lane and every transport. Whole classes of guard
disappear: three pin-conformance guards were already deleted, and
`test_architecture_advisory_normalizer_routing.py` (202 lines) plus `normalize_advisory_errors`
itself become unnecessary once (4) lands — the normalizer exists only to repair hand-built
advisories, and the guard exists only to ensure the normalizer is called.

**Cost.** Deleting a parameter is a breaking change to every raise site that used it, and each
must be re-expressed as a code choice or as `details`. The 14 contradicting recovery sites need
a per-site decision, and 4 of them look like a wrong-class problem rather than a missing code —
re-classing those changes the buyer-visible **code**, which requires spec grounding under
CLAUDE.md's Spec-Grounding Gate before any code is written.

**A testing affordance is entangled with recovery.** Two production sites
(`mock_ad_server.py`, `helpers/adapter_helpers.py`) read `recovery` from a test-behaviour hook.
CORRECTION (independent scope review, 2026-08-21): an earlier note here claimed that hook has no
writer. It does — `tests/bdd/steps/generic/given_media_buy.py:164` sets it, reached from `:2354`
(`"transient"`) and `:2854` (`"retryable"`). A line-scan missed them because the key and the value
are on different lines. The conclusion survives for a *better* reason: `"transient"` is already
`AdCPAdapterError`'s table value, so that writer is value-neutral, and **`"retryable"` is not in
`RecoveryHint` at all** — the field is assigned without validation, so an invalid recovery reaches
the buyer's wire today. Deleting the parameter is the only fix that closes that; flipping call
sites does not. `.11`/`.12` must therefore edit `tests/` too — 6 sites pass `recovery=`/
`suggestion=` (`harness/_base.py:150`, `given_media_buy.py:2351`/`:2839`,
`uc003_ext_error_scenarios.py:51`/`:690`/`:714`), which no bead currently mentions.

**When to revisit.** If AdCP ever makes recovery or suggestion request-dependent rather than
code-dependent, (2) and (3) stop being true and this ADR is superseded.

## Execution

| item | bead |
|---|---|
| delete the `recovery=` parameter; resolve the 14 contradicting sites | `salesagent-3dawm.11` |
| delete the `suggestion=` parameter (61 sites) | `salesagent-3dawm.12` |
| drop `GateFailure.suggestion`/`message` (required fields) | `salesagent-3dawm.13` — **NOT subsumed**; must FOLLOW `.14` |
| advisory constructible only from a typed exception; delete the normalizer and its guard | `salesagent-3dawm.14` |

## Amendment, 2026-08-21 — `model_copy` is guarded, not designed out

**Decision (owner).** Keep construction-time derivation. Do NOT contort the type system to make a
derived field proof against `pydantic.model_copy(update=...)`. Instead add an AST guard that bans
`model_copy` on the AdCPSalesAgentError / advisory-Error classes, with a **zero-entry allowlist**.

**Why this is the right trade, stated plainly.** It is a DETECT, and this ADR's own rule is REMOVE.
The exception is deliberate. Measured, the remove options are all worse:

    model_validator(mode="after")   derives at construction, but model_copy(update=) OVERWRITES it
    frozen = True                   same — model_copy bypasses it too
    @computed_field                 TypeError at class creation: pydantic refuses to let a subclass
                                    replace an inherited field, and message/suggestion/recovery are
                                    inherited from the SDK's Error
    override model_copy()           works, but puts a pydantic-internals workaround in a wire model

So closing it structurally means either abandoning SDK inheritance (violating critical pattern #1)
or shipping a `model_copy` override in a schema class. A guard is cheaper and more honest than
either.

**And the hole is not a human-shaped hole.** The rule for a person writing a raise site is: subclass,
raise the class for the code you mean, put specifics in `details`. Nobody reaches for
`model_copy(update={"message": ...})` to edit a graded wire field. This is an agent-shaped hole —
reachable only by someone who knows an obscure pydantic escape and looks for it — which is exactly
what a cheap AST check is for.

**Zero allowlist is arithmetically achievable, not aspirational.** MEASURED: 9 `model_copy` calls in
src/, of which exactly ONE is on an error class — src/core/exceptions.py:128, inside
`normalize_advisory_errors`, the function salesagent-3dawm.14 deletes. The other 8 are on
`base_format`, `identity`, `adcp_response`, `response`, `pkg` — non-error models the guard does not
scan. So the guard lands with no violations because .14 removes its only legitimate caller.

**Honest cost:** guard files go 134 -> 135. This epic has removed zero guards and now adds one. That
is a real regression against "remove the guards", accepted knowingly because the alternative is
worse, and recorded here rather than buried.
