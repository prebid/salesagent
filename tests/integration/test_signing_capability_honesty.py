"""D1 (#1291) — what we ADVERTISE is what we DO.

Core Invariant under test: *every signing field on the ``get_adcp_capabilities``
wire has exactly ONE source, and the side that ENFORCES it consumes the SAME
object.*

TDD RED. This module exists because that invariant is BROKEN in production right
now, in a direction nothing catches:

* C1 landed (``src/core/signing/webhook_sender_factory.py``). A tenant holding an
  ACTIVE signing key whose receiver registered no ``authentication`` gets a real
  RFC 9421 signature on every AdCP webhook — ``build_webhook_sender`` returns a
  sender whose ``signs_with_rfc9421`` is True.
* ``get_adcp_capabilities`` still answers ``webhook_signing: {supported: false}``,
  from the ``_WEBHOOK_SIGNING_UNSUPPORTED`` / ``_REQUEST_SIGNING_UNSUPPORTED``
  literals (``src/core/tools/capabilities.py``), on BOTH construction sites.

So the wire says "receivers MUST NOT expect a Signature header" while the socket
carries one. security.mdx @ v3.1.1 :1404 scopes ``webhook_signing.supported`` to
"any seller that emits webhooks" and reserves ``false`` for "the unsafe posture of
EMITTING UNSIGNED webhooks" — we are emitting SIGNED webhooks under a ``false``
declaration, which is the mirror image of the over-promise the STRICT declaration
policy was built to stop, and is just as unverifiable for the receiver.

Four groups, each grading one leg of the invariant, and every one of them driven
through a real surface rather than through a builder:

**1. The outbound half** (``TestOutboundWebhookSigningHonesty``) — the leg that is
actually diverging today, and the one the ticket's acceptance box omitted. The
oracle is production's own and it names D1 in its docstring:
``adcp.webhooks.WebhookSender.signs_with_rfc9421`` ("Boot-time validators read this
to enforce the ``webhook_signing.supported=true`` capability invariant"). For ONE
tenant and ONE receiver registration the emitted ``webhook_signing.supported`` must
EQUAL what ``build_webhook_sender`` actually returns — and both must equal the
posture the tenant can honestly hold, so a pair that agrees on the wrong answer
still fails.

The publishability gate is graded in the same parametrization rather than in a
separate test, because it is the same claim on the other side of one boundary: a
keyed tenant on an origin that cannot serve https must advertise ``false`` AND stop
taking the RFC 9421 arm. Key discovery for our outbound signatures runs through
``identity.brand_json_url``, which the pin fixes to ``^https://`` and which
security.mdx rejects otherwise (``request_signature_brand_json_url_missing``), so
on such a host every signature we emit is one no conformant receiver can resolve a
key for. ``canonical_agent_url`` returns ``http://localhost:8080`` when a tenant has
neither ``virtual_host`` nor ``SALES_AGENT_DOMAIN`` — the default integration shape —
so the https rows set a DOTTED ``virtual_host`` and the gated row sets an explicitly
localhost one. Neither is decoration: with no ``virtual_host`` at all every row
would grade the gated branch while reading like it graded the declared one.

**2. The declared algorithm equals the emitted one**
(``TestDeclaredAlgorithmsEqualTheEmittedOne``). ``algorithms`` is the set we WILL
SIGN WITH (security.mdx @ v3.1.1 :1419), so the oracle is the ``alg=`` parameter on
a delivered ``Signature-Input``, parsed by the SDK's own parser — never
``SIGNING_ALG_VALUES``, which is the ALLOWED set. The sibling suite
(``test_webhook_signing_boundary.py``) already grades membership; what is ungraded
is EQUALITY with what we declare, which is what a receiver statically validates.

**3. The inbound half** (``TestInboundRequestSigningHonesty``). ``request_signing``
is behavioral: its buckets change what the verifier does. The advertised block and
the ``VerifierCapability`` B1 enforces must be two views of ONE object, so the test
derives both and asserts they agree — plus asserts EXPLICITLY that ``warn_for`` and
the three ``protocol_methods_*`` buckets are ABSENT from ``VerifierCapability``
(SDK divergence #1, recorded on ``RequestSigningPosture.to_verifier_capability``),
so that lossy projection stays visible instead of decaying into silent
under-enforcement.

**4. Both construction sites, one builder** (``TestBothConstructionSitesAgree``).
``request_signing.supported`` is an AGENT-level fact — the pin defines it as
"Whether this agent VERIFIES RFC 9421 signatures on incoming requests", and
``RequestSignatureMiddleware`` is mounted process-wide with
``SigningConfig.verifier_enabled`` defaulting True. It is NOT key-backed (it
verifies with the COUNTERPARTY's keys), so a keyless tenant and a caller with no
resolved tenant at all must both see it true, while ``webhook_signing`` stays false
for exactly the reason it is false: no key.

Everything is read back off the REST wire (``TransportResult.wire_response`` is the
HTTP JSON body), never off a typed payload — the claim is about what a buyer
receives. Only the outbound SOCKET is stubbed (``tests.helpers.webhook_wire``). The
tenant's key is minted through production (``provision_signing_key``), read back
through the production repository, and key presence is derived by production's
``signing_key_backed`` — never re-derived here.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from collections.abc import Iterator
from typing import Any, NamedTuple

import pytest
from adcp.signing.verifier import VerifierCapability

from tests.harness.capabilities import CapabilitiesEnv
from tests.harness.transport import Transport
from tests.helpers.signing import deployment_kek, just_after_provisioning, provision_key, signing_key_repo
from tests.helpers.webhook_wire import capture_outbound_webhooks, signature_input_params

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_WEBHOOK_URL = "https://buyer.example.com/adcp/notifications"
_KID = "capability-honesty-key-1"
_ALG = "ed25519"

#: An origin that CANNOT serve https, so ``canonical_agent_url`` derives ``http://``
#: and no conformant ``brand_json_url`` exists for it. ``_is_localhost``
#: (``src/core/domain_config.py``) is the production predicate; this value is the
#: shape an integration tenant falls back to when nothing is configured, which is
#: precisely why the gate has to be graded rather than assumed unreachable.
_UNPUBLISHABLE_HOST = "localhost:8080"

#: The buckets ``VerifierCapability`` silently DROPS. Asserted absent rather than
#: remembered: the SDK carries 4 of the 8 schema properties and 2 of the 6 buckets,
#: and a bucket handed to it "looks like configuration and does nothing".
_BUCKETS_THE_SDK_DROPS = frozenset(
    {"warn_for", "protocol_methods_required_for", "protocol_methods_warn_for", "protocol_methods_supported_for"}
)


class _Seeded(NamedTuple):
    """One tenant, its ORM row, and the tenant-scoped signing repository."""

    env: CapabilitiesEnv
    tenant: Any
    principal: Any
    repo: Any
    tenant_id: str


@pytest.fixture
def honesty_env(integration_db, monkeypatch, request) -> Iterator[CapabilitiesEnv]:
    """A ``CapabilitiesEnv`` whose tenant id is unique to this test.

    Unique because the integration database is not rolled back between tests here
    and ``setup_default_data`` is get-or-create: a shared tenant id would carry a
    previous test's ``signing_keys`` row into the keyless rows, and every "we do
    not sign" assertion would then hold for the wrong reason.

    ``deployment_kek`` first — a ``db:`` mint refuses without the deployment-wide
    KEK (nothing is written to any filesystem; the private PEM lives ENCRYPTED on
    the ``signing_keys`` row under the KEK named by
    ``SigningConfig.key_passphrase_env``), so without it every keyed row would fail
    on provisioning rather than on the behavior it grades.
    """
    slug = re.sub(r"[^a-z0-9]", "", request.node.name.lower())[-24:]
    tenant_id = f"d1honesty_{slug}"

    with deployment_kek(monkeypatch), CapabilitiesEnv(tenant_id=tenant_id, principal_id=f"p_{slug}"[:40]) as env:
        yield env


def _seed(env: CapabilitiesEnv, *, virtual_host: str, keyed: bool) -> _Seeded:
    """Create the tenant at *virtual_host*, optionally minting its signing key.

    ``configure_tenant_field`` writes BOTH the ``tenants`` row (which is where
    ``canonical_agent_url`` reads the host from, via the ORM ``Tenant`` D1 resolves
    inside its unit of work) and the in-memory tenant dict the resolved identity
    carries — one seam, so the two cannot disagree about which host this tenant is.
    """
    tenant, principal = env.setup_default_data()
    env.configure_tenant_field("virtual_host", virtual_host)

    tenant_id = tenant.tenant_id
    repo = signing_key_repo(env, tenant_id)
    if keyed:
        provision_key(repo, tenant_id, _KID, alg=_ALG)

    return _Seeded(env=env, tenant=tenant, principal=principal, repo=repo, tenant_id=tenant_id)


def _register_receiver(seeded: _Seeded, **registration: Any) -> Any:
    """Seed the buyer's ``PushNotificationConfig`` row — the ONE mode selector.

    No ``authentication`` by default, which is the default shape of every
    registration and therefore the shape that selects the RFC 9421 arm
    (security.mdx @ v3.1.1 :1424).
    """
    from tests.factories import PushNotificationConfigFactory

    return PushNotificationConfigFactory(
        tenant=seeded.tenant,
        principal=seeded.principal,
        url=_WEBHOOK_URL,
        **registration,
    )


def _capabilities_wire(env: CapabilitiesEnv, **kwargs: Any) -> dict[str, Any]:
    """GET ``/api/v1/capabilities`` and hand back the HTTP JSON body.

    The wire, not the typed payload: a serializer round-trip proves the model is
    self-consistent, and the claim here is about the bytes a buyer receives.
    """
    result = env.call_via(Transport.REST, **kwargs)

    assert result.is_success, (
        "GET /api/v1/capabilities must answer a successful capabilities document for a tenant "
        "whose signing declaration is legal; it failed instead. envelope="
        f"{result.envelope!r} wire_error={result.wire_error_envelope!r}"
    )
    wire = result.wire_response
    assert wire is not None, "the REST dispatcher captured no wire body, so nothing below grades the wire"
    return wire


def _key_is_live(repo: Any, now: Any) -> None:
    """Assert through production that this tenant really can sign at *now*.

    ``signing_key_backed`` is THE single key-presence derivation (``signs`` off
    ``active_at``, ``publishes`` off ``publishable_at`` — the two facts sit on
    opposite sides of that distinction on purpose). Asserting through it rather
    than re-querying the table is what stops this suite growing a second one, and
    without the control a green "we do not sign" would be equally explained by a
    key that never became active.
    """
    from src.core.signing.posture import signing_key_backed

    assert signing_key_backed(repo, now=now).signs is True, (
        "the tenant holds no ACTIVE signing key at the instant under test, so every assertion "
        "about signing below would hold for the wrong reason"
    )


# ---------------------------------------------------------------------------
# 1. The outbound half — the live divergence, and the publishability gate
# ---------------------------------------------------------------------------


class TestOutboundWebhookSigningHonesty:
    """Emitted ``webhook_signing`` equals what ``build_webhook_sender`` does."""

    @pytest.mark.parametrize(
        ("virtual_host", "keyed", "expected"),
        [
            ("seller-signs.example.com", True, True),
            (_UNPUBLISHABLE_HOST, True, False),
            ("seller-keyless.example.com", False, False),
        ],
        ids=["keyed-publishable", "keyed-unpublishable", "keyless-publishable"],
    )
    def test_emitted_webhook_signing_supported_equals_whether_the_sender_signs(
        self, honesty_env, virtual_host, keyed, expected
    ):
        """The wire and the sender must give the SAME answer, and the RIGHT one.

        Three rows, one boundary each:

        * ``keyed-publishable`` — the live divergence. The sender signs today; the
          wire says ``false``. This row is the whole reason the module exists.
        * ``keyed-unpublishable`` — the publishability gate. The wire is already
          ``false`` here by accident (it is always ``false``), so what this row
          actually grades is the SENDER: on an origin whose ``brand_json_url``
          cannot be ``^https://``, the RFC 9421 arm must be dropped, because a
          signature no receiver can resolve a key for is not a capability. The
          gate therefore has to sit on the SHARED posture object, not on the
          advertise side alone.
        * ``keyless-publishable`` — the control, and a pin. Without it the equality
          above is satisfiable by a factory that never returns a signing sender at
          all; and it holds production to the rule that ``false`` may become a
          different VALUE but never SILENCE (the schema permits omitting the block,
          and omission is the dishonest declaration this epic exists to prevent).

        Both sides are asserted against ``expected`` rather than only against each
        other: a pair that agrees on the wrong answer is still a false declaration.
        """
        from src.core.signing.webhook_sender_factory import build_webhook_sender

        seeded = _seed(honesty_env, virtual_host=virtual_host, keyed=keyed)
        config = _register_receiver(seeded)
        now = just_after_provisioning()
        if keyed:
            _key_is_live(seeded.repo, now)

        wire = _capabilities_wire(honesty_env)

        # Non-vacuity: media_buy is built only on the tenant-resolved path, so
        # without this the assertions could be reading the no-tenant minimal
        # response, which declares the same two blocks.
        assert wire.get("media_buy") is not None, (
            "this must be the TENANT-RESOLVED capabilities response, not the no-tenant minimal one"
        )

        block = wire.get("webhook_signing")
        assert block is not None, (
            "webhook_signing is ABSENT from the wire. Omission is schema-legal and is exactly the "
            "dishonest declaration the STRICT policy exists to prevent: a receiver cannot tell "
            f"'this seller does not sign' from 'this seller did not say'. wire keys: {sorted(wire)}"
        )

        sender = build_webhook_sender(config=config, tenant_id=seeded.tenant_id, repo=seeded.repo, now=now)

        assert block["supported"] is expected, (
            f"tenant at {virtual_host!r} (keyed={keyed}) must advertise webhook_signing.supported="
            f"{expected}; the wire says {block['supported']!r}. security.mdx @ v3.1.1 :1404 reserves "
            "false for the posture of EMITTING UNSIGNED webhooks, and the emitted value must be "
            "derived from key material plus trust-root publishability, never from a literal"
        )
        assert sender.signs_with_rfc9421 is expected, (
            f"tenant at {virtual_host!r} (keyed={keyed}) must have signs_with_rfc9421={expected}; "
            f"build_webhook_sender returned {sender.signs_with_rfc9421!r} for a receiver that "
            "registered no `authentication`. The wire and the sender read ONE posture object, so "
            "the publishability gate binds the sender too: an unresolvable signature is not a "
            "capability, it is an unverifiable one"
        )
        assert block["supported"] is sender.signs_with_rfc9421, (
            f"advertise/enforce DIVERGENCE: the wire declares webhook_signing.supported="
            f"{block['supported']!r} while build_webhook_sender produces "
            f"signs_with_rfc9421={sender.signs_with_rfc9421!r} for the same tenant and the same "
            "receiver registration. One of the two is lying to the buyer"
        )


# ---------------------------------------------------------------------------
# 2. The declared algorithm set equals the one on the wire
# ---------------------------------------------------------------------------


class TestDeclaredAlgorithmsEqualTheEmittedOne:
    """``algorithms`` is what we WILL sign with, so the delivery is the oracle."""

    def test_declared_algorithms_equal_the_alg_on_the_delivered_signature(self, honesty_env):
        """``webhook_signing.algorithms`` == the ``alg=`` on a real ``Signature-Input``.

        Graded on an actual delivery from a production sender, and against the
        parameter parsed by the SDK's own structured-field parser, so neither side
        is a literal this module could have copied from the other. A receiver
        statically validating the declaration against the wire rejects every
        delivery the moment these two disagree — which is the same check C1 already
        enforces on the emit side, here closed on the advertise side.

        One key of one algorithm, deliberately: a tenant holding a single ed25519
        key that declared both profile algorithms would promise one it never emits,
        so ``algorithms`` must come from the ACTIVE key row and not from the ALLOWED
        set.
        """
        from src.services.protocol_webhook_service import ProtocolWebhookService

        seeded = _seed(honesty_env, virtual_host="seller-algs.example.com", keyed=True)
        config = _register_receiver(seeded)
        _key_is_live(seeded.repo, just_after_provisioning())

        wire = _capabilities_wire(honesty_env)
        declared = (wire.get("webhook_signing") or {}).get("algorithms")

        with capture_outbound_webhooks() as captured:
            asyncio.run(
                ProtocolWebhookService().send_notification(
                    config,
                    {"event": "media_buy_status", "adcp_version": "3.1.1"},
                    {
                        "task_type": "media_buy_status",
                        "tenant_id": seeded.tenant_id,
                        "principal_id": seeded.principal.principal_id,
                    },
                )
            )

        assert len(captured) == 1, (
            f"expected exactly one delivery to {_WEBHOOK_URL}, got {len(captured)} — the registered "
            "buyer was not told, or was told more than once, so there is no emitted alg to compare"
        )
        emitted_alg = signature_input_params(captured[0])["alg"]

        assert declared == [emitted_alg], (
            f"we advertise webhook_signing.algorithms={declared!r} but sign outbound webhooks with "
            f"alg={emitted_alg!r}. A receiver validating the declared algorithm set against the "
            "on-wire alg rejects every delivery; the set must be derived from the tenant's ACTIVE "
            "key row (security.mdx @ v3.1.1 :1419 — it is the set we WILL sign with, not the set "
            "the profile allows)"
        )


# ---------------------------------------------------------------------------
# 3. The inbound half — advertise and enforce are two views of ONE object
# ---------------------------------------------------------------------------


class TestInboundRequestSigningHonesty:
    """Advertised ``request_signing`` and the enforced ``VerifierCapability`` agree."""

    def test_advertised_request_signing_equals_the_enforced_verifier_capability(self, honesty_env):
        """One declaration, two views: the wire and what B1's middleware enforces.

        Advertising ``required_for: [create_media_buy]`` while the verifier does not
        enforce it is a false promise to buyers; the reverse silently rejects
        traffic we never said we would reject. So the test declares ONE posture and
        derives BOTH views from it — the wire through the REST response, the
        enforced capability through ``posture_for_tenant(...).to_verifier_capability()``,
        which is the sole reader the middleware uses.

        The declaration is deliberately non-trivial in the two ways that matter:
        it names a JSON-RPC method in the ``protocol_methods_*`` namespace (never in
        an AdCP bucket — security.mdx :1045-1059 requires that split be rejected at
        configuration time, not coerced), and it declares ``identity.brand_json_url``
        because a non-empty bucket fires the schema's ``required_when``. The value
        is the DERIVED one (``src/core/agent_identity.brand_json_url``), never a
        second literal: a second literal for a key origin is a
        ``request_signature_key_origin_mismatch`` waiting to happen.

        The last two assertions pin the SDK's lossy projection AS a loss. Four of
        the six buckets never reach ``VerifierCapability``; ``bucket_for`` keeps
        them. Asserting their absence is what stops a future refactor from handing
        them to the SDK — where they would read like configuration and enforce
        nothing.
        """
        from src.core.agent_identity import brand_json_url
        from src.core.database.models import Tenant
        from src.core.signing.posture import posture_for_tenant

        seeded = _seed(honesty_env, virtual_host="seller-inbound.example.com", keyed=False)
        orm_tenant = honesty_env.get_one(Tenant, tenant_id=seeded.tenant_id)
        assert orm_tenant is not None, "the tenant row must exist before its identity URLs are derived"

        declaration = {
            "supported": True,
            "covers_content_digest": "required",
            "supported_for": ["get_products", "create_media_buy", "list_creative_formats"],
            "required_for": ["create_media_buy"],
            "warn_for": ["get_products"],
            "protocol_methods_supported_for": ["tasks/get"],
            "protocol_methods_required_for": ["tasks/get"],
        }
        honesty_env.declare_capabilities(
            request_signing=declaration,
            identity={"brand_json_url": brand_json_url(orm_tenant)},
        )

        wire = _capabilities_wire(honesty_env)
        block = wire.get("request_signing")
        assert block is not None, (
            f"request_signing is ABSENT from the wire for a tenant that declared one; wire keys: {sorted(wire)}"
        )

        enforced = posture_for_tenant(honesty_env.identity_for(Transport.REST).tenant).to_verifier_capability()

        assert block["supported"] is enforced.supported, (
            f"advertised request_signing.supported={block['supported']!r} but the verifier enforces "
            f"supported={enforced.supported!r} — two sources for one field"
        )
        assert block["covers_content_digest"] == enforced.covers_content_digest, (
            f"advertised covers_content_digest={block['covers_content_digest']!r} but the verifier "
            f"enforces {enforced.covers_content_digest!r}: a buyer that omits Content-Digest is "
            "graded against a rule we never published"
        )
        assert set(block["required_for"]) == set(enforced.required_for), (
            f"advertised required_for={block['required_for']!r} but the verifier requires "
            f"{sorted(enforced.required_for)!r}. Advertising an operation we do not enforce is a "
            "false promise; enforcing one we did not advertise rejects traffic silently"
        )
        assert set(block["supported_for"]) == set(enforced.supported_for), (
            f"advertised supported_for={block['supported_for']!r} but the verifier accepts "
            f"{sorted(enforced.supported_for)!r}"
        )

        # The declaration must reach the wire unchanged — including the buckets the
        # SDK cannot carry, which is exactly why they must be on the wire.
        assert block["warn_for"] == declaration["warn_for"], (
            f"advertised warn_for={block.get('warn_for')!r}, declared {declaration['warn_for']!r}: "
            "warn_for is enforced by RequestSigningPosture.bucket_for, not by the SDK, so dropping "
            "it from the wire hides a shadow-mode rule buyers are already being graded against"
        )
        assert block["protocol_methods_required_for"] == declaration["protocol_methods_required_for"], (
            "the protocol_methods_* namespace must reach the wire as declared; advertised "
            f"{block.get('protocol_methods_required_for')!r}, declared "
            f"{declaration['protocol_methods_required_for']!r}"
        )

        projected = {field.name for field in dataclasses.fields(VerifierCapability)}
        assert _BUCKETS_THE_SDK_DROPS.isdisjoint(projected), (
            f"VerifierCapability now carries {sorted(_BUCKETS_THE_SDK_DROPS & projected)}. The "
            "projection was lossy on purpose and bucket_for compensates; if the SDK grew these "
            "fields, delete the compensation instead of leaving two enforcers"
        )


# ---------------------------------------------------------------------------
# 3b. identity — DERIVED for emission, and only where purpose-anchoring holds
# ---------------------------------------------------------------------------


class TestIdentityIsDerivedNeverReLiteralled:
    """The emitted trust-root pointer is ``agent_identity``'s output, not a copy."""

    def test_keyed_publishable_tenant_emits_the_derived_brand_json_url(self, honesty_env):
        """A declared posture obliges ``identity.brand_json_url``, and it is derived.

        A keyed tenant on a publishable origin gets
        ``webhook_signing.supported == true``, which is one of the pinned
        ``required_when`` triggers, so ``identity.brand_json_url`` MUST be present —
        and its value comes from ``src/core/agent_identity.brand_json_url``, the
        single URL derivation A3 landed. The verifier byte-matches these strings
        against where our keys actually resolved, so a second literal here is a
        ``request_signature_key_origin_mismatch`` by construction.

        ``key_origins.request_signing`` must be ABSENT even though A3 publishes a
        JWKS there: the pin's ``purpose_anchoring`` constraint requires a declared
        origin to have its posture, and with every ``request_signing`` bucket empty
        there is no posture to anchor it to. Emitting it anyway would also
        self-trigger ``required_when``, which is how an unconditional emission turns
        a conservative default into a rule about itself.
        """
        from src.core.agent_identity import brand_json_url
        from src.core.database.models import Tenant

        seeded = _seed(honesty_env, virtual_host="seller-identity.example.com", keyed=True)
        _register_receiver(seeded)
        _key_is_live(seeded.repo, just_after_provisioning())

        orm_tenant = honesty_env.get_one(Tenant, tenant_id=seeded.tenant_id)
        expected_url = brand_json_url(orm_tenant)
        assert expected_url.startswith("https://"), (
            "the derived brand_json_url must be https for this host, or the test grades the gated "
            f"branch while reading like it grades the declared one; got {expected_url!r}"
        )

        wire = _capabilities_wire(honesty_env)
        identity = wire.get("identity")

        assert identity is not None, (
            "a keyed tenant on a publishable origin advertises webhook_signing.supported true, "
            "which fires the pinned required_when trigger, so identity.brand_json_url MUST be "
            f"present; the wire carries no identity block at all. wire keys: {sorted(wire)}"
        )
        assert identity.get("brand_json_url") == expected_url, (
            f"emitted identity.brand_json_url={identity.get('brand_json_url')!r} but this tenant's "
            f"trust root is served at {expected_url!r} (src/core/agent_identity.brand_json_url). A "
            "counterparty resolves our signing keys through this URL and byte-matches the origin, "
            "so a second derivation is a request_signature_key_origin_mismatch waiting to happen"
        )
        assert (identity.get("key_origins") or {}).get("request_signing") is None, (
            "identity.key_origins.request_signing must NOT be emitted while every request_signing "
            "bucket is empty: the pin's purpose_anchoring constraint requires a declared origin to "
            "have its posture, and emitting it would self-trigger required_when. got "
            f"{identity.get('key_origins')!r}"
        )


