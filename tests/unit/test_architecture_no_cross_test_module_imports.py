"""Guard: a ``test_*.py`` module must not import from a sibling ``test_*.py`` module.

The disease: a shared helper lives in a module whose JOB is to be a test, so an
unrelated suite imports it. The exporter is then pinned by a consumer nobody
reading it can see — renaming it, splitting it, or moving a class out of it
silently breaks a different suite, and the breakage surfaces as a collection
error in a file the author never touched. Helpers that are meant to be imported
already have homes: ``tests/helpers/**``, ``tests/factories/**``,
``tests/bdd/steps/**``, ``conftest.py``, and suite-local ``_*_helpers.py`` /
``tests/unit/_architecture_helpers.py``. Imports from those are NOT the disease
and are not scanned — only a ``test_*`` EXPORTER counts.

Detection is import-graph/AST based, not regex: the four shapes below are all
graded off ``ast.Import`` / ``ast.ImportFrom`` nodes, so an import nested inside
a function body, or written as ``from tests.unit import test_foo``, is caught
identically to a module-level ``from tests.unit.test_foo import bar``. Because
nothing here is regex-based there is no near-miss/"would-be-missed" variant to
pin — ``test_ast_catches_forms_a_line_anchored_regex_would_miss`` shows the
shapes a line-anchored grep of the same rule drops and this detector does not.

**Why the allowlisted rows are not new violations.** The allowlist below may only
ever SHRINK; it starts at the four instances that existed when the guard was
written, and they are not one kind of thing:

* ``test_architecture_ci_suite_coverage.py`` -> ``tests.smoke.test_smoke_basic``
  is genuinely NOT the disease and is expected to stay forever. That guard's
  SUBJECT UNDER TEST is the smoke suite's no-skipped-tests test class; importing
  it is how the guard reaches the thing it grades. Moving ``TestNoSkippedTests``
  into a helper to satisfy a blanket rule would defeat the guard it feeds.
* The other three ARE the disease, in domains (capabilities, wrapper-field
  descriptions, delivery webhooks) unrelated to the change that introduced this
  guard, and are deferred rather than fixed inside an unrelated PR. Each is
  tagged ``DEFERRED`` below. There is no GitHub issue for them yet, so no
  ``# FIXME(#<gh>)`` marker is written — the project convention is that code
  comments cite GitHub issue numbers and never local tracker ids, and inventing
  an id that resolves for nobody is worse than naming the work here. The fix in
  each case is the same shape: move the shared symbol into
  ``tests/unit/_architecture_helpers.py`` (for the two guard modules) or a
  suite-local helper module, and repoint both sides.

Reproducible scan (what this guard automates):

    grep -rn "^\\s*from tests\\.[a-z_.]*\\.test_[a-z0-9_]* import" tests/ --include="*.py"
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    iter_module_trees,
)

_TESTS_ROOT = REPO_ROOT / "tests"
_TESTS_PACKAGE = "tests"

#: ``(importer repo-relative path, imported test module)``. MAY ONLY SHRINK.
#: Line numbers are deliberately not part of the key — an unrelated edit above
#: the import must not turn a real entry stale.
_ALLOWLIST: set[tuple[str, str]] = {
    # NOT the disease, and permanent: the imported test class IS this guard's
    # subject under test. See the module docstring.
    (
        "tests/unit/test_architecture_ci_suite_coverage.py",
        "tests.smoke.test_smoke_basic",
    ),
    # DEFERRED — same disease, unrelated domain (capabilities).
    (
        "tests/unit/test_version_negotiation.py",
        "tests.unit.test_get_adcp_capabilities",
    ),
    # DEFERRED — same disease, unrelated domain (delivery webhooks).
    (
        "tests/integration/test_delivery_webhooks_force.py",
        "tests.integration.test_delivery_webhooks_integration",
    ),
}


def _names_a_test_module(dotted: str) -> bool:
    """True when any component of *dotted* is a ``test_*`` module name.

    ``tests`` itself is excluded by the ``test_`` prefix (no underscore), so the
    package root can never match.
    """
    return any(part.startswith("test_") for part in dotted.split("."))


def _package_of(rel_path: str) -> tuple[str, ...]:
    """Dotted package parts a relative import inside *rel_path* resolves against.

    For ``tests/unit/test_x.py`` that is ``tests.unit``; for ``tests/fixtures/__init__.py``
    it is ``tests.fixtures`` — in both cases the parent directory of the file.
    """
    return Path(rel_path).parts[:-1]


def _resolve_relative(package: tuple[str, ...], level: int, module: str | None) -> str:
    """Absolute dotted path for a ``from . import`` / ``from ..x import`` node."""
    base = list(package[: len(package) - (level - 1)])
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _is_package_dir(dotted: str) -> bool:
    """True when *dotted* resolves to a DIRECTORY in the repo.

    This is what separates ``from tests.unit import test_foo`` (``tests/unit`` is a
    directory, so ``test_foo`` names a MODULE — the disease) from
    ``from tests.helpers.envelope_assertions import test_double`` (the base is a
    single module file, so ``test_double`` is a SYMBOL — not the disease, and
    flagging it would be a false positive).
    """
    return (REPO_ROOT / Path(*dotted.split("."))).is_dir()


def _import_from_targets(node: ast.ImportFrom, package: tuple[str, ...]) -> list[str]:
    """Dotted modules an ``ImportFrom`` reaches, covering both shapes.

    ``from tests.unit.test_foo import bar`` -> the module itself.
    ``from tests.unit import test_foo``     -> the module named by the alias.
    """
    module = node.module or ""
    absolute = _resolve_relative(package, node.level, node.module) if node.level else module
    if absolute.split(".")[:1] != [_TESTS_PACKAGE]:
        return []
    targets = [absolute]
    if _is_package_dir(absolute):
        targets.extend(f"{absolute}.{alias.name}" for alias in node.names if alias.name.startswith("test_"))
    return targets


def _find_test_module_imports(
    tree: ast.AST,
    *,
    package: tuple[str, ...] = (_TESTS_PACKAGE,),
) -> list[tuple[int, str]]:
    """``(lineno, imported module)`` for every import of a ``tests.*.test_*`` module."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for target in _import_from_targets(node, package):
                if _names_a_test_module(target):
                    found.append((node.lineno, target))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{_TESTS_PACKAGE}.") and _names_a_test_module(alias.name):
                    found.append((node.lineno, alias.name))
    return found


