"""BDD step definitions for TMP package sync (local feature).

The obligation these steps grade — *a buyer creates or updates a media buy, and
every registered active/draining provider holds current package data* — is
transport-blind, so the steps themselves are: setup goes through the env's
:class:`~tests.harness._mixins.TMPSyncMixin` seam, dispatch goes through
``dispatch_request`` → ``call_via``, and the assertion reads what the stub
provider actually received over a socket. Nothing here knows which transport it
is running on, which is why one scenario covers a2a/mcp/rest and e2e_rest
(#1197 review).
"""

from __future__ import annotations

from typing import Any

from adcp.types import AvailablePackage
from pytest_bdd import given, parsers, then, when

from src.services.tmp_provider_sync import AVAILABLE_PACKAGE_SCHEMA
from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
from tests.helpers.pinned_schema import validate_against_pinned_schema

#: The format the scenarios attach to the buy — the same reference catalog entry
#: the harness's default product uses, so it resolves on the live server too.
_EXPECTED_FORMAT_ID = {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}


def _create_media_buy(ctx: dict, *, expect_delivery: bool = True) -> str:
    """Dispatch a real create through ctx['transport'] and return its media_buy_id.

    The request comes from ``_ensure_request_defaults`` — the BDD layer's one
    definition of "a valid create_media_buy request" — with only the brand
    overridden. This file used to hand-build the payload and carry its own
    ``_future()``, giving the BDD layer a second definition inside the scenario
    that grades a cross-transport obligation (#1197 review).
    """
    env = ctx["env"]
    request_kwargs = dict(_ensure_request_defaults(ctx))
    request_kwargs["brand"] = {"domain": "tmp-package-sync.example.com"}
    # The buy carries eligible formats, because "the package's formats travel with
    # it" is one of the obligations the Then asserts. The spec syncs package
    # metadata at media-buy time rather than per request, so a router that never
    # received them cannot resolve creatives for the package (#1197 review).
    request_kwargs["packages"] = [
        {**package, "format_ids": [_EXPECTED_FORMAT_ID]} for package in request_kwargs["packages"]
    ]

    dispatch_request(ctx, **request_kwargs)
    result = ctx["result"]
    assert result.is_success, f"create_media_buy failed on {ctx['transport']}: {result.error}"

    # Read through the guarded wire helper, not off the typed payload: the id is
    # the reference the Then step compares the DELIVERED package against, so
    # taking it from a model round trip would let a transport that dropped
    # media_buy_id from its serialized body still hand the step a value and pass.
    media_buy_id = wire_field(ctx, "media_buy_id")
    assert media_buy_id, "create_media_buy returned no media_buy_id on the wire"
    ctx["tmp_media_buy_id"] = media_buy_id

    if expect_delivery:
        # The seam owns the wait; without it the assertion races the
        # fire-and-forget thread (in-process) or the server's own thread (e2e_rest).
        env.await_tmp_sync(count=1)
    else:
        # Nothing is expected, so there is no arrival to wait for — drain the
        # in-flight sync instead, then let the collector settle. Without both, "no
        # delivery" would pass simply because nothing had happened yet.
        env.join_tmp_syncs()
        env.settle_tmp_sync()
    return str(media_buy_id)


@given("a TMP provider is registered for the tenant")
def given_tmp_provider_registered(ctx: dict) -> None:
    """Register one active provider pointed at the env's collector."""
    ctx["env"].register_tmp_provider()


@given(parsers.parse('a TMP provider is registered for the tenant with status "{status}"'))
def given_tmp_provider_with_status(ctx: dict, status: str) -> None:
    """Register one provider in *status*, to grade the syncable-status axis.

    The sync fans out to ``list_syncable()`` — active + draining — so an operator
    setting a provider inactive must stop its package data (and its Bearer
    credential) reaching that provider. The sibling capability feature graded this
    axis on the wire; the sync did not, because every scenario here registered an
    active provider (#1197 review).
    """
    ctx["env"].register_tmp_provider(status=status)
    # `inactive` is excluded from list_syncable(), so no delivery is expected and
    # the When step must drain-and-settle rather than wait for an arrival.
    ctx["tmp_expects_delivery"] = status != "inactive"


@given("a TMP provider with a credential is registered for the tenant")
def given_credentialed_tmp_provider_registered(ctx: dict) -> None:
    """Register a provider that carries a Bearer credential."""
    ctx["tmp_credential"] = "provider-secret-token"
    ctx["env"].register_tmp_provider(auth_credentials=ctx["tmp_credential"])


@given("the Buyer Agent created a media buy whose packages were delivered")
def given_created_media_buy_already_synced(ctx: dict) -> None:
    """Seed the update scenario with a create whose own sync already landed.

    Awaiting the create's delivery here is what makes the update assertion
    falsifiable: without it, a wrapper that dropped ``fire_tmp_sync`` from the
    update path would still find one delivery waiting and pass.
    """
    _create_media_buy(ctx)


