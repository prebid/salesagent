#!/bin/bash
# Mutation check: does the e2e_rest circuit-breaker scenario actually grade the
# server's breaker? (#2060)
#
# WHY THIS EXISTS. @T-UC-004-webhook-circuit-open claims to prove that the
# DEPLOYED server opens its circuit breaker after repeated webhook delivery
# failures. That claim is a cross-process property, and no artifact inside the
# test process can establish it. Worse, the scenario passed for years while
# proving nothing: it seeded a breaker in the TEST process and read the same
# object back, and a non-strict xfail made the result unreportable either way.
#
# The only instrument that can tell a real grade from a vacuous one is a
# mutation: break the production line the scenario claims to depend on, and see
# whether the scenario notices. If it stays green, the scenario is decoration —
# whatever its wording says, and however many assertions it carries.
#
#   Baseline  -> the e2e_rest leg of the OPEN scenario must PASS
#   Mutant    -> with circuit_breaker.record_failure() deleted, it must FAIL
#   Restore   -> the source is put back, always, including on interrupt
#
# A green baseline alone proves nothing. A red mutant alone proves nothing.
# Only the PAIR is evidence, which is why this script insists on both.
#
# WHY A SCRIPT, NOT A TEST. Three reasons, all structural:
#
#   1. It has to run pytest twice against two different builds of the server.
#      A pytest test cannot rebuild the image it is running against.
#   2. src/ is baked into the adcp-server image — `build: .` in
#      docker-compose.e2e.yml with NO bind mount (webhook-capture and tests
#      mount `.:/app`; adcp-server deliberately does not). So the mutation only
#      reaches the server through an image rebuild, which makes this gate far
#      too expensive to sit in any default suite.
#   3. A script cannot be swept into a suite by collection. There is no marker
#      to forget and no `-m` expression that accidentally picks it up.
#
# It is therefore EXPLICITLY INVOKED — `make mutation-check-breaker`, or this
# script directly — and never part of `make quality` or `./run_all_tests.sh`.
#
# NO SILENT DEGRADATION. Every precondition this script cannot meet is a loud
# non-zero exit naming what was missing. There is no skip path, no reduced
# assertion, and no "stack unavailable, assuming fine": a check that cannot
# reach the thing it grades has FAILED, it has not been excused. That rule is
# the general form of the disease this script exists to detect.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

RED='\033[0;31m' GREEN='\033[0;32m' BLUE='\033[0;34m' YELLOW='\033[0;33m' NC='\033[0m'

# The production line under test, and the leg that must notice its absence.
MUTATION_FILE="src/services/webhook_delivery_service.py"
MUTATION_CALL="circuit_breaker.record_failure()"
GRADED_NODE_ID="tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_persistent_webhook_failures_open_circuit_breaker[e2e_rest]"

# bdd_e2e is the ONLY suite that grades e2e_rest: E2E_BASE_URL / E2E_POSTGRES_URL
# are set exclusively on the in-network `tests` service (docker-compose.e2e.yml),
# so the host runner cannot reach the live server as that transport at all.
# MUTATION_RUNNER may be any command that runs the bdd_e2e suite in-network AND
# leaves a fresh test-results/innet_*/bdd_e2e.json behind — that report, not the
# runner's exit code, is what this gate reads. A runner that executes elsewhere
# must therefore pull its results back before returning; wrap it if it does not:
#   MUTATION_RUNNER=/path/to/offload-wrapper scripts/mutation-check-webhook-breaker.sh
# where the wrapper is `cassini run --nodetach "$@" ; cassini fetch`.
MUTATION_RUNNER="${MUTATION_RUNNER:-./run_all_tests.sh}"
REPORT_GLOB="test-results/innet_*/bdd_e2e.json"

die() { echo -e "${RED}MUTATION CHECK FAILED — $*${NC}" >&2; exit 1; }

# ── Restore is unconditional ─────────────────────────────────────────────────
# Registered before the first mutation and never disarmed, so a Ctrl-C, a failed
# build or an unexpected `set -e` exit all put the source back. A mutation check
# that can leave production code deleted is a liability, not a gate.
BACKUP=""
restore_source() {
    [ -n "$BACKUP" ] && [ -f "$BACKUP" ] || return 0
    cp "$BACKUP" "$MUTATION_FILE"
    rm -f "$BACKUP"
    BACKUP=""
    if ! grep -qF "$MUTATION_CALL" "$MUTATION_FILE"; then
        echo -e "${RED}RESTORE FAILED: $MUTATION_CALL is missing from $MUTATION_FILE.${NC}" >&2
        echo -e "${RED}  Recover it with: git checkout -- $MUTATION_FILE${NC}" >&2
        return 1
    fi
    echo -e "${BLUE}Restored $MUTATION_FILE${NC}"
}
trap restore_source EXIT INT TERM

# ── Preflight ────────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 \
    || die "docker is not on PATH. This gate needs the in-network compose stack; it cannot be approximated locally."

# HEAD, not the index: a STAGED edit is uncommitted too, and `git diff` alone
# does not see one — which is exactly the case where the restore below would
# silently throw work away.
git diff --quiet HEAD -- "$MUTATION_FILE" \
    || die "$MUTATION_FILE has uncommitted changes. Refusing to mutate a dirty file — the restore would overwrite your edits."

grep -qF "$MUTATION_CALL" "$MUTATION_FILE" \
    || die "'$MUTATION_CALL' is not present in $MUTATION_FILE. The mutation target moved; update MUTATION_CALL to the line the scenario now depends on."

