"""Write-only secret fields on admin forms: the one keep / clear / set decision.

A stored credential (a Slack webhook URL, an agent credential) is never rendered back into
its form (``templates/components/secret_input.html``), so a blank field means "keep what is
saved", and removing it takes the field's explicit ``clear_<name>`` checkbox.
"""

from collections.abc import Mapping
from enum import Enum


class Keep(Enum):
    """The submitted form leaves the stored secret as it is."""

    KEEP = "keep"


KEEP = Keep.KEEP


def submitted_secret(form: Mapping[str, str], name: str) -> str | None | Keep:
    """What to store for secret field *name*: a new value, ``None`` to clear it, or ``KEEP``.

    The ``clear_<name>`` checkbox wins over a typed value, so a form that both clears and
    types does not silently keep the old secret.
    """
    if form.get(f"clear_{name}") == "on":
        return None
    value = (form.get(name) or "").strip()
    return value or KEEP
