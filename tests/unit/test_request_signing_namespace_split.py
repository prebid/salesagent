"""The two namespaces ``request_signing`` grades against, and the wall between them.

AdCP 3.1.1 ``security.mdx`` :1053, quoted whole because both halves of this file are in it:

    The matched value is the JSON-RPC envelope's ``method`` field (``tasks/cancel``,
    ``tasks/get``, …), **not** the MCP ``tools/call`` ``params.name``. AdCP tool names (no
    ``/``) MUST NOT appear in any ``protocol_methods_*`` array, and JSON-RPC method names
    (containing ``/``) MUST NOT appear in ``supported_for`` / ``warn_for`` / ``required_for``.
    Verifiers MUST reject capability blocks that violate the namespace split with a
    configuration-time error rather than silently coercing strings between the two.
    **Verifiers MUST NOT cross-namespace match: a ``protocol_methods_required_for``
    membership MUST NOT be satisfied by a body whose JSON-RPC ``method`` is ``tools/call``
    (even if ``params.name`` happens to equal a listed method string), and a ``required_for``
    membership MUST NOT be satisfied by a body whose JSON-RPC ``method`` is anything other
    than ``tools/call``.** The two buckets are matched against disjoint envelope fields.

Two obligations, in two places, so two test classes:

* CONFIGURATION time — a mixed-up declaration is refused when it is read, by name, with a
  message an operator can act on. :class:`TestTheNamespaceSplitIsRefusedAtConfigTime`.
* MATCHING time — a membership in one namespace is never satisfied by a name from the other.
  :class:`TestTheresNoCrossNamespaceMatch`.

The second is the one worth writing carefully, because the wrong implementation LOOKS right:
grading the resolved tool name against both bucket trios is simpler, reads as more thorough,
and is a conformance failure.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from src.core.exceptions import AdCPConfigurationError, AdCPRequestSignatureError
from src.core.schemas.capability_declarations import CapabilityDeclarations
from src.core.signing.capture import SignatureSubject
from src.core.signing.posture import RequestSigningPosture, posture_for_tenant
from src.core.signing.verifier import verify_inbound_signature
from src.core.tenant_context import TenantContext
from src.core.tools.registry import TOOLS, is_adcp_operation
from tests.helpers.signing import posture_declaration_document


@contextmanager
def _tenant_declaring(request_signing: dict[str, Any]) -> Iterator[TenantContext]:
    """A tenant whose stored declaration carries *request_signing*.

    Built as the row the resolver would have LOADED, so the verifier reads the posture the way
    production does — through ``posture_for_tenant`` parsing
    ``tenant.capability_declarations`` — rather than from a posture object handed to it
    directly. That parse is part of what is being graded: a declaration the store refused
    would resolve to ``supported: false`` and make every assertion below vacuous.

    Which is why ``identity.brand_json_url`` is declared alongside the posture and why the
    yield is gated on the parse. Naming any of the four trigger buckets fires the pinned
    ``identity.brand_json_url`` ``required_when`` rule
    (``_validate_identity_relations``), so a posture-only row is a CONFIGURATION_ERROR and
    ``posture_for_tenant`` downgrades it to :data:`UNSUPPORTED_POSTURE` with a warning —
    every operation lands in ``none`` and the namespace wall is never exercised. The
    assertion turns that silent downgrade into a failure.
    """
    # The document shape has ONE owner (tests/helpers/signing.posture_declaration_document),
    # and it is the owner because the identity pointer is DERIVED from the tenant rather than
    # authored: a hand-written brand_json_url is refused on the capabilities read path. The
    # virtual_host must be dotted for the same reason the sibling composition-rule fixture
    # says — src.core.agent_identity derives http:// for a single-label host, and the pin's
    # ^https:// (correctly) refuses that.
    base = TenantContext(
        tenant_id="t-namespace",
        name="Namespace Split",
        virtual_host="namespace-split.example.com",
    )
    tenant = base.model_copy(
        update={"capability_declarations": posture_declaration_document(base, {"supported": True, **request_signing})}
    )
    assert posture_for_tenant(tenant).supported is True, (
        "the declaration must be one the store ACCEPTS — an unreadable one resolves to "
        "supported: false, which puts every operation in the 'none' bucket and makes the "
        "cross-namespace assertions below pass without grading anything"
    )
    yield tenant


#: A name from each namespace, chosen so neither is a plausible member of the other: one is a
#: registry row, the other is a JSON-RPC lifecycle method the spec's own table names.
AN_ADCP_OPERATION = "create_media_buy"
A_PROTOCOL_METHOD = "tasks/cancel"


def test_the_fixtures_are_what_this_file_claims() -> None:
    """Meta-guard: without this, a renamed tool makes every assertion below vacuous."""
    assert is_adcp_operation(AN_ADCP_OPERATION), f"{AN_ADCP_OPERATION} must be a registry row"
    assert not is_adcp_operation(A_PROTOCOL_METHOD), f"{A_PROTOCOL_METHOD} must not be one"
    assert AN_ADCP_OPERATION in TOOLS


class TestTheNamespaceSplitIsRefusedAtConfigTime:
    """A capability block that mixes the namespaces is refused when it is READ.

    Config time, not verification time: the spec asks for "a configuration-time error rather
    than silently coercing strings between the two", and the difference is who finds out. A
    coerced declaration is discovered by a buyer, as a 401 on a request that should have been
    served (or a 200 on one that should not).
    """

    @pytest.mark.parametrize("bucket", ["required_for", "warn_for", "supported_for"])
    def test_a_jsonrpc_method_in_an_adcp_bucket_is_refused(self, bucket: str) -> None:
        with pytest.raises(AdCPConfigurationError) as refusal:
            CapabilityDeclarations.from_tenant({"request_signing": {"supported": True, bucket: [A_PROTOCOL_METHOD]}})
        details = refusal.value.details
        assert details is not None
        assert details.rejected_value == [A_PROTOCOL_METHOD], (
            "the refusal must NAME the offending string; an operator reading it has to know "
            "which entry to move and where"
        )
        assert "protocol_methods_" in (details.tracked_by or ""), "and where it belongs"

    @pytest.mark.parametrize(
        "bucket",
        ["protocol_methods_required_for", "protocol_methods_warn_for", "protocol_methods_supported_for"],
    )
    def test_an_adcp_tool_name_in_a_protocol_bucket_is_refused(self, bucket: str) -> None:
        """And refused by the NAMESPACE rule, not by the generated pattern.

        This is the direction that looks already-covered: the pinned schema's
        ``protocol_methods_*`` item model carries a pattern that a slash-free tool name cannot
        match, so pydantic would refuse it anyway — with "String should match pattern
        '^[a-z][a-z0-9_]*/...'", which names a regex rather than the rule the operator broke.
        The assertion on ``rejected_value`` is what distinguishes the two: only the namespace
        check reports the offending NAME.
        """
        with pytest.raises(AdCPConfigurationError) as refusal:
            CapabilityDeclarations.from_tenant({"request_signing": {"supported": True, bucket: [AN_ADCP_OPERATION]}})
        details = refusal.value.details
        assert details is not None
        assert details.rejected_value == [AN_ADCP_OPERATION], (
            "refused by the namespace rule, which names the string — not by pydantic's pattern, "
            "which names a regex and leaves the operator to work out why"
        )

    def test_a_correctly_split_declaration_is_accepted(self) -> None:
        """The guard must not be a blanket refusal — that would pass every case above vacuously."""
        declared = CapabilityDeclarations.from_tenant({"request_signing": {"supported": True}})
        assert declared.request_signing is not None
        assert declared.request_signing.supported is True


class TestTheresNoCrossNamespaceMatch:
    """A membership in one namespace is never satisfied by a name from the other.

    ``bucket_for`` takes the two namespaces as SEPARATE arguments and grades exactly one of
    them, which is how the rule is kept rather than checked: the caller says which field of
    the envelope named this request, and the other trio is not consulted at all.
    """

    def test_a_protocol_method_membership_is_not_satisfied_by_an_operation_of_that_name(self) -> None:
        """THE trap. A tool call whose NAME equals a listed protocol method is not that method.

        The spec spells this out — "even if ``params.name`` happens to equal a listed method
        string" — because the shortcut is so natural: the verifier has one resolved name in
        hand and two lists to check it against, and checking both looks strictly safer. It is
        the opposite. A seller that did it would enforce ``protocol_methods_required_for`` on
        the AdCP surface, refusing tool calls it advertised as unsigned-acceptable.
        """
        posture = RequestSigningPosture(supported=True, protocol_methods_required_for=[A_PROTOCOL_METHOD])

        assert posture.bucket_for(A_PROTOCOL_METHOD) != "required", (
            "an OPERATION named 'tasks/cancel' must not satisfy a protocol_methods_required_for "
            "membership — the two are matched against disjoint envelope fields"
        )
        assert posture.bucket_for("", protocol_method=A_PROTOCOL_METHOD) == "required", (
            "...while the same name arriving as the envelope's JSON-RPC method must"
        )

    def test_an_operation_membership_is_not_satisfied_by_a_protocol_method_of_that_name(self) -> None:
        """The mirror image, and the reason the wall has to be two-sided.

        A declaration cannot legally carry a slash-free name in ``protocol_methods_*`` (the
        config-time rule above), so this direction is reached by a name that IS legal in
        ``required_for`` arriving as a protocol method instead.
        """
        posture = RequestSigningPosture(supported=True, required_for=[AN_ADCP_OPERATION])

        assert posture.bucket_for(AN_ADCP_OPERATION) == "required"
        assert posture.bucket_for("", protocol_method=AN_ADCP_OPERATION) != "required", (
            "a required_for membership must not be satisfied off the envelope's method field"
        )

    def test_the_verifier_does_not_refuse_an_unsigned_request_on_a_protocol_membership(self) -> None:
        """END TO END: the trap above, driven through the verifier the resolver actually calls.

        A tenant declares ``protocol_methods_required_for: ["tasks/cancel"]`` and an anonymous,
        unsigned request arrives for an operation NAMED ``tasks/cancel``. A verifier that
        cross-matched would refuse it with ``request_signature_required``; a conforming one
        serves it, because that membership is matched against the envelope's ``method`` field
        and this request's name came from the other field entirely.

        The control below it is what makes this more than an assertion that nothing happens:
        the same verifier, the same unsigned anonymous request, against a membership declared
        in the namespace that DOES grade it, refuses. So the pass is "the wall held", not "the
        verifier is switched off".

        The control cannot simply move ``tasks/cancel`` into ``required_for`` — the
        config-time rule above refuses that declaration outright, which is the point of having
        both halves — so it uses the AdCP name in the AdCP bucket.
        """
        with _tenant_declaring({"protocol_methods_required_for": [A_PROTOCOL_METHOD]}) as tenant:
            across = SignatureSubject(operation=A_PROTOCOL_METHOD, registers_credentials=False, exchange=None)
            assert verify_inbound_signature(across, headers={}, tenant=tenant, principal=None) is None

        with (
            _tenant_declaring({"required_for": [AN_ADCP_OPERATION]}) as tenant,
            pytest.raises(AdCPRequestSignatureError) as refusal,
        ):
            within = SignatureSubject(operation=AN_ADCP_OPERATION, registers_credentials=False, exchange=None)
            verify_inbound_signature(within, headers={}, tenant=tenant, principal=None)
        assert str(refusal.value.error_code) == "request_signature_required", (
            "the control must refuse, or the case above proves nothing about the namespace wall"
        )
