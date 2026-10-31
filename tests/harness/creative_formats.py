"""CreativeFormatsEnv — integration test environment for _list_creative_formats_impl.

Patches: the creative agent registry singleton (replaced by a fresh instance of
production's own testing-mode ``ReferenceFormatsRegistry`` per env, so each scenario has
its own format cache), and the audit logger.
Real: the registry, the tenant's ``creative_agents`` rows, the dial to a peer creative
agent over the egress seam, and the format processing logic.

A scenario that names the formats a seller serves calls ``set_registry_formats``. The
formats are served by a real peer creative agent -- the webhook-capture service, started
once per worker in process and running in the compose stack on e2e -- the tenant's one
``creative_agents`` row points at it, the tenant does not federate the standard creative
agent, and the tenant's one product declares each of them -- the list is a projection of
the seller's products, so a format no product declares is not listed. The seller then
lists exactly those formats, on every transport, through production's own path.

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with CreativeFormatsEnv() as env:
            env.set_registry_formats([format_1, format_2])
            response = env.call_impl()
            assert len(response.formats) == 2

Available mocks via env.mock:
    "registry"     -- get_creative_agent_registry (lazy import in creative_formats.py)
    "audit_logger" -- get_audit_logger (module-level import in creative_formats.py)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import ListCreativeFormatsResponse
from tests.harness._base import IntegrationEnv
from tests.harness.peer_agent import PeerCreativeAgentMixin
from tests.harness.transport import DeliverResult
from tests.helpers.peer_creative_agent import new_peer_key


class CreativeFormatsEnv(PeerCreativeAgentMixin, IntegrationEnv):
    """Integration test environment for _list_creative_formats_impl.

    Runs production's testing-mode registry against real ``creative_agents`` rows and a
    real peer creative agent; mocks only the audit logger.
    """

    # Dispatch declaration: the base owns call_mcp/call_a2a.
    RESPONSE_MODEL = ListCreativeFormatsResponse

    # JUSTIFIED OVERRIDE — does NOT declare MCP_TOOL/A2A_SKILL, so it does not
    # take the base's client-core delegation. AdCPTestClient's UNWRAP parses the
    # wire into spec_response_model("list_creative_formats") — the PINNED
    # ListCreativeFormatsResponse — and our real format-registry wire does not
    # satisfy it (measured: 2520 errors, e.g. formats.N.assets.M.max_count /
    # .assets required by the pinned Assets variants but absent from ours).
    # That is a list_creative_formats-vs-pinned-schema conformance gap, NOT a
    # dispatch defect, so it is recorded and graded elsewhere rather than being
    # hidden by making the core's parse swallow ValidationError.
    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        """Dispatch list_creative_formats via the real FastMCP Client pipeline."""
        return self._run_mcp_client("list_creative_formats", ListCreativeFormatsResponse, **kwargs)

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch list_creative_formats via the real A2A handler pipeline."""
        return self._run_a2a_handler("list_creative_formats", ListCreativeFormatsResponse, **kwargs)

    EXTERNAL_PATCHES = {
        "registry": "src.core.creative_agent_registry.get_creative_agent_registry",
        "audit_logger": "src.core.tools.creative_formats.get_audit_logger",
    }
    REST_ENDPOINT = "/api/v1/creative-formats"

    #: The formats :meth:`set_registry_formats` seeded, or None when no Given named any.
    _seeded_formats: list[Any] | None = None
    #: The tenant's one product whose declarations the listing is derived from, once seeded.
    _declaring_product: Any = None

    def _configure_mocks(self) -> None:
        """Install production's testing-mode registry, fresh per env, and a no-op audit logger.

        ``ReferenceFormatsRegistry`` answers the standard creative agent from the committed
        capture and dials every agent the tenant configured, exactly as the e2e server's
        registry does, so a scenario with no format Given lists the capture in both worlds.
        The patch exists only so each env gets its own instance -- and with it its own
        format cache -- rather than the process's memoized one.
        """
        from src.core.creative_agent_registry import ReferenceFormatsRegistry

        self.mock["registry"].return_value = ReferenceFormatsRegistry()
        self.mock["audit_logger"].return_value = MagicMock()

    def set_registry_formats(self, formats: list[Any]) -> None:
        """The seller's compatibility list is exactly *formats*, on every transport.

        Two facts make it so, because the list is a projection of the seller's products
        (``_list_creative_formats_impl``): a product declares each format, and a creative
        agent publishes each one's definition. ``seed_own_creative_agent`` serves them from
        the peer, points the tenant's peer row at it and stops federating the standard
        creative agent; :meth:`declare_formats` puts their ids on the tenant's product. The
        entries are served verbatim (no re-homing): the listing path returns each
        ``format_id`` as published, so a Given's format keeps the identity its request steps
        name.
        """
        self.seed_own_creative_agent(formats, rehome=False)
        self.declare_formats([fmt.format_id for fmt in formats])
        self._seeded_formats = list(formats)

    def declare_formats(self, format_ids: list[Any]) -> None:
        """The tenant's one product declares exactly *format_ids* as its named-format arm.

        A legacy-only product (``core/product.json``'s "Named-format product" branch), whose
        ``format_ids`` the seller serves as authored, so the listing derives exactly these
        ids. Each is stored as the column stores a reference, ``{agent_url, id}``; a re-seed
        re-points the same product rather than adding a second one. No ids and no product yet
        seeds none: a product declaring no format is a state the product forms refuse.
        """
        from tests.factories import ProductFactory

        references = [{"agent_url": str(fid.agent_url), "id": str(fid.id)} for fid in format_ids]
        if self._declaring_product is None:
            if not references:
                return
            self._declaring_product = ProductFactory(tenant=self.ensure_tenant(), format_ids=references)
        else:
            self._declaring_product.format_ids = references
        self._commit_factory_data()

    def declare_reference_formats(self, *format_ids: str) -> None:
        """The tenant's product declares the reference creative agent's formats named *format_ids*.

        The standard creative agent stays federated and publishes these definitions from the
        committed capture, so the seller lists exactly them: a scenario that needs real
        ``Format`` bodies on the wire, or more than one entry for a filter to remove, names
        them here.
        """
        from src.core.creative_agent_registry import PUBLIC_DEFAULT_AGENT_URL
        from src.core.schemas import FormatId

        self.declare_formats([FormatId(agent_url=PUBLIC_DEFAULT_AGENT_URL, id=fid) for fid in format_ids])

    def adapter_served_format_ids(self) -> list[Any]:
        """The ids the tenant's ad-server adapter serves its own formats under, as it states them."""
        from src.core.helpers.adapter_helpers import adapter_served_creative_formats

        return [fmt.format_id for fmt in adapter_served_creative_formats(self._tenant_id)]

    def set_peer_format_payload(self, payload: list[dict[str, Any]]) -> None:
        """A creative agent configured on the tenant answers ``list_creative_formats`` with *payload*.

        *payload* is the agent's ``formats`` array as raw JSON, served verbatim, so an
        entry the pinned schema cannot validate reaches the seller's OWN parse. Nothing
        here is a stand-in for that parse: the agent is a real MCP endpoint over TLS, the
        tenant row is a real ``creative_agents`` row, and the registry is production's
        testing-mode ``ReferenceFormatsRegistry``. The standard creative agent stays
        federated: these scenarios locate the peer's formats by the agent_url it published.

        The tenant's product declares every entry's ``format_id``, so an entry the seller
        keeps is one it lists: the listing is a projection of the products, and an entry no
        product declared would be absent whether or not the parse kept it.
        """
        from src.core.schemas import FormatId

        self.configure_peer_agent(
            self.serve_peer_catalog(new_peer_key(), {"formats": payload}), owner="set_peer_format_payload"
        )
        self.declare_formats([FormatId.model_validate(entry["format_id"]) for entry in payload])

    def served_registry_formats(self) -> list[Any]:
        """The formats the seller lists this scenario, on every transport.

        What :meth:`set_registry_formats` seeded -- the peer serves exactly those, the
        standard agent is not federated and the tenant's product declares each -- or, when
        no Given named any, nothing: the list is a projection of the seller's products, and
        a tenant with none declares no format.
        """
        return list(self._seeded_formats or [])

    def call_impl(self, **kwargs: Any) -> ListCreativeFormatsResponse:
        """Call _list_creative_formats_impl.

        Accepts 'req' (ListCreativeFormatsRequest) and 'identity' kwargs.
        Defaults to self.identity if not provided.
        """
        from src.core.tools.creative_formats import _list_creative_formats_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        kwargs.setdefault("req", None)
        return _list_creative_formats_impl(**kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Forward FLAT kwargs to the REST body, matching a2a/mcp.

        This env dispatches a2a and mcp BY TOOL NAME, so its callers pass flat
        kwargs (``format_ids=[...]``) rather than a ``req`` object. The inherited
        default only serializes a Pydantic ``req`` and returns ``{}`` for
        anything else — so every flat-kwarg filter was silently dropped on REST
        alone, and REST ran UNFILTERED while a2a and mcp filtered. A parametrized
        test then reported three green transports while only two graded the
        filter (found via salesagent-3dawm.16).

        ListCreativeFormatsBody (src/routes/api_v1.py) declares format_ids and
        every other filter, and the route maps them into
        ListCreativeFormatsRequest, so forwarding them makes REST filter for real.

        A Pydantic ``req`` still takes precedence, so callers that pass one keep
        the inherited behaviour.
        """
        from pydantic import BaseModel as PydanticBaseModel

        if isinstance(kwargs.get("req"), PydanticBaseModel):
            return super().build_rest_body(**kwargs)

        body: dict[str, Any] = {}
        for key, value in kwargs.items():
            if key in ("req", "identity") or value is None:
                continue
            if isinstance(value, PydanticBaseModel):
                body[key] = value.model_dump(mode="json", exclude_none=True)
            elif isinstance(value, list):
                body[key] = [
                    v.model_dump(mode="json", exclude_none=True) if isinstance(v, PydanticBaseModel) else v
                    for v in value
                ]
            else:
                body[key] = value
        return body

    # parse_rest_response: the base's, which revives RESPONSE_MODEL.
