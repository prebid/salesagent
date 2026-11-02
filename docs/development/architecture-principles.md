# Architecture principles

The rules that decide where code belongs in this codebase — and why. The
mechanics of how a request reaches business logic are in
[Request lifecycle](request-lifecycle.md); the boundary contract with
canonical examples is Critical Pattern #5 in [CLAUDE.md](../../CLAUDE.md) and
[Patterns reference](patterns-reference.md). This page sits above both: six
principles, each short enough to apply on sight.

Every request takes the same path:

```
wire ──► boundary: construct models, resolve identity ──► _impl(models → models) ──► boundary: serialize models ──► wire / DB / egress
```

The boundary constructs models on the way in and serializes them on the way
out, and the code in the middle never touches anything else.

The six principles share one shape: one place states a fact, everything else
**derives** from it, and the code **refuses** at load any construction that
states that fact twice. A declaration drifts and a test permits; something
derived cannot drift, and something refused cannot be written. When you choose
how to enforce a rule, prefer refusing the shape to checking for it.

## 1. Logic lives in `_impl` — everything else is infrastructure

**Rule.** Business logic lives in exactly one place: the `_impl` functions
under `src/core/tools/` and the services they delegate to. Everything before
and after them — transports, validation, version negotiation, identity
resolution, account resolution, idempotency, the response envelope, error
translation — is infrastructure, and it lives in one seam,
`src/core/tools/_boundary.py`. If logic appears outside `_impl`, that placement
is the defect, regardless of whether the logic is correct.

**Illustration.** A budget rule added to the REST handler in
`src/routes/api_v1.py` is logic that the MCP and A2A callers never run. The
same rule inside `_create_media_buy_impl` runs identically for all transports,
because every transport makes the same call —
`serve(tool_name, payload, headers, protocol)` — and there are no per-tool
transport wrappers for it to hide in.

