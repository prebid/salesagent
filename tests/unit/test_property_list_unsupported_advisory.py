"""Unit tests for the property_list UNSUPPORTED_FEATURE advisory pattern.

When a buyer sends ``targeting_overlay.property_list`` and the bound adapter
does not yet compile that field into native targeting, the success envelope
carries a per-package ``UNSUPPORTED_FEATURE`` advisory under
``errors``. This pattern makes the silent-drop window visible-to-buyers
between #1276 (round-trip lands) and #1314 / #1313 (adapter compile / hard-
reject land). When the adapter capability flips True, the advisory
disappears automatically.

Covers: UC-002-MAIN-14a (property_list round-trip with advisory on success)
Covers: UC-003-MAIN-13 (property_list update with advisory on success)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.core.schemas import (
    CreateMediaBuyRequest,
    CreateMediaBuySuccess,
    Error,
    UpdateMediaBuyRequest,
    UpdateMediaBuySuccess,
)
from src.services.targeting_capabilities import (
    build_property_list_unsupported_advisories,
    supports_property_list_filtering,
)

# ---------------------------------------------------------------------------
# Helper-level coverage
# ---------------------------------------------------------------------------


class TestSupportsPropertyListFiltering:
    """``supports_property_list_filtering`` is the single source of truth for
    "does the bound adapter compile property_list?" Both the capabilities
    declaration and the per-call advisory consult it; they must always agree."""

    def test_none_adapter_returns_false(self):
        """Without an adapter we can't compile anything — return False."""
        assert supports_property_list_filtering(None) is False

    def test_adapter_without_attr_returns_false(self):
        """Adapters that have not declared the ClassVar default to False —
        the spec-honest "off until you say otherwise" stance."""

        class _BareAdapter:
            pass

        assert supports_property_list_filtering(_BareAdapter()) is False

    def test_adapter_with_true_classvar_returns_true(self):
        """An adapter that sets ``supports_property_list_filtering = True``
        on its class flips the helper. This is the contract Kevel (#1314)
        and any future compiling adapter satisfy."""

        class _CompilingAdapter:
            supports_property_list_filtering = True

        assert supports_property_list_filtering(_CompilingAdapter()) is True

    def test_adapter_with_false_classvar_returns_false(self):
        """Explicit False is the same as the default — keeps the contract
        from depending on truthiness of a missing attribute."""

        class _NonCompilingAdapter:
            supports_property_list_filtering = False

        assert supports_property_list_filtering(_NonCompilingAdapter()) is False


# ---------------------------------------------------------------------------
# Advisory builder coverage
# ---------------------------------------------------------------------------


def _make_pkg_with_property_list(list_id: str = "v1"):
    """Build a mock package whose targeting_overlay.property_list is set."""
    pkg = MagicMock()
    pkg.targeting_overlay = MagicMock()
    pkg.targeting_overlay.property_list = MagicMock(list_id=list_id)
    return pkg


def _make_pkg_without_property_list():
    """Build a mock package whose targeting_overlay does not set property_list."""
    pkg = MagicMock()
    pkg.targeting_overlay = MagicMock()
    pkg.targeting_overlay.property_list = None
    return pkg


def _make_pkg_no_overlay():
    """Build a mock package with no targeting_overlay at all."""
    pkg = MagicMock()
    pkg.targeting_overlay = None
    return pkg


