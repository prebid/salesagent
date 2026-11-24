"""The edit-product pages carry the stored targeting template in a hidden input, as JSON.

Written as ``value="{{ ...|tojson }}"``, a JSON string's ``"`` ended the attribute: a real
template reached the form as ``{`` (so saving the page dropped it), and a value holding
``" onfocus="...`` added an attribute. The input's own markup, taken from each template and
rendered by the admin app's Jinja environment, must give back exactly the stored dict.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

import pytest

from src.admin.app import create_app
from tests.unit._architecture_helpers import repo_root

_TEMPLATE = {
    "key_value_pairs": {"genre": ["action", 'say "hi"'], "x": ["</script><b>", "it's"]},
    "note": '" autofocus onfocus="alert(1)',
}


class _Inputs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attrs: list[dict[str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.attrs.append(dict(attrs))


_ENV = create_app({"TESTING": True, "SECRET_KEY": "test-secret"}).jinja_env


def _render_input(template_path: str, product: dict) -> dict[str, str | None]:
    source = (repo_root() / template_path).read_text(encoding="utf-8")
    (tag,) = re.findall(r'<input[^>]*id="targeting-data"[^>]*>', source)
    env = _ENV
    parser = _Inputs()
    parser.feed(env.from_string(tag).render(product=product))
    (attrs,) = parser.attrs
    return attrs


@pytest.mark.parametrize("template_path", ["templates/edit_product.html", "templates/edit_product_mock.html"])
def test_the_hidden_input_round_trips_the_stored_targeting_template(template_path):
    attrs = _render_input(template_path, {"targeting_template": _TEMPLATE})
    assert set(attrs) == {"type", "id", "name", "value"}
    assert json.loads(attrs["value"] or "") == _TEMPLATE


@pytest.mark.parametrize("template_path", ["templates/edit_product.html", "templates/edit_product_mock.html"])
def test_no_stored_template_is_an_empty_object(template_path):
    assert json.loads(_render_input(template_path, {"targeting_template": None})["value"] or "") == {}
