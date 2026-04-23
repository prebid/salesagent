"""Regression tests for AdCPSchemaValidator error propagation at the
validate_request / validate_response boundary.

Covers the contract from PR #1230: unresolvable ``$ref`` targets surface as
``SchemaResolutionError`` with an actionable "run ``make schemas-refresh``"
message, rather than being category-mistyped as ``SchemaValidationError``.

These are production-path tests (they call ``validate_request`` /
``validate_response``, not the internal ``_resolve_*`` helpers). The existing
tests at ``test_adcp_schema_validator_ref_resolution.py`` exercise helper-level
behavior but would miss regressions on the call chain that actually produced
#1213 — specifically: ``iter_errors`` -> ``retrieve`` -> referencing-library
wrapping -> a bare ``except Exception`` that reclassified the failure as a
schema-validation error. This file locks in the contract at the production
entry point.

Honors ``tests/CLAUDE.md`` rule #2: obligation tests must call a production
function, not just import it.

Relates to #1213.
"""

import json
from pathlib import Path

import pytest

from tests.e2e.adcp_schema_validator import (
    AdCPSchemaValidator,
    SchemaResolutionError,
    SchemaValidationError,
    cache_filename,
)

# ---------------------------------------------------------------------------
# Cache-layout helpers
#
# Rather than monkeypatching internals, we seed a real cache directory under
# ``tmp_path`` in the layout the validator expects, then drive validation end
# to end with ``offline_mode=True`` so no network calls are made.
# ---------------------------------------------------------------------------


TASK_NAME = "fake-unresolvable-task"
ROOT_REF = "/schemas/latest/fake/root.json"

# Absolute HTTPS form is intentional: ``walk_transitive_refs`` only follows
# refs that start with the local ``/schemas/{version}/`` prefix, so this ref
# is skipped during preload. It is only looked up by the ``Draft7Validator``
# during ``iter_errors`` via the Registry's retrieve callback — which is the
# exact code path that previously silently swallowed cache misses and
# mis-categorized them as validation failures.
MISSING_CHILD_REF_ABSOLUTE = "https://adcontextprotocol.org/schemas/latest/fake/missing-child.json"
MISSING_CHILD_REF_PATH = "/schemas/latest/fake/missing-child.json"

# Second child used only for the positive-control fixture.
PRESENT_CHILD_REF = "/schemas/latest/fake/present-child.json"


def _write_index(cache_dir: Path, task_name: str, root_ref: str) -> None:
    """Seed ``index.json`` so ``_find_schema_ref_for_task`` returns ``root_ref``."""
    index = {
        "schemas": {
            "media-buy": {
                "tasks": {
                    task_name: {
                        "request": {"$ref": root_ref},
                        "response": {"$ref": root_ref},
                    }
                }
            }
        }
    }
    (cache_dir / "index.json").write_text(json.dumps(index))


def _write_schema(cache_dir: Path, ref: str, body: dict) -> None:
    """Write ``body`` to the filesystem slot the validator will look up for ``ref``."""
    (cache_dir / cache_filename(ref)).write_text(json.dumps(body))


@pytest.fixture
def unresolvable_ref_cache(tmp_path: Path) -> Path:
    """Cache layout where the root schema $refs a URL not present on disk.

    Layout::

        <tmp_path>/
            index.json                                # fake-task -> root.json
            _schemas_latest_fake_root_json.json       # root; $refs missing-child
            # missing-child.json is DELIBERATELY absent
    """
    _write_index(tmp_path, TASK_NAME, ROOT_REF)
    _write_schema(
        tmp_path,
        ROOT_REF,
        {
            "$id": ROOT_REF,
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "child": {"$ref": MISSING_CHILD_REF_ABSOLUTE},
            },
            "required": ["child"],
        },
    )
    return tmp_path


