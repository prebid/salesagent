"""
Unit tests for E2E test-stack port allocation hardening.

Root cause of the mass e2e 502 failures (salesagent-18h.12): the port
allocators in scripts/test-stack.sh, .claude/skills/agent-db/agent-db.sh and
tests/e2e/conftest.py:find_free_port() all suffer from two defects when many
worktree agents run in parallel on one host:

1. Convergence (cross-process TOCTOU): every allocator scans the SAME range
   from the SAME low bound and returns the first free port. Two sibling
   worktrees racing between probe-close and `docker run -p` deterministically
   pick the *identical* lowest free port and collide -- one stack
   half-starts and nginx serves 502 on the contended port. The fix is a
   deterministic per-process scatter (start offset derived from PID) so
   independent processes diverge instead of converging.

2. Interface mismatch: the probe binds 127.0.0.1, but Docker publishes
   `-p host:container` on all interfaces (0.0.0.0). A port already
   published by another stack on 0.0.0.0 is NOT detected by a
   127.0.0.1-only probe, so the allocator hands out an already-taken port.
   The fix is to probe on the all-interfaces address Docker actually uses.

These tests pin that contract. They exercise the real production helpers
(tests.e2e.conftest.find_free_port / port_scan_start) -- no mocks, no AST
scanning.
"""

import socket

import pytest

from tests.e2e.conftest import find_free_port, port_scan_start


def _bind_all_interfaces(port: int) -> socket.socket:
    """Bind a port the way Docker publishes it: all interfaces, no reuse."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    s.bind(("", port))
    s.listen(1)
    return s


class TestPortScanStartScattersByProcess:
    """
    port_scan_start must deterministically scatter the scan origin across
    the range so two parallel worktree processes do not both start at the
    low bound and converge on the same first-free port.
    """

    def test_start_is_inside_range(self):
        start = port_scan_start(50000, 60000, pid=12345)
        assert 50000 <= start < 60000

    def test_same_pid_is_deterministic(self):
        a = port_scan_start(50000, 60000, pid=777)
        b = port_scan_start(50000, 60000, pid=777)
        assert a == b, "scan start must be stable within a process"

    def test_different_pids_diverge(self):
        """
        The core anti-convergence property: a spread of distinct PIDs must
        produce a spread of distinct scan origins (not all clamped to the
        low bound). Without this, parallel agents collide on the e2e port.
        """
        starts = {port_scan_start(50000, 60000, pid=p) for p in range(2000, 2200)}
        assert len(starts) > 50, (
            f"Only {len(starts)} distinct scan origins for 200 PIDs -- "
            "insufficient scatter, parallel worktrees will still converge."
        )

    def test_not_all_zero_offset(self):
        """A non-trivial fraction of PIDs must start above the low bound."""
        above = sum(1 for p in range(1, 500) if port_scan_start(50000, 60000, pid=p) > 50000)
        assert above > 400, "scan start barely moves off the low bound"


class TestFindFreePortDetectsDockerStyleBind:
    """find_free_port must not hand out a port already published on 0.0.0.0."""

    def test_skips_port_bound_on_all_interfaces(self):
        """
        Reproduces defect #2: a port held on 0.0.0.0 (how Docker -p binds)
        must be treated as unavailable. A 127.0.0.1-only probe would wrongly
        consider it free and hand it to a colliding docker run.
        """
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("", 0))
        occupied = probe.getsockname()[1]
        probe.close()

        holder = _bind_all_interfaces(occupied)
        try:
            for _ in range(20):
                got = find_free_port(occupied, occupied + 80)
                assert got != occupied, (
                    f"find_free_port returned {got}, a port already bound on "
                    "0.0.0.0 (Docker-style). This causes the e2e 502 collision."
                )
        finally:
            holder.close()


class TestFindFreePortContract:
    """Basic contract: stays in range, raises when exhausted."""

    def test_returns_port_within_requested_range(self):
        p = find_free_port(50000, 60000)
        assert 50000 <= p < 60000

    def test_distinct_held_ports_do_not_repeat(self):
        held: list[socket.socket] = []
        ports: list[int] = []
        try:
            for _ in range(8):
                p = find_free_port(50000, 60000)
                assert p not in ports, (
                    f"find_free_port returned duplicate port {p} while a prior allocation is still held."
                )
                ports.append(p)
                held.append(_bind_all_interfaces(p))
        finally:
            for s in held:
                s.close()

    def test_raises_when_range_exhausted(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("", 0))
        only = probe.getsockname()[1]
        probe.close()

        holder = _bind_all_interfaces(only)
        try:
            with pytest.raises(RuntimeError):
                find_free_port(only, only + 1)
        finally:
            holder.close()