class TestBuildPropertyListUnsupportedAdvisories:
    """The shared helper returns one ``Error`` per offending package, or an
    empty list when nothing's offending. The list (not None) keeps the call
    sites simple — they wrap with ``or None`` at the boundary."""

    def test_no_packages_returns_empty(self):
        """``packages=None`` and ``packages=[]`` short-circuit to empty."""
        assert build_property_list_unsupported_advisories(None, False) == []
        assert build_property_list_unsupported_advisories([], False) == []

    def test_capability_supported_returns_empty_even_with_property_list(self):
        """When the adapter compiles the field, no advisory is needed."""
        pkgs = [_make_pkg_with_property_list(), _make_pkg_with_property_list()]
        assert build_property_list_unsupported_advisories(pkgs, True) == []

    def test_capability_unsupported_no_property_list_returns_empty(self):
        """Buyer didn't ask for property_list, nothing to warn about."""
        pkgs = [_make_pkg_without_property_list(), _make_pkg_no_overlay()]
        assert build_property_list_unsupported_advisories(pkgs, False) == []

    def test_one_advisory_per_offending_package(self):
        """Two packages with property_list → two advisories, each indexed."""
        pkgs = [
            _make_pkg_with_property_list("a"),
            _make_pkg_without_property_list(),  # skipped
            _make_pkg_with_property_list("b"),
        ]
        advisories = build_property_list_unsupported_advisories(pkgs, False)
        assert len(advisories) == 2
        codes = [a.code for a in advisories]
        fields = [a.field for a in advisories]
        assert codes == ["UNSUPPORTED_FEATURE", "UNSUPPORTED_FEATURE"]
        # Indices reference the original packages list, NOT the filtered set.
        assert fields == [
            "packages[0].targeting_overlay.property_list",
            "packages[2].targeting_overlay.property_list",
        ]

    def test_advisory_carries_message_and_suggestion(self):
        """Buyers need to know WHY (silent-drop window) and WHAT TO DO
        (keep sending). Message + suggestion satisfy both."""
        pkgs = [_make_pkg_with_property_list()]
        advisory = build_property_list_unsupported_advisories(pkgs, False)[0]
        assert "property_list_filtering" in advisory.message
        assert "persisted" in advisory.message
        assert advisory.suggestion is not None
        assert "Continue to send property_list" in advisory.suggestion


# ---------------------------------------------------------------------------
# Schema coverage — the success envelopes carry the advisory field
# ---------------------------------------------------------------------------


class TestSuccessEnvelopeErrorsField:
    """``CreateMediaBuySuccess`` and ``UpdateMediaBuySuccess`` carry the
    optional ``errors`` field for AdCP 3.0.7 non-fatal-in-payload advisories."""

    def test_create_success_round_trips_errors(self):
        """``errors`` is set, model_dump preserves it."""
        resp = CreateMediaBuySuccess(
            media_buy_id="mb_1",
            status="completed",
            packages=[],
            errors=[Error(code="UNSUPPORTED_FEATURE", message="m", field="f")],
        )
        dumped = resp.model_dump(exclude_none=True)
        assert "errors" in dumped
        assert dumped["errors"][0]["code"] == "UNSUPPORTED_FEATURE"

    def test_create_success_errors_absent_when_none(self):
        """No advisory → no ``errors`` key under ``exclude_none=True``
        (keeps spec-default response shape clean)."""
        resp = CreateMediaBuySuccess(media_buy_id="mb_1", status="completed", packages=[])
        dumped = resp.model_dump(exclude_none=True)
        assert "errors" not in dumped

    def test_update_success_round_trips_errors(self):
        resp = UpdateMediaBuySuccess(
            media_buy_id="mb_1",
            status="completed",
            affected_packages=[],
            errors=[Error(code="UNSUPPORTED_FEATURE", message="m", field="f")],
        )
        dumped = resp.model_dump(exclude_none=True)
        assert "errors" in dumped
        assert dumped["errors"][0]["code"] == "UNSUPPORTED_FEATURE"

    def test_update_success_errors_absent_when_none(self):
        resp = UpdateMediaBuySuccess(media_buy_id="mb_1", status="completed", affected_packages=[])
        dumped = resp.model_dump(exclude_none=True)
        assert "errors" not in dumped


# ---------------------------------------------------------------------------
# Request-level coverage — `req.packages` shape flows through the helper
# ---------------------------------------------------------------------------