**Consequence.** Adding a transport, or fixing a transport bug, never changes
behavior; changing behavior never touches a transport. The `_impl` signature is
exactly `(req: <DTO>, identity: <one of the three identity types>)`, and the
identity annotation IS the tool's credential policy — the registry derives
`ToolSpec.requires_credential` from it, so a protected tool cannot declare
`ResolvedIdentity` and be served anonymously, and has no `None` left to
re-check on a caller the resolver already admitted. Enforcement: `_impl` may not import
transport machinery (`tests/unit/test_transport_agnostic_impl.py`); the
`ToolImpl` protocol typing `ToolSpec.impl` grades the DTO and identity type
against each other at the row (mypy);
`.ast-grep/rules/impl-signature-is-request-and-identity.yml` refuses what a
protocol cannot (an optional or defaulted identity, an extra defaulted
parameter); `ruff-boundary.toml` bans importing any `_impl` name outside the
registry, and bans importing the resolver at all. When unsure where a change
goes, use the [placement
table](request-lifecycle.md#where-does-my-change-go) in the request lifecycle.

**Corollary — a controller never calls another controller.** An `_impl` is a
controller: it reads who is calling off the identity and delegates. The work
belongs in a service function that takes an already-resolved caller.
`update_media_buy` uploads a package's inline creatives by calling the service,
`sync_creatives` (`src/core/tools/media_buy_update.py:945`), and not
`_sync_creatives_impl` — calling the other controller re-runs an auth check
that already passed and drags the outer request's `idempotency_key` into a
function with no business seeing it. `sync_creatives` is the one extracted
service; extract the next one when a second tool needs it.

## 2. Trust the database — never validate on read

**Rule.** The application wrote every row in the database, and the stored
structures are designed so that nothing the application stores can crash it.
Therefore **reads do not validate**: read into Pydantic models and proceed. No
`try/except` around parsing the application's own rows, no fallback branches
for data the write path never produces, no re-checking invariants that the
write path already guaranteed.

**Illustration.** JSON columns are declared with a model —
`JSONType(model=BrandReference)` in `src/core/database/json_type.py` — so the
column type itself materializes a typed model on read and serializes the model
on write. A repository method returns those models directly; the caller does
not first inspect them for well-formedness.

**Consequence.** Defensive re-validation is the instinct most newcomers arrive
with, and in this codebase it is the anti-pattern: it duplicates the write
path's guarantee, buries real bugs under fallbacks, and makes every read site
a policy decision. If a read *does* fail, the defect is on the write path (or
in a migration) — fix it there once, not at every read site. The typed-column
and repository layers plus code review carry this principle, rather than a
single structural guard; `tests/unit/test_architecture_no_defensive_rootmodel.py`
bans one defensive pattern directly (no `hasattr(x, "root")` unwrapping). Bare
`JSONType` columns with no model are legacy; a column you add always declares
one.

## 3. Pydantic models everywhere inside business logic

**Rule.** `_impl` receives models, works with models, and returns models —
never dicts. A dict has no schema, so every consumer re-derives the structure,
and none of those derivations can be checked. The same rule holds at the
adapter seam: an adapter takes and returns declared carrier types, not the
buyer's DTO and not a dict.

**Illustration.** `_create_media_buy_impl` takes a `CreateMediaBuyRequest` and
returns a `CreateMediaBuyResult`; it never sees the JSON-RPC params, the HTTP
body, or a `kwargs` dict of loose fields. Going outward, it hands the adapter
an `AdapterCreateRequest` — what the adapters read, and nothing a buyer must
send — and reads back an `AdapterCreateResult` (`src/adapters/base.py`). Both
carriers are `extra="forbid"`, so a stale keyword raises at construction
instead of being dropped.

**Consequence.** The type checker and the schema guards see every field access.
The `ToolImpl` protocol checks the DTO on a registry row and the `req` parameter
of its implementation against each other, so a model arrives typed at the
boundary and stays typed until a named edge serializes it. An error's
`details` obeys the same rule: it is a declared `ErrorDetails` subclass named
in the exception's type parameter, never a free-form dict.

## 4. Construction and serialization happen at the boundary — never in `_impl`

**Rule.** This principle is the central symmetry of the design:

- **Inbound**: The boundary constructs the Pydantic request model —
  `validated_request` parses the wire payload into the registry row's DTO
  ([Request lifecycle § The boundary](request-lifecycle.md#the-boundary-serve-and-invoke_tool)),
  and the DTO is the accepted shape: a `mode="before"` validator on the
  `BuyerRequest` mixin reduces the payload to the fields the DTO declares,
  whatever `additionalProperties` the pinned schema permits.
- **Outbound, response**: `_impl` returns a response model and stops. It does
  not build the response and does not shape its own output — a wire model
  conforms BY INHERITANCE from the pinned `adcp` type, and a field that must
  exist on the model but not on the wire is `Field(exclude=True)` at its
  declaration. Exactly one class carries a `@model_serializer`,
  `WireSerializerMixin` in `src/core/schemas/_base.py` (enforced by
  `tests/unit/test_architecture_one_wire_serializer_seat.py`), and it carries
  exactly two concerns: re-serializing a nested child by its instance, and
  retaining a required-nullable field under `exclude_none`. Never override
  `model_dump` — a `@model_serializer` runs on all three serialization paths and
  an override runs on one.
- **Outbound, database**: `_impl` hands models to repositories; it does not
  build dicts to store. `MediaBuyRepository.create_from_request`
  (`src/core/database/repositories/media_buy.py`) serializes the request at
  the DB boundary for exactly this reason, and `JSONType` serializes model
  values on write.
- **Outbound, external calls**: Egress goes through the egress gateway
  (`src/core/security/outbound_http.py`), which owns the wire-level concerns.

**Illustration.** A `model_dump()` inside `_impl` signals the violation: it
means business logic is deciding a wire or storage format.
`ruff-serialization.toml` and `.ast-grep/rules/serialize-only-at-the-edges.yml`
ban it everywhere under `src/` except the named edge modules — fifteen path
entries, exempted by path and never by a suppression comment. The wire itself
is one function, `to_wire` (`src/core/tools/_wire.py`), with three callers: the
MCP structured content, the A2A artifact, the REST body.

**Consequence.** Serialization decisions (aliases, exclusions, spec-pinned
nullable fields) happen once, at boundaries every transport shares, rather than
once per call site. The buyer's `context` object is the extreme case:
`AdcpResponse` refuses the field on construction and on assignment, and
`_boundary._served` writes it through the one deliberate bypass, on a fresh
run, a replay, and a failure alike — so no transport can forget the echo and
nothing writes it twice. Repositories are the only DB writers
(`tests/unit/test_architecture_repository_pattern.py`).

## 5. Errors: typed raises inside, one response class at the boundary

**Rule.** `_impl` signals failure by raising a typed `AdCPSalesAgentError`
subclass (`src/core/exceptions.py`) — and nothing else. The raise site names a
**code** and supplies **structured facts**; it never authors buyer-facing text.
The mechanism in full, including the second lane an error can travel in, is
[Error architecture](../design/error-architecture.md).

- `CODE_TABLE` (`src/core/errors/codes.py`) is the one classifier. It holds an
  entry per code — `recovery`, `suggestion`, `message`, HTTP `status` — which it
  builds at import from the pinned `adcp` bundle's `error-code.json` plus the
  platform codes `AppErrorCode` declares.
- The constructor has **no `message` parameter**. `message`, `recovery`,
  `suggestion`, and `status_code` are read-only properties resolving from
  `CODE_TABLE` at every read, so no instance can carry a value that disagrees
  with the table by any route.
- An error names its code by its class or explicitly, never both and never
  neither: `__init_subclass__` refuses a class whose code the table does not
  classify, and `__new__` refuses a bare base construction and refuses an
  `error_code=` that overrides a class that already names one.
- `details` is a declared `ErrorDetails` subclass — the base is generic in its
  detail type (`AdCPSalesAgentError[DetailsT]`), so mypy rejects a dict or a
  foreign detail class at every raise site. A list of problems is
  `ErrorProblem` entries carrying `code`, `subject_type`, `subject_id`,
  `field`, `rejected_value`, and `accepted_values` — and no free-text slot.
- Field-level rejections go to the pin's `issues[]` channel
  (`src/core/errors/issues.py`): an RFC 6901 pointer, a JSON Schema keyword,
  and a message derived from that keyword. `ErrorIssue` takes no `message`
  parameter.
- `internal_detail` is typed `BaseException | None` and holds the **caught
  exception**, never an authored sentence. It never serializes;
  `record_boundary_error` puts it in the server-side record with the traceback
  attached.

**Illustration.** A validation failure raises `AdCPValidationError(details=...)`.
The boundary builds one `AdcpErrorResponse` with `failure_response(...)`
(`src/core/tools/_boundary.py`), which records the fault with
`record_boundary_error`, carries the same error object at both layers the wire
declares (`adcp_error` and `errors[0]`), and stamps `context` and
`adcp_version` through `_served`; `_failed` raises it as `AdcpFailure`, the one
exception a transport catches. Each transport serializes that response with
`to_wire(...)` and adds only its own marker — REST the HTTP status from
`response.http_status`, MCP an `AdCPToolError` raised in `RegistryTool.run`,
A2A a Task state taken from the response's own `status`. Nothing rewrites a
code between the raise site and the wire: the AdCP code vocabulary is open and
a receiver decodes an unknown code by reading `recovery`, so a platform code
reaches the buyer verbatim.

**Consequence.** The error class *is* the code's identity, so a new error
condition is a new subclass, not a string — and `recovery`, the field a
receiver MUST read, is the one axis closed to three values (`Recovery` in
`src/core/errors/codes.py`). Guards enforce both sides of the boundary: no
`ToolError` in business logic (`ruff-boundary.toml`'s TID251 ban on
`fastmcp.exceptions.ToolError`), no `Error(code=...)` construction in
business logic
(`tests/unit/test_architecture_no_error_construction_in_impl.py`, whose cap
dict is empty, so a new site fails immediately), no authored string in
`internal_detail` in any spelling
(`.ast-grep/rules/internal-detail-is-an-exception.yml`, over `src/`, `scripts/`,
and `tests/`), and one server-side record per failure
(`tests/unit/test_tool_error_logging.py`).

## 6. The consequence for tests — derived, not decreed

Because of principle 1, logic exists in exactly one transport-agnostic place.
BDD therefore verifies **logic**, and scenarios are transport-independent by
construction: the scenario text never names a transport, the transport is a
parametrized fixture value (`ctx`), and the same scenario runs on `a2a`, `mcp`,
`rest`, and — when the in-network stack is up — `e2e_rest` (with `e2e_mcp` and
`e2e_a2a` opt-in per run). The test environment owns what differs per
transport; a scenario that genuinely is about one transport's framing carries
an `@mcp` / `@a2a` / `@rest` tag and is the declared exception. How the harness
derives the rest of it is
[BDD harness architecture](../design/bdd-harness-architecture.md).

It follows that response and error assertions must go through the **wire
helpers**, which read the bytes the transport actually produced:

- errors — `result.assert_wire_error(code, ...)`, which reads
  `wire_error_envelope`, refuses a code `CODE_TABLE` does not classify, and
  defaults `recovery` from that table so the assertion is non-vacuous without
  per-scenario duplication. It forwards to the one locator,
  `assert_envelope_shape()` (`tests/helpers/envelope_assertions.py`), where
  `recovery` is a required keyword. There is no `message_substr` parameter on
  `assert_wire_error` at all: asserting the buyer-facing message checks the
  table against itself;
- success — `wire_response` or the typed payload
  (policy: [tests/CLAUDE.md](../../tests/CLAUDE.md) § "Error verification
  policy").

**Every `Transport` member dispatches over a real wire.** `Transport.IMPL`,
`ImplDispatcher`, and the harness's synthesized error envelope are gone, because
modeling a direct in-process call as a transport gave every "assert on the
wire" rule a way around it. So a `TransportResult` can only come from a wire, and
`wire_error_envelope` holds the transport's own bytes or `None` — the harness
does not rebuild a production exception from them, and `result.error` is
deliberately not a production class, so an `isinstance` assertion against one
fails loudly rather than passing quietly. Calling `_impl` directly remains
correct where the obligation is about what `_impl` returns or raises: use
`env.call_impl(...)`.

A test that reaches for a transport's own representation — parsing an HTTP body
by hand, unpacking a Task artifact inline — steps outside what the scenario
verifies: it tests one transport's framing, which the scenario never claimed to
specify. Guards enforce the discipline
(`tests/unit/test_architecture_bdd_wire_discipline.py`,
`tests/unit/test_architecture_bdd_no_direct_call_impl.py`,
`tests/unit/test_architecture_bdd_dispatch_entries.py`).

## Enforcement

`make quality` checks every rule above: it runs `make quality-ci`, then the
unit and harness suites. The following mechanisms do the checking:

| Mechanism | What it covers |
|-----------|----------------|
| Five TID251 ruff configs over `src/` and `scripts/` — `ruff-boundary.toml`, `ruff-ownership.toml`, `ruff-serialization.toml`, `ruff-environment.toml`, `ruff-egress.toml` | Which module may import which name: the resolver, the `_impl` names, `ToolError`, the auth errors, `ContextObject`, the serializers, `os.environ`, every HTTP client. `ruff-egress.toml` runs with `--ignore-noqa`, so a comment cannot exempt a file — only a per-file-ignores row can |
| `ast-grep scan --config sgconfig.yml` | The rules ruff cannot express: the exact `_impl` parameter list, identity construction limited to its two owners, `context=` written by the boundary alone, `internal_detail` never an authored string, serialization only at the edges |
| `mypy src/` | The `ToolImpl` protocol on `ToolSpec.impl`, the generic detail type on every exception, the typed carriers at the adapter seam |
| Load-time refusals in the code itself | `ToolSpec.__post_init__` (identity annotation versus DTO), `_register_tool` (envelope bases, SDK grounding), `__init_subclass__` on `AdCPSalesAgentError` (code in `CODE_TABLE`), the import-time totality check on the pydantic keyword map |
| `tests/unit/test_architecture_*.py` | The remaining structural invariants, each with a shrink-only allowlist and a stale-entry test |
| Ratchet counters in `.pre-commit-hooks/` | Debt that may only shrink: type-ignores, mypy untyped defs, ruff complexity, code duplication, admin raw sessions, beads ids in FIXMEs (baseline zero) |

New violations fail the build. Allowlists only shrink, and every allowlisted
violation cites a GitHub issue — never a local beads id.
