#!/usr/bin/env python3
"""Populate schemas/{version}/ with the transitive closure of AdCP schemas.

Downloads the schema index + every referenced schema body from
adcontextprotocol.org, using the validator's existing ETag-aware caching
(see tests/e2e/adcp_schema_validator.py). Intended to be run on fresh
clones, after pinned-adcp-version bumps, or whenever the cache-completeness
guard (tests/unit/test_architecture_adcp_schema_cache_complete.py) fails.

Default version is `"latest"` — the floating upstream alias — which is what
the validator's BASE_SCHEMA_URL/INDEX_URL constants currently point at.
Pin to a specific semver with `--version <x.y.z>`, but note that upstream
index.json contents only match our BFS filter when the version prefix
aligns with the validator's BASE_SCHEMA_URL; true per-version pinning
requires parameterizing those constants (tracked as a follow-up).

Usage:
    uv run python scripts/ops/refresh_adcp_schemas.py
    uv run python scripts/ops/refresh_adcp_schemas.py --version latest

Wired into `make schemas-refresh`.
"""

import argparse
import asyncio
import functools
import logging
import sys
from pathlib import Path

# Allow `from tests.e2e.adcp_schema_validator import ...` when invoked
# directly via `uv run python scripts/ops/refresh_adcp_schemas.py`.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.e2e.adcp_schema_validator import AdCPSchemaValidator, collect_refs, walk_transitive_refs  # noqa: E402

DEFAULT_VERSION = "latest"

logger = logging.getLogger("refresh_adcp_schemas")


async def _refresh(version: str) -> None:
    """Download the transitive closure of AdCP schemas into schemas/{version}/.

    Walks index.json + every referenced body ourselves rather than relying on
    the deprecated preload_schemas() helper (which hardcoded 4 buckets out of
    16). Raises SystemExit if the BFS fetches zero schemas — that always
    indicates a prefix mismatch between the fetched index and the validator's
    BASE_SCHEMA_URL, never a legitimate state.
    """
    logger.info("Refreshing schemas into schemas/%s/", version)
    local_prefix = f"/schemas/{version}/"

    async with AdCPSchemaValidator(adcp_version=version) as validator:
        # The refresh CLI must fail loudly on upstream errors. Passing
        # allow_stale_cache=False prevents the downloader's built-in
        # flake tolerance (appropriate for e2e tests) from silently
        # returning the stale on-disk cache and reporting success while
        # the cache is weeks out of date.
        index = await validator.get_schema_index(allow_stale_cache=False)

        initial: set[str] = set()
        collect_refs(index, initial)

        fetch = functools.partial(validator.get_schema, allow_stale_cache=False)
        seen, _ = await walk_transitive_refs(initial, fetch, local_prefix)

    fetched = len(seen)
    cache_dir = PROJECT_ROOT / "schemas" / version
    pointer = PROJECT_ROOT / "schemas" / ".current-version"

    if fetched == 0:
        raise SystemExit(
            f"Fetched 0 schemas for version {version!r}. The upstream index at "
            f"{validator.INDEX_URL} returned refs that don't match the expected "
            f"prefix {local_prefix!r}. This means the validator's BASE_SCHEMA_URL "
            f"and the --version argument disagree. Rerun with --version latest "
            f"(matches the validator default) or update BASE_SCHEMA_URL to align."
        )

    pointer.write_text(version + "\n")
    logger.info("Fetched %d schemas; cache at %s; pointer -> %s", fetched, cache_dir, pointer)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the local AdCP schema cache at schemas/{version}/.",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        choices=["latest"],
        help=f"Schema version to fetch (default: {DEFAULT_VERSION!r}). Only 'latest' is "
        "accepted today — the validator's BASE_SCHEMA_URL is hardcoded to /schemas/latest/. "
        "Per-version pinning is tracked as a follow-up.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    asyncio.run(_refresh(args.version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