@pytest.fixture
def resolvable_cache(tmp_path: Path) -> Path:
    """Fully-populated cache for the positive-control test.

    Both the root and the child schemas exist on disk, so validation must
    succeed against valid data.
    """
    _write_index(tmp_path, TASK_NAME, ROOT_REF)
    _write_schema(
        tmp_path,
        ROOT_REF,
        {
            "$id": ROOT_REF,
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "child": {"$ref": PRESENT_CHILD_REF},
            },
            "required": ["child"],
        },
    )
    _write_schema(
        tmp_path,
        PRESENT_CHILD_REF,
        {
            "$id": PRESENT_CHILD_REF,
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Negative path — unresolvable ref must raise SchemaResolutionError
# ---------------------------------------------------------------------------


class TestValidateRequestRaisesOnUnresolvableRef:
    """``validate_request`` must raise ``SchemaResolutionError`` on cache miss.

    Before T2, an unresolvable ``$ref`` hit during ``iter_errors`` was wrapped
    by ``referencing`` as ``Unresolvable`` and then silently caught by a bare
    ``except Exception`` that emitted a ``SchemaValidationError`` at an
    unrelated JSON path. This test locks in the new contract: the failure
    surfaces as ``SchemaResolutionError`` with the failing URL and an
    actionable remediation hint.
    """

    @pytest.mark.asyncio
    async def test_missing_transitive_ref_raises_schema_resolution_error(self, unresolvable_ref_cache: Path) -> None:
        async with AdCPSchemaValidator(cache_dir=unresolvable_ref_cache, offline_mode=True) as validator:
            with pytest.raises(SchemaResolutionError) as exc_info:
                await validator.validate_request(TASK_NAME, {"child": {"name": "anything"}})

        assert exc_info.value.url == MISSING_CHILD_REF_ABSOLUTE
        assert "make schemas-refresh" in str(exc_info.value)


class TestValidateResponseRaisesOnUnresolvableRef:
    """Mirror of the request-side contract for ``validate_response``.

    The payload includes MCP/A2A protocol wrapper fields so the test
    exercises ``_extract_adcp_payload`` before the ref-resolution failure.
    """

    @pytest.mark.asyncio
    async def test_missing_transitive_ref_raises_schema_resolution_error(self, unresolvable_ref_cache: Path) -> None:
        wrapped_response = {
            "message": "Operation completed successfully",
            "context_id": "ctx_abc123",
            "child": {"name": "anything"},
        }
        async with AdCPSchemaValidator(cache_dir=unresolvable_ref_cache, offline_mode=True) as validator:
            with pytest.raises(SchemaResolutionError) as exc_info:
                await validator.validate_response(TASK_NAME, wrapped_response)

        assert exc_info.value.url == MISSING_CHILD_REF_ABSOLUTE
        assert "make schemas-refresh" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Positive control — valid data against fully-cached schemas must succeed.
# Prevents over-rotation: the regression guard above must not accidentally
# reject well-formed inputs that happen to share the fake-task name shape.
# ---------------------------------------------------------------------------


class TestValidateRequestAcceptsValidData:
    @pytest.mark.asyncio
    async def test_valid_request_against_cached_schemas_passes(self, resolvable_cache: Path) -> None:
        async with AdCPSchemaValidator(cache_dir=resolvable_cache, offline_mode=True) as validator:
            # Must return None (no raise); ``validate_request`` has no return value.
            result = await validator.validate_request(TASK_NAME, {"child": {"name": "widget-1"}})

        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_data_against_cached_schemas_raises_validation_error(self, resolvable_cache: Path) -> None:
        """Sanity check: with schemas fully cached, real validation errors
        still surface as ``SchemaValidationError`` (not ``SchemaResolutionError``).

        This complements the negative path above — together they show the two
        failure modes are cleanly distinguished.
        """
        async with AdCPSchemaValidator(cache_dir=resolvable_cache, offline_mode=True) as validator:
            with pytest.raises(SchemaValidationError):
                # Missing required ``name`` in child, plus an extra property
                # that violates ``additionalProperties: false``.
                await validator.validate_request(TASK_NAME, {"child": {"not_name": "oops"}})
