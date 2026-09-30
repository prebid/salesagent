"""Who gets refused for not signing, and who does not. security.mdx @ v3.1.1 :1268-1271.

    - An **unauthenticated** request to a ``required_for`` operation MUST be rejected with
      ``request_signature_required``.
    - An **unsigned but otherwise authenticated** request (valid bearer, API key, or mTLS
      identity; no ``Signature-Input``) to a ``required_for`` operation MUST NOT be rejected
      for missing signature. …
    - A **malformed signature** blocks fallback regardless.

This is the rule the SDK CANNOT decide for us: its ``_precheck_presence`` raises
``request_signature_required`` on the absent branch unconditionally, which is the strict
reading :1289 names the failure of — "a seller enabling ``required_for`` for operational
monitoring would inadvertently 401 every bearer-authed buyer". This agent is
bearer-authenticated on essentially every AdCP request, so the strict reading would refuse
nearly all production traffic the moment a tenant populates ``required_for``. Hence a test
that asserts a NON-refusal, with the refusing case beside it as its control.

One rule sits deliberately outside it (:1462-1465, restated at :1375 as firing "regardless of
``required_for`` membership"): a request that hands the seller webhook credentials must be
signed even when its caller IS authenticated, because the rule exists precisely because that
caller normally is, and an on-path mutator can inject or strip the ``authentication`` block.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from src.core.exceptions import AdCPRequestSignatureError
from src.core.schemas import Principal
from src.core.signing.capture import SignatureSubject
from src.core.signing.verifier import verify_inbound_signature
from src.core.tenant_context import TenantContext
from tests.helpers.signing import posture_declaration_document

A_PROTECTED_OPERATION = "create_media_buy"

#: Dotted, so the derived trust-root pointer is ``https://`` — see the fixture below.
AN_AGENT_HOST = "composition.example.com"


@contextmanager
def _tenant_requiring_signatures(**declared: Any) -> Iterator[TenantContext]:
    """A tenant whose declaration really does bucket ``A_PROTECTED_OPERATION`` as ``required``.

    The stored document is built by :func:`posture_declaration_document` — the ONE shape
    every suite writes — rather than by literalling the ``request_signing`` block alone,
    because a posture is unreadable without its trust-root pointer: any non-empty bucket
    fires the pinned ``identity.brand_json_url`` ``required_when`` trigger, so
    ``CapabilityDeclarations.from_tenant`` refuses the whole document and
    ``posture_for_tenant`` downgrades that refusal to ``UNSUPPORTED_POSTURE``. A fixture
    that skips the pointer therefore yields a tenant enforcing NOTHING, and the refusal
    tests below would grade the unreadable-declaration path while reading as though they
    graded the declared one.

    ``virtual_host`` is dotted for the same reason the integration writer's is: the
    pointer is DERIVED from ``src.core.agent_identity``, and on ``localhost`` it derives
    ``http://``, which the pin's ``^https://`` (correctly) refuses.
    """
    tenant = TenantContext(tenant_id="t-composition", name="Composition Rule", virtual_host=AN_AGENT_HOST)
    yield tenant.model_copy(
        update={
            "capability_declarations": posture_declaration_document(
                tenant, {"supported": True, "required_for": [A_PROTECTED_OPERATION], **declared}
            )
        }
    )


def _a_caller() -> Principal:
    """A principal the bearer resolved — the "other credential the verifier accepts"."""
    return Principal(principal_id="p-1", name="Acme Buying", platform_mappings={})


def _unsigned(operation: str = A_PROTECTED_OPERATION, *, registers_credentials: bool = False) -> SignatureSubject:
    """A request carrying no signature at all: no capture, hence nothing presented."""
    return SignatureSubject(operation=operation, registers_credentials=registers_credentials, exchange=None)


def test_an_unauthenticated_unsigned_request_to_a_required_operation_is_refused() -> None:
    """The control for everything below, and the rule's first bullet verbatim."""
    with _tenant_requiring_signatures() as tenant, pytest.raises(AdCPRequestSignatureError) as refusal:
        verify_inbound_signature(_unsigned(), headers={}, tenant=tenant, principal=None)
    assert str(refusal.value.error_code) == "request_signature_required"


def test_an_authenticated_unsigned_request_to_a_required_operation_is_served() -> None:
    """The rule's second bullet, and the one a strict SDK reading gets wrong.

    ``required_for`` does not retroactively invalidate the verifier's own authenticator
    configuration: the bearer is what this seller advertised as sufficient for this caller.
    """
    with _tenant_requiring_signatures() as tenant:
        assert verify_inbound_signature(_unsigned(), headers={}, tenant=tenant, principal=_a_caller()) is None


def test_an_operation_outside_the_buckets_is_served_unsigned_and_unauthenticated() -> None:
    """The refusal is scoped to the declared membership, not to signing being enabled."""
    with _tenant_requiring_signatures() as tenant:
        assert verify_inbound_signature(_unsigned("get_products"), headers={}, tenant=tenant, principal=None) is None


def test_registering_webhook_credentials_is_refused_even_for_an_authenticated_caller() -> None:
    """:1462-1465, the one refusal the composition rule does NOT exempt.

    "Sellers that support request signing MUST require the inbound request to be 9421-signed
    … when ``authentication`` is present on ``push_notification_config.authentication`` or any
    ``accounts[].notification_configs[].authentication``, rejecting with
    ``request_signature_required``."

    Exempting the authenticated caller would defeat the rule entirely, because the registering
    caller is normally bearer-authed — that is the threat model, not an edge case.
    """
    with _tenant_requiring_signatures() as tenant, pytest.raises(AdCPRequestSignatureError) as refusal:
        verify_inbound_signature(
            _unsigned("get_products", registers_credentials=True),
            headers={},
            tenant=tenant,
            principal=_a_caller(),
        )
    assert str(refusal.value.error_code) == "request_signature_required", (
        "the escalation fires on an operation in NO bucket and for an authenticated caller — "
        "both of the exemptions the composition rule would otherwise grant"
    )


def test_a_seller_that_does_not_verify_refuses_nothing() -> None:
    """``supported: false`` is not a verifier, so :1465 routes it to log-and-alarm instead.

    The 3.0 migration note, and the line :1465 draws: a seller with no way to enforce the rule
    is not made to 401 traffic it cannot check.
    """
    tenant = TenantContext(
        tenant_id="t-unsupported",
        name="Not A Verifier",
        capability_declarations={"request_signing": {"supported": False}},
    )
    assert (
        verify_inbound_signature(
            _unsigned("get_products", registers_credentials=True), headers={}, tenant=tenant, principal=None
        )
        is None
    )


def test_the_kill_switch_reads_no_posture_at_all() -> None:
    """A rollback is a flag flip: every request resolves identity from its bearer alone."""
    from tests.helpers.signing import verifier_disabled

    with _tenant_requiring_signatures() as tenant, verifier_disabled():
        assert verify_inbound_signature(_unsigned(), headers={}, tenant=tenant, principal=None) is None
