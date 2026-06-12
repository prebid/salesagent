#!/usr/bin/env python3
"""End-to-end smoke test against the FreeWheel **sandbox** via the real adapter client.

Drives ``src.adapters.freewheel.FreeWheelClient`` against
``https://api.sandbox.freewheel.tv`` using a token minted from the API Access
client-credentials service. Exercises the full sell-side path the adapter's
``create_media_buy`` depends on:

    auth -> inventory reads -> commercial reads -> advertiser (reuse/create) ->
    campaign -> insertion order -> placement -> creative resource ->
    (creative binding / forecasting probes) -> reverse-order cleanup

Credentials come from the environment (never printed):

    FREEWHEEL_CLIENT_ID
    FREEWHEEL_CLIENT_SECRET

Run::

    PYTHONPATH=. uv run python scripts/dev/freewheel/sandbox_e2e.py

Ephemeral entities (campaign, insertion order, placement, creative) are tagged
``scope3-sandbox-e2e-<uuid>`` and deleted in the ``finally`` block. Advertisers
are durable master data (DELETE returns 405) so an existing one is reused.

Sandbox routing quirks discovered live (differ from the production publisher API):
- Advertiser create is POST ``/services/v3/advertisers`` (plural); the adapter's
  campaign/IO/placement creates use the singular noun and are routed correctly.
- ``/auth/token/info`` returns 401 for API-Access tokens (legacy introspection).
- ``/reporting/*`` and ``standard_attributes`` need per-sandbox enablement.
- ad_unit_nodes cannot be created via API, so creative binding needs seeded inventory.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from urllib.error import HTTPError

from src.adapters.freewheel import FreeWheelClient, FreeWheelError
from src.adapters.freewheel._commercial import _element_to_dict
from src.adapters.freewheel.entities import Advertiser

TOKEN_URL = "https://token.apiaccess.freewheel.tv/oauth2/token"
SANDBOX_BASE_URL = "https://api.sandbox.freewheel.tv"
SAMPLE_VAST_URL = "https://samplelib.com/vast/sample-vast-2.0-inline-linear.xml"


class Report:
    """Collects per-step pass/fail/skip outcomes and prints a final summary."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []

    def _emit(self, mark: str, step: str, detail: str) -> None:
        print(f"{mark} {step}" + (f"  — {detail}" if detail else ""))
        self.rows.append((mark, step))

    def ok(self, step: str, detail: str = "") -> None:
        self._emit("✅", step, detail)

    def fail(self, step: str, detail: str = "") -> None:
        self._emit("❌", step, detail)

    def info(self, step: str, detail: str = "") -> None:
        self._emit("ℹ️ ", step, detail)

    def skip(self, step: str, detail: str = "") -> None:
        self._emit("⏭️ ", step, detail)

    def summary(self) -> int:
        passed = sum(1 for m, _ in self.rows if m == "✅")
        failed = sum(1 for m, _ in self.rows if m == "❌")
        print("\n" + "=" * 64)
        print(f"SUMMARY: {passed} passed, {failed} failed, {len(self.rows)} total steps")
        print("=" * 64)
        return 1 if failed else 0


def mint_token() -> str:
    """Mint a client-credentials bearer from the API Access token service."""
    cid = os.environ["FREEWHEEL_CLIENT_ID"]
    csec = os.environ["FREEWHEEL_CLIENT_SECRET"]
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def resolve_advertiser(client: FreeWheelClient, rpt: Report, label: str) -> int | None:
    """Reuse an existing advertiser, or create one (advertisers can't be deleted)."""
    try:
        existing = client.commercial.list_advertisers(per_page=5)
        if existing.items:
            advertiser_id = existing.items[0].id
            rpt.ok("advertiser (reuse existing)", f"advertiser_id={advertiser_id} count={existing.total_count}")
            return advertiser_id
        body = f'<?xml version="1.0" encoding="UTF-8"?>\n<advertiser><name>{label}</name></advertiser>'
        root = client._transport.post_xml("/services/v3/advertisers", body)
        advertiser_id = Advertiser.model_validate(_element_to_dict(root)).id
        rpt.ok("advertiser (created via POST /services/v3/advertisers)", f"advertiser_id={advertiser_id}")
        return advertiser_id
    except FreeWheelError as exc:
        rpt.fail("advertiser discover/create", f"{type(exc).__name__}: {exc}")
        return None


