"""Unit tests for ``fire_tmp_sync``'s no-fire contract and its extraction helper.

Scope boundary — read this before adding a test here:

**The positive "sync fired and the provider got the packages" case is NOT here.**
That is one BDD scenario (``tests/bdd/features/local-tmp-package-sync.feature``)
fanned out over a2a/mcp/rest and e2e_rest through the env-owned seam
(``tests.harness._mixins.TMPSyncMixin``). This file previously carried "a
``threading.Thread`` was constructed with these kwargs" positives: an in-process
artifact no transport observes, and a strictly weaker restatement of the
dispatched tests on the same code path (#1197 review).

What remains, and why each is distinct:

- ``TestFiresTmpSyncDecorator`` — that both ``_impl`` functions carry the
  decorator (a new entry point cannot forget the sync), and its fire/no-fire
  contract on the sync and async forms. A dispatched scenario cannot express the
  no-fire half as sharply (a failed dispatch has no media_buy_id to assert
  absence against).
- ``TestExtractMediaBuyId`` — ``_extract_media_buy_id`` graded as the pure
  function it is: its return value per member of the result union. That pins the
  rename regression the typed extraction exists to prevent far more directly than
  a constructor call — a member renaming ``media_buy_id`` fails on the returned
  value, not on mock call kwargs.
- ``TestFireGuard`` — the ``if not media_buy_id or not tenant_id`` guard, observed
  on the production registry (``_active_syncs``): "no sync was registered" is a
  seam the feature owns, unlike ``threading.Thread``.

beads: salesagent-tmp-sync
"""

from __future__ import annotations

import logging

import pytest

from src.core.schemas import Error
from src.core.schemas._base import (
    CreateMediaBuyError,
    CreateMediaBuyResult,
    UpdateMediaBuyError,
    UpdateMediaBuyResult,
    UpdateMediaBuySubmitted,
)
from src.services.tmp_provider_sync import _active_syncs, _extract_media_buy_id, fire_tmp_sync
from tests.harness import make_identity
from tests.unit._tmp_helpers import make_create_result, make_update_result


