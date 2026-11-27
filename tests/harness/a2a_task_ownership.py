"""Native task operations over the application's real JSON-RPC HTTP route."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import RootModel

from src.core.credentials import hash_token
from tests.factories import AdapterConfigFactory, MediaBuyFactory, PrincipalFactory, TenantFactory
from tests.factories.principal import plaintext_token_for
from tests.harness._base import BareIntegrationEnv, _a2a_jsonrpc_body
from tests.harness.transport import Transport, TransportResult
from tests.helpers.credentials import credential_headers


class A2ATaskOwnershipEnv(BareIntegrationEnv):
    """Seed callers through factories and retain task IDs received on the wire."""

    def response_parser(self, tool: str) -> Any:
        return lambda **data: RootModel(data)

    def address_tool(self, tool: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Address each invocation or durable poll without mutating the declaration."""
        action = payload.pop("action")
        if action == "invoke":
            return self.skill, dict(self.parameters)
        if action == "poll":
            return "get_task_status", {"task_id": self.adcp_task_id}
        raise ValueError(f"Unknown invocation action {action}")

    def _run_a2a_handler(self, skill_name: str, **kwargs: Any) -> TransportResult:
        """Invoke on the same HTTP handler that receives subsequent native lookups."""
        return self._run_a2a_over_http(skill_name, **kwargs)

    def prepare_task(self, state: str) -> None:
        tenant, principal = self.setup_default_data()
        PrincipalFactory(tenant=tenant, principal_id="sibling")
        other = TenantFactory(tenant_id="other-tenant")
        PrincipalFactory(tenant=other, principal_id=self._principal_id, token_hash=hash_token("other-tenant-token"))
        self.task_id = ""
        if state == "completed":
            skill, parameters = "get_adcp_capabilities", {}
        else:
            account_id = self.setup_default_account().account_id
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
                "account": {"account_id": account_id},
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
            result = super().call_via(
                transport, credential=credential, signed=kwargs.get("signed", False), action=action
            )
        else:
            task_id = self.task_id if kwargs.get("target", "existing") == "existing" else "missing-task"
            from a2a.types import CancelTaskRequest, GetTaskRequest

            self.requested_task_id = task_id
            request_cls = GetTaskRequest if kwargs["operation"] == "GetTask" else CancelTaskRequest
            body = _a2a_jsonrpc_body(kwargs["operation"], request_cls(id=task_id))
            result = self.send_a2a(body, credential)
        if action == "invoke":
            assert result.has_wire, "The receipt and native task lookup must use the same HTTP handler"
            self.task_id = result.envelope["a2a_task_id"]
            # Preserve the actual submitted handle for a later, independent poll.
            if self.skill == "update_media_buy" and self.parameters["media_buy_id"] != "missing-buy":
                self.adcp_task_id = result.require_wire()["task_id"]
        return result

    def assert_not_found(self, result: TransportResult) -> None:
        assert result.is_error
        assert result.has_wire
        assert result.raw_response.status_code == 200
        requested_id = self.requested_task_id
        refusal = result.envelope["jsonrpc_error"]
        assert requested_id in refusal["message"]
        assert refusal["data"] == {"task_id": requested_id}

    def assert_same_refusal(self, first: TransportResult, second: TransportResult) -> None:
        # IDs are caller-supplied. Everything else, including message and data,
        # must be indistinguishable; this compares wire responses, not table text.
        def without_id(result: TransportResult) -> Any:
            return (
                json.dumps(copy.deepcopy(result.envelope["jsonrpc_error"]), sort_keys=True)
                .replace(self.task_id, "TASK")
                .replace("missing-task", "TASK")
            )

        assert without_id(first) == without_id(second)
