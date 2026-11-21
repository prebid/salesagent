"""Guard: no template renders a stored ad-server refresh token.

The GAM refresh token is a credential to the publisher's ad server. It used to be
rendered into the settings page (a hidden input and the manual-entry field's value),
so anyone who could open the page could copy it, including someone who got there
through a forged request. The page now only learns whether a token is stored, and the
GAM endpoints use the stored token when the page sends a blank one.

This scans every template for a Jinja output expression (``{{ ... }}``) that would
print ``refresh_token``. Naming it in the condition of a conditional expression, to
choose what to print, is fine.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.unit._architecture_helpers import rel

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
OUTPUT_EXPRESSION = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
CONDITIONAL = re.compile(r"^(?P<value>.*?)\bif\b(?P<condition>.*?)\belse\b(?P<otherwise>.*)$", re.DOTALL)


def _prints_refresh_token(expression: str) -> bool:
    conditional = CONDITIONAL.match(expression)
    if conditional is None:
        return "refresh_token" in expression
    return "refresh_token" in conditional["value"] or "refresh_token" in conditional["otherwise"]


def rendered_refresh_tokens(text: str) -> list[str]:
    """The output expressions in *text* that would print a refresh token."""
    return [
        match.group(1).strip() for match in OUTPUT_EXPRESSION.finditer(text) if _prints_refresh_token(match.group(1))
    ]


def test_no_template_renders_a_refresh_token():
    offenders = {
        rel(path, TEMPLATES): rendered
        for path in sorted(TEMPLATES.rglob("*.html"))
        if (rendered := rendered_refresh_tokens(path.read_text(encoding="utf-8")))
    }
    assert not offenders, f"templates print a stored refresh token: {offenders}"


def test_the_scan_catches_the_forms_the_token_used_to_take():
    """The two shapes the settings page used before the fix, and the placeholder it uses now."""
    assert rendered_refresh_tokens("""<input type="hidden" value="{{ adapter_config.get('refresh_token') }}">""")
    assert rendered_refresh_tokens(
        """value="{{ adapter_config.get('refresh_token', '') if adapter_config else '' }}" """
    )
    assert rendered_refresh_tokens("""{{ 'saved' if refresh_token else adapter_config.refresh_token }}""")
    assert not rendered_refresh_tokens(
        """placeholder="{{ 'Saved.' if adapter_config and adapter_config.get('refresh_token') else 'Paste' }}" """
    )
