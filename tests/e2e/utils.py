import time
import httpx
import pytest

def wait_for_server_readiness(mcp_url: str, timeout: int = 60):
    """
    Wait for the MCP server to become ready by checking its health endpoint.
    
    Args:
        mcp_url: Base URL of the MCP server (e.g., http://localhost:8080)
        timeout: Maximum time to wait in seconds (default: 60)
    
    Raises:
        pytest.fail if server does not become ready within timeout
    """
    print(f"Waiting for MCP server at {mcp_url}...")
    for _ in range(timeout):
        try:
            # Synchronous wait logic using httpx for simplicity in sync/async contexts
            # But since we are in a helper, we can use sync httpx.Client or requests
            with httpx.Client() as client:
                resp = client.get(f"{mcp_url}/health", timeout=1.0)
                if resp.status_code == 200:
                    print("✓ Server is ready")
                    return
        except Exception:
            pass
        time.sleep(1)
    
    pytest.fail(f"Server at {mcp_url} did not become ready within {timeout} seconds")

