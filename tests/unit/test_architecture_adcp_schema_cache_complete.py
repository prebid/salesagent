"""Guard: the local AdCP schema cache must be transitively complete.

The validator at `tests/e2e/adcp_schema_validator.py` downloads AdCP schemas from
adcontextprotocol.org and caches them locally under `schemas/{version}/`. When a
schema references a `$ref` whose target is not in the cache, validation silently
degrades to a strict empty-object stub — `{"type": "object", "additionalProperties":
False, "properties": {}}` — which produces spurious errors at unrelated JSON paths.

This guard walks `schemas/<version>/index.json` + every referenced schema body
(BFS) and asserts every `$ref` to `/schemas/<version>/...` resolves to an
existing cache file. The version comes from `schemas/.current-version` (written
by `make schemas-refresh`); it falls back to "latest" if the pointer is missing,
which is the same default the refresh CLI uses.

Fix: run `make schemas-refresh` to populate the cache from upstream.

No allowlist — the cache is all-or-nothing. A partial cache is a stale cache.
"""

import asyncio
import json
from pathlib import Path

import pytest

from tests.e2e.adcp_schema_validator import cache_filename, collect_refs, walk_transitive_refs

ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = ROOT / "schemas"
VERSION_POINTER = CACHE_ROOT / ".current-version"
DEFAULT_VERSION = "latest"


def _resolve_cache_version() -> str:
    if VERSION_POINTER.exists():
        return VERSION_POINTER.read_text().strip() or DEFAULT_VERSION
    return DEFAULT_VERSION


class TestAdCPSchemaCacheComplete:
    """The AdCP schema cache must contain the transitive closure of every ref."""

    def test_cache_contains_all_transitive_refs(self):
        """BFS through index.json + schema bodies; every local $ref must resolve."""
        version = _resolve_cache_version()
        cache_dir = CACHE_ROOT / version
        index_file = cache_dir / "index.json"
        local_ref_prefix = f"/schemas/{version}/"

        if not index_file.exists():
            pytest.fail(
                f"AdCP schema cache not populated: {index_file} does not exist.\n\n"
                f"Fix by running:\n"
                f"  make schemas-refresh\n"
            )

        try:
            index = json.loads(index_file.read_text())
        except json.JSONDecodeError as e:
            pytest.fail(
                f"AdCP schema cache is corrupt: {index_file} is not valid JSON ({e}).\n\n"
                f"Fix by running:\n"
                f"  make schemas-refresh\n"
            )

        initial: set[str] = set()
        collect_refs(index, initial)

        missing: list[str] = []
        malformed: list[str] = []

        async def _load_from_cache(ref: str) -> dict:
            """Return the cached schema body, recording any failure without raising.

            `walk_transitive_refs` expects an async fetch that yields the body so the
            BFS can keep walking. Returning `{}` on failure keeps the closure
            boundary clean — every problem is captured in `missing` / `malformed`
            and surfaced below as a single error message.
            """
            cache_path = cache_dir / cache_filename(ref)
            if not cache_path.exists():
                missing.append(ref)
                return {}
            try:
                return json.loads(cache_path.read_text())
            except json.JSONDecodeError:
                malformed.append(ref)
                return {}

        seen, _ = asyncio.run(walk_transitive_refs(initial, _load_from_cache, local_ref_prefix))

        # Empty index → the BFS would silently pass every assertion below
        # by vacuous truth. Fail loudly instead.
        assert seen, (
            f"BFS found zero local refs in {index_file}; the schema index is empty or malformed. "
            f"Fix by running:\n  make schemas-refresh\n"
        )

        problems: list[str] = []
        if missing:
            problems.append(
                f"Missing {len(missing)} cache file(s) for refs declared in the closure. First 5: {sorted(missing)[:5]}"
            )
        if malformed:
            problems.append(f"Malformed cache file(s) for {len(malformed)} ref(s). First 5: {sorted(malformed)[:5]}")

        assert not problems, (
            f"AdCP schema cache at schemas/{version}/ is incomplete or corrupt:\n\n  "
            + "\n  ".join(problems)
            + "\n\nFix by running:\n  make schemas-refresh\n"
        )
