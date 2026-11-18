"""``verify_feature_error_codes.py`` must exit 2 — not 1 — when its instrument fails.

The script uses exit 1 for "non-canonical error codes found" and that code gates
``make quality``. So an *instrument* failure (the pinned enum cannot be loaded at
all) must not fall through to an uncaught traceback, which also exits 1 and would
read as "findings exist" — an empty worklist reported as a real result.

``load_enum()`` now reads ``adcp.ErrorCode`` directly, so the only way the
instrument can fail is the SDK not being importable at all. This module pins
that single contract plus the happy path, without which every exit-2 assertion
would still pass against a ``load_enum()`` gutted to raise unconditionally.

GH #1868
"""

from __future__ import annotations

import builtins
import importlib.util
import sys

import pytest

from tests.unit._architecture_helpers import REPO_ROOT

_SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_feature_error_codes.py"


def _load_script_module():
    """Import the script by path — scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location("_verify_feature_error_codes", _SCRIPT_PATH)
    assert spec and spec.loader, f"cannot load {_SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_script = _load_script_module()


def test_missing_sdk_exits_2(monkeypatch, capsys):
    """An unimportable dependency produces the diagnostic exit code, never the findings one.

    The gate now resolves canonicality through CODE_TABLE rather than adcp.ErrorCode
    (#1753: emittability, not spec membership), so this blocks the module
    load_enum actually performs. Patching `adcp` alone made the test ORDER-DEPENDENT:
    once any earlier test had imported src.core.errors.codes, it sat in sys.modules and
    the import never re-entered __import__, so the guard did not fire and the test
    passed alone but failed in a full run.

    The exit-2-not-exit-1 contract this test exists for is unchanged.
    """
    real_import = builtins.__import__
    blocked = "src.core.errors.codes"

    def _no_code_table(name, *args, **kwargs):
        if name in (blocked, "adcp"):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    # Evict it first: an already-imported module is served from sys.modules without
    # ever consulting __import__, which is exactly what made this order-dependent.
    monkeypatch.delitem(sys.modules, blocked, raising=False)
    monkeypatch.setattr(builtins, "__import__", _no_code_table)

    with pytest.raises(SystemExit) as exc_info:
        _script.load_enum()

    assert exc_info.value.code == 2, (
        f"a missing SDK produced exit {exc_info.value.code}, expected 2. Exit 1 means "
        "'non-canonical codes found' and gates make quality — an instrument failure reported "
        "that way is a silent false result."
    )
    assert "emittable code table not found" in capsys.readouterr().err


def test_unrelated_import_error_is_not_swallowed(monkeypatch):
    """Only the SDK's absence is relabelled — an unrelated failure propagates.

    The catch is deliberately one type around one import. Broadening it (an
    earlier version caught three types around a JSON read) turns any bug on the
    load path into a quiet exit 2 that reads as a clean instrument failure.
    """

    def _boom(name, *args, **kwargs):
        raise RuntimeError("an unrelated bug")

    monkeypatch.setattr(builtins, "__import__", _boom)

    with pytest.raises(RuntimeError, match="an unrelated bug"):
        _script.load_enum()


def test_load_enum_reads_the_sdk_enum():
    """Negative control: the happy path returns the SDK's real vocabulary."""
    codes = _script.load_enum()
    assert "VALIDATION_ERROR" in codes
    assert len(codes) >= 90, f"expected the SDK's current ~92-code enum, got {len(codes)}"
