"""The env's ability to send a genuinely signed request.

SCOPE (salesagent-n78j0.1.1): EVERY leg — ``rest``, ``a2a``, ``mcp`` and
``e2e_rest`` — realizes a real signature over the real HTTP path. There is no
leg left that cannot: #1721 deleted the ``impl`` transport, whose whole shape was
a direct in-process call past the boundary, so "this leg has no wire, so there is
nothing to sign" no longer describes anything in the harness. What survives is
the refusal — a dispatch asked to sign with no capability established RAISES
rather than downgrading to an unsigned send, because a silent downgrade is the
exact failure mode S1 exists to remove.

TWO REALIZATIONS, ONE INTENT. ``enable_request_signing()`` means "this buyer
signs", and what that takes differs by where the verifier runs:

* in-process (``build_signing_capability``): the counterparty's resolution is
  seeded straight into the verifier's process-global ``AGENT_RESOLUTION_CACHE``
  (``src.core.signing.verifier``), because the verifier is in THIS process.
* e2e (``build_e2e_signing_capability``): the verifier is in the SERVER
  container and cannot see anything this process patches, so the key must be
  PUBLISHED — installed on the counterparty origin's control plane and reached
  by the server's own three-hop brand.json walk — and the ``agent_url`` that
  walk starts from must be on a Principal row in the SERVER's database.

The seam between them is :func:`tests.harness._realize.realize_e2e` on
``BaseTestEnv.enable_request_signing``, so a Given (or a fixture) says
``env.enable_request_signing()`` once and never learns which world it is in.

WHAT ENABLING THIS CHANGES, beyond adding a key. Owner decision D1's corollary:
once an env has this capability, BOTH its signed and its unsigned dispatches go
over the HTTP path on every leg — ``a2a`` stops calling ``on_message_send``
directly and ``mcp`` stops using FastMCP's in-memory streams. The reason is the
shape of what the verifier is handed: a signature covers things the boundary
cannot rebuild — the exact bytes, and ``@target-uri`` from ``raw_path`` — so they
are recorded by ``SignedExchangeCapture`` (``src/core/signing/capture.py``), the
app's INNERMOST ASGI middleware. A leg that never crosses the ASGI stack reaches
``serve()`` with ``exchange=None``, and ``_carries_signature`` reads that —
correctly — as "this request presented no signature". Signed and unsigned then
differ by exactly one variable, the signature, which is what makes an unsigned
dispatch a CONTROL rather than a different experiment.

WHY THIS LIVES ON THE ENV. Nothing under ``tests/bdd/steps/`` may learn that a
transport exists (salesagent-n78j0.1.1). A step says ``signed=True``; which
headers that means, and how they reach the wire, is the env's and the
dispatcher's business.

WHY A REAL TOKEN IS REQUIRED, and why no harness-injected identity can stand in
for it. Under #1721 there is no auth middleware and no signature middleware to
sidestep: every transport hands the raw headers to ``serve()``, and
``_resolve_identity`` (``src/core/resolved_identity.py``, reachable only through
``src/core/tools/_boundary.py:invoke_tool``) is the ONE reader of both
credentials — the bearer AND the RFC 9421 signature. It reads those headers
itself and no parameter accepts a pre-parsed token, so an identity the harness
hands the app has nothing to feed it. The Principal row is then read in BOTH
directions: ``token -> Principal -> agent_url`` is the key-resolution input, and
the reverse, ``verified agent_url -> Principal``, is how a signed request
carrying no bearer identifies at all. One row answers both, which is why this
capability writes to the env's OWN Principal row (:func:`attach_agent_url`)
instead of relying on the env's mock identity — one acting identity, recorded
where production reads it.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class SigningCapability:
    """One counterparty's ability to sign, as the env holds it.

    ``private_key``/``key_id`` sign; ``jwks`` is what the verifier resolves to
    when it walks the counterparty's trust root; ``token`` is the bearer that
    ties the request back to the Principal carrying ``agent_url``.

    The capability carries no verification record of its own. It used to hold a
    ``verifications`` list fed by :func:`tests.helpers.signing.verifier_spy`, which
    :meth:`BaseTestEnv.signature_verifications` summed on the in-process legs — but
    the spy observes the SDK's ``verify_request_signature``, which is called BEFORE
    ``_handle_rejection`` gets to decide whether this operation's bucket swallows a
    refusal, so that made the acceptance oracle count an event preceding the
    acceptance decision. Both legs now read
    ``adcp_request_signature_verified_total``, which ``_verify_signed`` emits only
    once the checklist has returned a signer and nothing swallowed it.

    ``verifier_spy`` itself stands: it is also the refusal-STEP instrument, and the
    only one — the verified-total counter records acceptances alone, so nothing else
    can say WHICH exception the verifier raised. Callers that need that open it
    themselves around the dispatch they want to observe.
    """

    private_key: Any
    jwks: dict[str, Any]
    token: str
    key_id: str
    agent_url: str


class _ExitStackPatcher:
    """Adapts an ``ExitStack`` to the ``.stop()`` shape ``_patchers`` expects.

    ``env._patchers.append`` forwards into ``BaseTestEnv._guard``, so this stack is
    released by the SAME ``_release_entered`` pass as the database session and the
    env's own patches — reverse acquisition order, exactly once, on both release
    paths including the ``__enter__``-failed one. Reusing that is one lifecycle
    instead of two: a second teardown path is how a seeded resolution outlives its
    test and poisons the next one, and an ordering it does not share with the DB
    session is how it outlives the session it was seeded against.
    """

    def __init__(self, stack: ExitStack) -> None:
        self._stack = stack

    def stop(self) -> None:
        self._stack.close()


def unique_run_id() -> str:
    """A short identifier no other capability, worker or concurrent run shares.

    It names TWO things that must both be unique, for the same underlying reason
    — a shared name makes an observation that belongs to somebody else look like
    this test's:

    1. **the ``keyid``**, because the e2e leg's positive oracle is the scraped
       ``adcp_request_signature_verified_total{operation,keyid}`` delta and the
       server's registry is cumulative for the whole session. Under a shared kid
       a window drawn around this request reads another leg's increment and
       "incremented by exactly 1" stops being a claim about this request at all.
       Same idea, same shape, as the ``uuid4()`` webhook-signing kid in
       ``_mixins.py`` (whose reason is the 60s ``(tenant_id, kid)`` provider
       cache outliving a per-test database).
    2. **the e2e counterparty SLOT**, because the server caches a resolved
       ``AgentResolution`` — the JWKS included — per ``agent_url`` for
       ``agent_resolution_ttl_seconds`` (3600 by default,
       ``src.core.signing.verifier._resolution_for``). Two capabilities sharing
       one agent url would share that cache entry: whichever walked FIRST pins
       the keyset for an hour, and every later key published at that url is
       invisible to the verifier. A per-capability slot means a per-capability
       agent url, so the cache can never be poisoned — not across xdist workers,
       not across suites, and not for the existing ``/agent/listed`` consumers.
    """
    return uuid4().hex[:12]


def build_signing_capability(env: Any) -> SigningCapability:
    """Mint a counterparty key, publish it through its trust root, seed its principal.

    Registers teardown on *env* so the seeded resolution is torn down with the
    env, not left in the process-global ``AGENT_RESOLUTION_CACHE``.

    The counterparty is THE ENV'S OWN principal (:func:`attach_agent_url`), the same
    rule the e2e realization already obeyed, and not a second hardcoded
    ``sig_tenant``/``sig_principal`` pair. Two reasons, and the second is why this
    changed: owner decision D3 — once an env can sign there is exactly ONE acting
    identity, so a signed and an unsigned dispatch differ by the signature and
    nothing else; and a capability minted in a tenant OTHER than the env's makes
    every signed dispatch carry a bearer and an ``x-adcp-tenant`` naming a tenant
    the scenario never set up, which is unusable from a BDD env (whose tenant comes
    from its Givens) and was silently fine only because the one caller constructed
    its env with those very ids.
    """
    from tests.helpers.signing import (
        COUNTERPARTY_AGENT_URL,
        COUNTERPARTY_KID,
        counterparty_key,
        keypair_for,
    )

    key_id = f"{COUNTERPARTY_KID}-{unique_run_id()}"
    private_key, jwks = keypair_for(key_id)
    token = attach_agent_url(env, COUNTERPARTY_AGENT_URL)

    stack = ExitStack()
    stack.enter_context(counterparty_key(jwks))
    env._patchers.append(_ExitStackPatcher(stack))

    return SigningCapability(
        private_key=private_key,
        jwks=jwks,
        token=token,
        key_id=key_id,
        agent_url=COUNTERPARTY_AGENT_URL,
    )


def build_e2e_signing_capability(env: Any) -> SigningCapability:
    """The same intent, realized against a verifier running in another container.

    Three things replace the in-process shortcuts, and each is the REAL mechanism
    rather than a stand-in:

    * the key is PUBLISHED, not patched: installed on the counterparty origin's
      control plane, at a slot of this capability's own, and the server reaches
      it by walking ``agent_url -> capabilities -> identity.brand_json_url ->
      brand.json agents[] -> jwks_uri -> JWKS`` exactly as it would for a
      production counterparty (``adcp.signing.agent_resolver``);
    * ``agent_url`` goes onto the ENV'S OWN Principal row in the SERVER's
      database — the only legitimate source of a counterparty's identity
      (security.mdx forbids a header or body field), and the sole input to that
      walk. The env's own principal rather than a second seeded one, per owner
      decision D3: once an env can sign there is exactly ONE acting identity, so
      a signed and an unsigned dispatch differ by the signature and nothing else;
    * the bearer is that principal's real token, on ``Authorization`` (D4).

    Nothing is registered for teardown: the slot is unique to this capability, so
    leaving it published cannot affect another test, and the row it updates is
    the env's own.
    """
    from tests.e2e.counterparty_origin_service import slot_agent_url, slot_control_path
    from tests.helpers.signing import keypair_for

    config = env.e2e_config
    assert config is not None, (
        "build_e2e_signing_capability needs env.e2e_config — it is the e2e realization of "
        "enable_request_signing() and is only reached when env.is_e2e is true"
    )

    run_id = unique_run_id()
    key_id = f"harness-request-signing-{run_id}"
    private_key, jwks = keypair_for(key_id)
    agent_url = slot_agent_url(run_id)

    _publish_jwks(config, slot_control_path(run_id), jwks)
    token = attach_agent_url(env, agent_url)

    # The published trust root, read back as the SERVER will read it. A slot that
    # answers 404 or serves an empty keyset must fail HERE, naming the origin,
    # rather than three assertions later as a key the verifier could not resolve —
    # the failure mode the whole signing suite keeps having to tell apart from a
    # verifier bug.
    served = _counterparty_request(config, "GET", agent_url).json()
    brand_json_url = served["identity"]["brand_json_url"]
    agents = _counterparty_request(config, "GET", brand_json_url).json()["agents"]
    listed = [agent.get("url") for agent in agents]
    assert listed == [agent_url], (
        f"the counterparty origin must publish a brand.json listing EXACTLY {agent_url!r} for this "
        f"capability's slot, or the server's agents[] walk resolves no entry for it and the signature "
        f"it is about to make is refused with a discovery code at step 7; it lists {listed!r}"
    )
    published_kids = [
        key.get("kid") for key in _counterparty_request(config, "GET", agents[0]["jwks_uri"]).json()["keys"]
    ]
    assert published_kids == [key_id], (
        f"the JWKS this capability's brand.json points at must publish EXACTLY {key_id!r} — that is the "
        f"keyid the verifier will look up when it walks this agent url; it publishes {published_kids!r}"
    )

    return SigningCapability(
        private_key=private_key,
        jwks=jwks,
        token=token,
        key_id=key_id,
        agent_url=agent_url,
    )


def _counterparty_request(config: Any, method: str, url: str, **kwargs: Any) -> Any:
    """Reach the counterparty origin, turning a transport failure into a WIRING failure.

    Left bare, an unwired stack surfaces as ``httpx.ConnectError: Name or service
    not known`` from inside the client — a message naming neither the compose
    service nor the SNI map. The origin is scenery; a stack that cannot serve it
    must say which wiring site is missing. Raised, never skipped.
    """
    import ssl

    import httpx

    # An SSL CONTEXT, not the bare path: httpx deprecated `verify=<str>`. Verification
    # stays full-strength against the stack's private CA — the counterparty is our own
    # origin behind the shared TLS front, and `verify=False` would make the handshake
    # prove nothing.
    verify: Any = ssl.create_default_context(cafile=config.ca_bundle) if config.ca_bundle else True
    try:
        with httpx.Client(verify=verify, timeout=30) as client:
            response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise AssertionError(
            f"{method} {url} did not reach a counterparty origin ({exc!r}). The signed e2e leg needs "
            "the wiring guarded by tests/unit/test_architecture_e2e_counterparty_origin_wiring.py: a "
            "counterparty-origin compose service running tests.e2e.counterparty_origin_service, the "
            "tls-proxy alias, the nginx $ssl_server_name map row, and SAN coverage for the name."
        ) from exc
    assert response.status_code == 200, (
        f"{method} {url} answered HTTP {response.status_code}; the counterparty origin is stack wiring, "
        f"not the thing under test. Body: {response.text[:300]!r}"
    )
    return response


def _publish_jwks(config: Any, control_path: str, jwks: dict[str, Any]) -> None:
    """Install *jwks* at this capability's own slot on the counterparty origin."""
    from tests.e2e.counterparty_origin_service import PUBLIC_ORIGIN

    _counterparty_request(config, "PUT", f"{PUBLIC_ORIGIN}{control_path}", json=jwks)


