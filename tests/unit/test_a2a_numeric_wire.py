"""Pin the A2A numeric wire contract: integers widen to integral floats.

Every A2A response DataPart is built as ``Part(data=_dict_to_value(...))``;
``google.protobuf.Value`` stores all numbers as ``number_value`` (a double),
so integers reach the JSON wire as ``14.0``. That is inherent to a2a-sdk
1.0's proto-first Part type and is NOT an AdCP conformance break (draft-07
``"type": "integer"`` matches any number with a zero fractional part), but
it IS a cross-transport divergence — MCP and REST preserve ints.

These tests pin both halves of the contract on the same read path the
harness and storyboard runners use (``MessageToJson``): values survive
exactly, int-ness does not. If an a2a-sdk upgrade ever starts preserving
integers, the widening pin fails first — update the documented contract at
``_dict_to_value`` and the qualified echo-step docstrings together with it.
"""

import json

from google.protobuf import json_format

from src.a2a_server.adcp_a2a_server import _dict_to_value


def _roundtrip(data: dict) -> dict:
    """Serialize through the same proto Value + JSON path the A2A wire uses."""
    return json.loads(json_format.MessageToJson(_dict_to_value(data)))


def test_a2a_wire_preserves_numeric_values() -> None:
    """Numbers survive the proto round-trip with their VALUES intact."""
    wire = _roundtrip(
        {
            "attribution_window": {"post_click": {"interval": 14, "unit": "days"}},
            "sequence_number": 3,
            "spend": 1234.56,
        }
    )
    assert wire["attribution_window"]["post_click"]["interval"] == 14
    assert wire["sequence_number"] == 3
    assert wire["spend"] == 1234.56


def test_a2a_wire_widens_integers_to_floats() -> None:
    """Int-ness is destroyed at Part construction — the documented contract.

    Deliberately asserts the WIDENED type: this is a pin on a2a-sdk 1.0's
    proto Value representation, not an endorsement. A failure here means the
    SDK started preserving integers — remove the widening caveats from
    _dict_to_value and then_attribution_echo in the same change.
    """
    wire = _roundtrip({"interval": 14, "nested": {"count": 0}})
    assert isinstance(wire["interval"], float), (
        "a2a-sdk now preserves integer types on the DataPart wire — update the "
        "documented numeric contract at _dict_to_value (and the echo-step "
        "docstrings) to match"
    )
    assert isinstance(wire["nested"]["count"], float)
