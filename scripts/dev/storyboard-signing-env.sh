#!/usr/bin/env bash
#
# The storyboard agent's one signed-requests test-kit setting, exported: the
# pre-revoked keyid (ADCP_SIGNING_REVOKED_KEYIDS, vector 017).
#
# SOURCE THIS, never execute it: the whole point is to put variables into the
# environment `docker compose` interpolates from. Both bring-up paths call it, for
# the same reason they both call ensure-test-tls.sh:
#   scripts/test-stack.sh cmd_up       (host stack)
#   run_all_tests.sh                   (in-network stack)
#
# It must run at BRING-UP, not with the other storyboard seeding in tox: this is a
# setting the server process reads from its own environment at boot
# (src/core/config.py SigningSettings), so a value produced after the container
# started reaches nothing. The database half of the same contract (the test
# counterparty's grading record and its onboarded keyids) IS seeded later, from [testenv:storyboard], because the
# database can be written at any time.
#
# The value is DERIVED by scripts/setup/storyboard_signing.py from the test kit.
#
# A failure here is FATAL rather than silent. Without it vector 017's revoked key is
# not refused at checklist step 9, and the vector grades as a verifier defect.

_sb_root="$( cd "$( dirname "${BASH_SOURCE[0]}" )/../.." && pwd )"

# stdlib only, so a bare python3 is enough (the in-network runner has no uv on PATH —
# ensure-test-tls.sh documents the same constraint).
_sb_python=(python3)
command -v uv >/dev/null 2>&1 && _sb_python=(uv run python)

if ! _sb_env="$( cd "$_sb_root" && "${_sb_python[@]}" -m scripts.setup.storyboard_signing --env )"; then
    echo "storyboard-signing-env.sh: could not derive the signed-requests test-kit settings" >&2
    unset _sb_root _sb_python
    return 1 2>/dev/null || exit 1
fi

while IFS= read -r _sb_setting; do
    [ -n "$_sb_setting" ] && export "${_sb_setting?}"
done <<< "$_sb_env"

unset _sb_root _sb_python _sb_env _sb_setting
