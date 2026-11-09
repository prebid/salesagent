"""Native task operations over the application's real JSON-RPC HTTP route."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

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

    def prepare_task(self, state: str) -> None:
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
        parameters["context"] = {"trace": "native-receipt"}
        self.skill, self.parameters = skill, parameters
        self._commit_factory_data()

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
        headers = {**self._caller(caller), **_A2A_VERSION_HEADER}
        if self.e2e_config is not None:
            return httpx.post(f"{self.e2e_config.base_url}{_A2A_PATH}", headers=headers, json=body, timeout=30)
        return self.get_rest_client().post(_A2A_PATH, headers=headers, json=body)

    def call_via(self, transport: Transport, **kwargs: Any) -> TransportResult:
        assert transport in (Transport.A2A, Transport.E2E_A2A)
        action = kwargs.get("action")
        if action == "invoke":
            body = _a2a_message_send_body(self.skill, self.parameters)
        elif action == "poll":
            body = _a2a_message_send_body("get_task_status", {"task_id": self.adcp_task_id})
        else:
            task_id = self.task_id if kwargs.get("target", "existing") == "existing" else "missing-task"
            body = {"jsonrpc": "2.0", "id": "task-call", "method": kwargs["operation"], "params": {"id": task_id}}
        result = unwrap_a2a_native_response(self._post(body, kwargs["caller"]))
        if action == "invoke":
            self.task_id = result.wire_response["task"]["id"]
            # Preserve the actual submitted handle for a later, independent poll.
            if self.skill == "update_media_buy" and self.parameters["media_buy_id"] != "missing-buy":
                self.adcp_task_id = self.adcp_result(result)["task_id"]
        return result

    @staticmethod
    def adcp_result(result: TransportResult) -> dict[str, Any]:
        assert result.has_wire
        task = result.wire_response["task"]
        artifacts = task["artifacts"]
        assert len(artifacts) == 1, task
        parts = [part["data"] for part in artifacts[0]["parts"] if "data" in part]
        assert len(parts) == 1, artifacts
        assert "adcp_task_id" not in artifacts[0].get("metadata", {})
        return parts[0]

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
