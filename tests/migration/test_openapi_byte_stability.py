"""L0-02 guard: the ``/openapi.json`` schema must not drift silently.

The OpenAPI document is the authoritative description of every REST
contract we expose. An accidental route rename, response-model change,
or path-parameter rewrite all show up as a byte-level drift here.

At L0 the fixture captures the current hash — the test passes. When
OpenAPI genuinely changes (a feature PR adds an endpoint, intentionally),
the fixture is updated in the SAME commit, with the new SHA-256 in the
commit message for audit.

See ``.claude/notes/flask-to-fastapi/L0-implementation-plan.md`` §L0-02
and foundation-modules.md §5.5 row #24.

Hashing strategy: JSON is re-serialized via ``canonical_json_bytes``
(sorted keys, compact separators) BEFORE hashing so the result survives
both the production JSONResponse and any cross-version Python JSON
formatting tweaks. A pre-existing ``/a2a/`` ``operationId`` collision
(see ``_fixtures.normalize_openapi_for_hashing``) is scrubbed; this
normalization is a narrow workaround tracked in that helper's docstring.
"""

from __future__ import annotations

from pathlib import Path

from tests.migration._fixtures import canonical_json_bytes, normalize_openapi_for_hashing, sha256_hex

FIXTURE = Path(__file__).parent / "fixtures" / "openapi-byte-hash.sha256"


def _read_expected_hash() -> str:
    return FIXTURE.read_text(encoding="utf-8").strip()


def _observed_hash(boot_client) -> str:
    response = boot_client.get("/openapi.json")
    assert response.status_code == 200, f"expected 200 for /openapi.json, got {response.status_code}"
    schema = response.json()
    normalized = normalize_openapi_for_hashing(schema)
    return sha256_hex(canonical_json_bytes(normalized))


class TestOpenapiByteStability:
    """OpenAPI schema content is frozen against an explicit checked-in hash."""

    def test_openapi_matches_baseline_hash(self, boot_client):
        observed = _observed_hash(boot_client)
        expected = _read_expected_hash()

        assert observed == expected, (
            "OpenAPI schema content changed.\n"
            f"  expected sha256: {expected}\n"
            f"  observed sha256: {observed}\n"
            f"If this change is intentional, update {FIXTURE.name} with the new hash "
            "in the same commit and include the old/new hash in the commit message."
        )

    def test_planted_drift_is_detected(self, boot_client):
        """Meta-test: a mutated schema must fail the hash comparison."""
        response = boot_client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        normalized = normalize_openapi_for_hashing(schema)

        mutated = dict(normalized)
        mutated["info"] = {**mutated.get("info", {}), "title": "mutated-title-for-meta-test"}
        assert canonical_json_bytes(mutated) != canonical_json_bytes(
            normalized
        ), "planted mutation did not alter the canonical bytes — meta-test setup is wrong"

        observed = sha256_hex(canonical_json_bytes(mutated))
        expected = _read_expected_hash()
        assert observed != expected, "planted mutation was NOT detected — SHA-256 comparison is broken"
