"""A service-account document the google auth library can actually parse.

WHY THIS EXISTS. A GAM adapter builds its credentials when it is CONSTRUCTED. It used to
skip that whenever ``dry_run`` was set, and no adapter carries that flag any more (commit
a1b79d22d removed the testing-hook channel), so every test that constructs a GAM adapter
now deserializes the key for real: a truncated placeholder PEM, or ``"{}"``, fails inside
``google.auth`` rather than in the code under test.

The key is generated in this process and reaches no network -- nothing here authenticates.
One home for it because two suites need the same document (adapter construction and the
OAuth/service-account config tests), and CLAUDE.md § DRY treats the second copy as a defect.
"""

from __future__ import annotations

import json
from functools import lru_cache


@lru_cache(maxsize=1)
def throwaway_private_key() -> str:
    """A PEM-encoded RSA private key, generated once per process."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def service_account_json(*, project_id: str = "test-project") -> str:
    """The JSON string a GAM adapter's ``service_account_json`` config takes."""
    return json.dumps(
        {
            "type": "service_account",
            "project_id": project_id,
            "private_key_id": "key123",
            "private_key": throwaway_private_key(),
            "client_email": f"test@{project_id}.iam.gserviceaccount.com",
            "client_id": "123456789",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
