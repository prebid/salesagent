"""MediaBuyCreateListEnv — composite env for the post-create get_media_buys poll.

The UC-019 storyboard scenario polls get_media_buys for a buy the SAME scenario
just created, so it needs both tools in one environment and one identity. That is
what the graded storyboard step does too: AdCP 3.1.1
``dist/compliance/3.1.1/domains/media-buy/scenarios/available_actions.yaml`` →
phase ``read_persisted_buy_actions`` → step ``get_created_buy_available_actions``
sends ``media_buy_ids: ["$context.<id captured from create_media_buy>"]`` and
validates the response against ``media-buy/get-media-buys-response.json``.

Shape mirrors ``MediaBuyDualEnv`` (create + update): extend ``MediaBuyCreateEnv``
and route by request type. The get_media_buys dispatch itself is inherited from
``MediaBuyListDispatchMixin`` rather than re-implemented, so this env and
``MediaBuyListEnv`` grade the same tool through the same code.

Routing happens at the ``deliver_*`` frame, not ``call_*``: ``BaseTestEnv`` owns
the one ``call_mcp``/``call_a2a`` pair as ``deliver_*(...).payload``, and the wire
channel a scenario grades — ``DeliverResult.wire_response`` — travels on the
RETURN VALUE. A ``call_*`` override here would both violate the single-dispatch
guard (``tests/unit/test_architecture_harness_single_dispatch.py``) and throw the
list branch's real wire away.

REST is routed too, and has to be: ``get_media_buys`` answers on
``POST /api/v1/media-buys/query`` and ``_NO_REST_UC_TAG_PREFIXES`` is now EMPTY
(tests/bdd/conftest.py), so every UC-019 scenario — this composite's included — is
parametrized on rest and e2e_rest. The list branch switches the endpoint, the body
builder and the response parser together, exactly as ``AccountSyncEnv`` switches
between its two verbs; the create branch is untouched.
``tests/integration/test_harness_rest_refusal.py`` pins all three switches, and
pins that the non-list branch still delegates through ``super()``.

A third verb, get_media_buy_delivery, is dispatched the same way from
``DeliveryDispatchMixin`` (tests/harness/delivery_poll.py): a scenario that creates a buy
and then reads its delivery needs both on one env and one identity, because a
factory-seeded buy carries its package ids in ``raw_request`` and cannot show what create
persisted. The delivery verb runs the production ``media_buy_delivery.get_adapter``
(``MediaBuyCreateEnv`` patches only the create module's), so a tenant that sells through
Google Ad Manager reaches the ``GoogleAdManager`` its seeded adapter config selects, and a
Mock-adapter tenant reaches the Mock adapter.

GH #1900, GH #1941
"""

from __future__ import annotations

from typing import Any, Literal

from tests.harness.delivery_poll import DeliveryDispatchMixin
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.media_buy_list import MediaBuyListDispatchMixin
from tests.harness.transport import DeliverResult

# The verb discriminators are the mixins' own ``is_delivery_request`` and
# ``is_list_request``, inherited rather than restated here: a local copy is a second
# definition of the same predicate, and the transport whose copy went stale would dispatch
# a different tool than the other three (CLAUDE.md DRY invariant).

Verb = Literal["create", "list", "delivery"]


