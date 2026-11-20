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

from tests.bdd.steps.generic._creative_seeding import store_creative, store_package_creative
from tests.bdd.steps.generic.given_media_buy import (
    _ensure_request_defaults,
    _publish_on_default_product,
    _seed_auto_approval,
    accept_on_default_product,
)
from tests.e2e.webhook_capture_service import PEER_PREVIEW_TOOL
from tests.factories.creative import CreativeAssignmentFactory
from tests.factories.creative_asset import (
    DISPLAY_TAG_SLOT,
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


def _tag_url_assets(url: str) -> dict[str, Any]:
    """A tag URL creative's assets: one display_tag asset delivered as ``tag_url``."""
    return build_assets(display_tag_spec("tag_url", url=url))


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


@given(
    parsers.re(
        r'the package references the buyer\'s approved display_tag creative "(?P<creative_id>[^"]+)" carrying inline '
        r"(?P<markup_type>\w+) markup, (?:recorded with the creative agent's preview page as its url"
        r"|for a format option whose only delivery type is (?P<option_delivery_type>\w+))$"
    )
)
def given_inline_markup_creative(
    ctx: dict, creative_id: str, markup_type: str, option_delivery_type: str | None
) -> None:
    """An inline-markup display_tag creative, either recorded with the preview back-fill or on a narrower option.

    Recorded: sync used to write the first render's ``preview_url`` into the root ``url``, and
    its dimensions, when the buyer sent none; an inline-markup manifest sends neither, so a row
    synced then carries exactly this, for an option accepting inline markup. On a narrower
    option: the option's ``supported_delivery_types`` names only *option_delivery_type*, and
    the row carries no back-fill.
    """
    assets = build_assets(display_tag_spec("inline_markup", markup_type=markup_type, markup=INLINE_MARKUP))
    if option_delivery_type is None:
        store_package_creative(ctx, creative_id, assets, kind="display_tag", url=PREVIEW_URL, width=300, height=250)
        _publish_on_default_product(ctx, _display_tag_option("inline_markup"))
    else:
        store_package_creative(ctx, creative_id, assets, kind="display_tag")
        _publish_on_default_product(ctx, _display_tag_option(option_delivery_type))


@given(
    parsers.re(
        r"the package references the buyer's approved display_tag creative \"(?P<creative_id>[^\"]+)\" requesting "
        r'ad tag "(?P<url>[^"]+)"(?:, for a format option accepting (?:sizes "(?P<sizes>[^"]+)"|' + _RANGE + r"))?$"
    )
)
def given_tag_url_creative(
    ctx: dict,
    creative_id: str,
    url: str,
    sizes: str | None,
    min_width: str | None,
    max_width: str | None,
    min_height: str | None,
    max_height: str | None,
) -> None:
    """A display_tag asset delivered as a tag URL, for a fixed 300x250 option, a multi-size one or a responsive one."""
    size_params: dict[str, Any] | None = None
    if sizes:
        size_params = {"sizes": parse_sizes(sizes)}
    elif min_width and max_width and min_height and max_height:
        size_params = _size_range(min_width, max_width, min_height, max_height)
    store_package_creative(ctx, creative_id, _tag_url_assets(url), kind="display_tag")
    _publish_on_default_product(ctx, _display_tag_option("tag_url", size_params))


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
    parsers.re(
        r"the package references the buyer's approved display_tag creative \"(?P<creative_id>[^\"]+)\" whose "
        r'tag_url slot carries "(?P<url>[^"]+)" with (?:url_type "(?P<url_type>[^"]+)"|no url_type)$'
    )
)
def given_legacy_tag_url_creative(ctx: dict, creative_id: str, url: str, url_type: str | None) -> None:
    """The kind's backward-compatible ``tag_url`` slot: a url asset, with or without its own ``url_type``.

    The option overrides no slot, so the kind's default slots apply, the ones
    formats/canonical/display_tag.json declares.
    """
    assets = build_assets(legacy_tag_url_spec(url, url_type=url_type))
    store_package_creative(ctx, creative_id, assets, kind="display_tag")
    _publish_on_default_product(ctx, {"format_kind": "display_tag", "params": {"width": 300, "height": 250}})


