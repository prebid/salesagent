"""Transport-neutral bindings; all setup belongs to SandboxLifecycleEnv."""

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_dict, wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories.request import UpdateMediaBuyRequestFactory


@given(parsers.parse('a "{account}" buy ready for the "{action}" update'))
def updateable_buy(ctx: dict, account: str, action: str) -> None:
    ctx["sandbox_account"] = account
    ctx["update_action"] = action
    ctx["sandbox_buy_id"] = ctx["env"].seed_owned_buy(
        account,
        status="paused" if action == "campaign_resume" else "active",
        is_paused=action == "campaign_resume",
        package_paused=action in {"resume", "resume_targeting", "budget_targeting"},
        package_budget=5000 if action.startswith("budget") else 6000,
    )
    ctx["sandbox_before"] = ctx["env"].buy_state(ctx["sandbox_buy_id"])
    ctx["package_before"] = ctx["env"].package_state(ctx["sandbox_buy_id"])


@when(parsers.parse('that account applies the "{action}" update'))
def apply_update(ctx: dict, action: str) -> None:
    dispatch_request(ctx, req=ctx["env"].update_request(ctx["sandbox_account"], ctx["sandbox_buy_id"], action))


@then(
    parsers.parse(
        'the persisted update has budget {budget:d} and package paused "{package_paused}" and campaign paused "{campaign_paused}"'
    )
)
def persisted_update(ctx: dict, budget: int, package_paused: str, campaign_paused: str) -> None:
    state = ctx["env"].buy_state(ctx["sandbox_buy_id"])
    package_budget, config = ctx["env"].package_state(ctx["sandbox_buy_id"])
    assert package_budget == config["budget"] == budget
    assert config["paused"] is (package_paused == "true")
    if ctx["update_action"].endswith("_targeting"):
        assert config["targeting_overlay"]["geo_countries"] == ["US"]
    assert state[1] is (campaign_paused == "true")
    assert state[2] > ctx["sandbox_before"][2]
    assert wire_field(ctx, "revision") == state[2]
    if not ctx["update_action"].startswith("campaign_"):
        assert [(package["package_id"], package["paused"]) for package in wire_field(ctx, "affected_packages")] == [
            ("owned-package", package_paused == "true")
        ]


@when("that account lists the updated buy")
def list_updated_buy(ctx: dict) -> None:
    from tests.factories.request import GetMediaBuysRequestFactory

    dispatch_request(
        ctx,
        req=GetMediaBuysRequestFactory(
            account={"account_id": ctx["sandbox_account"]},
            media_buy_ids=[ctx["sandbox_buy_id"]],
            include_snapshot=False,
        ),
    )


@then(parsers.parse('the listed package has budget {budget:d} and paused "{paused}"'))
def listed_update(ctx: dict, budget: int, paused: str) -> None:
    package = wire_field(ctx, "media_buys")[0]["packages"][0]
    assert package["budget"] == budget
    assert package["paused"] is (paused == "true")


@then("the stored buy and package are unchanged")
def unapplied_update(ctx: dict) -> None:
    assert ctx["env"].buy_state(ctx["sandbox_buy_id"]) == ctx["sandbox_before"]
    assert ctx["env"].package_state(ctx["sandbox_buy_id"]) == ctx["package_before"]


@given(parsers.parse('a seller configured for "{adapter}" with automatic approval'))
def configured_seller(ctx: dict, adapter: str) -> None:
    ctx["env"].seed_lifecycle(adapter_type=adapter)


@when(parsers.parse('that account requests targeting "{field}" with value "{value}"'))
def targeted_buy(ctx: dict, field: str, value: str) -> None:
    dispatch_request(ctx, req=ctx["env"].request_with_targeting(ctx["sandbox_account"], field, value))


@then(parsers.parse('the targeting refusal names capability "{capability}"'))
def refused_targeting(ctx: dict, capability: str) -> None:
    assert ctx["result"].wire_error_details("UNSUPPORTED_FEATURE", recovery="correctable")["capability"] == capability


@given("a configured seller requiring human approval")
def seller_requiring_approval(ctx: dict) -> None:
    ctx["env"].seed_lifecycle(approval=True)


@given("an accessible sandbox account")
def accessible_sandbox(ctx: dict) -> None:
    ctx["sandbox_account"] = "sandbox"


@given("a configured seller with automatic approval")
def seller_automatic_approval(ctx: dict) -> None:
    ctx["env"].seed_lifecycle()


@given(parsers.parse('a buy owned by another accessible "{account}" account of the same principal'))
def other_account_buy(ctx: dict, account: str) -> None:
    ctx["sandbox_buy_id"] = ctx["env"].seed_owned_buy(account)
    ctx["sandbox_before"] = ctx["env"].buy_state(ctx["sandbox_buy_id"])


@when("that account requests a valid media buy")
def request_sandbox_buy(ctx: dict) -> None:
    dispatch_request(ctx, req=ctx["env"].create_request(ctx["sandbox_account"]))