# ---------------------------------------------------------------------------
# 4. Both construction sites, one builder
# ---------------------------------------------------------------------------


class TestBothConstructionSitesAgree:
    """The no-tenant and tenant-resolved responses come from ONE builder."""

    def test_no_tenant_response_declares_the_agent_level_verifier_posture(self, honesty_env):
        """A caller with no resolved tenant still gets the agent's real posture.

        ``request_signing.supported`` is defined by the pin as "Whether this agent
        VERIFIES RFC 9421 signatures on incoming requests" — an AGENT-level fact.
        ``RequestSignatureMiddleware`` is mounted process-wide and
        ``SigningConfig.verifier_enabled`` defaults True, so the agent genuinely
        does verify for a caller with no resolved tenant, and emitting ``false``
        there UNDER-declares a fact that is true. ``webhook_signing`` is the
        opposite case and stays ``false``: with no tenant there is no key, so we
        genuinely do not sign. ``identity`` is absent for the same reason — there is
        no trust root to point at.

        This is also the DRY leg: two independent literals for one wire field is
        the drift bug the ``_build_adcp_block`` extraction already exists to
        prevent, and the fix is ONE builder feeding both sites.
        """
        from src.core.config import get_config
        from tests.factories import PrincipalFactory

        _seed(honesty_env, virtual_host="seller-notenant.example.com", keyed=False)
        no_tenant = PrincipalFactory.make_identity(
            principal_id=None, tenant_id="d1honesty_no_tenant", tenant=None, protocol="rest"
        )

        wire = _capabilities_wire(honesty_env, identity=no_tenant)

        # Non-vacuity: the minimal response has no media_buy block, so this proves
        # the no-tenant branch really ran.
        assert wire.get("media_buy") is None, (
            "this must be the NO-TENANT minimal response; a media_buy block means a tenant resolved "
            "and the assertions below would grade the other construction site"
        )

        verifier_enabled = get_config().signing.verifier_enabled
        assert wire["request_signing"]["supported"] is verifier_enabled, (
            "the no-tenant response must declare request_signing.supported = "
            f"SigningConfig.verifier_enabled ({verifier_enabled}); got "
            f"{wire['request_signing']['supported']!r}. request_signing is not key-backed — it "
            "declares that we VERIFY with the COUNTERPARTY's keys — so the agent-level fact is the "
            "honest answer and a literal false under-declares it"
        )
        assert wire["webhook_signing"]["supported"] is False, (
            "the no-tenant response must declare webhook_signing.supported false: with no tenant "
            f"there is no key, so we genuinely do not sign. got {wire['webhook_signing']!r}"
        )
        assert wire.get("identity") is None, (
            "the no-tenant response must carry no identity block — there is no tenant trust root "
            f"to anchor a key origin against. got {wire.get('identity')!r}"
        )

    def test_keyless_tenant_verifies_but_does_not_sign_and_publishes_nothing(self, honesty_env):
        """The asymmetry, on the tenant-resolved site: verify true, sign false.

        The two blocks are backed by different things and a keyless tenant is where
        that shows. ``request_signing`` is backed by the mounted verifier plus
        ``verifier_enabled``; ``webhook_signing`` is backed by THIS tenant's own
        ``signing_keys`` row. Collapsing them onto one literal is what produced the
        current wire, where a keyless tenant and a keyed one are indistinguishable.

        ``identity`` stays ABSENT: with all six ``request_signing`` buckets empty
        and ``webhook_signing.supported`` false, none of the pinned
        ``required_when`` triggers fires, so the conservative default is emittable
        for a tenant with no trust root at all — which is what makes it safe to ship
        by default.
        """
        from src.core.config import get_config

        seeded = _seed(honesty_env, virtual_host="seller-verifies.example.com", keyed=False)
        wire = _capabilities_wire(honesty_env)

        assert wire.get("media_buy") is not None, (
            "this must be the TENANT-RESOLVED response, not the no-tenant minimal one"
        )

        verifier_enabled = get_config().signing.verifier_enabled
        assert wire["request_signing"]["supported"] is verifier_enabled, (
            f"tenant {seeded.tenant_id!r} holds no signing key, but request_signing is NOT "
            "key-backed: it declares that this agent verifies signatures on INCOMING requests "
            f"using the COUNTERPARTY's keys. Expected supported={verifier_enabled}, got "
            f"{wire['request_signing']['supported']!r}"
        )
        assert not wire["request_signing"].get("required_for"), (
            "the default posture must ship an EMPTY required_for — the pin says it is 'empty in 3.0 "
            "by default; sellers populate selectively during per-counterparty pilots', and a "
            f"non-empty default breaks every existing buyer at once. got "
            f"{wire['request_signing'].get('required_for')!r}"
        )
        assert wire["webhook_signing"]["supported"] is False, (
            f"tenant {seeded.tenant_id!r} holds no ACTIVE signing key, so it cannot sign outbound "
            f"webhooks; got {wire['webhook_signing']!r}"
        )
        assert wire.get("identity") is None, (
            "with every request_signing bucket empty and webhook_signing.supported false, no "
            "required_when trigger fires, so a keyless tenant must emit no identity block; got "
            f"{wire.get('identity')!r}"
        )
