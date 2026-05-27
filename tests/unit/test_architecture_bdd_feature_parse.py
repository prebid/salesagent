"""Guard: All Gherkin feature files must parse without errors.

Catches malformed scenarios (e.g., starting with ``And`` instead of
``Given``) that silently prevent entire test files from being collected.
Without this guard, broken feature files hide test gaps -- pytest-bdd
raises a collection error, but the rest of the suite keeps running,
giving false confidence.

Runs on every ``make quality`` to prevent regressions.
"""

from __future__ import annotations

from pathlib import Path

FEATURES_DIR = Path("tests/bdd/features")


class TestGherkinFeaturesParse:
    """Every .feature file must be parseable by pytest-bdd."""

    def test_all_feature_files_parse(self) -> None:
        """Parse every feature file and collect errors."""
        from pytest_bdd.feature import get_feature

        feature_files = sorted(FEATURES_DIR.glob("*.feature"))
        assert feature_files, f"No .feature files found in {FEATURES_DIR}"

        errors: list[str] = []
        for path in feature_files:
            try:
                get_feature(str(FEATURES_DIR), path.name)
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")

        assert not errors, f"{len(errors)} feature file(s) failed to parse:\n" + "\n".join(f"  {e}" for e in errors)
