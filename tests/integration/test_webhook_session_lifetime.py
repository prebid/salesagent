"""The signing session closes BEFORE the outbound POST (#1757, salesagent-nx8jp.8).

Core Invariant under test, quoted from the production docstring that claims it
(``signing_repo``, ``src/core/signing/outbound.py``):

    The session opened here lives for the key read and closes with the block, so it is
    never held across a delivery.

The claim used to be false. The old composition entered the repository on an
``ExitStack`` and ``yield``ed from INSIDE the block, so the checked-out session survived
for the whole of its caller's ``async with`` body — whose one statement was the outbound
POST, to a buyer-supplied URL, with a 10.0s timeout. A receiver that stopped answering
pinned a pooled connection for ten seconds per attempt.

The seam this repository now delivers on removes that shape TWICE over, which is why the
test survives the rewrite rather than retiring with the module it was written against:

* :func:`src.core.signing.outbound.signing_repo` takes no ``repo=`` donation, so no caller
  can hand it a lifetime that outlives the read;
* :func:`src.core.signing.outbound.delivery_signer_for_tenant` is an ordinary function, not
  a context manager — it consumes the repository EAGERLY (origin, posture, key material)
  and hands back a strategy holding key material — so every sender's shape is
  resolve-then-deliver, and the delivery call cannot be nested inside the session.

Neither of those is enforced by a structural guard, and both are one edit away from
regressing: a sender that wraps its ``deliver_webhook`` call in ``with signing_repo(...)``,
or a ``delivery_signer_for_tenant`` that returns something lazily bound to the session,
reinstates the pin without changing a single signature. That is what this module grades.

**Why this is an integration test and not a BDD scenario.** The behavior is the lifetime
of a database connection relative to an outbound socket write — an internal ordering that
produces no observable difference on any AdCP wire. Nothing about the request, the
response, the signature or the persisted state changes when it is fixed (the delivery path
is read-only on that session: ``canonical_origin``, ``active_at``, ``publishable_at``, with
the repository's only writers — ``create_from_keypair`` and ``revoke`` — off this path). No
cross-transport scenario can therefore reach it, and the lane's own Grader clause names an
integration test. Stated here so the choice is on the record rather than implied.

**The delivery driven is real.** One tenant, one signing key minted through production
(``provision_signing_key``), the real ``signing_repo`` opening its own real session on the
real engine, the real egress seam (``deliver_webhook`` -> ``outbound_http.send``), real RFC
9421 signing through the seam's per-attempt ``sign=`` hook. Only the SOCKET is replaced,
through this project's one capture point
(``tests.helpers.webhook_wire.capture_outbound_webhooks``) — the network is the true
external here, and the capture is what lets a probe run AT the moment of the POST.

**Which sender.** :func:`src.services.order_approval_service._send_approval_webhook`, one
of the three production senders that compose ``delivery_signer_for_tenant`` +
``deliver_webhook``, and the one whose frame holds NO session of its own: its registration
lookup (``_lookup_approval_webhook_auth``) opens and closes a session before the signer is
resolved, so the only connection that could be checked out when the POST fires is the
signing one. The other two are deliberately not driven here:
``webhook_delivery_service.send_delivery_webhook`` holds its own ``db`` session across
``_deliver_to_config`` (a real and separate question, not this one — measuring the union
would make this module red for a defect it does not describe), and
``protocol_webhook_service`` needs an async task context that adds scaffolding without
adding grading power. One sender is enough because the composition under test lives in
``delivery_signer_for_tenant``, which all three call.

**Two legs, and neither may pass vacuously.**

* the ORDER the three events happen in, asserted as an exact sequence;
* the POOL, read at the instant the POST fires: how many connections are checked out in
  excess of what was checked out before the delivery began. That is the resource cost the
  lane exists to remove, graded directly rather than by proxy, and it is what would catch
  a "fix" that reorders the probe marks while still pinning a connection.

Three controls keep both legs honest, because each leg is trivially satisfiable by a run in
which nothing happened:

1. the probed ``signing_repo`` must be ENTERED, because a run that resolves a signer
   WITHOUT opening a repository satisfies both legs for free: it appends nothing to the
   sequence, and it reads a checkout delta of 0 at the POST because it never checked
   anything out. That run is reachable by one mutation — a ``tenant_id`` of ``None``, for
   which ``signing_repo`` yields ``None`` without touching ``get_db_session`` at all — so
   the control guards a live failure mode rather than a hypothetical one.

   This control does NOT guard the 60-second ``(tenant_id, kid)`` provider cache, and
   cannot: ``_resolve_cached`` calls ``_select_row`` BEFORE consulting the cache, and
   ``delivery_signer_for_tenant`` enters ``signing_repo`` unconditionally, so a cache hit
   still opens and reads the repository. That cache is a real order-dependence hazard for
   deterministic-kid signing tests, which is why the kid below is unique per run and the
   cache is cleared around each test through production's own
   ``clear_signing_provider_cache`` — hygiene against a known flake, not this control.
2. exactly one POST must have gone out, and the sender must report it DELIVERED — an
   exception or a refusal before the POST would leave a repo-open/repo-close pair whose
   order is trivially "correct".
3. the POST must carry an RFC 9421 signature naming THIS tenant's kid, parsed by the SDK's
   own structured-field parser. That is the proof the repository was genuinely READ on the
   session under measurement (origin, posture and key row all come off it), without which
   "no connection checked out at POST time" would be true of a delivery that never touched
   the database at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NamedTuple
from unittest.mock import patch
from uuid import uuid4

import pytest

from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import deployment_kek, provision_key, signing_key_repo
from tests.helpers.webhook_wire import CapturedWebhook, capture_outbound_webhooks, signature_input_params

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT_ID = "tenant_webhook_session_lifetime"
_PRINCIPAL_ID = "principal_webhook_session_lifetime"
_MEDIA_BUY_ID = "mb_webhook_session_lifetime"

#: DOTTED, so ``canonical_agent_url`` derives an ``https://`` origin for it. Load-bearing:
#: #1291 D1 put the publishability gate on the ONE posture object the delivery signer
#: reads, so on the default integration host ``webhook_signing.supported`` is False, the
#: RFC 9421 arm is dropped, and control 3 below could never hold. Mirrors ``_AGENT_HOST``
#: in ``tests/integration/test_webhook_signing_boundary.py``.
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

    ``deployment_kek`` first: a ``db:`` mint REFUSES without the deployment KEK, so without
    it this test would fail on provisioning rather than on the ordering it grades. The
    cache is cleared on the way in AND out through production's own
    ``clear_signing_provider_cache`` (written for exactly this), because the
    ``(tenant_id, kid)`` entries outlive the per-test database.
    """
    from src.core.signing.provider import clear_signing_provider_cache

    clear_signing_provider_cache()
    with (
        deployment_kek(monkeypatch),
        BareIntegrationEnv(tenant_id=_TENANT_ID, principal_id=_PRINCIPAL_ID) as env,
    ):
        yield env
    clear_signing_provider_cache()