class TestFiresTmpSyncDecorator:
    """The sync is a consequence of the write, wired once per ``_impl``.

    It used to be four calls at the transport edges, so a fifth entry point could
    silently forget it and "exactly one sync per media-buy write" was held by a
    docstring rather than by the code (#1197 review). These grade the property the
    four call sites were approximating.
    """

    def test_both_media_buy_impls_are_decorated(self):
        """The structural claim: the sync cannot be forgotten by a new entry point.

        Asserted on the marker ``fires_tmp_sync`` stamps, not on ``__wrapped__``:
        every ``functools.wraps`` decorator sets ``__wrapped__``, so that assertion
        stayed green if this decorator were swapped for any other while package
        sync went dead on all four transports (#1197 review).
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from src.core.tools.media_buy_update import _update_media_buy_impl
        from src.services.tmp_provider_sync import FIRES_TMP_SYNC_MARKER

        for impl in (_create_media_buy_impl, _update_media_buy_impl):
            assert getattr(impl, FIRES_TMP_SYNC_MARKER, False) is True, (
                f"{impl.__name__} is not decorated with @fires_tmp_sync — the sync would "
                "no longer fire on any transport"
            )

    def test_the_marker_is_specific_to_this_decorator(self):
        """The falsifiability of the test above: another wraps-decorator must not pass."""
        import functools

        from src.services.tmp_provider_sync import FIRES_TMP_SYNC_MARKER

        def _other_decorator(fn):
            @functools.wraps(fn)
            def _w(*a, **k):
                return fn(*a, **k)

            return _w

        @_other_decorator
        def _impl(*, identity):
            return None

        # A different wraps-decorator sets __wrapped__ ...
        assert getattr(_impl, "__wrapped__", None) is not None
        # ... and does NOT set the marker, which is why the guard can fail.
        assert getattr(_impl, FIRES_TMP_SYNC_MARKER, False) is False

    def test_doubles_use_the_production_signature_shape(self):
        """``identity`` is keyword-only on both impls, so the decorator's read is exhaustive.

        The contract used to be a convention stated in a docstring: ``identity``
        was positional-or-keyword, so a caller passing it positionally would have
        silently stopped the sync, and no double could see it because every double
        was already keyword-only (#1197 review).
        """
        import inspect

        from src.core.tools.media_buy_create import _create_media_buy_impl
        from src.core.tools.media_buy_update import _update_media_buy_impl

        for impl in (_create_media_buy_impl, _update_media_buy_impl):
            params = inspect.signature(inspect.unwrap(impl)).parameters
            assert params["identity"].kind is inspect.Parameter.KEYWORD_ONLY, (
                f"{impl.__name__}.identity must be keyword-only — fires_tmp_sync reads it "
                "from kwargs, so a positional call would silently disable the sync"
            )

    def test_fires_with_the_impl_result_and_identity_on_success(self):
        from unittest.mock import patch

        from src.services.tmp_provider_sync import fires_tmp_sync

        result = make_create_result("mb_decorated")
        identity = make_identity(tenant_id="tenant_1")

        @fires_tmp_sync
        def _impl(*, identity):
            return result

        with patch("src.services.tmp_provider_sync.fire_tmp_sync") as mock_fire:
            assert _impl(identity=identity) is result

        mock_fire.assert_called_once_with(result, identity)

    def test_does_not_fire_when_the_impl_raises(self):
        """The no-fire contract: a failed write must not sync."""
        from unittest.mock import patch

        from src.services.tmp_provider_sync import fires_tmp_sync

        @fires_tmp_sync
        def _impl(*, identity):
            raise RuntimeError("boom")

        with patch("src.services.tmp_provider_sync.fire_tmp_sync") as mock_fire:
            with pytest.raises(RuntimeError, match="boom"):
                _impl(identity=make_identity(tenant_id="tenant_1"))

        mock_fire.assert_not_called()

    def test_async_impl_is_awaited_before_firing(self):
        """``_create_media_buy_impl`` is async; the decorator must await, not fire on a coroutine."""
        import asyncio
        from unittest.mock import patch

        from src.services.tmp_provider_sync import fires_tmp_sync

        result = make_create_result("mb_async")

        @fires_tmp_sync
        async def _impl(*, identity):
            return result

        identity = make_identity(tenant_id="tenant_1")
        with patch("src.services.tmp_provider_sync.fire_tmp_sync") as mock_fire:
            assert asyncio.run(_impl(identity=identity)) is result

        mock_fire.assert_called_once_with(result, identity)

    def test_async_impl_does_not_fire_when_it_raises(self):
        import asyncio
        from unittest.mock import patch

        from src.services.tmp_provider_sync import fires_tmp_sync

        @fires_tmp_sync
        async def _impl(*, identity):
            raise RuntimeError("boom")

        with patch("src.services.tmp_provider_sync.fire_tmp_sync") as mock_fire:
            with pytest.raises(RuntimeError, match="boom"):
                asyncio.run(_impl(identity=make_identity(tenant_id="tenant_1")))

        mock_fire.assert_not_called()


class TestExtractMediaBuyId:
    """``_extract_media_buy_id`` returns the id per union member, or None.

    The union is narrowed with ``isinstance`` and the id read off the concrete
    member, so a rename is a type error rather than a silently disabled sync.
    Real models (never attribute-carrying mocks) — a mock would take the
    unrecognised-type branch instead of the one under test.
    """

    def test_create_success_returns_the_inner_id(self):
        assert _extract_media_buy_id(make_create_result("mb_inner_001")) == "mb_inner_001"

    def test_update_success_returns_the_inner_id(self):
        assert _extract_media_buy_id(make_update_result("mb_direct_001")) == "mb_direct_001"

    def test_none_response_returns_none(self):
        assert _extract_media_buy_id(None) is None

    def test_create_error_returns_none(self):
        result = CreateMediaBuyResult(
            status="failed",
            response=CreateMediaBuyError(errors=[Error(code="create_failed", message="nope")]),
        )
        assert _extract_media_buy_id(result) is None

    def test_update_error_returns_none(self):
        result = UpdateMediaBuyResult(
            status="failed",
            response=UpdateMediaBuyError(errors=[Error(code="update_failed", message="nope")]),
        )
        assert _extract_media_buy_id(result) is None

    def test_submitted_update_returns_none(self):
        """A submitted update has no media_buy_id yet — "no id" is the correct outcome."""
        assert _extract_media_buy_id(UpdateMediaBuySubmitted(status="submitted", task_id="task_1")) is None

    def test_unrecognised_type_warns_instead_of_vanishing(self, caplog):
        """A result type outside the union warns and returns None.

        The regression the typed extraction exists to surface: a new or renamed
        result type stops the sync, and the operator gets a line naming the type
        rather than silence (#1197 review).
        """

        class _NotAResult:
            pass

        with caplog.at_level(logging.WARNING, logger="src.services.tmp_provider_sync"):
            assert _extract_media_buy_id(_NotAResult()) is None  # type: ignore[arg-type]

        assert "_NotAResult" in caplog.text


class TestFireGuard:
    """``fire_tmp_sync`` registers a sync only when it has both ids.

    Observed on ``_active_syncs`` — the production registry — rather than on a
    patched ``threading.Thread``: "no sync is in flight for this media buy" is
    what the guard means, and the registry is where the feature says it.
    """

    @staticmethod
    def _registered_keys() -> set[str]:
        return set(_active_syncs.list_active())

    def test_no_sync_registered_when_response_is_none(self):
        before = self._registered_keys()
        fire_tmp_sync(None, make_identity(tenant_id="tenant_1"))
        assert self._registered_keys() == before

    def test_no_sync_registered_when_identity_absent(self):
        before = self._registered_keys()
        fire_tmp_sync(make_create_result("mb_guard_1"), None)
        assert self._registered_keys() == before
        assert "mb_guard_1" not in self._registered_keys()

    def test_no_sync_registered_when_tenant_id_is_none(self, caplog):
        """An identity with no tenant is logged, not silently dropped."""
        before = self._registered_keys()
        with caplog.at_level(logging.WARNING, logger="src.services.tmp_provider_sync"):
            fire_tmp_sync(make_create_result("mb_guard_2"), make_identity(tenant_id=None))

        assert self._registered_keys() == before
        assert "mb_guard_2" in caplog.text

    def test_no_sync_registered_for_an_error_response(self):
        result = UpdateMediaBuyResult(
            status="failed",
            response=UpdateMediaBuyError(errors=[Error(code="update_failed", message="nope")]),
        )
        before = self._registered_keys()
        fire_tmp_sync(result, make_identity(tenant_id="tenant_1"))
        assert self._registered_keys() == before
