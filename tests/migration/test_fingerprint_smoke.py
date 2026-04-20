"""L0-21 consumer smoke test + planted-violation meta-test.

Proves the golden-fingerprint helper works end-to-end without
depending on L1+ port code that has not been written yet:

* ``test_round_trip_self_compare`` — captures a fingerprint of
  ``/health`` via the booted TestClient, saves it to a temp dir,
  loads it back, and asserts ``assert_matches`` accepts the
  self-comparison at all three strictness levels.

* ``test_planted_drift_is_detected`` — mutates each field of a
  captured fingerprint in turn and asserts ``assert_matches`` fires
  at the appropriate strictness level. This is the /write-guard
  meta-test pattern adapted to the fingerprint helper — it proves
  the detection logic actually rejects drift (not just accepts
  matches).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.migration._fingerprint import (
    assert_matches,
    capture_fingerprint,
    load_fingerprint,
    save_fingerprint,
)


@pytest.mark.integration
class TestFingerprintHelperEndToEnd:
    """Round-trip + planted-violation contract."""

    def test_round_trip_self_compare(self, boot_client, tmp_path, monkeypatch):
        """capture → save → load → assert_matches (byte/schema/status_only) all pass."""
        from tests.migration import _fingerprint as fp_mod

        monkeypatch.setattr(fp_mod, "_BASELINES_DIR", tmp_path)

        captured = capture_fingerprint(boot_client, "GET", "/health")
        save_fingerprint("smoke_health", captured)
        loaded = load_fingerprint("smoke_health")

        assert loaded == captured, "round-trip through JSON changed the fingerprint"
        for strictness in ("byte", "schema", "status_only"):
            assert_matches(captured, loaded, strictness=strictness)

    def test_planted_drift_is_detected(self, boot_client):
        """Mutating each field provokes an AssertionError at the right strictness.

        This is the /write-guard meta-test pattern — proves the helper
        actually catches drift, not just accepts a self-comparison.
        """
        baseline = capture_fingerprint(boot_client, "GET", "/health")

        # status_code drift — caught at ALL strictness levels.
        with pytest.raises(AssertionError):
            assert_matches(replace(baseline, status_code=500), baseline, strictness="byte")
        with pytest.raises(AssertionError):
            assert_matches(replace(baseline, status_code=500), baseline, strictness="schema")
        with pytest.raises(AssertionError):
            assert_matches(replace(baseline, status_code=500), baseline, strictness="status_only")

        # content_type drift — caught at ALL strictness levels.
        with pytest.raises(AssertionError):
            assert_matches(replace(baseline, content_type="text/plain"), baseline, strictness="byte")
        with pytest.raises(AssertionError):
            assert_matches(replace(baseline, content_type="text/plain"), baseline, strictness="status_only")

        # body_schema drift — caught at byte + schema, NOT at status_only.
        mutated_schema = replace(
            baseline,
            body_schema={"__type__": "object", "keys": ["injected"]},
        )
        with pytest.raises(AssertionError):
            assert_matches(mutated_schema, baseline, strictness="byte")
        with pytest.raises(AssertionError):
            assert_matches(mutated_schema, baseline, strictness="schema")
        # status_only tolerates schema drift by design.
        assert_matches(mutated_schema, baseline, strictness="status_only")

        # body_sha256 drift — caught ONLY at byte strictness.
        mutated_hash = replace(baseline, body_sha256="f" * 64)
        with pytest.raises(AssertionError):
            assert_matches(mutated_hash, baseline, strictness="byte")
        # schema + status_only tolerate body byte drift by design.
        assert_matches(mutated_hash, baseline, strictness="schema")
        assert_matches(mutated_hash, baseline, strictness="status_only")

    def test_unknown_strictness_rejected(self, boot_client):
        """Passing an unknown strictness raises ValueError, not AssertionError."""
        baseline = capture_fingerprint(boot_client, "GET", "/health")
        with pytest.raises(ValueError):
            assert_matches(baseline, baseline, strictness="telepathic")
