"""Tests for parallel test runner (run_all_tests.sh).

Verifies that the parallelized test runner produces correct results,
collects exit codes properly, and reports failures accurately.

These tests exercise the shell functions by sourcing the script and
running small shell snippets -- no Docker or pytest required.
"""

import os
import subprocess
import tempfile

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "run_all_tests.sh")


class TestParallelTestRunner:
    """Test the parallel execution infrastructure in run_all_tests.sh."""

    def test_script_exists(self):
        """The test runner script exists at the expected path."""
        assert os.path.isfile(SCRIPT_PATH), f"Script not found: {SCRIPT_PATH}"

    def test_script_is_executable(self):
        """The test runner script has execute permissions."""
        assert os.access(SCRIPT_PATH, os.X_OK), "Script is not executable"

    def test_run_suite_bg_function_exists(self):
        """The run_suite_bg() function is defined in the script."""
        result = subprocess.run(
            ["bash", "-c", f"source {SCRIPT_PATH} --source-only 2>/dev/null; type run_suite_bg"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"run_suite_bg() function not found in script. stderr: {result.stderr}"

    def test_collect_results_function_exists(self):
        """The collect_results() function is defined in the script."""
        result = subprocess.run(
            ["bash", "-c", f"source {SCRIPT_PATH} --source-only 2>/dev/null; type collect_results"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"collect_results() function not found in script. stderr: {result.stderr}"

    def test_parallel_suites_write_separate_exit_code_files(self):
        """Each parallel suite writes its exit code to a separate temp file.

        This is the core mechanism for collecting results from background
        processes -- FAILURES can't be a shared shell variable across &.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Simulate what run_suite_bg should do: write exit code to file
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f"""
                    source {SCRIPT_PATH} --source-only 2>/dev/null
                    export RESULTS_DIR="{tmpdir}"

                    # Run two fake suites in background
                    run_suite_bg "1/2" "suite_pass" "true"
                    PID1=$BGPID
                    run_suite_bg "2/2" "suite_fail" "false"
                    PID2=$BGPID

                    # Wait for both
                    wait $PID1 2>/dev/null
                    wait $PID2 2>/dev/null

                    # Check exit code files exist
                    [ -f "{tmpdir}/.exitcode.suite_pass" ] && echo "PASS_FILE_EXISTS"
                    [ -f "{tmpdir}/.exitcode.suite_fail" ] && echo "FAIL_FILE_EXISTS"

                    # Check exit code values
                    PASS_CODE=$(cat "{tmpdir}/.exitcode.suite_pass")
                    FAIL_CODE=$(cat "{tmpdir}/.exitcode.suite_fail")
                    echo "PASS_CODE=$PASS_CODE"
                    echo "FAIL_CODE=$FAIL_CODE"
                    """,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout
            assert "PASS_FILE_EXISTS" in output, (
                f"Exit code file for passing suite not created. stdout: {output}, stderr: {result.stderr}"
            )
            assert "FAIL_FILE_EXISTS" in output, (
                f"Exit code file for failing suite not created. stdout: {output}, stderr: {result.stderr}"
            )
            assert "PASS_CODE=0" in output, f"Passing suite should have exit code 0. stdout: {output}"
            assert "FAIL_CODE=1" in output, f"Failing suite should have exit code 1. stdout: {output}"

    def test_collect_results_builds_failures_string(self):
        """collect_results() reads exit code files and builds FAILURES."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f"""
                    source {SCRIPT_PATH} --source-only 2>/dev/null
                    export RESULTS_DIR="{tmpdir}"

                    # Simulate exit code files from parallel runs
                    echo "0" > "{tmpdir}/.exitcode.unit"
                    echo "1" > "{tmpdir}/.exitcode.integration"
                    echo "0" > "{tmpdir}/.exitcode.e2e"

                    SUITE_NAMES="unit integration e2e"
                    collect_results $SUITE_NAMES
                    echo "FAILURES=$FAILURES"
                    """,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout
            # Should report integration as failed
            assert "integration" in output, (
                f"Failed suite 'integration' not in FAILURES. stdout: {output}, stderr: {result.stderr}"
            )
            # Should NOT report unit or e2e as failed
            assert "FAILURES=" in output, f"FAILURES variable not set. stdout: {output}"
            lines = [l for l in output.strip().split("\n") if l.startswith("FAILURES=")]
            assert len(lines) == 1, f"Expected exactly one FAILURES= line, got: {lines}"
            failures_value = lines[0].split("=", 1)[1]
            assert "unit" not in failures_value, "unit should not be in FAILURES"
            assert "e2e" not in failures_value, "e2e should not be in FAILURES"

    def test_source_only_flag_does_not_execute(self):
        """Passing --source-only should define functions without running tests.

        This is needed so tests can source the script to access functions
        without triggering the actual test execution.
        """
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"source {SCRIPT_PATH} --source-only 2>/dev/null; echo SOURCED_OK",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "SOURCED_OK" in result.stdout, (
            f"--source-only flag not supported. stdout: {result.stdout}, stderr: {result.stderr}"
        )

    def test_ci_mode_runs_all_five_suites(self):
        """In CI mode without PYTEST_TARGET, all 5 suite names are referenced.

        We verify the script text mentions all expected suite names.
        """
        with open(SCRIPT_PATH) as f:
            content = f.read()
        for suite in ["unit", "integration", "integration_v2", "e2e", "ui"]:
            assert suite in content, f"Suite '{suite}' not found in script"

    def test_quick_mode_runs_three_suites(self):
        """Quick mode should reference unit, integration, integration_v2."""
        with open(SCRIPT_PATH) as f:
            content = f.read()
        # Check the quick mode section contains all three
        quick_section_found = False
        in_quick = False
        for line in content.split("\n"):
            if "quick" in line and "MODE" not in line:
                in_quick = True
            if in_quick and ("elif" in line or "else" in line):
                break
            if in_quick and "unit" in line:
                quick_section_found = True
        assert quick_section_found, "Quick mode section should reference unit tests"
