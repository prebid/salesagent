import shutil
import subprocess
import time

import httpx
import psycopg2
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


def resolve_media_buy_for_task_in_db(live_server: dict, task_id: str) -> str:
    """Resolve the media_buy_id an approval-pending create's task_id names.

    The spec ``submitted`` create variant carries ``task_id`` only
    (``media_buy_id`` is forbidden on that oneOf branch and arrives on the
    completion artifact); e2e flows that need the persisted row follow the
    workflow mapping the same way the approval machinery does. Connects
    host-side via ``live_server["postgres"]`` — the same proven path the
    webhook payload tests use (the docker-compose exec route silently
    depends on compose project context and broke in the full-suite run).
    """
    conn = psycopg2.connect(live_server["postgres"])
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT object_id FROM object_workflow_mappings WHERE step_id = %s AND object_type = 'media_buy'",
            (task_id,),
        )
        row = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()
    assert row is not None, f"No media_buy workflow mapping found for step {task_id}"
    return row[0]


def force_approve_media_buy_in_db(live_server: dict, media_buy_id: str):
    """
    Force approve media buy in database to bypass approval workflow.

    Executes the update inside the docker container to avoid host port mapping issues.

    Args:
        live_server: Dictionary containing server info (postgres connection details)
        media_buy_id: ID of the media buy to approve
    """

    # SQL update script to run inside container
    update_script = f"""
import os
import psycopg2
from datetime import datetime

try:
    # Connect using the internal DATABASE_URL which is always correct inside the container
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cursor = conn.cursor()

    cursor.execute(\"\"\"
        UPDATE media_buys
        SET status = 'approved',
            approved_at = NOW(),
            approved_by = 'system_override'
        WHERE media_buy_id = '{media_buy_id}'
    \"\"\")

    conn.commit()
    print(f'Successfully forced approval for media_buy_id: {media_buy_id}')

    cursor.close()
    conn.close()
except Exception as e:
    print(f'Error updating media buy: {{e}}')
    exit(1)
"""

    def _direct_db_update(prior_exc: Exception | None) -> None:
        """Update the media buy straight against the server DB (live_server params).

        In-network the runner reaches the server DB by service name (postgres:5432
        /adcp, via postgres_params); on the host path this is the fallback when the
        in-container exec fails.
        """
        try:
            if "postgres_params" in live_server:
                params = live_server["postgres_params"]
                conn = psycopg2.connect(
                    host=params["host"],
                    port=params["port"],
                    user=params["user"],
                    password=params["password"],
                    dbname=params["dbname"],
                )
            else:
                conn = psycopg2.connect(live_server["postgres"])

            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE media_buys
                SET status = 'approved',
                    approved_at = NOW(),
                    approved_by = 'system_override'
                WHERE media_buy_id = %s
            """,
                (media_buy_id,),
            )
            conn.commit()
            conn.close()
            print("Direct DB approval update successful")
        except Exception as ex:
            print(f"Direct DB update failed: {ex}")
            raise prior_exc if prior_exc else ex

    # Host path: pytest runs on the host and cannot reach the container DB
    # directly, so exec the update inside the adcp-server container. In-network
    # there is no docker-compose binary and the runner CAN reach the server DB by
    # service name — go straight to the direct update (mirrors the conftest seed).
    if shutil.which("docker-compose"):
        try:
            cmd = ["docker-compose", "exec", "-T", "adcp-server", "python", "-c", update_script]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            print(f"In-container DB update failed: {e}; stdout={e.stdout!r} stderr={e.stderr!r}")
            print("Attempting fallback direct connection...")
            _direct_db_update(e)
    else:
        _direct_db_update(None)


def build_jsonrpc_message_send(
    parts: list[dict], *, context_id: str | None = None, configuration: dict | None = None
) -> dict:
    """Raw A2A ``message/send`` JSON-RPC envelope for direct-HTTP e2e tests.

    The webhook and compliance e2e suites hand-rolled this envelope at seven
    sites; the message/id fields are always fresh UUIDs, ``context_id``
    defaults to a fresh UUID, and ``configuration`` (e.g.
    ``pushNotificationConfig``) is attached only when given.
    """
    import uuid as _uuid

    params: dict = {
        "message": {
            "messageId": str(_uuid.uuid4()),
            "contextId": context_id or str(_uuid.uuid4()),
            "role": "user",
            "parts": parts,
        }
    }
    if configuration is not None:
        params["configuration"] = configuration
    return {
        "jsonrpc": "2.0",
        "id": str(_uuid.uuid4()),
        "method": "message/send",
        "params": params,
    }
