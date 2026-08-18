"""
Standalone test for AdCP schema validation functionality.

This test validates that our schema validation system works correctly
without needing a running server, by testing the validation logic directly.
"""

from pathlib import Path

import pytest

from tests.helpers.adcp_schema_validator import AdCPSchemaValidator, SchemaError, SchemaValidationError


@pytest.mark.asyncio
async def test_schema_validator_initialization():
    """Test that the schema validator initializes and loads the pinned schema index."""
    async with AdCPSchemaValidator() as validator:
        # Test that we can get the schema index
        index = await validator.get_schema_index()
        assert isinstance(index, dict)
        assert "schemas" in index
        assert "media-buy" in index["schemas"]

        # Test that we can find task schemas
        schema_ref = await validator._find_schema_ref_for_task("get-products", "response")
        assert schema_ref is not None
        assert "get-products-response" in schema_ref


# test_valid_get_products_response removed:
# Validated a hardcoded response dict against adcontextprotocol.org/schemas/latest/...
# Did not exercise any sales agent behavior — purely fixture vs. upstream spec drift.
# Real schema conformance is covered by tests/unit/test_adcp_contract.py against the
# pinned adcp library version. Removed rather than skipped to satisfy the smoke-test
# TestNoSkippedTests guard.

# tests/e2e/debug_validation.py removed (#1838 review):
# A manual, non-pytest-collected debug script (run via `uv run python
# tests/e2e/debug_validation.py`) that hand-rolled an if/elif dispatch on
# get-products required-field names to build a synthetic minimal product --
# the same hand-maintained-second-copy disease PR #1868 exists to remove.
# Its debugging purpose (what does the pinned schema require, does a given
# payload validate) is already covered here (test_invalid_get_products_response,
# test_get_products_request_validation below) and by
# tests/unit/test_pydantic_schema_alignment.py's generate_minimal_valid_request /
# generate_example_value (the schema-derived generator the script should have
# reused instead of hand-rolling) and tests/unit/test_adcp_contract.py (the
# production-model contract check). Had zero external references (no CI job,
# Makefile target, or doc link) — deleted rather than repaired.


@pytest.mark.asyncio
async def test_invalid_get_products_response():
    """Test validation of an invalid get-products response."""
    async with AdCPSchemaValidator() as validator:
        # Create an invalid response (missing required 'products' field)
        invalid_response = {
            "message": "Here are some products",
            "context_id": "test-context",
            # Missing required 'products' field
        }

        # This should raise a SchemaValidationError
        with pytest.raises(SchemaValidationError) as exc_info:
            await validator.validate_response("get-products", invalid_response)

        error = exc_info.value
        assert "products" in str(error).lower()
        assert len(error.validation_errors) > 0


@pytest.mark.asyncio
async def test_get_products_request_validation():
    """Test validation of get-products request parameters.

    Per AdCP spec, buying_mode is required. When buying_mode is 'brief',
    the brief field is also required. When 'wholesale', brief must not be provided.
    """
    async with AdCPSchemaValidator() as validator:
        # Brief mode with brief text
        brief_request = {"buying_mode": "brief", "brief": "Looking for display advertising"}
        await validator.validate_request("get-products", brief_request)

        # Wholesale mode (no brief)
        wholesale_request = {"buying_mode": "wholesale"}
        await validator.validate_request("get-products", wholesale_request)

        # Brief mode with brand
        full_request = {
            "buying_mode": "brief",
            "brief": "Looking for display advertising",
            "brand": {"domain": "testbrand.com"},
        }
        await validator.validate_request("get-products", full_request)

        # Wholesale mode with brand
        url_request = {
            "buying_mode": "wholesale",
            "brand": {"domain": "testbrand.com"},
        }
        await validator.validate_request("get-products", url_request)