def _seed_tenant_with_key(env: BareIntegrationEnv) -> str:
    """Create the tenant/principal, mint their signing key through production, return the kid.

    COMMITTED, not merely flushed: ``signing_repo`` opens its OWN session, and an
    uncommitted key row is invisible to it. Without the commit the RFC 9421 arm would be
    dropped for want of a key and control 3 would fail — which is the honest failure, but
    not the one this module exists to produce.

    The kid is unique per run so the 60-second provider cache cannot serve this test an
    entry minted by an earlier one against a database that no longer exists.
    """
    from tests.factories import PrincipalFactory, TenantFactory

    tenant = TenantFactory(tenant_id=_TENANT_ID, virtual_host=_AGENT_HOST)
    PrincipalFactory(tenant=tenant, principal_id=_PRINCIPAL_ID)
    kid = f"webhook-session-lifetime-{uuid4().hex[:12]}"

    repo = signing_key_repo(env, tenant.tenant_id)
    provision_key(repo, tenant.tenant_id, kid, alg="ed25519")
    env.get_session().commit()
    return kid


@contextmanager
def _session_lifetime_probe() -> Iterator[_Probe]:
    """Wrap ``signing_repo`` and the outbound socket with probes on ONE sequence.

    ``signing_repo`` is patched on the module that RESOLVES it as a global
    (``src.core.signing.outbound``, where ``delivery_signer_for_tenant`` names it at call
    time) — the interception point — and DELEGATES to the real one, so the session under
    measurement is a real session on the real engine and its close is a real close.

    The socket probe runs from inside ``capture_outbound_webhooks``' responder, which the
    stub calls at the moment the request would have been written, so the pool reading is
    taken DURING the POST rather than inferred afterwards.
    """
    from src.core.database.database_session import get_pool_status
    from src.core.signing import outbound

    order: list[str] = []
    pool_checkouts: list[int] = []
    real_signing_repo = outbound.signing_repo

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

    with patch.object(outbound, "signing_repo", _probed_signing_repo):
        with capture_outbound_webhooks(responder=_at_post) as captured:
            yield _Probe(order=order, pool_checkouts_at_post=pool_checkouts, captured=captured)


