"""security.mdx @ v3.1.1 :1464 — a seller MUST log every request that ARRIVES carrying a
non-empty ``authentication`` block (``salesagent-3ajkg.8``).

WHY AN INTEGRATION TEST AND NOT A BDD SCENARIO, stated because this repository grades
behavior across transports by default and departing from that needs a reason rather than a
preference.

The observable is a log RECORD, and a log record is process-local. The ``e2e_rest`` leg runs
against a server in another container, and there is no carrier that returns its records to the
test process: the only two ``FileHandler``s in ``src/`` sit on ``adcp.audit``
(``src/core/audit_logger.py``), ``/metrics`` exposes counters rather than records, and the
in-network runner holds no docker socket. A cross-transport scenario asserting on ``caplog``
would therefore PASS VACUOUSLY on that leg — seeing nothing and reporting nothing wrong —
which is the cross-process blindness lane ``.10`` removed from the metrics oracle, reproduced
in the ticket that exists to close a spec gap.

So the duty is graded where it is observable, and the e2e leg is left ungraded ON THE RECORD
rather than by an assertion that cannot fail there.

WHAT THIS GRADES that the escalation path cannot. ``_credentials_force_a_signature``
(``request_verifier_middleware.py``) reads ``resolved.signature_forced and posture.supported``,
so it is unreachable for a seller declaring ``supported: false`` — exactly the population
:1465 sends to the log-and-alarm posture rather than exempting. The second case below is red
against any implementation placed there, which is what makes the seam choice graded rather
than asserted.
"""

from __future__ import annotations

import logging

import pytest

from tests.helpers.signing import (
    SIGNING_PRINCIPAL_ID,
    SIGNING_TENANT_ID,
    bucketed_declaration,
    declared_posture,
    seed_principal,
    unsupported,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_LOGGER = "src.core.signing.request_verifier_middleware"

#: 32-char floor: ``Authentication.credentials`` carries ``MinLen=32`` on the pinned model, so
#: a shorter placeholder is refused as a validation error and the request never reaches the
#: resolver — the scenario would then pass for the wrong reason.
_CREDENTIAL = "harness-webhook-shared-secret-0123456789"


def _body_with_authentication() -> dict:
    return {
        "creatives": [],
        "push_notification_config": {
            "url": "https://buyer.example/webhooks/adcp",
            "authentication": {"schemes": ["HMAC-SHA256"], "credentials": _CREDENTIAL},
        },
    }


@pytest.mark.parametrize(
    ("declaration", "case"),
    [
        (bucketed_declaration("required", "sync_creatives"), "supported-true"),
        (unsupported(), "supported-false-by-declaration"),
    ],
)
def test_an_arriving_credential_block_is_logged_whatever_the_declared_posture(
    integration_db, caplog, declaration, case
) -> None:
    """:1464 is unqualified by posture, so both rows must log.

    ``supported-false-by-declaration`` is the discriminating row: a log placed at
    ``_credentials_force_a_signature`` never fires for it, because that predicate reads
    ``resolved.signature_forced and posture.supported``.
    """
    from starlette.testclient import TestClient

    from src.app import app
    from tests.harness._base import BareIntegrationEnv

    with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
        token = seed_principal(env)
        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.WARNING, logger=_LOGGER), declared_posture(**declaration):
            client.post(
                "/api/v1/creatives/sync",
                json=_body_with_authentication(),
                headers={"Content-Type": "application/json", "x-adcp-auth": token},
            )

    matching = [r for r in caplog.records if "non-empty webhook authentication block" in r.getMessage()]
    assert len(matching) == 1, (
        f"[{case}] security.mdx @ v3.1.1 :1464 requires the seller to log every request arriving "
        f"with a non-empty authentication block; {len(matching)} such records were emitted. Zero "
        f"means the duty is unimplemented for this posture — and a log placed at "
        f"_credentials_force_a_signature reads 'resolved.signature_forced and posture.supported', "
        f"which cannot fire for a seller declaring supported: false."
    )
