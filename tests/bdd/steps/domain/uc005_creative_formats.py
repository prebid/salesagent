"""Domain step definitions for UC-005: Discover Creative Formats.

Format Givens construct real Format objects through FormatFactory
(tests/factories/format.py) in the generic modules. This module holds the UC-005-only
steps no other use case shares: the sandbox response flag boundary (BR-RULE-209
INV-4/INV-5), and the projection of the seller's product declarations
(``T-UC-005-local-product-projection``).
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import WIRE_MISSING, wire_absent, wire_field, wire_lookup
from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal
from tests.bdd.steps.generic.given_auth import _seed_account_for_principal
from tests.bdd.steps.generic.then_payload import register_boundary_handler
from tests.bdd.steps.generic.when_request import when_request_unfiltered

#: Boundary point -> (the account the request names, the sandbox flag the wire carries).
#:
#: ``sandbox`` on the request side: True seeds and names a sandbox account, False a
#: production one, None names none -- sandbox.mdx: "Omitting the selector from a natural
#: key defaults to production". On the response side the pinned
#: ``list-creative-formats-response.json`` says only "When true, this response contains
#: simulated data from sandbox mode", and BR-RULE-209 INV-5 has a production request
#: carry no flag; ``"absent"`` is that strict reading, ``"not true"`` admits the explicit
#: ``false`` the third row allows.
_SANDBOX_BOUNDARY: dict[str, tuple[bool | None, object]] = {
    "sandbox: true in response (sandbox account)": (True, True),
    "sandbox absent in response (production account)": (None, "absent"),
    "sandbox: false in response (explicit production)": (False, "not true"),
}


@given("a seller handling a list_creative_formats request for the given account context")
def given_seller_for_account_context(ctx: dict) -> None:
    """Seed the seller and the authenticated principal an account is granted to.

    A request that names an account needs a credential even on this public tool, so the
    principal must exist before the When names one.
    """
    ensure_tenant_principal(ctx, ctx["env"])


@when(parsers.parse('the Buyer Agent inspects the sandbox response flag at boundary "{boundary_point}"'))
def when_sandbox_boundary(ctx: dict, boundary_point: str) -> None:
    """Seed the row's account, name it on the request, and dispatch an unfiltered request."""
    sandbox, _expected = _SANDBOX_BOUNDARY[boundary_point]
    ctx["sandbox_boundary_point"] = boundary_point
    if sandbox is not None:
        _seed_account_for_principal(ctx, sandbox=sandbox)
    when_request_unfiltered(ctx)


@register_boundary_handler
def _sandbox_boundary_handler(ctx: dict, field: str, expected: str) -> bool:
    """Grade "the sandbox response handling should be valid" against the row's wire flag."""
    if field != "sandbox response":
        return False
    assert expected == "valid", f"sandbox response rows are all valid requests, got outcome {expected!r}"
    _sandbox, wire_flag = _SANDBOX_BOUNDARY[ctx["sandbox_boundary_point"]]
    if wire_flag is True:
        assert wire_field(ctx, "sandbox") is True, "a sandbox-account request must carry sandbox: true"
    elif wire_flag == "absent":
        wire_absent(ctx, "sandbox")
    else:
        flag = wire_lookup(ctx, "sandbox")
        assert flag is WIRE_MISSING or flag is False, f"a production request must not carry sandbox: true, got {flag!r}"
    return True


@given(parsers.parse('the seller\'s products declare only format "{name}" and one format no creative agent publishes'))
def given_products_declare_only(ctx: dict, name: str) -> None:
    """The tenant's product declares *name*, a registered format, and an id no agent publishes.

    Replaces the declarations the registry Given made, so the other registered formats are
    published by the seller's creative agent and declared by no product. The unpublished
    reference names the same agent, so only "no definition" separates it from *name*.
    """
    from src.core.schemas import FormatId

    (declared,) = [fmt for fmt in ctx["registry_formats"] if fmt.name == name]
    unpublished = FormatId(agent_url=declared.format_id.agent_url, id="unpublished_by_any_agent")
    ctx["env"].declare_formats([declared.format_id, unpublished])


@then(parsers.parse('the formats on the wire should be exactly "{name}"'))
def then_wire_formats_exactly(ctx: dict, name: str) -> None:
    """The listing the buyer received names exactly *name*, read off the wire."""
    formats = wire_field(ctx, "formats")
    assert isinstance(formats, list), f"formats is not a JSON array on the wire: {formats!r}"
    names = [entry.get("name") for entry in formats]
    assert names == [name], f"expected the listing to name exactly {name!r}, got {names}"
