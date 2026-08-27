"""MCP wire shape for list_accounts: unset response fields must be absent, never null.

Regression for salesagent-rrz8. FastMCP's ``ToolResult`` serializes a raw
(non-dict) ``structured_content`` via ``pydantic_core.to_jsonable_python``,
which BYPASSES ``SalesAgentBaseModel.model_dump(exclude_none=True)``. 13 of
14 MCP tool wrappers (including ``list_accounts`` at
``src/core/tools/accounts.py``) pass the raw Pydantic response model
straight into ``ToolResult(structured_content=response)`` instead of
pre-serializing via ``response.model_dump(mode="json")`` — so ``None``
fields wire-serialize as JSON ``null`` on MCP instead of being omitted, per
AdCP 3.1.1 absent-means-absent semantics. A2A/REST both correctly call
``response.model_dump(mode="json")`` and are unaffected.

This mirrors the idiom in ``tests/integration/test_sync_creatives_mcp_wire_shape.py``:
dispatch through the real MCP transport via ``env.call_via(Transport.MCP, ...)``
and assert on ``result.wire_response`` (the real ``ToolResult.structured_content``
captured by the harness MCP client) — not on ``response.model_dump()`` at the
model level, which does not exercise the ``ToolResult`` boundary and would not
catch this bug (confirmed pre-existing unit/integration tests of this shape
pass despite the bug).
"""

from __future__ import annotations

import pytest

from tests.factories import AccountFactory, AgentAccountAccessFactory, PrincipalFactory, TenantFactory
from tests.harness.account_list import AccountListEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_mcp_wire_omits_pagination_when_unset(integration_db):
    """list_accounts on MCP must omit `pagination` when the caller didn't request it.

    _list_accounts_impl returns ListAccountsResponse(pagination=None, ...) when
    called with no `pagination` param (see _apply_pagination: pagination=None ->
    (accounts, None)). AdCP 3.1.1 absent-means-absent semantics require the field
    be OMITTED from the wire payload, not present as JSON null.

    Mutation check: fixing src/core/tools/accounts.py:list_accounts to build
    ToolResult(structured_content=response.model_dump(mode="json")) (the
    products.py:832 pattern) turns this green; reverting it goes red again.
    """
    with AccountListEnv(tenant_id="la_mcp_wire_t1", principal_id="agent_la_mcp") as env:
        tenant = TenantFactory(tenant_id="la_mcp_wire_t1")
        principal = PrincipalFactory(tenant=tenant, principal_id="agent_la_mcp")
        account = AccountFactory(tenant=tenant, account_id="acc_la_mcp_1", name="Visible")
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)

        result = env.call_via(Transport.MCP)

    assert result.is_success, f"Expected success but got error: {result.error}"
    wire = result.wire_response
    assert wire is not None, "MCP dispatch must stash the real structured_content wire"

    accounts = wire.get("accounts")
    assert isinstance(accounts, list) and len(accounts) == 1, f"expected 1 account on the wire, got {accounts!r}"

    assert "pagination" not in wire, (
        "AdCP 3.1.1 absent-means-absent: `pagination` must be OMITTED from MCP "
        f"structured_content when unset, not present as null. Got: {wire!r}"
    )
