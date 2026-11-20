"""After a KEK loss, the signing-keys page and the setup checklist give the runbook's answer (#2288).

A tenant whose active key was minted under a key encryption key (KEK) this deployment no
longer holds publishes that key but cannot sign with it. ``docs/operations/signing-key-runbook.md``
says the recovery for that state is "restore the KEK. Do not re-mint." Before #2288 the two
admin surfaces disagreed with it:

* the signing-keys page badged the active key ``signing`` from the ``active_at`` time window
  alone, without checking that the private half can be opened;
* the setup checklist read ``publishes and not signs`` as "revoked or not yet active —
  provision a replacement", which is the one action the runbook says makes this state worse.

Both now read ``signing_key_backed``. Each test below seeds the keys with
``SigningKeyFactory`` (whose PEM is encrypted under ``TEST_KEK_PASSPHRASE``) and asserts on
what the operator is shown: the rendered page and the checklist task.

The KEK change is simulated the way ``test_signing_key_kek_mismatch.py`` does it: the
deployment's KEK variable holds a different passphrase from the one the row was encrypted
under.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.services.setup_checklist_service import SetupChecklistService
from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import TEST_KEK_VARIABLE, deployment_kek

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_RUNBOOK_ANCHOR = "docs/operations/signing-key-runbook.md#a-key-minted-under-a-kek-that-was-later-unset"
_REPLACEMENT_ADVICE = "provision a replacement"
_KEK_ADVICE = "Restore the KEK. Do not re-mint."


@pytest.fixture
def seeded(integration_db, request, monkeypatch) -> Any:
    """A tenant with no keys yet, the test KEK configured, and a key seeder.

    One tenant per test: the integration database is not rolled back between tests.
    """
    from tests.factories import SigningKeyFactory, TenantFactory

    slug = re.sub(r"[^a-z0-9]", "", request.node.name.lower())[-20:]
    tenant_id = f"keklost_{slug}"

    with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id=tenant_id) as env:
        tenant = TenantFactory(tenant_id=tenant_id, subdomain=f"keklost-{slug}")

        def seed(kid_suffix: str, **overrides: Any) -> str:
            kid = f"adcp-{slug}-{kid_suffix}"
            SigningKeyFactory(tenant=tenant, kid=kid, **overrides)
            env.get_session().commit()
            return kid

        yield tenant_id, seed


def _lose_the_kek(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the deployment KEK at a passphrase the seeded rows were not encrypted under."""
    monkeypatch.setenv(TEST_KEK_VARIABLE, "a-different-kek-than-the-key-was-minted-under")


def _signing_key_task(tenant_id: str) -> dict[str, Any]:
    """The checklist's signing-key task, from the per-tenant path."""
    status = SetupChecklistService(tenant_id).get_setup_status()
    (task,) = [task for task in status["optional"] if task["key"] == "signing_key"]
    return task


def _bulk_signing_key_task(tenant_id: str) -> dict[str, Any]:
    """The same task from the bulk path, which builds it from pre-fetched data."""
    SetupChecklistService.clear_cache(tenant_id)
    status = SetupChecklistService.get_bulk_setup_status([tenant_id])[tenant_id]
    (task,) = [task for task in status["optional"] if task["key"] == "signing_key"]
    return task


def _page(client: Any, tenant_id: str) -> str:
    response = client.get(f"/tenant/{tenant_id}/signing-keys/")
    assert response.status_code == 200, f"signing-keys page answered {response.status_code}"
    return response.get_data(as_text=True)


def _status_cell(page: str, kid: str) -> str:
    """The text of the Status cell on *kid*'s row, tags stripped."""
    row = re.search(rf"<tr>\s*<td class=\"kid\">{re.escape(kid)}</td>.*?</tr>", page, re.DOTALL)
    assert row is not None, f"the page lists no row for {kid!r}"
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row.group(0), re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " ", cells[3]).split())


class TestActiveKeyThatCannotBeDecrypted:
    """KEK lost or changed: the key is in its active window but nobody can open it."""

    def test_checklist_says_restore_the_kek_not_provision_a_replacement(self, seeded, monkeypatch):
        tenant_id, seed = seeded
        kid = seed("stranded")
        _lose_the_kek(monkeypatch)

        for task in (_signing_key_task(tenant_id), _bulk_signing_key_task(tenant_id)):
            assert task["is_complete"] is False
            assert _KEK_ADVICE in task["details"], task["details"]
            assert kid in task["details"], task["details"]
            assert _REPLACEMENT_ADVICE not in task["details"], task["details"]

    def test_page_does_not_badge_the_key_as_signing(self, seeded, monkeypatch, authenticated_admin_client):
        tenant_id, seed = seeded
        kid = seed("stranded")
        _lose_the_kek(monkeypatch)

        page = _page(authenticated_admin_client, tenant_id)

        assert _status_cell(page, kid) == "active, cannot be decrypted"
        assert _KEK_ADVICE in page
        assert _RUNBOOK_ANCHOR in page


class TestRevokedOrNotYetActiveKey:
    """A published key that is outside its active window still gets the replacement advice."""

    def test_revoked_key_in_its_grace_window(self, seeded, authenticated_admin_client):
        tenant_id, seed = seeded
        kid = seed("revoked", revoked_at=datetime.now(UTC) - timedelta(seconds=5))

        task = _signing_key_task(tenant_id)
        assert task["is_complete"] is False
        assert task["details"] == "Published key is revoked or not yet active — provision a replacement"
        assert _bulk_signing_key_task(tenant_id)["details"] == task["details"]

        page = _page(authenticated_admin_client, tenant_id)
        assert _status_cell(page, kid).startswith("revoked ")
        assert _KEK_ADVICE not in page

    def test_key_not_yet_active(self, seeded, authenticated_admin_client):
        tenant_id, seed = seeded
        kid = seed("future", not_before=datetime.now(UTC) + timedelta(days=1))

        task = _signing_key_task(tenant_id)
        assert task["is_complete"] is False
        assert task["details"] == "Published key is revoked or not yet active — provision a replacement"
        assert _bulk_signing_key_task(tenant_id)["details"] == task["details"]

        page = _page(authenticated_admin_client, tenant_id)
        assert _status_cell(page, kid) == "published"
        assert _KEK_ADVICE not in page


class TestHealthyKey:
    """Control: an active key the configured KEK opens is unchanged on both surfaces."""

    def test_checklist_and_page_report_signing(self, seeded, authenticated_admin_client):
        tenant_id, seed = seeded
        kid = seed("healthy")

        task = _signing_key_task(tenant_id)
        assert task["is_complete"] is True
        assert task["details"] == "Signing key provisioned and published"
        assert _bulk_signing_key_task(tenant_id)["details"] == task["details"]

        page = _page(authenticated_admin_client, tenant_id)
        assert _status_cell(page, kid) == "signing"
        assert _KEK_ADVICE not in page
