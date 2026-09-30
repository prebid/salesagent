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

WHERE THE DUTY LIVES NOW. This was written against the ASGI verifier middleware, which read
the raw body and sent its own 401. #1721 deleted that seam: a transport hands ``serve`` the
payload, ``invoke_tool`` holds the VALIDATED request, and the ONE reader of a credential is
``_resolve_identity`` — handed
``SignatureSubject(registers_credentials=registers_webhook_credentials(req))`` and calling
``verify_inbound_signature``. A request therefore ARRIVES, in the sense :1464 means, at the
boundary, so the dispatch below goes through ``invoke_tool`` rather than through an HTTP
client and a middleware that no longer exists.

WHAT THIS GRADES that the escalation path cannot. The escalation predicate on this
architecture is ``_bucket_for`` (``src/core/signing/verifier.py``), which reads
``subject.registers_credentials and posture.supported`` — the same shape the middleware's
``_credentials_force_a_signature`` had, and unreachable for the same population: a seller
declaring ``supported: false``, which is exactly who :1465 sends to the log-and-alarm posture
rather than exempting. The second row below is red against any implementation placed there,
which is what makes the seam choice graded rather than asserted.

NOT YET DISCHARGED BY PRODUCTION. #1721's inbound port carries the escalation — the refusal
each row asserts as its control — but not the log, so this test is red until a record is
emitted for an arriving ``authentication`` block from the resolver path, independently of
``posture.supported``.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from adcp.types import AccountReference

from tests.helpers.log_capture import capture_logs
from tests.integration.test_creative_v3 import (
    ACCOUNT_ID,
    _headers,
    _make_creative_dict,
    _seed_account_for,
    _sync_creatives,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "tenant_cred_log"
PRINCIPAL_ID = "principal_cred_log"

#: The duty is "log", not "log from module X", so the record is matched on what it SAYS and
#: the capture is taken on the ``src`` package logger that every production module's
#: ``getLogger(__name__)`` descends from. Pinning the module would grade the implementation's
#: file rather than the obligation; ``caplog`` would instead pin the ROOT, which
#: ``tests.helpers.log_capture`` exists because suite-level handler reconfiguration makes
#: unreliable.
_PRODUCTION_LOGGER = "src"
_SAYS = "webhook authentication block"

#: 32-char floor: ``PushAuthentication.credentials`` carries ``min_length=32``, so a shorter
#: placeholder is refused as a validation error and the request never reaches the resolver —
#: the scenario would then pass for the wrong reason.
_CREDENTIAL = "harness-webhook-shared-secret-0123456789"

_SIGNING_REFUSAL = "request_signature_required"


def _push_config_with_authentication() -> dict[str, Any]:
    return {
        "url": "https://buyer.example/webhooks/adcp",
        "authentication": {"schemes": ["HMAC-SHA256"], "credentials": _CREDENTIAL},
    }


@pytest.fixture
def seeded(integration_db, bound_factory_session):
    """Seed a seller declaring *declaration*, and return the headers its buyer calls with.

    ``invoke_tool`` takes HEADERS and the resolver is their one reader, so what the posture and
    the caller are read off is the rows seeded here. The posture is passed at CREATION rather
    than assigned afterwards: these factories persist with ``sqlalchemy_session_persistence =
    "commit"``, so a later attribute write would stay pending in the test's session and the
    resolver — which opens its own — would read the row without it.

    The account is seeded because ``sync_creatives`` REQUIRES ``account`` on the pin, and a
    request naming an account its caller cannot reach is refused inside the resolver, which
    would decide the outcome before the duty under test could be observed.
    """
    from tests.factories import PrincipalFactory, TenantFactory

    def seed(declaration: dict[str, Any]) -> dict[str, str]:
        tenant = TenantFactory(
            tenant_id=TENANT_ID,
            subdomain="cred-log",
            ad_server="mock",
            approval_mode="auto-approve",
            capability_declarations={"request_signing": declaration},
        )
        PrincipalFactory(tenant=tenant, principal_id=PRINCIPAL_ID)
        _seed_account_for(TENANT_ID, (PRINCIPAL_ID,))
        return _headers(TENANT_ID, PRINCIPAL_ID)

    return seed


def _dispatched_error_code(headers: dict[str, str], *, key: str) -> str | None:
    """Dispatch one authenticated sync carrying webhook credentials; report how it ended.

    The boundary answers every failure with a response and raises ``AdcpFailure`` carrying it
    (tests/CLAUDE.md § "Error verification policy": assert on the wire, not on a reconstructed
    exception), so the code on that response is the whole outcome this test needs. ``None``
    means the call was served.
    """
    from src.core.exceptions import AdcpFailure

    try:
        _sync_creatives(
            creatives=[_make_creative_dict(creative_id="c_cred_log")],
            idempotency_key=key,
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            push_notification_config=_push_config_with_authentication(),
            headers=headers,
        )
    except AdcpFailure as failure:
        mirror = failure.response.adcp_error
        return None if mirror is None else str(mirror.code)
    return None


@pytest.mark.parametrize(
    ("declaration", "escalates", "case"),
    [
        ({"supported": True}, True, "supported-true"),
        ({"supported": False}, False, "supported-false-by-declaration"),
    ],
)
def test_an_arriving_credential_block_is_logged_whatever_the_declared_posture(
    seeded, declaration, escalates, case
) -> None:
    """:1464 is unqualified by posture, so both rows must log.

    ``supported-false-by-declaration`` is the discriminating row: a log placed at the
    escalation never fires for it, because ``_bucket_for`` reads
    ``subject.registers_credentials and posture.supported``.

    ``escalates`` is each row's POSITIVE CONTROL, not a second obligation. It asserts the row
    really is in the posture it claims — the escalation refuses the supported seller and does
    not refuse the unsupported one — so a run with the verifier off, which degrades every
    declaration to ``supported: false``, cannot quietly collapse the two rows into one. The
    refusal itself is graded by ``tests/unit/test_request_signature_composition_rule.py``.
    """
    headers = seeded(declaration)

    with capture_logs(_PRODUCTION_LOGGER, level=logging.WARNING) as captured:
        code = _dispatched_error_code(headers, key=f"cred-log-{case}")

    assert (code == _SIGNING_REFUSAL) is escalates, (
        f"[{case}] control failed: the webhook-credential escalation must fire iff the seller "
        f"declares supported: true, and this call ended with code={code!r}"
    )

    matching = [record for record in captured.records if _SAYS in record]
    assert len(matching) == 1, (
        f"[{case}] security.mdx @ v3.1.1 :1464 requires the seller to log every request arriving "
        f"with a non-empty authentication block; {len(matching)} such records were emitted. Zero "
        f"means the duty is unimplemented for this posture — and a log placed at the escalation "
        f"reads 'subject.registers_credentials and posture.supported' (_bucket_for, "
        f"src/core/signing/verifier.py), which cannot fire for a seller declaring supported: false."
    )
