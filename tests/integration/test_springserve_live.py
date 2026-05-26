"""Live SpringServe API smoke test (Stages 1 + 2 + 3).

Exercises the SpringServe client against the real API:

* Stage 1 — token minting and per-endpoint scope probes.
* Stage 2 — full Campaign + Demand Tag create-then-cleanup cycle, plus
  pause/resume at both levels.
* Stage 3 — Video creative upload, binding to a demand tag, then cleanup.

Skipped by default; runs only when credentials are provisioned via env
vars. Provide ONE of:

    SPRINGSERVE_TEST_API_TOKEN          (pre-minted, 2hr TTL)

or both of:

    SPRINGSERVE_USERNAME (or SPRINGSERVE_TEST_EMAIL)
    SPRINGSERVE_PASSWORD (or SPRINGSERVE_TEST_PASSWORD)

The write cycle additionally needs::

    SPRINGSERVE_TEST_DEMAND_PARTNER_ID  (int; defaults to 88061 for Talpa)

(SpringServe's auth field is named ``email`` in their API but takes the
account login -- the username and email names are accepted interchangeably.)

Run with::

    uv run pytest tests/integration/test_springserve_live.py -m live -v

Created entities are tagged with ``adcp-stage2-smoke-<uuid>`` in their
``secondary_code`` so any orphans from a failed cleanup are easy to
identify and reap by hand.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.adapters.springserve import SpringServeClient, SpringServeError

logger = logging.getLogger(__name__)

API_TOKEN_ENV = "SPRINGSERVE_TEST_API_TOKEN"
# Both naming conventions are accepted -- SpringServe's API field is ``email``
# but the account login is often referred to as ``username``.
EMAIL_ENVS = ("SPRINGSERVE_USERNAME", "SPRINGSERVE_TEST_EMAIL")
PASSWORD_ENVS = ("SPRINGSERVE_PASSWORD", "SPRINGSERVE_TEST_PASSWORD")

pytestmark = pytest.mark.live


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _build_client() -> SpringServeClient:
    api_token = os.environ.get(API_TOKEN_ENV)
    email = _first_env(*EMAIL_ENVS)
    password = _first_env(*PASSWORD_ENVS)

    if api_token:
        return SpringServeClient(api_token=api_token)
    if email and password:
        return SpringServeClient(email=email, password=password)
    pytest.skip(
        f"Live SpringServe test requires {API_TOKEN_ENV} or ({'/'.join(EMAIL_ENVS)} + {'/'.join(PASSWORD_ENVS)})"
    )


@pytest.fixture(scope="module")
def client() -> SpringServeClient:
    return _build_client()


class TestAuthAndConnectivity:
    def test_campaigns_endpoint_reachable(self, client: SpringServeClient):
        """Smoke probe -- the campaigns endpoint must respond 2xx for our token.

        This is the single Stage-1 must-pass live check: it proves the
        transport's auth header shape (raw token, no Bearer prefix) is
        correct AND that the bearer has read scope on the primary surface
        the adapter will use.
        """
        status, body = client.probe("GET", "/campaigns?per_page=1")
        assert status == 200, f"campaigns probe failed: HTTP {status}: {body[:200]}"


class TestPermissionsProbe:
    """One probe per endpoint the adapter will eventually touch; logs the
    status so the operator can see at-a-glance which scopes are granted
    on their test account."""

    @pytest.mark.parametrize(
        "path,feature",
        [
            ("/campaigns?per_page=1", "create_media_buy"),
            ("/demand_tags?per_page=1", "create_media_buy"),
            ("/videos?per_page=1", "sync_creatives"),
            ("/supply_tags?per_page=1", "inventory_sync"),
            ("/supply_partners?per_page=1", "inventory_sync"),
            ("/report?per_page=1", "delivery_reporting"),
        ],
    )
    def test_endpoint_scope(self, client: SpringServeClient, path: str, feature: str):
        status, body = client.probe("GET", path)
        # Soft-assert: log denied endpoints rather than fail the whole pass,
        # so a Stage 1 deploy can ship before every scope is granted.
        if status in (401, 403):
            logger.warning("SpringServe %s denied (HTTP %s) feature=%s body=%s", path, status, feature, body[:200])
        else:
            logger.info("SpringServe %s OK (HTTP %s) feature=%s", path, status, feature)
        # The probe itself must complete; auth failures fail loudly.
        assert status != 0, f"probe to {path} produced no status"


# ----- Stage 2 -----

DEMAND_PARTNER_ID_ENV = "SPRINGSERVE_TEST_DEMAND_PARTNER_ID"
DEFAULT_TALPA_DEMAND_PARTNER_ID = 88061


@pytest.fixture
def demand_partner_id() -> int:
    return int(os.environ.get(DEMAND_PARTNER_ID_ENV, str(DEFAULT_TALPA_DEMAND_PARTNER_ID)))


@pytest.fixture
def smoke_label() -> str:
    """Short unique label so concurrent runs and orphan reaping stay sane."""
    return f"adcp-stage2-smoke-{uuid.uuid4().hex[:8]}"


class TestWriteRoundTrip:
    """Live create→check→pause→resume→delete cycle for Campaign + Demand Tag.

    All created entities are deleted in a finally-cleanup so a failed test
    doesn't leave orphans on the account. Look for ``adcp-stage2-smoke-*``
    in ``secondary_code`` if cleanup ever misses one.
    """

    def test_full_cycle(self, client: SpringServeClient, demand_partner_id: int, smoke_label: str):
        from src.adapters.springserve import SpringServeForbiddenError

        start = datetime.now(UTC) + timedelta(days=7)
        end = start + timedelta(days=14)
        campaign = None
        demand_tag = None
        try:
            # Create campaign (paused).
            try:
                campaign = client.campaigns.create(
                    name=smoke_label,
                    demand_partner_id=demand_partner_id,
                    is_active=False,
                    secondary_code=smoke_label,
                    note="AdCP Stage 2 live smoke test -- safe to delete",
                    rate_currency="EUR",
                )
            except SpringServeForbiddenError as exc:
                pytest.skip(
                    "SpringServe POST /campaigns scope not granted on this account "
                    f"({exc.status_code}: {exc.body}). Ask SpringServe support to enable "
                    "write scope on Campaigns + Demand Tags for the API user."
                )
            assert campaign.id > 0
            assert campaign.demand_partner_id == demand_partner_id
            assert campaign.is_active is False
            logger.info("created campaign id=%s name=%s", campaign.id, campaign.name)

            # Create one demand tag (also paused) under the campaign.
            # demand_class=line_item is the AdCP-canonical provisioning path
            # (SpringServe hosts the creative). Omitting it falls back to the
            # account default, which on Talpa's account is Tag-class and
            # requires a vast_endpoint_url we don't have here.
            demand_tag = client.demand_tags.create(
                name=f"{smoke_label}_dt_1",
                campaign_id=campaign.id,
                demand_partner_id=demand_partner_id,
                start_date=start,
                end_date=end,
                format="video",
                rate=0.01,  # nominal CPM; tag is inactive anyway
                rate_currency="EUR",
                is_active=False,
                secondary_code="pkg_smoke",
                note="Stage 2 demand tag -- safe to delete",
                country_codes=["NL"],
                demand_class="line_item",
            )
            assert demand_tag.id > 0
            assert demand_tag.campaign_id == campaign.id
            assert demand_tag.is_active is False
            logger.info("created demand_tag id=%s", demand_tag.id)

            # Re-fetch the campaign; demand_tag_ids should include the new tag.
            campaign_refetched = client.campaigns.get(campaign.id)
            assert demand_tag.id in campaign_refetched.demand_tag_ids

            # Resume + re-pause cycle at the demand-tag level.
            client.demand_tags.update(demand_tag.id, is_active=True)
            refetched_dt = client.demand_tags.get(demand_tag.id)
            assert refetched_dt.is_active is True

            client.demand_tags.update(demand_tag.id, is_active=False)
            assert client.demand_tags.get(demand_tag.id).is_active is False

            # Resume + re-pause cycle at the campaign level.
            client.campaigns.update(campaign.id, is_active=True)
            assert client.campaigns.get(campaign.id).is_active is True
            client.campaigns.update(campaign.id, is_active=False)
            assert client.campaigns.get(campaign.id).is_active is False
        finally:
            # Best-effort cleanup; log but don't re-raise so the actual
            # assertion failure (if any) propagates.
            if demand_tag is not None:
                try:
                    client.demand_tags.delete(demand_tag.id)
                except SpringServeError as exc:
                    logger.warning("cleanup: failed to delete demand_tag %s: %s", demand_tag.id, exc)
            if campaign is not None:
                try:
                    client.campaigns.delete(campaign.id)
                except SpringServeError as exc:
                    logger.warning("cleanup: failed to delete campaign %s: %s", campaign.id, exc)


class TestCreativeRoundTrip:
    """Stage 3 live cycle -- POST /videos, bind to a demand tag, cleanup.

    Uses a tiny remote URL (Google's sample MP4 if reachable from
    SpringServe's network; otherwise the test passes the URL through and
    SpringServe may reject the ingest -- that's fine, we still verify
    the POST path returns a typed VideoCreative).
    """

    SAMPLE_VIDEO_URL = "https://www.w3schools.com/html/mov_bbb.mp4"

    def test_create_video_and_bind_to_demand_tag(
        self, client: SpringServeClient, demand_partner_id: int, smoke_label: str
    ):
        from src.adapters.springserve import SpringServeForbiddenError

        start = datetime.now(UTC) + timedelta(days=7)
        end = start + timedelta(days=14)
        campaign = None
        demand_tag = None
        creative = None
        try:
            try:
                campaign = client.campaigns.create(
                    name=f"{smoke_label}_creative",
                    demand_partner_id=demand_partner_id,
                    is_active=False,
                    secondary_code=smoke_label,
                    rate_currency="EUR",
                )
            except SpringServeForbiddenError as exc:
                pytest.skip(
                    f"SpringServe POST /campaigns scope not granted ({exc.status_code}); "
                    "see TestWriteRoundTrip for the scope-grant ask."
                )

            demand_tag = client.demand_tags.create(
                name=f"{smoke_label}_creative_dt",
                campaign_id=campaign.id,
                demand_partner_id=demand_partner_id,
                start_date=start,
                end_date=end,
                format="video",
                rate=0.01,
                rate_currency="EUR",
                is_active=False,
                secondary_code="pkg_creative_smoke",
                # Line Item class is what supports the Creatives tab + hosted
                # binding. Without this, SpringServe accounts whose default
                # class is "Tag" silently produce a demand tag that can't be
                # bound to hosted creatives.
                demand_class="line_item",
            )

            try:
                creative = client.creatives.create(
                    name=f"{smoke_label}_creative_asset",
                    demand_partner_id=demand_partner_id,
                    creative_remote_url=self.SAMPLE_VIDEO_URL,
                    creative_format="video",
                    creative_content_type="video/mp4",
                    secondary_code=smoke_label,
                )
            except SpringServeForbiddenError as exc:
                pytest.skip(
                    f"SpringServe POST /videos scope not granted ({exc.status_code}); "
                    "ask for write scope on Videos alongside Campaigns + Demand Tags."
                )
            assert creative.id > 0
            assert creative.creative_format == "video"

            # Bind creative -> demand_tag and verify.
            client.demand_tags.update(
                demand_tag.id,
                line_item_ratios=[{"creative_id": creative.id, "ratio": 1}],
                is_active=True,
            )
            refetched = client.demand_tags.get(demand_tag.id)
            assert refetched.line_item_ratios == [{"creative_id": creative.id, "ratio": 1}]
            assert refetched.is_active is True
        finally:
            if creative is not None:
                try:
                    client.creatives.delete(creative.id)
                except SpringServeError as exc:
                    logger.warning("cleanup: failed to delete creative %s: %s", creative.id, exc)
            if demand_tag is not None:
                try:
                    client.demand_tags.delete(demand_tag.id)
                except SpringServeError as exc:
                    logger.warning("cleanup: failed to delete demand_tag %s: %s", demand_tag.id, exc)
            if campaign is not None:
                try:
                    client.campaigns.delete(campaign.id)
                except SpringServeError as exc:
                    logger.warning("cleanup: failed to delete campaign %s: %s", campaign.id, exc)


class TestAudioVastTagRoundTrip:
    """Live audio path -- SpringServe Demand Tag passthrough to an audio VAST URL."""

    SAMPLE_AUDIO_VAST_URL = "https://samplelib.com/vast/sample-vast-audio.xml"

    def test_create_audio_tag_with_vast_endpoint_url(
        self, client: SpringServeClient, demand_partner_id: int, smoke_label: str
    ):
        from src.adapters.springserve import SpringServeForbiddenError

        start = datetime.now(UTC) + timedelta(days=7)
        end = start + timedelta(days=14)
        campaign = None
        demand_tag = None
        try:
            try:
                campaign = client.campaigns.create(
                    name=f"{smoke_label}_audio_tag",
                    demand_partner_id=demand_partner_id,
                    is_active=False,
                    secondary_code=smoke_label,
                    rate_currency="EUR",
                )
            except SpringServeForbiddenError as exc:
                pytest.skip(
                    f"SpringServe POST /campaigns scope not granted ({exc.status_code}); "
                    "see TestWriteRoundTrip for the scope-grant ask."
                )

            demand_tag = client.demand_tags.create(
                name=f"{smoke_label}_audio_dt",
                campaign_id=campaign.id,
                demand_partner_id=demand_partner_id,
                start_date=start,
                end_date=end,
                format="audio",
                rate=0.01,
                rate_currency="EUR",
                is_active=False,
                secondary_code="pkg_audio_vast_smoke",
                demand_class="tag",
                vast_endpoint_url=self.SAMPLE_AUDIO_VAST_URL,
            )
            refetched = client.demand_tags.get(demand_tag.id)
            assert refetched.id == demand_tag.id
            assert refetched.format == "audio"
        finally:
            if demand_tag is not None:
                try:
                    client.demand_tags.delete(demand_tag.id)
                except SpringServeError as exc:
                    logger.warning("cleanup: failed to delete demand_tag %s: %s", demand_tag.id, exc)
            if campaign is not None:
                try:
                    client.campaigns.delete(campaign.id)
                except SpringServeError as exc:
                    logger.warning("cleanup: failed to delete campaign %s: %s", campaign.id, exc)
