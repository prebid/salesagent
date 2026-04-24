"""Integration tests for lazy-loading inventory tree API.

TDD Red phase: these tests define the lazy-loading contract for
GET /api/tenant/<id>/inventory/tree. They should ALL FAIL until the
backend is modified (salesagent-y6n3).

Three modes:
1. Root nodes (no params) — returns only roots + stats
2. Children (?parent_id=X) — returns direct children of X
3. Search (?search=term) — returns matching nodes + ancestors (limited)

Closes GitHub #1154 / salesagent-ozm9.
"""

import pytest

from src.core.database.database_session import get_db_session
from tests.factories import ALL_FACTORIES, GAMInventoryFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TENANT_ID = "tree_lazy_test"


@pytest.fixture()
def _bind_factories():
    """Bind factory sessions so factories can create ORM objects."""
    with get_db_session() as session:
        saved = {}
        for f in ALL_FACTORIES:
            saved[f] = (f._meta.sqlalchemy_session, f._meta.sqlalchemy_session_persistence)
            f._meta.sqlalchemy_session = session
            f._meta.sqlalchemy_session_persistence = "flush"
        yield session
        session.commit()
    for f, (orig_session, orig_persistence) in saved.items():
        f._meta.sqlalchemy_session = orig_session
        f._meta.sqlalchemy_session_persistence = orig_persistence


@pytest.fixture()
def tree_data(authenticated_admin_client, _bind_factories):
    """Seed a realistic 3-level hierarchy for tree tests.

    Structure:
        Root A (has_children=True)
            ├── Child A1 (has_children=True)
            │   ├── Grandchild A1a
            │   └── Grandchild A1b
            └── Child A2
        Root B (has_children=True)
            └── Child B1
        Root C (leaf — has_children=False)

    Also adds non-ad-unit inventory for stats testing.
    """
    tenant = TenantFactory(tenant_id=TENANT_ID, subdomain="tree-lazy-test")

    def _unit(inv_id, name, *, parent_id=None, has_children=False, status="ACTIVE"):
        return GAMInventoryFactory(
            tenant=tenant,
            inventory_id=inv_id,
            name=name,
            path=name.split(" / "),
            status=status,
            inventory_metadata={
                "parent_id": parent_id,
                "has_children": has_children,
                "ad_unit_code": f"code_{inv_id}",
                "sizes": [{"width": 300, "height": 250}],
            },
        )

    # Roots
    _unit("root_a", "Root A", has_children=True)
    _unit("root_b", "Root B", has_children=True)
    _unit("root_c", "Root C", has_children=False)

    # Children of Root A
    _unit("child_a1", "Child A1", parent_id="root_a", has_children=True)
    _unit("child_a2", "Child A2", parent_id="root_a", has_children=False)

    # Grandchildren of Child A1
    _unit("grand_a1a", "Grandchild A1a", parent_id="child_a1")
    _unit("grand_a1b", "Grandchild A1b", parent_id="child_a1")

    # Child of Root B
    _unit("child_b1", "Child B1", parent_id="root_b", has_children=False)

    # Unit with a distinctive path (for path search test)
    GAMInventoryFactory(
        tenant=tenant,
        inventory_id="path_match",
        name="Some Unit",
        path=["Network", "Sports", "Some Unit"],
        inventory_metadata={"parent_id": None, "has_children": False, "ad_unit_code": "code_path"},
    )

    # Non-ad-unit inventory for stats
    for inv_type, count in [("placement", 3), ("label", 2), ("custom_targeting_key", 4), ("audience_segment", 1)]:
        for i in range(count):
            GAMInventoryFactory(
                tenant=tenant,
                inventory_type=inv_type,
                inventory_id=f"{inv_type}_{i}",
                name=f"Test {inv_type} {i}",
                path=[],
                inventory_metadata={},
            )

    return tenant


# ---------------------------------------------------------------------------
# Mode 1: Root nodes (no params)
# ---------------------------------------------------------------------------


