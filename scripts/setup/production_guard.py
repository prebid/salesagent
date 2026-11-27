"""The one production refusal for the setup scripts that write repository-published credentials.

``seed_storyboard_tenant`` and ``init_database_ci`` create principals whose bearer tokens are
written in this repository, and ``storyboard_signing`` makes a principal trust the
signed-requests test kit's published keys. On a test stack that is the fixture; on a
deployment it is a credential anyone can read. Each script refuses through this function
before it touches the database, and each script's composition root calls ``load_settings()``
first, so the answer is ``RuntimeSettings.is_production``: the settings loader's one predicate
for ``PRODUCTION``, ``ENVIRONMENT=production`` and ``FLY_APP_NAME``.
"""

from __future__ import annotations


def refuse_on_production(action: str, consequence: str) -> None:
    """Raise ``SystemExit`` naming *action* and *consequence* when this is a production deployment."""
    from src.core.config import get_settings

    if get_settings().runtime.is_production:
        raise SystemExit(
            f"Refusing to {action} on a production deployment "
            f"(PRODUCTION, ENVIRONMENT=production or FLY_APP_NAME is set): {consequence}."
        )
