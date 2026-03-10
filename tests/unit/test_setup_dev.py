"""Unit tests for scripts/setup-dev.py.

Tests pure functions only — no Docker, no network, no filesystem side effects.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# The script is not in a package, so we import via importlib.
# We must register the module in sys.modules before exec_module
# because dataclasses inspects sys.modules during class creation.
_script_path = str(Path(__file__).resolve().parent.parent.parent / "scripts" / "setup-dev.py")
_spec = importlib.util.spec_from_file_location("setup_dev", _script_path)
assert _spec and _spec.loader
setup_dev = importlib.util.module_from_spec(_spec)
sys.modules["setup_dev"] = setup_dev
_spec.loader.exec_module(setup_dev)


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------


class TestParseVersion:
    def test_python_version(self):
        assert setup_dev.parse_version("Python 3.12.4", r"Python (?P<version>\d+\.\d+)") == (3, 12)

    def test_python_version_patch(self):
        assert setup_dev.parse_version("Python 3.12.4", r"Python (?P<version>\d+\.\d+\.\d+)") == (3, 12, 4)

    def test_no_match(self):
        assert setup_dev.parse_version("no version here", r"Python (?P<version>\d+\.\d+)") is None

    def test_unnamed_group(self):
        assert setup_dev.parse_version("v2.5.1", r"v(\d+\.\d+\.\d+)") == (2, 5, 1)


# ---------------------------------------------------------------------------
# check_version_meets_minimum
# ---------------------------------------------------------------------------


class TestCheckVersionMeetsMinimum:
    def test_equal(self):
        assert setup_dev.check_version_meets_minimum((3, 12), (3, 12)) is True

    def test_greater_major(self):
        assert setup_dev.check_version_meets_minimum((4, 0), (3, 12)) is True

    def test_greater_minor(self):
        assert setup_dev.check_version_meets_minimum((3, 13), (3, 12)) is True

    def test_less_than(self):
        assert setup_dev.check_version_meets_minimum((3, 11), (3, 12)) is False

    def test_less_major(self):
        assert setup_dev.check_version_meets_minimum((2, 99), (3, 0)) is False


# ---------------------------------------------------------------------------
# load_env_file
# ---------------------------------------------------------------------------


class TestLoadEnvFile:
    def test_nonexistent_file(self, tmp_path: Path):
        assert setup_dev.load_env_file(tmp_path / "missing.env") == {}

    def test_basic_parsing(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=qux\n")
        result = setup_dev.load_env_file(env_file)
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_skips_comments_and_blanks(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nKEY=value\n\n# another\n")
        result = setup_dev.load_env_file(env_file)
        assert result == {"KEY": "value"}

    def test_preserves_equals_in_value(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("URL=postgres://user:pass@host:5432/db?sslmode=disable\n")
        result = setup_dev.load_env_file(env_file)
        assert result == {"URL": "postgres://user:pass@host:5432/db?sslmode=disable"}

    def test_skips_lines_without_equals(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("VALID=yes\nINVALID_LINE\n")
        result = setup_dev.load_env_file(env_file)
        assert result == {"VALID": "yes"}

    def test_strips_double_quotes(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text('FOO="bar"\n')
        result = setup_dev.load_env_file(env_file)
        assert result == {"FOO": "bar"}

    def test_strips_single_quotes(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO='bar'\n")
        result = setup_dev.load_env_file(env_file)
        assert result == {"FOO": "bar"}

    def test_preserves_mismatched_quotes(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=\"bar'\n")
        result = setup_dev.load_env_file(env_file)
        assert result == {"FOO": "\"bar'"}

    def test_preserves_inner_quotes(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text('FOO=bar"baz\n')
        result = setup_dev.load_env_file(env_file)
        assert result == {"FOO": 'bar"baz'}


# ---------------------------------------------------------------------------
# merge_env
# ---------------------------------------------------------------------------


class TestMergeEnv:
    def test_existing_wins(self):
        result = setup_dev.merge_env({"A": "1"}, {"A": "2", "B": "3"})
        assert result == {"A": "1", "B": "3"}

    def test_empty_existing(self):
        result = setup_dev.merge_env({}, {"A": "1"})
        assert result == {"A": "1"}

    def test_empty_defaults(self):
        result = setup_dev.merge_env({"A": "1"}, {})
        assert result == {"A": "1"}


# ---------------------------------------------------------------------------
# generate_secret_key
# ---------------------------------------------------------------------------


class TestGenerateSecretKey:
    def test_length(self):
        key = setup_dev.generate_secret_key(16)
        assert len(key) == 32  # hex encoding doubles length

    def test_uniqueness(self):
        a = setup_dev.generate_secret_key()
        b = setup_dev.generate_secret_key()
        assert a != b


# ---------------------------------------------------------------------------
# ensure_env_secrets
# ---------------------------------------------------------------------------


class TestEnsureEnvSecrets:
    def test_generates_missing_flask_secret(self):
        result = setup_dev.ensure_env_secrets({})
        assert "FLASK_SECRET_KEY" in result
        assert len(result["FLASK_SECRET_KEY"]) > 0

    def test_preserves_existing_secret(self):
        result = setup_dev.ensure_env_secrets({"FLASK_SECRET_KEY": "my-secret"})
        assert result["FLASK_SECRET_KEY"] == "my-secret"

    def test_does_not_mutate_input(self):
        original = {"OTHER": "value"}
        setup_dev.ensure_env_secrets(original)
        assert "FLASK_SECRET_KEY" not in original

    def test_generates_missing_encryption_key(self):
        """ENCRYPTION_KEY is required by src/core/utils/encryption.py.
        Without it, any code path using encrypt/decrypt raises ValueError."""
        result = setup_dev.ensure_env_secrets({})
        assert "ENCRYPTION_KEY" in result
        assert len(result["ENCRYPTION_KEY"]) > 0

    def test_preserves_existing_encryption_key(self):
        result = setup_dev.ensure_env_secrets({"ENCRYPTION_KEY": "existing-key"})
        assert result["ENCRYPTION_KEY"] == "existing-key"

    def test_generates_valid_fernet_encryption_key(self):
        """Generated ENCRYPTION_KEY must be a valid Fernet key."""
        from cryptography.fernet import Fernet

        result = setup_dev.ensure_env_secrets({})
        # Should not raise — key must be valid base64 Fernet key
        Fernet(result["ENCRYPTION_KEY"].encode())


# ---------------------------------------------------------------------------
# serialize_env
# ---------------------------------------------------------------------------


class TestSerializeEnv:
    def test_sorted_output(self):
        result = setup_dev.serialize_env({"Z": "1", "A": "2"})
        assert result == "A=2\nZ=1\n"

    def test_empty_dict(self):
        assert setup_dev.serialize_env({}) == "\n"


# ---------------------------------------------------------------------------
# build_env_from_template
# ---------------------------------------------------------------------------


class TestBuildEnvFromTemplate:
    def test_nonexistent_template(self, tmp_path: Path):
        assert setup_dev.build_env_from_template(tmp_path / "nope") == {}

    def test_extracts_uncommented_values(self, tmp_path: Path):
        template = tmp_path / ".env.template"
        template.write_text("# Comment line\n# KEY=commented\nACTIVE=yes\n")
        result = setup_dev.build_env_from_template(template)
        assert result == {"ACTIVE": "yes"}


# ---------------------------------------------------------------------------
# get_conductor_port
# ---------------------------------------------------------------------------


class TestGetConductorPort:
    def test_default(self):
        assert setup_dev.get_conductor_port({}) == 8000

    def test_custom_port(self):
        assert setup_dev.get_conductor_port({"CONDUCTOR_PORT": "9000"}) == 9000

    def test_invalid_port_falls_back(self):
        assert setup_dev.get_conductor_port({"CONDUCTOR_PORT": "abc"}) == 8000


# ---------------------------------------------------------------------------
# StepResult and SetupReport
# ---------------------------------------------------------------------------


class TestSetupReport:
    def test_empty_report_is_success(self):
        assert setup_dev.SetupReport().success is True

    def test_all_ok(self):
        report = setup_dev.SetupReport()
        report.add(setup_dev.StepResult(name="a", ok=True, message="ok"))
        report.add(setup_dev.StepResult(name="b", ok=True, message="ok"))
        assert report.success is True

    def test_one_failure(self):
        report = setup_dev.SetupReport()
        report.add(setup_dev.StepResult(name="a", ok=True, message="ok"))
        report.add(setup_dev.StepResult(name="b", ok=False, message="fail"))
        assert report.success is False


# ---------------------------------------------------------------------------
# assert_prerequisites (with mocked subprocess)
# ---------------------------------------------------------------------------


class TestAssertPrerequisites:
    def test_all_present(self):
        """When all commands succeed, prerequisites pass."""
        with patch.object(setup_dev, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Python 3.12.4", stderr="")
            result = setup_dev.assert_prerequisites()
        assert result.ok is True

    def test_missing_tool(self):
        """When a command is not found, prerequisites fail with hint."""
        with patch.object(setup_dev, "_run", side_effect=FileNotFoundError):
            result = setup_dev.assert_prerequisites()
        assert result.ok is False
        assert "not installed" in result.message

    def test_version_too_low(self):
        """When Python version is below minimum, prerequisites fail."""
        with patch.object(setup_dev, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Python 3.11.0", stderr="")
            result = setup_dev.assert_prerequisites()
        assert result.ok is False
        assert "3.12" in result.message


# ---------------------------------------------------------------------------
# ensure_env (with tmp_path)
# ---------------------------------------------------------------------------


class TestEnsureEnv:
    def test_creates_env_from_template(self, tmp_path: Path):
        template = tmp_path / ".env.template"
        template.write_text("CREATE_DEMO_TENANT=true\n")
        env_path = tmp_path / ".env"

        with (
            patch.object(setup_dev, "ENV_FILE", env_path),
            patch.object(setup_dev, "ENV_TEMPLATE", template),
        ):
            result = setup_dev.ensure_env()

        assert result.ok is True
        assert "Created" in result.message
        content = env_path.read_text()
        assert "CREATE_DEMO_TENANT=true" in content
        assert "FLASK_SECRET_KEY=" in content

    def test_preserves_existing_values(self, tmp_path: Path):
        template = tmp_path / ".env.template"
        template.write_text("KEY=default\n")
        env_path = tmp_path / ".env"
        env_path.write_text("KEY=custom\n")

        with (
            patch.object(setup_dev, "ENV_FILE", env_path),
            patch.object(setup_dev, "ENV_TEMPLATE", template),
        ):
            result = setup_dev.ensure_env()

        assert result.ok is True
        content = setup_dev.load_env_file(env_path)
        assert content["KEY"] == "custom"

    def test_idempotent_when_no_changes(self, tmp_path: Path):
        template = tmp_path / ".env.template"
        template.write_text("")
        env_path = tmp_path / ".env"
        env_path.write_text("ENCRYPTION_KEY=existing-key\nFLASK_SECRET_KEY=existing\n")

        with (
            patch.object(setup_dev, "ENV_FILE", env_path),
            patch.object(setup_dev, "ENV_TEMPLATE", template),
        ):
            result = setup_dev.ensure_env()

        assert result.ok is True
        assert result.skipped is True


# ---------------------------------------------------------------------------
# ensure_pre_commit
# ---------------------------------------------------------------------------


class TestEnsurePreCommit:
    def test_skips_when_already_installed(self, tmp_path: Path):
        hook_dir = tmp_path / ".git" / "hooks"
        hook_dir.mkdir(parents=True)
        (hook_dir / "pre-commit").write_text("#!/bin/sh\n")

        with patch.object(setup_dev, "ROOT_DIR", tmp_path):
            result = setup_dev.ensure_pre_commit()

        assert result.ok is True
        assert result.skipped is True

    def test_installs_hooks(self, tmp_path: Path):
        (tmp_path / ".git" / "hooks").mkdir(parents=True)

        with (
            patch.object(setup_dev, "ROOT_DIR", tmp_path),
            patch.object(setup_dev, "_run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = setup_dev.ensure_pre_commit()

        assert result.ok is True
        assert not result.skipped
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# ensure_tox
# ---------------------------------------------------------------------------


class TestEnsureTox:
    def test_found(self):
        with patch("shutil.which", return_value="/usr/local/bin/tox"):
            result = setup_dev.ensure_tox()
        assert result.ok is True
        assert "available" in result.message

    def test_not_found(self):
        with patch("shutil.which", return_value=None):
            result = setup_dev.ensure_tox()
        assert result.ok is True  # not a hard failure
        assert "not found" in result.message