# ── The one place that decides what a run said ───────────────────────────────
# Reads the outcome the graded node id ACTUALLY got out of the run's own JSON
# report. Never infers it from the runner's exit code: a suite exits non-zero
# for any failure anywhere, and exits zero on an xfail — both of which would
# make this gate report the opposite of the truth.
outcome_of_graded_node() {
    local report="$1"
    uv run python - "$report" "$GRADED_NODE_ID" <<'PYEOF'
import json, sys
report_path, node_id = sys.argv[1], sys.argv[2]
with open(report_path) as fh:
    report = json.load(fh)
for test in report.get("tests", []):
    if test["nodeid"] == node_id:
        print(test["outcome"])
        break
else:
    print("ABSENT")
PYEOF
}

newest_report() {
    # shellcheck disable=SC2086
    ls -1dt $REPORT_GLOB 2>/dev/null | head -1
}

# Stdout carries ONLY the report path, so the caller can capture it. Everything
# else — including the runner's own output, which must stay visible live — goes
# to stderr; a run swallowed into a command substitution is a run nobody can
# watch, and this gate takes tens of minutes.
run_bdd_e2e() {
    local phase="$1"
    echo -e "${BLUE}[$phase] $MUTATION_RUNNER bdd_e2e${NC}" >&2
    local before after
    before="$(newest_report || true)"
    # The runner's exit code is deliberately ignored — the mutant phase EXPECTS a
    # failing suite. The report below is the authority on what happened.
    $MUTATION_RUNNER bdd_e2e >&2 || true
    after="$(newest_report || true)"
    [ -n "$after" ] || die "[$phase] no $REPORT_GLOB was produced. The in-network run did not complete; this check cannot grade e2e_rest without it."
    [ "$after" != "$before" ] || die "[$phase] no NEW $REPORT_GLOB appeared (newest is still '$before'). Refusing to grade this phase against a stale report."
    echo "$after"
}

# ── Phase 1: baseline ────────────────────────────────────────────────────────
echo -e "${YELLOW}=== Phase 1/2: baseline (unmutated source) ===${NC}"
BASELINE_REPORT="$(run_bdd_e2e baseline)"
BASELINE_OUTCOME="$(outcome_of_graded_node "$BASELINE_REPORT")"
echo -e "${BLUE}baseline outcome: $BASELINE_OUTCOME  ($BASELINE_REPORT)${NC}"

case "$BASELINE_OUTCOME" in
    passed) ;;
    ABSENT)  die "the graded node id did not run in the baseline:\n  $GRADED_NODE_ID\nAn absent test grades nothing. Check that the scenario is still parametrized over e2e_rest." ;;
    xpassed|xfailed)
             die "the graded leg is still xfail-routed (outcome '$BASELINE_OUTCOME'). Under a non-strict xfail both a pass and a failure report green, so the mutant below could not redden even if the scenario were perfect. Remove the tag from _UC004_E2E_WEBHOOK_INTERNAL_TAGS in tests/bdd/conftest.py first." ;;
    *)       die "the baseline is '$BASELINE_OUTCOME', not 'passed'. Fix the scenario before asking whether it is vacuous — a mutation check on a red baseline says nothing." ;;
esac

# ── Phase 2: mutant ──────────────────────────────────────────────────────────
echo -e "${YELLOW}=== Phase 2/2: mutant (${MUTATION_CALL} deleted) ===${NC}"
BACKUP="$(mktemp)"
cp "$MUTATION_FILE" "$BACKUP"
# Delete only the bare call statement, so the mutation is exactly "the server
# stops recording delivery failures against its breaker" and nothing else.
uv run python - "$MUTATION_FILE" "$MUTATION_CALL" <<'PYEOF'
import sys
path, call = sys.argv[1], sys.argv[2]
with open(path) as fh:
    lines = fh.readlines()
kept = [line for line in lines if line.strip() != call]
removed = len(lines) - len(kept)
if removed != 1:
    sys.exit(f"expected exactly 1 bare '{call}' statement, deleted {removed}")
with open(path, "w") as fh:
    fh.writelines(kept)
PYEOF
echo -e "${BLUE}Deleted $MUTATION_CALL from $MUTATION_FILE${NC}"
echo -e "${BLUE}The runner rebuilds the adcp-server image (run_all_tests.sh: 'dc build ... adcp-server ...'),${NC}"
echo -e "${BLUE}so the mutation reaches the server despite src/ being baked into the image.${NC}"

MUTANT_REPORT="$(run_bdd_e2e mutant)"
MUTANT_OUTCOME="$(outcome_of_graded_node "$MUTANT_REPORT")"
echo -e "${BLUE}mutant outcome: $MUTANT_OUTCOME  ($MUTANT_REPORT)${NC}"

restore_source

# ── Verdict ──────────────────────────────────────────────────────────────────
if [ "$MUTANT_OUTCOME" = "failed" ]; then
    echo -e "${GREEN}MUTATION CHECK PASSED${NC}"
    echo -e "${GREEN}  baseline: passed   mutant: failed${NC}"
    echo -e "${GREEN}  The scenario noticed the server losing $MUTATION_CALL, so it is grading the${NC}"
    echo -e "${GREEN}  deployed server's breaker and not an object the test process made for itself.${NC}"
    exit 0
fi

die "THE MUTANT SURVIVED (outcome '$MUTANT_OUTCOME').
  $GRADED_NODE_ID
  passed with $MUTATION_CALL deleted from the server, which means it does not
  depend on that line and cannot be evidence that the breaker records failures.
  The scenario is vacuous on e2e_rest whatever its wording claims. Reports:
    baseline: $BASELINE_REPORT
    mutant:   $MUTANT_REPORT"
