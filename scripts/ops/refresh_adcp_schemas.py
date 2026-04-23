#!/usr/bin/env python3
"""Populate schemas/{version}/ with the transitive closure of AdCP schemas.

Downloads the schema index + every referenced schema body from
adcontextprotocol.org, using the validator's existing ETag-aware caching
(see tests/e2e/adcp_schema_validator.py). Intended to be run on fresh
clones, after pinned-adcp-version bumps, or whenever the cache-completeness
guard (tests/unit/test_architecture_adcp_schema_cache_complete.py) fails.

Version resolution:
  1. `--version <explicit>` overrides everything.
  2. Default: call `adcp.get_adcp_version()` (the pinned Python-types
     version). If the upstream URL for that version responds 200, use it.
     This keeps the JSON schema set aligned with the pinned Python types.
  3. Fallback: if the pinned-version URL 404s, log a warning and use
     "latest" — the floating upstream alias. Disable with
     `--strict-version` to fail instead.

Usage:
    uv run python scripts/ops/refresh_adcp_schemas.py
    uv run python scripts/ops/refresh_adcp_schemas.py --version latest
    uv run python scripts/ops/refresh_adcp_schemas.py --strict-version

Wired into `make schemas-refresh`.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

# Allow `from tests.e2e.adcp_schema_validator import ...` when invoked
# directly via `uv run python scripts/ops/refresh_adcp_schemas.py`.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.e2e.adcp_schema_validator import AdCPSchemaValidator, collect_refs  # noqa: E402

UPSTREAM_BASE = "https://adcontextprotocol.org/schemas"
FALLBACK_VERSION = "latest"

logger = logging.getLogger("refresh_adcp_schemas")


def _resolve_pinned_version() -> str | None:
    """Return the version string the pinned `adcp` package declares, or None."""
    try:
        from adcp import get_adcp_version
    except ImportError:
        return None
    try:
        return get_adcp_version()
    except (AttributeError, TypeError) as e:
        # The pinned adcp package shipped without a callable get_adcp_version.
        # Surface as a hard miss so we either fall back to FALLBACK_VERSION or
        # exit under --strict-version — never silently keep going.
        logger.warning("adcp.get_adcp_version() not callable (%s); treating as unavailable", e)
        return None


def _upstream_url(version: str) -> str:
    return f"{UPSTREAM_BASE}/{version}/index.json"


def _probe_version(version: str, timeout: float = 10.0) -> bool:
    """Return True if upstream has an index.json for this version."""
    url = _upstream_url(version)
    try:
        response = httpx.head(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as e:
        logger.warning("Upstream probe for %s failed: %s", url, e)
        return False
    return response.status_code == 200


def _select_version(explicit: str | None, strict: bool) -> str:
    """Pick the version to refresh against, per the tri-mode resolution."""
    if explicit:
        logger.info("Using explicit --version %s", explicit)
        return explicit

    pinned = _resolve_pinned_version()
    if pinned and _probe_version(pinned):
        logger.info("Using pinned adcp version %s", pinned)
        return pinned

    if strict:
        if pinned is None:
            raise SystemExit(
                "Could not resolve a pinned adcp version (the `adcp` package is missing or "
                "exposes no get_adcp_version()).\n"
                "Refusing to fall back under --strict-version. "
                f"Rerun without --strict-version or pass --version {FALLBACK_VERSION}."
            )
        raise SystemExit(
            f"Pinned adcp version {pinned!r} not available at {_upstream_url(pinned)}.\n"
            "Refusing to fall back under --strict-version. "
            f"Rerun without --strict-version or pass --version {FALLBACK_VERSION}."
        )

    if pinned:
        logger.warning(
            "Pinned adcp version %s not available upstream at %s — falling back to %r. "
            "File a follow-up to pin the Python-types and JSON-schema versions together.",
            pinned,
            _upstream_url(pinned),
            FALLBACK_VERSION,
        )
    else:
        logger.warning("Could not resolve pinned adcp version; using %r", FALLBACK_VERSION)

    return FALLBACK_VERSION


async def _refresh(version: str) -> None:
    """Download the transitive closure of AdCP schemas into schemas/{version}/.

    We walk index.json + every referenced body ourselves rather than relying
    on preload_schemas()'s hardcoded 4-bucket iteration (the current index has
    16 top-level buckets and the helper predates that expansion).
    """
    logger.info("Refreshing schemas into schemas/%s/", version)
    local_prefix = f"/schemas/{version}/"

    async with AdCPSchemaValidator(adcp_version=version) as validator:
        index = await validator.get_schema_index()

        queue: set[str] = set()
        collect_refs(index, queue)

        seen: set[str] = set()
        fetched = 0
        while queue:
            ref = queue.pop()
            if ref in seen:
                continue
            seen.add(ref)
            if not (ref.startswith(local_prefix) and ref.endswith(".json")):
                continue

            schema = await validator.get_schema(ref)
            fetched += 1
            nested: set[str] = set()
            collect_refs(schema, nested)
            queue |= nested - seen

    cache_dir = PROJECT_ROOT / "schemas" / version
    pointer = PROJECT_ROOT / "schemas" / ".current-version"
    pointer.write_text(version + "\n")
    logger.info("Fetched %d schemas; cache at %s; pointer -> %s", fetched, cache_dir, pointer)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the local AdCP schema cache at schemas/{version}/.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Explicit schema version (e.g. 'latest' or a pinned semver). " "Overrides pinned-version resolution.",
    )
    parser.add_argument(
        "--strict-version",
        action="store_true",
        help="Fail if the pinned adcp version has no upstream index.json. "
        "Without this flag, the script falls back to 'latest' with a warning.",
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

    version = _select_version(explicit=args.version, strict=args.strict_version)
    asyncio.run(_refresh(version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
