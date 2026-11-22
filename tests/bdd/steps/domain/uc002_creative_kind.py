"""Steps for local-uc002-creative-kind-reaches-adapter.feature: what the ad server receives per creative kind.

The Givens seed one approved creative of a kind, and the product option it selects, the way a
synced creative is stored (``store_package_creative``, with the per-representation builders of
``tests/factories/creative_asset.py``); the Whens are the shared create dispatch and the
operator steps of ``gam_seller.py``. The Thens read the typed creative the asset carries under
``creative`` through the env's two adapter seams: ``uploaded_adapter_assets`` for the
synchronous create, ``creative_manager_assets`` for the seller's approval and the retroactive
push. Both project the asset the way the persistence edge does, so the assertion reads the same
JSON on every transport.
"""

from __future__ import annotations

import json
from typing import Any

from pytest_bdd import given, parsers, then

from src.core.format_resolver import registry_projects
from src.core.schemas import FormatId
from tests.bdd.steps.generic._creative_seeding import store_creative, store_package_creative
from tests.bdd.steps.generic._table import comma_list
from tests.bdd.steps.generic._table import rows as table_rows
from tests.bdd.steps.generic.given_media_buy import (
    _ensure_request_defaults,
    _publish_on_default_product,
    _seed_auto_approval,
    accept_on_default_product,
)
from tests.e2e.webhook_capture_service import PEER_PREVIEW_TOOL
from tests.factories.creative import (
    DEFAULT_CREATIVE_AGENT_URL,
    CreativeAssignmentFactory,
    declared_format_spec,
    served_format_spec,
)
from tests.factories.creative_asset import (
    DISPLAY_TAG_SLOT,
    AssetSpec,
    build_assets,
    display_tag_spec,
    html5_bundle_specs,
    html5_zip_bundle,
    html_spec,
    image_spec,
    legacy_tag_url_spec,
    named_format_assets,
    parse_sizes,
    prompt_assets,
    url_spec,
    video_spec,
)
from tests.factories.request import CreativeAssetRequestFactory

#: The markup the inline-markup scenario ships and expects back, defined once because the
#: Given seeds it and the Then compares against it. A newline, both quote characters and a
#: macro token: the bytes a reformatting, escaping or macro-expanding receiver would change.
INLINE_MARKUP = (
    '<iframe src="https://ads.example-adserver.com/render?slot=42&cb=%%CACHEBUSTER%%"\n'
    "  width='300' height='250' frameborder=\"0\" scrolling=\"no\"></iframe>"
)

#: What the creative agent's preview of a creative is: an untrusted page to embed in a
#: cross-origin iframe (preview_creative.mdx), which sync used to write into the creative's
#: root ``url`` when the buyer sent none.
PREVIEW_URL = "https://creative.adcontextprotocol.org/preview/cr-inline/desktop.html"

#: The display_tag kind's slot for an explicit display_tag asset, as display.mdx names it.
_SERVING_TAG_SLOT = {"asset_group_id": DISPLAY_TAG_SLOT, "asset_type": "display_tag", "required": True}

_RECEIVER = r"(?P<receiver>the ad server|the ad server's creative manager)"
_RECEIVES_KIND = _RECEIVER + r' receives creative "(?P<creative_id>[^"]+)" of kind "(?P<kind>[^"]+)" as '