class TestRootNodes:
    """GET /inventory/tree with no params returns only root ad units."""

    def test_returns_only_roots(self, authenticated_admin_client, tree_data):
        """Only units with no parent_id should be returned."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree")
        assert resp.status_code == 200
        data = resp.get_json()

        root_ids = {u["id"] for u in data["root_units"]}
        assert root_ids == {"root_a", "root_b", "root_c", "path_match"}

    def test_roots_have_no_children_array(self, authenticated_admin_client, tree_data):
        """Root response should NOT include nested children (lazy-loaded on expand)."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree")
        data = resp.get_json()

        for unit in data["root_units"]:
            # Either no 'children' key, or an empty list
            children = unit.get("children", [])
            assert children == [], f"Root {unit['id']} should not have pre-loaded children"

    def test_roots_have_has_children_flag(self, authenticated_admin_client, tree_data):
        """Each root node must include has_children boolean."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree")
        data = resp.get_json()

        by_id = {u["id"]: u for u in data["root_units"]}
        assert by_id["root_a"]["has_children"] is True
        assert by_id["root_b"]["has_children"] is True
        assert by_id["root_c"]["has_children"] is False

    def test_roots_ordered_by_name(self, authenticated_admin_client, tree_data):
        """Root nodes must be ordered alphabetically for deterministic rendering."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree")
        data = resp.get_json()

        names = [u["name"] for u in data["root_units"]]
        assert names == sorted(names)

    def test_includes_stats(self, authenticated_admin_client, tree_data):
        """Root response must include inventory type counts and last_sync."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree")
        data = resp.get_json()

        assert data["placements"] == 3
        assert data["labels"] == 2
        assert data["custom_targeting_keys"] == 4
        assert data["audience_segments"] == 1

    def test_includes_total_active_count(self, authenticated_admin_client, tree_data):
        """Root response must include total count of active ad units."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree")
        data = resp.get_json()

        # 9 ad units total (3 roots + 3 children + 2 grandchildren + 1 path_match)
        assert data["total_active_count"] == 9

    def test_includes_truncated_flag(self, authenticated_admin_client, tree_data):
        """Root response must include truncated boolean."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree")
        data = resp.get_json()

        assert "truncated" in data
        assert data["truncated"] is False  # 3 roots is well under the limit

    def test_node_fields(self, authenticated_admin_client, tree_data):
        """Each node must include id, name, status, code, path, sizes, has_children."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree")
        data = resp.get_json()

        node = data["root_units"][0]
        required_fields = {"id", "name", "status", "code", "path", "has_children", "sizes"}
        assert required_fields.issubset(set(node.keys())), f"Missing fields: {required_fields - set(node.keys())}"

    def test_excludes_inactive_units(self, authenticated_admin_client, tree_data, _bind_factories):
        """Only ACTIVE ad units should be returned as roots."""
        # tree_data already created the tenant; add a STALE root
        GAMInventoryFactory(
            tenant_id=TENANT_ID,
            inventory_id="stale_root",
            name="Stale Root",
            status="STALE",
            inventory_metadata={"parent_id": None, "has_children": False},
        )
        _bind_factories.commit()  # Make visible to endpoint's session

        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree")
        data = resp.get_json()

        root_ids = {u["id"] for u in data["root_units"]}
        assert "stale_root" not in root_ids


# ---------------------------------------------------------------------------
# Mode 2: Children (?parent_id=X)
# ---------------------------------------------------------------------------


class TestChildren:
    """GET /inventory/tree?parent_id=X returns direct children of X."""

    def test_returns_direct_children(self, authenticated_admin_client, tree_data):
        """Only direct children of the specified parent should be returned."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?parent_id=root_a")
        assert resp.status_code == 200
        data = resp.get_json()

        child_ids = {u["id"] for u in data["units"]}
        assert child_ids == {"child_a1", "child_a2"}

    def test_children_have_has_children_flag(self, authenticated_admin_client, tree_data):
        """Children must include has_children for next-level lazy loading."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?parent_id=root_a")
        data = resp.get_json()

        by_id = {u["id"]: u for u in data["units"]}
        assert by_id["child_a1"]["has_children"] is True
        assert by_id["child_a2"]["has_children"] is False

    def test_children_ordered_by_name(self, authenticated_admin_client, tree_data):
        """Children must be ordered alphabetically."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?parent_id=root_a")
        data = resp.get_json()

        names = [u["name"] for u in data["units"]]
        assert names == sorted(names)

    def test_grandchildren(self, authenticated_admin_client, tree_data):
        """Can fetch grandchildren by passing a child's ID as parent_id."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?parent_id=child_a1")
        data = resp.get_json()

        grandchild_ids = {u["id"] for u in data["units"]}
        assert grandchild_ids == {"grand_a1a", "grand_a1b"}

    def test_leaf_node_returns_empty(self, authenticated_admin_client, tree_data):
        """Fetching children of a leaf node returns empty array."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?parent_id=root_c")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["units"] == []

    def test_nonexistent_parent_returns_empty(self, authenticated_admin_client, tree_data):
        """Fetching children of a nonexistent parent returns empty array."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?parent_id=does_not_exist")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["units"] == []

    def test_no_stats_in_children_response(self, authenticated_admin_client, tree_data):
        """Children response should not include stats (already loaded with roots)."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?parent_id=root_a")
        data = resp.get_json()

        # Stats fields should be absent in children mode
        assert "placements" not in data
        assert "labels" not in data
        assert "total_active_count" not in data

    def test_children_node_fields(self, authenticated_admin_client, tree_data):
        """Each child node has the same fields as root nodes."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?parent_id=root_a")
        data = resp.get_json()

        node = data["units"][0]
        required_fields = {"id", "name", "status", "code", "path", "has_children", "sizes"}
        assert required_fields.issubset(set(node.keys()))


