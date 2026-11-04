"""Native task operations over the application's real JSON-RPC HTTP route."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.credentials import hash_token
from tests.factories import AdapterConfigFactory, MediaBuyFactory, PrincipalFactory, TenantFactory
from tests.factories.principal import plaintext_token_for
from tests.harness._base import (
    _A2A_PATH,
    _A2A_VERSION_HEADER,
    BareIntegrationEnv,
    _a2a_message_send_body,
)
from tests.harness.raw_wire import unwrap_a2a_native_response
from tests.harness.transport import Transport, TransportResult
from tests.helpers.credentials import credential_headers


class A2ATaskOwnershipEnv(BareIntegrationEnv):
    """Seed callers through factories and retain task IDs received on the wire."""

    def setup_task(self, state: str, creator: str = "owner") -> None:
        tenant, principal = self.setup_default_data()
        PrincipalFactory(tenant=tenant, principal_id="sibling")
        other = TenantFactory(tenant_id="other-tenant")
        PrincipalFactory(tenant=other, principal_id=self._principal_id, token_hash=hash_token("other-tenant-token"))
        self.task_id = ""
        if state == "completed":
            skill, parameters = "get_adcp_capabilities", {}
        else:
            self.setup_default_account()
            AdapterConfigFactory(tenant=tenant, mock_manual_approval_required=True)
            now = datetime.now(UTC)
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                status="active",
                start_date=now.date(),
                end_date=(now + timedelta(days=30)).date(),
            )
            skill = "update_media_buy"
            parameters = {
                "account": {"account_id": "acct_test"},
                "idempotency_key": "ownership-setup-key",
                "media_buy_id": buy.media_buy_id if state == "submitted" else "missing-buy",
                "end_time": (now + timedelta(days=60)).isoformat(),
            }
        self._commit_factory_data()
        if creator == "signed-owner":
            self.enable_request_signing()
        response = self._post(_a2a_message_send_body(skill, parameters), creator)
        self.created_task = response.json()["result"]["task"]
        self.task_id = self.created_task["id"]
        from src.app import _request_handler

        self._guard("a2a_task", lambda: _request_handler.tasks.pop(self.task_id, None))
        assert self.created_task["status"]["state"] == f"TASK_STATE_{state.upper()}", self.created_task

    def _caller(self, caller: str) -> dict[str, str]:
        if caller == "owner":
            return self.credential()
        if caller == "sibling":
            return self.credential(token=plaintext_token_for("sibling"))
        if caller == "other-tenant":
            return credential_headers(tenant="other-tenant", token="other-tenant-token")
        if caller == "anonymous":
            return self.credential(token=None)
        if caller == "invalid":
            return self.credential(token="invalid-ownership-token")
        raise ValueError(f"Unknown caller {caller}")

    def _post(self, body: dict, caller: str) -> Any:
        if caller in {"signed-owner", "malformed-signature"}:
            raw, headers = self.wire_request(
                path=_A2A_PATH,
                body=body,
                signed="malformed" if caller == "malformed-signature" else True,
                extra=dict(_A2A_VERSION_HEADER),
            )
            return self.get_rest_client().post(_A2A_PATH, content=raw, headers=headers)
        response = self.get_rest_client().post(
            _A2A_PATH, headers={**self._caller(caller), **_A2A_VERSION_HEADER}, json=body
        )
        return response

    def call_via(self, transport: Transport, **kwargs: Any) -> TransportResult:
        assert transport is Transport.A2A
        task_id = self.task_id if kwargs.get("target", "existing") == "existing" else "missing-task"
        response = self._post(
            {"jsonrpc": "2.0", "id": "ownership-call", "method": kwargs["operation"], "params": {"id": task_id}},
            kwargs["caller"],
        )
        return unwrap_a2a_native_response(response)

    def assert_unchanged(self) -> None:
        result = self.call_via(Transport.A2A, operation="GetTask", caller="owner")
        assert result.wire_response == self.created_task

    @staticmethod
    def assert_not_found(result: TransportResult) -> None:
        assert result.is_error
        assert result.has_wire
        assert result.envelope["jsonrpc_error"]["code"] == -32001
        requested_id = json.loads(result.raw_response.request.content)["params"]["id"]
        assert requested_id in result.envelope["jsonrpc_error"]["message"]
        assert result.envelope["jsonrpc_error"]["data"] == {"task_id": requested_id}

    def assert_same_refusal(self, first: TransportResult, second: TransportResult) -> None:
        # IDs are caller-supplied. Everything else, including message and data,
        # must be indistinguishable; this compares wire responses, not table text.
        def without_id(result: TransportResult) -> Any:
            return (
                json.dumps(result.raw_response.json()["error"], sort_keys=True)
                .replace(self.task_id, "TASK")
                .replace("missing-task", "TASK")
            )

        assert without_id(first) == without_id(second)
