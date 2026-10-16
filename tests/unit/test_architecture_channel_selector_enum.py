"""Guard: the admin channel picker offers exactly the pinned ``MediaChannel`` enum.

``templates/components/channel_selector.html`` once hard-coded nine ``<option>`` values,
four of which (``video``, ``audio``, ``native``, ``retail``) were not members of the AdCP
``MediaChannel`` enum, and fifteen members were missing. A product tagged with one of the
four had the channel dropped on read (``product_conversion.py`` logs and skips an unknown
string), so ``get_products`` matched it on adapter defaults while the edit form kept showing
the stale value as selected (GH #1532). Nothing parsed the template, which is how it shipped.

The component is rendered here with a bare Jinja environment and the ``media_channel_options``
context the picker iterates, built from the enum IN THIS TEST rather than imported from the
production helper: the invariant graded is "the template emits what it is given and nothing
else", and the helper that supplies the list is graded by ``test_media_channel_options.py``.
A hand-added literal option shows up in the rendered list whatever its spelling, which is why
there is no regex over the template source.
"""

from __future__ import annotations

import re

import pytest
from adcp.types._generated import MediaChannel
from jinja2 import Environment, FileSystemLoader

from tests.unit._architecture_helpers import REPO_ROOT

pytestmark = pytest.mark.arch_guard

_COMPONENT = "components/channel_selector.html"
_OPTION = re.compile(r'<option\s+value="([^"]+)"([^>]*)>')
_ENUM_VALUES = [member.value for member in MediaChannel]
_OPTIONS = [(member.value, member.value) for member in MediaChannel]


def _render(selected: list[str], *, source: str | None = None) -> list[tuple[str, str]]:
    """``(value, attributes)`` per rendered ``<option>`` of the component, or of *source*."""
    env = Environment(loader=FileSystemLoader(REPO_ROOT / "templates"), autoescape=True)
    template = env.get_template(_COMPONENT) if source is None else env.from_string(source)
    return _OPTION.findall(template.render(media_channel_options=_OPTIONS, selected_channels=selected))


def test_rendered_option_values_are_exactly_the_media_channel_enum_in_order():
    """List equality: an extra literal, a missing member or a duplicate all fail."""
    assert [value for value, _ in _render([])] == _ENUM_VALUES


@pytest.mark.parametrize("member", list(MediaChannel), ids=lambda m: m.value)
def test_rendering_preselects_every_declared_media_channel(member: MediaChannel):
    """The edit form can only pre-select a channel the picker offers."""
    pairs = _render([member.value])
    assert [value for value, attrs in pairs if "selected" in attrs] == [member.value]


class TestTheRenderSeesALiteralOption:
    """A guard must be seen to fail: a literal option is observed, and it is not a member."""

    def test_a_hardcoded_option_is_observed_by_value(self):
        assert [value for value, _ in _render([], source='<option value="video">Video</option>')] == ["video"]

    def test_the_observed_literal_is_outside_the_enum(self):
        assert "video" not in _ENUM_VALUES
