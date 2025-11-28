#!/usr/bin/env python3
"""
Simple A2A query script with secure authentication.
Uses Authorization headers instead of query parameters for security.
Avoids python-a2a CLI limitations with authentication.
"""

import json
import sys

import requests


def query_a2a(message, token="demo_token_123", endpoint="http://localhost:8091", tenant_id=None):
    """Send an authenticated query to the A2A server."""

    # Construct the URL (no token in query string for security)
    url = f"{endpoint}/tasks/send"

    # Set up authentication header (secure method)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Add tenant hint if provided (useful for localhost where subdomain detection doesn't work)
    if tenant_id:
        headers["x-adcp-tenant"] = tenant_id

    # Create the A2A message format
    payload = {"message": {"content": {"text": message}}}

    # Send the request with authentication header
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()

        # Parse and display the response
        data = response.json()

        # Extract the text from the response
        if "artifacts" in data and data["artifacts"]:
            for artifact in data["artifacts"]:
                if "parts" in artifact:
                    for part in artifact["parts"]:
                        if part.get("type") == "text":
                            print(part["text"])
        else:
            print(json.dumps(data, indent=2))

    except requests.exceptions.RequestException as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    # Get message from command line or use default
    message = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What products do you have?"

    # You can override these with environment variables
    import os

    token = os.getenv("A2A_TOKEN", "demo_token_123")
    endpoint = os.getenv("A2A_ENDPOINT", "http://localhost:8091")
    tenant_id = os.getenv("A2A_TENANT_ID", None)

    query_a2a(message, token, endpoint, tenant_id)
