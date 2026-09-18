#!/usr/bin/env python3
"""Provision an RFC 9421 signing key for a tenant (#1291, salesagent-7x8t).

The scripted transport over ``src.core.signing.keys.provision_signing_key`` — the
SAME function ``src/admin/blueprints/signing_keys.py`` calls. Two thin transports
over one implementation; a second copy of provisioning logic here would be the
duplication class this repo blocks PRs for.

The public half of the minted key is published at the tenant's
``/.well-known/jwks.json`` the moment the row exists, so a tenant with no routable
``virtual_host``/``subdomain`` has a key nobody can fetch. Provisioning does not
make a host route.

Usage::

    uv run python scripts/ops/provision_signing_key.py --tenant-id publisher_1
    uv run python scripts/ops/provision_signing_key.py --tenant-id publisher_1 \\
        --ref-scheme env --env-var-name ADCP_SIGNING_KEY_PEM
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core.database.repositories.uow import SigningKeyUoW  # noqa: E402
from src.core.exceptions import AdCPConfigurationError  # noqa: E402
from src.core.signing.algorithms import SIGNING_ALG_VALUES  # noqa: E402
from src.core.signing.keys import MINTABLE_REF_SCHEMES, provision_signing_key  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant-id", required=True, help="Tenant the key belongs to")
    parser.add_argument(
        "--alg",
        default="ed25519",
        choices=SIGNING_ALG_VALUES,
        help="RFC 9421 signature algorithm (default: ed25519)",
    )
    parser.add_argument(
        "--ref-scheme",
        default="db",
        choices=MINTABLE_REF_SCHEMES,
        help=(
            "Where the private half lives. db (default): encrypted on the key's own row, under the "
            "deployment KEK. env: handed back once for the operator to export"
        ),
    )
    parser.add_argument(
        "--env-var-name",
        default=None,
        help="Environment variable the operator will export the PEM as (required for --ref-scheme env)",
    )
    return parser


def _operator_reason(exc: AdCPConfigurationError) -> str:
    """Render a refusal for the operator standing at the terminal.

    ``str(exc)`` is CODE_TABLE's generic ``CONFIGURATION_ERROR`` sentence and names
    no knob: no raise site authors that text, so interpolating the exception would
    print the same line for a missing KEK and for a forbidden ref scheme. The
    actionable facts ride the structured channels instead — ``field``, the
    ``ConfigurationDetails`` axes (``tracked_by`` carries the knob name,
    ``rejected_value`` the offending input) and ``internal_detail`` for a cause —
    so all three are read here.
    """
    parts = [str(exc)]
    if exc.field is not None:
        parts.append(f"field={exc.field}")
    if exc.details is not None:
        parts.extend(f"{axis}={value}" for axis, value in exc.details.to_wire().items())
    if exc.internal_detail is not None:
        parts.append(f"cause: {exc.internal_detail}")
    return "; ".join(parts)


def main(argv: list[str] | None = None) -> int:
    """Provision one signing key. Returns a process exit code.

    A configuration refusal (no KEK, a scheme this deployment forbids) exits
    non-zero naming the knob to set — never a traceback, because the operator's
    next action is a setting, not a bug report. The knob reaches the terminal
    through :func:`_operator_reason`, not through the exception's text.
    """
    args = _parser().parse_args(argv)

    try:
        with SigningKeyUoW(args.tenant_id) as uow:
            provisioned = provision_signing_key(
                uow.signing_keys,
                tenant_id=args.tenant_id,
                alg=args.alg,
                ref_scheme=args.ref_scheme,
                env_var_name=args.env_var_name,
            )
            kid = provisioned.row.kid
            handoff = provisioned.private_key_pem
    except AdCPConfigurationError as exc:
        print(f"Could not provision a signing key for {args.tenant_id}: {_operator_reason(exc)}", file=sys.stderr)
        return 1

    print(f"Provisioned signing key {kid} for tenant {args.tenant_id} ({args.alg}, {args.ref_scheme} storage).")
    if handoff is not None:
        # Printed ONCE and stored nowhere. Until the process running the signer
        # has this exported, the published JWK has no resolvable private half.
        print(f"Export this PEM as {args.env_var_name} before the key signs anything:", file=sys.stderr)
        print(handoff.decode(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
