"""AdCPSchemaValidator's exception boundary: what each failure class comes out as.

Three classes of failure cross this module's boundary, and callers branch on
the difference:

- the payload violates the AdCP contract      -> SchemaValidationError
- the INSTRUMENT failed (schema tree missing,
  schema file corrupt, $ref unresolvable)     -> SchemaError
- the validator itself has a bug              -> that bug's own exception,
                                                 unwrapped

Collapsing the third class into the first is the exact confusion #1843 exists
to eliminate: an AttributeError in the validator reading as "your payload is
not spec-compliant" sends the reader hunting a contract violation that does
not exist.

Regression for the PR #1868 review: the schema-root lookup used to raise a
bare RuntimeError when the installed adcp SDK has no schema tree for the
pinned spec version, while every other pinned-schema failure point raised
AssertionError. Both are now one type -- pinned_schema.PinnedSchemaError --
which AdCPSchemaValidator._wrap_pinned translates into the public SchemaError,
"the type this module's callers branch on" per its own docstring.
AdCPSchemaValidator.__init__ resolves the schema root through _wrap_pinned on
every instantiation, so a caller written to the documented `except
SchemaError:` contract catches an SDK-layout failure too.

No test previously simulated a missing SDK schema tree; existing SchemaError
tests (tests/e2e/test_schema_validation_standalone.py) cover bad refs only.
"""

from __future__ import annotations

import json

import jsonschema.exceptions
import pytest
import referencing.exceptions
from referencing.jsonschema import DRAFT7

from tests.helpers.adcp_schema_validator import AdCPSchemaValidator, SchemaError, SchemaValidationError

# One task that exists in the pinned index, so _find_schema_ref_for_task
# resolves and execution reaches _validate_against_schema — the boundary
# under test — rather than short-circuiting on a missing ref.
_TASK = "get-products"

# PointerToNowhere requires the resource the pointer failed against; its
# content is irrelevant here — only the exception's TYPE is under test.
_DRAFT7_RESOURCE = DRAFT7.create_resource({})


def _validator_raising(exc: BaseException) -> AdCPSchemaValidator:
    """A validator whose compiled-validator lookup fails with *exc*.

    _get_compiled_validator is the first call inside _validate_against_schema's
    try block, so this injects a failure at the boundary without needing a
    corrupt SDK on disk.
    """
    validator = AdCPSchemaValidator()

    def _boom(_schema_ref: str):
        raise exc

    validator._get_compiled_validator = _boom  # type: ignore[method-assign]
    return validator


async def test_validator_bug_propagates_unwrapped():
    """An implementation bug must NOT be relabeled as a contract violation."""
    validator = _validator_raising(AttributeError("'NoneType' object has no attribute 'iter_errors'"))

    with pytest.raises(AttributeError):
        await validator.validate_response(_TASK, {"products": []})


@pytest.mark.parametrize(
    "instrument_failure",
    [
        pytest.param(jsonschema.exceptions.SchemaError("schema is not valid draft-07"), id="invalid-schema"),
        # The three referencing types do NOT share a base: Unresolvable is its
        # own root, while Unretrievable and NoSuchResource subclass KeyError.
        # Listing all three keeps the tuple in adcp_schema_validator.py honest —
        # dropping any one of them turns exactly one of these params red.
        pytest.param(
            referencing.exceptions.PointerToNowhere(ref="#/nope", resource=_DRAFT7_RESOURCE), id="unresolvable-pointer"
        ),
        pytest.param(referencing.exceptions.Unretrievable(ref="file:///pinned/schemas/gone.json"), id="bad-ref"),
        pytest.param(
            referencing.exceptions.NoSuchResource(ref="file:///pinned/schemas/absent.json"), id="absent-resource"
        ),
        pytest.param(json.JSONDecodeError("Expecting value", "{", 0), id="corrupt-schema-file"),
    ],
)
async def test_instrument_failures_surface_as_schema_error(instrument_failure):
    """A broken instrument is SchemaError, never SchemaValidationError.

    SchemaValidationError subclasses SchemaError, so `pytest.raises(SchemaError)`
    alone would pass on the pre-fix code too — the exact-type assertion is what
    grades the distinction.
    """
    validator = _validator_raising(instrument_failure)

    with pytest.raises(SchemaError) as exc_info:
        await validator.validate_response(_TASK, {"products": []})

    assert not isinstance(exc_info.value, SchemaValidationError), (
        f"{type(instrument_failure).__name__} is an instrument failure, but it surfaced as "
        f"SchemaValidationError — callers read that as 'the payload violates the AdCP contract'"
    )


def test_missing_sdk_schema_tree_raises_schema_error(monkeypatch):
    import adcp

    # pinned_schema.schema_root() imports adcp at call time and derives the
    # schema directory from get_adcp_spec_version(); a nonexistent version makes
    # the directory-existence check fail, exercising the failure branch.
    monkeypatch.setattr(adcp, "get_adcp_spec_version", lambda: "0.0.0")

    # SchemaError subclasses neither PinnedSchemaError nor AssertionError, so
    # this raises-check alone proves the boundary translation happened.
    with pytest.raises(SchemaError):
        AdCPSchemaValidator()
