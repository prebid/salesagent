#!/usr/bin/env bash
# Run the @adcp/sdk media_buy_seller storyboard against a live agent on both
# transports (MCP + A2A) and print a tight pass/fail summary.
#
# This is the launch-readiness check that would have caught #71. The reporter
# ran this exact storyboard against deployed Wonderstruck staging — every
# protocol drift surfaces here in seconds.
#
# Usage:
#   AGENT_URL=https://wonderstruck.sales-agent.scope3.com \
#   AGENT_TOKEN=<bearer-token> \
#   ./scripts/storyboard-check.sh
#
#   # Or pass storyboard ID (default: media_buy_seller)
#   STORYBOARD=capability_discovery ./scripts/storyboard-check.sh
#
#   # Or production-path mode (no sandbox routing):
#   NO_SANDBOX=1 ./scripts/storyboard-check.sh
#
#   # Local dev (non-HTTPS agent — production must terminate TLS):
#   AGENT_URL=http://localhost:8000 AGENT_TOKEN=ci-test-token \
#   ALLOW_HTTP=1 ./scripts/storyboard-check.sh
#
#   # Pin the JS storyboard runner/spec line:
#   ADCP_SDK_PACKAGE=@adcp/sdk@7.11.0 ./scripts/storyboard-check.sh          # AdCP 3.0.x
#   ADCP_SDK_PACKAGE=@adcp/sdk@8.1.0-beta.11 ./scripts/storyboard-check.sh   # AdCP 3.1 beta
#
# Outputs:
#   - One-line summary per transport: passed/failed/skipped + duration
#   - List of failing step IDs with truncated error messages
#   - Per-transport JSON written to /tmp/storyboard-<protocol>.json for
#     drilldown
#
# Exit code:
#   0 if BOTH transports pass all steps
#   1 if either transport has any failing step
#   2 on missing prerequisites (npx, jq, env vars)
#
# Prereqs: npx (Node), jq

set -uo pipefail

# ─── Args / env ──────────────────────────────────────────────────────────────
AGENT_URL="${AGENT_URL:-}"
AGENT_TOKEN="${AGENT_TOKEN:-}"
STORYBOARD="${STORYBOARD:-media_buy_seller}"
TIMEOUT="${TIMEOUT:-180}"
NO_SANDBOX="${NO_SANDBOX:-0}"
ADCP_SDK_PACKAGE="${ADCP_SDK_PACKAGE:-@adcp/sdk}"
# ALLOW_HTTP="1" lets you run against a non-HTTPS local agent (e.g.
# http://localhost:8000). The SDK refuses HTTP by default — production
# agents must terminate TLS. Only use this for local dev validation.
ALLOW_HTTP="${ALLOW_HTTP:-0}"

if [[ -z "$AGENT_URL" || -z "$AGENT_TOKEN" ]]; then
    cat <<EOF >&2
✗ AGENT_URL and AGENT_TOKEN must be set.

Example:
  AGENT_URL=https://wonderstruck.sales-agent.scope3.com \\
  AGENT_TOKEN=<bearer> ./scripts/storyboard-check.sh
EOF
    exit 2
fi

# Strip trailing slash and any /mcp suffix; we'll add per-transport suffixes
# below so MCP and A2A both get the right URL shape.
AGENT_BASE="${AGENT_URL%/}"
AGENT_BASE="${AGENT_BASE%/mcp}"

for cmd in npx jq; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "✗ $cmd is required" >&2
        exit 2
    fi
done

EXTRA_FLAGS=()
if [[ "$NO_SANDBOX" == "1" ]]; then
    EXTRA_FLAGS+=(--no-sandbox)
fi
if [[ "$ALLOW_HTTP" == "1" ]]; then
    EXTRA_FLAGS+=(--allow-http)
fi

# ─── Per-transport runner ────────────────────────────────────────────────────

