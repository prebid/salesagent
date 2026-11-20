"""How the configured GAM network's answer is read back for a Then (tests/harness/gam_network.py).

The e2e realization of every GAM Then reads what the network holds; this grades the one
translation between that answer and the shape the in-process stand-in records: plain data,
unset fields absent, and each id the harness realized for a scenario id reported under it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tests.harness.gam_network import scenario_named


def _soap(type_name: str, **values: Any) -> SimpleNamespace:
    """A SOAP object as zeep answers one: its fields in ``__values__``, its type in ``_xsd_type``."""
    return SimpleNamespace(__values__=values, _xsd_type=SimpleNamespace(name=type_name))


def test_an_answer_reads_as_the_seller_wrote_it_under_the_scenarios_ids() -> None:
    creative = _soap(
        "ThirdPartyCreative",
        id=901,
        advertiserId=6007567433,
        size=_soap("Size", width=728, height=90, isAspectRatio=False),
        snippet="<div>ad</div>",
        appliedLabels=[],
        lockedOrientation=None,
    )
    association = _soap(
        "LineItemCreativeAssociation",
        lineItemId=555,
        creativeId=901,
        sizes=[_soap("Size", width=300, height=250, isAspectRatio=True)],
    )
    named = scenario_named([creative, association], {"6007567433": "3000001", "555": "9000001"})
    assert named == [
        {
            "id": 901,
            "advertiserId": "3000001",
            "size": {"width": 728, "height": 90},
            "snippet": "<div>ad</div>",
            "xsi_type": "ThirdPartyCreative",
        },
        {
            "lineItemId": "9000001",
            "creativeId": 901,
            "sizes": [{"width": 300, "height": 250, "isAspectRatio": True}],
            "xsi_type": "LineItemCreativeAssociation",
        },
    ]
