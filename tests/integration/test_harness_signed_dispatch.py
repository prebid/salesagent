"""salesagent-n78j0.1.1: the cross-transport env can send a SIGNED request.

The #1757 review's central finding: the headline property this work exists to
provide — a signed request accepted, an unsigned one refused, identically on
every transport — was asserted by code shape and never OBSERVED, and that is
what let the A2A credential-location bypass through. This file is the only
place the harness's signing is observed end to end.

SCOPE. All four WIRE legs. The three IN-PROCESS ones — ``rest``, ``a2a``,
``mcp`` — share one pair, driven from one parametrization rather than three
copies: the rules a signed dispatch must obey are identical on every transport,
so a per-leg copy of the pair would be the duplication the project treats as a
correctness defect (CLAUDE.md, DRY). ``e2e_rest`` is graded HERE TOO, by the
third test, on the same env and through the same ``call_via`` seam — relocating
it into a per-transport module of its own is exactly the cheapest-path failure
(SF-5) this whole epic exists to undo.

WHY ``e2e_rest`` NEEDS ITS OWN TEST RATHER THAN A THIRD PARAMETRIZATION. Not
because the property differs — it is the same property — but because the leg
crosses a PROCESS boundary and the oracle below cannot. ``verifier_spy`` patches
the verifier in THIS process; the live server's verifier runs in another
container. Two observables do cross, and both are wire facts:

* ACCEPTED: ``adcp_request_signature_verified_total{operation,keyid}`` scraped
  over HTTP, incremented by EXACTLY 1. ``record_signature_verified`` has one
  call site in ``src/`` (``request_verifier_middleware``), on the branch reached
  only after the verifier returns AND Tier 3 passes. Being POSITIVE, the usual
  objection to a metric delta — that a zero is equally produced by an
  unresolvable counterparty, a ``none`` bucket or a 404 scrape — does not apply:
  a 404 scrape yields NO samples and errors, and every ambiguity listed produces
  0, not 1. The per-capability ``keyid`` is what makes "exactly 1" a claim about
  THIS request rather than about the server's cumulative session.
* REFUSED: the byte-exact ``WWW-Authenticate: Signature error="..."`` challenge,
  read with ``rejection_code``. A bare 401 is satisfied by auth middleware
  rejecting first, by a 404 wearing a 401, and by a malformed-header precheck.

Because those two observations are a scrape window and a challenge header rather
than a spy, they belong in ONE sequenced test (scrape -> signed -> scrape ->
unsigned) instead of two independent ones.

WHY THE ORACLE IS ``verifier_spy`` AND NOT THE STATUS CODE. A 200 does not mean
"the signature was accepted" — it is equally true of a middleware that never
looked at the request. Under ``required_for`` an unsigned request carrying a
VALID bearer is also 200, and correctly so: security.mdx :1269 says such a
request MUST NOT be rejected for the missing signature (only an unacceptable
credential, :1268, is refused). So status alone cannot distinguish "signed and
verified" from "unsigned and waved through" — the first draft of this file got
that wrong. ``verifier_spy`` records what the REAL verifier was handed and what
it returned; ``VerifiedSigner.key_id`` is the positive-path observable that
proves the signature this seam produced was actually verified.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from adcp.signing import REQUEST_SIGNATURE_HEADER_MALFORMED
from adcp.types import GetAdcpCapabilitiesResponse

from tests.harness._base import BareIntegrationEnv
from tests.harness.transport import E2EConfig, Transport, TransportResult
from tests.helpers.signing import (
    BODYLESS_ADCP_PATH,
    LADDER_OPERATIONS,
    SIGNATURE_REALIZATIONS,
    SIGNING_AGENT_HOST,
    SIGNING_PRINCIPAL_ID,
    SIGNING_TENANT_ID,
    VERIFIED_METRIC,
    VERIFIER_ERROR,
    VERIFIER_RESULT,
    bucketed_declaration,
    declared_posture,
    posture_declaration_document,
    rejection_code,
    scraped_verified_count,
    verifier_spy,
)

#: The one operation the pair is dispatched as, on every leg. It has to be ONE
#: operation or the legs are not comparable: ``bucketed_declaration`` puts it in
#: ``required_for``, and the verifier grades whatever name the request resolves
#: to — ``get_adcp_capabilities`` is the name on all three surfaces
#: (``POST /api/v1/capabilities`` through the route registry, the ``tools/call``
#: ``params.name`` on ``/mcp``, the explicit skill in the ``message/send`` data
#: part on ``/a2a``; ``src/core/signing/operations.py``). Every OTHER operation
#: is left in the ``none`` bucket by that same declaration, which is what keeps
#: the controls meaningful.
SIGNED_OPERATION = "get_adcp_capabilities"

#: The legs this file grades. ``IMPL`` is excluded permanently — a direct
#: in-process function call has no wire, so there is nothing to sign — and the
#: e2e legs for the reason in the module docstring.
IN_PROCESS_LEGS = (Transport.REST, Transport.A2A, Transport.MCP)


class _SignedDispatchEnv(BareIntegrationEnv):
    """One env that reaches ``get_adcp_capabilities`` through all three legs.

    ``BareIntegrationEnv`` carries the real DB session and factory binding but
    no adapter mocks, which is all a signature check needs: the request is
    accepted or refused by the middleware before any domain handler runs, and
    capabilities assembly degrades gracefully when the adapter lookup fails
    (``src/core/tools/capabilities.py``), so no stand-in has to be installed for
    the call to complete.

    One env rather than one per transport, because the subject under test is the
    ENV's signing capability: a per-leg env would let the legs drift on identity,
    posture or operation and the cross-transport claim would stop being one
    claim. The REST leg keeps the raw dict parser the spike used — its subject is
    the signature, not the payload shape, and the typed parse is already graded
    by ``CapabilitiesEnv``'s own suites.
    """

    REST_ENDPOINT = BODYLESS_ADCP_PATH

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        return dict(kwargs)

    def parse_rest_response(self, data: Any) -> Any:  # type: ignore[override]
        return data

    def parse_rest_error(self, status_code: int, data: Any) -> Exception:  # type: ignore[override]
        return AssertionError(f"HTTP {status_code}: {data}")

    def call_a2a(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        return self._run_a2a_handler(SIGNED_OPERATION, GetAdcpCapabilitiesResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        return self._run_mcp_client(SIGNED_OPERATION, GetAdcpCapabilitiesResponse, **kwargs)


def _assert_verified_under_counterparty_key(
    calls: list[dict[str, Any]], result: TransportResult, expected_key_id: str
) -> None:
    """The positive oracle, stated once for every leg.

    *expected_key_id* is the capability's OWN kid rather than the shared module
    constant: since the e2e leg's oracle is a metric series labelled by ``keyid``,
    every capability now mints a kid of its own (see
    ``signing_capability.unique_run_id``). Asserting against the capability makes
    this a strictly stronger claim than the constant was — the signature must be
    verified under the key THIS env published, not merely under a key named like it.

    Exactly ONE GRADED verification is the claim, and it is a claim in its own
    right on the MCP leg: an MCP session puts SEVERAL requests on the wire
    (``initialize``, ``notifications/initialized``, the ``tools/call``, the
    session ``DELETE``), all of them SIGNED by the harness, and only the
    ``tools/call`` resolves to a graded operation. The others name a protocol
    method with no declared bucket, or no operation at all, so they fall in the
    narrowed ``none`` bucket.

    Those frames DO reach the verifier — ``security.mdx`` :1226 binds a verifier
    to pre-check a signed request "even for operations not in ``required_for``",
    and they are signed and on an AdCP surface. They clear the pre-check, fail
    step 7 against the empty resolver the narrowed ``none`` path supplies BY
    CONSTRUCTION, and pass through. So counting verifier INVOCATIONS stopped
    being a proxy for "one graded verification" when the verifier gained its
    pre-check phase; it now counts pass-throughs too.

    Counting frames that reached a VERIFIED outcome states the obligation
    directly and is strictly stronger. Two above one still means session frames
    were graded as operations — the failure this guards — and zero still means
    the request never reached the verifier at all. What it no longer reddens on
    is a pre-checked pass-through, which is specified behaviour.
    """
    graded = [call for call in calls if VERIFIER_ERROR not in call]
    assert len(graded) == 1, (
        f"exactly one frame must be GRADED against the counterparty key; {len(graded)} were "
        f"(of {len(calls)} verifier invocation(s) — the rest are narrowed-none pre-check "
        "pass-throughs, which do not count and must not be graded)"
    )
    signer = graded[0].get(VERIFIER_RESULT)
    assert signer is not None, (
        "the verifier RAISED on the signature this seam produced — the harness "
        "signed bytes other than the ones it sent, or under a key the "
        "counterparty's trust root does not publish"
    )
    assert signer.key_id == expected_key_id, (
        f"verified against {signer.key_id!r}, expected the counterparty key {expected_key_id!r}"
    )
    assert result.is_success, (
        f"a verified request was still refused: {result.envelope} "
        f"error={result.error!r} wire={result.wire_error_envelope!r}"
    )


@pytest.mark.requires_db
@pytest.mark.parametrize("transport", IN_PROCESS_LEGS, ids=[leg.value for leg in IN_PROCESS_LEGS])
class TestSignedDispatchAcrossTransports:
    """``call_via(<leg>, signed=...)`` — does the seam really sign, on every leg?"""

    def test_signed_dispatch_is_verified_against_the_counterparty_key(self, integration_db, transport):
        """``signed=True`` puts a signature on the wire that the REAL verifier accepts.

        Also proves the seam signs the EXACT bytes it sends: each leg must
        serialize once and send those bytes, so the ``content-digest`` the
        signature covers matches the body on the wire. Re-serializing (httpx's
        ``json=``, or a second ``model_dump`` between signing and sending)
        surfaces here as the verifier raising
        ``request_signature_digest_mismatch`` instead of returning a signer.

        A leg with no signature realization does NOT fail here quietly: it
        refuses ``signed=True`` outright rather than sending an unsigned request,
        so the failure is "this leg cannot sign" and never a green that hides an
        unsigned send.
        """
        with _SignedDispatchEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            capability = env.enable_request_signing()

            with declared_posture(**bucketed_declaration("required", *LADDER_OPERATIONS)), verifier_spy() as calls:
                result = env.call_via(transport, signed=True)

            _assert_verified_under_counterparty_key(calls, result, capability.key_id)

    def test_unsigned_dispatch_runs_no_crypto(self, integration_db, transport):
        """The control: identical call, ``signed=False`` — the verifier never runs.

        Same bearer, same tenant hint, same operation, same posture; the ONLY
        difference is the signature. Without this the test above would pass even
        if ``signed=True`` were silently sending an unsigned request, because a
        bearer-authenticated unsigned request is a spec-correct 200 (:1269).

        This is a control, not a red: it must hold BEFORE the a2a/mcp legs are
        wired (they have no wire to sign on) and AFTER (they have one, and do not
        sign it). If wiring a leg turns this red, that leg is signing when it was
        asked not to — the invariant "signed and unsigned differ by NOTHING
        except the signature" broken from the other side.
        """
        with _SignedDispatchEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            env.enable_request_signing()

            with declared_posture(**bucketed_declaration("required", *LADDER_OPERATIONS)), verifier_spy() as calls:
                result = env.call_via(transport, signed=False)

            assert calls == [], f"an UNSIGNED request ran signature verification {len(calls)} time(s)"
            assert result.is_success, (
                f"the unsigned control must still complete, or it is not a control: "
                f"{result.envelope} error={result.error!r} wire={result.wire_error_envelope!r}"
            )


@pytest.mark.requires_db
def test_refusal_is_graded_by_the_challenge_not_by_the_status(integration_db):
    """``assert_signature_challenge`` grades WHICH refusal happened, byte-exactly.

    The refusal half of this file's property, on the in-process leg — until now
    only the live-stack test below could state it, and it stated it by reading
    ``WWW-Authenticate`` by hand.

    WHY A CREDENTIAL-LESS CALL. security.mdx :1269 makes an unsigned request
    carrying a valid bearer a spec-correct 200, so ``signed=False`` ALONE never
    reaches the verifier's refusal branch. ``identity=None`` — the harness's
    established "send without a credential" — is what leaves the missing
    signature as the only thing the request can be refused for.

    WHY NOT ``status_code == 401``. Because a 401 is equally produced by the auth
    middleware rejecting first, by a 404 wearing a 401, and by the
    malformed-header precheck — and because a status-shaped oracle on this path
    has already been observed to be vacuous: forcing a leg to dispatch UNSIGNED
    in salesagent-n78j0.1.1 left ``is_success`` passing.

    The three negatives are the assertion's own non-vacuity, graded rather than
    argued: the wrong (but real) code fails, a code outside the verifier's
    vocabulary fails, and — the one that matters — an ACCEPTED dispatch fails
    instead of being read as a refusal.

    ONLY THE REST LEG, deliberately and for now: ``_run_a2a_over_http`` /
    ``_run_mcp_over_http`` always attach the capability's bearer and their
    JSON-RPC readers discard the HTTP response on a 4xx, so neither can produce
    an anonymous refusal to grade yet. The helper does not paper over that — on
    such a result it FAILS, naming the cause.
    """
    with _SignedDispatchEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
        env.enable_request_signing()

        with declared_posture(**bucketed_declaration("required", *LADDER_OPERATIONS)):
            refused = env.call_via(Transport.REST, signed=False, identity=None)
            accepted = env.call_via(Transport.REST, signed=True)

        refused.assert_signature_challenge("request_signature_required")

        with pytest.raises(AssertionError, match="request_signature_invalid"):
            refused.assert_signature_challenge("request_signature_invalid")

        with pytest.raises(AssertionError, match="not a request-signature rejection code"):
            refused.assert_signature_challenge("REQUEST_SIGNATURE_REQUIRED")

        assert accepted.is_success, (
            f"the signed control must be accepted, or the refusal above is not attributable to the "
            f"missing signature: {accepted.envelope} error={accepted.error!r}"
        )
        with pytest.raises(AssertionError, match="request_signature_required"):
            accepted.assert_signature_challenge("request_signature_required")


# ---------------------------------------------------------------------------
# Two REGRESSION LOCKS on the seams salesagent-nx8jp.9 widened
# ---------------------------------------------------------------------------
#
# Neither of these is a RED grader and neither could be. A lock says "this holds,
# and HERE IS WHAT BREAKS IT"; a red grader says "this fails until I fix it". The
# behaviours below hold on the tree that introduces them, so each is stated together
# with the NAMED MUTATION it was checked against — recording a lock without one is
# the false claim about coverage this epic's standard forbids.

#: Every realization that puts something signature-shaped on the wire. DERIVED rather
#: than listed, so a fifth realization added to the type is locked by construction
#: instead of silently inheriting the ``impl`` exemption the epic exists to close.
_WIRE_REALIZATIONS = tuple(r for r in SIGNATURE_REALIZATIONS if r is not False)


@pytest.mark.requires_db
@pytest.mark.parametrize("signed", _WIRE_REALIZATIONS, ids=[str(r) for r in _WIRE_REALIZATIONS])
def test_impl_refuses_every_signature_realization(integration_db, signed):
    """LOCK 1 — ``impl`` refuses ANY realization, not just ``signed=True``.

    THE CONTRACT'S FIRST ASSERTION. ``_refuse_signed_impl``
    (``tests/harness/dispatchers.py``) has exactly two references — its definition
    and the single ``if signed:`` call above it — and :data:`IN_PROCESS_LEGS`
    excludes ``IMPL`` permanently, so until now NOTHING graded it. It was believed
    by reading, which is the epic's own headline failure mode.

    It has to be re-stated the moment ``signed`` stops being a bool: ``impl`` is a
    direct in-process function call with nothing between the caller and ``_impl``,
    so a MALFORMED signature has exactly as little meaning there as a valid one, and
    a leg that ignored the realization would run the scenario unsigned and let it
    pass having signed nothing.

    PINNED AS ``pytest.raises(NotImplementedError)`` WITH THE MESSAGE, not as
    ``result.is_error``: ``ImplDispatcher.dispatch`` wraps its ``call_impl`` in
    ``except Exception`` and hands back ``TransportResult(error=exc)``, so an
    is_error-shaped assertion is satisfied by ANY failure inside the call — this env
    does not implement ``call_impl`` at all, and its own ``NotImplementedError``
    would keep such a lock green forever. Only the refusal ESCAPES ``call_via``,
    because it is raised before the try.

    NAMED MUTATION (checked, salesagent-rt8ht.4): ``if signed:`` -> ``if signed is
    True:`` at ``dispatchers.py``. ``"malformed"`` and ``"tampered"`` then fall
    through to ``call_impl``, whose failure is swallowed into the result, and this
    test reddens with DID NOT RAISE for both — while the ``True`` parameter stays
    green, which is what says the mutation was the realization arm and not the
    refusal itself.
    """
    with _SignedDispatchEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
        with pytest.raises(NotImplementedError, match="has no meaning on transport 'impl'"):
            env.call_via(Transport.IMPL, signed=signed)

        # The control: the refusal is attributable to the REALIZATION, not to
        # dispatching on ``impl`` at all. Unsigned, the same call returns a result
        # (this env implements no ``call_impl``, so that result carries an error —
        # which is precisely why the assertion above cannot be an is_error check).
        unsigned = env.call_via(Transport.IMPL, signed=False)
        assert "has no meaning on transport 'impl'" not in str(unsigned.error), (
            f"an UNSIGNED impl dispatch was refused as if it carried a signature: {unsigned.error!r}"
        )


def _refused_frame(result: TransportResult) -> tuple[str, str | None]:
    """WHICH MCP frame the verifier refused: its JSON-RPC method and session id.

    Read off the httpx request the refusing response answers, so the attribution is
    made from the bytes the harness actually sent rather than from anything the
    harness reports about itself. ``mcp-session-id`` is present only on a frame sent
    AFTER ``initialize`` minted one, which makes it the wire's own evidence that the
    session was established.

    Exists because the two things a scenario needs to tell apart here are
    indistinguishable everywhere else: see :func:`test_a_failure_realization_reaches_
    the_operation_frame_not_the_handshake`.
    """
    import json

    response = result.raw_response
    assert response is not None, (
        f"expected a refusing HTTP response to attribute to a frame, got none "
        f"(is_error={result.is_error}, error={result.error!r}, payload={result.payload!r})"
    )
    request = response.request
    method = json.loads(request.content)["method"]
    return method, request.headers.get("mcp-session-id")


@pytest.mark.requires_db
def test_a_failure_realization_reaches_the_operation_frame_not_the_handshake(integration_db):
    """LOCK 2 — the graded-frame rule: MCP opens its session CLEANLY, then 401s.

    An MCP dispatch is THREE frames — ``initialize``, ``notifications/initialized``,
    ``tools/call`` — and only the last is the operation the scenario named. The
    realization must therefore attach to the last one alone: a session-wide
    realization 401s the ``initialize``, the operation frame is never sent, and the
    scenario grades a refusal of a frame it never mentioned.

    WHY THE OBVIOUS ASSERTION WOULD BE VACUOUS, and why this test is shaped the way
    it is. ``assert_signature_challenge`` grades the challenge header byte-exactly
    and DELIBERATELY not the status code. Both refusals are step-1 pre-check
    failures, which reject in every bucket, so both answer 401 with the IDENTICAL
    ``WWW-Authenticate: Signature error="request_signature_header_malformed"``; both
    arrive as a :class:`~tests.harness._base.WireRefusal` that ``McpDispatcher``
    surfaces as ``raw_response``. The challenge assertion below therefore passes
    under this test's own mutation. It is kept as SHAPE DOCUMENTATION — it says the
    refusal is the one the realization asks for — and it is explicitly NOT the
    discriminator.

    THE DISCRIMINATOR is the frame attribution, which is the only thing that differs:
    the refused request is the ``tools/call``, and it carries an ``mcp-session-id``,
    which exists only because ``initialize`` answered with one.

    NAMED MUTATION (checked, salesagent-rt8ht.4): make ``_mcp_open_session`` pass the
    realization through to its two ``_mcp_send`` calls instead of
    ``bool(self._signed_dispatch)``. The refused frame becomes ``initialize``, which
    carries no session id, and BOTH assertions in the lock redden while
    ``assert_signature_challenge`` stays green.
    """
    with _SignedDispatchEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
        env.enable_request_signing()

        with declared_posture(**bucketed_declaration("required", *LADDER_OPERATIONS)):
            refused = env.call_via(Transport.MCP, signed="malformed")

        # Shape documentation, not the discriminator — see the docstring.
        refused.assert_signature_challenge(REQUEST_SIGNATURE_HEADER_MALFORMED)

        method, session_id = _refused_frame(refused)
        assert method == "tools/call", (
            f"the malformed signature was refused on the {method!r} frame, not on the operation "
            "frame the scenario named. A handshake frame carrying the realization 401s before "
            "the operation is ever sent, and its challenge is byte-identical to the operation "
            "frame's — so every scenario asserting 'a malformed signature is refused' would pass "
            "having graded a frame it never mentioned. Handshake frames sign CORRECTLY whenever "
            "signing is on at all (BaseTestEnv._mcp_open_session)."
        )
        assert session_id is not None, (
            "the refused frame carried no mcp-session-id, so no session was ever established — "
            "the 401 came from the handshake and the operation frame was never sent. A session "
            "that cannot be opened has graded nothing."
        )


# ---------------------------------------------------------------------------
# The e2e_rest leg — the only one that leaves the process
# ---------------------------------------------------------------------------


def _live_stack() -> E2EConfig | None:
    """The live stack this run was given, or ``None`` if it was given none.

    Same contract as ``tests/bdd/conftest.py``'s ``e2e_stack``, deliberately: an
    ``E2E_BASE_URL`` that is SET but unreachable is a hard ERROR — the stack was
    explicitly configured and could not be reached, and degrading that to a skip
    reports a leg that never ran as success. Absent configuration means this run
    was never offered a stack (a plain integration slice), and the e2e leg below
    is simply not defined; the in-network runner sets these vars for every suite
    (``docker-compose.e2e.yml``'s ``tests`` service + tox ``pass_env``), so the
    leg IS graded on every full run.
    """
    base_url = os.environ.get("E2E_BASE_URL")
    postgres_url = os.environ.get("E2E_POSTGRES_URL")
    if not base_url or not postgres_url:
        return None
    try:
        httpx.get(f"{base_url}/health", timeout=10).raise_for_status()
    except Exception as exc:  # noqa: BLE001 - re-raised loudly below
        raise RuntimeError(
            f"E2E_BASE_URL={base_url!r} is set but {base_url}/health did not answer ({exc!r}). The signed "
            "e2e_rest leg cannot run. Start the in-network stack (run_all_tests.sh) or unset E2E_BASE_URL. "
            "Refusing to skip — a skipped e2e leg is a false green."
        ) from exc
    return E2EConfig(
        base_url=base_url,
        postgres_url=postgres_url,
        tls_base_url=os.environ.get("E2E_TLS_BASE_URL"),
        ca_bundle=os.environ.get("E2E_CA_BUNDLE"),
    )


LIVE_STACK = _live_stack()


@contextmanager
def _posture_declared_on_the_live_tenant(env: Any):
    """Declare ``required_for`` on the LIVE tenant, then put it back.

    Written through the env's own session — which in e2e mode is bound to the
    SERVER's database — and NOT through ``declared_posture``: a ``TenantConfigUoW``
    write from the runner opens its own engine against the runner's
    ``DATABASE_URL`` (the suite database, not the server's) and is empirically not
    visible to the live server's read. Same document either way
    (``posture_declaration_document``), different writer.

    ``virtual_host`` is set to a DOTTED host first because
    ``identity.brand_json_url`` is derived from it and the pinned ``required_when``
    fixes that pointer to ``^https://``: on a single-label host the derived pointer
    is ``http://`` and the WHOLE declaration is refused — which would leave the
    operation in the ``none`` bucket, pass the signed request through unverified,
    and read as "the seam did not sign".

    Restored on exit because the live database is SHARED with every other suite
    that talks to this stack; leaving ``required_for`` declared on its default
    tenant would silently change what those runs are grading.
    """
    from src.core.database.models import Tenant

    tenant = env.get_one(Tenant, tenant_id=env.tenant_id)
    assert tenant is not None, f"the live server database has no tenant {env.tenant_id!r} to declare a posture on"
    previous_declarations = tenant.capability_declarations
    previous_host = tenant.virtual_host

    env.configure_tenant_field("virtual_host", SIGNING_AGENT_HOST)
    tenant = env.get_one(Tenant, tenant_id=env.tenant_id)
    declaration = posture_declaration_document(tenant, bucketed_declaration("required", *LADDER_OPERATIONS))
    env.configure_tenant_field("capability_declarations", declaration)
    try:
        yield
    finally:
        env.configure_tenant_field("capability_declarations", previous_declarations)
        env.configure_tenant_field("virtual_host", previous_host)


if LIVE_STACK is not None:

    @pytest.mark.requires_db
    def test_signed_e2e_rest_dispatch_is_verified_by_the_live_server(integration_db):
        """``call_via(E2E_REST, signed=True)`` is verified by a verifier in ANOTHER container.

        The same property the three in-process legs assert, on the one leg that
        actually leaves the process — which owner decision D1 calls "the only
        really truthful one". Both observables are wire facts that cross the
        container boundary, and they are sequenced into ONE test because a metric
        window and a challenge header are not independent assertions:

        * ACCEPTED — ``adcp_request_signature_verified_total{operation,keyid}``
          moves by EXACTLY 1 across the signed dispatch. One call site in ``src/``
          (``record_signature_verified``), on the branch reached only after the
          verifier returns AND Tier 3 passes. 0 means the 2xx came from somewhere
          else (verifier disabled, bucket collapsed to ``none`` and the signed
          request passed through unverified, or plain bearer auth); >1 means the
          kid is not unique to this capability and the delta is measuring somebody
          else's request.
        * REFUSED — the unsigned, CREDENTIAL-LESS dispatch is answered with
          ``WWW-Authenticate: Signature error="request_signature_required"``, read
          byte-exactly with ``rejection_code``. Credential-less on purpose:
          security.mdx :1269 makes an unsigned request carrying a valid bearer a
          spec-correct 200, so only an unacceptable credential reaches the
          challenge branch. A bare 401 would not do — it is satisfied by the auth
          middleware rejecting first, by a 404 wearing a 401, and by a
          malformed-header precheck.

        The status code is NOT the oracle on either side, for the reason the
        module docstring gives.
        """
        with _SignedDispatchEnv(e2e_config=LIVE_STACK) as env:
            capability = env.enable_request_signing()

            with _posture_declared_on_the_live_tenant(env):
                before = scraped_verified_count(LIVE_STACK.base_url, capability.key_id, when="before")
                accepted = env.call_via(Transport.E2E_REST, signed=True)
                after = scraped_verified_count(LIVE_STACK.base_url, capability.key_id, when="after")
                refused = env.call_via(Transport.E2E_REST, signed=False, identity=None)

            assert accepted.is_success, (
                f"a signed dispatch over the live stack was refused: {accepted.envelope} "
                f"error={accepted.error!r} wire={accepted.wire_error_envelope!r}. "
                f"WWW-Authenticate={rejection_code(accepted.raw_response)!r}"
            )
            delta = after - before
            assert delta == 1.0, (
                f"{VERIFIED_METRIC}{{keyid={capability.key_id!r}}} must "
                f"increment by EXACTLY 1 across the signed dispatch; it moved by {delta}. The 2xx above is "
                "equally consistent with a request that was never verified, so this delta — not the status "
                "code — is what says the verifier ran and accepted THIS signature."
            )
            # Through the harness helper rather than a local header read: the
            # challenge is graded the same way on every leg, and the helper adds the
            # non-vacuity this call site had to state in prose (an unknown code, or
            # a result with no HTTP response, fails loudly instead of comparing
            # equal to None). A 2xx here still means what the prose said — the
            # posture never reached this request, and the accepted leg above proves
            # less than it appears to.
            refused.assert_signature_challenge("request_signature_required")
