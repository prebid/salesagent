"""adcp-server's baked-in container uid can't use run_all_tests.sh's log mitigation.

Bug: salesagent-dhhlg. A live `cassini run` (sa-67ae7993, worktree
salesagent-sbsweep) crashed adcp-server at container startup with
``PermissionError: [Errno 13] Permission denied: '/app/logs/audit.log'`` even
though run_all_tests.sh pre-creates ``logs/`` (mkdir + chmod 2775 setgid) and
recreates each log file at mode 664, specifically so a second uid can append to
files the first uid created. run_all_tests.sh's own comment claims this is
"Verified live: ci:ci 0644 -> sacirunner:ci 0664".

That verification covers the INVOKING uid (whoever runs run_all_tests.sh)
versus the `tests` container's uid (``TEST_UID``/``TEST_GID``, derived from the
same invoker via ``id -u``/``id -g`` -- see docker-compose.e2e.yml's `tests`
service). It never covers adcp-server's OWN identity: the Dockerfile bakes a
FIXED uid/gid that has nothing to do with whoever invokes run_all_tests.sh --

    Dockerfile:124  RUN groupadd -r -g 1001 app && useradd -r -u 1001 -g app ...
    Dockerfile:146  USER app:app

-- and docker-compose.e2e.yml declares no ``group_add:`` for the adcp-server
service, so uid 1001/gid 1001 ("app") is never made a member of whatever group
owns the invoker-created log files. A file at mode 664 grants write only to its
owner and group members; adcp-server's baked uid is neither, so it falls back to
"other" (read only), and any attempt to open the file for append -- exactly what
``logging.FileHandler(path, mode="a")`` does, which is exactly what
``src.core.audit_logger`` did EAGERLY AT IMPORT on the salesagent-sbsweep
worktree (HEAD at crash time predated commit 9ad1ee66f, which made that same
import lazy and pointed adcp-server's own logs at a container-local
``ADCP_LOG_DIR`` instead of the shared bind mount) -- raises PermissionError.

These tests reproduce the mechanism directly against real Linux uid/gid
permission enforcement inside Docker containers (not a permission-bit
calculation in Python), using adcp-server's actual baked-in identity from the
Dockerfile and run_all_tests.sh's own mitigation steps verbatim. The fix:
docker-compose.e2e.yml's adcp-server now carries
`group_add: ["${TEST_GID:-0}"]`.
"""

import shutil
import subprocess
import uuid

import pytest

pytestmark = pytest.mark.integration

_DOCKER_AVAILABLE = shutil.which("docker") is not None

# The exact uid:gid the Dockerfile bakes for adcp-server (Dockerfile:124, 146).
_ADCP_SERVER_UID_GID = "1001:1001"

# Stand-in for "whoever invokes run_all_tests.sh". Any uid/gid distinct from
# both adcp-server's own identity and root is sufficient to exercise the gap --
# the mitigation's own comment names it "sacirunner:ci" on the real box.
_INVOKER_UID_GID = "2000:2000"

# run_all_tests.sh's mitigation, verbatim (see its comment around lines
# 135-152): pre-create logs/ group-writable + setgid, then unlink and recreate
# each file at 664 so a stale foreign-owned file can't defeat a chmod sweep.
_RUN_ALL_TESTS_SH_MITIGATION = (
    "cd /work && mkdir -p logs && chmod 2775 logs && "
    "for f in audit.log error.log structured.jsonl security.jsonl; do "
    'rm -f "logs/$f" 2>/dev/null || true; '
    ': > "logs/$f" && chmod 664 "logs/$f"; '
    "done"
)


def _run_mitigation_scenario(*, group_add: str | None) -> subprocess.CompletedProcess:
    """Replay run_all_tests.sh's mitigation as the INVOKER identity inside a
    throwaway container, then attempt to append to the resulting file as
    adcp-server's real baked-in identity (uid 1001, gid 1001/"app") -- what
    logging.FileHandler(path, mode="a") does. `group_add`, when given,
    mirrors docker-compose's own `group_add:` (a container-CREATION-time
    setting, not retroactively applicable via `docker exec` -- hence it is
    a `docker run` flag here, not part of the later `docker exec -u ...`).
    Returns the append attempt's CompletedProcess; caller asserts.
    """
    volume = f"adcp-log-mitigation-repro-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)
    container = None
    try:
        run_cmd = ["docker", "run", "-d", "--rm", "-v", f"{volume}:/work"]
        if group_add:
            run_cmd += ["--group-add", group_add]
        run_cmd += ["alpine:3.20", "sleep", "120"]
        run = subprocess.run(run_cmd, check=True, capture_output=True, text=True)
        container = run.stdout.strip()

        subprocess.run(
            ["docker", "exec", container, "chown", _INVOKER_UID_GID, "/work"],
            check=True,
            capture_output=True,
        )

        mitigate = subprocess.run(
            ["docker", "exec", "-u", _INVOKER_UID_GID, container, "sh", "-c", _RUN_ALL_TESTS_SH_MITIGATION],
            capture_output=True,
            text=True,
        )
        assert mitigate.returncode == 0, f"mitigation setup itself failed:\n{mitigate.stderr}"

        return subprocess.run(
            ["docker", "exec", "-u", _ADCP_SERVER_UID_GID, container, "sh", "-c", "echo probe >> /work/logs/audit.log"],
            capture_output=True,
            text=True,
        )
    finally:
        if container:
            subprocess.run(["docker", "stop", container], capture_output=True)
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)


@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="requires a local docker daemon")
def test_adcp_server_can_append_once_group_added_salesagent_dhhlg_fixed():
    """The fix: docker-compose.e2e.yml's adcp-server now carries
    `group_add: ["${TEST_GID:-0}"]`, making its baked-in uid a member of
    the invoker's group -- exactly what --group-add mirrors here. The
    append that failed in the sibling test (below) must now succeed.
    """
    append = _run_mitigation_scenario(group_add=_INVOKER_UID_GID.split(":")[1])
    assert append.returncode == 0, (
        "adcp-server's baked-in uid (1001:1001) still could not append to audit.log "
        f"even WITH group_add covering the invoker's group -- stderr: {append.stderr!r}"
    )


@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="requires a local docker daemon")
def test_adcp_server_uid_cannot_append_to_a_log_file_without_group_add():
    """The bug, still true of the MECHANISM in isolation (not of
    salesagent-1210's current compose file, which now sets group_add):
    without it, adcp-server's baked-in uid is neither the file's owner
    nor a group member, so it falls into 664's read-only "other" bucket.
    """
    append = _run_mitigation_scenario(group_add=None)
    assert append.returncode != 0, (
        "adcp-server's baked-in uid unexpectedly succeeded WITHOUT group_add -- "
        "this test's own premise (the gap is real without it) no longer holds"
    )
