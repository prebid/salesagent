"""wire_objects: attribute access over wire dict trees for BDD oracles."""

from tests.bdd.steps._outcome_helpers import wire_objects


def test_wire_objects_wraps_nested_dicts_for_attr_access() -> None:
    buys = wire_objects(
        [
            {
                "media_buy_id": "mb-1",
                "status": "active",
                "packages": [{"package_id": "pkg-1", "product_id": "prod-1"}],
            }
        ]
    )
    assert buys[0].media_buy_id == "mb-1"
    assert buys[0].status == "active"
    assert buys[0].packages[0].package_id == "pkg-1"
    assert buys[0].packages[0].product_id == "prod-1"


def test_wire_objects_leaves_scalars_unchanged() -> None:
    assert wire_objects("active") == "active"
    assert wire_objects(None) is None
    assert wire_objects([1, 2]) == [1, 2]
