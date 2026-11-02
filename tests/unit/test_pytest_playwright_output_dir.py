"""pytest-playwright must not be pointed at test-results/.

The plugin's ``--output`` option defaults to ``test-results`` and, at the start of
EVERY session -- UI suite or not -- it ``shutil.rmtree()``s that directory
(pytest_playwright.py, the ``_pw_artifacts_folder`` fixture). ``test-results/`` is
where run_all_tests.sh and the CI-box runner write the per-run JSON reports, which are
the baselines every failing-nodeid membership diff is made against. With the default in
force, a plain ``pytest tests/unit/<one file>`` in the dev venv deleted every recorded
run on the machine, and the loss was only ever noticed when a baseline named in a goal
turned out not to exist any more.

tests/conftest.py redirects the option to ``.playwright-artifacts`` in
``pytest_configure`` -- there and not in pytest.ini addopts, because the tox envs do not
install the plugin and an addopts ``--output`` is an unrecognized argument for them.
This reads the option the plugin itself reads, so the regression is a failing unit test
rather than a vanished directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import PLAYWRIGHT_ARTIFACTS_DIR, PLAYWRIGHT_DEFAULT_OUTPUT


def test_playwright_output_dir_is_not_the_reports_dir(pytestconfig: pytest.Config) -> None:
    if not pytestconfig.pluginmanager.hasplugin("playwright"):
        # No plugin, no --output option, and no directory wipe to guard against: the
        # tox envs run this way. Nothing to grade here, and nothing at risk.
        return
    output = Path(pytestconfig.getoption("--output"))
    reports = Path(PLAYWRIGHT_DEFAULT_OUTPUT)
    assert output != reports and reports not in output.parents, (
        f"pytest-playwright --output resolves to {output}, which is (or lies under) "
        f"{reports}/ -- the plugin rmtree()s its output directory at every session start, "
        f"so this setting would delete every recorded run. tests/conftest.py's "
        f"pytest_configure should have redirected it to {PLAYWRIGHT_ARTIFACTS_DIR}."
    )
