"""E2E-4: the proof-of-control challenge is signed, conformant and echo-checked over real HTTP.

salesagent-mp53.4 (#1291 C2) — the sibling of ``tests/integration/test_notification_proof_challenge.py``
on a real socket. That module drives ``sync_accounts`` in-process and replaces the SOCKET
and DNS; this one changes nothing at all. The server container signs with a key it holds
under its own KEK, opens a real TLS connection to a real HTTPS receiver, and the receiver's
answer decides whether the subscriber is persisted active.

**Why an e2e module exists for a surface an integration module already grades.**
``NotificationProofService.prove`` refuses at five gates BEFORE any socket opens, and the
last two of them — the uncredentialed-legacy check and ``check_url_ssrf(url,
require_https=True)`` — cannot be exercised honestly with the socket replaced: an
in-process module answers those gates against a stub. On this stack they run for real
against a destination production accepts ON ITS OWN TERMS. That is the measured hole the
epic named: "the BDD harness replaces the prover with a MagicMock; the e2e path returns
before the POST" (``tests/harness/account_sync.py`` says so in its own error text —
``set_notification_proof_result(succeeds=True)`` has NO e2e realization, and the e2e
failure direction is realized by a reserved-TLD url, i.e. a refusal at gate 2).

**PROVES.**

1. The challenge POST that leaves the server container carries an RFC 9421 signature that
   verifies — over the exact BYTES on the wire, under the WEBHOOK profile tag, against a
   JWKS resolved from the ``seller_agent_url`` the challenge itself named.
2. Its body is EXACTLY the seven properties ``webhook-challenge.json`` requires, with no
   ``idempotency_key`` (the field ``WebhookSender.send_raw`` would have injected).
3. Activation depends on the ECHO. The SAME receiver, answering 2xx WITHOUT the echo,
   does not activate the subscriber; answering with the echo does.
4. Both fail-closed legs refuse BEFORE the POST — a keyless tenant, and a REST-registered
   subscriber — with zero captures on a receiver that would otherwise have captured.

**DOES NOT PROVE.** That the signature fails to verify under swapped key material, or
that the discovery chain is well formed — those are ``test_webhook_signature_e2e``'s
(E2E-3) negative control and discovery walk, and keeping them apart is what makes a
failure here attributable to the CHALLENGE path.

## The Core Invariant, and what it forbids this module from doing

*The challenge is either a real, RFC 9421-signed POST of the exact seven-field
``webhook.challenge`` document whose ECHO decides activation, or it is not sent at all —
and this module may only ever make production's own gates PASS, never bypass them.*

So nothing here patches ``check_url_ssrf``, ``is_reserved_tld_host`` or
``get_notification_proof_service``, and nothing reaches ``WebhookURLValidator.validate_for_testing``
(the delivery path's carve-out, which this path deliberately does not call). The receiver
is reachable because it genuinely satisfies both gates: ``webhooks.adcp-e2e.dev`` is a
normal delegable gTLD name, not an RFC 6761 special-use one, and it resolves into this
stack's own NON-private per-stack subnet. That the gate is still ARMED inside this stack
— 127.0.0.1, RFC1918, link-local and the metadata address all still refused — is graded
by ``tests/e2e/test_webhook_capture_egress_gate_e2e.py``, which is what keeps "we reached
it" distinguishable from "we disarmed it".

``webhooks.adcp.test`` would NOT work and is not a naming preference: AdCP 3.1.1 lists
``.test`` among the RFC 6761 names the SSRF path MUST refuse, and
``notification_proof_service`` applies ``is_reserved_tld_host`` unconditionally BEFORE the
SSRF check, so a ``.test`` receiver is refused before a socket opens.

## Why BOTH fail-closed legs point at the SAME receiver

Gate 3 ("this challenge cannot be signed") short-circuits BEFORE gate 5 (SSRF). A
fail-closed leg pointed at a plain-HTTP or unreachable receiver would still show zero
captures under a mutation that REMOVED the refusal under test, because control would fall
through to ``check_url_ssrf(url, require_https=True)`` and be refused there instead. Zero
captures is evidence only when the receiver WOULD have captured — which is why both legs
dial the same https origin the success leg proves is reachable.

## Mutations this module is built to fail under, and where each goes red

* remove the echo requirement from ``_response_proves_control`` (accept any 2xx) →
  red at the NO-ECHO leg, which would then activate;
* remove the ``signing is None`` refusal in ``prove`` → red at the KEYLESS leg (a
  challenge would be POSTed and captured). This is the mutation the architect review
  added as MEDIUM-2, and it only discriminates because that leg dials a reachable
  receiver;
* make ``_challenge_signing`` return a signing identity for ``protocol == "rest"`` →
  red at the REST leg, same way;
* drop the ``strategy.build_auth_headers`` call in ``send_signed_challenge`` → red at the
  three-headers assertion;
* let the body be built through ``WebhookSender.send_raw`` (which injects
  ``idempotency_key``) → red at the exact-seven-keys assertion.

## Spec, at the version this repo pins (AdCP 3.1.1 via ``adcp==6.6.0``)

``v3.1.1:docs/accounts/tasks/sync_accounts.mdx`` § "Endpoint proof of control":

* :207 — "The challenge POST itself MUST be signed with the seller's RFC 9421 webhook
  profile key even when the candidate config selects legacy delivery auth … The receiver
  MUST verify the RFC 9421 signature and MUST reject the challenge unless
  ``seller_agent_url``, ``delivery_auth``, and ``event_types`` match the pending
  registration."
* :209 — "The standard challenge is an HTTPS POST to the candidate ``url`` with a JSON
  body containing ``type``, ``challenge``, ``account_id``, ``subscriber_id``,
  ``seller_agent_url``, ``delivery_auth``, and ``event_types``." Those SEVEN, and
  ``dist/schemas/3.1.1/core/webhook-challenge.json`` is ``additionalProperties: false``
  with all seven ``required``.
* :223-233 — "The receiver proves control by returning HTTP ``2xx`` with a JSON body
  containing exactly one echo field", plus the backward-compatible ``token`` alias.
* :235 — the challenge value MUST be cryptographically random and single-use; a failed,
  non-2xx, malformed, mismatched or timed-out challenge means proof FAILED.
* :237 — on proof failure the seller returns ``action: "failed"`` with
  ``errors[].code: "VALIDATION_ERROR"`` and ``error.field`` pointing at
  ``accounts[i].notification_configs[j].url``.

No 3.1.1 conformance storyboard grades this surface — ``notification-config-lifecycle.yaml``
puts active registration out of scope — so the assertions here and in the integration
sibling are the only graders that exist.

## In-network only, and why that is a skip rather than a failure

The capture service publishes NO host port: its readback control plane is reachable only
by compose service name. The skip below is therefore the same host-vs-in-network gate
``test_webhook_capture_egress_gate_e2e`` uses, and nothing else in this module may skip —
every precondition inside a run RAISES, because degrading a missing precondition to a
skip would report it as success. ``./run_all_tests.sh`` runs every suite in-network, so
the skip path is the developer convenience, not CI.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import httpx
import pytest
from adcp.signing.webhook_signer import WEBHOOK_TAG
from adcp.types.generated_poc.core.webhook_challenge import WebhookChallenge

from tests.e2e._signing_e2e import (
    a2a_data_part,
    buyer_headers,
    ca_verified_ssl_context,
    declaring_tenant_provisioner,
    netloc,
    post_a2a,
    provision_signing_key_via_admin,
    published_jwks_for_agent,
    signing_declarations,
    tls_base_url,
)
from tests.e2e.adcp_request_builder import build_a2a_message_send
from tests.e2e.conftest import e2e_in_network
from tests.e2e.webhook_capture_service import decode_body, header_value, webhook_path
from tests.helpers.signing import verify_as_conformant_receiver
from tests.helpers.webhook_wire import CapturedWebhook, signature_input_label

pytestmark = pytest.mark.skipif(
    not e2e_in_network(),
    reason=(
        "in-network only: the webhook-capture service publishes no host port, so its readback "
        "control plane is reachable by compose service name alone (set ADCP_TEST_HOST)"
    ),
)

#: Distinct from ``whsig_e2e`` / ``jwkspub_e2e`` / ``tr_e2e_tls`` / ``dn4i_e2e``: all of
#: them claim the same TLS ``virtual_host``, which ``get_tenant_by_virtual_host`` matches
#: as an exact string, so only one may hold it at a time. e2e runs serially and the seam
#: drops the row at BOTH ends, which makes that an ordering constraint, not a concurrent one.
_SLUG = "npcprf_e2e"
_TENANT_ID = "npcprf_e2e"

#: Handed IN to the tenant seam so the yield stays a 2-tuple, and a literal because this
#: module has to authenticate with it.
_BUYER_TOKEN = "npcprf-e2e-buyer-token"

#: The operation the tenant declares a ``request_signing`` posture for. ``supported_for``
#: rather than ``required_for``: a required bucket could refuse the anonymous capabilities
#: fetch that the trust-root documents are read through.
_DECLARED_OPERATION = "sync_accounts"

_ALG = "ed25519"

#: The success-leg receiver, reached through the shared ``tls-proxy`` front by SNI. A
#: literal rather than something read out of the compose wiring: a name read out of the
#: thing under test cannot disagree with it.
_CAPTURE_HOSTNAME = "webhooks.adcp-e2e.dev"
_CAPTURE_TLS_PORT = 8443

#: The capture service's READBACK control plane — plain HTTP, by compose service name,
#: never through the TLS front. Deliveries and readback are deliberately different traffic
#: (``tests/e2e/webhook_capture_service``): the server only ever sees the TLS origin.
_CAPTURE_READBACK_ORIGIN = "http://webhook-capture:8080"

_SUBSCRIBER_ID = "npcprf-e2e-subscriber"

#: An ACCOUNT-surface notification type. Both halves are load-bearing: the enum is closed,
#: and production separately refuses a MEDIA-BUY-anchored type on the account surface with
#: ``INVALID_REQUEST`` at ``event_types[0]`` BEFORE any proof runs — which would make every
#: ``action == "failed"`` assertion here pass for an unrelated reason.
_EVENT_TYPES = ["creative.status_changed"]

#: One brand domain per leg, so each leg provisions its OWN account by natural key
#: (``brand``/``operator``/``sandbox``) and no leg reads another's persisted state.
#: ``.com`` throughout: ``_check_domain_validity`` rejects a brand under a reserved TLD
#: before any proof runs.
_BRAND_KEYLESS = "keyless-npcprf.com"
_BRAND_REST = "rest-npcprf.com"
_BRAND_NO_ECHO = "noecho-npcprf.com"
_BRAND_PROVEN = "proven-npcprf.com"

#: The seven properties ``webhook-challenge.json`` requires and (being
#: ``additionalProperties: false``) permits — sync_accounts.mdx @ v3.1.1 :209.
_REQUIRED_CHALLENGE_FIELDS = frozenset(
    {"type", "challenge", "account_id", "subscriber_id", "seller_agent_url", "delivery_auth", "event_types"}
)


@pytest.fixture
def signing_capable_tenant(live_server):
    """A tenant that DECLARES a signing posture, owns NO key, and can be driven by a buyer.

    ``mint_key=False`` (fixed inside :func:`declaring_tenant_provisioner`) is what makes
    the keyless leg real: the key must arrive later through a PRODUCTION transport, or
    this module grades its own fixture — and a runner-minted row is encrypted under the
    runner's KEK, which the server container cannot open when it comes to sign.
    ``buyer_access_token`` seeds the principal a live authenticated ``sync_accounts``
    needs.
    """
    with declaring_tenant_provisioner(
        live_server,
        tenant_id=_TENANT_ID,
        slug=_SLUG,
        declarations_from_tenant=signing_declarations(_DECLARED_OPERATION),
        buyer_access_token=_BUYER_TOKEN,
    ) as provision:
        yield provision


def _subscriber_url(key: str, *, echo: str | None) -> str:
    """The buyer's notification endpoint for one leg: the real HTTPS capture origin.

    ``echo`` selects whether that endpoint will PROVE control (echo the single-use value)
    or merely accept the POST. Both spellings are the same origin, so every leg clears
    gates 1, 2, 4 and 5 identically and only the gate under test can differ.
    """
    return f"https://{_CAPTURE_HOSTNAME}:{_CAPTURE_TLS_PORT}{webhook_path(key, echo=echo)}"


def _sync_accounts_parameters(brand_domain: str, url: str) -> dict[str, Any]:
    """One provisioning-mode account entry activating ONE notification subscriber.

    Provisioning mode (``brand`` + ``operator``, no ``account`` reference) on purpose: it
    is the mode where the account id does not exist until the write transaction, so it is
    the mode that grades whether the ``account_id`` in the challenge is the id the account
    is actually created with.
    """
    return {
        "accounts": [
            {
                "brand": {"domain": brand_domain},
                "operator": brand_domain,
                "billing": "operator",
                "notification_configs": [
                    {
                        "subscriber_id": _SUBSCRIBER_ID,
                        "url": url,
                        "event_types": list(_EVENT_TYPES),
                        "active": True,
                    }
                ],
            }
        ]
    }


async def _sync_over_a2a(client: httpx.AsyncClient, *, brand_domain: str, url: str) -> dict[str, Any]:
    """Drive ``sync_accounts`` over A2A — the transport that CAN be challenged.

    ``identity.protocol`` is ``"a2a"`` here, and ``agent_endpoint_urls`` publishes an
    ``agents[]`` entry for it, so ``_challenge_signing`` can produce a ``seller_agent_url``
    a receiver could resolve. The REST leg below is the same request on the transport that
    cannot.
    """
    message = build_a2a_message_send(skill="sync_accounts", parameters=_sync_accounts_parameters(brand_domain, url))
    result = await post_a2a(client, message, leg=f"sync_accounts[{brand_domain}]", token=_BUYER_TOKEN)
    assert "error" not in result, f"the A2A sync_accounts returned an error: {result['error']!r}"
    return _one_account(a2a_data_part(result), leg=brand_domain)


async def _sync_over_rest(client: httpx.AsyncClient, *, brand_domain: str, url: str) -> dict[str, Any]:
    """The SAME request over REST, which publishes no ``agents[]`` entry.

    Not a variation for coverage's sake: ``identity.protocol == "rest"`` is precisely what
    makes ``_challenge_signing`` return ``None`` (``accounts.py`` — "we publish no
    ``agents[]`` entry for that transport, and a receiver matches ``seller_agent_url``
    byte-for-byte against our brand.json"). Driving the same body at the same receiver
    over this transport is how that refusal is graded with no extra infrastructure.
    """
    response = await client.post(
        "/api/v1/accounts/sync",
        json=_sync_accounts_parameters(brand_domain, url),
        headers=buyer_headers(_BUYER_TOKEN),
    )
    assert response.status_code == 200, (
        f"the REST sync_accounts must succeed at the transport level — a per-entry proof refusal is "
        f"reported INSIDE a 200, not as an HTTP error; got {response.status_code}: {response.text[:600]!r}"
    )
    return _one_account(response.json(), leg=brand_domain)


def _one_account(payload: dict[str, Any] | None, *, leg: str) -> dict[str, Any]:
    """The single account result out of a ``sync_accounts`` response body."""
    accounts = (payload or {}).get("accounts") or []
    assert len(accounts) == 1, (
        f"the {leg!r} leg sent exactly one account entry, so the response must carry exactly one account "
        f"result; it carries {len(accounts)}. Response: {str(payload)[:600]!r}"
    )
    return accounts[0]


def _captures(key: str) -> dict[str, list[dict]]:
    """Everything the capture service recorded under *key*.

    No polling, deliberately, and the difference from ``test_webhook_signature_e2e``'s
    ``_await_deliveries`` is the point: a delivery webhook is fired asynchronously behind
    a retry ladder, whereas the proof handshake is SYNCHRONOUS inside ``sync_accounts`` —
    the receiver records the wire before it answers, and production has already read that
    answer by the time the response we are holding was built. So a missing capture here is
    a missing challenge, never a race, and polling would only blur the two.
    """
    try:
        response = httpx.get(f"{_CAPTURE_READBACK_ORIGIN}{webhook_path(key)}", timeout=10.0)
    except httpx.HTTPError as exc:
        raise AssertionError(
            f"the webhook-capture readback plane is unreachable at {_CAPTURE_READBACK_ORIGIN!r} ({exc!r}) — "
            "the compose service is not running, so 'zero captures' below would be true of every leg and "
            "this module would grade nothing"
        ) from exc
    assert response.status_code == 200, (
        f"readback of capture key {key!r} must succeed; got HTTP {response.status_code}: {response.text[:300]!r}"
    )
    return response.json()


def _assert_capture_service_is_live() -> None:
    """Fail — never skip — when the receiver is not answering before any leg runs.

    Every fail-closed assertion below is "the receiver captured nothing". That claim is
    worthless if the receiver could not have captured anything, and the failure would
    otherwise surface as a passing test.
    """
    try:
        response = httpx.get(f"{_CAPTURE_READBACK_ORIGIN}/health", timeout=10.0)
    except httpx.HTTPError as exc:
        raise AssertionError(
            f"the webhook-capture service is not answering at {_CAPTURE_READBACK_ORIGIN!r} ({exc!r})"
        ) from exc
    assert response.status_code == 200, f"GET /health must succeed; got HTTP {response.status_code}"
    assert response.json().get("compose_project_name"), (
        "the capture service must report the compose project it belongs to, or a readback cannot tell "
        f"this stack's receiver from a cross-wired sibling's; served {response.json()!r}"
    )


def _sole_challenge(key: str, *, leg: str) -> tuple[CapturedWebhook, dict[str, Any]]:
    """The ONE challenge captured under *key*, as the receiving socket saw it.

    The ``@target-uri`` is rebuilt from the ``Host`` header the receiver was given plus
    the path it was dialled at — never from the URL registered here. It is ``https``
    because that is the scheme the SERVER dialled: the tls-proxy terminates TLS and
    forwards ``Host`` verbatim, so the authority (port included) is the one the signature
    covers, while the capture service itself only ever sees plaintext. Passing an
    ``http://`` origin instead would surface as ``webhook_signature_invalid``, which reads
    like a crypto bug and is really a reconstruction bug.
    """
    captured = _captures(key)
    entries = captured.get("received_raw") or []
    assert len(entries) == 1, (
        f"the {leg!r} leg must produce exactly ONE challenge POST (:235 — sellers SHOULD make at most one "
        f"initial challenge POST on the sync_accounts critical path); the receiver recorded {len(entries)}. "
        f"Captured: {str(captured)[:600]!r}"
    )
    entry = entries[0]
    host = header_value(entry, "host")
    assert host, f"the captured challenge carries no Host header, so its @target-uri cannot be rebuilt: {entry!r}"
    body = decode_body(entry)
    delivery = CapturedWebhook(
        url=f"https://{host}{entry['path']}",
        headers=httpx.Headers(entry["headers"]),
        content=body,
    )
    return delivery, json.loads(body)


@pytest.mark.asyncio
async def test_the_proof_of_control_challenge_is_signed_conformant_and_echo_checked(
    docker_services_e2e, live_server, signing_capable_tenant
):
    """Four legs against one live server, in the order the key material forces.

    A single test rather than four, for the same reason ``test_webhook_signature_e2e`` is
    one: only ONE tenant may hold the TLS ``virtual_host`` at a time, and the keyless leg
    is only keyless BEFORE the production provisioning call that the other three need. The
    order is the instrument — "this deployment never challenges anything" is ruled out by
    the legs that do, and "this deployment challenges everything" by the legs that do not.
    """
    _assert_capture_service_is_live()

    base_url = tls_base_url(live_server)
    verify = ca_verified_ssl_context()

    # The netloc INCLUDES the port: get_tenant_by_virtual_host matches the Host header as
    # an exact string, and httpx sends the port unless it is the scheme default.
    tenant, key = signing_capable_tenant(netloc(base_url))
    assert key is None, "this tenant's key must arrive through a production path, not through the fixture"
    brand_json_url = tenant.capability_declarations["identity"]["brand_json_url"]

    keyless_key = uuid.uuid4().hex
    rest_key = uuid.uuid4().hex
    no_echo_key = uuid.uuid4().hex
    proven_key = uuid.uuid4().hex

    # ── Leg A — KEYLESS. Refused at gate 3, before any socket opens. ───────────
    async with httpx.AsyncClient(base_url=base_url, verify=verify, timeout=60.0) as client:
        keyless = await _sync_over_a2a(
            client, brand_domain=_BRAND_KEYLESS, url=_subscriber_url(keyless_key, echo="challenge")
        )

    assert _captures(keyless_key)["received_raw"] == [], (
        "a tenant holding no signing key must send NO challenge — an unsigned challenge is one no conformant "
        "receiver can attribute to us (sync_accounts.mdx @ v3.1.1 :207). The receiver it was pointed at is the "
        "SAME one the proven leg below reaches, so this zero is evidence rather than an SSRF refusal wearing "
        f"a signing refusal's clothes. Captured: {_captures(keyless_key)!r}"
    )
    _assert_failed_with_proof_error(keyless, leg="keyless")

    # ── Provision the key through a PRODUCTION transport. ──────────────────────
    # The admin route runs INSIDE the server container, so the row is encrypted under the
    # CONTAINER's KEK — a key the server can actually open when it comes to sign.
    provisioned_kid = provision_signing_key_via_admin(base_url, tenant_id=_TENANT_ID, alg=_ALG)

    # ── Leg B — REST. The tenant CAN sign now; the transport still cannot be named. ──
    async with httpx.AsyncClient(base_url=base_url, verify=verify, timeout=60.0) as client:
        rest = await _sync_over_rest(client, brand_domain=_BRAND_REST, url=_subscriber_url(rest_key, echo="challenge"))

    assert _captures(rest_key)["received_raw"] == [], (
        "a REST-registered subscriber must not be challenged: we publish no agents[] entry for REST, so any "
        "seller_agent_url we could send is one a strict receiver cannot resolve a key for. This leg runs AFTER "
        "provisioning precisely so a zero here cannot be explained by the tenant being keyless. Captured: "
        f"{_captures(rest_key)!r}"
    )
    _assert_failed_with_proof_error(rest, leg="rest")

    # ── Leg C — 2xx WITHOUT the echo. The challenge IS sent; control is NOT proven. ──
    async with httpx.AsyncClient(base_url=base_url, verify=verify, timeout=60.0) as client:
        no_echo = await _sync_over_a2a(client, brand_domain=_BRAND_NO_ECHO, url=_subscriber_url(no_echo_key, echo=None))

    unproven, unproven_payload = _sole_challenge(no_echo_key, leg="no-echo")
    _assert_failed_with_proof_error(no_echo, leg="no-echo")
    assert _active_flags(no_echo) != [True], (
        "a 2xx that does not echo the single-use value proves REACHABILITY, not control (:223-235) — the rule it "
        "replaces is satisfied by every endpoint that accepts POSTs, including one an attacker pointed at us. "
        f"The subscriber was persisted as {_active_flags(no_echo)!r}"
    )

    # ── Leg D — PROVEN. The same receiver, echoing. ────────────────────────────
    async with httpx.AsyncClient(base_url=base_url, verify=verify, timeout=60.0) as client:
        proven = await _sync_over_a2a(
            client, brand_domain=_BRAND_PROVEN, url=_subscriber_url(proven_key, echo="challenge")
        )
        delivery, payload = _sole_challenge(proven_key, leg="proven")
        # This resolution IS the seller_agent_url assertion: the helper refuses unless
        # exactly one sales agents[] entry in the SERVED brand.json byte-equals the value
        # the challenge put on the wire — the match a receiver performs with no
        # canonicalization (security.mdx @ v3.1.1 :1104 step 5) before it can resolve a
        # key at all. Grading it against a literal would only restate this module's fixture.
        jwks, published_kid = await published_jwks_for_agent(
            client, brand_json_url=brand_json_url, agent_url=payload["seller_agent_url"]
        )

    assert not (proven.get("errors") or []), (
        f"a proven challenge must leave the entry clean; got errors {proven.get('errors')!r}"
    )
    assert _active_flags(proven) == [True], (
        "the subscriber must be persisted ACTIVE once control is proven, or this module graded a signature whose "
        f"outcome production ignored; got {proven.get('notification_configs')!r}"
    )

    # (a) the three headers a receiver needs, all present.
    missing = [h for h in ("signature", "signature-input", "content-digest") if h not in delivery.headers]
    assert not missing, (
        f"the challenge is missing {missing} — a receiver cannot verify it, and :207 makes the signature a MUST "
        f"even when the candidate selects legacy delivery auth. Headers: {sorted(delivery.headers.keys())}"
    )

    parsed = signature_input_label(delivery)

    # (b) the WEBHOOK profile tag. Domain separation between requests and webhooks is
    # carried by the tag, not by the key purpose (security.mdx @ v3.1.1 :1426), so a
    # request-profile tag here is a signature a webhook verifier rejects.
    assert parsed.params.get("tag") == WEBHOOK_TAG, (
        f"the challenge must be signed under the webhook profile tag {WEBHOOK_TAG!r}; the wire carries "
        f"{parsed.params.get('tag')!r}"
    )

    # (c) the signature covers the digest of the body, so it is a signature over the BYTES
    # the receiver read rather than over a header set that would survive a swapped payload.
    assert "content-digest" in parsed.components, (
        f"content-digest must be a COVERED component of the signature; covered components: {list(parsed.components)}"
    )

    # (d) it verifies — against the wire bytes, under a key resolved from the
    # seller_agent_url the CHALLENGE ITSELF named, through the SDK's verifier. Verifying
    # against a re-serialization of the payload would defeat the whole content-digest
    # claim above, which is why the capture service records raw bytes.
    verified = verify_as_conformant_receiver(delivery, jwks)
    assert verified.key_id == provisioned_kid, (
        f"the signature must verify under the kid the admin route reported provisioning ({provisioned_kid!r}); "
        f"it verified under {verified.key_id!r}"
    )
    assert verified.key_id == published_kid, (
        f"the verifying key must be the one PUBLISHED at the origin the challenge's seller_agent_url resolves to "
        f"({published_kid!r}); the signature verified under {verified.key_id!r} — a key we sign with but do not "
        "publish is a key no counterparty can use"
    )

    _assert_body_is_the_seven_field_document(payload, account_id=proven.get("account_id"))

    # (e) the value is single-use: two challenges from the same tenant, minutes apart,
    # never repeat it (:235 — "cryptographically random, single-use, and scoped to the
    # registration tuple"). The no-echo leg is the second sample, which is the only reason
    # its capture is kept rather than discarded.
    assert payload["challenge"] != unproven_payload["challenge"], (
        "two challenges must not share a value: it is single-use and scoped to the registration tuple (:235). "
        f"Both legs were issued {payload['challenge']!r}"
    )


def _active_flags(account: dict[str, Any]) -> list[bool]:
    """The ``active`` flag of every notification config echoed back for one account."""
    return [config.get("active") for config in (account.get("notification_configs") or [])]


def _assert_failed_with_proof_error(account: dict[str, Any], *, leg: str) -> None:
    """The fail-closed SHAPE: ``action: failed`` plus a VALIDATION_ERROR at the url.

    :237 pins all three. Note what this does and does NOT discriminate: ``_proof_error``
    emits one code, recovery and field for EVERY proof failure, so this shape is
    byte-identical across the keyless, REST and no-echo legs. It is worth grading because
    it is the buyer-facing contract — but the thing that tells the legs APART is the
    capture count on a receiver production would otherwise have accepted, which each
    caller asserts separately. A later reader must not mistake this for the proof.
    """
    assert account.get("action") == "failed", (
        f"the {leg!r} leg must report action 'failed' for an unprovable activation (:237); got "
        f"{account.get('action')!r}"
    )
    errors = account.get("errors") or []
    assert [error.get("code") for error in errors] == ["VALIDATION_ERROR"], (
        f"the {leg!r} leg must carry exactly one VALIDATION_ERROR (:237); got {errors!r}"
    )
    assert errors[0].get("field") == "notification_configs[0].url", (
        f"the error must point at the endpoint that could not be proven (:237); got {errors[0].get('field')!r}"
    )
    assert errors[0].get("recovery") == "correctable", (
        f"a proof failure is correctable — the buyer fixes the endpoint and re-sends; got {errors[0].get('recovery')!r}"
    )


def _assert_body_is_the_seven_field_document(payload: dict[str, Any], *, account_id: str | None) -> None:
    """Exactly the seven required properties, right values, no ``idempotency_key``.

    Two assertions where one would nearly do, on purpose. ``WebhookChallenge`` is
    ``extra='forbid'``, so validating through the SDK type tracks the schema instead of a
    copy of it and rejects an injected field on its own; the key-set comparison is what
    NAMES the intruder in the failure message, which matters because the field production
    would inject if the challenge were sent through ``WebhookSender.send_raw`` is
    ``idempotency_key`` and that is a one-word diagnosis.
    """
    assert set(payload) == set(_REQUIRED_CHALLENGE_FIELDS), (
        "the challenge body must be EXACTLY the seven properties webhook-challenge.json requires and permits "
        f"(sync_accounts.mdx @ v3.1.1 :209; additionalProperties: false). Extra: "
        f"{sorted(set(payload) - _REQUIRED_CHALLENGE_FIELDS)}, missing: "
        f"{sorted(_REQUIRED_CHALLENGE_FIELDS - set(payload))}. An 'idempotency_key' among the extras means the "
        "challenge was sent through the delivery path (WebhookSender.send_raw injects one before signing), "
        "which produces a document every conformant receiver must reject"
    )

    challenge = WebhookChallenge.model_validate(payload)

    assert challenge.type == "webhook.challenge", f"the wire type must be `webhook.challenge`, got {challenge.type!r}"
    assert re.fullmatch(r"[A-Za-z0-9_.:-]{32,255}", challenge.challenge), (
        f"challenge {challenge.challenge!r} violates the schema pattern ^[A-Za-z0-9_.:-]{{32,255}}$"
    )
    assert challenge.subscriber_id == _SUBSCRIBER_ID, (
        f"the challenge must name the subscriber being registered; got {challenge.subscriber_id!r}"
    )
    assert [getattr(event, "value", str(event)) for event in challenge.event_types] == _EVENT_TYPES, (
        "event_types is one of the three fields a receiver matches against its pending registration (:207), so "
        f"it must be the set the subscriber asked for; got {challenge.event_types!r}"
    )
    assert challenge.delivery_auth.mode.value == "rfc9421", (
        "a candidate declaring no authentication delivers over RFC 9421, and delivery_auth reports the FUTURE "
        f"delivery mode as data; got {challenge.delivery_auth.mode!r}"
    )
    assert challenge.delivery_auth.credential_fingerprint is None, (
        "credential_fingerprint is FORBIDDEN for mode rfc9421 (webhook-challenge.json), so emitting one is not a "
        f"harmless extra — it is a body the receiver must reject; got {challenge.delivery_auth.credential_fingerprint!r}"
    )
    assert account_id and challenge.account_id == account_id, (
        "the challenge must name the account id the account is actually created with, or the proof is scoped to "
        f"an account that never exists; challenge={challenge.account_id!r} created={account_id!r}"
    )
