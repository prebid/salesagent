"""Provision the storyboard agent to satisfy the signed-requests test-kit contract.

The contract is ``dist/compliance/{version}/test-kits/signed-requests-runner.yaml``. It
names five agent-side preconditions, and this module is the ONE owner of all five — the
three that are deployment settings (read by the server process from its environment) and
the two that are tenant state (written to the database).

Both halves are here rather than beside their consumers because they are one fact seen
twice: the counterparty this agent trusts. Split across a compose file and a seed script,
the ``agent_url`` in the registry and the ``agent_url`` on the principal drift, and the
symptom is a 401 on every positive vector with nothing naming the cause —
``_signature_credential`` looks the signer up by exactly that string
(``src/core/resolved_identity.py``) and an anonymous caller on ``create_media_buy`` is
``AUTH_MISSING``, which the challenge responder lifts to the same 401 a signature refusal
produces.

What the contract requires, and where each lands
------------------------------------------------
1. ``request_signing.required_for`` covering the graded operations -> the tenant's
   ``capability_declarations`` (:func:`declarations`).
2. A counterparty JWKS holding ``test-ed25519-2026`` and ``test-es256-2026`` with
   ``adcp_use: "request-signing"``, trusted as a registered test counterparty ->
   ``ADCP_SIGNING_COUNTERPARTY_REGISTRY`` (:func:`counterparty_registry`), plus the
   principal that JWKS resolves to (:func:`seed`).
3. ``test-revoked-2026`` pre-revoked before the negative phase ->
   ``ADCP_SIGNING_REVOKED_KEYIDS``.
4. A replay TTL of at least ``min_replay_ttl_seconds`` -> ``ADCP_SIGNING_REPLAY_TTL_OVERRIDES``.
5. The per-keyid replay cache at its configured cap for vector 020 ->
   ``ADCP_SIGNING_PER_KEYID_CAP_OVERRIDES``.

Why the registry and not the brand.json walk
--------------------------------------------
``_resolution_for`` consults ``counterparty_registry`` only when there is no
``agent_url`` to walk from, and ``agent_url`` comes from the principal the BEARER
resolved. The conformance runner sends no bearer on a vector probe — the signature is the
credential — so the walk has no input and the registry is the only path. That is the case
the registry was built for; its docstring says so.

Why this deployment must not signal production
-----------------------------------------------
``SigningSettings`` refuses a non-empty ``counterparty_registry`` (and both override maps)
under any production signal, and it is right to: the private keys of the conformance
corpus are PUBLISHED in ``keys.json``, so trusting those keyids in production would let
anyone at all sign as a registered counterparty. The storyboard agent is a grading
deployment that wants production's forward-compatible REQUEST BOUNDARY and nothing else
about production, so it declares that one axis explicitly
(``ADCP_PYDANTIC_EXTRA_MODE``) instead of claiming to be production. See the service
definition in ``docker-compose.e2e.yml``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.setup.init_database_ci import CI_TEST_SUBDOMAIN  # noqa: E402

#: The Host the conformance runner dials, and therefore the ``virtual_host`` the storyboard
#: tenant must answer to.
#:
#: A VECTOR PROBE CARRIES NO ROUTING HEADER. The runner's ``-H x-adcp-tenant=...`` reaches
#: the MCP ``initialize`` handshake and the ordinary storyboard steps, but a signed vector
#: is built from the vector's own headers plus an ``Mcp-Session-Id``
#: (``@adcp/sdk`` ``probe.mjs`` ``attachMcpSessionHeader``) — so ``_detect_tenant`` has only
#: the Host to go on. Without a tenant there is no posture, ``posture_for_tenant(None)``
#: falls back to the agent-level default whose buckets are all empty, and every negative
#: vector is SPEC-CORRECTLY answered 200.
#:
#: ``tests/storyboard/test_storyboard_conformance.py`` builds its agent URLs from this, so
#: the value the runner dials and the value the database answers to are one string.
STORYBOARD_VIRTUAL_HOST = "storyboard.adcp.test:8443"

#: The conformance runner AS A COUNTERPARTY: the ``agent_url`` its keys resolve at.
#:
#: A name, not a destination. Nothing dials it — a registry entry short-circuits the
#: three-hop walk and carries its JWKS inline (``build_registry_resolution``) — but it is
#: what ``identity.key_origins.request_signing`` is byte-matched against at checklist step
#: 7, and what ``get_principal_by_agent_url`` looks the established signer up by.
#:
#: Under ``.test``, which RFC 6761 §6.2 reserves precisely so it can never resolve.
COUNTERPARTY_KEY_ORIGIN = "https://runner.adcp-conformance.test"
COUNTERPARTY_AGENT_URL = f"{COUNTERPARTY_KEY_ORIGIN}/a2a"
COUNTERPARTY_JWKS_URI = f"{COUNTERPARTY_KEY_ORIGIN}/.well-known/jwks.json"

#: The principal the runner's verified signature establishes. It holds a token because the
#: schema requires one; nothing ever presents it, and it is deliberately NOT the CI token —
#: a second principal answering to ``ci-test-token`` would make the token lookup ambiguous.
COUNTERPARTY_PRINCIPAL_ID = "storyboard-conformance-runner"
COUNTERPARTY_PRINCIPAL_TOKEN = "storyboard-conformance-runner-token-not-presented"

#: The operation every graded vector but two addresses, and therefore the one operation
#: ``required_for`` has to name.
#:
#: The corpus is uniform about this: 26 of the 28 negative vectors declare
#: ``required_for: ["create_media_buy"]``. The two that do not are ``negative/027``
#: (``required_for: []`` — it grades the webhook-credential escalation, which fires
#: REGARDLESS of the bucket) and ``negative/028`` (the protocol-method namespace, which
#: this agent declines; see docs/design/signing-vs-request-boundary.md). So a single-entry
#: bucket grades everything a larger one would, and every operation added beyond it is a
#: promise to buyers that nothing here checks.
REQUIRED_FOR = ("create_media_buy",)

#: ``covers_content_digest``. The corpus asks for all three values and an agent has ONE:
#: ``negative/007`` needs ``required`` (a signature omitting content-digest must be
#: refused), ``negative/018`` needs ``forbidden`` (one covering it must be refused), and
#: every other vector is written against ``either``.
#:
#: ``either`` is the value that grades the most and mis-grades nothing: it costs vectors
#: 007 and 018, while ``required`` would additionally fail the three positives that do not
#: sign a digest and ``forbidden`` would fail ``positive/002``, which does. This is a
#: property of the corpus, not a gap in this agent — reported with the fixture PR.
COVERS_CONTENT_DIGEST = "either"

#: Test-kit ``stateful_vector_contract.revocation.pre_revoked_keyid``.
REVOKED_KEYID = "test-revoked-2026"

#: Test-kit ``stateful_vector_contract.rate_abuse.grading_target_per_keyid_cap_requests``.
#: The runner sends this many requests and expects the NEXT one refused. It is a GRADING
#: target and explicitly not a production recommendation — ``SigningSettings`` refuses it
#: as a global cap and accepts it only for a named counterparty, which is what this is.
GRADING_PER_KEYID_CAP = 100

#: The replay-row lifetime for the runner's keyids, in seconds.
#:
#: Bounded on BOTH sides by the test-kit, and the window between them is narrow:
#:
#: * above ``min_replay_ttl_seconds: 10`` (itself above ``max_interval_seconds: 5``), or
#:   the first of ``negative/016``'s two submissions evicts before the second arrives and
#:   the vector passes SPURIOUSLY — both accepted, no rejection observed;
#: * above ``window_seconds: 60``, or ``negative/020``'s rows drain before the 101st
#:   request and the cap is never reached;
#: * well BELOW what the vectors' own 300s signature window would otherwise leave behind
#:   (~360s once ``remember()`` raises the claim to ``expires - now + skew``), because
#:   020 drives one keyid to its cap of 100 and those rows would then trip step 9a for
#:   every LATER vector — including 016's first submission, which must be accepted.
#:
#: ``src/core/signing/replay_store.py`` carries the same derivation from the store's side.
GRADING_REPLAY_TTL_SECONDS = 70

#: Where the runner's keypairs are published. The VENDORED snapshot, not the downloaded
#: bundle: this module is read by ``scripts/test-stack.sh`` at compose time, before
#: anything has materialized a bundle, and the vendored tree is committed and sha256-pinned
#: by ``MANIFEST.json`` — so it is both always present and not a second copy of the keys.
_KEYS_JSON = (
    _PROJECT_ROOT / "tests" / "fixtures" / "adcp_conformance_vectors" / "3.1.1" / "request-signing" / "keys.json"
)

#: Members of a published JWK that are not part of the public key.
_PRIVATE_MEMBERS = ("_private_d_for_test_only", "$comment")


def counterparty_jwks() -> dict[str, Any]:
    """The runner's published keys, as a JWKS a verifier may trust.

    Every key in ``keys.json`` is included, and that is deliberate rather than lax: three
    of the four are there so a vector can be refused on its MERITS instead of on a missing
    key. ``negative/009`` presents ``test-gov-2026`` and must reach step 8 to be refused
    for its ``adcp_use``; ``negative/017`` presents ``test-revoked-2026`` and must reach
    step 9 to be refused as revoked. Omit either and both are refused at step 7 with
    ``request_signature_key_unknown`` — a rejection that grades as a FAIL because it is the
    wrong code, arrived at without ever running the check the vector is about.

    ``unknown-key-9999`` is in no JWKS anywhere, which is what keeps ``negative/008``
    honest.

    Private material is stripped. It is public spec data and the corpus says so, but a
    private key installed in a verifier's TRUST STORE is the wrong shape regardless of who
    else can read it.
    """
    published = json.loads(_KEYS_JSON.read_text())
    return {
        "keys": [
            {name: value for name, value in key.items() if name not in _PRIVATE_MEMBERS}
            for key in published["keys"]
            if "kid" in key
        ]
    }


def counterparty_registry() -> dict[str, dict[str, Any]]:
    """``ADCP_SIGNING_COUNTERPARTY_REGISTRY`` — every runner keyid, one counterparty.

    Keyed per keyid because that is the only handle a bearer-less request offers, and the
    setting refuses anything that looks like a pattern. All four entries name the SAME
    counterparty and carry the same JWKS: they are one agent with four published keys, and
    the SDK selects within the JWKS by ``kid``.
    """
    jwks = counterparty_jwks()
    entry = {
        "agent_url": COUNTERPARTY_AGENT_URL,
        "jwks_uri": COUNTERPARTY_JWKS_URI,
        "key_origin": COUNTERPARTY_KEY_ORIGIN,
        "jwks": jwks,
    }
    return {key["kid"]: dict(entry) for key in jwks["keys"]}


def signing_env() -> dict[str, str]:
    """The storyboard agent's signing environment, as ``NAME -> value``.

    Read by ``scripts/test-stack.sh``, which exports these before ``docker compose up`` so
    the service definition can interpolate them. Generated rather than written into the
    compose file because three of the four values are DERIVED — from ``keys.json`` and from
    the test-kit's own numbers — and a literal copy in YAML is a silent 401 the day the
    corpus is re-vendored.
    """
    registry = counterparty_registry()
    compact: dict[str, Any] = {"separators": (",", ":")}
    return {
        "ADCP_SIGNING_COUNTERPARTY_REGISTRY": json.dumps(registry, **compact),
        "ADCP_SIGNING_REVOKED_KEYIDS": REVOKED_KEYID,
        # Every registered keyid, not just the two the runner signs positives with: the cap
        # and the TTL bound how long ANY of them keeps replay rows, and a keyid left on the
        # production floor would hold rows for ~360s and trip step 9a for a later vector.
        "ADCP_SIGNING_PER_KEYID_CAP_OVERRIDES": json.dumps(dict.fromkeys(registry, GRADING_PER_KEYID_CAP), **compact),
        "ADCP_SIGNING_REPLAY_TTL_OVERRIDES": json.dumps(dict.fromkeys(registry, GRADING_REPLAY_TTL_SECONDS), **compact),
    }


def declarations(brand_json_url: str) -> dict[str, Any]:
    """The storyboard tenant's ``capability_declarations``.

    ``identity.brand_json_url`` is obliged by the posture, not chosen: a ``required_for``
    naming any operation triggers the pinned schema's ``required_when``, and the capability
    read path additionally byte-matches the declared value against the one this agent
    actually serves. So it is passed in, derived by the caller from the tenant row, rather
    than written here as a second literal of the same origin.
    """
    return {
        "request_signing": {
            "supported": True,
            "covers_content_digest": COVERS_CONTENT_DIGEST,
            "required_for": list(REQUIRED_FOR),
        },
        "identity": {"brand_json_url": brand_json_url},
    }


def seed() -> None:
    """Make the storyboard tenant answer to the runner's Host, posture and counterparty.

    Idempotent, and run from ``[testenv:storyboard]``'s ``commands_pre`` — AFTER the suites
    that share this database, which ``depends`` orders ahead of it. That ordering is load
    bearing: ``required_for`` changes what an UNAUTHENTICATED ``create_media_buy`` is
    answered (``request_signature_required`` rather than ``AUTH_MISSING``), and
    ``virtual_host`` changes the origin this tenant publishes its trust root at. Neither
    should reach a suite that did not ask for it.
    """
    from sqlalchemy import select

    from src.core.agent_identity import brand_json_url
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Tenant
    from src.core.database.repositories.principal import PrincipalRepository

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(subdomain=CI_TEST_SUBDOMAIN)).first()
        if tenant is None:
            raise SystemExit(
                f"No tenant with subdomain {CI_TEST_SUBDOMAIN!r}. Run scripts.setup.init_database_ci first."
            )

        tenant.virtual_host = STORYBOARD_VIRTUAL_HOST
        session.flush()  # so brand_json_url() derives from the host just written
        tenant.capability_declarations = declarations(brand_json_url(tenant))
        print(f"Storyboard tenant {tenant.tenant_id} answers to {STORYBOARD_VIRTUAL_HOST}")
        print(f"   posture: required_for={list(REQUIRED_FOR)} covers_content_digest={COVERS_CONTENT_DIGEST}")

        principals = PrincipalRepository(session, tenant.tenant_id)
        counterparty = principals.find_by_agent_url(COUNTERPARTY_AGENT_URL)
        if counterparty is None:
            principals.create_with_token(
                COUNTERPARTY_PRINCIPAL_TOKEN,
                principal_id=COUNTERPARTY_PRINCIPAL_ID,
                name="Storyboard conformance runner",
                platform_mappings={"mock": {"advertiser_id": "storyboard-conformance-runner"}},
                agent_url=COUNTERPARTY_AGENT_URL,
            )
            print(f"   counterparty principal created at {COUNTERPARTY_AGENT_URL}")
        else:
            print(f"   counterparty principal already at {COUNTERPARTY_AGENT_URL}")
        session.commit()


def main() -> None:
    """``seed`` by default; ``--env`` prints the settings for ``scripts/test-stack.sh``."""
    if "--env" in sys.argv[1:]:
        for name, value in signing_env().items():
            print(f"{name}={value}")
        return
    seed()


if __name__ == "__main__":
    main()
