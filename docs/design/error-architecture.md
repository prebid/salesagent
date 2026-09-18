# Error architecture

Every buyer-facing error resolves through one mechanism. This page describes it: the one
code table, the two lanes an error can travel in, and the reason neither lane authors
its own text. Its companion, [Building a tool § Errors](../development/building-tools.md#errors),
states the rules for writing a raise site. Read this page when you add a code, decide
which lane a failure belongs in, or change how an error reaches the wire.

**Measured at:** `073987796`, 2026-09-18, against AdCP 3.1.1 (`adcp==6.6.0`).

## Contents

- [One table classifies every code](#one-table-classifies-every-code)
- [The code vocabulary is open; recovery is not](#the-code-vocabulary-is-open-recovery-is-not)
- [An error names its code by its class](#an-error-names-its-code-by-its-class)
- [Two lanes: a raised failure and an advisory](#two-lanes-a-raised-failure-and-an-advisory)
- [Neither lane authors text](#neither-lane-authors-text)
- [Field-level rejections travel in issues](#field-level-rejections-travel-in-issues)
- [retry_after is clamped once](#retry_after-is-clamped-once)
- [Worked case: format discovery](#worked-case-format-discovery)
- [Enforcement](#enforcement)

## One table classifies every code

`CODE_TABLE` (`src/core/errors/codes.py:463`) maps a code to the four things a buyer
needs with it: the recovery classification, the suggestion, the message, and the HTTP
status that signals the failure. Nothing else decides those four, and nothing decides
them per raise site.

The table holds 100 entries. `_load_published_codes` (`src/core/errors/codes.py:261`)
loads the 92 codes AdCP 3.1.1 publishes at import, from the pinned bundle's
`enums/error-code.json` through `importlib.resources`, so the table cannot drift from
the file it came from. The other 8 are this platform's own: `AppErrorCode`
(`src/core/errors/codes.py:118`) declares them with their `CodeEntry` inline, so
declaring a code and declaring what it means are one act — a member with no entry is a
`TypeError` at class creation.

`recovery`, `suggestion` and `message` come from the pinned file and nowhere else. A
message resolves from the first of two sources that has one: an authored override for
the nine published codes whose pinned text is implementer-facing
(`_AUTHORED_SPEC_MESSAGES` in `src/core/errors/codes.py`), then the pinned
`enumDescriptions` with the trailing `Recovery: …` clause stripped. The SDK's
`STANDARD_ERROR_CODES` used to sit between them; it shadowed published text for 37 of
the 92 codes and covered none of the other 55, so it is gone and the SDK is a
cross-check here as it is everywhere else.

`CodeEntry` refuses an empty suggestion or message, and a status outside 100..599, at
construction (`src/core/errors/codes.py:108`). That check runs at the one place that
builds both the loaded and the authored entries, so no test and no raise site re-checks
non-emptiness.

The HTTP status belongs to the code, not to the class that raised it. `_HTTP_STATUS`
(`src/core/errors/codes.py:357`) maps the published codes this seller emits to a status
inside the band the pin fixes per code; a published code this seller never raises
resolves to 500. `AppErrorCode` members carry their status in their own entry.
`CODE_BY_VALUE` (`src/core/errors/codes.py:466`) is the same vocabulary keyed by the
wire string, which is how `AdcpErrorResponse.http_status`
(`src/core/schemas/_base.py:858`) turns a serialized code back into a status.

## The code vocabulary is open; recovery is not

AdCP 3.1.1 types `error.code` in `core/error.json` as a string rather than a closed
enum: the published codes are documentary, a sender may emit a code outside that set,
and a receiver decodes an unknown one by reading `error.recovery`. So a platform code
reaches the buyer verbatim, and nothing between the raise site and the envelope rewrites
a code.

That openness is what makes `recovery` the field with a closed set. `Recovery`
(`src/core/errors/codes.py:71`) is a `StrEnum` of `correctable`, `transient`, and
`terminal` — the one transcription of the wire schema's closed set, and the one
vocabulary in this codebase for that axis.

## An error names its code by its class

`AdCPSalesAgentError` (`src/core/exceptions.py:165`) is generic in its details class, so
each of the 77 concrete subclasses declares its exact detail type in its type parameter
and mypy rejects both a dict and a foreign detail class at every raise site.

An error names its code by its class. There is no second way and no exception:
`__init_subclass__` (`src/core/exceptions.py:258`) refuses at class creation a subclass
whose `_code` the table does not classify, and `__new__` (`src/core/exceptions.py:274`)
refuses to construct a class that declares none — which is what makes
`AdCPSalesAgentError` and `AdCPRequestSignatureError` abstract.

That rule used to read "by its class **or explicitly**, never both and never neither",
and `__new__` carried a second refusal for an `error_code=` on a class that already
declared one. Both halves existed for a single caller. `AdCPRequestSignatureError` was
the only class in the tree declaring no `_code`, so the RFC 9421 verifier was the only
raise site naming its own — which made the invariant a convention with one exception in
it rather than a property nothing can violate. It is now the abstract parent of the 28
request-signature classes (`src/core/exceptions.py:513` onward), each declaring its code
like every other class here, and `error_code` is off the constructor entirely.

Those 28 are **written out**, one class statement each, never generated from
`SignatureErrorCode`. The enum is derived from the SDK's taxonomy, so generating classes
from it would let an SDK upgrade rename this seller's public error classes — silently,
with no source line to change and no diff to review. Typed out, the same upstream change
is an `AttributeError` at import on the line that names the member.

The constructor (`src/core/exceptions.py:318`) has no `message` parameter and no
`error_code` parameter. Its keyword-only parameters are `details`, `issues`, `field`,
`retry_after`, and `internal_detail`. `message`, `recovery`, `suggestion`, and
`status_code` are read-only properties that resolve from `CODE_TABLE` at every read
(`src/core/exceptions.py:376`). No instance can carry a value that disagrees with the
table by any route, assignment included, and `__str__` returns the property.

`adcp_error_for` (`src/core/exceptions.py:1650`) is the one normalizer from an untyped
exception to a typed one. Every branch carries a type mapping and no text. A typed error
passes through unchanged. The SDK's `SignatureVerificationError` becomes the
request-signature class its `code` names, through the written-out
`_SIGNATURE_ERROR_BY_CODE` table (`src/core/exceptions.py:889`) — the one seam where a
code arrives as a string, because checks 1-13 run inside `adcp.signing.verifier` and it
reports which failed the only way it can. A pydantic `ValidationError` becomes
`AdCPInvalidRequestError` with a derived `field` and `issues[]`, and the function tests
for it before `ValueError`, which it subclasses. A plain `ValueError` becomes
`AdCPValidationError`, a `PermissionError` becomes `AdCPAuthorizationError`, and anything
else becomes `AdCPInternalError`.

## Two lanes: a raised failure and an advisory

There are exactly two ways an error reaches a buyer, and the response type says which.

| Lane | What the buyer gets | What builds it |
|---|---|---|
| Raised failure | `AdcpErrorResponse` — `status: "failed"`, the error at both `adcp_error` and `errors[0]`, and no tool payload | `AdcpErrorResponse.of` (`src/core/schemas/_base.py:872`), from the boundary's `failure_response` (`src/core/tools/_boundary.py:163`) |
| Advisory | The tool's own success response, carrying the failure in its declared `errors[]` array | `Error.of` / `Error.from_exception` (`src/core/schemas/_base.py:251`, `:284`) at the tool |

An implementation raises an `AdCPSalesAgentError` subclass and returns only on success.
The boundary records the fault, builds the response once, and raises `AdcpFailure`
(`src/core/exceptions.py:1594`) — the one exception a transport catches. Each transport
adds only its own failure marker: an HTTP status, an MCP `ToolError`, or a failed A2A
task state. All three serialize the body with the same `to_wire` the success path uses.

An advisory is different in kind: the call succeeded, the response is the answer the
buyer asked for, and `errors[]` reports what the tool degraded to produce it. The pinned
response schema has to declare the array — it is per tool, not on the envelope — so the
lane exists only for tools whose schema carries `errors`.

### Which lane a failure takes

Ask whether the response the tool is about to return is still an answer. If the
operation produced its result, the failure is an advisory and the response stays a
success. If there is no result to return, raise.

Two consequences are worth stating, because both have gone wrong here before:

- A partial failure is a successful response. It does not become a failure because
  several entries failed; what kind of wrong an entry is decides the level, not how many.
  [Building a tool § Batch tools have two levels of failure](../development/building-tools.md#batch-tools-have-two-levels-of-failure)
  tabulates the batch case.
- A degraded success must say so. An empty result array with no `errors[]` entry claims
  the seller genuinely has nothing to return. Answering that shape when a dependency
  failed is the defect the advisory lane exists to prevent, and the buyer cannot detect
  it: the response validates, the status is `completed`, and nothing distinguishes
  "none exist" from "the source is unreachable".

Six tools and one service carry an advisory site: `get_adcp_capabilities`
(`src/core/tools/capabilities.py:131`), `get_media_buys`
(`src/core/tools/media_buy_list.py:310`, `:498`), `sync_accounts`
(`src/core/tools/accounts.py:455`, `:1030`), `get_media_buy_delivery`
(`src/core/tools/media_buy_delivery.py:218`, `:342`, `:490`), `list_creatives`
(`src/core/tools/creatives/listing.py:589`), `list_creative_formats`
(`src/core/tools/creative_formats.py:570`, `src/core/creative_agent_registry.py:760`),
and the targeting-capabilities service (`src/services/targeting_capabilities.py:204`).
Each site under `src/core/tools/` names in a comment which response section it degrades.

## Neither lane authors text

Both lanes resolve the same three graded fields from the same table, and neither offers
a parameter for them.

On the exception, they are read-only properties (above). On the advisory, `Error`
(`src/core/schemas/_base.py:180`) narrows the SDK's `code: str` to `ErrorCodeT` while
carrying the parent's length constraints forward, and a `mode="before"` model validator
overwrites `message`, `suggestion`, and `recovery` from `CODE_TABLE`
(`src/core/schemas/_base.py:229`). `Error.of` takes `code`, `field`, `details`, and
`retry_after` and nothing else, so a call site cannot author a sentence — the name does
not exist to pass. `Error.from_exception` takes the typed exception that already knows
its code, so nothing can pair a code with another failure's details.

`frozen=True` alone does not close a derived field: measured against pydantic 2.12.5,
five routes overwrite one. So `model_copy`, `__replace__`, and `model_construct` each
re-validate instead (`src/core/schemas/_base.py:309`), which also means
`model_copy(update={"code": X})` correctly yields X's message rather than carrying the
previous one.

[ADR-010](../decisions/adr-010-graded-wire-fields-are-functions-of-the-code.md) is the
decision that a graded wire field is a function of the code. Its 2026-08-21 amendment
chose an AST guard banning `model_copy` over an override; the code ships the override
and no such guard exists in `.ast-grep/rules/`, so that amendment describes a path not
taken.

`internal_detail` is typed `BaseException | None` and holds the caught exception. It is
absent from `AdcpErrorResponse.of`, so it never serializes;
`.ast-grep/rules/internal-detail-is-an-exception.yml` refuses an authored string there in
every spelling, over `src/`, `scripts/`, and `tests/`.

A details block is a declared class, never a dict. `ErrorDetails.to_wire`
(`src/core/errors/details.py:85`) is the one projection and `_details_to_wire`
(`src/core/exceptions.py:147`) its one call site. `ErrorProblem`
(`src/core/errors/details.py:101`) carries a list of problems as structured facts —
`code`, `subject_type`, `subject_id`, `field`, `rejected_value`, `accepted_values` — and
declares no free-text field, so there is no slot for a sentence to move into.

## Field-level rejections travel in issues

`issues[]` is the pin's structured field-level rejection map, and no raise site authors
an item's text. `ErrorIssue` (`src/core/errors/issues.py:291`) extends the SDK `Issue`,
takes no `message` parameter, derives the message from its `keyword` through
`_KEYWORD_SENTENCES` (`src/core/errors/issues.py:261`), and refuses a passed `message`
explicitly rather than leaving it to the extra mode: `message` is a required field on
the SDK parent, and production's `extra="ignore"` otherwise accepts one silently.

A pydantic error type becomes a JSON Schema keyword through `PYDANTIC_KEYWORD_MAP`
(`src/core/errors/issues.py:149`). At import, `_verify_keyword_map_is_total`
(`src/core/errors/issues.py:234`) verifies that the map is total in both directions over
`pydantic_core.ErrorType`: an unclassified member, or a stale entry pydantic no longer
defines, raises `RuntimeError` at import instead of failing later on a buyer's error
path. A member with no honest JSON Schema equivalent maps to `None`, which omits the
issue rather than naming a cause the seller cannot substantiate.

The pin asks for the same path twice, in two spellings, and one class owns both.
`JsonPointer` (`src/core/errors/issues.py:78`) renders RFC 6901 for `issues[].pointer`
and the JSONPath-lite form for the top-level `field`, and `pointer_to_field`
(`src/core/errors/issues.py:456`) is the translation the pin makes a MUST. The exception
derives `field` from `issues[0].pointer` in `__init__` when the caller passed none
(`src/core/exceptions.py:353`), so every reader of the error sees one value.

## retry_after is clamped once

The pin bounds `retry_after` at 1..3600 and requires a seller to stay inside it.
`RETRY_AFTER_MAX` and `clamp_retry_after` (`src/core/exceptions.py:115`, `:118`) are the
one floor and ceiling, shared by the idempotency policy's rejection branches, the egress
attempt recorder, and the outbound error mapping. It is a spec constant, not an
operational knob, so it is deliberately not settings-tunable.

## Worked case: format discovery

Format discovery is where the advisory lane was first argued for, and it is the clearest
case: one tool aggregates over several creative agents, any of which can fail
independently while the rest answer.

`list_creative_formats` reads the pinned response schema's optional `errors` array
(`media-buy/list-creative-formats-response.json` in the 3.1 bundle declares
`errors: array of core/error.json`, with only `formats` required). The registry returns
a `FormatFetchResult` carrying formats, one advisory per failed agent, and — never
serialized — the failed agent URLs in the same order
(`src/core/creative_agent_registry.py:142`, `:697`). The tool then decides the code and
builds the response (`src/core/tools/creative_formats.py:137`).

These product obligations are what the tests and the source comments cite. They are not
AdCP obligations: the pin permits the array and does not mandate it for this condition.

| Obligation | What it requires | Where it holds |
|---|---|---|
| FD-ERR-01 | A failed agent yields formats from the healthy agents plus one `errors[]` entry for the failed one | `src/core/creative_agent_registry.py:753`, `src/core/tools/creative_formats.py:521` |
| FD-ERR-02 | All agents failing yields empty `formats` plus `errors[]`, never a bare empty array | same sites; the tool omits `errors` only when the list is empty |
| FD-ERR-03 | A registry that cannot be created raises `AdCPServiceUnavailableError`, so the failure takes the raised lane and the buyer gets `status: "failed"` | `src/core/tools/creative_formats.py:168` |
| FD-ERR-04 | Each entry is a spec `error.json` object with at least a code and a message | structural: `Error` derives the message from the code and `CodeEntry` refuses an empty one |
| FD-ERR-05 | All agents succeeding leaves `errors` absent | `src/core/tools/creative_formats.py:521` (`errors=agent_errors if agent_errors else None`) |

Two further facts about this case matter more than the obligations.

**One internal cause, two buyer-facing conditions.** A failed agent means different
things depending on whether the request referenced it, and only the tool can tell —
the registry never sees the request. `_route_agent_failures`
(`src/core/tools/creative_formats.py:531`) splits them: when `req.format_ids` names a
format on the failed agent, the reference did not resolve, and the entry becomes
`REFERENCE_NOT_FOUND` with `field="format_ids"`; when nobody asked for it, the agent
merely failed during seller-side aggregation and the entry stays `AGENT_UNREACHABLE`
with `field="formats"`, naming the response section it degrades. AdCP 3.1.1 is explicit
about the first half — "Requested `format_id` doesn't exist, or referenced creative
agent is unavailable / not accessible. `error.field` MUST identify which typed parameter
failed to resolve" (`v3.1.1:docs/creative/task-reference/list_creative_formats.mdx:654`).

The split is buyer-visible in `recovery`, which is the point: `AGENT_UNREACHABLE` is
`transient`, so retrying the same request may work, and `REFERENCE_NOT_FOUND` is
`correctable`, so retrying the same `format_ids` never does. Both derive from the code,
so the raise site authors neither.

**No advisory names the agent.** Every field of an `error.json` object is client-facing,
so nothing interpolates the caught exception or the seller-configured `agent_url` into
one. Correlation between an advisory and the agent that caused it travels on
`FormatFetchResult.failed_agent_urls`, parallel to `errors` and never serialized; the
raw cause goes to the server-side log.

**The admin helper is a separate path, and it degrades.**
`list_available_formats` (`src/core/format_resolver.py:477`) returns `[]` for every
failure, typed or not. A comment at the site records that as deliberate: its only caller
is the admin UI (`src/admin/blueprints/products.py:158`), which catches the SDK's
`adcp.exceptions.ADCPError` — a different class tree from `AdCPSalesAgentError` — so
propagating a typed error there reaches no buyer and turns a degrading page into a 500.
The obligations above are about the buyer-facing tool, not this helper.

## Enforcement

| What it holds | Mechanism |
|---|---|
| A class cannot name a code the table does not classify | `__init_subclass__` at class creation (`src/core/exceptions.py:258`) |
| An error cannot be built by a class that declares no code | `__new__` (`src/core/exceptions.py:274`) |
| A code table entry cannot be blank or carry a non-status | `CodeEntry.__post_init__` (`src/core/errors/codes.py:108`) |
| Every pydantic error type is classified, or explicitly omitted | import-time totality check (`src/core/errors/issues.py:234`) |
| A raise site cannot author message, recovery, or suggestion | no such parameter, on either lane; properties and a `mode="before"` validator |
| `internal_detail` cannot take a string | `.ast-grep/rules/internal-detail-is-an-exception.yml` plus mypy on `src/` |
| Business logic cannot construct a wire error directly | `tests/unit/test_architecture_no_error_construction_in_impl.py` — `Error(code=...)` literal construction under `src/core/tools/` and `src/adapters/`, with an empty cap dict, so a new site fails immediately; the sanctioned constructors are `Error.of` and `Error.from_exception` |
| Recovery values stay inside the pinned set | `tests/unit/test_architecture_error_recovery_enum_conformance.py` |
| The two authentication errors are the resolver's alone | `ruff-boundary.toml` TID251 |
| A failure body is a valid response envelope | `AdcpErrorResponse` is a declared response class; the pin requires `status` and `of` sets it |

## What this replaced

The lanes used to disagree with each other and with the pin.

The advisory lane was hand-built text: `wire_advisory()` constructed an entry and
`normalize_advisory_errors()` repaired it afterwards, filling `recovery` and `suggestion`
only where a caller had left them unset and never touching `message` at all.

The raised lane took the buyer's text as arguments. Its constructor accepted `message`,
`suggestion`, and `status_code`, and per-class `_default_message` / `_default_recovery` /
`_default_suggestion` knobs supplied the rest, so a class could contradict the pinned
classification for its own code. The HTTP status came from a per-class
`_default_status_code`, writable by inheritance, so a class that re-coded itself kept its
parent's status — 26 such declarations, per the note at `src/core/errors/codes.py:345`.
`details` was typed `dict[str, Any]`, so each raise site assembled the facts in whatever
shape it chose.

On the way out, `ERROR_CODE_MAPPING`, `translate_error_code()`, and
`to_wire_error_code()` then rewrote the code against a closed `WIRE_STANDARD_CODES` set,
which an open vocabulary makes unnecessary. Each transport hand-assembled its own refusal
dict, and none of those dicts could carry `status` or `context`, so every error body this
seller emitted was invalid against every pinned response schema.

None of those mechanisms exists; the names survive only in comments recording their
removal (`src/core/exceptions.py:50-99`). The code a raise site declares is the code the
buyer reads, and the four fields around it are table lookups.
