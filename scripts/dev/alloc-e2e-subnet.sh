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
# src/core/security/egress/policy.py's _blocked_address, whose `ip.is_private`
# term covers it, so no hostname or certificate could make an in-stack receiver
# reachable while the gate stayed armed).
#
# But a FIXED subnet is a concurrency break, measured: `docker network create
# --subnet 223.255.255.0/26` succeeds once and the SECOND stack on the box fails
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
# 223.255.255.0/24 is APNIC's Debogon Project prefix. It is the last /24 of
# 223/8, which RFC 3330 §3 held back as "Reserved but subject to allocation";
# when IANA delegated 223/8 to APNIC (IANA IPv4 Address Space Registry, 2010)
# APNIC retained this final /24 for debogon measurement instead of assigning
# it. APNIC's RDAP entry for it still reads `name: Debogon-prefix, registrant:
# APNIC Pty Ltd` — a measurement prefix, never a production destination — and
# RIPE RIS sees it announced by 0 of 325 peers, so squatting it inside a
# container network namespace shadows nothing this box can reach. Its
# neighbours are NOT interchangeable: 223.255.252.0/23 (CHINANET-FJ) and
# 223.255.254.0/24 (MBS-AS-AP) are live, globally announced production
# networks. Do not widen the pool past this /24.
#
# WHY IT MOVED OFF 192.88.99.0/24 (6to4 anycast, deprecated by RFC 7526)
# ---------------------------------------------------------------------
# GH #1802 added 192.88.99.0/24 to EgressPolicy._SUPPLEMENT_NETWORKS in
# src/core/security/egress/policy.py, and _in_supplement_range is checked
# UNCONDITIONALLY — first term of _blocked_address, ahead of and outside the
# ADCP_OUTBOUND_ALLOW_PRIVATE hatch. So the old pool went from "accepted, which
# is the whole point" to "refused under every posture": measured, all 256
# addresses of 192.88.99.0/24 now fail EgressPolicy.check_registration. That
# would have failed exactly the in-network accept-case tests this pool exists
# to make gradeable. Do NOT restore it by deleting the supplement entry —
# that entry is production SSRF policy grounded in RFC 7526.
#
# IF THIS EVER HAS TO MOVE AGAIN, RE-DERIVE — DO NOT GUESS
# -------------------------------------------------------
# The whole IANA IPv4 Special-Purpose Address Registry is now closed to us
# (measured entry by entry against the real predicate). Every entry is refused
# either by the SDK's flag set — 0/8, 10/8, 127/8, 169.254/16, 172.16/12,
# 192.0.0/24, 192.0.2/24, 192.168/16, 198.18/15 (RFC 2544), 198.51.100/24,
# 203.0.113/24, 224/4, 240/4 — or by _SUPPLEMENT_NETWORKS: 100.64/10,
# 192.31.196/24, 192.52.193/24, 192.88.99/24, 192.175.48/24. IANA holds no
# other reserved IPv4 (the only RESERVED /8s left are loopback, private,
# multicast and 240/4 "future use"). IPv6 is no escape either: 3fff::/20
# (RFC 9637) and 2002::/16 are is_private, 5f00::/16 (RFC 9602) is
# is_reserved, 2001:20::/28 is in the supplement.
#
# So any usable pool is delegated global unicast, and the selection criterion
# is therefore "registry-documented as non-production AND unannounced in BGP",
# confirmed by CALLING EgressPolicy.check_registration on several addresses —
# never by reading ipaddress.is_private, which is only half the predicate.
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

POOL="${E2E_SUBNET_POOL:-223.255.255.0/24}"
PREFIX="${E2E_SUBNET_PREFIX:-26}"

# The probe below asks Docker which slices are already taken rather than
# tracking state in a file: a stack killed with -9 leaves no file behind but its
# network can linger, and that lingering network is what the next `up` collides
# with.
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
