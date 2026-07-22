"""Unit tests for the sync_governance tool (UC-030, #1329).

Covers the seller-side governance-binding contract per AdCP 3.1.1
(account/sync-governance-request.json + sync-governance-response.json +
accounts/tasks/sync_governance.mdx):

- Success variant: envelope status=completed, per-account status=synced,
  governance_agents[].url echoed, credentials NEVER echoed.
- Persistence: url-only, replace semantics (update_fields overwrites binding).
- Authority MUST: unknown account -> failed ACCOUNT_NOT_FOUND; unowned account
  -> failed SCOPE_INSUFFICIENT. Partial failure stays the success variant.
- Auth required (operation-level) and empty-accounts validation.

These are _impl-level tests, so they assert on the typed response (per
tests/CLAUDE.md, wire-envelope assertions are for error-path transport tests;
the success/persistence contract is verified against the typed payload here).
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from src.core.exceptions import (
    AdCPAccountNotFoundError,
    AdCPAuthenticationError,
    AdCPAuthorizationError,
)
from src.core.schemas.account import SyncGovernanceRequest, SyncGovernanceResponse

GOV_URL = "https://governance.pinnacle-media.com"
BEARER_CREDS = "x" * 64  # >= 32 chars per schema minLength


def _make_identity(principal_id: str | None = "principal-1", tenant_id: str = "tenant-1"):
    from tests.factories import PrincipalFactory

    tenant = {"tenant_id": tenant_id, "name": "Test Publisher", "subdomain": "testpub"}
    return PrincipalFactory.make_identity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant,
        protocol="mcp",
    )


def _make_request(
    *,
    account_ref: dict | None = None,
    url: str = GOV_URL,
    idempotency_key: str = "uuid-v4-unit-00000000000000001",
    accounts: list[dict] | None = None,
) -> SyncGovernanceRequest:
    if accounts is None:
        accounts = [
            {
                "account": account_ref or {"account_id": "acc_1"},
                "governance_agents": [
                    {"url": url, "authentication": {"schemes": ["Bearer"], "credentials": BEARER_CREDS}}
                ],
            }
        ]
    return SyncGovernanceRequest(idempotency_key=idempotency_key, accounts=accounts)


def _patch_deps(*, resolve_side_effect=None, repo: MagicMock | None = None) -> tuple[ExitStack, MagicMock]:
    """Patch AccountUoW, resolve_account, and the audit logger.

    Returns (stack, repo_mock) — enter the stack in a `with` block.
    """
    stack = ExitStack()
    repo = repo or MagicMock()

    mock_uow = MagicMock()
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow.accounts = repo
    stack.enter_context(patch("src.core.tools.governance.AccountUoW", return_value=mock_uow))
    stack.enter_context(patch("src.core.tools.governance.get_audit_logger"))
    stack.enter_context(patch("src.core.tools.governance.resolve_account", side_effect=resolve_side_effect))
    return stack, repo


class TestSyncGovernanceSuccess:
    """Happy-path contract: synced, persisted url-only, echoed without credentials."""

    @pytest.mark.asyncio
    async def test_synced_status_and_completed_envelope(self):
        from src.core.tools.governance import _sync_governance_impl

        stack, repo = _patch_deps(resolve_side_effect=lambda ref, ident, r: "acc_1")
        with stack:
            resp = await _sync_governance_impl(_make_request(), _make_identity())

        assert isinstance(resp, SyncGovernanceResponse)
        assert len(resp.accounts) == 1
        assert resp.accounts[0].status == "synced"
        # Envelope status is the synchronous-success protocol status.
        assert resp.model_dump(mode="json")["status"] == "completed"

    @pytest.mark.asyncio
    async def test_persists_url_only_with_replace_semantics(self):
        from src.core.tools.governance import _sync_governance_impl

        stack, repo = _patch_deps(resolve_side_effect=lambda ref, ident, r: "acc_1")
        with stack:
            await _sync_governance_impl(_make_request(url=GOV_URL), _make_identity())

        # Persisted via update_fields (replace), and only the URL is stored —
        # never the credentials (the DB column model is url-only by design).
        repo.update_fields.assert_called_once_with("acc_1", governance_agents=[{"url": GOV_URL + "/"}])

    @pytest.mark.asyncio
    async def test_credentials_never_echoed(self):
        from src.core.tools.governance import _sync_governance_impl

        stack, _repo = _patch_deps(resolve_side_effect=lambda ref, ident, r: "acc_1")
        with stack:
            resp = await _sync_governance_impl(_make_request(), _make_identity())

        dumped = resp.model_dump(mode="json")
        serialized = str(dumped)
        assert "authentication" not in serialized
        assert BEARER_CREDS not in serialized
        assert "credentials" not in serialized
        # But the URL IS echoed.
        assert dumped["accounts"][0]["governance_agents"][0]["url"] == GOV_URL + "/"


class TestSyncGovernanceAuthorityContract:
    """The normative MUST: verify authority before persisting; per-account failures."""

    @pytest.mark.asyncio
    async def test_unknown_account_fails_with_account_not_found(self):
        from src.core.tools.governance import _sync_governance_impl

        def _raise(ref, ident, r):
            raise AdCPAccountNotFoundError("Account 'acc_x' not found.", suggestion="Use list_accounts.")

        stack, repo = _patch_deps(resolve_side_effect=_raise)
        with stack:
            resp = await _sync_governance_impl(_make_request(account_ref={"account_id": "acc_x"}), _make_identity())

        assert resp.accounts[0].status == "failed"
        assert resp.accounts[0].errors[0].code == "ACCOUNT_NOT_FOUND"
        # A failed account never persists a binding.
        repo.update_fields.assert_not_called()

    @pytest.mark.asyncio
    async def test_unowned_account_fails_with_scope_insufficient(self):
        from src.core.tools.governance import _sync_governance_impl

        def _raise(ref, ident, r):
            raise AdCPAuthorizationError("Agent lacks access to 'acc_1'.", suggestion="Use list_accounts.")

        stack, repo = _patch_deps(resolve_side_effect=_raise)
        with stack:
            resp = await _sync_governance_impl(_make_request(), _make_identity())

        assert resp.accounts[0].status == "failed"
        # An existing account the agent has no authority over uses the standard
        # SCOPE_INSUFFICIENT code (pinned error-code enum + graded BR-UC-030), not
        # the AdCPAuthorizationError default wire code (AUTH_REQUIRED) — this
        # asserts we set the spec/graded code explicitly, not the wire default.
        assert resp.accounts[0].errors[0].code == "SCOPE_INSUFFICIENT"
        repo.update_fields.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_failure_stays_success_variant(self):
        from src.core.tools.governance import _sync_governance_impl

        def _resolve(ref, ident, r):
            if ref.root.account_id == "acc_ok":
                return "acc_ok"
            raise AdCPAccountNotFoundError("nope", suggestion="s")

        accounts = [
            {
                "account": {"account_id": "acc_ok"},
                "governance_agents": [
                    {"url": GOV_URL, "authentication": {"schemes": ["Bearer"], "credentials": BEARER_CREDS}}
                ],
            },
            {
                "account": {"account_id": "acc_bad"},
                "governance_agents": [
                    {"url": GOV_URL, "authentication": {"schemes": ["Bearer"], "credentials": BEARER_CREDS}}
                ],
            },
        ]
        stack, repo = _patch_deps(resolve_side_effect=_resolve)
        with stack:
            resp = await _sync_governance_impl(_make_request(accounts=accounts), _make_identity())

        assert len(resp.accounts) == 2
        statuses = {a.account.root.account_id: a.status for a in resp.accounts}
        assert statuses == {"acc_ok": "synced", "acc_bad": "failed"}
        # Only the synced account persisted.
        repo.update_fields.assert_called_once_with("acc_ok", governance_agents=[{"url": GOV_URL + "/"}])


class TestSyncGovernanceOperationLevel:
    """Operation-level failures: auth and empty accounts."""

    @pytest.mark.asyncio
    async def test_missing_auth_raises_auth_required(self):
        from src.core.tools.governance import _sync_governance_impl

        stack, _repo = _patch_deps(resolve_side_effect=lambda *a: "acc_1")
        with stack, pytest.raises(AdCPAuthenticationError):
            await _sync_governance_impl(_make_request(), identity=None)

    def test_empty_accounts_rejected_at_schema(self):
        # The request schema enforces accounts minItems:1, so an empty array is a
        # construction-time validation error — it never reaches the impl.
        with pytest.raises(ValueError):
            SyncGovernanceRequest(idempotency_key="uuid-v4-unit-00000000000000001", accounts=[])


class TestSyncGovernanceRequestSchema:
    """Request schema enforces the spec's idempotency_key + agent constraints."""

    def test_idempotency_key_required(self):
        with pytest.raises(ValueError):
            SyncGovernanceRequest(
                accounts=[
                    {
                        "account": {"account_id": "acc_1"},
                        "governance_agents": [
                            {"url": GOV_URL, "authentication": {"schemes": ["Bearer"], "credentials": BEARER_CREDS}}
                        ],
                    }
                ]
            )

    def test_idempotency_key_too_short_rejected(self):
        with pytest.raises(ValueError):
            _make_request(idempotency_key="short")  # < 16 chars

    def test_non_https_agent_url_rejected(self):
        with pytest.raises(ValueError):
            _make_request(url="http://governance.pinnacle-media.com")

    def test_credentials_below_min_length_rejected(self):
        with pytest.raises(ValueError):
            SyncGovernanceRequest(
                idempotency_key="uuid-v4-unit-00000000000000001",
                accounts=[
                    {
                        "account": {"account_id": "acc_1"},
                        "governance_agents": [
                            {"url": GOV_URL, "authentication": {"schemes": ["Bearer"], "credentials": "short"}}
                        ],
                    }
                ],
            )

    def test_more_than_one_agent_per_account_rejected(self):
        with pytest.raises(ValueError):
            SyncGovernanceRequest(
                idempotency_key="uuid-v4-unit-00000000000000001",
                accounts=[
                    {
                        "account": {"account_id": "acc_1"},
                        "governance_agents": [
                            {"url": GOV_URL, "authentication": {"schemes": ["Bearer"], "credentials": BEARER_CREDS}},
                            {
                                "url": "https://other.example.com",
                                "authentication": {"schemes": ["Bearer"], "credentials": BEARER_CREDS},
                            },
                        ],
                    }
                ],
            )