def main() -> int:
    if not os.environ.get("FREEWHEEL_CLIENT_ID") or not os.environ.get("FREEWHEEL_CLIENT_SECRET"):
        print("ERROR: set FREEWHEEL_CLIENT_ID and FREEWHEEL_CLIENT_SECRET", file=sys.stderr)
        return 2

    label = f"scope3-sandbox-e2e-{uuid.uuid4().hex[:8]}"
    rpt = Report()
    print(f"FreeWheel sandbox E2E — base_url={SANDBOX_BASE_URL} label={label}\n")

    client = FreeWheelClient(api_token=mint_token(), base_url=SANDBOX_BASE_URL)
    rpt.ok("Phase 1: mint client-credentials token + build FreeWheelClient")

    # ----- Phase 1: connectivity -----
    try:
        info = client.token_info()
        rpt.ok("token_info()", f"user_id={info.get('user_id')} expires_in={info.get('expires_in')}")
    except FreeWheelError as exc:
        rpt.info("token_info()", f"{type(exc).__name__} (sandbox legacy introspection — non-blocking)")

    # ----- Phase 2: inventory reads (v4 JSON) -----
    for name, fn in [("list_sites", client.inventory.list_sites), ("list_videos", client.inventory.list_videos)]:
        try:
            page = fn(per_page=5)
            rpt.ok(f"inventory.{name}()", f"total_count={page.total_count}")
        except FreeWheelError as exc:
            rpt.fail(f"inventory.{name}()", f"{type(exc).__name__}: {exc}")

    # ----- Phase 3+4: commercial read + advertiser (reuse or create) -----
    advertiser_id = resolve_advertiser(client, rpt, label)

    campaign_id: int | None = None
    io_id: int | None = None
    placement_id: int | None = None
    creative_id: int | None = None

    try:
        if advertiser_id is None:
            rpt.skip("commercial + creative write chain", "no advertiser available")
        else:
            # ----- Phase 5: commercial write round-trip (the create_media_buy backbone) -----
            campaign = client.commercial.create_campaign(name=label, advertiser_id=advertiser_id)
            campaign_id = campaign.id
            rpt.ok("commercial.create_campaign()", f"campaign_id={campaign_id} advertiser_id={campaign.advertiser_id}")

            io = client.commercial.create_insertion_order(name=label, campaign_id=campaign_id)
            io_id = io.id
            rpt.ok("commercial.create_insertion_order()", f"io_id={io_id} currency={io.currency} stage={io.stage}")

            placement = client.commercial.create_placement(name=label, insertion_order_id=io_id)
            placement_id = placement.id
            rpt.ok("commercial.create_placement()", f"placement_id={placement_id} status={placement.status}")

            fetched_io = client.commercial.get_insertion_order(io_id)
            ok = fetched_io.id == io_id and fetched_io.campaign_id == campaign_id
            (rpt.ok if ok else rpt.fail)("commercial.get_insertion_order() read-back", f"io_id={fetched_io.id}")

            fetched_placement = client.commercial.get_placement(placement_id)
            ok = fetched_placement.insertion_order_id == io_id
            (rpt.ok if ok else rpt.fail)("commercial.get_placement() read-back", f"placement_id={fetched_placement.id}")

            # ----- Phase 6: creative resource round-trip (v4 JSON) -----
            creative = client.creatives.create_creative(
                name=label,
                advertiser_ids=[advertiser_id],
                external_id=label,
                renditions=[
                    {
                        "uri": SAMPLE_VAST_URL,
                        "content_type": "application/xml",
                        "vast_rendition": True,
                        "https_compatibility": "compatible",
                    }
                ],
                duration=15,
            )
            creative_id = creative.id
            rpt.ok("creatives.create_creative()", f"creative_id={creative_id}")

            fetched_creative = client.creatives.get_creative(creative_id, include_renditions=True)
            rpt.ok(
                "creatives.get_creative() read-back",
                f"creative_id={fetched_creative.id} renditions={len(fetched_creative.renditions)}",
            )

            # ----- Phase 7: creative binding — needs an ad_unit_node (read-only master data) -----
            rpt.skip(
                "creatives.create_creative_instance() (binding)",
                "blank sandbox has no ad_unit_node; ad_unit_nodes aren't API-creatable — needs seeded inventory",
            )

            # ----- Phase 8: forecasting probe (placement-scoped) -----
            try:
                forecast = client.forecasting.nightly_forecast(placement_id)
                rpt.ok("forecasting.nightly_forecast()", f"placement_id={forecast.placement_id}")
            except FreeWheelError as exc:
                rpt.info("forecasting.nightly_forecast()", f"{type(exc).__name__} (needs per-sandbox enablement)")

    except FreeWheelError as exc:
        rpt.fail("write chain aborted", f"{type(exc).__name__}: {exc}")
    finally:
        # ----- Cleanup: reverse dependency order, best-effort. Advertiser is durable (DELETE -> 405). -----
        print("\n--- cleanup ---")
        if creative_id is not None:
            _cleanup(rpt, "creative", lambda: client.creatives.delete_creative(creative_id))
        if placement_id is not None:
            _cleanup(rpt, "placement", lambda: client.commercial.delete_placement(placement_id))
        if io_id is not None:
            _cleanup(rpt, "insertion_order", lambda: client.commercial.delete_insertion_order(io_id))
        if campaign_id is not None:
            _cleanup(rpt, "campaign", lambda: client.commercial.delete_campaign(campaign_id))

    return rpt.summary()


def _cleanup(rpt: Report, kind: str, deleter) -> None:
    try:
        deleter()
        rpt.ok(f"cleanup: deleted {kind}")
    except (FreeWheelError, HTTPError) as exc:
        rpt.info(f"cleanup: {kind} not deleted", f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
