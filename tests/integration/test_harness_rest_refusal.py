"""A transport the harness cannot dispatch must refuse loudly, not dispatch something else.

``MediaBuyCreateListEnv`` routes ``req=GetMediaBuysRequest`` to the get_media_buys
dispatch. On IMPL/A2A/MCP it always did; on REST it does now, because
``get_media_buys`` HAS a REST route — ``@router.post("/media-buys/query")`` in
``src/routes/api_v1.py`` — so every UC-019 scenario is parametrized on rest and e2e_rest.
The per-UC ``_NO_REST_UC_TAG_PREFIXES`` exclusion that once withheld them is gone
entirely: reachability is the ``ToolSpec`` row's answer, and every row carries a
``RestBinding``.

That is a change of fact, not of obligation. The obligation is and was: a list request
must never be dispatched as something else. While the route did not exist, the only way
to honour it was to refuse; the inherited create builder would otherwise have done one
of two things with a list request, measured at the time:

  * ``req=`` branch: ``AttributeError: 'GetMediaBuysRequest' object has no attribute
    'packages'``, raised inside ``_restore_creative_ids`` (media_buy_create.py:54
    via :406) — an obscure crash in a create-only helper, not a refusal.
  * flat-kwargs branch: builds ``{"media_buy_ids": [...], "idempotency_key": ...}`` and
    POSTs it, create-SHAPED, to the create collection ``/api/v1/media-buys``.

With the route landed, the way to honour it is to dispatch the list request AT THE LIST
ROUTE, which ``TestListRestDispatchReachesTheListRoute`` pins end to end. A refusal here
now would fail a graded transport rather than guard an un-routed one.

The REFUSAL DIALECT is still graded below, because envs that genuinely cannot dispatch a
transport still need it, and getting it wrong is silent. Two launderers sit between such
a raise and the report:

  (a) ``tests/bdd/conftest.py:103-105`` converts any ``NotImplementedError`` raised
      in a BDD call phase into ``report.outcome = "skipped"`` + ``wasxfail``. A
      NotImplementedError refusal is therefore a silent shrink of the test matrix.
  (b) ``RestDispatcher.dispatch`` (dispatchers.py:151-181) wraps the whole in-process
      REST dispatch — INCLUDING ``env.build_rest_body`` — in ``except Exception ->
      TransportResult(error=exc)``. An ``Exception``-derived refusal comes back as an
      error-shaped result, indistinguishable from a production error response, so any
      Then step that only checks "an error was returned" goes GREEN on the harness
      refusing to dispatch.

``pytest.fail(..., pytrace=False)`` raises ``_pytest.outcomes.Failed``, whose MRO is
``(Failed, OutcomeException, BaseException)``: it is not an ``Exception`` (escapes (b))
and not a ``NotImplementedError`` (escapes (a)). ``test_weaker_refusal_dialects_are_swallowed``
below is the mechanical demonstration, against the real dispatcher.

GH #1941 (review finding F18); precedent for shape: tests/integration/test_harness_wire_response.py
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.schemas._base import GetMediaBuysRequest, GetMediaBuysResponse, UpdateMediaBuyRequest
from tests.harness.media_buy_create_list import MediaBuyCreateListEnv
from tests.harness.media_buy_create_update_list import MediaBuyCreateUpdateListEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.requires_db
class TestListRestDispatchReachesTheListRoute:
    """The REST branch of the create+list env dispatches get_media_buys, not create."""

    def test_rest_list_request_reaches_the_query_route(self, integration_db):
        """A REST get_media_buys call returns a list response, not a create-shaped call.

        Driven through ``env.call_via(Transport.REST, ...)`` — the whole dispatch — and
        NOT through a direct ``build_rest_body()`` call, for the same reason the refusal
        pin it replaces was: a direct-call assertion passes while ``RestDispatcher``'s
        ``except Exception`` swallows a real failure one call-frame over, so nothing
        propagates and the test grades a builder in isolation rather than a dispatch.

        ``GetMediaBuysResponse`` is the load-bearing assertion. The create route answers
        with a create result and the create builder crashes on ``packages`` before it
        ever POSTs, so neither can produce this type: a response of this shape is proof
        the list request reached ``POST /api/v1/media-buys/query``. The empty
        ``media_buys`` is read off ``require_wire`` — the HTTP body itself, not the
        re-parsed payload — and is the correct answer for an id no principal owns;
        grading it keeps the pin from passing on a route that answers anything at all.
        """
        with MediaBuyCreateListEnv() as env:
            # Seeds the tenant + principal the REST auth dep resolves the token against.
            # Without it the route answers AUTH_MISSING and the dispatch is graded on
            # the auth boundary rather than on which tool it reached.
            env.setup_media_buy_data()
            result = env.call_via(Transport.REST, req=GetMediaBuysRequest(media_buy_ids=["mb_absent"]))

        assert not result.is_error, f"REST get_media_buys did not dispatch: {result.error!r}"
        assert isinstance(result.payload, GetMediaBuysResponse), (
            f"REST dispatched something other than get_media_buys: got {type(result.payload).__name__}"
        )
        assert result.require_wire()["media_buys"] == []

    def test_rest_list_request_builds_the_list_body_at_the_list_route(self):
        """The body, endpoint and method all switch together for a list request.

        Three assertions and not one, because each is a separate way to dispatch the
        wrong call: a create-shaped body at the right route, the right body at the
        create collection, or the right body PUT rather than POSTed. The endpoint and
        method are read AFTER ``build_rest_body`` because that is the order
        ``RestE2EDispatcher`` reads them in.
        """
        env = MediaBuyCreateListEnv()

        body = env.build_rest_body(req=GetMediaBuysRequest(media_buy_ids=["mb_absent"]))

        assert body == {"media_buy_ids": ["mb_absent"]}
        assert env.REST_ENDPOINT == "/api/v1/media-buys/query"
        assert env.REST_METHOD == "post"


@pytest.mark.requires_db
class TestRefusalDialectIsStillGraded:
    """The dialect an env that genuinely cannot dispatch a transport must refuse in."""

    def test_refusal_escapes_both_launderers_by_type(self):
        """The refusal type is outside the reach of both launderers on the dispatch path.

        This is the property the choice of ``pytest.fail`` rests on, pinned so a future
        "simplification" back to ``NotImplementedError`` (which re-branches both) cannot pass
        silently. Launderer (b) is additionally demonstrated end-to-end against the real
        dispatcher in ``test_weaker_refusal_dialects_are_swallowed``.
        """
        failed = pytest.fail.Exception
        assert not issubclass(failed, Exception), (
            f"{failed.__name__} is an Exception subclass — RestDispatcher's "
            "`except Exception` (dispatchers.py:180) would swallow the refusal into an "
            "error-shaped TransportResult"
        )
        assert not issubclass(failed, NotImplementedError), (
            f"{failed.__name__} is a NotImplementedError subclass — tests/bdd/conftest.py:103-105 "
            "would convert the refusal into skipped + wasxfail"
        )
        assert issubclass(failed, BaseException)


class _RefusalDialectEnv(MediaBuyCreateListEnv):
    """A create+list env whose REST body builder refuses in a configurable dialect."""

    refusal: Any = None

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        raise self.refusal


@pytest.mark.requires_db
class TestRefusalDialectSurvivesTheDispatcher:
    """Why the dialect is ``pytest.fail`` and not the ``_outcome_helpers`` AssertionError."""

    @pytest.mark.parametrize(
        "refusal",
        [
            pytest.param(NotImplementedError("REST get_media_buys is not routed"), id="not-implemented-error"),
            pytest.param(AssertionError("REST get_media_buys is not routed"), id="assertion-error"),
        ],
    )
    def test_weaker_refusal_dialects_are_swallowed(self, integration_db, refusal):
        """An ``Exception``-derived refusal returns as an error-shaped result, not a raise.

        The refusal becomes ``TransportResult(error=...)`` — the same shape a real
        production error response produces — so a Then step asserting only "an error was
        returned" cannot tell "the seller rejected the request" from "the harness declined
        to dispatch". That is the launderer the pin above exists to route around, measured
        here against the production ``RestDispatcher`` rather than argued.
        """
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


@pytest.mark.requires_db
class TestNonListRestRoutingIsPreserved:
    """The non-list branch must delegate via ``super()``, not by naming a parent class.

    ``MediaBuyCreateUpdateListEnv.__mro__`` is [CreateUpdateList, CreateList,
    ListDispatchMixin, DualEnv, CreateEnv, IntegrationEnv, BaseTestEnv], so
    ``build_rest_body`` resolves to ``MediaBuyDualEnv.build_rest_body`` — the stateful
    create-vs-update router. A list override on ``MediaBuyCreateListEnv`` that fell back
    to ``MediaBuyCreateEnv.build_rest_body`` explicitly instead of
    ``super().build_rest_body(**kwargs)`` would skip that router: updates would build a
    create-shaped body and POST the collection. The same applies to ``REST_ENDPOINT``
    and ``REST_METHOD``, which are now properties on that class and must defer through
    ``super()`` for the two verbs below.
    """

    def test_update_request_still_routes_to_the_update_body_and_endpoint(self):
        env = MediaBuyCreateUpdateListEnv()
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id="mb_seeded",
            paused=True,
        )

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
