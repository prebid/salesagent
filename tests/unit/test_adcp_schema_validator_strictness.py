"""Unit coverage for AdCPSchemaValidator's strict-mode behavior.

Strict mode (default `strict=True`) makes the validator raise on silent-failure
paths it used to swallow with `print("Warning:")`. Verifies:

- Default constructor uses strict=True
- Missing cache file → SchemaResolutionError (offline-mode)
- Corrupt cache file → SchemaResolutionError (offline-mode)
- strict=False preserves the empty-object stub fallback
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


class TestStrictModeDefaults:
    def test_strict_defaults_to_true(self, empty_cache_dir: Path):
        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True)
        assert v.strict is True

    def test_strict_false_can_be_opted_in(self, empty_cache_dir: Path):
        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True, strict=False)
        assert v.strict is False


class TestSchemaResolutionErrorShape:
    def test_is_schema_error_subclass(self):
        assert issubclass(SchemaResolutionError, SchemaError)

    def test_carries_url(self):
        err = SchemaResolutionError("/schemas/latest/foo/bar.json")
        assert err.url == "/schemas/latest/foo/bar.json"
        assert "/schemas/latest/foo/bar.json" in str(err)


class TestUnresolvableRefStrictMode:
    def test_strict_raises_on_missing_cache_file(self, empty_cache_dir: Path):
        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True, strict=True)
        with pytest.raises(SchemaResolutionError) as exc_info:
            v._resolve_adcp_schema_ref("/schemas/latest/missing/foo.json")
        assert exc_info.value.url == "/schemas/latest/missing/foo.json"

    def test_lenient_returns_empty_stub_on_missing_cache_file(self, empty_cache_dir: Path):
        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True, strict=False)
        result = v._resolve_adcp_schema_ref("/schemas/latest/missing/foo.json")
        assert result == {"type": "object", "additionalProperties": False, "properties": {}}

    def test_strict_raises_on_corrupt_cache_file(self, empty_cache_dir: Path):
        ref = "/schemas/latest/corrupt/bar.json"
        cached_path = empty_cache_dir / (ref.replace("/", "_").replace(".", "_") + ".json")
        cached_path.write_text("not valid json {{{")

        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True, strict=True)
        with pytest.raises(SchemaResolutionError) as exc_info:
            v._resolve_adcp_schema_ref(ref)
        assert exc_info.value.url == ref

    def test_lenient_falls_back_on_corrupt_cache_file(self, empty_cache_dir: Path):
        ref = "/schemas/latest/corrupt/bar.json"
        cached_path = empty_cache_dir / (ref.replace("/", "_").replace(".", "_") + ".json")
        cached_path.write_text("not valid json {{{")

        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True, strict=False)
        result = v._resolve_adcp_schema_ref(ref)
        assert result == {"type": "object", "additionalProperties": False, "properties": {}}


class TestHttpResolutionStrictMode:
    def test_strict_raises_for_unknown_http_host(self, empty_cache_dir: Path):
        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True, strict=True)
        with pytest.raises(SchemaResolutionError):
            v._resolve_http_schema_ref("https://example.invalid/schemas/foo.json")

    def test_lenient_returns_stub_for_unknown_http_host(self, empty_cache_dir: Path):
        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True, strict=False)
        result = v._resolve_http_schema_ref("https://example.invalid/schemas/foo.json")
        assert result == {"type": "object", "additionalProperties": False, "properties": {}}

    def test_adcontextprotocol_url_delegates_to_adcp_resolver(self, empty_cache_dir: Path):
        v = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True, strict=True)
        # Delegation routes to the AdCP resolver, which then raises strict.
        with pytest.raises(SchemaResolutionError) as exc_info:
            v._resolve_http_schema_ref("https://adcontextprotocol.org/schemas/latest/missing.json")
        assert exc_info.value.url == "/schemas/latest/missing.json"


class TestResolverCacheHitWorks:
    """Sanity check: a valid cached schema is returned, regardless of strict."""

    def test_cached_schema_is_returned(self, empty_cache_dir: Path):
        ref = "/schemas/latest/example/widget.json"
        cached_path = empty_cache_dir / (ref.replace("/", "_").replace(".", "_") + ".json")
        cached_path.write_text(json.dumps({"type": "object", "properties": {"id": {"type": "string"}}}))

        v_strict = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True, strict=True)
        assert v_strict._resolve_adcp_schema_ref(ref) == {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        }

        v_lenient = AdCPSchemaValidator(cache_dir=empty_cache_dir, offline_mode=True, strict=False)
        assert v_lenient._resolve_adcp_schema_ref(ref) == {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        }
