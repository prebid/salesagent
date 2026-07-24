# shellcheck shell=bash
# Shared gh API retry helper for IPR Agreement workflow jobs.
# Source from a workflow run step after checkout: `. scripts/ci/ipr_gh_retry.sh`
#
# Captures each attempt privately and promotes only on success so failed
# Unicorn/HTML stdout cannot concatenate into the destination.

# Fetch signatures + PR commits into ``$1`` (tmp dir). Args: tmp, repository, pr number.
# Writes ``sigs.json`` and ``commits.json``. Local names avoid SC2153 vs env REPO/PR_NUMBER.
ipr_fetch_sigs_and_commits() {
  local tmp="${1:?tmp dir required}"
  local repository="${2:?repository required}"
  local pull_number="${3:?pull number required}"

  echo "Fetching signatures from ipr-signatures branch..."
  gh_retry_to "${tmp}/sigs.b64" gh api \
    "repos/${repository}/contents/signatures/ipr-signatures.json?ref=ipr-signatures" \
    --jq .content
  tr -d '\n' < "${tmp}/sigs.b64" | base64 --decode > "${tmp}/sigs.json"

  echo "Fetching PR commit authors..."
  gh_retry_to "${tmp}/commits.json" gh api --paginate \
    "repos/${repository}/pulls/${pull_number}/commits"
}

# Shared verify orchestration for ipr-check / ipr-sign / ci.yml ipr-gate.
# Args: repository, pull number, pr author login; remaining args forwarded to
# ``ipr_verify.py verify`` (e.g. Path B ``--missing-message`` / ``--ok-message``).
# Local ``author_login`` avoids SC2153 vs env PR_AUTHOR (same pattern as
# repository/pull_number vs REPO/PR_NUMBER above).
ipr_verify_pr() {
  local repository="${1:?repository required}"
  local pull_number="${2:?pull number required}"
  local author_login="${3:?pr author required}"
  shift 3
  local tmp rc=0
  tmp="$(mktemp -d)"
  if ! ipr_fetch_sigs_and_commits "${tmp}" "${repository}" "${pull_number}"; then
    rm -rf "${tmp}"
    return 1
  fi
  python3 scripts/ci/ipr_verify.py verify \
    --sigs "${tmp}/sigs.json" \
    --commits "${tmp}/commits.json" \
    --pr-author "${author_login}" \
    "$@" || rc=$?
  rm -rf "${tmp}"
  return "${rc}"
}

gh_retry_to() {
  local dest="$1"
  shift
  local attempt=1
  local max=5
  local sleep_s
  local rc
  local attempt_out
  attempt_out="$(mktemp)"
  while true; do
    set +e
    "$@" >"${attempt_out}" 2>/tmp/ipr_gh_retry_err
    rc=$?
    set -e
    if [ "${rc}" -eq 0 ]; then
      mv "${attempt_out}" "${dest}"
      return 0
    fi
    rm -f "${attempt_out}"
    if [ "${attempt}" -ge "${max}" ]; then
      echo "command failed after ${max} attempts: $*" >&2
      head -c 200 /tmp/ipr_gh_retry_err >&2 || true
      return 1
    fi
    sleep_s=$((attempt * 15))
    echo "attempt ${attempt}/${max} failed; retrying in ${sleep_s}s..." >&2
    sleep "${sleep_s}"
    attempt=$((attempt + 1))
    attempt_out="$(mktemp)"
  done
}
