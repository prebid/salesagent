#!/usr/bin/env python3
"""Test property discovery service with real publisher domains.

This script tests the property discovery service against real publishers
like weather.com and accuweather.com to verify it can fetch and parse
their adagents.json files.
"""

import asyncio
import logging
from datetime import UTC, datetime

from adcp import fetch_adagents, get_all_properties, get_all_tags

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def test_domain(domain: str) -> dict:
    """Test fetching adagents.json from a domain.

    Args:
        domain: Publisher domain to test

    Returns:
        Dict with test results
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"Testing domain: {domain}")
    logger.info(f"{'='*60}")

    result = {
        "domain": domain,
        "success": False,
        "error": None,
        "properties_count": 0,
        "tags_count": 0,
        "properties": [],
        "tags": [],
    }

    try:
        # Fetch adagents.json
        logger.info(f"Fetching https://{domain}/.well-known/adagents.json")
        adagents_data = await fetch_adagents(domain)
        logger.info("✅ Successfully fetched adagents.json")

        # Extract properties
        properties = get_all_properties(adagents_data)
        result["properties_count"] = len(properties)
        result["properties"] = properties
        logger.info(f"Found {len(properties)} properties")

        # Show property details
        for i, prop in enumerate(properties, 1):
            logger.info(f"\n  Property {i}:")
            logger.info(f"    Type: {prop.get('property_type')}")
            logger.info(f"    Name: {prop.get('name', 'N/A')}")
            logger.info(f"    Identifiers: {prop.get('identifiers', [])}")
            logger.info(f"    Tags: {prop.get('tags', [])}")

        # Extract tags
        tags = get_all_tags(adagents_data)
        result["tags_count"] = len(tags)
        result["tags"] = tags
        logger.info(f"\nFound {len(tags)} unique tags: {', '.join(tags) if tags else 'None'}")

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"❌ Error: {e}")

    return result


async def main():
    """Test multiple publisher domains."""
    domains = [
        "weather.com",
        "accuweather.com",
        "wonderstruck.org",
        "wunderground.com",
        "weather.gov",
    ]

    logger.info(f"\n{'#'*60}")
    logger.info("Testing Property Discovery with Real Publishers")
    logger.info(f"{'#'*60}\n")
    logger.info(f"Testing {len(domains)} domains: {', '.join(domains)}")
    logger.info(f"Started at: {datetime.now(UTC).isoformat()}\n")

    # Test all domains in parallel
    tasks = [test_domain(domain) for domain in domains]
    results = await asyncio.gather(*tasks)

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info("SUMMARY")
    logger.info(f"{'='*60}")

    successful = sum(1 for r in results if r["success"])
    failed = len(results) - successful

    logger.info(f"\nTotal domains tested: {len(domains)}")
    logger.info(f"Successful: {successful}")
    logger.info(f"Failed: {failed}")

    if successful > 0:
        logger.info("\n✅ Successful domains:")
        for r in results:
            if r["success"]:
                logger.info(f"  - {r['domain']}: {r['properties_count']} properties, {r['tags_count']} tags")

    if failed > 0:
        logger.info("\n❌ Failed domains:")
        for r in results:
            if not r["success"]:
                logger.info(f"  - {r['domain']}: {r['error']}")

    logger.info(f"\nFinished at: {datetime.now(UTC).isoformat()}")

    return results


if __name__ == "__main__":
    asyncio.run(main())
