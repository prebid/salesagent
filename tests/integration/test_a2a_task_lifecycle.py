"""The in-memory A2A task registry retains only tasks returned to callers.

This is an internal resource-lifetime contract: the JSON-RPC refusal returns no task
ID, so a buyer-facing BDD scenario cannot observe the orphaned registry entry.
Requests use the real HTTP route, with no patched dispatch or fabricated errors.
The unchanged refusal code is also covered by T-PREDISPATCH-a2a-unknown-skill.
"""

import pytest
from a2a.types import Task, TaskState
from google.protobuf import json_format

from tests.factories import AdapterConfigFactory, MediaBuyFactory
from tests.harness._base import _A2A_PATH, _A2A_VERSION_HEADER, BareIntegrationEnv, _a2a_message_send_body

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _send_skill(client, skill):
    body = _a2a_message_send_body("get_products", {})
    # Mutate the wire so malformed skills reach production parsing too.
    body["params"]["message"]["parts"][0]["data"]["skill"] = skill
    return client.post(
        _A2A_PATH,
        headers=_A2A_VERSION_HEADER,
        json=body,
    )


def _assert_registered_task(response, task_registry, expected_state):
    assert response.status_code == 200
    returned_task = json_format.ParseDict(response.json()["result"]["task"], Task())
    assert returned_task.status.state == expected_state
    assert task_registry[returned_task.id].task == returned_task
    return returned_task


@pytest.fixture
def task_registry(integration_db):
    from src.app import _request_handler

    initial_ids = set(_request_handler.tasks)
    try:
        yield _request_handler.tasks
    finally:
        # The application handler is process-scoped; remove only this test's entries.
        for task_id in set(_request_handler.tasks) - initial_ids:
            _request_handler.tasks.pop(task_id)


@pytest.mark.parametrize(
    ("skill", "error_code", "message_prefix"),
    [
        pytest.param("nonexistent_skill", -32601, "Unknown skill 'nonexistent_skill'", id="unknown-skill"),
        pytest.param(["get_products"], -32603, "message processing failed:", id="malformed-skill"),
    ],
)
def test_protocol_errors_do_not_register_unreturned_tasks(task_registry, skill, error_code, message_prefix):
    initial_ids = set(task_registry)
    with BareIntegrationEnv() as env:
        client = env.get_rest_client()
        # A real validation refusal returns a FAILED Task. It must remain
        # registered even when subsequent requests fail at the protocol layer.
        response = _send_skill(client, "create_media_buy")
        returned_task = _assert_registered_task(response, task_registry, TaskState.TASK_STATE_FAILED)
        retained_ids = initial_ids | {returned_task.id}

        for _ in range(2):
            response = _send_skill(client, skill)
            assert response.status_code == 200
            body = response.json()
            assert body["error"]["code"] == error_code
            assert body["error"]["message"].startswith(message_prefix)
            assert "result" not in body
            assert set(task_registry) == retained_ids
            assert task_registry[returned_task.id].task == returned_task


def test_submitted_task_remains_registered(task_registry):
    with BareIntegrationEnv() as env:
        tenant, principal = env.setup_default_data()
        env.setup_default_account()
        AdapterConfigFactory(tenant=tenant, mock_manual_approval_required=True)
        media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
        media_buy_id = media_buy.media_buy_id
        env._commit_factory_data()

        response = env.get_rest_client().post(
            _A2A_PATH,
            headers={**env.credential(), **_A2A_VERSION_HEADER},
            json=_a2a_message_send_body(
                "update_media_buy",
                {
                    "account": {"account_id": "acct_test"},
                    "idempotency_key": "task-lifecycle-submitted",
                    "media_buy_id": media_buy_id,
                    "end_time": "2026-12-01T00:00:00Z",
                },
            ),
        )
        _assert_registered_task(response, task_registry, TaskState.TASK_STATE_SUBMITTED)
