# Plan: emit the pinned `issues[]` channel from the raised lane, typed

Frozen scope authority for `salesagent-rys3u.6`. Every claim below was verified
by reading the pinned schema or the source, not inferred.

Pin: AdCP 3.1.1 via `adcp==6.6.0`. Schemas at
`../adcp/dist/schemas/3.1.1/`.

## Why this blocks rys3u.2

The first-pass shape catalog routed multi-field validation rejection through our
own `details.problems`. The pin already canonicalizes that channel as top-level
`issues[]`, so shipping our own would be the same defect as using `available`
instead of `accepted_values`. 34 `AdCPValidationError` sites plus 5
`AdCPInvalidRequestError` sites depend on the answer; converting them first
means converting them twice.

## Verified facts

`core/error.json` declares a top-level `issues` property:

> Structured list of validation failures. Primary use is `VALIDATION_ERROR`,
> where multi-field rejections are common and `field` (singular) cannot carry
> the full pointer map.

Item schema: `required: ["pointer", "message", "keyword"]`,
`additionalProperties: true`. Optional: `schemaPath`, `schema_id`,
`discriminator`.

A MUST on the seller:

> When `issues` is present, sellers MUST also populate `field` from `issues[0]`
> ... translating the RFC 6901 `pointer` format to the JSONPath-lite format
> `field` uses (e.g., `/packages/0/targeting` -> `packages[0].targeting`).

Current state:

- `AdCPSalesAgentError.__init__` has NO `issues` parameter. Params are exactly:
  `error_code`, `status_code`, `details`, `field`, `retry_after`, `context`,
  `internal_detail`. The raised lane cannot emit the channel at all.
- The advisory `Error` already can: `Error.model_fields["issues"]` is
  `list[adcp.types.generated_poc.core.error.Issue] | None`.
- `adcp.types.generated_poc.core.error.Issue` is a plain `BaseModel`, NOT a
  RootModel, `extra="allow"`, fields `pointer`/`message`/`keyword` required plus
  `schemaPath`/`schema_id`/`discriminator` optional. It is subclassable, unlike
  the pricing options (adcp-client-python#1077).
- Neither assertion helper can grade the channel: in
  `tests/harness/transport.py` and `tests/helpers/envelope_assertions.py`,
  `details` is mentioned 19 and 16 times, `issues` **zero** times.

## The design

### 1. `JsonPointer` value type

A pointer is never a hand-written string literal at a raise site; the pin's
translation MUST is satisfied structurally, not by hand.

    JsonPointer.of("packages", 0, "targeting")
        .pointer  ->  "/packages/0/targeting"     # RFC 6901, issues[].pointer
        .field    ->  "packages[0].targeting"     # JSONPath-lite, top-level field

Owns RFC 6901 escaping: `~` -> `~0`, `/` -> `~1`, so a member named `a/b`
renders `/a~1b` and round-trips. Integer segments render as array indices.

Cross-check the `.field` rendering against the existing JSONPath-lite producer
`first_validation_error_field()` (`src/core/exceptions.py`) so the two agree.

### 2. `ErrorIssue(LibraryIssue)`

Extends the SDK type per critical pattern #1 (`Library*` alias, inheritance, no
duplication), overriding its `extra="allow"` to the repo policy.

    class ErrorIssue(LibraryIssue):
        model_config = ConfigDict(extra=get_pydantic_extra_mode(), frozen=True)
        pointer: str
        keyword: JsonSchemaKeyword
        code: ErrorCodeT
        rejected_value: str | None = None
        accepted_values: list[str] | None = None

There is NO `message` parameter. `additionalProperties: true` on the pinned item
is what lets the typed fields sit alongside the pinned keys.

`JsonSchemaKeyword` is a `Literal` over the JSON Schema vocabulary the codebase
actually emits. The design atom fixes its membership; it is closed, and grows by
editing the Literal.

### 3. Message is derived, never authored

`message` is required on each item, so it cannot be omitted. The pin requires
the FIELD to be present; it does not require a human to write it. A
`mode="before"` model validator resolves it from `(code, keyword)` -- the same
mechanism `Error._derive_graded_fields` already uses for the top-level message.

This keeps ONE policy at both levels: a buyer-facing sentence is a function of
the code, never authored at a raise site. It is NOT a second, free-form policy.

The design atom fixes the `(code, keyword) -> sentence` table's shape and its
initial entries.

### 4. `issues` on the raised lane

Add `issues: list[ErrorIssue] | None = None` to `AdCPSalesAgentError.__init__`, carried
into the wire envelope by `build_two_layer_error_envelope` alongside the
existing `_details_to_wire` call. Top-level `field` is DERIVED from
`issues[0].pointer` when issues are present and `field` was not passed --
never typed twice.

### 5. The assertion layer must grade it

`assert_wire_error` and `assert_envelope_shape` gain an `issues=` keyword with
the same subset-membership semantics `details=` already has. Without this every
`issues[]` scenario passes vacuously, and an ungraded channel is
indistinguishable from an absent one.

## Out of scope, explicitly

- Converting the validation family's 39 sites. That is rys3u.2, unblocked by
  this task.
- The five spec-gate questions the catalog raised (open questions 7, 8, 9, 12,
  13): whether CONFIGURATION_ERROR may carry details; UPDATE_ACTIONS vs the
  pinned media-buy-valid-action enum; BUDGET_TOO_LOW routing; a code for a
  cardinality refusal; absent-vs-malformed URL distinguishability. Each is a
  protocol-behavior decision needing its own grounding. Do NOT decide or file
  them here.
- `ProblemsDetails.problems` -> `list[SerializeAsAny[ErrorProblem]]` and the
  `to_wire()` key-set test. Prerequisites of rys3u.2, not of this task.
- Repairing `EntityRefDetails` being both base and type parameter on ~10
  committed classes. Known, recorded, follow-up.
- `ErrorProblem` and `details.problems` keep their role for per-ENTITY outcomes
  (a failed creative is not a field). Do not remove or re-point them.

## Acceptance

1. A raise site can emit `issues[]` and CANNOT author an item's `message` --
   there is no parameter to pass.
2. `pointer` is never a string literal at a raise site; `JsonPointer` builds it.
3. Top-level `field` is derived from `issues[0].pointer`, not passed twice.
4. A member name containing `/` or `~` round-trips through `JsonPointer`.
5. `JsonPointer(...).field` agrees with `first_validation_error_field()` on the
   same path.
6. `assert_wire_error(..., issues=[...])` fails when the channel is absent or
   wrong, and a test proves it fails (not just that it passes).
7. Emitted envelope validates against `core/error.json` at the pinned version.
8. `make quality` green.