class TestSigningSessionClosesBeforeTheOutboundPost:
    """A webhook sender must not hold its signing session across the delivery."""

    def test_signing_session_is_closed_before_the_webhook_leaves(self, integration_db, signing_env) -> None:
        """One real, signed delivery: open, close, THEN POST — and no pinned connection.

        No ``PushNotificationConfig`` row is registered for this URL, so
        ``_lookup_approval_webhook_auth`` resolves ``(None, None, None)`` and
        ``_authentication_or_refusal`` reports "no ``authentication`` block" — the pinned
        schema's own selector for the RFC 9421 profile (security.mdx @ v3.1.1 :1424). That
        is the arm that consumes the signer, and therefore the only arm on which this
        module's subject exists at all.
        """
        from src.core.database.database_session import get_pool_status
        from src.services.order_approval_service import _send_approval_webhook

        kid = _seed_tenant_with_key(signing_env)

        with _session_lifetime_probe() as probe:
            checked_out_before = get_pool_status()["checked_out"]
            outcome = _send_approval_webhook(
                webhook_url=_WEBHOOK_URL,
                tenant_id=_TENANT_ID,
                principal_id=_PRINCIPAL_ID,
                media_buy_id=_MEDIA_BUY_ID,
                status="approved",
                message="Order approved successfully",
            )

        # -- Control 1: the probe was ENTERED, so neither leg below is vacuous. --------
        assert REPO_OPENED in probe.order, (
            "the probed signing_repo was never entered, so the recorded sequence "
            f"{probe.order!r} says nothing about when its session closed. Production resolved a "
            "signer without opening a repository at all — check the tenant seeding and the patch "
            "target (delivery_signer_for_tenant resolves `signing_repo` as a module global of "
            "src.core.signing.outbound)"
        )

        # -- Control 2: exactly one delivery went out, and the sender says it landed. ---
        assert len(probe.captured) == 1, (
            f"expected exactly 1 webhook POSTed to {_WEBHOOK_URL}, got {len(probe.captured)} — "
            "an ordering assertion over a delivery that never happened grades nothing"
        )
        assert outcome is not None and outcome.kind == "delivered", (
            "the probed delivery did not land: the sender reported "
            f"{outcome if outcome is None else outcome.kind!r} — "
            f"{outcome if outcome is None else outcome.detail!r}"
        )

        # -- Control 3: it was really signed with THIS tenant's key, off THIS session. --
        assert signature_input_params(probe.captured[0])["keyid"] == kid, (
            "the delivery did not carry an RFC 9421 signature naming this tenant's key, so the "
            "repository was not read on the session under measurement and 'no connection checked "
            "out' would hold for a delivery that never touched the database. Signature-Input "
            f"params: {signature_input_params(probe.captured[0])!r}"
        )

        # -- Leg 1: the ordering the invariant demands. --------------------------------
        assert probe.order == EXPECTED_ORDER, (
            "the signing repository's session was still open when the outbound POST went out. "
            f"Expected {EXPECTED_ORDER!r}, got {probe.order!r}. delivery_signer_for_tenant must "
            "consume the repository EAGERLY and return after its `with` block closes, and every "
            "sender must call it BEFORE handing the delivery to deliver_webhook — otherwise the "
            "session stays checked out for a POST to a buyer-supplied URL with a 10.0s timeout "
            "per attempt. signing_repo's own docstring (src/core/signing/outbound.py) says that "
            "session 'is never held across a delivery'"
        )

        # -- Leg 2: the resource cost itself, read AT the POST. ------------------------
        assert probe.pool_checkouts_at_post[0] - checked_out_before == 0, (
            "a pooled database connection was still checked out while the outbound POST was in "
            f"flight: {checked_out_before} checked out before the delivery, "
            f"{probe.pool_checkouts_at_post[0]} at the instant of the POST. That connection is "
            "pinned for as long as the receiver takes to answer (10.0s per attempt, up to three "
            "attempts), on every sender that composes delivery_signer_for_tenant with the egress "
            "seam"
        )
