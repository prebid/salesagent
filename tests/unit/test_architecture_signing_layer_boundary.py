"""Guard: the signing layer owns its contract — production code never reaches beneath it.

#1291 (``salesagent-z6nr.33``). ``src/core/signing/`` is THE signing interface for this
application. Where ``adcp.signing`` is right, the layer delegates; where the pinned SDK
(``adcp==6.6.0``) is wrong, the layer carries the merged upstream fix. The property that
makes the layer worth having: a caller can never observe — and never needs to know —
whether a behaviour came from the SDK or from the layer's copy. That only holds while
callers cannot reach beneath the layer, which is what this guard pins, in two rules:

RULE A — no ``adcp.signing`` import in ``src/`` or ``scripts/`` outside the layer.
    The measured cost of not having this rule (2026-07-31): ``src/core/schemas/_base.py``
    imported ``canonicalize_target_uri`` straight from the SDK and thereby got UNFIXED
    canonicalization — it accepted every malformed authority shape the layer's gate
    exists to reject — and ``src/core/metrics.py`` imported ``REQUEST_TO_WEBHOOK_CODE``
    the same way. ~73 structural guards existed; none forbade either.

RULE B — no ``src.core.signing.<submodule>`` import in ``src/`` OR ``scripts/`` outside
    the layer. Callers import from ``src.core.signing`` (the facade) only. Without this
    rule the facade is decorative: at measurement time 19 import sites in ``src/`` reached
    through to submodules and ZERO imported the facade, so "everything else under
    ``src/core/signing/`` is private to the layer" was unenforced prose. Widened to also
    scan ``scripts/`` (#1291 A5 follow-up, salesagent-z6nr.27): rule A already scanned
    ``scripts/`` and rule B did not, an asymmetry nothing in this docstring ever argued for,
    and it let ``scripts/ops/provision_signing_key.py`` reach into two private submodules
    while its sibling transport (``src/admin/blueprints/signing_keys.py``) imported the
    same names from the facade.

Scope decisions (deliberate, from the salesagent-z6nr.33 design):

* ``tests/`` is EXEMPT from rule A: 18 test files legitimately import ``adcp.signing``
  to cross-check the layer AGAINST the SDK (parity tests are the point of them) and to
  build signed fixtures from SDK primitives. The guard's subject is production
  reach-through, not test cross-checks — so the scan covers ``src/`` and ``scripts/``.
  Rule B is exempt from ``tests/`` for the same reason its allowlist stays empty:
  submodule reach-through in test code couples a test to the layer's internal layout,
  which is real debt (tracked as salesagent-z6nr.36) but not production reach-through,
  which is this guard's subject.
* ``alembic/`` is scanned by NEITHER rule's roots (``src/``, ``scripts/``). Migrations
  are committed and this repo forbids editing one after commit
  (``alembic/versions/e7a2c40b91d5_add_signing_keys_table.py`` imports
  ``src.core.signing.algorithms`` directly) — a documented scope decision, not an
  allowlist entry. Both allowlists below stay ``frozenset()``.
* ``adcp.webhooks`` / ``adcp.webhook_auth`` are different subpackages and outside this
  guard per the ticket's wording; the webhook sending boundary has its own guard
  (``test_architecture_webhook_sender_boundary.py``).
* No TYPE_CHECKING carve-out (unlike ``test_architecture_local_schema_imports.py``):
  a type-only reach-through still couples the caller to the SDK's — or the layer's —
  internal layout, and the facade exports the types too.

Both allowlists are EMPTY and stay empty: the two rule-A leaks and the 19 rule-B
reach-through sites are fixed/migrated in the same change that turns this guard green
(salesagent-z6nr.33 plan steps 5-7), and per the repository rule allowlists only shrink.

Detector falsifiability: the meta-tests at the bottom run both detectors over synthetic
trees, proving each rule can go red — and stays silent on facade imports, on the layer's
own internal imports, and on ``adcp.webhooks``. A guard whose detector is only ever
pointed at a tree that already satisfies it is asserted green, never proven able to fail.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist

ROOT = Path(__file__).resolve().parents[2]

#: The layer. Files under these prefixes are the ONLY place SDK delegation may live, and
#: the only place that may import sibling submodules directly.
#:
#: TWO directories, ONE layer (salesagent-n78j0.3). The dependency-free leaf —
#: value-sets, canonicalization, error codes, the operation vocabulary — moved to
#: ``src/core/signing_contract/`` because Python runs a package's ``__init__`` before any
#: of its submodules, so while the leaf sat under ``src/core/signing/`` merely importing
#: a value-set executed the facade and pulled in ``keys`` -> ORM, middleware -> metrics,
#: and ``replay_store``/``revocation`` -> config. Three import cycles, all removed by the
#: move.
#:
#: THIS ENTRY GRANTS NO NEW PERMISSION. It tracks a path: the leaf was always part of the
#: layer and always allowed to delegate to the SDK. Both allowlists below remain EMPTY.
#: The first prefix stays first so the facade keeps being the layer's public surface.
LAYER_PREFIXES = ("src/core/signing/", "src/core/signing_contract/")

#: The facade's own directory — the one a probe writes a synthetic layer file into.
LAYER_PREFIX = LAYER_PREFIXES[0]


def _in_layer(rel: str) -> bool:
    """Is *rel* (a repo-relative posix path) a file of the signing layer?"""
    return any(rel.startswith(prefix) for prefix in LAYER_PREFIXES)


#: The facade module paths, DERIVED from the layer inventory rather than named. ``from
#: <facade> import <exported name>`` is the one sanctioned import shape for callers.
#:
#: Derived, because the singular ``FACADE`` this replaces named only the first of the two
#: packages ``LAYER_PREFIXES`` already lists, so every reach-through into the second was
#: invisible to Rule B while ``_layer_submodules()`` walked both. A third layer package
#: now extends Rule B without an edit, exactly as ``_layer_submodules()`` already does.
FACADES = tuple(prefix.rstrip("/").replace("/", ".") for prefix in LAYER_PREFIXES)

#: Rule A allowlist — (relative path, dotted import reference). EMPTY by design: the two
#: leaks that motivated the rule are fixed in the same change that lands this guard.
#: Per the repository rule, this may only shrink (i.e. stay empty).
ADCP_SIGNING_ALLOWLIST: frozenset[tuple[str, str]] = frozenset()

#: Rule B allowlist — (relative path, dotted import reference). EMPTY by design: the 19
#: measured reach-through sites migrate to the facade in the same change. Shrink-only.
SUBMODULE_REACH_THROUGH_ALLOWLIST: frozenset[tuple[str, str]] = frozenset()


def _layer_submodules(root: Path = ROOT) -> frozenset[str]:
    """The layer's submodule names (files and subpackages), derived from the tree.

    Derived rather than hand-listed so that adding a module to the layer (e.g. the
    ``_upstream/`` vendor package) extends rule B automatically.
    """
    names: set[str] = set()
    for prefix in LAYER_PREFIXES:
        layer_dir = root / prefix
        if not layer_dir.exists():
            continue
        names |= {p.stem for p in layer_dir.glob("*.py") if p.stem != "__init__"}
        names |= {p.name for p in layer_dir.iterdir() if p.is_dir() and not p.name.startswith("__")}
    return frozenset(names)


def _scan_files(roots: list[Path], *, relative_to: Path) -> list[tuple[str, ast.Module]]:
    """(relative path, parsed AST) for every .py file under *roots*, excluding the layer."""
    out: list[tuple[str, ast.Module]] = []
    for tree_root in roots:
        if not tree_root.exists():
            continue
        for path in sorted(tree_root.rglob("*.py")):
            rel = path.relative_to(relative_to).as_posix()
            if _in_layer(rel):
                continue
            out.append((rel, ast.parse(path.read_text(encoding="utf-8"), rel)))
    return out


def _adcp_signing_violations(files: list[tuple[str, ast.Module]]) -> set[tuple[str, str]]:
    """Rule A detector: every ``adcp.signing`` import outside the layer.

    Catches all three shapes: ``from adcp.signing[...] import X``,
    ``from adcp import signing`` and ``import adcp.signing[...]``.
    """
    found: set[tuple[str, str]] = set()
    for rel, tree in files:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "adcp.signing" or module.startswith("adcp.signing."):
                    found.update((rel, f"{module}.{alias.name}") for alias in node.names)
                elif module == "adcp":
                    found.update((rel, "adcp.signing") for alias in node.names if alias.name == "signing")
            elif isinstance(node, ast.Import):
                found.update(
                    (rel, alias.name)
                    for alias in node.names
                    if alias.name == "adcp.signing" or alias.name.startswith("adcp.signing.")
                )
    return found


def _submodule_reach_through_violations(
    files: list[tuple[str, ast.Module]],
    submodules: frozenset[str],
) -> set[tuple[str, str]]:
    """Rule B detector: every import of a layer SUBMODULE outside the layer.

    ``from src.core.signing import canonical_target_uri`` (a facade export) is the
    sanctioned shape and is not flagged; ``from src.core.signing import posture``
    (a submodule smuggled through the package) is.
    """
    found: set[tuple[str, str]] = set()
    for rel, tree in files:
        for node in ast.walk(tree):
            for facade in FACADES:
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.startswith(f"{facade}."):
                        found.update((rel, f"{module}.{alias.name}") for alias in node.names)
                    elif module == facade:
                        found.update(
                            (rel, f"{facade}.{alias.name}") for alias in node.names if alias.name in submodules
                        )
                elif isinstance(node, ast.Import):
                    found.update((rel, alias.name) for alias in node.names if alias.name.startswith(f"{facade}."))
    return found


@pytest.mark.arch_guard
class TestSigningLayerBoundary:
    """Production code depends on the signing layer's public surface and nothing beneath it."""

    def test_no_adcp_signing_import_outside_the_layer(self):
        """RULE A: nothing in src/ or scripts/ imports adcp.signing except the layer.

        An ``adcp.signing`` import outside ``src/core/signing/`` gets whatever the
        pinned SDK does — including the behaviours the layer exists to correct — and
        makes the caller observe SDK capability timing, which is exactly the coupling
        the layer removes.
        """
        files = _scan_files([ROOT / "src", ROOT / "scripts"], relative_to=ROOT)
        assert_violations_match_allowlist(
            _adcp_signing_violations(files),
            set(ADCP_SIGNING_ALLOWLIST),
            fix_hint=(
                "Import the behaviour from src.core.signing (the facade) instead. If the facade "
                "does not export it yet, export it there — the layer owns the signing contract; "
                "callers never see whether the SDK or the layer's vendored copy provides it."
            ),
        )

    def test_no_submodule_reach_through_outside_the_layer(self):
        """RULE B: src/ and scripts/ import the facade, never src.core.signing.<submodule>.

        Reach-through pins callers to the layer's internal layout, so vendoring or
        deleting a unit inside the layer (the whole point of the design: SDK bumps are
        a non-event) would ripple into caller imports. Scans scripts/ alongside src/
        (widened #1291 A5 follow-up, salesagent-z6nr.27) so an ops script cannot reach
        through where an admin blueprint transporting the same function is required to
        use the facade.
        """
        files = _scan_files([ROOT / "src", ROOT / "scripts"], relative_to=ROOT)
        assert_violations_match_allowlist(
            _submodule_reach_through_violations(files, _layer_submodules()),
            set(SUBMODULE_REACH_THROUGH_ALLOWLIST),
            fix_hint=(
                "Import the name from its package's facade — src.core.signing or "
                "src.core.signing_contract. Submodules under either are private to the layer; "
                "if a needed name is not on the facade, add it to THAT package's __init__.py "
                "explicit exports. The remedy names the package the violation is in: this "
                "detector covers every package in LAYER_PREFIXES, so a hint naming only the "
                "first would send the reader to the wrong __init__.py."
            ),
        )


