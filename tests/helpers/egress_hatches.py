"""The outbound escape hatch, spelled in exactly one place.

``src/core/security/outbound_http.py`` reads an environment variable to decide
whether the seam's default posture — no private/reserved destination addresses
— may be relaxed for a test that needs a loopback origin. Three test surfaces
need to set it: the seam's own suite (``set_flags``), the webhook-delivery envs
that run a real local origin (``LocalOriginMixin``), and the BDD env that
grades a refusal (``RealResolverProductEnv``).

Three spellings of the same value is three places to get it wrong — so the
name, and the ``"true"``/``"false"`` literal the repo's ``== "true"``
convention reads, live here and nowhere else.

The value is ALWAYS written, including the off case: a hatch left unset is a
hatch decided by whatever exported it into the shell, which is how a refusal
test gets silently disarmed.

``ADCP_OUTBOUND_ALLOW_INSECURE`` (GH #1802): production no longer reads
this at all — ``_require_tls``/``_require_https`` are unconditional. There is
therefore no ``insecure`` parameter here anymore; a caller cannot relax the
scheme gate through this helper because there is nothing left to relax.
``ALLOW_INSECURE_ENV`` is kept ONLY as a literal for tests proving the (now
inert) env var has no effect — see
``tests/integration/test_outbound_allow_insecure_gate_removal.py``.
"""

from __future__ import annotations

ALLOW_PRIVATE_ENV = "ADCP_OUTBOUND_ALLOW_PRIVATE"

# An https public-unicast IP LITERAL, used as a destination that is deliberately
# never dialled. It lives beside the hatch names because the reason is about hatch
# posture: an IP literal passes under EVERY posture without resolving DNS, so a test
# that only needs "a syntactically valid, non-private, non-loopback destination"
# gets one without doing live DNS (a hostname would make the test NXDOMAIN-refuse,
# or worse, actually resolve). 1.1.1.1 specifically is a well-known public resolver
# address that no test suite should ever connect to — the choice is deliberate.
#
# Only the ORIGIN is shared: callers append their own path, because the path is the
# part each site varies (and in uc004 the host is the only variable under test,
# deliberately parallel to its BLOCKED_WEBHOOK_URL sibling).
UNDIALLED_PUBLIC_HTTPS_ORIGIN = "https://1.1.1.1"
ALLOW_INSECURE_ENV = "ADCP_OUTBOUND_ALLOW_INSECURE"


def egress_hatch_env(*, private: bool) -> dict[str, str]:
    """Return the environment mapping that sets the private-range hatch explicitly.

    Args:
        private: Allow reserved/private-range destination addresses.

    Returns:
        A ``{name: "true"|"false"}`` mapping suitable for ``patch.dict(os.environ, ...)``
        or for feeding ``monkeypatch.setenv`` item by item.
    """
    return {
        ALLOW_PRIVATE_ENV: "true" if private else "false",
    }
