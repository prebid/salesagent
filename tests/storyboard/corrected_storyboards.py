"""Corrections to setup fixtures in the pinned compliance storyboards.

Filed upstream as adcontextprotocol/adcp#7609.

``compliance/universal/webhook-emission.yaml`` drives four ``create_media_buy`` steps
and never discovers a product first. Its only preceding step is
``get_adcp_capabilities``, so when ``@adcp/sdk``'s request builder reaches

    product_id: fixtureProductId ?? product?.product_id ?? context.product_id ?? "test-product"

(``lib/testing/storyboard/request-builder.js``) every candidate is empty and the body
ships the literal ``"test-product"`` — a string the SDK's own ``PRODUCT_ID_SENTINELS``
marks as "no real id was available". A conformant seller answers ``PRODUCT_NOT_FOUND``,
and the four webhook triggers plus everything gated behind them grade nothing about
webhooks. Measured on this agent: 7 ``webhook_emission`` checks failed per protocol,
four of them on that error alone (run innet_200926_0635).

The storyboard's own subject is webhook emission; the product is scaffolding it forgot
to set up. The correction is the one every media-buy storyboard already performs — a
``get_products`` step with ``context_outputs`` binding ``product_id`` and
``pricing_option_id`` — inserted into the discovery phase that already exists for
exactly this purpose ("Discover which async operations the agent supports so the runner
knows which to drive").

THE PINNED TREE IS NOT EDITED. This runs over the sibling corrected tree that
:func:`tests.storyboard.corrected_vectors.corrected_compliance_tree` materializes.

For ``webhook_emission``, one step is ADDED. No existing step, validation or
expected outcome is touched, so every check the storyboard graded before it still grades
the same thing — the four triggers simply reach the agent's create path instead of
dying on a placeholder.

``invalid_transitions`` also needs one sandbox account across its lifecycle. The SDK
adds ``sandbox: true`` to ordinary requests but sends expected-error samples directly,
so an omitted selector switches those probes to the live account. AdCP v3.1.1's
``docs/media-buy/advanced-topics/sandbox.mdx`` requires the selector on every natural-key
request. Upstream adcontextprotocol/adcp#8216 (closing #8179) adds it to all six samples;
we backport exactly that change to the copied protocol/domain fixture. Its error oracles
and invalid entity IDs are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests.storyboard import corrected_vectors

#: Relative to the compliance root the runner is pointed at.
_WEBHOOK_EMISSION = Path("universal") / "webhook-emission.yaml"

#: The phase whose stated job is discovery. The inserted step joins it rather than
#: forming a phase of its own: a new phase id would appear in the runner's report as a
#: storyboard section that upstream's copy does not have, and the correction should be
#: invisible to everything except the product the trigger steps name.
_DISCOVERY_PHASE = "capability_discovery"

#: Modelled on ``compliance/protocols/media-buy/scenarios/*``'s ``get_products_brief``,
#: minus its ``account:`` block. The trigger steps carry no account either and resolve
#: one through the runner's own defaults — naming a brand/operator pair here would grade
#: account resolution, which is a different storyboard's job.
_DISCOVERY_STEP: dict[str, Any] = {
    "id": "get_products_for_webhook_trigger",
    "title": "Discover a product for the webhook-emitting operation",
    "narrative": (
        "The webhook triggers below call create_media_buy. Discover a real product first "
        "so the request names one, rather than the builder's last-resort placeholder.\n"
    ),
    "task": "get_products",
    "requires_tool": "get_products",
    "schema_ref": "media-buy/get-products-request.json",
    "response_schema_ref": "media-buy/get-products-response.json",
    "doc_ref": "/media-buy/task-reference/get_products",
    "stateful": False,
    "expected": "Return at least one product with pricing options.\n",
    "sample_request": {
        "buying_mode": "brief",
        "brief": "Any available inventory; this step exists to name a product.",
        "context": {"correlation_id": "webhook_emission--get_products_for_webhook_trigger"},
    },
    "context_outputs": [
        {"path": "products[0].product_id", "key": "product_id"},
        {"path": "products[0].pricing_options[0].pricing_option_id", "key": "pricing_option_id"},
    ],
    "validations": [
        {
            "check": "field_present",
            "path": "products",
            "description": "Agent returns a products collection the trigger steps can buy from",
        }
    ],
}


def correct_webhook_emission(tree: Path) -> Path:
    """Insert the product-discovery step into *tree*'s ``webhook-emission.yaml``.

    Idempotent: a tree already carrying the step is returned untouched, so the
    correction survives a caller that applies it twice.

    Raises:
        FileNotFoundError: the storyboard is not where the runner expects it.
        ValueError: the discovery phase is gone, i.e. the upstream storyboard was
            restructured and this correction no longer describes it. Silently skipping
            would leave the triggers on ``"test-product"`` while the suite reported a
            correction had been applied.
    """
    path = tree / _WEBHOOK_EMISSION
    if not path.is_file():
        raise FileNotFoundError(f"no webhook-emission storyboard at {path}")

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    phases = document.get("phases") or []
    phase = next((p for p in phases if p.get("id") == _DISCOVERY_PHASE), None)
    if phase is None:
        raise ValueError(
            f"{path}: no phase {_DISCOVERY_PHASE!r} to hold the product-discovery step "
            f"(found {[p.get('id') for p in phases]}) — the upstream storyboard changed shape"
        )

    steps = phase.setdefault("steps", [])
    if any(step.get("id") == _DISCOVERY_STEP["id"] for step in steps):
        return path
    steps.append(_DISCOVERY_STEP)

    path.write_text(yaml.safe_dump(document, sort_keys=False, width=100), encoding="utf-8")
    return path


_INVALID_TRANSITIONS = Path("media-buy/scenarios/invalid_transitions.yaml")
_INVALID_TRANSITION_STEPS = (
    "update_unknown_media_buy",
    "get_products_brief",
    "create_buy",
    "update_unknown_package",
    "first_cancel",
    "second_cancel",
)
_INVALID_TRANSITIONS_ACCOUNT = {"brand": {"domain": "acmeoutdoor.example"}, "operator": "pinnacle-agency.example"}


def correct_invalid_transitions_accounts(tree: Path) -> Path:
    """Backport adcp#8216's six sandbox selectors without changing any probe oracle.

    Validate both copies before writing either. A changed step sequence or account
    needs a fresh upstream comparison, rather than silently applying this correction
    to different fixtures. Already-corrected files remain byte-for-byte unchanged.
    """
    protocol = tree / "protocols" / _INVALID_TRANSITIONS
    if not protocol.is_file():
        raise FileNotFoundError(f"no invalid_transitions storyboard at {protocol}")
    paths = [protocol]
    domain = tree / "domains" / _INVALID_TRANSITIONS
    if domain.is_file():
        paths.append(domain)

    corrections = []
    for path in paths:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if document.get("id") != "media_buy_seller/invalid_transitions":
            raise ValueError(f"{path}: invalid_transitions storyboard identity changed")
        steps = [step for phase in document.get("phases", []) for step in phase.get("steps", [])]
        if tuple(step.get("id") for step in steps) != _INVALID_TRANSITION_STEPS:
            raise ValueError(f"{path}: invalid_transitions step sequence changed")
        accounts = []
        for step in steps:
            account = step.get("sample_request", {}).get("account")
            if (
                not isinstance(account, dict)
                or {key: value for key, value in account.items() if key != "sandbox"} != _INVALID_TRANSITIONS_ACCOUNT
                or ("sandbox" in account and account["sandbox"] is not True)
            ):
                raise ValueError(f"{path}: invalid_transitions account changed at {step['id']}")
            accounts.append(account)
        corrections.append((path, document, accounts))

    for path, document, accounts in corrections:
        if all(account.get("sandbox") is True for account in accounts):
            continue
        for account in accounts:
            account["sandbox"] = True
        path.write_text(yaml.safe_dump(document, sort_keys=False, width=100), encoding="utf-8")
    return protocol


def graded_compliance_tree(source: Path) -> Path:
    """The compliance tree the runner is pointed at: the pinned one, fixtures corrected.

    THE PINNED TREE IS NOT EDITED. This writes a sibling (``adcp-<version>-corrected/``,
    covered by the runner directory's existing ``adcp-*/`` ignore) and hands the runner
    that. These corrections repair fixtures while preserving what each check grades:

    * request-signing vector BODIES (``corrected_vectors``, adcp#7567). A seller
      validates the payload before it authenticates the caller, so the corpus's stub
      bodies (``{"plan_id":"plan_001"}``) are answered ``INVALID_REQUEST`` and the RFC
      9421 checklist never runs — measured here as all 27 graded signed-requests checks
      failing with ``got 200 (error="(none)")``.
    * a product for ``webhook_emission`` to buy (``corrected_storyboards``, adcp#7609).
      That storyboard discovers no product, so its four ``create_media_buy`` triggers
      ship the SDK's ``"test-product"`` placeholder and are answered
      ``PRODUCT_NOT_FOUND`` — measured as 4 of the 7 webhook_emission failures in run
      innet_200926_0635.

    * one sandbox account throughout ``invalid_transitions`` (adcp#8179/#8216).
      Expected-error samples bypass the SDK's account enrichment; without an explicit
      selector they reference a live account after setup created a sandbox buy.
      Backport the merged upstream six-selector correction while retaining every oracle.

    Everything else is copied through byte-for-byte. These local corrections preserve
    the pinned version until its replacement includes the upstream fixture fixes.
    """
    dest = source.parent.parent / f"{source.parent.name}-corrected" / source.name
    tree = corrected_vectors.corrected_compliance_tree(source, dest)
    correct_webhook_emission(tree)
    correct_invalid_transitions_accounts(tree)
    return tree