def _display_tag_option(delivery_type: str, size_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """A display_tag format option taking one display_tag asset of *delivery_type*, as display.mdx publishes it.

    *size_params* is the option's size mode (formats/canonical/display_tag.json: fixed,
    ``sizes`` or responsive bounds), a fixed 300x250 when not given. The option is named
    ``display_tag_<delivery_type>`` so a package can select it by ``format_option_refs``.
    """
    return {
        "format_option_id": f"display_tag_{delivery_type}",
        "format_kind": "display_tag",
        "params": {
            **(size_params or {"width": 300, "height": 250}),
            "supported_delivery_types": [delivery_type],
            "slots": [_SERVING_TAG_SLOT],
        },
    }


#: The responsive bounds a scenario states as ``widths 300 to 970 and heights 90 to 250``.
_RANGE = r"widths (?P<min_width>\d+) to (?P<max_width>\d+) and heights (?P<min_height>\d+) to (?P<max_height>\d+)"


def _size_range(min_width: str, max_width: str, min_height: str, max_height: str) -> dict[str, int]:
    """Responsive bounds as an option's params and as the carrier's ``size_ranges`` entry (same four names)."""
    return {
        "min_width": int(min_width),
        "max_width": int(max_width),
        "min_height": int(min_height),
        "max_height": int(max_height),
    }


# ── Givens ─────────────────────────────────────────────────────────────

#: An optional clause on a creative Given: the row was synced while sync still copied the
#: creative agent's preview onto it, so its root ``url`` is the preview page and its root size
#: the preview's.
_RECORDED_PREVIEW = (
    r"(?:, recorded with the creative agent's preview page \"(?P<preview_url>[^\"]+)\" as its url at "
    r"(?P<preview_width>\d+)x(?P<preview_height>\d+))?"
)


#: An optional clause on a creative Given: the manifest also carries an impression tracker
#: pixel, a url asset under the registry's canonical ``impression_tracker`` slot with
#: ``url_type: "tracker_pixel"`` (core/assets/url-asset.json).
_TRACKER = r'(?:, with impression tracker "(?P<tracker_url>[^"]+)")?'


def _tracker_specs(tracker_url: str | None) -> list[AssetSpec]:
    """The url asset :data:`_TRACKER` states: none, or the impression tracker pixel."""
    if tracker_url is None:
        return []
    return [url_spec("impression_tracker", url=tracker_url, url_type="tracker_pixel")]


def _recorded_preview(preview_url: str | None, width: str | None, height: str | None) -> dict[str, Any]:
    """The root keys :data:`_RECORDED_PREVIEW` states: none, or the preview page and size."""
    if preview_url is None:
        return {}
    return {"url": preview_url, "width": int(str(width)), "height": int(str(height))}


@given(
    parsers.re(
        r'the package references the buyer\'s approved display_tag creative "(?P<creative_id>[^"]+)" carrying '
        r'(?:the inline markup "(?P<markup>[^"]+)" of type "(?P<markup_type>[^"]+)"'
        r"|its byte-sensitive inline (?P<sensitive_type>\w+) markup"
        r'|the tag URL "(?P<tag_url>[^"]+)"'
        r'|the paired redirect "(?P<ad_request_url>[^"]+)" with click-through "(?P<clickthrough_url>[^"]+)")'
        + _TRACKER
        + _RECORDED_PREVIEW
        + "$"
    )
)
def given_display_tag_creative(
    ctx: dict,
    creative_id: str,
    markup: str | None,
    markup_type: str | None,
    sensitive_type: str | None,
    tag_url: str | None,
    ad_request_url: str | None,
    clickthrough_url: str | None,
    tracker_url: str | None,
    preview_url: str | None,
    preview_width: str | None,
    preview_height: str | None,
) -> None:
    """A canonical ``display_tag`` creative carrying one ``display_tag`` asset of the kind's three delivery types.

    AdCP 3.2.7 core/assets/display-tag-asset.json: ``delivery_type`` ``tag_url`` with ``url``,
    ``inline_markup`` with ``markup`` and ``markup_type``, or ``paired_redirect`` with
    ``ad_request_url`` and ``clickthrough_url``, under the slot an option accepting it declares
    (``serving_tag``). "byte-sensitive" markup is :data:`INLINE_MARKUP`, whose newline and
    quote characters a Gherkin string cannot carry. The product's options are another Given's.
    """
    if tag_url is not None:
        asset = display_tag_spec("tag_url", url=tag_url)
    elif ad_request_url is not None:
        asset = display_tag_spec("paired_redirect", ad_request_url=ad_request_url, clickthrough_url=clickthrough_url)
    elif sensitive_type is not None:
        asset = display_tag_spec("inline_markup", markup=INLINE_MARKUP, markup_type=sensitive_type)
    else:
        asset = display_tag_spec("inline_markup", markup=markup, markup_type=markup_type)
    recorded = _recorded_preview(preview_url, preview_width, preview_height)
    store_package_creative(
        ctx, creative_id, build_assets(asset, *_tracker_specs(tracker_url)), kind="display_tag", **recorded
    )


@given(
    parsers.re(
        r'the package references the buyer\'s approved html5 creative "(?P<creative_id>[^"]+)" bundled '
        r'(?:at "(?P<bundle_url>[^"]+)"|by (?P<hosted>the buyer\'s bundle host)'
        r"|by (?P<silent>a bundle host that does not answer)|with no url), "
        r'with backup image "(?P<backup_url>[^"]+)"(?:, sized (?P<width>\d+)x(?P<height>\d+))?$'
    )
)
def given_html5_bundle_creative(
    ctx: dict,
    creative_id: str,
    bundle_url: str | None,
    hosted: str | None,
    silent: str | None,
    backup_url: str,
    width: str | None,
    height: str | None,
) -> None:
    """A canonical ``html5`` creative: slots ``html5_bundle`` (zip) and ``backup_image`` (formats/canonical/html5.json).

    The zip is at *bundle_url*; or served by the buyer's bundle host (``serve_html5_bundle``),
    its bytes kept to compare with what the ad server receives; or at a host that does not
    answer (``serve_html5_bundle(None)``); or it names no ``url``, the member
    core/assets/zip-asset.json requires. The backup image is 300x250 unless sized.
    """
    if hosted is not None:
        ctx["html5_bundle"] = html5_zip_bundle()
        bundle_url = ctx["env"].serve_html5_bundle(ctx["html5_bundle"])
    elif silent is not None:
        bundle_url = ctx["env"].serve_html5_bundle(None)
    size = {} if width is None else {"backup_width": int(width), "backup_height": int(str(height))}
    assets = build_assets(*html5_bundle_specs(bundle_url, backup_url=backup_url, **size))
    store_package_creative(ctx, creative_id, assets, kind="html5")


@given(
    parsers.re(
        r"the package references the buyer's approved display_tag creative \"(?P<creative_id>[^\"]+)\" whose "
        r'tag_url slot carries "(?P<url>[^"]+)" with (?:url_type "(?P<url_type>[^"]+)"|no url_type)$'
    )
)
def given_legacy_tag_url_creative(ctx: dict, creative_id: str, url: str, url_type: str | None) -> None:
    """The kind's backward-compatible ``tag_url`` slot: a url asset, with or without its own ``url_type``."""
    store_package_creative(
        ctx, creative_id, build_assets(legacy_tag_url_spec(url, url_type=url_type)), kind="display_tag"
    )


@given(
    parsers.re(
        r'the package references the buyer\'s approved (?P<width>\d+)x(?P<height>\d+) image creative "(?P<creative_id>'
        r'[^"]+)" showing "(?P<url>[^"]+)"(?:, with click-through "(?P<click_url>[^"]+)")?'
        + _TRACKER
        + r'(?:, beside the video "(?P<video_url>[^"]+)" in its "(?P<video_slot>[^"]+)" slot)?$'
    )
)
def given_sized_image_creative(
    ctx: dict,
    width: str,
    height: str,
    creative_id: str,
    url: str,
    click_url: str | None,
    tracker_url: str | None,
    video_url: str | None,
    video_slot: str | None,
) -> None:
    """A canonical image creative whose ``image_main`` file is *width* by *height*.

    With a click-through, the manifest carries it as a url asset under the registry's canonical
    ``landing_page_url`` slot with ``url_type: "clickthrough"`` (core/assets/url-asset.json).
    With a video, the manifest carries it in *video_slot*, ahead of ``image_main``, so a reader
    that takes the first media asset in the manifest takes the video.
    """
    extra = _tracker_specs(tracker_url)
    if click_url is not None:
        extra.append(url_spec("landing_page_url", url=click_url, url_type="clickthrough"))
    assets = _image_assets(int(width), int(height), url, *extra)
    if video_url is not None:
        assets = build_assets(video_spec(str(video_slot), url=video_url)) | assets
    store_package_creative(ctx, creative_id, assets, kind="image")


@given(
    parsers.re(
        r"the product publishes (?:an? (?P<kind>image|html5) format option"
        r"|a display_tag format option (?:for (?P<delivery_type>\w+) delivery|on the kind's default slots)) "
        r'(?:fixed at (?P<width>\d+)x(?P<height>\d+)|accepting sizes "(?P<sizes>[^"]+)"|accepting ' + _RANGE + r")$"
    )
)
def given_product_publishes_option(
    ctx: dict,
    kind: str | None,
    delivery_type: str | None,
    width: str | None,
    height: str | None,
    sizes: str | None,
    min_width: str | None,
    max_width: str | None,
    min_height: str | None,
    max_height: str | None,
) -> None:
    """The default product publishes one canonical format option, in one size mode, and the create path auto-approves.

    formats/canonical/display_tag.json gives an option "Exactly one of: (a) fixed (`width` +
    `height` both set), (b) multi-size (`sizes` set), (c) responsive". A display_tag option
    for a delivery type declares that one type and the ``serving_tag`` slot ("Format options
    accepting inline or paired delivery override `slots` with a `display_tag` asset and declare
    `supported_delivery_types`"); on the kind's default slots it overrides nothing.
    """
    if sizes is not None:
        size_params: dict[str, Any] = {"sizes": parse_sizes(sizes)}
    elif width is not None:
        size_params = {"width": int(width), "height": int(str(height))}
    else:
        size_params = _size_range(str(min_width), str(max_width), str(min_height), str(max_height))
    if delivery_type is not None:
        option = _display_tag_option(delivery_type, size_params)
    elif kind is None:
        option = {"format_option_id": "display_tag_default_slots", "format_kind": "display_tag", "params": size_params}
    else:
        option = {"format_option_id": f"{kind}_option", "format_kind": kind, "params": size_params}
    _publish_on_default_product(ctx, option)


@given("the product publishes no format_ids")
def given_product_publishes_no_format_ids(ctx: dict) -> None:
    """The default product drops its legacy arm, so the canonical options a later Given publishes are its only formats."""
    ctx["default_product"].format_ids = None
    ctx["env"]._commit_factory_data()


@given("the package selects that format option by its format_option_ref")
def given_package_selects_option(ctx: dict) -> None:
    """The package names the option the previous Given published, through the canonical 3.2 selector.

    media-buy/package-request.json ``format_option_refs``: "Each reference matches one target
    product ``format_options[]`` entry"; a product-local option is ``{scope: "product",
    format_option_id}``. Create records the selected declaration on the package, which is what
    the seller's approval later reconstructs the package from.
    """
    (option_id,) = [option["format_option_id"] for option in ctx["default_product"].format_options[-1:]]
    _ensure_request_defaults(ctx)["packages"][0]["format_option_refs"] = [
        {"scope": "product", "format_option_id": option_id}
    ]


@given(parsers.parse('creative "{creative_id}" is named on every package of the request'))
def given_creative_on_every_package(ctx: dict, creative_id: str) -> None:
    """Every package of the request names *creative_id* in its creative_ids, once each."""
    for package in _ensure_request_defaults(ctx)["packages"]:
        named = package.setdefault("creative_ids", [])
        if creative_id not in named:
            named.append(creative_id)


@given(
    parsers.parse(
        'the package references the buyer\'s approved "{format_id}" creative "{creative_id}" whose only asset '
        'is html "{content}"'
    )
)
def given_html_only_creative(ctx: dict, format_id: str, creative_id: str, content: str) -> None:
    """A reference-catalog creative carrying its one required html asset and nothing else: no root url or size."""
    (asset_id,) = named_format_assets(format_id)
    store_package_creative(ctx, creative_id, build_assets(html_spec(asset_id, content=content)), fmt=format_id)
    accept_on_default_product(ctx, format_id)


@given(
    parsers.re(
        r"the package references the buyer's approved generative creative \"(?P<creative_id>[^\"]+)\" in "
        r'"(?P<format_id>[^"]+)", (?:built to "(?P<built_url>[^"]+)"|not built yet)$'
    )
)
def given_generative_creative(ctx: dict, creative_id: str, format_id: str, built_url: str | None) -> None:
    """A generative creative as sync stores it: its prompt, and the build output when the build produced one.

    With a build output, sync keeps it as ``output_format``. The row's root ``url`` is the
    creative agent's preview page, with the preview's size, as sync used to back-fill it: so a
    reader that took the root ``url`` for the build output would hand the ad server the
    preview page.
    """
    assets = prompt_assets("A trail running shoe on a mountain path", format_id=format_id)
    built: dict[str, Any] = {}
    if built_url is not None:
        built = {"output_format": {"url": built_url}, "url": PREVIEW_URL, "width": 300, "height": 250}
    store_package_creative(ctx, creative_id, assets, fmt=format_id, **built)
    accept_on_default_product(ctx, format_id)


def _image_assets(width: int, height: int, url: str, *extra: AssetSpec) -> dict[str, Any]:
    """A canonical image creative's ``image_main`` slot, the file *width* by *height*, and any *extra* assets."""
    return build_assets(image_spec("image_main", url=url, width=width, height=height), *extra)


@given(
    parsers.re(
        r'the buyer holds a live media buy booked at the ad server as order "(?P<order_id>[^"]+)" with packages? '
        r'"(?P<package_ids>[^"]+)"(?:, and package "(?P<gone_package_id>[^"]+)" on product "(?P<gone_product_id>'
        r'[^"]+)", which the seller no longer has)?$'
    )
)
def given_live_booked_buy(
    ctx: dict, order_id: str, package_ids: str, gone_package_id: str | None, gone_product_id: str | None
) -> None:
    """An active buy, already booked at the ad server, as create leaves it, each package on its own line item.

    The named packages are on the default product. The one after them, when stated, was booked
    on a product the seller has since removed: its stored ``product_id`` names no product row.
    """
    line_items = {package_id: f"line-item-{package_id}" for package_id in comma_list(package_ids)}
    package_products: dict[str, str] = {}
    if gone_package_id is not None and gone_product_id is not None:
        line_items[gone_package_id] = f"line-item-{gone_package_id}"
        package_products[gone_package_id] = gone_product_id
    ctx["live_media_buy"] = ctx["env"].book_at_ad_server(
        line_items=line_items,
        order_id=order_id,
        product=ctx["default_product"],
        status="active",
        package_products=package_products,
    )


@given(
    parsers.re(
        r"the buyer's creative \"(?P<creative_id>[^\"]+)\", (?P<status>approved|under review), "
        r'(?:a display_tag carrying the tag URL "(?P<tag_url>[^"]+)"'
        r'|a (?P<width>\d+)x(?P<height>\d+) image showing "(?P<image_url>[^"]+)"'
        r'(?: in the reference format "(?P<unserved_format>[^"]+)", which the creative agent does not serve)?'
        r'|an html5 creative bundled by the buyer\'s bundle host, with backup image "(?P<backup_url>[^"]+)"), '
        r'is assigned to (?:packages? "(?P<package_ids>[^"]+)" of the live media buy|the media buy awaiting approval)$'
    )
)
def given_creative_assigned_to_buy(
    ctx: dict,
    creative_id: str,
    status: str,
    tag_url: str | None,
    width: str | None,
    height: str | None,
    image_url: str | None,
    unserved_format: str | None,
    backup_url: str | None,
    package_ids: str | None,
) -> None:
    """A creative already assigned to a stored buy: the live one's named packages, or the held buy's one package.

    The rows create leaves for a creative it held back (under review), or a creative assigned
    to the buy before the product's option changed (approved). Either way it was never pushed:
    the seller's approval of the creative, or of the buy, is what pushes it. An html5 bundle's
    zip is served by the buyer's bundle host (``serve_html5_bundle``), and its bytes are kept
    to compare with what the ad server receives.

    An image in a reference format the creative agent does not serve was stored when the agent
    still declared it as an image format, and the product still lists that reference. Neither
    the agent's catalog nor the canonical mapping registry knows the id now, which the step
    checks, so the scenario cannot pass on a format the seller can still resolve.
    """
    env = ctx["env"]
    if package_ids is not None:
        media_buy = ctx["live_media_buy"]
        packages = comma_list(package_ids)
    else:
        (media_buy,) = env.media_buys_awaiting_approval()
        packages = [package.package_id for package in media_buy.packages]
    if tag_url is not None:
        kind, assets = "display_tag", build_assets(display_tag_spec("tag_url", url=tag_url))
    elif backup_url is not None:
        ctx["html5_bundle"] = html5_zip_bundle()
        bundle_url = env.serve_html5_bundle(ctx["html5_bundle"])
        kind, assets = "html5", build_assets(*html5_bundle_specs(bundle_url, backup_url=backup_url))
    else:
        kind, assets = "image", _image_assets(int(str(width)), int(str(height)), str(image_url))
    identity: dict[str, Any] = {"kind": kind}
    if unserved_format is not None:
        reference = FormatId(agent_url=DEFAULT_CREATIVE_AGENT_URL, id=unserved_format)
        assert served_format_spec(DEFAULT_CREATIVE_AGENT_URL, unserved_format) is None, unserved_format
        assert not registry_projects(reference), unserved_format
        identity = {
            "fmt": unserved_format,
            "format_spec": declared_format_spec(DEFAULT_CREATIVE_AGENT_URL, unserved_format),
        }
        accept_on_default_product(ctx, unserved_format)
    creative = store_creative(
        ctx, creative_id, assets, status="approved" if status == "approved" else "pending_review", **identity
    )
    for package_id in packages:
        CreativeAssignmentFactory(creative=creative, media_buy=media_buy, package_id=package_id)
    env._commit_factory_data()


@given(
    parsers.parse(
        'the tenant\'s own creative agent publishes "{format_id}" and previews every creative as its page '
        '"{preview_url}", sized {width:d}x{height:d}'
    )
)
def given_hosted_agent_previews(ctx: dict, format_id: str, preview_url: str, width: int, height: int) -> None:
    """The tenant's hosted creative agent publishes the reference *format_id* and answers previews with that page and size.

    preview_creative.mdx: "Every render's ``preview_url`` returns an untrusted HTML page for
    cross-origin iframe embedding". The product sells the format at that agent, so a creative
    in it is looked up from the agent and previewed by it, on every transport.
    """
    env = ctx["env"]
    agent_url = env.host_reference_format(format_id)
    env.answer_hosted_previews(preview_url, (width, height))
    env.set_product_format_ids(ctx["default_product"].product_id, [{"agent_url": agent_url, "id": format_id}])
    ctx["hosted_agent_url"] = agent_url
    _seed_auto_approval(ctx)


@given(
    parsers.parse(
        'the request\'s package carries the buyer\'s "{format_id}" creative "{creative_id}" whose only asset is '
        'html "{content}", in that agent\'s format'
    )
)
def given_inline_html_creative_in_hosted_format(ctx: dict, format_id: str, creative_id: str, content: str) -> None:
    """The package uploads one creative in the hosted agent's *format_id*, carrying its one html asset.

    create_media_buy syncs a package's ``creatives`` through the creative-sync service before
    it books (``process_and_upload_package_creatives``), so the creative is stored by sync,
    previewed by the agent, then handed to the ad server.
    """
    (asset_id,) = named_format_assets(format_id)
    package = _ensure_request_defaults(ctx)["packages"][0]
    package.setdefault("creatives", []).append(
        CreativeAssetRequestFactory.payload(
            creative_id=creative_id,
            name=f"Creative {creative_id}",
            format_id={"agent_url": ctx["hosted_agent_url"], "id": format_id},
            assets=build_assets(html_spec(asset_id, content=content)),
        )
    )


# ── Thens ──────────────────────────────────────────────────────────────


_CREATIVE_MANAGER = "the ad server's creative manager"


def _handed(ctx: dict, receiver: str) -> list[dict[str, Any]]:
    """Every asset dict *receiver* was handed, in order."""
    env = ctx["env"]
    return env.uploaded_adapter_assets() if receiver == "the ad server" else env.creative_manager_assets()


def _received_asset(ctx: dict, receiver: str, creative_id: str) -> dict[str, Any]:
    """The newest asset dict *receiver* was handed for *creative_id*; fails when it was handed none."""
    handed = _handed(ctx, receiver)
    mine = [asset for asset in handed if asset.get("creative_id") == creative_id]
    assert mine, (
        f"{receiver} was never handed creative {creative_id!r}; it was handed "
        f"{[asset.get('creative_id') for asset in handed]!r}"
    )
    return mine[-1]


def _received_once(ctx: dict, creative_id: str) -> dict[str, Any]:
    """The one asset dict the creative manager was handed for *creative_id*; fails on none or several.

    A creative on several packages of a buy is one creative at the ad server, associated with
    each package's line item, so a push hands it over once, with every package.
    """
    handed = _handed(ctx, _CREATIVE_MANAGER)
    mine = [asset for asset in handed if asset.get("creative_id") == creative_id]
    assert len(mine) == 1, (
        f"{_CREATIVE_MANAGER} was handed creative {creative_id!r} {len(mine)} times, not once; it was handed "
        f"{[asset.get('creative_id') for asset in handed]!r}"
    )
    return mine[0]


def _received_creative(ctx: dict, receiver: str, creative_id: str, kind: str | None = None) -> dict[str, Any]:
    """The typed creative *receiver* was handed for *creative_id*, checked to name it and, when given, *kind*."""
    asset = _received_asset(ctx, receiver, creative_id)
    creative = asset.get("creative")
    assert isinstance(creative, dict), (
        f"{receiver} was handed creative {creative_id!r} with no typed creative under 'creative', only the "
        f"legacy keys {sorted(asset)!r}: {asset!r}"
    )
    assert creative.get("creative_id") == creative_id, (
        f"{receiver} was handed typed creative {creative.get('creative_id')!r} under creative {creative_id!r}"
    )
    assert kind in (None, creative.get("format_kind")), (
        f"{receiver} was handed creative {creative_id!r} as kind {creative.get('format_kind')!r}, not {kind!r}"
    )
    return creative


def _assert_representation(ctx: dict, receiver: str, creative_id: str, kind: str, expected: dict[str, Any]) -> None:
    """*receiver* was handed *creative_id* as *kind* in exactly the *expected* representation."""
    representation = _received_creative(ctx, receiver, creative_id, kind).get("representation")
    assert representation == expected, (
        f"{receiver} was handed creative {creative_id!r} represented as {representation!r}, not {expected!r}"
    )


@then(parsers.re(_RECEIVES_KIND + r"inline (?P<markup_type>\w+) markup, the buyer's markup byte for byte$"))
def then_receives_inline_markup(ctx: dict, receiver: str, creative_id: str, kind: str, markup_type: str) -> None:
    """display-tag-asset.json: "Receivers MUST preserve its bytes"; compared to the bytes the Given stored."""
    expected = {"type": "inline_markup", "markup": INLINE_MARKUP, "markup_type": markup_type}
    _assert_representation(ctx, receiver, creative_id, kind, expected)


@then(parsers.re(_RECEIVES_KIND + r'the ad tag "(?P<url>[^"]+)"$'))
def then_receives_tag_url(ctx: dict, receiver: str, creative_id: str, kind: str, url: str) -> None:
    """A third-party ad-request URL the ad server invokes at impression time."""
    _assert_representation(ctx, receiver, creative_id, kind, {"type": "tag_url", "url": url})


@then(
    parsers.re(
        _RECEIVES_KIND
        + r'one redirect pair of ad request "(?P<ad_request_url>[^"]+)" and clickthrough "(?P<clickthrough_url>[^"]+)"$'
    )
)
def then_receives_paired_redirect(
    ctx: dict, receiver: str, creative_id: str, kind: str, ad_request_url: str, clickthrough_url: str
) -> None:
    """display-tag-asset.json: "The pair MUST NOT be split"; both URLs in one representation."""
    expected = {"type": "paired_redirect", "ad_request_url": ad_request_url, "clickthrough_url": clickthrough_url}
    _assert_representation(ctx, receiver, creative_id, kind, expected)


@then(parsers.re(_RECEIVES_KIND + r'inline html "(?P<content>[^"]+)"$'))
def then_receives_html(ctx: dict, receiver: str, creative_id: str, kind: str, content: str) -> None:
    """html-asset.json: inline HTML, served as its ``content``."""
    _assert_representation(ctx, receiver, creative_id, kind, {"type": "html", "content": content})


@then(
    parsers.re(_RECEIVES_KIND + r'the html5 bundle "(?P<url>[^"]+)" with backup image "(?P<backup_image_url>[^"]+)"$')
)
def then_receives_html5_bundle(
    ctx: dict, receiver: str, creative_id: str, kind: str, url: str, backup_image_url: str
) -> None:
    """formats/canonical/html5.json: the zip is what is served; the backup image rides with it."""
    expected = {"type": "html5_bundle", "url": url, "backup_image_url": backup_image_url}
    _assert_representation(ctx, receiver, creative_id, kind, expected)


@then(parsers.re(_RECEIVES_KIND + r'(?P<asset_type>image|video|audio) media "(?P<url>[^"]+)"$'))
def then_receives_media(ctx: dict, receiver: str, creative_id: str, kind: str, asset_type: str, url: str) -> None:
    """A hosted media file, served as the file."""
    _assert_representation(ctx, receiver, creative_id, kind, {"type": "media", "asset_type": asset_type, "url": url})


@then(parsers.re(_RECEIVER + r' receives creative "(?P<creative_id>[^"]+)" sized "(?P<sizes>[^"]+)"$'))
def then_receives_sizes(ctx: dict, receiver: str, creative_id: str, sizes: str) -> None:
    """The sizes the creative is trafficked at: the ones its format option or format declares, in order."""
    creative = _received_creative(ctx, receiver, creative_id)
    assert creative.get("sizes") == parse_sizes(sizes), (
        f"{receiver} was handed creative {creative_id!r} sized {creative.get('sizes')!r}, not {parse_sizes(sizes)!r}"
    )


@then(
    parsers.re(
        _RECEIVER + r' receives creative "(?P<creative_id>[^"]+)" with no fixed size, sized within ' + _RANGE + "$"
    )
)
def then_receives_size_range(
    ctx: dict, receiver: str, creative_id: str, min_width: str, max_width: str, min_height: str, max_height: str
) -> None:
    """A responsive option states a range and no size: the carrier holds that one range and no fixed size."""
    creative = _received_creative(ctx, receiver, creative_id)
    expected = [_size_range(min_width, max_width, min_height, max_height)]
    assert (creative.get("sizes"), creative.get("size_ranges")) == ([], expected), (
        f"{receiver} was handed creative {creative_id!r} sized {creative.get('sizes')!r} within "
        f"{creative.get('size_ranges')!r}, not within {expected!r} with no fixed size"
    )


@then(
    parsers.parse('the ad server\'s creative manager is handed creative "{handed}" once and not creative "{withheld}"')
)
def then_creative_manager_handed_one_not_other(ctx: dict, handed: str, withheld: str) -> None:
    """The operator path pushed *handed* once, which proves it reached the upload, and withheld *withheld*.

    Read by ``creative_id``, a key the asset carries today, so the verdict is about which
    creative was pushed and not about the typed creative.
    """
    _received_once(ctx, handed)
    received = [asset.get("creative_id") for asset in _handed(ctx, _CREATIVE_MANAGER)]
    assert withheld not in received, (
        f"the ad server's creative manager was handed {withheld!r}, a creative its product's option refuses: "
        f"{received!r}"
    )


@then(
    parsers.parse(
        'the ad server\'s creative manager is handed creative "{creative_id}" once, assigned to packages '
        '"{package_ids}", sized "{sizes}" on each'
    )
)
def then_creative_manager_handed_once_per_buy(ctx: dict, creative_id: str, package_ids: str, sizes: str) -> None:
    """One asset for *creative_id*, carrying an assignment per package, each with that package's creative at *sizes*.

    A creative on two packages of a buy is one creative at the ad server, associated with
    each package's line item: handing it over once per package creates it twice.
    """
    assignments = _received_once(ctx, creative_id).get("package_assignments") or []
    # Sorted by package: the assignment rows come back in no stated order.
    observed = sorted((a.get("package_id"), (a.get("creative") or {}).get("sizes")) for a in assignments)
    expected = sorted((package_id, parse_sizes(sizes)) for package_id in comma_list(package_ids))
    assert observed == expected, (
        f"{_CREATIVE_MANAGER} was handed creative {creative_id!r} assigned as (package, sizes) {observed!r}, "
        f"not {expected!r}"
    )


@then(
    parsers.parse(
        'the seller\'s approval of creative "{withheld}" tells the seller it was not pushed to the live media buy, '
        'with code "{code}", and the approval of creative "{pushed}" reports nothing'
    )
)
def then_seller_told_creative_withheld(ctx: dict, withheld: str, code: str, pushed: str) -> None:
    """The Admin UI route's answer names the withheld creative, the buy and the refusal's code; the pushed one's is clean."""
    approvals = ctx["seller_creative_approvals"]
    buy_id = ctx["live_media_buy"].media_buy_id
    assert approvals[withheld].get("warnings") == [f"Creative {withheld} was not pushed to buy {buy_id}: {code}"], (
        f"expected the seller to be told {withheld!r} was not pushed to {buy_id!r} with {code}; "
        f"the approval answered {approvals[withheld]!r}"
    )
    assert "warnings" not in approvals[pushed], (
        f"the approval of {pushed!r}, which was pushed, answered {approvals[pushed]!r}"
    )


@then("the ad server's creative manager is handed no creative")
def then_creative_manager_handed_no_creative(ctx: dict) -> None:
    """Neither operator path's upload ran."""
    handed = _handed(ctx, _CREATIVE_MANAGER)
    assert handed == [], f"the ad server's creative manager was handed {[a.get('creative_id') for a in handed]!r}"


@then("the ad server is handed no creative")
def then_ad_server_handed_no_creative(ctx: dict) -> None:
    """The synchronous create uploaded nothing to the ad server."""
    uploaded = _handed(ctx, "the ad server")
    assert uploaded == [], f"the ad server was handed {[a.get('creative_id') for a in uploaded]!r}"


@then(
    parsers.re(
        r"the creative agent's preview page (?:\"(?P<preview_url>[^\"]+)\" )?appears nowhere in what the ad server "
        r'receives for creative "(?P<creative_id>[^"]+)"$'
    )
)
def then_preview_page_not_handed(ctx: dict, preview_url: str | None, creative_id: str) -> None:
    """No key of the handed asset, legacy or typed, carries the preview page: the row's, or the one the agent rendered."""
    page = preview_url or PREVIEW_URL
    asset = _received_asset(ctx, "the ad server", creative_id)
    assert page not in json.dumps(asset), (
        f"the ad server was handed the creative agent's preview page {page!r} for creative {creative_id!r}: {asset!r}"
    )


@then(parsers.parse('the tenant\'s own creative agent was asked to preview the html "{content}"'))
def then_hosted_agent_previewed(ctx: dict, content: str) -> None:
    """The seller asked its hosted agent for a preview of a manifest carrying *content*, read off the agent's record.

    So the preview page the agent answered was offered to the seller, and a Then saying the
    page appears nowhere downstream grades a page the seller held.
    """
    calls = ctx["env"].hosted_agent_calls(PEER_PREVIEW_TOOL)
    previewed = [call for call in calls if content in json.dumps(call)]
    assert len(previewed) == 1, (
        f"expected one preview request carrying {content!r}; the tenant's agent was asked {calls!r}"
    )


@then(parsers.parse("the {error_code:w} refusal's problems are exactly, one per creative:"))
def then_refusal_problems_per_creative(ctx: dict, error_code: str, datatable: Any) -> None:
    """``details.problems[]`` is exactly the table's problems, each about the creative its row names.

    Columns: ``subject_id | field | code``. ``ErrorProblem`` (src/core/errors/details.py) names
    its subject (``subject_type``, ``subject_id``), the manifest path within that subject the
    problem is about (``field``, e.g. ``assets.image_main.width``) and its code from the code
    table. Two creatives can fail at the same path, so the problems are told apart by their
    subject. The whole list is compared, so a missing creative, an invented one, or one
    creative's problem repeated in place of another's fails it.
    """
    problems = ctx["result"].wire_error_details(error_code).get("problems") or []
    held = sorted(
        (str(p.get("subject_type")), str(p.get("subject_id")), str(p.get("field")), str(p.get("code")))
        for p in problems
    )
    expected = sorted(("creative", row["subject_id"], row["field"], row["code"]) for row in table_rows(datatable))
    assert held == expected, f"the refusal's problems are {held}, not {expected}: {problems}"
