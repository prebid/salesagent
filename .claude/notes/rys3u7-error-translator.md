# Error-translator collapse — scope authority for salesagent-rys3u.7

Frozen copy of the ticket's design, verbatim. The molecule's solution-review
grades fidelity to THIS file, which is why it lives outside the bead.

One implementation of exception -> AdCPSalesAgentError, named for what it does, and one
serializer on AdCPSalesAgentError. Four concrete defects, all in the error path.

## The model, which is already correct

Application code raises `AdCPSalesAgentError`. Foreign code (pydantic, stdlib, third
party) raises whatever it raises. The transport boundary catches everything and
shapes the response:

    except Exception as e:
        err = adcp_error_for(e)     # always an AdCPSalesAgentError
        return <transport wrapper>(err.status_code, build_two_layer_error_envelope(err))

    REST : JSONResponse(status_code=err.status_code,
                        content=build_two_layer_error_envelope(err))
    A2A  : InternalError(message=f"{operation} failed: {err.message}",
                         data=build_two_layer_error_envelope(err))
    MCP  : AdCPToolError(build_two_layer_error_envelope(err),
                         status_code=err.status_code)

No new carrier type. `AdCPSalesAgentError` already exposes `.status_code` and `.message`,
and `build_two_layer_error_envelope` already accepts one.

Four of the five boundaries already do this. The boundary shape does not change.

There are TWO kinds of boundary and both are legitimate callers of the mapper:

  * TRANSPORT boundaries, one per protocol, which shape a response.
  * PER-ITEM boundaries inside a partial-success loop, which convert one item's
    failure into that item's advisory and keep going. These need the TOTAL
    mapper, because the loop catches everything: `src/core/tools/creatives/
    _sync.py:202` catches `(ValidationError, ValueError)` and `:377` catches
    `Exception`. `src/core/context_manager.py:369` is the same kind.

A per-item boundary is NOT a misplaced caller. Do not route one through
`adcp_validation_boundary`: that context manager catches `ValidationError` only,
so it covers neither of those clauses, and narrowing the catch to make it fit
would drop the arm the comment at `_sync.py:385-388` exists to preserve
("anything else becomes INTERNAL_ERROR ... synthesizing a bare INTERNAL_ERROR
here instead threw away the field the buyer needs").

## The wrapper stays transport-specific

Do not generify the wrapper into a shared catch-all. `src/a2a_server/
adcp_a2a_server.py:318-322` documents why: `InternalError` must remain an
`A2AError` so the SDK's `JsonRpcDispatcher` serializes it structurally. Raising a
non-`A2AError` there hits the dispatcher's own `except Exception` branch and is
flattened to a bare `InternalError` with NO envelope. Only the
convert-and-envelope half is shared.

## Defect 1 — two implementations of the same mapping

`adcp_validation_boundary` (src/core/validation_helpers.py) raises:

    raise AdCPValidationError(
        field=field if field is not None else first_validation_error_field(e),
        issues=issues_from_validation_error(errors),
    ) from e

`normalize_to_adcp_error` (src/core/exceptions.py:1199-1204), ValidationError
branch:

    return AdCPValidationError(
        field=first_validation_error_field(exc),
        issues=issues_from_validation_error(errors),
    )

Identical but for the optional `field` override.

Keep the total one: it handles `AdCPSalesAgentError` passthrough (with internal-detail
logging), `ValidationError`, `ValueError`, `PermissionError`, and a catch-all
`INTERNAL_ERROR`. Re-express the context manager over it, keeping the override:

    @contextmanager
    def adcp_validation_boundary(context: str = "parameters", field: str | None = None):
        try:
            yield
        except ValidationError as e:
            raise adcp_error_for(e, field=field) from e

The context manager stays for ergonomics -- wrapping a block is genuinely
different from converting an exception already in hand.

Delete the stale claims from its docstring: it says it carries "the
buyer-friendly `format_validation_error` message" and "error.json's top-level
`suggestion`". `AdCPSalesAgentError` has no `message` parameter and `suggestion` resolves
from `CODE_TABLE`, so both fields are identical on either path.

## Defect 2 — the name

`normalize_to_adcp_error` -> **`adcp_error_for`**. "Normalize" names a category
of operation, not the operation. Reads as what it is at the call site:
`raise adcp_error_for(e) from e`.

## Defect 3 — one path normalizes twice

`src/core/mcp_compat_middleware.py:124` normalizes for the audit record, then
passes the RAW exception to `_translate_to_tool_error`, whose comment states it
"intentionally normalizes it a second time". Convert once, pass the result.

## Defect 4 — three serializers on AdCPSalesAgentError, two dead

  * `to_adcp_error()` (src/core/exceptions.py:414) -- ZERO call sites. Returns
    the flat `{"errors": [...]}` dict; its docstring calls itself legacy,
    superseded by `build_two_layer_error_envelope`, "retained only for
    non-envelope callers (audit logging, SDK interop)" -- callers that do not
    exist. DELETE.
  * `AdCPSalesAgentError.to_dict()` (:384) -- no production callers. Five hits: one is a
    different class (`gam_inventory_discovery.py:944`); the other four are tests
    (`test_product_schema_obligations.py:477`, `:498`, `:1446`, and
    `tests/bdd/steps/generic/then_error.py:152`). DELETE, and point those four at
    the wire envelope -- `then_error.py:152` reads `error.to_dict()`, an
    assertion on a reconstructed object where a wire envelope exists.

Leaves one serializer, `build_two_layer_error_envelope`, whose name states which
shape it produces.

## Changelog

  * Rename `normalize_to_adcp_error` -> `adcp_error_for`; add the optional
    `field` override.
  * Re-express `adcp_validation_boundary` over it; delete the stale docstring
    claims.
  * Convert once in `mcp_compat_middleware`.
  * Delete `to_adcp_error()`.
  * Delete `AdCPSalesAgentError.to_dict()`; migrate its four test callers to the wire
    envelope.

## Acceptance

  * One implementation of exception -> AdCPSalesAgentError.
  * No new carrier type; boundaries read `.status_code` / `.message` off the
    AdCPSalesAgentError.
  * No exception normalized twice on any path.
  * `AdCPSalesAgentError` has exactly one serializer.
  * No test asserts on a reconstructed error object where a wire envelope exists.
  * A2A still raises `A2AError` subclasses, so the dispatcher keeps serializing
    the envelope structurally.
  * `make quality` green; full in-network run with 0 failures and an unchanged
    xpass set (baseline: 38 nodeids, sha256 b73ab7428e1b1970, pinned at
    .claude/notes/rys3u6-baseline-xpass.txt).
