"""Guard: a VERIFIED RootModel field is never stringified.

Disease: interpolating a pydantic ``RootModel`` into a string does NOT yield the
wrapped scalar — ``RootModel`` does not override ``__str__``, so an f-string,
``str()``, ``+`` concat, ``%``-format or ``join()`` renders the model's repr,
``root='94103'``. Wherever that string then goes — a wire message, a persisted
natural key, a log line — it is wrong, and for a ``JSONType`` column it can make the
row unloadable through the ORM. This is a repeat offender across the codebase
(``BrandReference.brand_id``, then ``CreateMediaBuyRequest.start_time``), which is why
it gets a guard rather than another one-off fix.

**What is banned:** stringification of the curated set of fields whose declared type
was verified to be (or to contain) a RootModel. Not the raw output of
``scripts/ci/scan_rootmodel_stringification.py`` — that scan is name-anchored over
every RootModel-typed field name in the SDK, and most of its hits are name collisions
on unrelated models (``BrandReference.domain`` is ``str``, our ``FormatId.id`` is
``str``, ``.value`` is an ``Enum``, ``.text`` is a ``requests.Response``). Baselining
those would bake noise into the guard. The curated set is what a reader can trust.

**Why there is no diagnostic/value exemption:** the scan splits hits into "diagnostic"
(log/exception text) and "value" (persisted/returned). That split is unsound as a
soundness signal — an inlined ``raise ValueError(f"...{x}")`` classifies as diagnostic
while the identical defect written as ``msg = f"...{x}"`` then ``raise ValueError(msg)``
classifies as value. An earlier sweep missed a real buyer-facing defect for exactly
that reason. For a field verified to be a RootModel there is no context in which
``root='US'`` is the intended rendering, so the form is banned outright.

**Why ``start_time`` is resolved by ANNOTATION, not by name or receiver name:**
``start_time`` is ``StartTiming`` (a RootModel) on ``CreateMediaBuyRequest`` but a
plain ``datetime`` on ``UpdateMediaBuyRequest`` and on the ORM ``MediaBuy``. A bare
name rule would demand ``.root`` on a datetime (an ``AttributeError``); a
receiver-NAME rule (``req``/``request`` only) fails in both directions — it would flag
the ``req.start_time`` reads in ``media_buy_update.py``, where ``req`` is an
``UpdateMediaBuyRequest``, and it would MISS ``self.start_time`` inside
``CreateMediaBuyRequest`` itself (``src/core/schemas/_base.py``), which is the one
place the defect has no ``req`` to anchor on. Resolving the receiver to the enclosing
function's annotated parameter type — and, for ``self``, to the enclosing class name —
is pure AST, exact, and needs no exemptions.

**Fields deliberately OUT of the set:**

- ``brand_id`` — already banned more strongly by
  ``tests/unit/test_guards_brand_id_single_accessor.py``, which forbids reading the
  ATTRIBUTE at all outside ``src/core/helpers/brand_key.py``. Duplicating it here
  would be duplicated logic.
- ``geo_metros`` — ``GeoMetro`` is NOT a RootModel (it has ``system``/``values``
  fields), so its rendering is not this defect. ``src/adapters/mock_ad_server.py``
  stringifies it on the line adjacent to two banned sites; that is intentional, not an
  oversight.

**Known blind spot (pinned below, not papered over):** element-level stringification
bound to a local first — ``rendered = [f"{c}" for c in overlay.geo_countries]`` — is
NOT caught. The comprehension element is the loop variable, and no attribute in the
banned set is stringified at that node. ``TestGuardMetaCases`` pins this as a
would-be-missed case so the limit is visible rather than assumed away.

**Do not "fix" a violation with a defensive ``hasattr`` probe for the root
attribute** — ``tests/unit/test_architecture_no_defensive_rootmodel.py`` bans that
form outright (the literal is omitted here so this docstring does not trip it).
RootModel presence is trusted: read ``.root`` directly.
"""

import ast
import re

from scripts.ci.scan_rootmodel_stringification import Site, find_stringification_sites
from tests.unit._architecture_helpers import (
    REPO_ROOT,
    assert_detector_catches_ast_snippets,
    parse_module,
    src_python_files,
)

#: Fields whose declared type was verified (against the pinned ``adcp`` SDK) to be or
#: to wrap a RootModel on EVERY model that declares them — no name collisions, so a
#: bare name-anchored ban is sound.
#: ``geo_countries``/``geo_regions`` -> ``GeoCountry``/``GeoRegion``;
#: ``geo_postal_areas``/``geo_postal_areas_exclude`` -> ``PostalArea``.
UNCONDITIONALLY_BANNED_FIELDS = frozenset(
    {
        "geo_countries",
        "geo_regions",
        "geo_postal_areas",
        "geo_postal_areas_exclude",
    }
)