@given(
    parsers.parse(
        'the package references the buyer\'s approved display_tag creative "{creative_id}" redirecting through '
        'ad request "{ad_request_url}" and clickthrough "{clickthrough_url}"'
    )
)
def given_paired_redirect_creative(ctx: dict, creative_id: str, ad_request_url: str, clickthrough_url: str) -> None:
    """A paired-redirect display_tag asset: both URLs in one asset, as the schema requires."""
    tag = display_tag_spec("paired_redirect", ad_request_url=ad_request_url, clickthrough_url=clickthrough_url)
    store_package_creative(ctx, creative_id, build_assets(tag), kind="display_tag")
    _publish_on_default_product(ctx, _display_tag_option("paired_redirect"))


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
        r'the package references the buyer\'s approved html5 creative "(?P<creative_id>[^"]+)" '
        r'(?:bundled at "(?P<bundle_url>[^"]+)"|whose zip bundle names no url) with backup image "(?P<backup_url>[^"]+)"$'
    )
)
def given_html5_bundle_creative(ctx: dict, creative_id: str, bundle_url: str | None, backup_url: str) -> None:
    """The html5 kind's two slots, ``html5_bundle`` (zip) and ``backup_image`` (formats/canonical/html5.json).

    "names no url" stores a zip asset without the ``url`` core/assets/zip-asset.json requires.
    """
    assets = build_assets(*html5_bundle_specs(bundle_url, backup_url=backup_url))
    store_package_creative(ctx, creative_id, assets, kind="html5")
    _publish_on_default_product(ctx, {"format_kind": "html5", "params": {"width": 300, "height": 250}})


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


def _image_option(size_params: dict[str, Any]) -> dict[str, Any]:
    """An image format option of that size mode, named so a package can select it by ``format_option_refs``."""
    return {"format_option_id": "image_option", "format_kind": "image", "params": size_params}


def _image_assets(width: int, height: int, url: str) -> dict[str, Any]:
    """A canonical image creative's ``image_main`` slot, the file *width* by *height*."""
    return build_assets(image_spec("image_main", url=url, width=width, height=height))


@given(
    parsers.re(
        r'the package references the buyer\'s approved (?P<width>\d+)x(?P<height>\d+) image creative "(?P<creative_id>'
        r'[^"]+)" showing "(?P<url>[^"]+)"(?:, for a format option accepting sizes "(?P<sizes>[^"]+)")?$'
    )
)
def given_sized_image_creative(
    ctx: dict, width: str, height: str, creative_id: str, url: str, sizes: str | None
) -> None:
    """A canonical image creative whose file is *width* by *height*; with the clause, the image option accepting *sizes*.

    Without the clause the product's formats are another Given's: a GAM seller's product
    formats, or the option an earlier creative's Given published.
    """
    store_package_creative(ctx, creative_id, _image_assets(int(width), int(height), url), kind="image")
    if sizes is not None:
        _publish_on_default_product(ctx, _image_option({"sizes": parse_sizes(sizes)}))


@given(parsers.re(r'the product publishes an? (?P<kind>\w+) format option accepting sizes "(?P<sizes>[^"]+)"$'))
def given_product_publishes_sized_option(ctx: dict, kind: str, sizes: str) -> None:
    """The default product's one option of *kind* (a display_tag option takes its tag URL), accepting *sizes*."""
    assert kind in ("display_tag", "image"), f"no option builder for kind {kind!r}"
    size_params = {"sizes": parse_sizes(sizes)}
    option = _display_tag_option("tag_url", size_params) if kind == "display_tag" else _image_option(size_params)
    _publish_on_default_product(ctx, option)


