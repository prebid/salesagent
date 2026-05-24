from src.admin.blueprints.products import _catalog_acl_notification_scope


def test_catalog_acl_scope_treats_empty_acl_as_unrestricted() -> None:
    assert _catalog_acl_notification_scope([], ["buyer_1"]) is None
    assert _catalog_acl_notification_scope(["buyer_1"], []) is None


def test_catalog_acl_scope_includes_removed_and_added_principals() -> None:
    assert _catalog_acl_notification_scope(["buyer_1", "buyer_2"], ["buyer_2", "buyer_3"]) == [
        "buyer_1",
        "buyer_2",
        "buyer_3",
    ]
