"""Native task operations over the application's real JSON-RPC HTTP route."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import RootModel

from src.core.credentials import hash_token
from tests.factories import AdapterConfigFactory, MediaBuyFactory, PrincipalFactory, TenantFactory
from tests.factories.principal import plaintext_token_for
from tests.harness._base import BareIntegrationEnv
from tests.harness.client import jsonrpc_http_result
from tests.harness.transport import Transport, TransportResult
from tests.helpers.credentials import credential_headers


class A2ATaskOwnershipEnv(BareIntegrationEnv):
    """Seed callers through factories and retain task IDs received on the wire."""

    A2A_OVER_HTTP = True

    def response_parser(self, tool: str) -> Any:
        return lambda **data: RootModel(data)

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

    def call_via(self, transport: Transport, **kwargs: Any) -> TransportResult:
        assert transport in (Transport.A2A, Transport.E2E_A2A)
        action = kwargs.get("action")
        credential = self._caller(kwargs["caller"])
        if action in ("invoke", "poll"):
            self.A2A_SKILL = self.skill if action == "invoke" else "get_task_status"
            parameters = self.parameters if action == "invoke" else {"task_id": self.adcp_task_id}
            result = super().call_via(
                transport, credential=credential, signed=kwargs.get("signed", False), **parameters
            )
        else:
            task_id = self.task_id if kwargs.get("target", "existing") == "existing" else "missing-task"
            body = {"jsonrpc": "2.0", "id": "task-call", "method": kwargs["operation"], "params": {"id": task_id}}
            result = jsonrpc_http_result(transport, self.post_a2a(body, credential))
        if action == "invoke":
            self.task_id = result.envelope["task"]["id"]
            # Preserve the actual submitted handle for a later, independent poll.
            if self.skill == "update_media_buy" and self.parameters["media_buy_id"] != "missing-buy":
                self.adcp_task_id = self.adcp_result(result)["task_id"]
        return result

    @staticmethod
    def adcp_result(result: TransportResult) -> dict[str, Any]:
        assert result.has_wire
        assert "adcp_task_id" not in result.envelope["task"]["artifacts"][0].get("metadata", {})
        if result.is_error:
            return result.error_envelope()
        assert result.wire_response is not None
        return result.wire_response

    @staticmethod
    def assert_not_found(result: TransportResult) -> None:
        assert result.is_error
        assert result.has_wire
        assert result.raw_response.status_code == 200
        assert result.envelope["jsonrpc_error_code"] == -32001
        requested_id = json.loads(result.raw_response.request.content)["params"]["id"]
        refusal = result.raw_response.json()["error"]
        assert requested_id in refusal["message"]
        assert refusal["data"] == {"task_id": requested_id}

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