@when("the sandbox account requests an update to that buy")
def update_other_account(ctx: dict) -> None:
    dispatch_request(
        ctx,
        req=UpdateMediaBuyRequestFactory(
            account={"account_id": "sandbox"},
            media_buy_id=ctx["sandbox_buy_id"],
            paused=True,
        ),
    )


@then(parsers.parse('the wire response has status "{status}" and a task identifier'))
def submitted_with_task(ctx: dict, status: str) -> None:
    assert wire_field(ctx, "status") == status
    assert isinstance(wire_field(ctx, "task_id"), str)
    assert ctx["env"].approval_task_state(wire_field(ctx, "task_id")) == ("requires_approval", ctx["sandbox_account"])


@then("it has no confirmed media buy identifier or packages")
def no_confirmed_buy(ctx: dict) -> None:
    body = wire_dict(ctx)
    assert "media_buy_id" not in body
    assert "packages" not in body


@then(parsers.parse('the wire error is "{code}" with recovery "{recovery}"'))
def scoped_error(ctx: dict, code: str, recovery: str) -> None:
    ctx["result"].assert_wire_error(code, recovery=recovery)


@then("the stored buy is unchanged")
def unchanged_buy(ctx: dict) -> None:
    assert ctx["env"].buy_state(ctx["sandbox_buy_id"]) == ctx["sandbox_before"]


@given("a sandbox seller configured for GAM")
def gam_seller(ctx: dict) -> None:
    ctx["env"].seed_lifecycle(adapter_type="google_ad_manager")
    ctx["sandbox_account"] = "sandbox"


@given("a product with invalid GAM configuration")
def invalid_gam_product(ctx: dict) -> None:
    ctx["env"].set_product_policy({"line_item_type": "INVALID"})


@given("a product without GAM configuration")
def no_gam_product(ctx: dict) -> None:
    ctx["env"].set_product_policy(None)


@then(parsers.parse('the wire response has status "{status}"'))
def wire_status(ctx: dict, status: str) -> None:
    assert wire_field(ctx, "status") == status


@then("the shared product still has no GAM configuration")
def unchanged_product(ctx: dict) -> None:
    assert ctx["env"].product_policy() is None


@given("stored buys belonging to sandbox and live accounts")
def reporting_buys(ctx: dict) -> None:
    ctx["env"].seed_owned_buy("sandbox", media_buy_id="sandbox-report", status="active")
    ctx["env"].seed_owned_buy("live", media_buy_id="live-report", status="active")


@when(parsers.parse('the buyer requests "{tool}" for the sandbox account'))
def scoped_report(ctx: dict, tool: str) -> None:
    from tests.bdd.steps.generic._dispatch import dispatch_via_client

    dispatch_via_client(ctx, tool, {"account": {"account_id": "sandbox"}})


@then(parsers.parse('only the sandbox buy appears in "{field}"'))
def scoped_rows(ctx: dict, field: str) -> None:
    from tests.bdd.steps._outcome_helpers import wire_advisory_errors

    assert [row["media_buy_id"] for row in wire_field(ctx, field)] == ["sandbox-report"]
    assert wire_advisory_errors(ctx) == []


@given("a stored sandbox buy and a live buy created through the seller")
def executing_reporting_buys(ctx: dict) -> None:
    ctx["env"].seed_owned_buy("sandbox", media_buy_id="sandbox-report", status="active")
    ctx["live_report_id"] = ctx["env"].seed_live_reporting_buy(ctx["transport"])


@when("the buyer requests snapshots across accounts")
def all_snapshots(ctx: dict) -> None:
    from tests.factories.request import GetMediaBuysRequestFactory

    dispatch_request(
        ctx,
        req=GetMediaBuysRequestFactory(
            account=None, media_buy_ids=["sandbox-report", ctx["live_report_id"]], include_snapshot=True
        ),
    )


@then("the sandbox buy has a zero delivery snapshot")
def sandbox_snapshot(ctx: dict) -> None:
    buy = next(row for row in wire_field(ctx, "media_buys") if row["media_buy_id"] == "sandbox-report")
    assert buy["packages"][0]["snapshot"]["impressions"] == 0
    assert buy["packages"][0]["snapshot"]["spend"] == 0
    from tests.bdd.steps._outcome_helpers import wire_advisory_errors

    live = next(row for row in wire_field(ctx, "media_buys") if row["media_buy_id"] == ctx["live_report_id"])
    snapshot = live["packages"][0]["snapshot"]
    assert snapshot["impressions"] > 0
    assert snapshot["spend"] > 0
    assert snapshot["impressions"] == snapshot["spend"] / 0.01
    assert "snapshot_unavailable_reason" not in live["packages"][0]
    assert wire_advisory_errors(ctx) == []


@when("the buyer requests delivery across accounts")
def all_delivery(ctx: dict) -> None:
    from tests.bdd.steps.generic._dispatch import dispatch_via_client

    dispatch_via_client(ctx, "get_media_buy_delivery", {"media_buy_ids": ["sandbox-report", "live-report"]})


