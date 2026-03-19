"""Tests for the BDD step assertion completeness inspector.

Validates that the AST extraction correctly finds step functions,
extracts their step text, and identifies the step type (given/when/then).
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path

import pytest

# The script lives in the pi-agentic-coding plugin (extracted from this repo)
_PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".claude" / "scripts" / "inspect_bdd_steps.py"
_PLUGIN_INSTALLED_PATH = (
    Path.home()
    / "projects"
    / "pi-agentic-coding"
    / "plugins"
    / "qa-bdd"
    / "skills"
    / "inspect-steps"
    / "scripts"
    / "inspect_bdd_steps.py"
)
SCRIPT_PATH = _PLUGIN_PATH if _PLUGIN_PATH.exists() else _PLUGIN_INSTALLED_PATH


@pytest.fixture(autouse=True)
def _load_inspect_module():
    """Dynamically import the inspection script as a module."""
    if not SCRIPT_PATH.exists():
        pytest.skip("inspect_bdd_steps.py not available (lives in pi-agentic-coding plugin)")
    spec = importlib.util.spec_from_file_location("inspect_bdd_steps", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["inspect_bdd_steps"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    yield
    sys.modules.pop("inspect_bdd_steps", None)


class TestBddStepExtraction:
    """Test the AST-based BDD step function extraction."""

    def test_extracts_plain_string_then_step(self, tmp_path: Path) -> None:
        """Plain @then("text") decorator should be extracted."""
        source = textwrap.dedent("""
            from pytest_bdd import then

            @then("the operation should fail")
            def then_operation_fails(ctx: dict) -> None:
                assert "error" in ctx
        """)
        (tmp_path / "steps.py").write_text(source)

        from inspect_bdd_steps import extract_bdd_steps

        steps = extract_bdd_steps(tmp_path)
        assert len(steps) == 1
        assert steps[0].step_type == "then"
        assert steps[0].step_text == "the operation should fail"
        assert steps[0].function_name == "then_operation_fails"

    def test_extracts_parsers_parse_then_step(self, tmp_path: Path) -> None:
        """@then(parsers.parse('text with {param}')) should be extracted."""
        source = textwrap.dedent("""
            from pytest_bdd import parsers, then

            @then(parsers.parse('the error code should be "{code}"'))
            def then_error_code(ctx: dict, code: str) -> None:
                assert ctx.get("error_code") == code
        """)
        (tmp_path / "steps.py").write_text(source)

        from inspect_bdd_steps import extract_bdd_steps

        steps = extract_bdd_steps(tmp_path)
        assert len(steps) == 1
        assert steps[0].step_text == 'the error code should be "{code}"'

    def test_extracts_given_and_when_steps(self, tmp_path: Path) -> None:
        """@given and @when decorators should also be extracted."""
        source = textwrap.dedent("""
            from pytest_bdd import given, when

            @given("a Seller Agent is operational")
            def given_seller(ctx: dict) -> None:
                ctx["seller"] = True

            @when("the Buyer Agent requests formats")
            def when_request(ctx: dict) -> None:
                pass
        """)
        (tmp_path / "steps.py").write_text(source)

        from inspect_bdd_steps import extract_bdd_steps

        steps = extract_bdd_steps(tmp_path)
        types = {s.step_type for s in steps}
        assert types == {"given", "when"}

    def test_extracts_source_text_with_body(self, tmp_path: Path) -> None:
        """Extracted source should include the full function body."""
        source = textwrap.dedent('''
            from pytest_bdd import then

            @then("no error should be raised")
            def then_no_error(ctx: dict) -> None:
                """Assert no error."""
                assert "error" not in ctx
        ''')
        (tmp_path / "steps.py").write_text(source)

        from inspect_bdd_steps import extract_bdd_steps

        steps = extract_bdd_steps(tmp_path)
        assert len(steps) == 1
        assert "assert" in steps[0].source_text
        assert "then_no_error" in steps[0].source_text

    def test_scans_real_bdd_steps_directory(self) -> None:
        """Smoke test: can extract steps from the actual BDD step files."""
        from inspect_bdd_steps import extract_bdd_steps

        steps_dir = Path("tests/bdd/steps")
        if not steps_dir.exists():
            pytest.skip("BDD steps directory not found")

        steps = extract_bdd_steps(steps_dir)
        # We know there are 50+ Then steps across the files
        then_steps = [s for s in steps if s.step_type == "then"]
        assert len(then_steps) >= 40, f"Expected 40+ Then steps, found {len(then_steps)}"

    def test_handles_multiple_files(self, tmp_path: Path) -> None:
        """Should find steps across multiple .py files in subdirectories."""
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.py").write_text(
            textwrap.dedent("""
            from pytest_bdd import then
            @then("step A")
            def step_a(ctx): pass
        """)
        )
        (tmp_path / "sub" / "b.py").write_text(
            textwrap.dedent("""
            from pytest_bdd import given
            @given("step B")
            def step_b(ctx): pass
        """)
        )

        from inspect_bdd_steps import extract_bdd_steps

        steps = extract_bdd_steps(tmp_path)
        names = {s.function_name for s in steps}
        assert names == {"step_a", "step_b"}

    def test_ignores_non_step_functions(self, tmp_path: Path) -> None:
        """Helper functions without step decorators should be skipped."""
        source = textwrap.dedent("""
            from pytest_bdd import then

            def _helper(ctx):
                return ctx.get("data")

            @then("check data")
            def then_check(ctx):
                assert _helper(ctx) is not None
        """)
        (tmp_path / "steps.py").write_text(source)

        from inspect_bdd_steps import extract_bdd_steps

        steps = extract_bdd_steps(tmp_path)
        assert len(steps) == 1
        assert steps[0].function_name == "then_check"

    def test_records_file_path_and_line_number(self, tmp_path: Path) -> None:
        """Each step should have accurate file path and line number."""
        source = textwrap.dedent("""
            from pytest_bdd import then

            @then("first step")
            def first(ctx): pass

            @then("second step")
            def second(ctx): pass
        """)
        (tmp_path / "steps.py").write_text(source)

        from inspect_bdd_steps import extract_bdd_steps

        steps = extract_bdd_steps(tmp_path)
        assert len(steps) == 2
        assert all(s.file_path.endswith("steps.py") for s in steps)
        # Second function should be on a later line
        assert steps[1].line_number > steps[0].line_number
