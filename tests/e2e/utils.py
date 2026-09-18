import shutil
import subprocess
import time
from contextlib import contextmanager

import httpx
import psycopg2
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from sqlalchemy import select

from scripts.setup.init_database_ci import CI_TEST_TENANT_ID
from tests.helpers.credentials import credential_headers


def make_mcp_client(
    live_server: dict,
    *,
    token: str | None = None,
    tenant: str | None = CI_TEST_TENANT_ID,
    dry_run: bool = False,
    session_id: str | None = None,
    host: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Client:
    """Build an MCP client against the live e2e stack (GH #1423 consolidation).

    Single home for the authed-client construction previously copy-pasted across
    ~10 tests/e2e files. The credential trio comes from ``credential_headers``
    (``tests/harness/client.py``), the one producer; the kwargs below are the
    intentional variations, and only the first two are credentials:

    - ``token``: the credential, sent as ``Authorization: Bearer`` (omit for
      unauthenticated flows).
    - ``tenant``: value for ``x-adcp-tenant`` (default ``ci-test``; pass None to
      omit, for example domain-routing tests that select the tenant via ``host``).
    - ``dry_run``: adds ``X-Dry-Run: true`` (the ``e2e_client`` fixture default;
      lifecycle tests that must persist real state leave it off).
    - ``session_id``: adds ``X-Test-Session-ID`` for testing-hook isolation.
    - ``host``: overrides the ``Host`` header (domain-routing tests).

    Returns an un-entered ``Client``; callers use ``async with``.
    """
    headers = credential_headers(token=token, tenant=tenant)
    if session_id is not None:
        headers["X-Test-Session-ID"] = session_id
    if dry_run:
        headers["X-Dry-Run"] = "true"
    if host is not None:
        headers["Host"] = host
    if extra_headers:
        headers.update(extra_headers)
    transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)
    return Client(transport=transport)


class _LiveDBEnv:
    """Minimal env shim over the live e2e database for ``tests/factories`` helpers.

    Bridges factory-level helpers (which expect a harness env exposing
    ``get_session()`` and ``_commit_factory_data()``, and which may invoke
    factory classes — see tests/harness/_base.py) to the Docker-hosted e2e
    stack, where only the DSN in ``live_server['postgres']`` is available
    (GH #1423 consolidation).
    """

    def __init__(self, session):
        self._session = session

    def get_session(self):
        return self._session

    def _commit_factory_data(self) -> None:
        """Commit pending factory/session state (harness env contract)."""
        self._session.commit()


@contextmanager
def live_db_env(live_server: dict):
    """Yield a harness-contract env bound to the live e2e database.

    Binds ``tests/factories`` to the live-DB session for the duration of the
    context (mirroring tests/harness/_base.py ``__enter__``/``__exit__``), so
    factory-based helpers like ``set_adapter_test_behavior`` can create rows —
    without the binding, factory instantiation crashes with "No session
    provided". Nested factory-binding envs are rejected the same way the
    harness rejects them: the binding is global state.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from src.core.database.database_session import _pydantic_json_serializer
    from tests.factories import ALL_FACTORIES

    engine = create_engine(live_server["postgres"], json_serializer=_pydantic_json_serializer)
    session = Session(engine)
    for f in ALL_FACTORIES:
        assert f._meta.sqlalchemy_session is None, (
            f"Factory {getattr(f, '__name__', type(f).__name__)} session already bound — "
            "live_db_env cannot nest inside another factory-binding env"
        )
    for f in ALL_FACTORIES:
        f._meta.sqlalchemy_session = session
    try:
        yield _LiveDBEnv(session)
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
        session.close()
        engine.dispose()


def set_live_adapter_behavior(live_server: dict, *, tenant_id: str = CI_TEST_TENANT_ID, **behavior):
    """Upsert adapter test-behavior on the live e2e DB via the shared factory helper.

    Single e2e entry point for what used to be five copy-pasted psycopg2
    upserts of ``adapter_config.mock_manual_approval_required``. Delegates to
    tests/factories/core.py ``set_adapter_test_behavior`` — the one home for
    the logical operation — through :func:`live_db_env`. Fails loud: a missing
    tenant or DB error is a test-infrastructure defect, never something to
    print-and-continue past.
    """
    from src.core.database.models import Tenant
    from tests.factories.core import set_adapter_test_behavior

    with live_db_env(live_server) as env:
        tenant = env.get_session().scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            raise RuntimeError(
                f"Tenant {tenant_id!r} not found in the live e2e DB — did the stack's init_database_ci.py seed run?"
            )
        return set_adapter_test_behavior(env, tenant.tenant_id, **behavior)


def declare_tenant_front(live_server: dict, origin: str, *, tenant_id: str = CI_TEST_TENANT_ID) -> str:
    """Point *tenant_id*'s ``virtual_host`` at *origin*, and return the host it stored.

    A card fetch is DISCOVERY: the client has a hostname and nothing else, so the tenant
    has to be resolvable from the Host alone, and a request naming no tenant is refused.
    Which host this stack is served at is decided per session — in-network it is a compose
    service name, on the host path a dynamically allocated TLS port — so it cannot be a
    literal in a seeder. The test knows it, from ``live_server``, and states it here.

    Idempotent, and scoped to this stack's own database through the same ``live_db_env``
    every other e2e mutation goes through.
    """
    from urllib.parse import urlsplit

    from sqlalchemy import select

    from src.core.database.models import Tenant

    front = urlsplit(origin).netloc or origin
    with live_db_env(live_server) as env:
        session = env.get_session()
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            raise RuntimeError(
                f"Tenant {tenant_id!r} not found in the live e2e DB — did the stack's init_database_ci.py seed run?"
            )
        tenant.virtual_host = front
        session.commit()
    return front


def wait_until(predicate, timeout_seconds: float, poll_interval: float = 0.5) -> bool:
    """Poll ``predicate`` until it is truthy or ``timeout_seconds`` elapses.

    A ``time.monotonic()`` deadline, not an iteration counter — the caller's
    ``predicate`` may itself be a network round trip (e.g. a webhook-capture
    readback, salesagent-amht.3), so counting iterations under-waits whenever
    a single check costs more than ``poll_interval``. Returns whether
    ``predicate`` was ever truthy, so a caller can assert on the return value
    instead of re-evaluating ``predicate`` a second time.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_interval)
    return bool(predicate())


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
