"""BDD step definitions for the TMP capability declaration (local feature).

The obligation — *a seller declares ``trusted_match.core`` exactly when it has a
provider the surface actually serves* — is transport-blind, so these steps are:
setup registers the state that makes the surface real (a row, via the factory),
dispatch goes through ``dispatch_request`` → ``call_via``, and the assertion
reads the field off the real serialized body via ``wire_field``.

This replaced a hand-written ``@parametrize("transport", [MCP, A2A, REST])`` in
``tests/integration/test_tmp_provider_integration.py`` that rolled its own
envelope extraction and structurally could not include ``e2e_rest`` (#1197
review).
"""

from __future__ import annotations

import re

from pytest_bdd import given, parsers, then, when

from src.core.tools.capabilities import CAPABILITIES_RESPONSE_SCHEMA
from tests.bdd.steps._outcome_helpers import wire_dict
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.helpers.pinned_schema import load as load_pinned_schema


@given(parsers.parse('the tenant\'s only TMP provider has status "{status}"'))
def given_provider_with_status(ctx: dict, status: str) -> None:
    """Make the tenant have EXACTLY one provider, in *status*.

    Through ``replace_tmp_providers`` rather than ``TMPProviderFactory`` directly:
    the scenarios assert on "a tenant whose only provider is …", and calling the
    factory made that precondition true only because the harness happens to hand
    each scenario a clean database — which is not something this step states or
    controls (#1197 review). "Replace" states it.
    """
    from tests.factories import replace_tmp_providers

    replace_tmp_providers(
        ctx["env"],
        ctx["env"]._tenant_id,
        name=f"Capability Declaration Provider ({status})",
        status=status,
    )
    ctx["env"]._commit_factory_data()


@given("the tenant has no TMP provider registered")
def given_no_provider(ctx: dict) -> None:
    """The falsifiable half: a hardcoded constant would fail the Then below."""
    ctx["env"]._commit_factory_data()


@when("the Buyer Agent asks for the seller's capabilities")
def when_buyer_asks_for_capabilities(ctx: dict) -> None:
    dispatch_request(ctx)
    # `dispatch_request` sets ctx["result"] only when the dispatch RETURNED; a
    # dispatch that raised leaves only ctx["error"]. Reading ctx["result"] blindly
    # turned that into a bare KeyError that hid the actual failure.
    if "result" not in ctx:
        raise AssertionError(f"get_adcp_capabilities did not dispatch on {ctx['transport']}: {ctx.get('error')!r}")
    result = ctx["result"]
    assert result.is_success, f"get_adcp_capabilities failed on {ctx['transport']}: {result.error}"


def _declared_features(ctx: dict) -> list[str]:
    """``experimental_features`` as the buyer sees it, absent field included.

    Read through ``wire_dict`` rather than ``wire_field`` because absence is the
    assertion in half these scenarios and the field is omitted, not emitted as
    ``[]`` — indexing it would raise where the obligation says "not declared".
    """
    return list(wire_dict(ctx).get("experimental_features") or [])


@then(parsers.parse('experimental_features includes "{feature_id}"'))
def then_features_include(ctx: dict, feature_id: str) -> None:
    declared = _declared_features(ctx)
    assert feature_id in declared, f"{ctx['transport']}: expected {feature_id} in {declared}"

    # Every declared id must satisfy the pinned schema's item pattern — the same
    # "grade against the authority" move the sibling sync step makes with
    # AVAILABLE_PACKAGE_SCHEMA. The previous line here asserted
    # `feature_id == TRUSTED_MATCH_FEATURE_ID`, which no edit could redden while
    # the line above stayed green (#1197 review).
    item_pattern = load_pinned_schema(CAPABILITIES_RESPONSE_SCHEMA)["properties"]["experimental_features"]["items"][
        "pattern"
    ]
    for declared_id in declared:
        assert re.fullmatch(item_pattern, declared_id), (
            f"{declared_id!r} does not match the schema's experimental-feature id pattern {item_pattern}"
        )


@then(parsers.parse('experimental_features does not include "{feature_id}"'))
def then_features_exclude(ctx: dict, feature_id: str) -> None:
    declared = _declared_features(ctx)
    assert feature_id not in declared, f"{ctx['transport']}: unexpected {feature_id} in {declared}"
