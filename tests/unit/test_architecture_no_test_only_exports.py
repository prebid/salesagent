"""Guard: production modules must not carry test-only exports.

`spec_response_model` resolved a tool's pinned SDK RESPONSE model. It had ZERO
production callers — its only consumers were the harness client and its tests —
yet it lived in `src/core/version_compat.py`. A test-only export in a production
module is the "owned by two concerns" state the plan forbids leaving silent, and
the cheapest way for it to come back is for someone to re-import it "because it's
already there".

The companion assertion that `src/core/version_compat.py` does not re-export it is
gone, along with that module: v2 backward-compat was deleted whole, so there is no
longer a module for the symbol to come back to. What remains is the pair that still
grades something — the helper resolves from its home under `tests/`, and nothing in
`src/` imports it.

Deliberately narrow: this pins the ONE symbol the lane moved, rather than trying
to infer test-only-ness across the tree. A broad heuristic here would be a guard
that fails for reasons nobody can act on.
"""


def test_spec_response_model_resolves_from_its_new_home():
    from tests.harness.spec_models import spec_response_model

    assert spec_response_model("get_products").__name__ == "GetProductsResponse"
    # The request-side sibling used to stay in production because the acceptance
    # seam needed it. This sweep reverted that seam — src/ is byte-identical to
    # origin/main — so `spec_request_model` no longer exists in production and
    # asserting on it here would grade a symbol this branch deliberately removed.


def test_no_production_module_imports_the_harness_helper():
    """src/ must never import from tests/ — the direction that would re-couple them."""
    import pathlib

    offenders = [str(p) for p in pathlib.Path("src").rglob("*.py") if "tests.harness.spec_models" in p.read_text()]
    assert offenders == [], f"production modules importing the harness helper: {offenders}"