# Run the storyboard against one transport and print a one-line summary plus
# a bulleted list of failing steps. Writes raw JSON to /tmp/storyboard-<proto>.json.
# Sets the global RC_PROTO_<protocol> to 0 (all passed) or 1 (any failure).
run_one() {
    local protocol="$1"
    local url="$2"
    local out="/tmp/storyboard-${protocol}.json"
    local err="/tmp/storyboard-${protocol}.err"

    echo "── ${protocol^^} ──"
    echo "→ ${url}"

    # Capture both streams; the always-on STORYBOARD-FAIL summary lives on
    # stderr; --json puts the full ComplianceResult on stdout so we can
    # post-process skip causes (which the always-on summary doesn't surface
    # — see adcontextprotocol/adcp-client#1623).
    npx -y "$ADCP_SDK_PACKAGE" storyboard run "$url" "$STORYBOARD" \
        --auth "$AGENT_TOKEN" \
        --protocol "$protocol" \
        --json \
        --timeout "$TIMEOUT" \
        "${EXTRA_FLAGS[@]}" \
        >"$out" 2>"$err"
    local exit_code=$?

    # Pull the summary block from stderr — present whether or not the run
    # passed. Falls back to "no summary" when the SDK errored before running.
    local summary
    summary=$(awk '/^──── Storyboard Summary ────/,/^STORYBOARD-/' "$err" | head -20)
    if [[ -z "$summary" ]]; then
        echo "  (no summary — SDK aborted before running; see $err)"
        echo "  $(tail -3 "$err" | head -1)"
        eval "RC_${protocol}=1"
        return
    fi

    # Steps line: "Steps:     0 passed, 11 failed, 48 skipped"
    grep -E "^Steps:" <<<"$summary" | sed 's/^/  /'
    grep -E "^Duration:" <<<"$summary" | sed 's/^/  /'

    # Failing steps (one per line under STORYBOARD-FAIL block)
    local failing
    failing=$(awk '/^STORYBOARD-FAIL/,/^$/' "$err" | grep -E '^\s+- ' | head -50)
    if [[ -n "$failing" ]]; then
        echo "  Failing steps:"
        # Truncate each line at 200 chars so the summary stays readable; full
        # output remains in the .json/.err files.
        echo "$failing" | sed 's/^/  /' | cut -c1-200
    fi

    # Skip causes (parsed from the --json output). The SDK's always-on summary
    # only counts skips; this surfaces *why*, so a "30 skipped" line breaks down
    # into "missing comply_test_controller (28), missing sync_accounts (2)" —
    # actionable instead of opaque. See upstream issue
    # adcontextprotocol/adcp-client#1623 for the eventual fix.
    if [[ -s "$out" ]] && command -v python3 >/dev/null; then
        local skip_summary
        skip_summary=$(python3 - "$out" <<'PYEOF'
import json, re, sys
from collections import defaultdict
try:
    data = json.load(open(sys.argv[1]))
except (json.JSONDecodeError, OSError):
    sys.exit(0)

# Collect (cause, scenario) tuples from skipped steps.
causes = defaultdict(set)  # cause_key -> set(scenario_id)
total = 0
def walk(o, sc=None):
    global total
    if isinstance(o, dict):
        if o.get('scenario'):
            sc = o['scenario']
        if o.get('skipped') and o.get('skip_reason'):
            reason = o['skip_reason']
            tool = None
            for w in (o.get('warnings') or []):
                m = re.search(r'did not advertise tool ["\']([^"\']+)["\']', str(w))
                if m:
                    tool = m.group(1); break
            key = f"{reason}: {tool}" if tool else reason
            causes[key].add(sc or '?')
            total += 1
        for v in o.values(): walk(v, sc)
    elif isinstance(o, list):
        for v in o: walk(v, sc)

walk(data)
if not causes:
    sys.exit(0)

print('  Skip causes:')
for key, scenarios in sorted(causes.items(), key=lambda x: -len(x[1])):
    print(f'    [{len(scenarios):3d}] {key}')
    # Compact scenario list — strip the storyboard prefix.
    short = sorted({s.split('/', 1)[-1].rsplit('/', 1)[0] if s.count('/') >= 2 else s for s in scenarios})
    line = ', '.join(short)
    if len(line) > 200:
        line = line[:197] + '...'
    print(f'         Affected: {line}')
PYEOF
)
        if [[ -n "$skip_summary" ]]; then
            echo "$skip_summary"
        fi
    fi

    # passed=0 in summary == failure for our purposes
    if grep -qE "^Steps:.*0 passed" <<<"$summary" || grep -qE "STORYBOARD-FAIL" "$err"; then
        eval "RC_${protocol}=1"
    else
        # Conservative: only mark pass if no STORYBOARD-FAIL line at all
        if grep -qE "STORYBOARD-FAIL" "$err"; then
            eval "RC_${protocol}=1"
        else
            eval "RC_${protocol}=0"
        fi
    fi

    echo
    return $exit_code  # report SDK exit code per transport
}

# ─── Main ────────────────────────────────────────────────────────────────────

echo "Storyboard: $STORYBOARD"
echo "SDK:        $ADCP_SDK_PACKAGE"
echo "Sandbox:    $([[ $NO_SANDBOX == 1 ]] && echo off || echo on)"
echo

# MCP gets the /mcp path; A2A speaks at the host root per AdCP convention.
RC_mcp=0
RC_a2a=0
run_one mcp "${AGENT_BASE}/mcp" || true

# Storyboard scenarios mutate state (update_swap_lists, etc.) and reuse
# idempotency keys per scenario. Running MCP and A2A back-to-back against the
# same DB causes the second protocol's idempotency replay to return the cached
# create response while reading the post-update DB state — surfaces as bogus
# verify_create_persisted failures. Set BETWEEN_PROTOCOLS_HOOK to a shell
# command that resets the seller's storage between protocol runs (no-op when
# unset, e.g. against a remote agent we don't control).
if [[ -n "${BETWEEN_PROTOCOLS_HOOK:-}" ]]; then
    echo "── Reset between protocols ──"
    eval "$BETWEEN_PROTOCOLS_HOOK" || echo "  (hook returned non-zero; continuing)"
    echo
fi

run_one a2a "${AGENT_BASE}/" || true

echo "── Result ──"
echo "MCP: $([[ $RC_mcp == 0 ]] && echo PASS || echo FAIL)"
echo "A2A: $([[ $RC_a2a == 0 ]] && echo PASS || echo FAIL)"

if [[ $RC_mcp != 0 || $RC_a2a != 0 ]]; then
    exit 1
fi
