"""REST routing for create+list composite — list hits query, create stays collection.

``MediaBuyCreateListEnv`` routes ``req=GetMediaBuysRequest`` to get_media_buys on
every transport, including REST via ``POST /api/v1/media-buys/query`` (PR #1950 /
#1830). Before that route existed the list REST arm refused loudly with
``pytest.fail`` so a create-shaped body could not silently POST the collection;
that refusal is gone now that the route + list body builder are wired together.

What still must not regress:

  * List REST uses the query endpoint and list-shaped body (not create collection).
  * Non-list arms still ``super()`` into DualEnv create/update routing
    (``MediaBuyCreateUpdateListEnv``).
  * Weaker refusal dialects (``NotImplementedError`` / ``AssertionError``) remain
    swallowed by ``RestDispatcher`` — pinned so a future "simplify back to
    NotImplementedError" cannot silently shrink the matrix.

GH #1941 (review finding F18); GH #1830 REST query enablement.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.schemas._base import GetMediaBuysRequest, UpdateMediaBuyRequest
from tests.harness.media_buy_create_list import MediaBuyCreateListEnv
from tests.harness.media_buy_create_update_list import MediaBuyCreateUpdateListEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.requires_db
class TestRestListDispatchRoutesToQuery:
    """List REST arm hits /media-buys/query with a list-shaped body."""

    def test_rest_list_request_routes_through_call_via(self, integration_db):
        """A REST get_media_buys call dispatches — it does not pytest.fail refuse.

        Driven through ``env.call_via(Transport.REST, ...)`` so endpoint selection
        and body build both run (RestDispatcher reads endpoint before body build;
        ``_run_rest_request`` must re-select the query URL from request content).
        """
        with MediaBuyCreateListEnv() as env:
            body = env.build_rest_body(req=GetMediaBuysRequest(media_buy_ids=["mb_absent"]))
            assert env.REST_ENDPOINT == "/api/v1/media-buys/query"
            assert body.get("media_buy_ids") == ["mb_absent"]
            assert "packages" not in body

            result = env.call_via(Transport.REST, req=GetMediaBuysRequest(media_buy_ids=["mb_absent"]))

        assert result.error is None, f"list REST dispatch failed: {result.error!r}"
        assert result.payload is not None


class _RefusalDialectEnv(MediaBuyCreateListEnv):
    """A create+list env whose REST body builder refuses in a configurable dialect."""

    refusal: Any = None

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        raise self.refusal


@pytest.mark.requires_db
class TestRefusalDialectSurvivesTheDispatcher:
    """Why a harness refusal must use ``pytest.fail`` and not Exception subclasses."""

    @pytest.mark.parametrize(
        "refusal",
        [
            pytest.param(NotImplementedError("REST get_media_buys is not routed"), id="not-implemented-error"),
            pytest.param(AssertionError("REST get_media_buys is not routed"), id="assertion-error"),
        ],
    )
    def test_weaker_refusal_dialects_are_swallowed(self, integration_db, refusal):
        """An ``Exception``-derived refusal returns as an error-shaped result, not a raise."""
        env_class = type("_Dialect", (_RefusalDialectEnv,), {"refusal": refusal})
        with env_class() as env:
            result = env.call_via(Transport.REST, req=GetMediaBuysRequest(media_buy_ids=["mb_absent"]))

        assert result.is_error, "expected the dispatcher to have swallowed the refusal into an error result"
        assert result.error is refusal
        assert result.wire_error_envelope is None, (
            "a swallowed harness refusal carries no wire envelope — it never reached the server, "
            "which is exactly why it is indistinguishable from a transport-level production failure"
        )

    def test_the_chosen_dialect_propagates(self, integration_db):
        """The same env, refusing via ``pytest.fail``, raises out of the dispatcher."""
        env_class = type("_Dialect", (_RefusalDialectEnv,), {})

        def _refuse() -> None:
            pytest.fail("REST get_media_buys is not routed", pytrace=False)

        with env_class() as env:
            env.build_rest_body = lambda **kwargs: _refuse()  # type: ignore[method-assign]
            with pytest.raises(pytest.fail.Exception):
                env.call_via(Transport.REST, req=GetMediaBuysRequest(media_buy_ids=["mb_absent"]))

    def test_refusal_escapes_both_launderers_by_type(self):
        """The refusal type is outside the reach of both launderers on the dispatch path."""
        failed = pytest.fail.Exception
        assert not issubclass(failed, Exception), (
            f"{failed.__name__} is an Exception subclass — RestDispatcher's "
            "`except Exception` would swallow the refusal into an error-shaped TransportResult"
        )
        assert not issubclass(failed, NotImplementedError), (
            f"{failed.__name__} is a NotImplementedError subclass — tests/bdd/conftest.py "
            "would convert the refusal into skipped + wasxfail"
        )
        assert issubclass(failed, BaseException)


@pytest.mark.requires_db
class TestNonListRestRoutingIsPreserved:
    """The non-list arm must delegate via ``super()``, not by naming a parent class.

    ``MediaBuyCreateUpdateListEnv.__mro__`` is [CreateUpdateList, CreateList,
    ListDispatchMixin, DualEnv, CreateEnv, IntegrationEnv, BaseTestEnv], so
    ``build_rest_body`` resolves to ``MediaBuyDualEnv.build_rest_body`` — the stateful
    create-vs-update router. A list override on ``MediaBuyCreateListEnv`` that fell
    back to ``MediaBuyCreateEnv.build_rest_body`` explicitly instead of
    ``super().build_rest_body(**kwargs)`` would skip that router: updates would build a
    create-shaped body and POST the collection.
    """

    def test_update_request_still_routes_to_the_update_body_and_endpoint(self):
        env = MediaBuyCreateUpdateListEnv()
        req = UpdateMediaBuyRequest(media_buy_id="mb_seeded", paused=True)

        body = env.build_rest_body(req=req)

        assert "media_buy_id" not in body, (
            "update REST body still carries media_buy_id — build_rest_body did not reach "
            "MediaBuyDualEnv._build_update_rest_body, so super() delegation was bypassed"
        )
        assert body["paused"] is True
        assert env.REST_ENDPOINT == "/api/v1/media-buys/mb_seeded"
        assert env.REST_METHOD == "put"

    def test_create_request_still_routes_to_the_create_collection(self):
        env = MediaBuyCreateUpdateListEnv()
        body = env.build_rest_body(
            brand={"domain": "testbrand.com"},
            packages=[{"product_id": "prod_1", "budget": 1000, "pricing_option_id": "po_1"}],
        )

        assert body["brand"] == {"domain": "testbrand.com"}
        assert env.REST_ENDPOINT == "/api/v1/media-buys"
        assert env.REST_METHOD == "post"

    def test_list_request_routes_to_query_on_triple_env(self):
        env = MediaBuyCreateUpdateListEnv()
        body = env.build_rest_body(req=GetMediaBuysRequest(media_buy_ids=["mb_x"]))
        assert env.REST_ENDPOINT == "/api/v1/media-buys/query"
        assert body.get("media_buy_ids") == ["mb_x"]
