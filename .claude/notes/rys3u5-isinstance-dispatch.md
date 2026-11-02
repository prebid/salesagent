# Plan: `except` is the dispatch for exceptions, not `isinstance`

Scope authority for `salesagent-rys3u.5`, which the `structural-change` formula
requires to live OUTSIDE the task it grades. The principle, the groups and the
acceptance below are TRANSCRIBED from the ticket the owner wrote; this document
adds only the drift reconciliation in § "The tree has moved", which is a
statement of fact about the current tree, not a new goal.

Read together with `.claude/notes/rys3u6-issues-channel.md`, the sibling lane's
plan, and the parent epic `salesagent-rys3u`.

## The principle

`except` is the language's type dispatch for exceptions, so an `isinstance` on
one is redundant. Every remaining instance is either a signature declared wider
than any caller needs, or a classification that belongs on the object.

Precedent, already fixed under `rys3u.2`: `_invalid_params_from_ssrf_error` in
`src/a2a_server/adcp_a2a_server.py` declared `exc: Exception` and narrowed it
with `isinstance(exc, AdCPUrlNotAllowedError)`. One caller already held that
type; the other held a bare `ValueError` and needed one built. Narrowing the
parameter and building the error at the caller that lacks one deleted the branch.

## No behavior delta

If the buyer sees any difference, the refactor is wrong. That is why this lane
runs on `structural-change` rather than a TDD formula: the gate is a PREDICTION
(a command, the number it prints now, the number it prints after), not a new
test. Group 2 is the one exception, and it is a deliberate decision rather than
a refactor — see below.

## Group 1 — type ladders on an exception held as a value

A helper takes `Exception` and re-derives what its caller already knew. Fix by
dispatching at the catch site with `except`, or by narrowing the helper's
parameter so the caller's knowledge survives the call.

- `src/core/tool_error_logging.py` — `extract_error_info` (`AdCPToolError` /
  `AdCPSalesAgentError` / `ToolError` ladder), `is_typed = isinstance(error, AdCPSalesAgentError)`,
  and the `AdCPToolError` branch in the `ToolError`-to-envelope converter.
- `src/services/property_discovery_service.py` — `_log_fetch_error`'s three-way
  ladder over `AdagentsNotFoundError` / `AdagentsTimeoutError` /
  `AdagentsValidationError`.

## Group 2 — a second classifier duplicating CODE_TABLE

`src/core/metrics.py` `categorize_error` recomputes by `isinstance` what the
error already carries: `AdCPServiceUnavailableError().recovery` is `transient`,
`AdCPValidationError().recovery` is `correctable`. Two classifiers for one fact
is the defect the parent epic exists to remove, and the hand-picked list
silently misses every AdCP class not on it — `AdCPNotFoundError` lands in
`other` today.

CAUTION, and why this is not a trivial edit: the labels are Prometheus series
names. Deriving from `recovery` moves every correctable AdCP error from `other`
into `validation`, which shifts existing series and any dashboard reading them.
Decide the label mapping first and record the decision.

Keep `isinstance` for the builtin branches. You cannot attach a property to
`TimeoutError`.

## Not in scope

- Foreign types this repo does not own: `SQLAlchemyError`, pydantic
  `ValidationError`, fastmcp `ToolError`, `GAMError`, builtins. Legitimate.
  `normalize_to_adcp_error` in `src/core/exceptions.py` is exactly this — a
  boundary normalizer converting types it did not define.
- Response-union discriminators, which are not exceptions:
  `isinstance(response, CreateMediaBuyError)` in `media_buy_create.py` and
  `isinstance(result, UpdateMediaBuyError)` in `media_buy_update.py`.
  Errors-as-return-values is a separate question.
- The migration scaffolding in `_details_to_wire`, which already carries a
  delete-when note tied to `rys3u.2`.

## The tree has moved — reconcile the line, not the intent

Measured at HEAD `151759604`, 2026-08-27. The ticket's acceptance criterion 1
names this oracle:

    grep -rnE "isinstance\([a-z_]+, *\(?(Exception|AdCP[A-Za-z]*Error|ValueError|ValidationError)" --include='*.py' src/ | wc -l

It prints **14**. Those 14 are NOT the 11 sites the ticket's two groups
enumerate, and the difference is load-bearing for the design atom:

| site | disposition |
|---|---|
| `src/core/tool_error_logging.py` ×4 | Group 1 — in scope |
| `src/core/metrics.py` ×1 | Group 2 — in scope (`:59`'s sibling ladder does NOT match the oracle, because it leads with `TimeoutError`) |
| `src/core/exceptions.py` ×3 | the boundary normalizer the ticket puts OUT of scope |
| `src/core/mcp_compat_middleware.py` ×1 | foreign type (pydantic `ValidationError`) — out of scope |
| `src/core/tools/creatives/_sync.py` ×1 | foreign type (pydantic `ValidationError`) — out of scope |
| `src/adapters/gam/utils/error_handler.py` ×1 | boundary normalizer over GAM types — out of scope |
| `src/services/property_discovery_service.py` ×1 | `isinstance(result, Exception)` over an `asyncio.gather` result — a VALUE discriminator, the same class the ticket excludes for response unions |
| `src/a2a_server/adcp_a2a_server.py` ×1 | the ticket calls this the ALREADY-FIXED precedent; the branch is still present |
| `src/app.py` ×1 | prose inside a docstring, not code |

Two consequences the design atom must settle before any edit, and which the
solution-review gate should grade rather than wave through:

1. **`14 -> 0` is not reachable inside the stated scope.** Nine of the fourteen
   are sites the ticket itself excludes, or are not code at all. Either the
   oracle narrows to the in-scope set, or the exclusions are re-opened. Picking
   silently is the failure mode; the number must not be met by touching a site
   the ticket protects.
2. **`property_discovery_service`'s three-way ladder is in the ticket's Group 1
   but is invisible to the oracle** — `AdagentsNotFoundError` does not match
   `AdCP[A-Za-z]*Error`. So the count can reach its target while the work the
   ticket describes is undone. The oracle and the scope have to be brought into
   agreement in the design, not discovered at the verify atom.

## Acceptance

Transcribed from the ticket; criterion 1 is subject to the reconciliation above.

1. No `isinstance` naming an exception class this repo defines, outside a
   boundary normalizer handling foreign types.
2. Each site resolved one of two ways, recorded per site: the signature was
   declared wider than any caller needs (narrow it), or the classification
   belongs on the object (move it).
3. `categorize_error` derives AdCP classification from the error, with the label
   mapping decided and recorded.
4. ZERO new test files, ZERO new guard files, ZERO allowlist entries.
5. Unit tests COLLECTED unchanged, `mypy` 0. A changed collected count means
   behavior moved.
6. `cassini run` exit=0 with zero failures, and the xpassed SET byte-identical
   to `.claude/notes/rys3u6-baseline-xpass.txt` (38 nodeids, sha256
   `b73ab7428e1b1970` in the no-trailing-newline form).
