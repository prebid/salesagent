#!/usr/bin/env bash
# Allocate a DISJOINT non-private subnet slice for one e2e compose stack.
#
# Prints `E2E_NETWORK_SUBNET=<cidr>` on stdout; callers `eval` it or export the
# value before `docker compose up`.
#
# WHY THIS EXISTS (salesagent-mp53.9)
# -----------------------------------
# docker-compose.e2e.yml pins the stack's network to a NON-PRIVATE range so the
# server reaches its webhook receiver at an address production's own SSRF gate
# accepts (172.16/12 — Docker's default bridge — is refused by
# src/core/security/url_validator.py's BLOCKED_NETWORKS, so no hostname or
# certificate could make an in-stack receiver reachable while the gate stayed
# armed).
#
# But a FIXED subnet is a concurrency break, measured: `docker network create
# --subnet 192.88.99.0/26` succeeds once and the SECOND stack on the box fails
# with "Pool overlaps with other one on this address space" — even for a
# smaller slice inside the held range. This repo runs stacks concurrently BY
# DESIGN (test-stack.sh sets COMPOSE_PROJECT_NAME="adcp-test-$$"; the CI box
# runs several worktrees at once). Templating the value without shipping an
# allocator just moves the collision to the default — every stack still asks
# for the same slice.
#
# So this allocates the way ports already are (scripts/test-stack.sh, and
# tests/e2e/conftest.py's find_free_port): scatter the scan origin by PID so
# parallel agents diverge instead of racing for the same first slot, probe what
# Docker has actually taken, and wrap around so the whole pool is searched.
#
# POOL CHOICE
# -----------
# 192.88.99.0/24 is IANA-allocated 6to4 relay anycast space, DEPRECATED by RFC
# 7526 and no longer routed for its original purpose. Squatting it is confined
# to a container network namespace and reaches no real host. It is the
# least-bad option available, not an arbitrary pick: every documentation range
# (192.0.2/24, 198.51.100/24, 203.0.113/24), the RFC 2544 benchmarking range
# and 240/4 all report is_private=True in Python's ipaddress module and would
# be refused by the very gate this exists to satisfy. 100.64/10 is non-private
# but sits in BLOCKED_NETWORKS. Verified with the real predicate — see
# salesagent-mp53.9.
#
# SLICE SIZE
# ----------
# /26 (61 usable) sized from the PEAK container count, not the service count: a
# full run provisions per-worker stacks when E2E_WORKERS>0 (8 on the CI box),
# each adding TWO containers, so peak is 8 services + 2*8 sidecars = 24 plus a
# gateway. /28 (13 usable) dies partway into a 20-minute run; /27 (29) fits with
# 4 spare. /26 costs concurrency — four slices per /24 — which is the right
# trade: a stack that dies at container 14 wastes far more than a queued one.
set -euo pipefail

POOL="${E2E_SUBNET_POOL:-192.88.99.0/24}"
PREFIX="${E2E_SUBNET_PREFIX:-26}"

# Slices already taken by a live Docker network. Asking Docker rather than
# tracking state in a file: a stack killed with -9 leaves no file behind but its
# network can linger, and that lingering network is what the next `up` collides
# with.
taken() {
    docker network ls -q 2>/dev/null | while read -r id; do
        docker network inspect "$id" --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null || true
    done
}

python3 - "$POOL" "$PREFIX" "$$" <<'PY'
import ipaddress, os, subprocess, sys

pool = ipaddress.ip_network(sys.argv[1])
prefix = int(sys.argv[2])
pid = int(sys.argv[3])

slices = list(pool.subnets(new_prefix=prefix))
if not slices:
    sys.exit(f"pool {pool} cannot be split into /{prefix} slices")

out = subprocess.run(
    ["docker", "network", "ls", "-q"], capture_output=True, text=True, check=False
).stdout.split()
taken = []
for net_id in out:
    fmt = "{{range .IPAM.Config}}{{.Subnet}} {{end}}"
    got = subprocess.run(
        ["docker", "network", "inspect", net_id, "--format", fmt],
        capture_output=True, text=True, check=False,
    ).stdout.split()
    for cidr in got:
        try:
            taken.append(ipaddress.ip_network(cidr))
        except ValueError:
            continue

# Scatter the origin by PID so two agents starting at the same moment do not
# both pick slot 0, then wrap so the whole pool is still searched.
start = pid % len(slices)
for offset in range(len(slices)):
    candidate = slices[(start + offset) % len(slices)]
    if not any(candidate.overlaps(t) for t in taken):
        print(f"E2E_NETWORK_SUBNET={candidate}")
        break
else:
    sys.exit(
        f"no free /{prefix} slice in {pool} — {len(slices)} slices, all overlapping a live "
        f"Docker network. Tear down abandoned stacks (docker network prune) or widen "
        f"E2E_SUBNET_POOL / shrink E2E_SUBNET_PREFIX."
    )
PY