class MediaBuyCreateListEnv(DeliveryDispatchMixin, MediaBuyListDispatchMixin, MediaBuyCreateEnv):
    """create_media_buy env that also dispatches get_media_buys and get_media_buy_delivery.

    A ``req=GetMediaBuyDeliveryRequest(...)`` kwarg routes to the delivery path, a
    ``req=GetMediaBuysRequest(...)`` to the list path, and anything else falls through to
    the inherited create path. ``req=`` is a free discriminator because the dispatchers
    this env actually uses — ``_run_a2a_handler`` and ``_run_mcp_client`` — already
    flatten a request model into the flat skill/tool parameters those wrappers accept.

    No extra patches: get_media_buys is a pure DB read, and get_media_buy_delivery reaches
    the production adapter (see the module docstring).
    """

    def _verb_of(self, kwargs: dict[str, Any]) -> Verb:
        """Which of the three verbs this dispatch is, by the request's type."""
        if self.is_delivery_request(kwargs):
            return "delivery"
        if self.is_list_request(kwargs):
            return "list"
        return "create"

    def call_impl(self, **kwargs: Any) -> Any:
        verb = self._verb_of(kwargs)
        if verb == "delivery":
            return self._call_delivery_impl(**kwargs)
        if verb == "list":
            return self._call_list_impl(**kwargs)
        return super().call_impl(**kwargs)

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Route by request CONTENT: delivery and list requests to their mixins, else create.

        JUSTIFIED OVERRIDE (``_KNOWN_DELIVER_OVERRIDES``): this env selects the
        tool AND the parser from request content, so it can declare no single
        ``A2A_SKILL`` for the base to dispatch on.

        The ``DeliverResult`` is returned AS IS — the mixins drive ``_run_a2a_handler``,
        which already yields one carrying the REAL artifact wire. Re-wrapping it would
        double-nest the payload and discard that wire.
        """
        verb = self._verb_of(kwargs)
        if verb == "delivery":
            return self._deliver_delivery_a2a(**kwargs)
        if verb == "list":
            return self._deliver_list_a2a(**kwargs)
        return super().deliver_a2a(**kwargs)

    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        """Content router; see :meth:`deliver_a2a`.

        The mixins return the ``DeliverResult`` whose ``wire_response`` is the real
        ``structured_content`` — the MCP bytes the envelope ``status`` assertions (#1941)
        grade.
        """
        verb = self._verb_of(kwargs)
        if verb == "delivery":
            return self._deliver_delivery_mcp(**kwargs)
        if verb == "list":
            return self._deliver_list_mcp(**kwargs)
        return super().deliver_mcp(**kwargs)

    # -- REST: one env, three endpoints ---------------------------------------
    #
    # ``_active_verb`` is set by BOTH writers below, unconditionally, because the two
    # REST paths read ``REST_ENDPOINT`` in OPPOSITE orders: ``RestE2EDispatcher``
    # calls ``build_rest_body`` first and then reads the attribute, while the
    # in-process ``RestDispatcher``/``call_rest`` read the attribute BEFORE building
    # the body. Setting the flag in both makes either order correct, and setting it
    # unconditionally means a create request always clears the verb a preceding list or
    # delivery request left. (The ``AccountSyncEnv`` shape, for the same reason.)
    _active_verb: Verb = "create"

    @property
    def REST_ENDPOINT(self) -> str:  # noqa: N802 — matches the inherited class-attr name
        """The route of the verb being dispatched; the inherited one for create.

        A @property because the verb is only known once the request is in hand, and
        the E2E dispatcher reads this attribute directly. ``super()`` and not a
        literal: ``MediaBuyCreateUpdateListEnv`` linearizes ``MediaBuyDualEnv`` behind
        this class, whose own ``REST_ENDPOINT`` property carries the per-id PUT URL an
        update needs — naming ``/api/v1/media-buys`` here would silently send every
        update in that composite to the create endpoint.
        """
        if self._active_verb == "delivery":
            return self.DELIVERY_REST_ENDPOINT
        if self._active_verb == "list":
            return self.LIST_REST_ENDPOINT
        return super().REST_ENDPOINT

    @property
    def REST_METHOD(self) -> str:  # noqa: N802 — dispatcher reads getattr(env, "REST_METHOD", "post")
        """POST for the list and delivery routes, whatever the primary verb uses otherwise.

        Declared for the same reason as ``REST_ENDPOINT``: under
        ``MediaBuyCreateUpdateListEnv`` the inherited property answers "put" whenever
        the update flag is set, and this class's read branches return from
        ``build_rest_body`` before that flag is recomputed — so a read following an update
        would otherwise PUT the read route. ``getattr`` over the proxy because the plain
        create env declares no such attribute and the dispatcher's own default is "post".
        """
        if self._active_verb != "create":
            return "post"
        return getattr(super(), "REST_METHOD", "post")

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        """Route the in-process REST call to the endpoint of the verb being dispatched.

        ``call_rest``/``RestDispatcher`` resolved ``endpoint`` from ``REST_ENDPOINT``
        before the verb of THIS request was set, so the read branches recompute it. The
        create branch forwards the argument UNCHANGED rather than recomputing: below this
        class sits ``MediaBuyDualEnv``, whose ``REST_ENDPOINT`` is a property over its
        own create-vs-update flag that is not set until its ``_run_rest_request``
        runs — reading it here would resolve against the PREVIOUS request's verb.
        """
        self._active_verb = self._verb_of(kwargs)
        if self._active_verb == "delivery":
            endpoint = self.DELIVERY_REST_ENDPOINT
        elif self._active_verb == "list":
            endpoint = self.LIST_REST_ENDPOINT
        return super()._run_rest_request(endpoint, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Build the body of whichever verb is being dispatched.

        Building the create body for a read request is not an option: it is
        create-shaped and dies inside ``_restore_creative_ids`` reading a ``packages``
        attribute the read request does not have. The refusal DIALECT is graded for the
        envs that genuinely cannot dispatch a transport —
        ``tests/integration/test_harness_rest_refusal.py``.
        """
        self._active_verb = self._verb_of(kwargs)
        if self._active_verb == "delivery":
            return self._build_delivery_rest_body(**kwargs)
        if self._active_verb == "list":
            return self._build_list_rest_body(**kwargs)
        # super(), not an explicit parent call: MediaBuyCreateUpdateListEnv resolves
        # this through MediaBuyDualEnv's stateful create/update routing, which naming
        # a parent directly would bypass.
        return super().build_rest_body(**kwargs)

    def parse_rest_response(self, data: dict[str, Any]) -> Any:
        """Parse the REST body into the response model of the verb that was dispatched.

        No reset of ``_active_verb`` here, unlike the ``MediaBuyDualEnv`` precedent:
        both writers above set it unconditionally at the start of every request, so a
        stale value cannot survive into the next one — and the E2E error path parses
        the error envelope instead of calling this, which would leave a reset-here
        value stale exactly when it matters.
        """
        if self._active_verb == "delivery":
            return self._parse_delivery_rest_response(data)
        if self._active_verb == "list":
            return self._parse_list_rest_response(data)
        return super().parse_rest_response(data)