#: Fields whose name is shared by models with different declared types, banned only
#: when the receiver resolves to the model on which the field IS a RootModel.
#: ``CreateMediaBuyRequest.start_time`` is ``StartTiming``; the same name on
#: ``UpdateMediaBuyRequest`` and on the ORM model is a plain datetime.
ANNOTATION_QUALIFIED_FIELDS = {"start_time": "CreateMediaBuyRequest"}

_SCANNED_FIELDS = UNCONDITIONALLY_BANNED_FIELDS | set(ANNOTATION_QUALIFIED_FIELDS)

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _annotation_mentions(annotation: str | None, type_name: str) -> bool:
    """True when *annotation* names *type_name*, through any wrapper.

    Matches ``CreateMediaBuyRequest``, ``CreateMediaBuyRequest | None`` and
    ``Optional[CreateMediaBuyRequest]`` alike — the wrapper does not change which model
    the attribute is read off.
    """
    return annotation is not None and type_name in _IDENTIFIER.findall(annotation)


def _receiver_is_type(site: Site, type_name: str) -> bool:
    """True when *site*'s receiver resolves to *type_name* by annotation or by ``self``."""
    if _annotation_mentions(site.receiver_annotation, type_name):
        return True
    return site.receiver_expr == "self" and site.enclosing_class == type_name


def find_banned_stringification_sites(tree: ast.Module) -> list[Site]:
    """Sites in *tree* that stringify a verified RootModel field."""
    banned: list[Site] = []
    for site in find_stringification_sites(tree, field_names=_SCANNED_FIELDS):
        if site.attr in UNCONDITIONALLY_BANNED_FIELDS:
            banned.append(site)
        elif _receiver_is_type(site, ANNOTATION_QUALIFIED_FIELDS[site.attr]):
            banned.append(site)
    return banned


def find_banned_stringification_linenos(tree: ast.Module) -> list[int]:
    """Line numbers only — the single-argument form ``assert_detector_catches_ast_snippets`` takes."""
    return sorted(site.lineno for site in find_banned_stringification_sites(tree))


def test_no_verified_rootmodel_field_is_stringified():
    violations: list[str] = []
    for path in src_python_files(REPO_ROOT):
        rel = path.relative_to(REPO_ROOT).as_posix()
        for site in find_banned_stringification_sites(parse_module(path)):
            violations.append(f"{rel}:{site.lineno}: [{site.form}] {site.receiver_expr}.{site.attr}")
    assert not violations, (
        "A verified RootModel field is stringified. RootModel has no __str__, so this "
        "renders the repr (root='94103'), not the scalar — wrong in an error message a "
        "buyer reads, wrong in a persisted key, wrong in a log. Unwrap explicitly: "
        "`.root` for a single value, `[x.root for x in field]` for a list.\n  " + "\n  ".join(violations)
    )


