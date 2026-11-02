"""A buyer sending the SPEC's exact JSON shape must have it land, on every transport.

This is the acceptance metric for the derived-announcement work (salesagent-prkv.5 R4):
not "does our advertised schema look tidy", but "if a consumer builds a payload straight
from the AdCP 3.1.1 request schema, is it accepted and does it take effect -- identically
on MCP, A2A and REST".

The payload here is not hand-written. It is built FROM the pinned schema's own declared
properties, restricted to the fields the tool implements, so the test cannot drift into
asserting a shape we invented. If the SDK pin moves and a field changes type, this fails.

Transport independence is the second half of the claim: the SAME payload is dispatched on
each transport and the SAME assertions run against each result. Only the envelope differs,
so a per-transport branch here would defeat the point and must never be added.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from tests.harness.capabilities import CapabilitiesEnv

_PINNED = pathlib.Path(importlib.util.find_spec("adcp").origin).parent / "_schemas/3.1"


def _pinned_request_properties(filename: str) -> dict:
    """The pinned schema's declared top-level properties for a request."""
    matches = list(_PINNED.rglob(filename))
    assert matches, f"{filename} not found under the pinned schema tree {_PINNED}"
    return json.loads(matches[0].read_text()).get("properties", {})


@pytest.mark.requires_db
class TestSpecShapedPayloadLands:
    """get_adcp_capabilities: this PR authored the whole request path, so it is the case
    where a drifted advertised shape would be our own doing."""

    #: A spec-shaped value per declared property we implement. Values are chosen to be
    #: valid under the PINNED schema, not under our own annotations.
    _SPEC_VALUES = {
        "protocols": ["media_buy"],
        "context": {"request_id": "spec-shaped-1"},
        "adcp_version": "3.1.1",
        "adcp_major_version": 3,
        "ext": {"acme_vendor": {"tier": "gold"}},
    }

    def _payload(self) -> dict:
        """Spec-declared fields ∩ fields the tool implements -- built, never hand-listed."""
        from src.core.tools.registry import TOOLS

        declared = set(_pinned_request_properties("get-adcp-capabilities-request.json"))
        # The ROW names the DTO. This resolved it from the MCP wrapper, which no longer
        # exists -- registration is generated from this same row.
        model = TOOLS["get_adcp_capabilities"].dto
        assert model is not None, "the capabilities tool must resolve to its request DTO"
        implemented = set(model.model_fields)

        payload = {k: v for k, v in self._SPEC_VALUES.items() if k in declared and k in implemented}
        assert payload, "built an empty payload -- the schema or the DTO join is broken"
        # Every value we send must be a field the SPEC declares; catches a test that has
        # drifted into exercising an invented field.
        assert set(payload) <= declared, f"payload carries non-spec fields: {set(payload) - declared}"
        return payload

    def test_the_spec_shape_is_fully_covered_by_what_we_implement(self):
        """Our implemented set must not silently shrink below the fields we send."""
        payload = self._payload()
        declared = set(_pinned_request_properties("get-adcp-capabilities-request.json"))
        missing = declared - set(payload)
        assert not missing, (
            f"the pinned schema declares {sorted(missing)} which this test does not send -- "
            "either the tool stopped implementing them or the fixture went stale"
        )

    def test_spec_shaped_payload_lands_on_mcp(self, integration_db):
        with CapabilitiesEnv() as env:
            env.setup_default_data()
            result = env.call_mcp(**self._payload())
        self._assert_landed(result, "mcp")

    def test_spec_shaped_payload_lands_on_a2a(self, integration_db):
        with CapabilitiesEnv() as env:
            env.setup_default_data()
            result = env.call_a2a(**self._payload())
        self._assert_landed(result, "a2a")

    # The REST leg is graded on the wire by @T-UC-010-ext-e-echo (context echoed) and
    # @T-UC-010-ext-d-filter (media_buy selected) on rest and e2e_rest; the copy that
    # stood here posted without credential headers and was deleted rather than migrated.

    @staticmethod
    def _assert_landed(result, transport: str) -> None:
        """Accepted AND acted on -- identical assertions for every transport."""
        data = result if isinstance(result, dict) else result.model_dump(mode="json")
        assert data.get("context") == {"request_id": "spec-shaped-1"}, (
            f"[{transport}] context was not echoed from the spec-shaped request; got {data.get('context')!r}. "
            "Landing means the value took effect, not merely that the call returned 200."
        )
        assert data.get("media_buy") is not None, (
            f"[{transport}] protocols=['media_buy'] did not select the media_buy section"
        )
        # The protocols FILTER is deliberately NOT asserted here, and this comment is the
        # record of why. Two earlier versions of that assertion were vacuous: checking only
        # that media_buy is PRESENT passes when protocols is ignored and everything is
        # returned; checking that signals/creative are ABSENT passes trivially because this
        # fixture's tenant never declares them (the four declaration blocks are measurement,
        # specialisms, supported_protocols and trusted_match -- none populates signals), so
        # disabling the filter outright reddened nothing.
        # It is graded properly, with a tenant that has sections to null out, by the wired
        # BDD scenario @T-UC-010-ext-d-filter on every transport.
        # Duplicating it here in a form that cannot fail is worse than not asserting it.