# ---------------------------------------------------------------------------
# Mode 3: Search (?search=term)
# ---------------------------------------------------------------------------


class TestSearch:
    """GET /inventory/tree?search=term returns matching nodes + ancestors."""

    def test_search_returns_matching_nodes(self, authenticated_admin_client, tree_data):
        """Search results include nodes matching the search term."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?search=Grandchild")
        assert resp.status_code == 200
        data = resp.get_json()

        all_ids = _collect_ids(data["root_units"])
        assert "grand_a1a" in all_ids
        assert "grand_a1b" in all_ids

    def test_search_includes_ancestors(self, authenticated_admin_client, tree_data):
        """Search results include ancestor nodes for proper tree structure."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?search=Grandchild")
        data = resp.get_json()

        all_ids = _collect_ids(data["root_units"])
        assert "root_a" in all_ids  # grandparent
        assert "child_a1" in all_ids  # parent

    def test_search_tree_structure_intact(self, authenticated_admin_client, tree_data):
        """Search results form a proper tree: root -> child -> grandchild."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?search=Grandchild A1a")
        data = resp.get_json()

        # Root A should be the only root
        assert len(data["root_units"]) == 1
        root = data["root_units"][0]
        assert root["id"] == "root_a"

        # Child A1 should be under Root A
        assert len(root["children"]) == 1
        child = root["children"][0]
        assert child["id"] == "child_a1"

        # Grandchild A1a should be under Child A1
        assert len(child["children"]) == 1
        grandchild = child["children"][0]
        assert grandchild["id"] == "grand_a1a"

    def test_search_matched_flag(self, authenticated_admin_client, tree_data):
        """Matching nodes have matched_search=True, ancestors have matched_search=False."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?search=Grandchild A1a")
        data = resp.get_json()

        root = data["root_units"][0]
        child = root["children"][0]
        grandchild = child["children"][0]

        assert root.get("matched_search") is False  # ancestor, not a match
        assert child.get("matched_search") is False  # ancestor, not a match
        assert grandchild["matched_search"] is True  # actual match

    def test_search_includes_truncated_flag(self, authenticated_admin_client, tree_data):
        """Search response includes truncated boolean."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?search=Root")
        data = resp.get_json()

        assert "truncated" in data
        assert isinstance(data["truncated"], bool)

    def test_search_includes_search_active_flag(self, authenticated_admin_client, tree_data):
        """Search response includes search_active=True."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?search=Root")
        data = resp.get_json()

        assert data["search_active"] is True

    def test_search_no_results(self, authenticated_admin_client, tree_data):
        """Search with no matches returns empty root_units."""
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?search=zzz_no_match")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["root_units"] == []
        assert data["search_active"] is True

    def test_search_by_path(self, authenticated_admin_client, tree_data):
        """Search matches against path, not just name."""
        # tree_data creates 'path_match' unit with path ["Network", "Sports", "Some Unit"]
        resp = authenticated_admin_client.get(f"/api/tenant/{TENANT_ID}/inventory/tree?search=Sports")
        data = resp.get_json()

        all_ids = _collect_ids(data["root_units"])
        assert "path_match" in all_ids

    def test_search_deep_hierarchy_batch_ancestors(self, authenticated_admin_client, _bind_factories):
        """Deep hierarchy (5 levels) works correctly — verifies batch ancestor walk."""
        tenant = TenantFactory(tenant_id="deep_test", subdomain="deep-test")

        parent_id = None
        for depth in range(5):
            inv_id = f"depth_{depth}"
            name = f"Level {depth}" if depth < 4 else "DeepLeaf"
            GAMInventoryFactory(
                tenant=tenant,
                inventory_id=inv_id,
                name=name,
                inventory_metadata={
                    "parent_id": parent_id,
                    "has_children": depth < 4,
                },
            )
            parent_id = inv_id
        _bind_factories.commit()  # Make visible to endpoint's session

        resp = authenticated_admin_client.get("/api/tenant/deep_test/inventory/tree?search=DeepLeaf")
        data = resp.get_json()

        # Walk down the tree — all 5 levels should be present
        node = data["root_units"][0]
        assert node["id"] == "depth_0"
        for depth in range(1, 4):
            assert len(node["children"]) == 1, f"Missing child at depth {depth}"
            node = node["children"][0]
            assert node["id"] == f"depth_{depth}"
        # Leaf
        assert len(node["children"]) == 1
        leaf = node["children"][0]
        assert leaf["id"] == "depth_4"
        assert leaf["matched_search"] is True


# ---------------------------------------------------------------------------
# Helpers for test assertions
# ---------------------------------------------------------------------------


def _collect_ids(nodes):
    """Recursively collect all node IDs from a tree."""
    ids = set()
    for node in nodes:
        ids.add(node["id"])
        ids.update(_collect_ids(node.get("children", [])))
    return ids