class TestGuardMetaCases:
    """The guard's own detection contract (positive / negative / would-be-missed)."""

    def test_flags_every_stringification_form_of_an_unconditional_field(self):
        assert_detector_catches_ast_snippets(
            find_banned_stringification_linenos,
            snippets={
                # The two live buyer-facing sites, in shape: the repr lands in a
                # ValueError that the adapter boundary re-raises onto the wire.
                "fstring_in_raise": (
                    'def build(overlay):\n    raise ValueError(f"Cannot fulfill: {overlay.geo_postal_areas}.")\n'
                ),
                # Same defect, written so the scan's diagnostic/value split would
                # classify it the OTHER way — the guard must not care.
                "fstring_bound_then_raised": (
                    "def build(overlay):\n"
                    '    msg = f"Cannot fulfill: {overlay.geo_postal_areas_exclude}."\n'
                    "    raise ValueError(msg)\n"
                ),
                # Console/log rendering is still the repr; still banned.
                "fstring_in_log_call": (
                    "def show(targeting, log):\n    log(f\"'countries': {targeting.geo_countries},\")\n"
                ),
                "explicit_str_call": ("def show(targeting):\n    return str(targeting.geo_regions)\n"),
                "concat": ('def show(targeting):\n    return "regions: " + targeting.geo_regions\n'),
                "percent_format": ('def show(targeting):\n    return "regions: %s" % (targeting.geo_regions,)\n'),
                # join() over the bare field stringifies each element via __str__.
                "join_of_bare_field": ('def show(targeting):\n    return ",".join(targeting.geo_countries)\n'),
            },
        )

    def test_flags_start_time_when_the_receiver_is_annotated_create_media_buy_request(self):
        """The 5pkw shape: ``req`` is a ``CreateMediaBuyRequest``, so ``start_time`` is StartTiming."""
        source = (
            "async def _create_media_buy_impl(req: CreateMediaBuyRequest, identity):\n"
            '    raise ValueError(f"start_time {req.start_time} is in the past")\n'
        )
        assert find_banned_stringification_linenos(ast.parse(source)) == [2]

    def test_flags_start_time_on_self_inside_create_media_buy_request(self):
        """The receiver-NAME rule's false negative: no ``req`` to anchor on.

        ``src/core/schemas/_base.py`` validates and formats ``start_time`` from inside
        ``CreateMediaBuyRequest`` itself, which is precisely where the defect has been
        written before. The enclosing ClassDef supplies the type.
        """
        source = (
            "class CreateMediaBuyRequest(LibraryCreateMediaBuyRequest):\n"
            "    def validate_timezone_aware(self):\n"
            '        raise ValueError(f"start_time {self.start_time} must be timezone-aware")\n'
        )
        assert find_banned_stringification_linenos(ast.parse(source)) == [3]

    def test_does_not_flag_start_time_on_update_media_buy_request(self):
        """The receiver-NAME rule's false positive: same param name, plain datetime.

        ``UpdateMediaBuyRequest.start_time`` is ``datetime | 'asap' | None``. Flagging it
        would demand ``.root`` on a datetime — an AttributeError — or force an
        exemption, and this guard ships with an empty exemption set.
        """
        source = (
            "async def _update_media_buy_impl(req: UpdateMediaBuyRequest, identity):\n"
            '    return f"start_time {req.start_time}"\n'
        )
        assert find_banned_stringification_linenos(ast.parse(source)) == []

    def test_does_not_flag_start_time_on_an_orm_media_buy(self):
        """``MediaBuy.start_time`` is a DB column: a plain datetime, unannotated local."""
        source = 'def render(media_buy):\n    return f"start_time {media_buy.start_time}"\n'
        assert find_banned_stringification_linenos(ast.parse(source)) == []

    def test_does_not_flag_the_correct_list_comprehension_unwrap(self):
        """The shape the fix takes in kevel.py / xandr.py / triton_digital.py."""
        source = (
            "def build(overlay):\n"
            "    countries = [c.root for c in overlay.geo_countries]\n"
            "    regions = [r.root for r in overlay.geo_regions]\n"
            "    return countries, regions\n"
        )
        assert find_banned_stringification_linenos(ast.parse(source)) == []

    def test_does_not_flag_a_join_over_root_unwrapped_elements(self):
        """A guard that flagged this would forbid the very fix it demands.

        The join arm walks its whole argument, so it sees the ITERATOR
        (``overlay.geo_countries``) even though what is stringified is ``c.root``.
        """
        source = 'def build(overlay):\n    return ",".join(c.root for c in overlay.geo_countries)\n'
        assert find_banned_stringification_linenos(ast.parse(source)) == []

    def test_does_not_flag_geo_metros(self):
        """``GeoMetro`` is not a RootModel — it renders its own fields, not ``root=``."""
        source = "def show(targeting, log):\n    log(f\"'metros': {targeting.geo_metros},\")\n"
        assert find_banned_stringification_linenos(ast.parse(source)) == []

    def test_would_be_missed_element_stringification_bound_to_a_local(self):
        """Documented blind spot, pinned so it stays visible.

        The comprehension stringifies the loop variable, not the banned attribute, so
        no node in the banned set is stringified. Closing this needs element-type
        inference through the comprehension, which no rule here attempts.
        """
        source = 'def build(overlay):\n    rendered = [f"{c}" for c in overlay.geo_countries]\n    return rendered\n'
        assert find_banned_stringification_linenos(ast.parse(source)) == []


class TestDetectorJoinArm:
    """The detector fix this guard depends on: ``.root`` elements exempt from the join arm."""

    def test_join_over_bare_field_is_still_a_hit(self):
        tree = ast.parse('def f(t):\n    return ",".join(t.geo_countries)\n')
        sites = find_stringification_sites(tree, field_names={"geo_countries"})
        assert [(s.attr, s.form) for s in sites] == [("geo_countries", "join()")]

    def test_join_over_root_unwrapped_elements_is_not_a_hit(self):
        tree = ast.parse('def f(t):\n    return ",".join(c.root for c in t.geo_countries)\n')
        assert find_stringification_sites(tree, field_names={"geo_countries"}) == []

    def test_join_over_str_of_element_is_still_a_hit(self):
        """Only the ``.root`` unwrap is exempt — ``str(c)`` still renders the repr."""
        tree = ast.parse('def f(t):\n    return ",".join(str(c) for c in t.geo_countries)\n')
        sites = find_stringification_sites(tree, field_names={"geo_countries"})
        assert [s.attr for s in sites] == ["geo_countries"]


class TestDetectorSiteContext:
    """``Site`` records what the annotation rule needs — the name-only record could not."""

    def test_site_records_receiver_annotation_and_enclosing_scopes(self):
        tree = ast.parse(
            'class Runner:\n    def go(self, req: CreateMediaBuyRequest):\n        return f"{req.start_time}"\n'
        )
        (site,) = find_stringification_sites(tree, field_names={"start_time"})
        assert (
            site.attr,
            site.receiver_expr,
            site.receiver_annotation,
            site.enclosing_function,
            site.enclosing_class,
        ) == (
            "start_time",
            "req",
            "CreateMediaBuyRequest",
            "go",
            "Runner",
        )
