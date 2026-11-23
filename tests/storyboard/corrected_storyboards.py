"""Storyboard corrections: give ``webhook_emission`` a product to buy.

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

What is corrected, and nothing else: one step is ADDED. No existing step, validation or
expected outcome is touched, so every check the storyboard graded before it still grades
the same thing — the four triggers simply reach the agent's create path instead of
dying on a placeholder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

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