# ---------------------------------------------------------------------------
# Meta-tests: the detectors can actually go red, and do not cry wolf.
# ---------------------------------------------------------------------------

#: Rule A probes — each reintroduces one import shape of the disease.
_SDK_LEAK_FROM = "from adcp.signing import canonicalize_target_uri\n"
_SDK_LEAK_FROM_SUBMODULE = "from adcp.signing.errors import REQUEST_TO_WEBHOOK_CODE\n"
_SDK_LEAK_FROM_PACKAGE = "from adcp import signing\n"
_SDK_LEAK_PLAIN_IMPORT = "import adcp.signing.canonical\n"

#: Rule A negative — a DIFFERENT adcp subpackage, explicitly outside this guard.
_SDK_OTHER_SUBPACKAGE = "from adcp.webhooks import WebhookSender\n"

#: Rule B probes — reach-through in both shapes, including the smuggled-through-package one.
_REACH_THROUGH_FROM = "from src.core.signing.posture import posture_for_tenant\n"
_REACH_THROUGH_VIA_PACKAGE = "from src.core.signing import posture\n"
_REACH_THROUGH_PLAIN_IMPORT = "import src.core.signing.posture\n"

#: The SAME three shapes against the LEAF package. Rule B was blind to every one of them
#: while ``FACADE`` was a single value, and a live violation sat at ``src/core/metrics.py``
#: unreported. Probing only the first package is what let that happen, so the probes are
#: paired with the detector's own domain rather than sampled from it.
_LEAF_REACH_THROUGH_FROM = "from src.core.signing_contract.vocabulary import resolved_operation_names\n"
_LEAF_REACH_THROUGH_VIA_PACKAGE = "from src.core.signing_contract import vocabulary\n"
_LEAF_REACH_THROUGH_PLAIN_IMPORT = "import src.core.signing_contract.vocabulary\n"

