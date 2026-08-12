"""tests/helpers/pinned_schema.py must resolve to the SAME AdCP spec version
the installed adcp SDK is pinned to — not an independently vendored SHA.

Regression for PR #1838, reframed as a tier-1 DRY defect:
pinned_schema.py vendored its own frozen schema tree
(tests/fixtures/adcp_schemas_pinned/, pinned to
adcontextprotocol/adcp@04f59d2d5 / tag v3.1-04f59d2d5, the 3.1.0-beta.3 era)
independently of the installed adcp SDK's own pin (pyproject.toml, currently
6.6.0 / spec 3.1.1) — a full minor-track behind and already diverged (e.g.
get-products-response.json: 12 properties vendored vs 17 in the SDK's tree;
enums/error-code.json: 64 codes vendored vs 92 in the SDK's tree). A spec
bump that touches one pin and not the other ships tests grading two
different specs with no signal.

This test doesn't care HOW pinned_schema.py resolves a schema (the SDK's
plain tree or something else) — only that whatever it resolves comes from
the SAME physical directory the installed SDK ships, so there is exactly ONE
upstream pin in play, and that resolved schemas carry properties only the
SDK's current (3.1.1) tree has, never the old vendored fixture.
"""

from __future__ import annotations

import json

import adcp

from tests.helpers.pinned_schema import _resolve_filename, schema_root


class TestPinnedSchemaTracksSDKVersion:
    def test_schema_dir_is_inside_the_installed_sdk_package(self):
        """pinned_schema.py's schema directory must live under the installed
        adcp package, not a separately committed/vendored fixture tree."""
        import tests.helpers.pinned_schema as pinned_schema_module

        schema_dir = pinned_schema_module.schema_root().resolve()
        sdk_root = adcp.__file__
        import pathlib

        sdk_package_dir = pathlib.Path(sdk_root).resolve().parent
        assert schema_dir.is_relative_to(sdk_package_dir), (
            f"pinned_schema.py resolves schemas from {schema_dir}, which is outside "
            f"the installed adcp SDK package ({sdk_package_dir}) — it is still reading "
            f"an independently vendored/pinned tree instead of the SDK's own."
        )

    def test_get_products_response_has_sdk_only_properties(self):
        """get-products-response.json must carry properties the OLD vendored
        fixture never had (cache_scope) — proves resolution reads the SDK's
        current tree, not the frozen 04f59d2d5 snapshot."""
        schema_path = _resolve_filename("get-products-response.json")
        data = json.loads(schema_path.read_text())

        # Flatten oneOf/allOf arms into one property set for this check.
        all_props: set[str] = set(data.get("properties", {}) or {})
        for arm_key in ("oneOf", "allOf", "anyOf"):
            for arm in data.get(arm_key, []) or []:
                all_props |= set(arm.get("properties", {}) or {})

        assert "cache_scope" in all_props, (
            "get-products-response.json resolved by pinned_schema.py is missing "
            "'cache_scope' — this property only exists in the SDK's current schema "
            "tree, not the old vendored 04f59d2d5 fixture; its absence means "
            "pinned_schema.py is still reading the stale, independently-pinned tree."
        )

    def test_error_code_enum_has_sdk_count(self):
        """enums/error-code.json must have the SDK's current 92 codes, not the
        old vendored fixture's 64 — a direct, quantified version-drift check."""
        schema_path = _resolve_filename("error-code.json")
        data = json.loads(schema_path.read_text())
        codes = data.get("enum", [])
        assert len(codes) >= 90, (
            f"error-code.json resolved by pinned_schema.py has only {len(codes)} codes "
            "(the old vendored fixture had 64) — expected the SDK's current ~92-code "
            "enum, indicating resolution still reads the stale, independently-pinned tree."
        )

    def test_resolved_tree_version_equals_installed_sdk_pin(self):
        """schema_root()'s resolved tree must equal the installed SDK's own
        version claim (#1868 review).

        The three tests above grade content FLOORS (property presence, code
        count >= 90) — a future spec bump that leaves schema_root()
        pointing at a stale tree (e.g. major_minor hardcoded, derivation
        severed) would still satisfy every floor and go green. This asserts
        EQUALITY between the resolved tree's own index.json["adcp_version"]
        and adcp.get_adcp_spec_version() — the actual derivation the
        docstring promises ("must resolve to the SAME AdCP spec version the
        installed adcp SDK is pinned to"), not just a shape that happens to
        still satisfy today.
        """
        index = json.loads((schema_root() / "index.json").read_text())
        assert index["adcp_version"] == adcp.get_adcp_spec_version(), (
            f"schema_root()'s resolved tree carries adcp_version={index['adcp_version']!r}, "
            f"but the installed SDK is pinned to {adcp.get_adcp_spec_version()!r} — the "
            "derivation from the SDK's pin to the resolved schema tree is severed."
        )
