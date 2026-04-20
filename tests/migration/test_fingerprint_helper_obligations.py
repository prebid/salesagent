"""L0-21 Red: obligations for the golden-fingerprint capture helper.

These tests are written BEFORE ``tests/migration/_fingerprint.py`` exists.
They describe the contract consumers at L1+ will rely on when porting a
Flask blueprint to FastAPI — each port's test captures the new router's
response fingerprint and compares it against the Flask baseline we
checkpoint now.

The contract:

1. ``ResponseFingerprint`` is a ``@dataclass`` with 5 fields: ``status_code``,
   ``content_type`` (media type only — no charset/boundary), ``body_sha256``
   (lowercase hex of canonical JSON for JSON bodies, or raw bytes for
   non-JSON), ``headers_of_interest`` (cache-control + any ``x-*`` header;
   Date/Server/Content-Length excluded — they drift per process), and
   ``body_schema`` (top-level JSON keys sorted for objects; ``{"__type__":
   "array", "length": N}`` for arrays; ``{"__type__": "html"}`` for HTML).

2. ``capture_fingerprint(client, method, path, **kwargs)`` hits an endpoint
   via a ``TestClient`` (in-process — no live services) and returns a
   ``ResponseFingerprint``. ``kwargs`` are forwarded to the TestClient
   request (json, params, headers, etc).

3. ``save_fingerprint(name, fp)`` writes the fingerprint to
   ``tests/migration/fingerprints/baselines/<name>.json`` with sorted keys
   and stable whitespace so the file is git-review friendly.

4. ``load_fingerprint(name)`` reads the same file back into a
   ``ResponseFingerprint`` without loss.

5. ``assert_matches(actual, expected, strictness)`` compares with three
   strictness levels:
     * ``"byte"`` — ``body_sha256`` must match exactly. Use this for
       endpoints whose body is deterministic (static config, version
       strings).
     * ``"schema"`` — top-level JSON keys match but body content may
       differ. Use when the body contains timestamps, per-request IDs,
       or database-generated surrogates.
     * ``"status_only"`` — only ``status_code`` and ``content_type`` must
       match. Use when the body is entirely dynamic (e.g. pings).
   All three levels ALSO require ``status_code`` and ``content_type``
   equality — strictness controls how deeply the body is compared.

Red state: this module imports the helper and calls each function.
The module does not exist yet → ``ModuleNotFoundError`` at collection
time, which pytest reports as an import-failure on this file. That is
the expected failing state for the Red commit.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

# This import is expected to fail at collection time in the Red commit.
from tests.migration._fingerprint import (  # noqa: E402
    ResponseFingerprint,
    assert_matches,
    capture_fingerprint,
    load_fingerprint,
    save_fingerprint,
)


class TestResponseFingerprintShape:
    """Contract: ResponseFingerprint has 5 well-named fields."""

    def test_dataclass_has_expected_fields(self):
        names = {f.name for f in dataclass_fields(ResponseFingerprint)}
        assert names == {
            "status_code",
            "content_type",
            "body_sha256",
            "headers_of_interest",
            "body_schema",
        }

    def test_instance_round_trips_through_save_and_load(self, tmp_path, monkeypatch):
        # Redirect baselines to a temp dir so we don't pollute the repo.
        from tests.migration import _fingerprint as fp_mod

        monkeypatch.setattr(fp_mod, "_BASELINES_DIR", tmp_path)
        fp = ResponseFingerprint(
            status_code=200,
            content_type="application/json",
            body_sha256="a" * 64,
            headers_of_interest={"cache-control": "no-store"},
            body_schema={"__type__": "object", "keys": ["a", "b"]},
        )
        save_fingerprint("sample", fp)
        loaded = load_fingerprint("sample")
        assert loaded == fp


class TestCaptureFingerprint:
    """Contract: capture_fingerprint hits an endpoint via TestClient."""

    def test_captures_json_response(self, boot_client):
        fp = capture_fingerprint(boot_client, "GET", "/health")
        assert fp.status_code == 200
        assert fp.content_type == "application/json"
        # /health returns {"service": "mcp", "status": "healthy"} — object body.
        assert fp.body_schema.get("__type__") == "object"
        assert sorted(fp.body_schema.get("keys", [])) == ["service", "status"]
        # body_sha256 is a lowercase hex SHA-256 string.
        assert isinstance(fp.body_sha256, str) and len(fp.body_sha256) == 64

    def test_content_type_strips_charset_parameters(self, boot_client):
        """Media type only — charset/boundary are stripped (they drift)."""
        fp = capture_fingerprint(boot_client, "GET", "/health")
        assert ";" not in fp.content_type
        assert fp.content_type.strip() == fp.content_type


class TestAssertMatches:
    """Contract: strictness knob controls body comparison depth."""

    @pytest.fixture
    def baseline(self):
        return ResponseFingerprint(
            status_code=200,
            content_type="application/json",
            body_sha256="a" * 64,
            headers_of_interest={"cache-control": "no-store"},
            body_schema={"__type__": "object", "keys": ["a", "b"]},
        )

    def test_byte_strictness_passes_on_identical(self, baseline):
        assert_matches(baseline, baseline, strictness="byte")

    def test_byte_strictness_fails_on_body_drift(self, baseline):
        drifted = ResponseFingerprint(
            status_code=baseline.status_code,
            content_type=baseline.content_type,
            body_sha256="b" * 64,  # different body
            headers_of_interest=baseline.headers_of_interest,
            body_schema=baseline.body_schema,
        )
        with pytest.raises(AssertionError):
            assert_matches(drifted, baseline, strictness="byte")

    def test_schema_strictness_tolerates_body_drift(self, baseline):
        drifted = ResponseFingerprint(
            status_code=baseline.status_code,
            content_type=baseline.content_type,
            body_sha256="b" * 64,  # different body, same keys
            headers_of_interest=baseline.headers_of_interest,
            body_schema=baseline.body_schema,
        )
        # "schema" strictness accepts body_sha256 drift as long as keys match.
        assert_matches(drifted, baseline, strictness="schema")

    def test_schema_strictness_fails_on_key_drift(self, baseline):
        drifted = ResponseFingerprint(
            status_code=baseline.status_code,
            content_type=baseline.content_type,
            body_sha256=baseline.body_sha256,
            headers_of_interest=baseline.headers_of_interest,
            body_schema={"__type__": "object", "keys": ["a", "b", "c"]},
        )
        with pytest.raises(AssertionError):
            assert_matches(drifted, baseline, strictness="schema")

    def test_status_only_tolerates_schema_drift(self, baseline):
        drifted = ResponseFingerprint(
            status_code=baseline.status_code,
            content_type=baseline.content_type,
            body_sha256="b" * 64,
            headers_of_interest=baseline.headers_of_interest,
            body_schema={"__type__": "object", "keys": ["totally", "different"]},
        )
        assert_matches(drifted, baseline, strictness="status_only")

    def test_status_only_fails_on_status_drift(self, baseline):
        drifted = ResponseFingerprint(
            status_code=500,  # different
            content_type=baseline.content_type,
            body_sha256=baseline.body_sha256,
            headers_of_interest=baseline.headers_of_interest,
            body_schema=baseline.body_schema,
        )
        with pytest.raises(AssertionError):
            assert_matches(drifted, baseline, strictness="status_only")

    def test_unknown_strictness_raises_value_error(self, baseline):
        with pytest.raises(ValueError):
            assert_matches(baseline, baseline, strictness="telepathic")


class TestBaselinesDirExists:
    """Contract: the baselines directory is a committed, importable package."""

    def test_baselines_dir_is_a_package(self):
        here = Path(__file__).parent / "fingerprints" / "baselines"
        assert here.is_dir()
        assert (here / "__init__.py").is_file()
