#!/usr/bin/env bash
#
# The storyboard agent's signed-requests test-kit configuration, exported.
#
# SOURCE THIS, never execute it: the whole point is to put variables into the
# environment `docker compose` interpolates from. Both bring-up paths call it, for
# the same reason they both call ensure-test-tls.sh:
#   scripts/test-stack.sh cmd_up       (host stack)
#   run_all_tests.sh                   (in-network stack)
#
# It must run at BRING-UP, not with the other storyboard seeding in tox: these are
# settings the server process reads from its own environment at boot
# (src/core/config.py SigningSettings), so a value produced after the container
# started reaches nothing. The tenant half of the same contract IS seeded later, from
# [testenv:storyboard], because the database can be written at any time.
#
# Values are DERIVED by scripts/setup/storyboard_signing.py from the vendored
# conformance keys and the test-kit's own thresholds — see that module for what each
# one satisfies.
#
# A failure here is FATAL rather than silent. Without these the storyboard agent
# trusts no counterparty, and every signed vector is refused at checklist step 7 with
# request_signature_key_unknown — 20+ graded checks failing for a reason that looks
# like a verifier bug and is actually a missing export.

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