@when("the Buyer Agent creates a media buy")
def when_buyer_creates_media_buy(ctx: dict) -> None:
    _create_media_buy(ctx, expect_delivery=ctx.get("tmp_expects_delivery", True))


@when("the Buyer Agent updates that media buy")
def when_buyer_updates_media_buy(ctx: dict) -> None:
    from src.core.schemas import UpdateMediaBuyRequest

    dispatch_request(ctx, req=UpdateMediaBuyRequest(media_buy_id=ctx["tmp_media_buy_id"], budget=7500.0))
    result = ctx["result"]
    assert result.is_success, f"update_media_buy failed on {ctx['transport']}: {result.error}"
    ctx["env"].await_tmp_sync(count=2)


def _assert_delivery(ctx: dict, count: int) -> None:
    """Assert EXACTLY *count* deliveries, and that the last one is well-formed.

    Exact, not ``>=``: the seam's ``await_tmp_sync`` is the liveness wait (and it
    settles briefly after the expected delivery lands, so a duplicate in flight has
    time to arrive), while this is the correctness signal. A ``>=`` assertion let a
    double-fire — including the REST double-fire that the sync's single trigger
    exists to prevent — pass green on every transport (#1197 review).

    The body is validated against the pinned ``available-package.json`` rather than
    a restated key set, so a spec bump that adds an optional member production
    correctly emits does not fail here, while a lost ``seller_agent.agent_url``
    does.
    """
    env = ctx["env"]
    deliveries = env.tmp_sync_deliveries()
    assert len(deliveries) == count, (
        f"expected exactly {count} POST /packages/sync delivery(ies), got {len(deliveries)}"
    )
    entry = deliveries[count - 1]

    assert entry["method"] == "POST", f"packages sync must be a POST, got {entry['method']}"
    assert entry["path"] == "/tmp/packages/sync", f"provider_url() built the wrong path: {entry['path']!r}"

    body: Any = entry["body"]
    assert isinstance(body, list), f"packages sync body must be a JSON array, got {type(body).__name__}"
    assert body, "packages sync body was an empty array"

    for package in body:
        validate_against_pinned_schema(AVAILABLE_PACKAGE_SCHEMA, package)
        AvailablePackage.model_validate(package)
        assert package["media_buy_id"] == ctx["tmp_media_buy_id"]
        assert package["seller_agent"] == {"agent_url": env.tmp_seller_agent_url}
        # The media buy's eligible formats travel with the package. The spec is
        # explicit that package metadata is synced at media-buy time and NOT sent
        # per request, so a router that never received the formats cannot resolve
        # creatives for this package (#1197 review).
        assert package.get("format_ids"), f"the delivered package carries no format_ids: {package}"
        delivered_ids = {format_id["id"] for format_id in package["format_ids"]}
        assert delivered_ids == {_EXPECTED_FORMAT_ID["id"]}, (
            f"delivered formats {sorted(delivered_ids)} are not the buy's formats"
        )
        for format_id in package["format_ids"]:
            # The AdCP format-id object, not a bare string: agent_url is what makes
            # it resolvable across namespaces.
            assert format_id.get("agent_url"), f"format_id missing its agent_url: {format_id}"


@then("the provider receives nothing")
def then_provider_receives_nothing(ctx: dict) -> None:
    """An inactive provider is excluded from the fan-out entirely.

    Not merely "no packages": no request at all, so the provider never sees the
    tenant's package set OR the Bearer credential that would have accompanied it.
    """
    deliveries = ctx["env"].tmp_sync_deliveries()
    assert deliveries == [], f"an inactive provider must receive nothing, got {deliveries}"


@then("the provider receives the packages for that media buy")
def then_provider_received_packages(ctx: dict) -> None:
    _assert_delivery(ctx, 1)


@then("the delivery carries the provider's credential")
def then_delivery_carries_the_credential(ctx: dict) -> None:
    """The credential reaches the provider as a Bearer header, on the real wire.

    This is the third of the three things invariant 4 says must be identical
    across transports (URL, auth header, body). It was previously graded only by
    ``mock_client.post.assert_called_once_with(headers=...)`` under a patched
    ``httpx.Client`` — so sending the credential as a query parameter instead
    would have passed (#1197 review).
    """
    delivery = ctx["env"].tmp_sync_deliveries()[-1]
    assert delivery["headers"].get("authorization") == f"Bearer {ctx['tmp_credential']}"


@then("the delivery carries no credential")
def then_delivery_carries_no_credential(ctx: dict) -> None:
    """An uncredentialed provider must not receive an Authorization header."""
    delivery = ctx["env"].tmp_sync_deliveries()[-1]
    assert "authorization" not in delivery["headers"]


@then("the provider receives the packages for that media buy a second time")
def then_provider_received_packages_again(ctx: dict) -> None:
    _assert_delivery(ctx, 2)
