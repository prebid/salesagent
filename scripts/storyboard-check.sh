#!/usr/bin/env bash
# Run @adcp/sdk storyboards against a live agent on one or both transports and
# print a tight pass/fail summary.
#
# By default this runs a capability-driven assessment: the SDK calls
# get_adcp_capabilities and selects the storyboards for the agent's advertised
# specialisms. Set STORYBOARD or STORYBOARDS only when deliberately narrowing a
# local investigation.
#
# Usage:
#   AGENT_URL=https://wonderstruck.sales-agent.scope3.com \
#   AGENT_TOKEN=<bearer-token> \
#   ./scripts/storyboard-check.sh
#
#   # Pin the SDK version used by CI/release checks:
#   ADCP_SDK_VERSION=7.11.0 ./scripts/storyboard-check.sh
#
#   # Or pass one storyboard ID
#   STORYBOARD=capability_discovery ./scripts/storyboard-check.sh
#
#   # Or run a targeted storyboard smoke set:
#   STORYBOARDS=pagination_integrity_list_accounts PROTOCOLS=mcp \
#   ADCP_SDK_VERSION=7.11.0 ./scripts/storyboard-check.sh
#
#   # Or run all storyboards resolved from specific specialism bundles:
#   SPECIALISMS=sales-non-guaranteed,signal-owned \
#   EXCLUDED_STORYBOARDS=security_baseline ./scripts/storyboard-check.sh
#
#   # Or restrict report tracks while keeping capability-driven selection:
#   TRACKS=core,media_buy,signals ./scripts/storyboard-check.sh
#
#   # Or production-path mode (no sandbox routing):
#   NO_SANDBOX=1 ./scripts/storyboard-check.sh
#
#   # Local dev (non-HTTPS agent — production must terminate TLS):
#   AGENT_URL=http://localhost:8000 AGENT_TOKEN=ci-test-token \
#   ALLOW_HTTP=1 ./scripts/storyboard-check.sh
#
# Outputs:
#   - One-line summary per transport: passed/failed/skipped + duration
#   - List of failing step IDs with truncated error messages
#   - Per-transport JSON written to $REPORT_DIR/storyboard-<protocol>.json for
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
ADCP_SDK_VERSION="${ADCP_SDK_VERSION:-latest}"
ADCP_SDK_PACKAGE="@adcp/sdk@${ADCP_SDK_VERSION}"
STORYBOARD="${STORYBOARD-}"
STORYBOARDS="${STORYBOARDS:-}"
SPECIALISMS="${SPECIALISMS:-}"
EXCLUDED_STORYBOARDS="${EXCLUDED_STORYBOARDS:-}"
TRACKS="${TRACKS:-}"
PROTOCOLS="${PROTOCOLS:-mcp,a2a}"
TIMEOUT="${TIMEOUT:-180}"
NO_SANDBOX="${NO_SANDBOX:-0}"
ASSERTS_SEEDED_STATE="${ASSERTS_SEEDED_STATE:-0}"
STORYBOARD_SOFT_FAIL="${STORYBOARD_SOFT_FAIL:-0}"
REPORT_DIR="${REPORT_DIR:-/tmp}"
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
if [[ "$ASSERTS_SEEDED_STATE" == "1" ]]; then
    EXTRA_FLAGS+=(--asserts-seeded-state)
fi
if [[ "$STORYBOARD_SOFT_FAIL" == "1" ]]; then
    EXTRA_FLAGS+=(--soft-fail)
fi

mkdir -p "$REPORT_DIR"

if [[ "$PROTOCOLS" == "both" ]]; then
    PROTOCOLS="mcp,a2a"
fi

