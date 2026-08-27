"""TDD RED for salesagent-nx8jp.8 — the signing session closes BEFORE the outbound POST.

Core Invariant under test, quoted from the production docstring that already claims it
(``signing_repo``, ``src/core/signing/webhook_sender_factory.py:656``):

    The session opened here lives for the key read and closes with the block, so it is
    never held across a delivery.

The composition in :func:`adcp_webhook_sender` breaks that claim. It enters the
repository context on an ``ExitStack`` (:704-705) and ``yield``s from INSIDE the block
(:706-712), so the checked-out session survives for the whole of its caller's
``async with`` body. That caller is :func:`deliver_adcp_webhook` (:732) and the body
holds exactly one statement — ``await sender.send_raw(...)``, the outbound POST
(:733-738). ``_TIMEOUT_SECONDS`` is 10.0 (:108), so a receiver that stops answering
pins a pooled connection for ten seconds per attempt, on a URL the BUYER supplied.

**Why this is an integration test and not a BDD scenario.** The behavior is the
lifetime of a database connection relative to an outbound socket write — an internal
ordering that produces no observable difference on any AdCP wire. Nothing about the
request, the response, the signature or the persisted state changes when it is fixed
(the delivery path is read-only on that session: ``canonical_origin``, ``active_at``,
``publishable_at``, with the repository's only writers — ``create_from_keypair`` and
``revoke`` — off this path). No cross-transport scenario can therefore reach it, and
the lane's own Grader clause names an integration test. Stated here so the choice is
on the record rather than implied.

**The delivery driven is real.** One tenant, one signing key minted through production
(``provision_signing_key``), the real ``signing_repo`` opening its own real session on
the real engine, the real SDK ``WebhookSender``, real RFC 9421 signing. Only the SOCKET
is replaced, through this project's one capture point
(``tests.helpers.webhook_wire.capture_outbound_webhooks``) — the network is the true
external here, and the capture is what lets a probe run AT the moment of the POST.

**Two legs, and neither may pass vacuously.**

* the ORDER the three events happen in, asserted as an exact sequence;
* the POOL, read at the instant the POST fires: how many connections are checked out
  in excess of what was checked out before the delivery began. That is the resource
  cost the lane exists to remove, graded directly rather than by proxy, and it is what
  would catch a "fix" that reorders the probe marks while still pinning a connection.

Three controls keep both legs honest, because each leg is trivially satisfiable by a
run in which nothing happened:

1. the probed ``signing_repo`` must be ENTERED, because a run that resolves a sender
   WITHOUT opening a repository satisfies both legs for free: it appends nothing to the
   sequence, and it reads a checkout delta of 0 at the POST because it never checked
   anything out. Measured under mutation, not assumed — building the sender with
   ``repo=None`` records ``pool_checkouts_at_post=[0]`` and reddens on this control
   alone.

   This control does NOT guard the 60-second ``(tenant_id, kid)`` provider cache, and
   cannot: ``_resolve_cached`` calls ``_select_row`` at
   ``src/core/signing/provider.py:293`` BEFORE consulting the cache at ``:296``, and
   ``adcp_webhook_sender`` enters ``signing_repo`` unconditionally, so a cache hit still
   opens and reads the repository. That cache is a real order-dependence hazard for
   deterministic-kid signing tests, which is why the kid below is unique per run and the
   cache is cleared around each test through production's own
   ``clear_signing_provider_cache`` — hygiene against a known flake, not this control.
2. exactly one POST must have gone out, and it must have been ACCEPTED — an exception
   thrown before the POST would leave a repo-open/repo-close pair whose order is
   trivially "correct".
3. the POST must carry an RFC 9421 signature naming THIS tenant's kid, parsed by the
   SDK's own structured-field parser. That is the proof the repository was genuinely
   READ on the session under measurement (origin, posture and key row all come off it),
   without which "no connection checked out at POST time" would be true of a delivery
   that never touched the database at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NamedTuple
from unittest.mock import patch
from uuid import uuid4

import pytest

from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import deployment_kek, just_after_provisioning, provision_key, signing_key_repo
from tests.helpers.webhook_wire import CapturedWebhook, capture_outbound_webhooks, signature_input_params

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT_ID = "tenant_webhook_session_lifetime"

#: DOTTED, so ``canonical_agent_url`` derives an ``https://`` origin for it. Load-bearing:
#: #1291 D1 put the publishability gate on the ONE posture object the sender reads, so on
#: the default integration host ``webhook_signing.supported`` is False, the RFC 9421 arm is
#: dropped, and control 3 below could never hold. Mirrors ``_AGENT_HOST`` in
#: ``tests/integration/test_webhook_signing_boundary.py``.
_AGENT_HOST = "seller-webhook-session-lifetime.example.com"

_WEBHOOK_URL = "https://buyer.example.com/adcp/notifications"

#: The three marks appended to ONE shared sequence. Named constants rather than bare
#: strings so the expected sequence in the assertion reads as the invariant it encodes.
REPO_OPENED = "signing-session:opened"
REPO_CLOSED = "signing-session:closed"
POSTED = "outbound-post:sent"

#: The sequence the invariant demands: open, close, THEN deliver.
EXPECTED_ORDER = [REPO_OPENED, REPO_CLOSED, POSTED]


class _Probe(NamedTuple):
    """What one probed delivery recorded."""

    #: Every event in the order it happened — see :data:`EXPECTED_ORDER`.
    order: list[str]
    #: Connections checked out of the engine pool at the instant of each POST.
    pool_checkouts_at_post: list[int]
    #: The captured POSTs themselves, as the receiving socket would have seen them.
    captured: list[CapturedWebhook]


@pytest.fixture
def signing_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[BareIntegrationEnv]:
    """A tenant env that can mint a real key, with the provider cache isolated.

    ``deployment_kek`` first: a ``db:`` mint REFUSES without the deployment KEK, so
    without it this test would fail on provisioning rather than on the ordering it
    grades. The cache is cleared on the way in AND out through production's own
    ``clear_signing_provider_cache`` (written for exactly this), because the
    ``(tenant_id, kid)`` entries outlive the per-test database.
    """
    from src.core.signing.provider import clear_signing_provider_cache

    clear_signing_provider_cache()
    with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id=_TENANT_ID) as env:
        yield env
    clear_signing_provider_cache()


def _seed_tenant_with_key(env: BareIntegrationEnv) -> str:
    """Create the tenant and mint its signing key through production; return the kid.

    COMMITTED, not merely flushed: ``signing_repo`` opens its OWN session, and an
    uncommitted key row is invisible to it. Without the commit the RFC 9421 arm would be
    dropped for want of a key and control 3 would fail — which is the honest failure, but
    not the one this module exists to produce.

    The kid is unique per run so the 60-second provider cache cannot serve this test an
    entry minted by an earlier one against a database that no longer exists.
    """
    from tests.factories import TenantFactory

    tenant = TenantFactory(tenant_id=_TENANT_ID, virtual_host=_AGENT_HOST)
    kid = f"webhook-session-lifetime-{uuid4().hex[:12]}"

    repo = signing_key_repo(env, tenant.tenant_id)
    provision_key(repo, tenant.tenant_id, kid, alg="ed25519")
    env.get_session().commit()
    return kid


@contextmanager
def _session_lifetime_probe() -> Iterator[_Probe]:
    """Wrap ``signing_repo`` and the outbound socket with probes on ONE sequence.

    ``signing_repo`` is patched on the module that RESOLVES it as a global
    (``webhook_sender_factory``:705) — the interception point — and DELEGATES to the real
    one, so the session under measurement is a real session on the real engine and its
    close is a real close. The socket probe runs from inside
    ``capture_outbound_webhooks``' responder, which the stub calls at the moment the
    request would have been written, so the pool reading is taken DURING the POST rather
    than inferred afterwards.
    """
    from src.core.database.database_session import get_pool_status
    from src.core.signing import webhook_sender_factory as factory

    order: list[str] = []
    pool_checkouts: list[int] = []
    real_signing_repo = factory.signing_repo

    @contextmanager
    def _probed_signing_repo(tenant_id: str | None) -> Iterator[Any]:
        order.append(REPO_OPENED)
        with real_signing_repo(tenant_id) as repo:
            yield repo
        order.append(REPO_CLOSED)

    def _at_post(_captured: CapturedWebhook) -> tuple[int, bytes | None]:
        order.append(POSTED)
        pool_checkouts.append(get_pool_status()["checked_out"])
        return 200, None

    with patch.object(factory, "signing_repo", _probed_signing_repo):
        with capture_outbound_webhooks(responder=_at_post) as captured:
            yield _Probe(order=order, pool_checkouts_at_post=pool_checkouts, captured=captured)


def _notification_payload() -> dict[str, Any]:
    return {
        "adcp_version": "3.1.1",
        "notification_type": "media_buy_status",
        "media_buy_id": "mb_webhook_session_lifetime",
    }


class TestSigningSessionClosesBeforeTheOutboundPost:
    """``adcp_webhook_sender`` must not hold its repository session across the delivery."""

    def test_signing_session_is_closed_before_the_webhook_leaves(self, integration_db, signing_env) -> None:
        """One real, signed delivery: open, close, THEN POST — and no pinned connection.

        ``config=None`` selects the RFC 9421 arm (``legacy_auth_mode`` returns ``None``
        for a missing registration), and ``client=None`` is what the two synchronous
        senders pass, so the SDK owns its own client — the ordinary delivery shape.
        """
        from src.core.database.database_session import get_pool_status
        from src.core.signing.webhook_sender_factory import deliver_adcp_webhook

        kid = _seed_tenant_with_key(signing_env)

        with _session_lifetime_probe() as probe:
            checked_out_before = get_pool_status()["checked_out"]
            result = asyncio.run(
                deliver_adcp_webhook(
                    url=_WEBHOOK_URL,
                    payload=_notification_payload(),
                    idempotency_key="idem-webhook-session-lifetime-1",
                    config=None,
                    tenant_id=_TENANT_ID,
                    now=just_after_provisioning(),
                )
            )

        # -- Control 1: the probe was ENTERED, so neither leg below is vacuous. --------
        assert REPO_OPENED in probe.order, (
            "the probed signing_repo was never entered, so the recorded sequence "
            f"{probe.order!r} says nothing about when its session closed. Production resolved a "
            "sender without opening a repository at all — check the tenant seeding and the patch "
            "target (webhook_sender_factory resolves `signing_repo` as a module global)"
        )

        # -- Control 2: exactly one delivery went out, and the receiver accepted it. ----
        assert len(probe.captured) == 1, (
            f"expected exactly 1 webhook POSTed to {_WEBHOOK_URL}, got {len(probe.captured)} — "
            "an ordering assertion over a delivery that never happened grades nothing"
        )
        assert result.status_code == 200, (
            f"the probed delivery did not land: HTTP {result.status_code} from {result.url} — "
            f"body {result.response_body!r}"
        )

        # -- Control 3: it was really signed with THIS tenant's key, off THIS session. --
        assert signature_input_params(probe.captured[0])["keyid"] == kid, (
            "the delivery did not carry an RFC 9421 signature naming this tenant's key, so the "
            "repository was not read on the session under measurement and 'no connection checked "
            f"out' would hold for a delivery that never touched the database. Signature-Input "
            f"params: {signature_input_params(probe.captured[0])!r}"
        )

        # -- Leg 1: the ordering the invariant demands. --------------------------------
        assert probe.order == EXPECTED_ORDER, (
            "the signing repository's session was still open when the outbound POST went out. "
            f"Expected {EXPECTED_ORDER!r}, got {probe.order!r}. adcp_webhook_sender yields from "
            "INSIDE its repository block, so the session stays checked out for the whole of "
            "deliver_adcp_webhook's `async with` body — whose one statement is the POST to a "
            "buyer-supplied URL with a 10.0s timeout. signing_repo's own docstring "
            "(webhook_sender_factory.py:656) says that session 'is never held across a delivery'"
        )

        # -- Leg 2: the resource cost itself, read AT the POST. ------------------------
        assert probe.pool_checkouts_at_post[0] - checked_out_before == 0, (
            "a pooled database connection was still checked out while the outbound POST was in "
            f"flight: {checked_out_before} checked out before the delivery, "
            f"{probe.pool_checkouts_at_post[0]} at the instant of the POST. That connection is "
            "pinned for as long as the receiver takes to answer (up to _TIMEOUT_SECONDS = 10.0), "
            "per attempt, on every one of the three delivery paths"
        )