@pytest.mark.asyncio
async def test_pinned_sdk_schema_source(monkeypatch):
    """The validator grades the PINNED spec, from the installed SDK, offline.

    Replaces the former test_offline_mode, which was xfailed non-strict for
    #1308 ("/schemas/latest/ evolves faster than the adcp library"). Pinning
    resolves #1308's root cause: the validator now loads schemas bundled with
    the pinned adcp SDK, so live-registry drift cannot change what this suite
    grades, and this minimal spec-valid payload must validate deterministically.
    """
    import socket

    import adcp

    from tests.unit.test_adcp_spec_version import EXPECTED_SPEC_VERSION

    # Minimal payload the PINNED 3.1.1 response schema accepts: cache_scope is
    # required on the standard (else) branch since AdCP 3.1, and the top-level
    # protocol status ("completed" TaskStatus) is required via the response
    # envelope (allOf.1.required in get-products-response.json).
    payload = {"products": [], "cache_scope": "public", "status": "completed"}

    # "Offline" is graded, not claimed: any socket creation from here on fails.
    def _no_network(*args, **kwargs):
        raise AssertionError("schema validation attempted network access")

    monkeypatch.setattr(socket, "socket", _no_network)

    async with AdCPSchemaValidator() as validator:
        # The schema source is the SDK's _schemas/<major.minor> tree — asserted
        # on the validator's OWN root (not recomputed here), so a wrong tree out
        # of pinned_schema.schema_root() fails this test.
        assert validator.schema_root.parent.name == "_schemas"
        assert validator.schema_root.parent.parent == Path(adcp.__file__).parent, (
            f"validator loads schemas from {validator.schema_root}, expected the adcp SDK's _schemas tree"
        )
        # The loaded index is the repo's pinned spec version. EXPECTED_SPEC_VERSION
        # is the pin-drift guard's constant (tests/unit/test_adcp_spec_version.py);
        # comparing against it — not just the SDK's self-report — pins the version
        # this suite grades even if the SDK pin shifts without the guard updating.
        index = await validator.get_schema_index()
        assert index["adcp_version"] == EXPECTED_SPEC_VERSION == adcp.get_adcp_spec_version()

        await validator.validate_response("get-products", payload)


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        # Version-root-relative, the form the SDK index uses — the ONLY form.
        ("media-buy/get-products-request.json", "media-buy/get-products-request.json"),
        # A #fragment is stripped; the file part still has to be the one form.
        ("core/format-id.json#/definitions/x", "core/format-id.json"),
    ],
)
def test_normalize_ref_accepted_forms(ref, expected):
    """The one accepted ref form resolves against the pinned version root."""
    validator = AdCPSchemaValidator()
    assert validator._normalize_ref(ref) == expected


@pytest.mark.parametrize(
    "ref",
    [
        # The absolute-URL form upstream adcp#6133 introduced — the outage's trigger.
        # Silently rewriting it onto the pin hid that "latest" was never honoured.
        "https://adcontextprotocol.org/schemas/latest/core/version-envelope.json",
        # Site-rooted, version-bearing: the version segment used to be discarded,
        # so a ref naming ANY version quietly graded against the pin instead.
        "/schemas/v1/media-buy/get-products-request.json",
        "/schemas/3.1.1/core/format-id.json",
        "/schemas/",  # nothing after the prefix
        "/other/x.json",  # site-rooted but not under /schemas/
        "../x.json",  # traversal out of the version root
        "https://evil.example.com/x.json",  # foreign host
        "",  # empty
    ],
)
def test_normalize_ref_rejected_forms(ref):
    """Anything but the one form raises instead of being rewritten or probed."""
    validator = AdCPSchemaValidator()
    with pytest.raises(SchemaError):
        validator._normalize_ref(ref)


@pytest.mark.asyncio
async def test_resolution_failure_is_not_a_validation_error():
    """A schema-RESOLUTION failure propagates as SchemaError, unwrapped.

    Callers branch on SchemaValidationError to mean "the payload violates the
    contract" — a missing/unresolvable schema must not be conflated with that
    (the ``except SchemaError: raise`` arm in ``_validate_against_schema``).
    """
    validator = AdCPSchemaValidator()
    with pytest.raises(SchemaError) as exc_info:
        await validator._validate_against_schema("media-buy/does-not-exist.json", {}, "resolution failure")
    assert not isinstance(exc_info.value, SchemaValidationError)


@pytest.mark.asyncio
async def test_schema_path_rejects_embedded_traversal():
    """A ref that normalizes clean but traverses mid-path is contained.

    '_normalize_ref' only rejects '..' prefixes; the containment check in
    'tests.helpers.pinned_schema._resolve_filename' must stop
    'media-buy/../../../../etc/hosts' before any filesystem read.
    """
    validator = AdCPSchemaValidator()
    with pytest.raises(SchemaError, match="escapes the pinned SDK schema tree"):
        await validator.get_schema("media-buy/" + "../" * 8 + "etc/hosts")


@pytest.mark.asyncio
async def test_schema_caching():
    """Test that schemas are properly cached for performance."""
    async with AdCPSchemaValidator() as validator:
        # First call should download the schema
        schema_ref = await validator._find_schema_ref_for_task("get-products", "response")
        schema1 = await validator.get_schema(schema_ref)

        # Second call should use cached version
        schema2 = await validator.get_schema(schema_ref)

        # Should be the same object (cached)
        assert schema1 is schema2

        # Check that compiled validators are also cached
        validator1 = validator._get_compiled_validator(schema_ref)
        validator2 = validator._get_compiled_validator(schema_ref)
        assert validator1 is validator2


@pytest.mark.asyncio
async def test_task_name_mapping():
    """Test that different task name formats are handled correctly."""
    async with AdCPSchemaValidator() as validator:
        # Test hyphen format (schema format)
        schema_ref1 = await validator._find_schema_ref_for_task("get-products", "response")

        # Test underscore format (should be converted)
        # Note: this tests the logic in the test client that converts underscore to hyphen
        assert schema_ref1 is not None
        assert "get-products" in schema_ref1