def _violation_linenos(tree: ast.AST) -> list[int]:
    """Detector shape ``assert_detector_catches_ast_snippets`` expects."""
    return [lineno for lineno, _ in _find_test_module_imports(tree)]


def _scan_tests_tree() -> set[tuple[str, str]]:
    """``(importer, imported module)`` pairs across every module under ``tests/``."""
    found: set[tuple[str, str]] = set()
    for tree, rel_path in iter_module_trees([_TESTS_ROOT]):
        package = _package_of(rel_path)
        for _lineno, target in _find_test_module_imports(tree, package=package):
            found.add((rel_path, target))
    return found


@pytest.mark.arch_guard
def test_no_test_module_imports_from_a_sibling_test_module() -> None:
    """A ``test_*.py`` exporter pins an unrelated suite to its own file name."""
    assert_violations_match_allowlist(
        _scan_tests_tree(),
        _ALLOWLIST,
        fix_hint=(
            "A module whose job is to BE a test must not also be a helper library. "
            "Move the shared symbol into tests/helpers/**, tests/factories/**, a "
            "suite-local _*_helpers.py, or tests/unit/_architecture_helpers.py for "
            "guard helpers — then repoint both sides. The allowlist may only shrink."
        ),
    )


@pytest.mark.arch_guard
def test_scan_is_not_vacuous() -> None:
    """Non-vacuity: the scanner must actually reach modules under ``tests/``.

    An empty scan would make the allowlist assertion above fail loudly (four
    stale entries), but this states the reason directly rather than leaving a
    future reader to infer it from a confusing stale-entry message.
    """
    trees = list(iter_module_trees([_TESTS_ROOT]))
    assert len(trees) > 100, f"expected the tests/ tree to be scanned, parsed only {len(trees)} modules"


@pytest.mark.arch_guard
def test_detector_catches_known_bad_import_shapes() -> None:
    """Positive meta-test: every violating shape is flagged."""
    assert_detector_catches_ast_snippets(
        _violation_linenos,
        snippets={
            "from-test-module": "from tests.unit.test_foo import shared_helper\n",
            "from-package-importing-test-module": "from tests.unit import test_foo\n",
            "plain-import": "import tests.integration.test_bar\n",
            "aliased-import": "import tests.integration.test_bar as bar\n",
            "nested-inside-function": (
                "def test_thing():\n    from tests.unit.test_foo import shared_helper\n\n    assert shared_helper\n"
            ),
            "deep-package": "from tests.bdd.steps.test_helper_module import step\n",
        },
    )


@pytest.mark.arch_guard
def test_detector_catches_relative_import_of_a_test_module() -> None:
    """Positive meta-test: ``from .test_foo import x`` resolves and is flagged."""
    detector = functools.partial(_find_test_module_imports, package=("tests", "unit"))
    tree = ast.parse("from .test_foo import shared_helper\n")
    assert detector(tree) == [(1, "tests.unit.test_foo")]


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("helpers-package", "from tests.helpers.signing import provision_key\n"),
        ("factories-package", "from tests.factories import TenantFactory\n"),
        ("bdd-steps", "from tests.bdd.steps.generic._dispatch import dispatch_request\n"),
        ("conftest", "from tests.conftest import integration_db\n"),
        ("suite-local-helpers", "from tests.unit._architecture_helpers import REPO_ROOT\n"),
        ("relative-suite-local-helper", "from ._thread_registry_helpers import dead_thread\n"),
        ("production-code", "from src.core.signing.canonical import canonical_target_uri\n"),
        ("stdlib", "import ast\n"),
        ("test-prefixed-symbol-not-module", "from tests.helpers.envelope_assertions import test_double\n"),
    ],
)
def test_detector_passes_clean_import_shapes(label: str, source: str) -> None:
    """Negative meta-test: modules that exist to be imported are never flagged."""
    package = ("tests", "unit")
    assert _find_test_module_imports(ast.parse(source), package=package) == [], label


@pytest.mark.arch_guard
def test_ast_catches_forms_a_line_anchored_regex_would_miss() -> None:
    """The AST detector has no near-miss blind spot the recorded grep has.

    Nothing in this guard is regex-based, so there is no "would-be-missed"
    regex variant to pin. This test states the payoff of that choice: the
    recorded scan command is a line-anchored ``from tests.<pkg>.test_<mod> import``
    grep, and each shape below slips past it while naming the same disease.
    """
    regex_blind_shapes = {
        "from-package-importing-test-module": "from tests.unit import test_foo\n",
        "plain-import": "import tests.unit.test_foo\n",
        "line-continuation": "from tests.unit.\\\n    test_foo import shared_helper\n",
        "relative": "from .test_foo import shared_helper\n",
    }
    missed = [
        label
        for label, source in regex_blind_shapes.items()
        if not _find_test_module_imports(ast.parse(source), package=("tests", "unit"))
    ]
    assert missed == [], f"AST detector missed regex-blind shape(s): {missed}"