@given(
    parsers.re(
        r'the buyer holds a live media buy booked at the ad server as order "(?P<order_id>[^"]+)" with package '
        r'"(?P<package_id>[^"]+)"(?:, and package "(?P<gone_package_id>[^"]+)" on product "(?P<gone_product_id>'
        r'[^"]+)", which the seller no longer has)?$'
    )
)
def given_live_booked_buy(
    ctx: dict, order_id: str, package_id: str, gone_package_id: str | None, gone_product_id: str | None
) -> None:
    """An active buy, already booked at the ad server, as create leaves it.

    Its first package is on the default product. The second, when stated, was booked on a
    product the seller has since removed: its stored ``product_id`` names no product row.
    """
    line_items = {package_id: "line-item-live"}
    package_products: dict[str, str] = {}
    if gone_package_id is not None and gone_product_id is not None:
        line_items[gone_package_id] = "line-item-gone"
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
        r'(?:a display_tag requesting ad tag "(?P<tag_url>[^"]+)"'
        r'|a (?P<width>\d+)x(?P<height>\d+) image showing "(?P<image_url>[^"]+)"'
        r'|an html5 bundle from the buyer\'s bundle host with backup image "(?P<backup_url>[^"]+)"), is assigned to '
        r'(?:packages? "(?P<package_ids>[^"]+)" of the live media buy|the media buy awaiting approval)$'
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
    backup_url: str | None,
    package_ids: str | None,
) -> None:
    """A creative already assigned to a stored buy: the live one's named packages, or the held buy's one package.

    The rows create leaves for a creative it held back (under review), or a creative assigned
    to the buy before the product's option changed (approved). Either way it was never pushed:
    the seller's approval of the creative, or of the buy, is what pushes it. An html5 bundle's
    zip is served by the buyer's bundle host (``serve_html5_bundle``), and its bytes are kept
    to compare with what the ad server receives.
    """
    env = ctx["env"]
    if package_ids is not None:
        media_buy = ctx["live_media_buy"]
        packages = [package_id.strip() for package_id in package_ids.split(",")]
    else:
        (media_buy,) = env.media_buys_awaiting_approval()
        packages = [package.package_id for package in media_buy.packages]
    if tag_url is not None:
        kind, assets = "display_tag", _tag_url_assets(tag_url)
    elif backup_url is not None:
        ctx["html5_bundle"] = html5_zip_bundle()
        bundle_url = env.serve_html5_bundle(ctx["html5_bundle"])
        kind, assets = "html5", build_assets(*html5_bundle_specs(bundle_url, backup_url=backup_url))
    else:
        kind, assets = "image", _image_assets(int(str(width)), int(str(height)), str(image_url))
    creative = store_creative(
        ctx, creative_id, assets, kind=kind, status="approved" if status == "approved" else "pending_review"
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


def _received_asset(ctx: dict, receiver: str, creative_id: str) -> dict[str, Any]:
    """The newest asset dict *receiver* was handed for *creative_id*; fails when it was handed none."""
    env = ctx["env"]
    handed = env.uploaded_adapter_assets() if receiver == "the ad server" else env.creative_manager_assets()
    mine = [asset for asset in handed if asset.get("creative_id") == creative_id]
    assert mine, (
        f"{receiver} was never handed creative {creative_id!r}; it was handed "
        f"{[asset.get('creative_id') for asset in handed]!r}"
    )
    return mine[-1]


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


@then(parsers.parse('the ad server\'s creative manager is handed creative "{handed}" and not creative "{withheld}"'))
def then_creative_manager_handed_one_not_other(ctx: dict, handed: str, withheld: str) -> None:
    """The operator path pushed *handed*, which proves it reached the upload, and withheld *withheld*.

    Read by ``creative_id``, a key the asset carries today, so the verdict is about which
    creative was pushed and not about the typed creative.
    """
    received = [asset.get("creative_id") for asset in ctx["env"].creative_manager_assets()]
    assert handed in received, (
        f"the ad server's creative manager was never handed {handed!r} (it was handed {received!r}), so this "
        f"scenario cannot tell whether {withheld!r} was withheld or the path never reached the upload"
    )
    assert withheld not in received, (
        f"the ad server's creative manager was handed {withheld!r}, a creative its product's option refuses: "
        f"{received!r}"
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
    handed = ctx["env"].creative_manager_assets()
    assert handed == [], f"the ad server's creative manager was handed {[a.get('creative_id') for a in handed]!r}"


@then("the ad server is handed no creative")
def then_ad_server_handed_no_creative(ctx: dict) -> None:
    """The synchronous create uploaded nothing to the ad server."""
    uploaded = ctx["env"].uploaded_adapter_assets()
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


@then(parsers.parse('the {error_code:w} refusal\'s problems name exactly the creatives "{creative_ids}"'))
def then_refusal_problems_name_creatives(ctx: dict, error_code: str, creative_ids: str) -> None:
    """The creatives ``details.problems[]`` is about are exactly *creative_ids*, comma-separated: none missing, none invented."""
    problems = ctx["result"].wire_error_details(error_code).get("problems") or []
    named = {problem.get("subject_id") for problem in problems if problem.get("subject_type") == "creative"}
    expected = {creative_id.strip() for creative_id in creative_ids.split(",")}
    assert named == expected, (
        f"the refusal's problems name creatives {sorted(named)}, not {sorted(expected)}: {problems}"
    )


@then(
    parsers.parse('the {error_code:w} refusal names creative "{creative_id}" at "{field}" with problem code "{code}"')
)
def then_refusal_problem_names_creative(ctx: dict, error_code: str, creative_id: str, field: str, code: str) -> None:
    """``details.problems[]`` holds one problem about *creative_id* at manifest path *field*, coded *code*.

    ``ErrorProblem`` (src/core/errors/details.py) classifies each problem with the code table's
    vocabulary (``code``) and names its subject (``subject_type``, ``subject_id``) and the
    manifest path it is about (``field``, e.g. ``assets.image_main.width``). Two creatives can
    fail at the same path, so a problem is found by its subject and its path together.
    """
    problems = ctx["result"].wire_error_details(error_code).get("problems") or []
    mine = [
        (problem.get("subject_type"), problem.get("code"))
        for problem in problems
        if (problem.get("subject_id"), problem.get("field")) == (creative_id, field)
    ]
    assert mine == [("creative", code)], (
        f"expected one problem about creative {creative_id!r} at {field!r} coded {code!r}; the refusal's "
        f"problems are {problems}"
    )
