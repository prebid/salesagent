#!/usr/bin/env python3
"""
Refresh AdCP Schemas from Official Source

This script supports two modes:

**Incremental Mode (Default):**
- Uses ETag-based caching (HTTP 304 Not Modified)
- Only downloads schemas that changed on server
- Preserves .meta files to maintain ETags
- Fast, efficient, no spurious git changes
- Ideal for workspace setup and regular updates

**Clean Mode (--clean flag):**
- Deletes ALL cached schemas and metadata
- Forces complete re-download from server
- Use when cache may be corrupted
- Use when testing schema download logic

Usage:
    # Incremental update (recommended)
    python scripts/refresh_adcp_schemas.py

    # Clean refresh (nuclear option)
    python scripts/refresh_adcp_schemas.py --clean

    # Different version
    python scripts/refresh_adcp_schemas.py --version v2
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.e2e.adcp_schema_validator import preload_schemas


async def refresh_schemas(adcp_version: str = "v1", dry_run: bool = False, clean: bool = False):
    """
    Refresh AdCP schemas from official source.

    Args:
        adcp_version: AdCP version to download (e.g., "v1")
        dry_run: If True, show what would be deleted without actually deleting
        clean: If True, delete all cached files before downloading (nuclear option).
               If False (default), use ETag-based incremental updates (preserves .meta files)
    """
    # Determine cache directory (schemas moved to project root in #614)
    project_root = Path(__file__).parent.parent
    cache_dir = project_root / "schemas" / adcp_version

    print("🔍 AdCP Schema Refresh Tool")
    print(f"Version: {adcp_version}")
    print(f"Cache directory: {cache_dir}")
    print(f"Mode: {'Clean refresh (delete all)' if clean else 'Incremental (ETag caching)'}")
    print()

    # Step 1: Clean up existing cache (only if --clean flag is set)
    if clean:
        if cache_dir.exists():
            cached_files = list(cache_dir.glob("*.json"))
            meta_files = list(cache_dir.glob("*.meta"))
            total_files = len(cached_files) + len(meta_files)

            print(f"📂 Found {len(cached_files)} cached schema files and {len(meta_files)} metadata files")

            if cached_files or meta_files:
                print(f"\n{'🔍 Would delete' if dry_run else '🗑️  Deleting'} old files:")

                # Delete schema files
                for schema_file in sorted(cached_files):
                    print(f"  - {schema_file.name}")
                    if not dry_run:
                        schema_file.unlink()

                # Delete metadata files
                for meta_file in sorted(meta_files):
                    print(f"  - {meta_file.name} (metadata)")
                    if not dry_run:
                        meta_file.unlink()

                if not dry_run:
                    print(
                        f"\n✅ Deleted {total_files} old files ({len(cached_files)} schemas + {len(meta_files)} metadata)"
                    )
            else:
                print("✨ No cached files found (clean slate)")
        else:
            print("✨ Cache directory doesn't exist yet (clean slate)")
            if not dry_run:
                cache_dir.mkdir(parents=True, exist_ok=True)

        if dry_run:
            print("\n🔍 DRY RUN - no changes made")
            print("Run without --dry-run to actually refresh schemas")
            return
    else:
        # Incremental mode: preserve existing cache and .meta files
        if cache_dir.exists():
            cached_files = list(cache_dir.glob("*.json"))
            meta_files = list(cache_dir.glob("*.meta"))
            print(f"📂 Found {len(cached_files)} cached schemas and {len(meta_files)} metadata files")
            print("   ETag-based caching enabled (only downloads if changed on server)")
        else:
            print("📂 Cache directory doesn't exist - will create and download all schemas")
            if not dry_run:
                cache_dir.mkdir(parents=True, exist_ok=True)

        if dry_run:
            print("\n🔍 DRY RUN - would use ETag caching to download only changed schemas")
            return

    # Step 2: Download fresh schemas
    print("\n📥 Downloading fresh schemas from https://adcontextprotocol.org...")
    print()

    try:
        await preload_schemas(load_all=True, adcp_version=adcp_version)
    except Exception as e:
        print(f"\n❌ Error downloading schemas: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 3: Verify results
    print("\n🔍 Verifying downloaded schemas...")
    new_cached_files = list(cache_dir.glob("*.json"))
    print(f"✅ Successfully cached {len(new_cached_files)} schemas")

    # Check for problematic files
    print("\n🔍 Checking for outdated schema references...")
    budget_json_files = [f for f in new_cached_files if "budget_json" in f.name]

    if budget_json_files:
        print(f"⚠️  WARNING: Found {len(budget_json_files)} budget.json references:")
        for f in budget_json_files:
            print(f"  - {f.name}")
        print("\n💡 These files should NOT exist per AdCP spec (budgets are plain numbers)")
        print("   The official spec may have changed or there's a bug in the schema download")
    else:
        print("✅ No budget.json references found (correct per AdCP spec)")

    # Show summary
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    print(f"Cache directory: {cache_dir}")
    print(f"Total schemas: {len(new_cached_files)}")
    print(f"Schema version: {adcp_version}")
    print()
    print("✅ Schema refresh completed successfully!")
    print()
    print("Next steps:")
    print("1. Run tests to verify schemas work correctly")
    print("2. Commit updated schema cache to git")
    print("3. Update any code that assumed budget object format")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Refresh AdCP schemas from official source",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Incremental refresh (default - uses ETag caching, preserves .meta files)
  python scripts/refresh_adcp_schemas.py

  # Clean refresh (deletes all cache, forces re-download)
  python scripts/refresh_adcp_schemas.py --clean

  # Dry run to see what would happen
  python scripts/refresh_adcp_schemas.py --dry-run

  # Refresh specific version
  python scripts/refresh_adcp_schemas.py --version v2
        """,
    )

    parser.add_argument("--version", default="v1", help="AdCP version to download (default: v1)")

    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")

    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete all cached files before downloading (nuclear option). "
        "Default: use ETag-based incremental updates.",
    )

    args = parser.parse_args()

    # Run async refresh
    asyncio.run(refresh_schemas(adcp_version=args.version, dry_run=args.dry_run, clean=args.clean))


if __name__ == "__main__":
    main()
