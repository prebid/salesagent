"""Importing the audit logger must not touch the filesystem.

``src.core.audit_logger`` used to create a CWD-relative ``logs/`` directory and
open two ``logging.FileHandler``s at MODULE IMPORT time. Import-time I/O on a
relative path makes the module's importability depend on who owns the current
working directory, which is a property no importer can control:

* under Docker every suite runs with CWD ``/app`` on a bind mount shared with
  the server container. Whichever container touched ``logs/audit.log`` first
  owned it at mode 644, and the other uid then died at *collection* with
  ``PermissionError: [Errno 13] Permission denied: '/app/logs/audit.log'`` --
  taking every suite that imports this module with it;
* anyone running the suite from a directory they cannot write hits the same
  wall, with no way to opt out short of not importing the module.

The invariant these tests pin is therefore about the import itself, not about
any particular deployment: importing a logging module writes nothing, so it
cannot fail on a filesystem it was never asked to touch. Handlers open on first
emit instead, which is when a log path is genuinely needed.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Root bypasses the permission bits these two scenarios are built from, so the
# hostile directory/file simply is not hostile to it. The invariant still gets
# graded for root by test_import_writes_nothing_into_cwd, which asserts the
# absence of writes rather than a denial of them.
_requires_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="permission bits do not constrain root; the write-nothing test grades this invariant for root",
)


_EMIT_ONE_AUDIT_RECORD = (
    "from src.core.audit_logger import get_audit_logger\n"
    "get_audit_logger('mock', tenant_id='tenant_x').log_operation(\n"
    "    operation='create_media_buy', principal_name='Acme',\n"
    "    principal_id='p_1', adapter_id='adv_1', success=True)\n"
)


def _run_in_fresh_interpreter(cwd: Path, code: str, **env_overrides: str) -> subprocess.CompletedProcess:
    """Run ``code`` in a FRESH interpreter rooted at ``cwd``.

    A subprocess is essential, not incidental: the import runs once per
    interpreter, and pytest has already imported this module long before any
    test body runs, so an in-process import would assert nothing.
    """
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), **env_overrides},
        capture_output=True,
        text=True,
        timeout=180,
    )


def _import_audit_logger(cwd: Path) -> subprocess.CompletedProcess:
    return _run_in_fresh_interpreter(cwd, "import src.core.audit_logger")


def test_import_writes_nothing_into_cwd(tmp_path):
    """The import must leave the working directory exactly as it found it."""
    workdir = tmp_path / "empty"
    workdir.mkdir()

    result = _import_audit_logger(workdir)

    assert result.returncode == 0, f"import failed:\n{result.stderr}"
    assert sorted(p.name for p in workdir.iterdir()) == [], (
        "importing src.core.audit_logger created filesystem entries in the CWD; import must perform no filesystem I/O"
    )


@_requires_non_root
def test_import_succeeds_when_cwd_is_not_writable(tmp_path):
    """A read-only working directory must not make the module unimportable."""
    workdir = tmp_path / "readonly"
    workdir.mkdir()
    workdir.chmod(0o555)
    try:
        result = _import_audit_logger(workdir)
    finally:
        # Restore write permission so pytest's tmp_path cleanup can remove it.
        workdir.chmod(0o755)

    assert result.returncode == 0, f"import failed under a read-only CWD:\n{result.stderr}"


@_requires_non_root
def test_import_succeeds_when_existing_log_file_is_not_writable(tmp_path):
    """The exact CI shape: ``logs/`` is writable but ``audit.log`` is not.

    This is what a second uid saw on the shared bind mount after the first one
    created the file at mode 644 -- the directory could be traversed and written,
    yet opening the existing file for append was refused.
    """
    workdir = tmp_path / "occupied"
    logs = workdir / "logs"
    logs.mkdir(parents=True)
    for name in ("audit.log", "error.log"):
        log_file = logs / name
        log_file.touch()
        log_file.chmod(0o000)

    result = _import_audit_logger(workdir)

    assert result.returncode == 0, f"import failed on a pre-existing log file owned by another uid:\n{result.stderr}"


@_requires_non_root
def test_logging_does_not_raise_when_the_backup_file_cannot_be_opened(tmp_path):
    """An unwritable backup file must not take the caller down with it.

    Deferring the handler's open to first emit is not on its own enough:
    ``FileHandler.emit`` opens outside the ``try`` that guards the write, so
    without the handler's own guard this scenario merely relocates the original
    PermissionError from import time to the first audited operation -- straight
    into business logic.
    """
    workdir = tmp_path / "unwritable_backup"
    logs = workdir / "logs"
    logs.mkdir(parents=True)
    for name in ("audit.log", "error.log"):
        log_file = logs / name
        log_file.touch()
        log_file.chmod(0o000)

    result = _run_in_fresh_interpreter(workdir, _EMIT_ONE_AUDIT_RECORD)

    assert result.returncode == 0, (
        "an unwritable audit backup file propagated into the caller instead of "
        f"degrading the backup sink:\n{result.stderr}"
    )
    # The handler's docstring claims "a degraded sink ANNOUNCING ITSELF, not a
    # silently swallowed failure". Without this, replacing `self.handleError(record)`
    # with `pass` -- making the swallow genuinely silent -- leaves this module
    # green, and the distinction the docstring draws is graded nowhere.
    assert "--- Logging error ---" in result.stderr, (
        "the degraded sink swallowed the failure silently; `handleError` must "
        f"report it on stderr. stderr was:\n{result.stderr!r}"
    )


def test_logging_still_writes_its_backup_file_without_a_preexisting_log_dir(tmp_path):
    """Emitting an audit record must still land ``logs/structured.jsonl``.

    Moving directory creation out of import time must not quietly disable the
    file backup: ``_write_structured_log`` opens its path inside a broad
    ``except Exception``, so a missing directory would degrade to a log line and
    nothing else, which is exactly the kind of silent loss that hides for months.

    The database write inside ``log_operation`` is expected to fail here (no DB
    is configured) and is deliberately not the subject -- the production code
    catches it and proceeds to the file backup, which is what is being graded.
    """
    workdir = tmp_path / "no_log_dir"
    workdir.mkdir()

    result = _run_in_fresh_interpreter(workdir, _EMIT_ONE_AUDIT_RECORD)

    assert result.returncode == 0, f"emitting an audit record failed:\n{result.stderr}"
    structured = workdir / "logs" / "structured.jsonl"
    assert structured.is_file(), (
        "logs/structured.jsonl was not written; the log directory must be created "
        "on first use now that it is no longer created at import"
    )
    assert "create_media_buy" in structured.read_text(encoding="utf-8"), (
        "the audit record never reached the backup file"
    )


def test_adcp_log_dir_redirects_the_backup_away_from_the_working_directory(tmp_path):
    """``ADCP_LOG_DIR`` is what lets two uids sharing a CWD stop contending.

    The compose stack sets it for the server so the server and the test runner --
    which share /app as a bind mount but run as different uids -- never write to
    the same log files. If this knob stops being honoured, that separation is
    silently gone and the containers are racing again.
    """
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    private_logs = tmp_path / "private" / "adcp-logs"

    result = _run_in_fresh_interpreter(workdir, _EMIT_ONE_AUDIT_RECORD, ADCP_LOG_DIR=str(private_logs))

    assert result.returncode == 0, f"emitting an audit record failed:\n{result.stderr}"
    assert (private_logs / "structured.jsonl").is_file(), (
        f"ADCP_LOG_DIR was not honoured; nothing was written to {private_logs}"
    )
    assert not (workdir / "logs").exists(), "the backup was written to the CWD despite ADCP_LOG_DIR pointing elsewhere"
