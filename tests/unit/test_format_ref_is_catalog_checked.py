"""``resolvable_format_ref`` is the door a fixture authors a creative's CLAIM through.

A claimed ``format_id`` is the one format literal in a fixture that production resolves over
the wire: ``creatives/_validation.py`` hands it to ``fetch_format_spec`` and refuses the
creative REFERENCE_NOT_FOUND when the agent does not serve it. The in-process harness mocks
the registry to answer every resolution with one stub, so a claim naming a format nobody
serves reads GREEN on three transports and fails only on e2e_rest -- which is how
``video_640x480`` survived in BR-UC-FORMAT-SATISFACTION until a box run graded it
(salesagent-yfu33).

Both halves of a reference are graded here, because a reference only resolves as a pair:

- the ID must be in the pinned capture;
- the AGENT_URL must be one production treats as an OPERATOR agent. That is asked of
  production's own ``is_operator_agent`` rather than compared to a string written here --
  the alternative pins the spelling and grades nothing. An operator reference is answered
  from the catalog under ``ADCP_TESTING`` and rerouted to the pinned container by
  ``_connection_agent_url`` otherwise; any other url is a counterparty judged by the egress
  seam.

So a fixture must NOT name the dialable ``CREATIVE_AGENT_URL`` the e2e stack configures, even
though that is the url the run can actually reach: it is an MCP endpoint, not an identity, and
``is_operator_agent`` never consults it. The operator agent is recognized by origin against
``CREATIVE_AGENT_ORIGIN``, which the e2e stack sets to the public agent's origin in the RUNNER
and the SERVER alike, because its container stands in for the public agent. The catalog's own
entries all declare the public identity too, so it is also what
``identity.legacy`` is keyed on when a product's arm and a creative's claim are compared. The
fixture names the identity; production decides the transport.
"""

from __future__ import annotations

import pytest

from src.core.creative_agent_registry import is_operator_agent
from src.core.format_cache import load_reference_formats
from tests.factories.format import resolvable_format_ref


def _catalog_ids() -> set[str]:
    return {fmt.format_id.id for fmt in load_reference_formats()}


def test_a_catalog_id_yields_a_reference_production_treats_as_operator() -> None:
    catalog = _catalog_ids()
    # The population, asserted: a catalog that loaded empty would make every membership
    # check below pass for the wrong reason.
    assert len(catalog) > 1, f"the pinned capture served {len(catalog)} formats; nothing to select from"

    served = sorted(catalog)[0]
    ref = resolvable_format_ref(served)

    assert ref["id"] == served
    assert is_operator_agent(ref["agent_url"]), (
        f"{ref['agent_url']!r} is not an operator agent, so production judges a reference naming it "
        "by the egress seam instead of answering it from the catalog"
    )


def test_an_id_the_catalog_does_not_serve_is_refused_with_the_remedy() -> None:
    absent = "video_640x480"
    # The id is the one that actually escaped, so this test fails the day it is published --
    # at which point it is no longer an example of the defect and must be replaced.
    assert absent not in _catalog_ids(), f"{absent!r} is now in the capture; pick an id that is still absent"

    with pytest.raises(ValueError) as exc:
        resolvable_format_ref(absent)

    message = str(exc.value)
    assert absent in message, "the refusal does not name the id it refused"
    assert "reference_formats.json" in message, "the refusal does not name the authority"
    assert "pick_reference_format" in message, "the refusal does not print the remedy"
