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
The rendered HTML is parsed by the reader the admin page tests use
(``tests/helpers/channel_picker.py``), so a hand-added literal option is observed whatever its
attribute order or quoting; the last test adds one in each spelling and watches the comparison
fail.
"""

from __future__ import annotations

import pytest
from adcp.types._generated import MediaChannel
from jinja2 import Environment, FileSystemLoader

from tests.helpers.channel_picker import picker_options
from tests.unit._architecture_helpers import REPO_ROOT

pytestmark = pytest.mark.arch_guard

_TEMPLATES = REPO_ROOT / "templates"
_COMPONENT = "components/channel_selector.html"
_ENUM_VALUES = [member.value for member in MediaChannel]
_OPTIONS = [(member.value, member.value) for member in MediaChannel]


def _render(selected: list[str], *, source: str | None = None) -> list[tuple[str, bool]]:
    """``(value, selected)`` per rendered ``<option>`` of the component, or of *source*."""
    env = Environment(loader=FileSystemLoader(_TEMPLATES), autoescape=True)
    template = env.get_template(_COMPONENT) if source is None else env.from_string(source)
    return picker_options(template.render(media_channel_options=_OPTIONS, selected_channels=selected))


def _offered(source: str | None = None) -> list[str]:
    """What the guard compares with the enum: every rendered option's value, in order."""
    return [value for value, _ in _render([], source=source)]


def test_rendered_option_values_are_exactly_the_media_channel_enum_in_order():
    """List equality: an extra literal, a missing member or a duplicate all fail."""
    assert _offered() == _ENUM_VALUES


@pytest.mark.parametrize("member", list(MediaChannel), ids=lambda m: m.value)
def test_rendering_preselects_every_declared_media_channel(member: MediaChannel):
    """The edit form can only pre-select a channel the picker offers."""
    assert [value for value, selected in _render([member.value]) if selected] == [member.value]


@pytest.mark.parametrize(
    "literal",
    [
        '<option value="video">Video</option>',
        '<option selected value="video">Video</option>',
        '<option class="x" value="video">Video</option>',
        "<option value='video'>Video</option>",
    ],
    ids=["plain", "selected-first", "attribute-first", "single-quoted"],
)
def test_a_hand_added_option_fails_the_comparison_whatever_its_spelling(literal: str):
    """A guard must be seen to fail: ``video`` is outside the enum, so one added option breaks the equality."""
    component = (_TEMPLATES / _COMPONENT).read_text(encoding="utf-8")
    source = component.replace("</select>", f"{literal}\n</select>", 1)
    assert source != component, "the component has no </select> to add an option before"

    offered = _offered(source)

    assert "video" in offered
    assert offered != _ENUM_VALUES