def attach_agent_url(env: Any, agent_url: str) -> str:
    """Record *agent_url* on the env's own Principal row; return its bearer token.

    Written through ``env._session``, which in e2e mode is bound to the LIVE
    SERVER's database — the one the verifier reads. ``setup_default_data`` is
    get-or-create and idempotent, so the row ``_seed_e2e_identity`` (or a Given)
    already created is REUSED rather than re-inserted, and an env that seeds
    nothing of its own still gets a counterparty to sign as.

    ``Principal.agent_url`` is the ONLY legitimate source of a counterparty's
    identity — security.mdx forbids taking it from a header or a body field — and
    it is the sole input to the verifier's key resolution, which is why this is a
    DB write and not a header.

    The tenant's ``virtual_host`` is made DOTTED here too — see
    :func:`ensure_declarable_identity_host`.

    The token comes back from :meth:`~tests.harness._base.BaseTestEnv._principal_token`,
    the env's own reader, and not off the row: #1721 replaced the plaintext
    ``Principal.access_token`` column with ``token_hash``, so there is no readable
    credential on the row to return. That reader derives the plaintext the way
    ``PrincipalFactory`` derived the hash (``plaintext_token_for``), which is what makes
    the bearer handed back here the one that authenticates as this principal — and
    reusing it keeps ONE derivation for the token every leg of this env presents.
    """
    session = env._session
    assert session is not None, "enable_request_signing() must be called inside the env's `with` block"
    _tenant, principal = env.setup_default_data()
    principal.agent_url = agent_url
    ensure_declarable_identity_host(env)
    session.commit()
    token = env._principal_token()
    assert token is not None, (
        f"no bearer could be derived for principal {principal.principal_id!r}; a signing counterparty "
        "must present one, because the verifier reads token -> Principal -> agent_url"
    )
    return token


def ensure_declarable_identity_host(env: Any) -> None:
    """Give the env's tenant a DOTTED ``virtual_host`` if it has not got one.

    ``identity.brand_json_url`` is DERIVED from the virtual host, and any non-empty
    ``request_signing`` bucket fires that pointer's pinned ``required_when``, which
    fixes it to ``^https://``. ``_get_protocol_for_domain`` deliberately derives
    ``http`` for localhost and single-label hosts — and ``test_tenant``'s factory
    default is single-label — so on the default integration host the whole
    declaration is REFUSED. The consequence is not a loud failure: every operation
    stays in the ``none`` bucket, the signed request is waved through unverified, and
    the suite reads as "the seam did not sign".

    Called from both the capability builder and
    :meth:`~tests.harness._base.BaseTestEnv.declare_request_signing`, because either
    may run first and neither may assume the other did.
    """
    from src.core.database.models import Tenant
    from tests.helpers.signing import SIGNING_AGENT_HOST

    session = env._session
    tenant = session.get(Tenant, env._tenant_id) if session is not None else None
    if tenant is not None and "." not in (tenant.virtual_host or ""):
        tenant.virtual_host = SIGNING_AGENT_HOST
