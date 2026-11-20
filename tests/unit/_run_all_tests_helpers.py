"""Shared anchors for the ``run_all_tests.sh`` contract/behavior tests.

``test_run_all_tests_contract.py`` (argument-resolution contract, suite-list
single-sourcing) and ``test_run_all_tests_parallel_tox.py`` (the default run
must hand ``tox -p`` to the tests container) both need to locate the runner
script on disk. That anchor used to live in the contract module and be imported
out of it, which made a module whose job is to BE a test double as a helper
library — the disease ``test_architecture_no_cross_test_module_imports.py``
forbids: a rename or split of the contract test would break an unrelated suite.

The leading underscore keeps pytest from collecting this as a test module.
"""

from __future__ import annotations

from pathlib import Path

#: Project root, computed once from this module's path.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The in-network test runner both modules exercise.
RUNNER = REPO_ROOT / "run_all_tests.sh"
