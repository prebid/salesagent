#!/usr/bin/env python3
"""
Test script to verify the format_id type handling fix.

Database JSONB returns dicts, request validation returns FormatId objects.
Use dict access for database, attribute access for request.
"""

from pydantic import BaseModel


class FormatId(BaseModel):
    """Simulating the FormatId Pydantic model."""
    agent_url: str | None = None
    id: str


def test_format_handling():
    """Test different format_id representations."""
    
    print("=" * 60)
    print("Testing format_id type handling")
    print("=" * 60)
    
    # Database returns dicts (JSONB)
    db_format_ids = [
        {"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_300x250_image"}
    ]
    
    # Request returns FormatId objects (Pydantic validated)
    request_format_ids = [
        FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_300x250_image")
    ]
    
    print("\n1. Database format_ids (dicts):")
    for fmt in db_format_ids:
        # Use dict access
        agent_url = fmt["agent_url"]
        format_id = fmt["id"]
        normalized_url = str(agent_url).rstrip("/") if agent_url else None
        print(f"   ✅ ({normalized_url}, {format_id})")
    
    print("\n2. Request format_ids (FormatId objects):")
    for fmt in request_format_ids:
        # Use attribute access
        normalized_url = str(fmt.agent_url).rstrip("/") if fmt.agent_url else None
        print(f"   ✅ ({normalized_url}, {fmt.id})")
    
    print("\n3. Cross-type comparison:")
    # Build product keys from database (dicts)
    product_keys = set()
    for fmt in db_format_ids:
        agent_url = fmt["agent_url"]
        normalized_url = str(agent_url).rstrip("/") if agent_url else None
        product_keys.add((normalized_url, fmt["id"]))
    
    # Validate request keys against product
    for fmt in request_format_ids:
        normalized_url = str(fmt.agent_url).rstrip("/") if fmt.agent_url else None
        key = (normalized_url, fmt.id)
        is_valid = key in product_keys
        print(f"   {'✅ VALID' if is_valid else '❌ INVALID'}: {key}")
    
    print("\n" + "=" * 60)
    print("✅ Simple approach: dict access for DB, attribute access for request")
    print("=" * 60)


if __name__ == "__main__":
    test_format_handling()