@then("the sandbox buy has zero delivered impressions and spend")
def sandbox_delivery(ctx: dict) -> None:
    buy = next(row for row in wire_field(ctx, "media_buy_deliveries") if row["media_buy_id"] == "sandbox-report")
    assert buy["totals"]["impressions"] == 0
    assert buy["totals"]["spend"] == 0
    live = next(row for row in wire_field(ctx, "media_buy_deliveries") if row["media_buy_id"] == "live-report")
    assert live["totals"]["impressions"] > 0


@given(parsers.parse('a seller requiring approval through "{gate}"'))
def approval_gate(ctx: dict, gate: str) -> None:
    ctx["env"].seed_lifecycle(tenant_approval=gate == "tenant", adapter_approval=gate == "adapter")
    if gate == "product":
        ctx["env"].set_product_policy({"auto_create_enabled": False})


@given(parsers.parse('the requesting account is "{account}"'))
def requesting_account(ctx: dict, account: str) -> None:
    ctx["sandbox_account"] = account


@when("sandbox accounts create buys with the same purchase order text")
def same_purchase_order(ctx: dict) -> None:
    ctx["created_sandbox_buys"] = []
    for account in ("sandbox", "sibling"):
        dispatch_request(ctx, req=ctx["env"].create_request(account, po_number="shared-po-text"))
        ctx["created_sandbox_buys"].append(wire_field(ctx, "media_buy_id"))


@then("the two simulated buys have distinct identifiers and stored accounts")
def distinct_sandbox_buys(ctx: dict) -> None:
    first, second = ctx["created_sandbox_buys"]
    assert first != second
    assert first.startswith("sandbox_") and second.startswith("sandbox_")
    assert [ctx["env"].created_buy_ownership(buy_id) for buy_id in (first, second)] == ["sandbox", "sibling"]


@given("an approved creative already assigned to a live buy")
def shared_live_creative(ctx: dict) -> None:
    ctx["shared_creative_id"], _ = ctx["env"].seed_shared_creative()


@when("the sandbox account creates a buy with that creative")
def create_with_shared_creative(ctx: dict) -> None:
    from tests.factories.request import PackageRequestFactory

    request = ctx["env"].create_request(
        packages=[PackageRequestFactory(product_id="prod_1", creative_ids=[ctx["shared_creative_id"]])]
    )
    dispatch_request(ctx, req=request)


@then("that buy has its own simulated creative reference")
def own_creative_reference(ctx: dict) -> None:
    platform_id = ctx["env"].sandbox_reference_for(wire_field(ctx, "media_buy_id"), ctx["shared_creative_id"])
    assert platform_id.startswith("sandbox_creative_")
    assert platform_id != "live-creative-123"


@then("the shared live creative reference is unchanged")
def live_reference_unchanged(ctx: dict) -> None:
    creative, _, live_package = ctx["env"].creative_persistence(ctx["shared_creative_id"])
    assert creative["platform_creative_id"] == "live-creative-123"
    assert "sandbox_creative_ids" not in live_package


@given(parsers.parse('a stored buy whose account is "{condition}"'))
def broken_account(ctx: dict, condition: str) -> None:
    ctx["env"].seed_owned_buy(None if condition == "absent" else "sandbox", media_buy_id="broken-buy")
    if condition != "absent":
        ctx["env"].set_sandbox_account_condition(condition)


@when(parsers.parse('the buyer requests "{tool}" for the broken stored buy'))
def broken_account_report(ctx: dict, tool: str) -> None:
    from tests.bdd.steps.generic._dispatch import dispatch_via_client

    params = {"media_buy_ids": ["broken-buy"]}
    if tool == "get_media_buys":
        params["include_snapshot"] = True
    dispatch_via_client(ctx, tool, params)


@given("a live stored buy whose GAM credentials are absent")
def missing_gam_credentials(ctx: dict) -> None:
    ctx["env"].seed_owned_buy("live", media_buy_id="broken-buy")
    ctx["env"].remove_gam_credentials()


@when("the buyer lists the broken stored buy without snapshots")
def broken_no_snapshot(ctx: dict) -> None:
    from tests.factories.request import GetMediaBuysRequestFactory

    dispatch_request(
        ctx, req=GetMediaBuysRequestFactory(account=None, media_buy_ids=["broken-buy"], include_snapshot=False)
    )


@then("the listing includes the buy without a configuration advisory")
def no_snapshot_no_execution(ctx: dict) -> None:
    from tests.bdd.steps._outcome_helpers import wire_advisory_errors

    assert [buy["media_buy_id"] for buy in wire_field(ctx, "media_buys")] == ["broken-buy"]
    assert wire_advisory_errors(ctx) == []


@then(parsers.parse('the reporting advisory is "{code}" with recovery "{recovery}"'))
def account_refusal_advisory(ctx: dict, code: str, recovery: str) -> None:
    from tests.bdd.steps._outcome_helpers import wire_advisory_errors

    assert wire_field(ctx, "status") == "completed"
    errors = wire_advisory_errors(ctx)
    assert [(error["code"], error["recovery"]) for error in errors] == [(code, recovery)]