class TestCreateRequestPackagesFlow:
    """``CreateMediaBuyRequest.packages`` carries the same Package model the
    helper inspects. Round-trip via the real Pydantic model so we catch any
    attribute drift between PackageRequest's targeting_overlay typing and
    what the helper reads."""

    def _make_request_with_property_list(self) -> CreateMediaBuyRequest:
        from tests.helpers.adcp_factories import create_test_package_request

        return CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id="prod_1",
                    budget=1000.0,
                    pricing_option_id="cpm_usd_fixed",
                    targeting_overlay={
                        "property_list": {
                            "agent_url": "https://gov.example",
                            "list_id": "v1",
                        },
                    },
                ),
            ],
            start_time="2030-01-01T00:00:00Z",
            end_time="2030-01-31T23:59:59Z",
        )

    def test_advisory_fires_through_real_request_model(self):
        """End-to-end: real CreateMediaBuyRequest → helper sees property_list →
        one advisory. Guards against PackageRequest's attribute shape changing
        without the helper noticing."""
        req = self._make_request_with_property_list()
        advisories = build_property_list_unsupported_advisories(req.packages, False)
        assert len(advisories) == 1
        assert advisories[0].field == "packages[0].targeting_overlay.property_list"


class TestUpdateRequestPackagesFlow:
    """Same end-to-end coverage for UpdateMediaBuyRequest."""

    def test_advisory_fires_through_real_update_request(self):
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_1",
            packages=[
                {
                    "package_id": "pkg_1",
                    "targeting_overlay": {
                        "property_list": {
                            "agent_url": "https://gov.example",
                            "list_id": "v1",
                        },
                    },
                },
            ],
        )
        advisories = build_property_list_unsupported_advisories(req.packages, False)
        assert len(advisories) == 1
        assert advisories[0].field == "packages[0].targeting_overlay.property_list"

    def test_update_without_packages_returns_empty(self):
        """An update with no ``packages`` (e.g. ``paused: true``) has nothing
        to advise about."""
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", paused=True)
        advisories = build_property_list_unsupported_advisories(req.packages, False)
        assert advisories == []


# ---------------------------------------------------------------------------
# Idempotency replay: advisory must be rebuilt live, not read from cache
# ---------------------------------------------------------------------------


class TestIdempotencyReplayRebuildsAdvisory:
    """The cached idempotency response is reconstructed from DB columns and
    doesn't persist the ``errors`` advisory list. Each replay rebuilds the
    advisory live using the CURRENT adapter capability — so the advisory
    disappears automatically when the capability flips True between Day-1
    and the replay, rather than locking in stale Day-1 state.
    """

    def test_replay_rebuild_helper_returns_advisory_when_capability_off(self):
        """The helper used by _build_idempotency_hit_result returns the same
        advisory it would have returned on the original request."""
        from tests.helpers.adcp_factories import create_test_package_request

        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id="prod_1",
                    budget=1000.0,
                    pricing_option_id="cpm_usd_fixed",
                    targeting_overlay={
                        "property_list": {
                            "agent_url": "https://gov.example",
                            "list_id": "v1",
                        },
                    },
                ),
            ],
            start_time="2030-01-01T00:00:00Z",
            end_time="2030-01-31T23:59:59Z",
        )
        # adapter=None mirrors the "no compiling adapter" state of today's
        # _build_idempotency_hit_result early-path call site
        # (FIXME(idempotency-adapter) in media_buy_create.py).
        advisories = build_property_list_unsupported_advisories(
            req.packages,
            supports_property_list_filtering(None),
        )
        assert len(advisories) == 1
        assert advisories[0].code == "UNSUPPORTED_FEATURE"

    def test_replay_rebuild_disappears_when_capability_flips_true(self):
        """The point of rebuild-live: when an adapter flips
        ``supports_property_list_filtering=True`` between Day-1 and the
        replay, the advisory naturally disappears. This is what makes
        rebuild correct vs persisting the Day-1 errors list verbatim."""
        from tests.helpers.adcp_factories import create_test_package_request

        class _CompilingAdapter:
            supports_property_list_filtering = True

        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id="prod_1",
                    budget=1000.0,
                    pricing_option_id="cpm_usd_fixed",
                    targeting_overlay={
                        "property_list": {
                            "agent_url": "https://gov.example",
                            "list_id": "v1",
                        },
                    },
                ),
            ],
            start_time="2030-01-01T00:00:00Z",
            end_time="2030-01-31T23:59:59Z",
        )
        advisories = build_property_list_unsupported_advisories(
            req.packages,
            supports_property_list_filtering(_CompilingAdapter()),
        )
        assert advisories == []