RESOLVED_STORYBOARDS="$STORYBOARDS"
if [[ -z "$RESOLVED_STORYBOARDS" && -n "$SPECIALISMS" ]]; then
    ids_file="${REPORT_DIR}/resolved-storyboards.txt"
    : >"$ids_file"
    IFS=',' read -ra SPECIALISM_LIST <<< "$SPECIALISMS"
    for specialism in "${SPECIALISM_LIST[@]}"; do
        specialism="${specialism//[[:space:]]/}"
        if [[ -z "$specialism" ]]; then
            continue
        fi
        specialism_json=$(npx -y "$ADCP_SDK_PACKAGE" storyboard show --specialism "$specialism" --json) || {
            echo "✗ Failed to resolve storyboard specialism '$specialism'" >&2
            exit 2
        }
        jq -r '.storyboards[].id' <<<"$specialism_json" >>"$ids_file"
    done

    RESOLVED_IDS=()
    while IFS= read -r storyboard_id; do
        if [[ -z "$storyboard_id" ]]; then
            continue
        fi
        if [[ ",$EXCLUDED_STORYBOARDS," == *",$storyboard_id,"* ]]; then
            continue
        fi
        RESOLVED_IDS+=("$storyboard_id")
    done < <(sort -u "$ids_file")

    if [[ ${#RESOLVED_IDS[@]} -eq 0 ]]; then
        echo "✗ SPECIALISMS did not resolve to any runnable storyboards." >&2
        exit 2
    fi
    RESOLVED_STORYBOARDS=$(IFS=,; echo "${RESOLVED_IDS[*]}")
fi

# ─── Per-transport runner ────────────────────────────────────────────────────

# Run the storyboard against one transport and print a one-line summary plus
# a bulleted list of failing steps. Writes raw JSON to /tmp/storyboard-<proto>.json.
# Sets the global RC_PROTO_<protocol> to 0 (all passed) or 1 (any failure).
run_one() {
    local protocol="$1"
    local url="$2"
    local out="${REPORT_DIR}/storyboard-${protocol}.json"
    local err="${REPORT_DIR}/storyboard-${protocol}.err"
    local summary_out="${REPORT_DIR}/storyboard-${protocol}-summary.json"
    local cmd=(npx -y "$ADCP_SDK_PACKAGE" storyboard run "$url")

    if [[ -n "$RESOLVED_STORYBOARDS" ]]; then
        cmd+=(--storyboards "$RESOLVED_STORYBOARDS")
    elif [[ -n "$STORYBOARD" ]]; then
        cmd+=("$STORYBOARD")
    fi
    if [[ -n "$TRACKS" ]]; then
        cmd+=(--tracks "$TRACKS")
    fi

    cmd+=(
        --auth "$AGENT_TOKEN"
        --protocol "$protocol"
        --json
        --timeout "$TIMEOUT"
        --summary-output "$summary_out"
        "${EXTRA_FLAGS[@]}"
    )

    echo "── ${protocol^^} ──"
    echo "→ ${url}"

    # Capture both streams; the always-on STORYBOARD-FAIL summary lives on
    # stderr; --json puts the full ComplianceResult on stdout so we can
    # post-process skip causes (which the always-on summary doesn't surface
    # — see adcontextprotocol/adcp-client#1623).
    "${cmd[@]}" >"$out" 2>"$err"
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

if [[ -n "$RESOLVED_STORYBOARDS" ]]; then
    echo "Storyboards: $RESOLVED_STORYBOARDS"
elif [[ -n "$STORYBOARD" ]]; then
    echo "Storyboard: $STORYBOARD"
else
    echo "Storyboards: capability-driven"
fi
if [[ -n "$SPECIALISMS" ]]; then
    echo "Specialisms: $SPECIALISMS"
fi
if [[ -n "$EXCLUDED_STORYBOARDS" ]]; then
    echo "Excluded:   $EXCLUDED_STORYBOARDS"
fi
echo "SDK:        $ADCP_SDK_PACKAGE"
if [[ -n "$TRACKS" ]]; then
    echo "Tracks:     $TRACKS"
fi
echo "Protocols:  $PROTOCOLS"
echo "Sandbox:    $([[ $NO_SANDBOX == 1 ]] && echo off || echo on)"
echo "Reports:    $REPORT_DIR"
echo

IFS=',' read -ra PROTOCOL_LIST <<< "$PROTOCOLS"
VALID_PROTOCOLS=()
for protocol in "${PROTOCOL_LIST[@]}"; do
    protocol="${protocol//[[:space:]]/}"
    if [[ -z "$protocol" ]]; then
        continue
    fi
    case "$protocol" in
        mcp|a2a)
            VALID_PROTOCOLS+=("$protocol")
            ;;
        *)
            echo "✗ Unsupported protocol '$protocol' in PROTOCOLS. Use mcp,a2a,both." >&2
            exit 2
            ;;
    esac
done

if [[ ${#VALID_PROTOCOLS[@]} -eq 0 ]]; then
    echo "✗ PROTOCOLS did not include any runnable protocols." >&2
    exit 2
fi

# MCP gets the /mcp path; A2A speaks at the host root per AdCP convention.
for i in "${!VALID_PROTOCOLS[@]}"; do
    protocol="${VALID_PROTOCOLS[$i]}"
    eval "RC_${protocol}=0"
    if [[ "$protocol" == "mcp" ]]; then
        run_one mcp "${AGENT_BASE}/mcp" || true
    else
        run_one a2a "${AGENT_BASE}/" || true
    fi

    # Storyboard scenarios mutate state (update_swap_lists, etc.) and reuse
    # idempotency keys per scenario. Running MCP and A2A back-to-back against
    # the same DB causes the second protocol's idempotency replay to return
    # the cached create response while reading the post-update DB state.
    # Set BETWEEN_PROTOCOLS_HOOK to a shell command that resets the seller's
    # storage between protocol runs.
    if [[ "$i" -lt $((${#VALID_PROTOCOLS[@]} - 1)) && -n "${BETWEEN_PROTOCOLS_HOOK:-}" ]]; then
        echo "── Reset between protocols ──"
        eval "$BETWEEN_PROTOCOLS_HOOK" || echo "  (hook returned non-zero; continuing)"
        echo
    fi
done

echo "── Result ──"
OVERALL_RC=0
for protocol in "${VALID_PROTOCOLS[@]}"; do
    eval "rc=\$RC_${protocol}"
    echo "${protocol^^}: $([[ $rc == 0 ]] && echo PASS || echo FAIL)"
    if [[ "$rc" != 0 ]]; then
        OVERALL_RC=1
    fi
done

if [[ $OVERALL_RC != 0 && "$STORYBOARD_SOFT_FAIL" == "1" ]]; then
    echo "Soft fail enabled; returning success despite storyboard failures."
    exit 0
fi

if [[ $OVERALL_RC != 0 ]]; then
    exit 1
fi