#: Rule B negative — the sanctioned facade import shape.
_FACADE_IMPORT = "from src.core.signing import canonical_target_uri\n"

#: Submodule names for the synthetic tree (mirrors a slice of the real layer).
#: The submodule names the Rule B probes reach through, one per layer package. A probe
#: whose submodule is missing here reports NOTHING and passes as a negative, which is how
#: ``vocabulary`` — the leaf package's own module, and the one carrying the live violation
#: — could have been probed and still looked clean.
_PROBE_SUBMODULES = frozenset({"posture", "canonical", "vocabulary"})


@pytest.mark.arch_guard
class TestSigningBoundaryDetectors:
    """Each rule's detector is proven able to go red on a synthetic tree."""

    @staticmethod
    def _probe(tmp_path: Path, source: str) -> list[tuple[str, ast.Module]]:
        probe = tmp_path / "src" / "probe.py"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(source, encoding="utf-8")
        return _scan_files([tmp_path / "src"], relative_to=tmp_path)

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (_SDK_LEAK_FROM, "adcp.signing.canonicalize_target_uri"),
            (_SDK_LEAK_FROM_SUBMODULE, "adcp.signing.errors.REQUEST_TO_WEBHOOK_CODE"),
            (_SDK_LEAK_FROM_PACKAGE, "adcp.signing"),
            (_SDK_LEAK_PLAIN_IMPORT, "adcp.signing.canonical"),
        ],
    )
    def test_rule_a_detector_finds_a_reintroduced_leak(self, tmp_path, source, expected):
        """POSITIVE: every adcp.signing import shape outside the layer is discovered."""
        found = _adcp_signing_violations(self._probe(tmp_path, source))
        assert found == {("src/probe.py", expected)}

    def test_rule_a_detector_ignores_other_adcp_subpackages(self, tmp_path):
        """NEGATIVE: adcp.webhooks is a different subpackage, outside this guard."""
        assert _adcp_signing_violations(self._probe(tmp_path, _SDK_OTHER_SUBPACKAGE)) == set()

    def test_rule_a_detector_ignores_the_layer_itself(self, tmp_path):
        """NEGATIVE: the layer's own SDK delegation is where delegation is supposed to live."""
        layer_file = tmp_path / LAYER_PREFIX / "canonical.py"
        layer_file.parent.mkdir(parents=True, exist_ok=True)
        layer_file.write_text(_SDK_LEAK_FROM, encoding="utf-8")
        files = _scan_files([tmp_path / "src"], relative_to=tmp_path)
        assert _adcp_signing_violations(files) == set()

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (_REACH_THROUGH_FROM, "src.core.signing.posture.posture_for_tenant"),
            (_REACH_THROUGH_VIA_PACKAGE, "src.core.signing.posture"),
            (_REACH_THROUGH_PLAIN_IMPORT, "src.core.signing.posture"),
            (
                _LEAF_REACH_THROUGH_FROM,
                "src.core.signing_contract.vocabulary.resolved_operation_names",
            ),
            (_LEAF_REACH_THROUGH_VIA_PACKAGE, "src.core.signing_contract.vocabulary"),
            (_LEAF_REACH_THROUGH_PLAIN_IMPORT, "src.core.signing_contract.vocabulary"),
        ],
    )
    def test_rule_b_detector_finds_reach_through(self, tmp_path, source, expected):
        """POSITIVE: submodule reach-through is discovered in all three import shapes."""
        found = _submodule_reach_through_violations(self._probe(tmp_path, source), _PROBE_SUBMODULES)
        assert found == {("src/probe.py", expected)}

    def test_rule_b_detector_ignores_facade_imports(self, tmp_path):
        """NEGATIVE: importing an exported NAME from the facade is the sanctioned shape."""
        found = _submodule_reach_through_violations(self._probe(tmp_path, _FACADE_IMPORT), _PROBE_SUBMODULES)
        assert found == set()

    def test_layer_submodules_are_derived_from_the_tree(self):
        """The rule-B submodule set tracks the real layer, so new modules are covered."""
        submodules = _layer_submodules()
        assert "canonical" in submodules
        assert "webhook_sender_factory" in submodules
        assert "__init__" not in submodules
