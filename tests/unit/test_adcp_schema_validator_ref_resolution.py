"""Unit coverage for AdCPSchemaValidator's reference-resolution error contract.

Validation is unconditionally strict: unresolvable `$ref` targets surface as
`SchemaResolutionError` rather than silently falling back to an empty-object
stub (which historically masked real AdCP drift as spurious
`additionalProperties` errors at unrelated JSON paths).

Verifies:
- Missing cache file → SchemaResolutionError with the ref URL attached
- Corrupt (unparseable) cache file → SchemaResolutionError
- HTTP ref to an unknown host → SchemaResolutionError
- adcontextprotocol.org HTTP refs delegate to the AdCP resolver
- Valid cache hit returns the cached schema unchanged
- SchemaResolutionError is a SchemaError subclass exposing the failed URL
"""

import json
from pathlib import Path

import pytest

from tests.e2e.adcp_schema_validator import (
    AdCPSchemaValidator,
    SchemaError,
    SchemaResolutionError,
)


@pytest.fixture
def empty_cache_dir(tmp_path: Path) -> Path:
    cache = tmp_path / "schemas-test"
    cache.mkdir()
    return cache


class TestSchemaResolutionErrorShape:
    def test_is_schema_error_subclass(self):
        assert issubclass(SchemaResolutionError, SchemaError)

    def test_carries_url(self):
        err = SchemaResolutionError("/schemas/latest/foo/bar.json")
        assert err.url == "/schemas/latest/foo/bar.json"
        assert "/schemas/latest/foo/bar.json" in str(err)


class TestUnresolvableRef:
    def test_raises_on_missing_cache_file(self, empty_cache_dir: Path):
        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True)
        with pytest.raises(SchemaResolutionError) as exc_info:
            v._resolve_adcp_schema_ref("/schemas/latest/missing/foo.json")
        assert exc_info.value.url == "/schemas/latest/missing/foo.json"
        # Error message must point the developer at the remediation.
        assert "make schemas-refresh" in str(exc_info.value)

    def test_raises_on_corrupt_cache_file(self, empty_cache_dir: Path):
        ref = "/schemas/latest/corrupt/bar.json"
        cached_path = empty_cache_dir / (ref.replace("/", "_").replace(".", "_") + ".json")
        cached_path.write_text("not valid json {{{")

        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True)
        with pytest.raises(SchemaResolutionError) as exc_info:
            v._resolve_adcp_schema_ref(ref)
        assert exc_info.value.url == ref
        assert "make schemas-refresh" in str(exc_info.value)


class TestHttpResolution:
    def test_raises_for_unknown_http_host(self, empty_cache_dir: Path):
        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True)
        with pytest.raises(SchemaResolutionError):
            v._resolve_http_schema_ref("https://example.invalid/schemas/foo.json")

    def test_adcontextprotocol_url_delegates_to_adcp_resolver(self, empty_cache_dir: Path):
        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True)
        # Delegation routes through the AdCP resolver, which raises on a cache miss.
        with pytest.raises(SchemaResolutionError) as exc_info:
            v._resolve_http_schema_ref("https://adcontextprotocol.org/schemas/latest/missing.json")
        assert exc_info.value.url == "/schemas/latest/missing.json"


class TestResolverCacheHit:
    """A valid cached schema is returned unchanged."""

    def test_cached_schema_is_returned(self, empty_cache_dir: Path):
        ref = "/schemas/latest/example/widget.json"
        cached_path = empty_cache_dir / (ref.replace("/", "_").replace(".", "_") + ".json")
        cached_path.write_text(json.dumps({"type": "object", "properties": {"id": {"type": "string"}}}))

        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True)
        assert v._resolve_adcp_schema_ref(ref) == {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        }