@pytest.mark.asyncio
async def test_find_schema_ref_searches_every_index_section():
    """The resolver must search every task-bearing section of the pinned index, not just media-buy/signals.

    #1843: _find_schema_ref_for_task only checked the media-buy and
    signals sections. The pinned 3.1.1 index carries tasks in 10 sections;
    sync-creatives and list-creatives live under "creative", get-task-status
    lives under "protocol" — none of them resolvable before this fix, so any
    validate_request/validate_response call for them silently no-op'd.
    """
    async with AdCPSchemaValidator() as validator:
        index = await validator.get_schema_index()
        unresolved = []
        for section_name, section in index.get("schemas", {}).items():
            for task_name, task_info in section.get("tasks", {}).items():
                for request_or_response in ("request", "response"):
                    if request_or_response not in task_info:
                        continue
                    resolved = await validator._find_schema_ref_for_task(task_name, request_or_response)
                    if resolved is None:
                        unresolved.append(f"{section_name}/{task_name}/{request_or_response}")

        assert not unresolved, f"tasks unreachable by the resolver: {unresolved}"


def _iter_index_schema_refs(node: object) -> list[str]:
    """Recursively collect every ``$ref`` string under an index ``"schemas"`` subtree.

    The 16 top-level sections do not share one shape: task-bearing sections
    (account, media-buy, ...) carry refs under ``tasks[*].request/response``;
    non-task sections (core, enums, pricing-options, extensions) carry them
    directly under their own ``schemas`` key; several sections additionally
    carry a ``supporting-schemas`` map; ``extensions`` carries a ``registry``
    map; ``trusted-match`` carries ``operations`` instead of ``tasks``;
    ``adagents``/``brand`` carry a single section-level ``$ref``. A walk that
    hardcodes any subset of these container names (``tasks``,
    ``supporting-schemas``, ...) silently misses refs under a name it didn't
    anticipate — including a future one the next spec bump adds. Walking
    every ``$ref`` regardless of its container name is the only way to
    actually cover "every schema ref the pinned index names" (#1868 review).
    """
    refs: list[str] = []
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            refs.append(ref)
        for key, value in node.items():
            if key == "$ref":
                continue
            refs.extend(_iter_index_schema_refs(value))
    elif isinstance(node, list):
        for item in node:
            refs.extend(_iter_index_schema_refs(item))
    return refs


@pytest.mark.asyncio
async def test_every_indexed_schema_ref_loads():
    """Every schema ref the pinned index names must actually load, not just resolve by name.

    Regression for the validator previously serving only the SDK's bundled/
    subtree, which physically ships 8 of the SDK's 16 top-level categories
    (no account/, enums/, governance/, etc.). The index resolves task names
    to refs in ALL categories (see test_find_schema_ref_searches_every_index_section
    above), so a category outside bundled/ (e.g. account/list-accounts-response.json)
    resolved by name but then raised "not found" the moment get_schema() tried
    to actually load it — a gap only a real load exercises, not a name lookup.

    Walks every ``$ref`` under the index's ``"schemas"`` subtree via
    ``_iter_index_schema_refs`` (all 16 sections, every container shape —
    ``tasks``, ``schemas``, ``supporting-schemas``, ``registry``,
    ``operations``, section-level ``$ref``), not just ``tasks[*].request/
    response`` (previously 124 of 434 distinct refs, 29%).
    """
    async with AdCPSchemaValidator() as validator:
        index = await validator.get_schema_index()
        refs = sorted(set(_iter_index_schema_refs(index.get("schemas", {}))))

        failed = []
        for ref in refs:
            try:
                await validator.get_schema(ref)
            except SchemaError as e:
                failed.append(f"{ref}: {e}")

        assert not failed, f"indexed schema refs that failed to load: {failed}"


@pytest.mark.asyncio
async def test_unresolvable_task_name_raises_instead_of_silently_skipping():
    """An unresolvable task name must raise, not warn-and-return.

    #1843: validate_request/validate_response printed a warning and
    returned on a resolver miss, so the caller observed success having graded
    nothing — a quiet failure (CLAUDE.md "No Quiet Failures").
    """
    async with AdCPSchemaValidator() as validator:
        with pytest.raises(SchemaError):
            await validator.validate_request("this-task-does-not-exist", {})

        with pytest.raises(SchemaError):
            await validator.validate_response("this-task-does-not-exist", {})


if __name__ == "__main__":
    import asyncio

    async def run_tests():
        """Run tests manually for debugging."""
        print("Testing schema validator initialization...")
        await test_schema_validator_initialization()
        print("✓ Initialization test passed")

        print("Testing invalid response validation...")
        await test_invalid_get_products_response()
        print("✓ Invalid response test passed")

        print("Testing request validation...")
        await test_get_products_request_validation()
        print("✓ Request validation test passed")

        print("Testing schema caching...")
        await test_schema_caching()
        print("✓ Schema caching test passed")

        print("All tests passed!")

    asyncio.run(run_tests())
