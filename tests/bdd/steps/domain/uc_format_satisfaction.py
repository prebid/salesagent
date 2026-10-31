"""Steps for BR-UC-FORMAT-SATISFACTION: a format claim against a canonical declaration.

Setup stores a REAL dual-arm ``products`` row through ``ProductFactory`` -- the same factory
every other product fixture uses -- and the refusal is read off the WIRE through
``assert_wire_error``, which is the sanctioned error surface.

THREE TOOLS, because the satisfaction function has gates on three paths and the bead grades
all three. ``create_media_buy`` reuses the shared When from ``uc002_create_media_buy``;
``update_media_buy`` and ``sync_creatives`` get their own Whens here, which dispatch through
the same parametrized transport. The two in-feature envs differ and are routed by tag:
``@format_satisfaction`` takes ``MediaBuyDualEnv`` (create AND update dispatch off one
seeded chain) and ``@format_satisfaction_sync`` takes ``CreativeSyncEnv``.

TWO TABLES, and both of them are DATA rather than behaviour: one maps a declaration name to
the ``format_options`` document the seller stores, the other maps a selector name to the
package members the buyer sends. They are stored documents and request members -- the
faithful shapes -- not a registry of objects a factory should be building:
``core/product-format-declaration.json`` generates a sixteen-branch discriminated union over
``format_kind``, so the column holds a document and the model picks its branch on readback.
Naming each row lets the feature's Examples say which rule it is exercising instead of
carrying JSON a reader has to decode.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import (
    assert_sync_entry_refused_and_not_stored,
    parked_media_buy,
    stored_package_config,
    wire_absent,
    wire_entry,
    wire_entry_errors,
    wire_envelope_errors,
    wire_field,
)
from tests.factories import PricingOptionFactory, ProductFactory, StoredPlacementFactory
from tests.factories.creative import DEFAULT_CREATIVE_AGENT_URL

#: The legacy arm every product here also carries, matching ``MediaBuyCreateEnv``'s own
#: seeded default so the deprecated selector route and the rest of the create flow behave
#: exactly as they do for every other create scenario. The canonical arm is what varies.
_LEGACY_ARM: list[dict[str, str]] = [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250"}]

#: THE SAME legacy entry, SIZED. Every other product fixture in this module leaves the
#: deprecated arm's width and height unset, which is why nothing here could grade the
#: dimension-precedence rule: with no dimensions on the product's catalogue entry, "the
#: buyer's own sizing wins" and "the product's entry fills in" give the SAME answer, so
#: inverting the rule changed nothing (salesagent-49m16, measured: 0 of 8759 nodeids).
#: The conjunction needed a fixture, and this is it.
_LEGACY_ARM_SIZED: list[dict[str, Any]] = [
    {"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250", "width": 300, "height": 250}
]

#: The creative agent's host, which every spelling below is written against.
_AGENT_HOST = urlsplit(DEFAULT_CREATIVE_AGENT_URL).netloc

#: Spellings of the creative agent's url, by the name a scenario gives them. AUTHORED, never
#: computed by ``canonical_agent_url``: asking the canonicalizer under test to build its own
#: inputs would compare the implementation with itself.
#:
#: The first three name the SAME agent under docs/reference/url-canonicalization at the pin,
#: one algorithm step each -- step 2 lowercases the host, step 2 removes "a single trailing
#: [root-label] dot", step 5 applies ``remove_dot_segments`` to the path (``/x/..`` is ``/``,
#: which is also what step 5 gives the empty path). The host case is the CONTROL: the SDK's
#: own comparison already applied that step, so a row that only ever passed proves the
#: scenario can pass. The last names a DIFFERENT agent, because "canonicalization removes
#: only dot segments, never real path segments" -- it is the row a seller that collapsed
#: too much would fail.
#:
#: "a mounted path" is the standard creative agent served at ``/mcp``. It is a different
#: FORMAT IDENTITY by the same rule ``a different path`` is, and the same AGENT for the
#: registry's projection: the owner's ruling is that an agent's identity is its origin and
#: that legacy v1 ids map to a kind at the reference agent's origin, whatever path it is
#: mounted at (``format_resolver.registry_addressed``).
_AGENT_URL_SPELLINGS: dict[str, str] = {
    "an upper-cased host": f"https://{_AGENT_HOST.upper()}",
    "a trailing root-label dot": f"https://{_AGENT_HOST}.",
    "a removable dot segment": f"https://{_AGENT_HOST}/x/..",
    "a different path": f"https://{_AGENT_HOST}/other-agent",
    "a mounted path": f"https://{_AGENT_HOST}/mcp",
}


def _agent_url_spelled(variant: str) -> str:
    """The creative agent's url in the named spelling, refusing a row that respells nothing.

    The spellings are written against a bare-authority url, so the guard checks that is
    still what the factory's default is: an agent url that grew a path would turn
    ``/x/..`` into a different path and the dot-segment row into a different agent.
    """
    assert DEFAULT_CREATIVE_AGENT_URL == f"https://{_AGENT_HOST}", (
        f"the spellings are written for a bare https authority; the default agent is now "
        f"{DEFAULT_CREATIVE_AGENT_URL!r}, so rewrite them against its path"
    )
    spelled = _AGENT_URL_SPELLINGS.get(variant)
    assert spelled is not None, f"no agent url spelling named {variant!r}; have {sorted(_AGENT_URL_SPELLINGS)}"
    assert spelled != DEFAULT_CREATIVE_AGENT_URL, f"{variant!r} does not respell the agent url at all"
    return spelled


#: The option id the product publishes, and the id a publisher-scoped declaration publishes
#: under its own domain. Named once so a selector row and its declaration cannot drift.
_PRODUCT_OPTION_ID = "satisfaction_image_mrec"
_PUBLISHER_DOMAIN = "shorts.streamhaus.example"

#: A legacy id the SELLER names in a declaration's ``v1_format_ref`` and nothing else
#: resolves: no registry glob, no size-encoded slug for the SDK to upgrade, absent from the
#: reference capture. Named once so the declaration and the selector cannot drift.
_HOUSE_LEGACY_ID = "satisfaction_house_mrec"

#: What the seller STORES, per declaration name. Each is a
#: ``core/product-format-declaration.json`` document.
_DECLARATIONS: dict[str, list[dict[str, Any]]] = {
    # The conformance fixture's own shape: an image kind fixed at 300x250, publishing a
    # product-local option id and asserting the legacy id it projects to. The size cap and
    # the file-format set are the pinned canonical_formats_mrec_display declaration's own,
    # and they are what make ``exact_300x250`` -- which states neither -- grade the pin's
    # inheritance rule rather than a declaration with nothing else to omit.
    "fixed_300x250": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "v1_format_ref": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250"}],
            "params": {"width": 300, "height": 250, "max_file_size_kb": 200, "image_formats": ["jpg", "png"]},
        }
    ],
    # A 728x90 image asserting its own legacy id. Linked as an inventory PROFILE'S contract
    # beside a product declaring ``fixed_300x250``, so the profile's arm and the product's
    # own differ in the id the ad server is asked for.
    "leaderboard_728x90": [
        {
            "format_kind": "image",
            "format_option_id": "satisfaction_image_leaderboard",
            "v1_format_ref": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_728x90"}],
            "params": {"width": 728, "height": 90},
        }
    ],
    # The same fixed size with a NUMERIC limit whose name is no bound prefix. A claim silent
    # about it inherits it exactly as it inherits ``max_file_size_kb``: what decides
    # silence is the pin's list of constraints a claim must state, not the constraint's
    # shape.
    "text_limited_300x250": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "params": {"width": 300, "height": 250, "headline_max_chars": 40},
        }
    ],
    # A RANGE constraint, which formats.mdx grades by containment rather than overlap.
    "duration_range": [
        {
            "format_kind": "video_hosted",
            "format_option_id": "satisfaction_video_range",
            "params": {"duration_ms_range": [5000, 60000]},
        }
    ],
    # An EXACT duration, the other half of "Exact dimensions and durations must match"
    # (formats.mdx). ``duration_ms_exact`` is one of the four constraints a claim must state.
    "exact_duration": [
        {
            "format_kind": "video_hosted",
            "format_option_id": "satisfaction_video_exact",
            "params": {"duration_ms_exact": 30000},
        }
    ],
    # BOTH duration keys, so the pin's "Duration precedence is duration_ms_exact >
    # duration_ms_range" (core/product-format-declaration.json) decides which one a selector
    # is held to: the exact value is ``exact_duration``'s, the range ``duration_range``'s.
    "exact_over_range": [
        {
            "format_kind": "video_hosted",
            "format_option_id": "satisfaction_video_exact_in_range",
            "params": {"duration_ms_exact": 30000, "duration_ms_range": [5000, 60000]},
        }
    ],
    # A REQUIRED SLOT, which formats.mdx grades on both the group and the asset type.
    "required_slots": [
        {
            "format_kind": "video_hosted",
            "format_option_id": "satisfaction_video_slots",
            "params": {"slots": [{"asset_group_id": "video_main", "asset_type": "video", "required": True}]},
        }
    ],
    # A PUBLISHER-SCOPED option. The domain's presence is the pin's scope discriminator, so
    # this option and a product-local one sharing the id are different options.
    "publisher_300": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "publisher_domain": _PUBLISHER_DOMAIN,
            "params": {"width": 300, "height": 250},
        }
    ],
    # TWO declarations of ONE kind, which is work item M2's shape and the case
    # ``core/product-format-declaration.json`` names: ``format_option_id`` is "REQUIRED
    # when a product contains multiple declarations with the same format_kind". A bare
    # ``format_kind: image`` selector has to be graded against the declaration its params
    # satisfy, not against whichever is declared first.
    "two_image_options": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "params": {"width": 300, "height": 250},
        },
        {
            "format_kind": "image",
            "format_option_id": "satisfaction_image_leaderboard",
            "params": {"width": 728, "height": 90},
        },
    ],
    # TWO 300x250 image declarations that differ only in the file formats they take. Both
    # NARROW the v1 projection of ``display_300x250`` (canonical-formats.mdx § "Narrows"),
    # so that legacy id selects BOTH, and an option ref naming either one selects a
    # different set (media-buy/package-request.json step (2)).
    "two_mrec_options": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "params": {"width": 300, "height": 250, "image_formats": ["jpg", "png"]},
        },
        {
            "format_kind": "image",
            "format_option_id": "satisfaction_image_mrec_gif",
            "params": {"width": 300, "height": 250, "image_formats": ["gif"]},
        },
    ],
    # A 300x250 image whose OTHER slots carry their own limits (formats/canonical/_base.json
    # ``slots[]``): a required headline of at most 25 characters and at most two of them, a
    # body text the declaration caps at 90 characters (the flat ``body_text_max_chars``), and
    # an optional logo capped at 50 KB -- "1 KB = 1,000 bytes" -- at 1x or 2x.
    "slot_limited_300x250": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "params": {
                "width": 300,
                "height": 250,
                "body_text_max_chars": 90,
                "slots": [
                    {"asset_group_id": "image_main", "asset_type": "image", "required": True},
                    {"asset_group_id": "headline", "asset_type": "text", "required": True, "max_chars": 25, "max": 2},
                    {"asset_group_id": "body_text", "asset_type": "text", "required": False},
                    {
                        "asset_group_id": "logo",
                        "asset_type": "image",
                        "required": False,
                        "max_size_kb": 50,
                        "pixel_ratios": [1, 2],
                    },
                ],
            },
        }
    ],
    # A RESPONSIVE image (600 to 1200 wide) held to a 1.91:1 aspect ratio. image.json: the
    # ratio, "When used with sizes[] or responsive ranges, narrows accepted entries to those
    # matching the aspect ratio".
    "responsive_1_91": [
        {
            "format_kind": "image",
            "format_option_id": "satisfaction_image_landscape",
            "params": {"min_width": 600, "max_width": 1200, "aspect_ratio": "1.91:1"},
        }
    ],
    # A hosted-video option narrowing the file it takes: one codec, one container, a 10 MB
    # cap, a bitrate ceiling of 8,000 kbps and 25 or 30 frames per second.
    # formats/canonical/video_hosted.json's ``max_file_size_mb``: "1 MB is exactly 1,000,000
    # bytes".
    "mp4_h264_10mb_video": [
        {
            "format_kind": "video_hosted",
            "format_option_id": "satisfaction_video_mp4",
            "params": {
                "video_codecs": ["h264"],
                "containers": ["mp4"],
                "max_file_size_mb": 10,
                "max_bitrate_kbps": 8000,
                "frame_rates": [25, 30],
            },
        }
    ],
    # FLAT PER-SLOT PARAMS, which formats/canonical/_base.json puts on the declaration: "flat
    # ``headline_max_chars`` on responsive_creative". A responsive creative's ``headlines``
    # pool limited by ``headline_max_chars`` and ``headlines_max`` (the param names the role
    # in the singular and in the plural); a carousel's cards whose ``headline`` member
    # ``card_headline_max_chars`` limits; an audio spot with a loudness target, a true-peak
    # ceiling and a required square companion image capped at 100 KB; and a hosted video
    # requiring captions, with a 300x250 companion banner. Each states its own slots where the
    # kind's defaults would require more than the scenario sends.
    "responsive_headlines": [
        {
            "format_kind": "responsive_creative",
            "format_option_id": "satisfaction_responsive",
            "params": {
                "headline_max_chars": 30,
                "headlines_max": 2,
                "slots": [{"asset_group_id": "headlines", "asset_type": "text", "required": True, "min": 1, "max": 5}],
            },
        }
    ],
    "carousel_card_headlines": [
        {
            "format_kind": "image_carousel",
            "format_option_id": "satisfaction_carousel",
            "params": {
                "card_headline_max_chars": 25,
                "slots": [{"asset_group_id": "cards", "asset_type": "card", "required": True, "min": 2, "max": 10}],
            },
        }
    ],
    "audio_with_companion": [
        {
            "format_kind": "audio_hosted",
            "format_option_id": "satisfaction_audio_companion",
            "params": {
                "loudness_lufs": -16,
                "loudness_tolerance_db": 1,
                "true_peak_dbfs": -2,
                "companion_image_required": True,
                "companion_image_aspect_ratio": "1:1",
                "companion_image_max_file_size_kb": 100,
            },
        }
    ],
    "captioned_video_with_banner": [
        {
            "format_kind": "video_hosted",
            "format_option_id": "satisfaction_video_captioned",
            "params": {"captions": "required", "companion_banner_widths": [300], "companion_banner_heights": [250]},
        }
    ],
    # THE REST OF THE CAROUSEL'S FLAT PARAMS (formats/canonical/image_carousel.json): three or
    # four cards (``min_cards`` / ``max_cards`` narrowing the slot's own 2 to 10), square card
    # media (``card_aspect_ratio``), image or video media (``allowed_card_media_asset_types``),
    # an image card capped at 100 KB and a video card at 1,000 KB and 15 seconds
    # (``card_image_max_file_size_kb``, ``card_video_max_file_size_kb``,
    # ``card_video_max_duration_ms``), and two permitted per-card calls to action
    # (``cta_values``, which core/assets/card-asset.json applies to each card's ``cta``).
    "carousel_card_media": [
        {
            "format_kind": "image_carousel",
            "format_option_id": "satisfaction_carousel_media",
            "params": {
                "min_cards": 3,
                "max_cards": 4,
                "card_aspect_ratio": "1:1",
                "allowed_card_media_asset_types": ["image", "video"],
                "card_image_max_file_size_kb": 100,
                "card_video_max_file_size_kb": 1000,
                "card_video_max_duration_ms": 15000,
                "cta_values": ["SHOP_NOW", "LEARN_MORE"],
                "slots": [{"asset_group_id": "cards", "asset_type": "card", "required": True, "min": 2, "max": 10}],
            },
        }
    ],
    # THE REST OF THE RESPONSIVE CREATIVE'S FLAT PARAMS (formats/canonical/responsive_creative.json):
    # videos of 6 to 30 seconds (``video_min_duration_ms`` / ``video_max_duration_ms``), logos at
    # 1:1 or 4:1 (``logo_aspect_ratios``), and every image the pool holds capped at 150 KB
    # (``asset_image_max_file_size_kb``). The slots are stated so that only the headline is
    # required and the media pools are optional, each manifest filling the one under test.
    "responsive_pool_limits": [
        {
            "format_kind": "responsive_creative",
            "format_option_id": "satisfaction_responsive_pool",
            "params": {
                "video_min_duration_ms": 6000,
                "video_max_duration_ms": 30000,
                "logo_aspect_ratios": ["1:1", "4:1"],
                "asset_image_max_file_size_kb": 150,
                "slots": [
                    {"asset_group_id": "headlines", "asset_type": "text", "required": True, "min": 1, "max": 5},
                    {"asset_group_id": "images_landscape", "asset_type": "image", "required": False, "max": 20},
                    {"asset_group_id": "video", "asset_type": "video", "required": False, "max": 5},
                    {"asset_group_id": "logo", "asset_type": "image", "required": False, "max": 5},
                ],
            },
        }
    ],
    # A CLOSED SET OF CALLS TO ACTION on each kind that declares ``cta_values``: image.json
    # ("Permitted CTA values for this product"), native_in_feed.json ("When set, narrows the cta
    # slot to a closed enum") and video_hosted.json. Each kind's default slots are kept, so the
    # ``cta`` slot is the canonical one and stays optional.
    "image_cta_300x250": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "params": {"width": 300, "height": 250, "cta_values": ["LEARN_MORE", "SHOP_NOW"]},
        }
    ],
    "native_cta": [
        {
            "format_kind": "native_in_feed",
            "format_option_id": "satisfaction_native_cta",
            "params": {"cta_values": ["LEARN_MORE", "SIGN_UP"]},
        }
    ],
    "video_cta": [
        {
            "format_kind": "video_hosted",
            "format_option_id": "satisfaction_video_cta",
            "params": {"cta_values": ["WATCH_MORE", "SHOP_NOW"]},
        }
    ],
    # A native unit capping its images at 100 KB: native_in_feed.json ``max_image_file_size_kb``,
    # "Maximum file size in kilobytes for main_image and icon". The default slots are kept, so
    # both image slots are the canonical optional ones.
    "native_image_cap": [
        {
            "format_kind": "native_in_feed",
            "format_option_id": "satisfaction_native_image_cap",
            "params": {"max_image_file_size_kb": 100},
        }
    ],
    # A VERTICAL hosted video stating no aspect_ratio. video_hosted.json: "Vertical = 9:16",
    # and aspect_ratio is "Inferred from orientation if omitted".
    "vertical_video": [
        {
            "format_kind": "video_hosted",
            "format_option_id": "satisfaction_video_vertical",
            "params": {"orientation": "vertical"},
        }
    ],
    # A hosted-audio spot fixed at 30 seconds, mp3 only, capped at 1 MB, sampled at 44.1 or
    # 48 kHz, in stereo. audio_hosted.json's ``max_file_size_mb`` is in "decimal megabytes".
    "mp3_30s_audio": [
        {
            "format_kind": "audio_hosted",
            "format_option_id": "satisfaction_audio_30s",
            "params": {
                "duration_ms_exact": 30000,
                "audio_codecs": ["mp3"],
                "max_file_size_mb": 1,
                "audio_sample_rates": [44100, 48000],
                "audio_channels": ["stereo"],
            },
        }
    ],
    # A video option fixed at 15 seconds. ``duration_ms_exact`` is the declaration's
    # spelling of one fixed duration; a legacy reference spells it ``duration_ms``.
    "fixed_15s_video": [
        {
            "format_kind": "video_hosted",
            "format_option_id": "satisfaction_video_15s",
            "params": {"duration_ms_exact": 15000},
        }
    ],
    # A hosted-audio option with no constraint of its own. It exists for a legacy reference
    # the registry has no glob for -- the reference agent's ``audio_standard_30s`` -- so the
    # only step of registries/v1-canonical-mapping.json's order that projects it is step 2,
    # the served declaration's own ``canonical.kind``.
    "any_audio": [
        {
            "format_kind": "audio_hosted",
            "format_option_id": "satisfaction_audio",
            "params": {},
        }
    ],
    # A 300x250 image declaration with no other constraint, and the same declaration
    # accepting Retina renditions. The pair differs ONLY in ``pixel_ratios``, which
    # formats/canonical/image.json makes the density axis: "Absence means [1]", and with
    # ``[1, 2]`` "a 600x500 intrinsic asset at 2x satisfies a logical 300x250 size".
    "plain_300x250": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "params": {"width": 300, "height": 250},
        }
    ],
    "retina_300x250": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "params": {"width": 300, "height": 250, "pixel_ratios": [1, 2]},
        }
    ],
    # A RENDITION SET: the pinned acme_retina_mrec example's declaration
    # (static/examples/products/canonical/acme_retina_mrec.json), its image_main slot
    # requiring the 1x and 2x renditions and accepting an optional 1.5x.
    # formats/canonical/_base.json ``required_pixel_ratios``: "the manifest slot value MUST be
    # an array containing exactly one matching image rendition for each required ratio".
    # ``retina_300x250`` is the same density axis with no requirement, the control that an
    # acceptance set alone does not demand every rendition.
    "retina_rendition_set": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "params": {
                "width": 300,
                "height": 250,
                "pixel_ratios": [1, 1.5, 2],
                "slots": [
                    {
                        "asset_group_id": "image_main",
                        "asset_type": "image",
                        "required": True,
                        "min": 2,
                        "max": 3,
                        "pixel_ratios": [1, 1.5, 2],
                        "required_pixel_ratios": [1, 2],
                    }
                ],
            },
        }
    ],
    # ``fixed_300x250`` asserting a HOUSE legacy id in its ``v1_format_ref`` instead of the
    # registry's: an id no ``format_id_glob`` names, the SDK's upgrade does not rewrite and
    # no creative agent serves. Step 1 of registries/v1-canonical-mapping.json's order -- the
    # seller's own v2 -> v1 link -- is therefore the ONLY step that reaches this declaration
    # from that id.
    "house_ref_300x250": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "v1_format_ref": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": _HOUSE_LEGACY_ID}],
            "params": {"width": 300, "height": 250},
        }
    ],
    # TWO 300x250 images, each asserting a DIFFERENT legacy id in its ``v1_format_ref``, the
    # house one FIRST. ``display_300x250_image`` reaches the second by step 1 of
    # registries/v1-canonical-mapping.json's order (its own v1_format_ref) and reaches BOTH by
    # step 3 (the registry's projection to an image at 300x250, which both narrow). Step 1
    # first selects the second alone; a seller that skipped it, or ran the projection first,
    # selects both, and a package records the first of a selection -- the house option.
    "house_mrec_then_asserted_mrec": [
        {
            "format_kind": "image",
            "format_option_id": "satisfaction_image_mrec_house",
            "v1_format_ref": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": _HOUSE_LEGACY_ID}],
            "params": {"width": 300, "height": 250},
        },
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "v1_format_ref": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250_image"}],
            "params": {"width": 300, "height": 250},
        },
    ],
    # ``fixed_300x250`` with the ``v1_format_ref`` assertion REMOVED, and nothing else
    # changed. That one difference decides whether the accepted package's deprecated
    # ``format_ids_to_provide`` projection has a source at all, which is the pair the two
    # emission scenarios grade.
    "fixed_300x250_no_ref": [
        {
            "format_kind": "image",
            "format_option_id": _PRODUCT_OPTION_ID,
            "params": {"width": 300, "height": 250, "max_file_size_kb": 200, "image_formats": ["jpg", "png"]},
        }
    ],
}

#: The two declarations ``two_image_options`` publishes, named so a placement can narrow
#: to one of them by restating it inline -- ``core/placement.json``'s ``format_options``
#: items are whole declarations, matched to the product's by ``format_option_id``.
_MREC, _LEADERBOARD = _DECLARATIONS["two_image_options"]

#: ``two_image_options`` with the 728x90 option PUBLISHER-SCOPED: the 300x250 is product-local,
#: so it applies to every publisher's slice, and the 728x90 applies only to
#: ``_PUBLISHER_DOMAIN``'s (formats.mdx § Matching and satisfaction).
_DECLARATIONS["local_and_publisher_options"] = [_MREC, {**_LEADERBOARD, "publisher_domain": _PUBLISHER_DOMAIN}]

#: ``two_image_options`` with BOTH options scoped to ``_PUBLISHER_DOMAIN``: no option is
#: product-local, so in a product also sold across another publisher that publisher's slice
#: is empty, and nothing any package selects can cover it.
_DECLARATIONS["publisher_only_options"] = [
    {**option, "publisher_domain": _PUBLISHER_DOMAIN} for option in (_MREC, _LEADERBOARD)
]

#: The publisher no option is scoped to, beside ``_PUBLISHER_DOMAIN`` in a multi-publisher product.
_OTHER_PUBLISHER_DOMAIN = "other.example"

#: What the seller STORES in ``properties`` (served as ``publisher_properties``), per name, for
#: the publisher-slice coverage scenarios. One ``core/publisher-property-selector.json``
#: selector per publisher; each is an eligibility slice when the product has no placements.
_PUBLISHERS: dict[str, list[dict[str, str]]] = {
    "streamhaus_and_other": [
        {"publisher_domain": _PUBLISHER_DOMAIN, "selection_type": "all"},
        {"publisher_domain": _OTHER_PUBLISHER_DOMAIN, "selection_type": "all"},
    ],
    "streamhaus_only": [{"publisher_domain": _PUBLISHER_DOMAIN, "selection_type": "all"}],
}


def _manifest(name: str) -> dict[str, Any]:
    """The ``assets`` of a canonical creative, by the name a scenario gives its manifest.

    The third table, and data like the other two: each entry is the asset map a buyer
    sends, keyed by slot id as creative-manifests.mdx § Basic structure keys it. Every entry
    differs from ``image_300x250`` -- the manifest ``fixed_300x250`` accepts -- in ONE member,
    so a refusal row and its accept row differ in the fact under test and nothing else.
    Built per call because a scenario may mutate what it was handed.
    """
    from tests.factories.creative_asset import (
        asset_spec,
        build_assets,
        image_spec,
        manifest_image_spec,
        text_spec,
        url_spec,
        video_spec,
    )

    mrec = manifest_image_spec("image_main")
    # A 30-second mp4/h264 video of 8 MB at 5,000 kbps and 30 fps, and a 30-second stereo mp3
    # of 900 KB at 44.1 kHz: what the video and audio declarations accept, each other manifest
    # of its kind differing in one member. The frame rate is a string, as video-asset.json
    # types it ("to preserve precision").
    clip = video_spec(
        "video_main",
        duration_ms=30000,
        video_codec="h264",
        container_format="mp4",
        file_size_bytes=8_000_000,
        video_bitrate_kbps=5000,
        frame_rate="30",
    )
    spot = asset_spec(
        "audio_main",
        "audio",
        url="https://example.com/spot.mp3",
        duration_ms=30000,
        codec="mp3",
        file_size_bytes=900_000,
        sampling_rate_hz=44100,
        channels="stereo",
    )

    def headlined(*specs: Any, headlines: tuple[str, ...] = ("Trail Pro boots",)) -> dict[str, Any]:
        """The 300x250 plus *headlines* in ``headline`` (an array when several) and *specs*.

        ``slot_limited_300x250`` requires the headline, so each of its manifests carries one
        and differs from ``image_headlined`` in the one slot under test.
        """
        several = len(headlines) > 1
        return build_assets(mrec, *(text_spec("headline", content=h, multiple=several) for h in headlines), *specs)

    logo = image_spec("logo", width=100, height=100).with_fields(file_size_bytes=40_000)
    mastered = spot.with_fields(loudness_lufs=-16.5, true_peak_dbfs=-2)
    companion = image_spec("companion_image", width=250, height=250).with_fields(file_size_bytes=100_000)
    captioned = clip.with_fields(captions_url="https://example.com/captions.vtt")
    banner = image_spec("companion_banner", width=300, height=250)

    def headline_pool(*headlines: str) -> Any:
        """A responsive creative's ``headlines`` pool: one text asset per headline, as an array."""
        return [text_spec("headlines", content=headline, multiple=True) for headline in headlines]

    # A carousel card's media: a square image of 100,000 bytes, and a square 15-second video of
    # 1,000,000 bytes -- each at every limit carousel_card_media states.
    card_image = {
        "asset_type": "image",
        "url": "https://example.com/card.png",
        "width": 1080,
        "height": 1080,
        "file_size_bytes": 100_000,
    }
    card_video = {
        "asset_type": "video",
        "url": "https://example.com/card.mp4",
        "width": 1080,
        "height": 1080,
        "duration_ms": 15000,
        "file_size_bytes": 1_000_000,
    }

    def card(headline: str = "Trail Pro", media: dict[str, Any] | None = None, **fields: object) -> Any:
        """One card-asset of a carousel's ``cards`` array: *media* (the square image by default),
        *headline*, and the per-card fields in *fields* (a ``cta``)."""
        return asset_spec("cards", "card", multiple=True, media=dict(media or card_image), headline=headline, **fields)

    def carousel(first: Any, count: int = 3) -> dict[str, Any]:
        """*count* cards, the first one *first* and the rest the square image card."""
        return build_assets(first, *(card() for _ in range(count - 1)))

    # A responsive creative's media pools, each filled beside a headline pool of one (``pool``).
    pool_video = video_spec("video", width=1080, height=1080, duration_ms=15000, multiple=True)
    pool_logo = image_spec("logo", width=400, height=100, multiple=True).with_fields(file_size_bytes=20_000)
    pool_image = image_spec("images_landscape", width=1200, height=628, multiple=True).with_fields(
        file_size_bytes=150_000
    )

    def pool(*specs: Any) -> dict[str, Any]:
        """A headline pool of one and *specs*."""
        return build_assets(*headline_pool("Trail Pro boots"), *specs)

    # The kinds' calls to action: the native unit's required title, advertiser name and
    # landing page, beside a ``cta`` text asset.
    native = (
        text_spec("title", content="Trail Pro 3000"),
        text_spec("advertiser_name", content="Acme Outdoor"),
        url_spec("landing_page_url", url="https://example.com/trail-pro"),
    )

    def native_image(role: str, file_size_bytes: int) -> dict[str, Any]:
        """The native unit's required assets beside one image in *role* of *file_size_bytes*."""
        image = image_spec(role, width=1200, height=627).with_fields(file_size_bytes=file_size_bytes)
        return build_assets(*native, image)

    def rendition(width: int, height: int, **fields: object) -> Any:
        """One rendition of the 300x250 in an ``image_main`` array, at *width* x *height* intrinsic."""
        return manifest_image_spec("image_main", width=width, height=height, multiple=True).with_fields(**fields)

    manifests: dict[str, Any] = {
        "image_300x250": lambda: build_assets(mrec),
        "image_728x90": lambda: build_assets(manifest_image_spec("image_main", width=728, height=90)),
        "image_160x600": lambda: build_assets(manifest_image_spec("image_main", width=160, height=600)),
        # 120 KB, between the two caps ``narrowed_twice`` places. 200 KB exactly, the cap
        # ``fixed_300x250`` declares, one byte over it, 200 binary
        # kilobytes (204,800 bytes), and 500 binary kilobytes, over the cap however it is read.
        # canonical-formats.mdx: "1 KB = 1,000 bytes".
        "image_300x250_120kb": lambda: build_assets(mrec.with_fields(file_size_bytes=120_000)),
        "image_300x250_200kb": lambda: build_assets(mrec.with_fields(file_size_bytes=200_000)),
        "image_300x250_200_001b": lambda: build_assets(mrec.with_fields(file_size_bytes=200_001)),
        "image_300x250_200kib": lambda: build_assets(mrec.with_fields(file_size_bytes=200 * 1024)),
        "image_300x250_500kb": lambda: build_assets(mrec.with_fields(file_size_bytes=500 * 1024)),
        # The 300x250 image silent about its file size -- the member a capped declaration needs.
        "image_300x250_unsized": lambda: build_assets(image_spec("image_main")),
        # A Retina rendition of the 300x250: 600x500 intrinsic pixels, the ratio stated or
        # left for the seller to infer (core/assets/image-asset.json ``pixel_ratio``).
        "image_600x500_2x": lambda: build_assets(
            manifest_image_spec("image_main", width=600, height=500).with_fields(pixel_ratio=2)
        ),
        "image_600x500": lambda: build_assets(manifest_image_spec("image_main", width=600, height=500)),
        # The 300x250 at 1.5x: a density between the two ``retina_300x250`` accepts.
        "image_450x375_1_5x": lambda: build_assets(
            manifest_image_spec("image_main", width=450, height=375).with_fields(pixel_ratio=1.5)
        ),
        # RENDITION SETS of the 300x250: image_main as an array, one image per density
        # (formats/canonical/_base.json ``required_pixel_ratios``). Each differs from
        # ``image_renditions_1x_2x`` in one rendition: inferred instead of stated ratios, an
        # added optional 1.5x, the 1x dropped, the 1x sent twice.
        "image_renditions_1x_2x": lambda: build_assets(
            rendition(300, 250, pixel_ratio=1), rendition(600, 500, pixel_ratio=2)
        ),
        "image_renditions_1x_2x_inferred": lambda: build_assets(rendition(300, 250), rendition(600, 500)),
        "image_renditions_1x_1_5x_2x": lambda: build_assets(
            rendition(300, 250, pixel_ratio=1), rendition(450, 375, pixel_ratio=1.5), rendition(600, 500, pixel_ratio=2)
        ),
        "image_renditions_2x": lambda: build_assets(rendition(600, 500, pixel_ratio=2)),
        "image_renditions_1x_1x_2x": lambda: build_assets(
            rendition(300, 250, pixel_ratio=1), rendition(300, 250, pixel_ratio=1), rendition(600, 500, pixel_ratio=2)
        ),
        # One of the image_formats ``fixed_300x250`` declares, and one it does not.
        "image_300x250_png": lambda: build_assets(mrec.with_fields(format="png")),
        "image_300x250_gif": lambda: build_assets(mrec.with_fields(format="gif")),
        # The required slot holding the wrong asset type, and the media keyed by no slot.
        "image_main_as_text": lambda: build_assets(text_spec("image_main", content="Trail Pro")),
        "image_unnamed": lambda: build_assets(image_spec("banner")),
        # SLOT LIMITS, against ``slot_limited_300x250``: each limit at its bound and one past
        # it. A headline of 25 and 26 characters, two and three headlines, a body text of 90
        # and 91 characters, and a logo of 50,000 and 51,000 bytes -- 51 decimal kilobytes and
        # 49.8 binary ones -- or silent about its size, or at a density outside 1x and 2x.
        "image_headlined": lambda: headlined(),
        "image_headline_25_chars": lambda: headlined(headlines=("x" * 25,)),
        "image_headline_26_chars": lambda: headlined(headlines=("x" * 26,)),
        "image_two_headlines": lambda: headlined(headlines=("Trail Pro boots", "Built for the summit")),
        "image_three_headlines": lambda: headlined(headlines=("Trail Pro boots", "Built for the summit", "Shop now")),
        "image_body_text_90_chars": lambda: headlined(text_spec("body_text", content="x" * 90)),
        "image_body_text_91_chars": lambda: headlined(text_spec("body_text", content="x" * 91)),
        "image_logo_50kb": lambda: headlined(logo.with_fields(file_size_bytes=50_000)),
        "image_logo_51kb": lambda: headlined(logo.with_fields(file_size_bytes=51_000)),
        "image_logo_unsized": lambda: headlined(image_spec("logo", width=100, height=100)),
        "image_logo_2x": lambda: headlined(logo.with_fields(pixel_ratio=2)),
        "image_logo_3x": lambda: headlined(logo.with_fields(pixel_ratio=3)),
        # ASPECT RATIO, against ``responsive_1_91``: 1200x628 is 1.91:1 to two places, and
        # 1200x1200 is inside the width range at 1:1.
        "image_1200x628": lambda: build_assets(manifest_image_spec("image_main", width=1200, height=628)),
        "image_1200x1200": lambda: build_assets(manifest_image_spec("image_main", width=1200, height=1200)),
        # VIDEO: the clip at another duration, codec or container, and over the 10 MB cap by
        # 400,000 bytes -- 10.4 decimal megabytes, 9.92 binary ones.
        "video_30s": lambda: build_assets(clip),
        "video_15s": lambda: build_assets(clip.with_fields(duration_ms=15000)),
        "video_90s": lambda: build_assets(clip.with_fields(duration_ms=90000)),
        "video_30s_vp9": lambda: build_assets(clip.with_fields(video_codec="vp9")),
        "video_30s_webm": lambda: build_assets(clip.with_fields(container_format="webm")),
        "video_30s_10mb": lambda: build_assets(clip.with_fields(file_size_bytes=10_000_000)),
        "video_30s_10_4mb": lambda: build_assets(clip.with_fields(file_size_bytes=10_400_000)),
        # The clip at the bitrate ceiling and over it, and at 60 fps.
        "video_30s_8000kbps": lambda: build_assets(clip.with_fields(video_bitrate_kbps=8000)),
        "video_30s_12000kbps": lambda: build_assets(clip.with_fields(video_bitrate_kbps=12000)),
        "video_30s_60fps": lambda: build_assets(clip.with_fields(frame_rate="60")),
        # The clip at 1080x1920, 9:16, beside its 640x360 (16:9) self.
        "video_30s_1080x1920": lambda: build_assets(clip.with_fields(width=1080, height=1920)),
        # FLAT PER-SLOT PARAMS. A responsive headline at 30 and 31 characters, two and three
        # headlines; a carousel whose first card's headline is 25 and 26 characters; the
        # spot at -16.5 LUFS and -2 dBFS with a 250x250 companion of 100,000 bytes, and with
        # each of those one step past its limit or the companion left out; the clip with a
        # caption track and a 300x250 banner, without the track, and with a 728x90 banner.
        "responsive_headline_30_chars": lambda: build_assets(*headline_pool("x" * 30)),
        "responsive_headline_31_chars": lambda: build_assets(*headline_pool("x" * 31)),
        "responsive_two_headlines": lambda: build_assets(*headline_pool("Trail Pro boots", "Built for the summit")),
        "responsive_three_headlines": lambda: build_assets(
            *headline_pool("Trail Pro boots", "Built for the summit", "Shop now")
        ),
        "carousel_card_headline_25_chars": lambda: build_assets(card("x" * 25), card("Second card")),
        "carousel_card_headline_26_chars": lambda: build_assets(card("x" * 26), card("Second card")),
        "audio_mastered_with_companion": lambda: build_assets(mastered, companion),
        "audio_too_loud_with_companion": lambda: build_assets(mastered.with_fields(loudness_lufs=-14.5), companion),
        "audio_peaking_with_companion": lambda: build_assets(mastered.with_fields(true_peak_dbfs=-1), companion),
        "audio_with_wide_companion": lambda: build_assets(mastered, companion.with_fields(width=300, height=250)),
        "audio_with_heavy_companion": lambda: build_assets(mastered, companion.with_fields(file_size_bytes=100_001)),
        "audio_without_companion": lambda: build_assets(mastered),
        "video_captioned_with_banner": lambda: build_assets(captioned, banner),
        "video_uncaptioned_with_banner": lambda: build_assets(clip, banner),
        "video_captioned_with_leaderboard": lambda: build_assets(captioned, banner.with_fields(width=728, height=90)),
        # THE CAROUSEL'S CARDS, against ``carousel_card_media``: three and four cards beside two
        # and five; the first card's media a square image or video at every limit, or one step
        # past one -- 1200x628, 100,001 bytes (over 100 KB, under 100 binary kilobytes), a
        # video of 1,000,001 bytes or 15,001 ms -- and its call to action in the set or not.
        "carousel_three_cards": lambda: carousel(card()),
        "carousel_four_cards": lambda: carousel(card(), count=4),
        "carousel_two_cards": lambda: carousel(card(), count=2),
        "carousel_five_cards": lambda: carousel(card(), count=5),
        "carousel_wide_card": lambda: carousel(card(media={**card_image, "width": 1200, "height": 628})),
        "carousel_heavy_image_card": lambda: carousel(card(media={**card_image, "file_size_bytes": 100_001})),
        "carousel_video_card": lambda: carousel(card(media=card_video)),
        "carousel_heavy_video_card": lambda: carousel(card(media={**card_video, "file_size_bytes": 1_000_001})),
        "carousel_long_video_card": lambda: carousel(card(media={**card_video, "duration_ms": 15001})),
        "carousel_card_cta_shop_now": lambda: carousel(card(cta="SHOP_NOW")),
        "carousel_card_cta_buy_now": lambda: carousel(card(cta="BUY_NOW")),
        # THE RESPONSIVE POOLS, against ``responsive_pool_limits``: a video at 6 and 30 seconds
        # and one millisecond outside each; a 4:1 logo beside a 2:1 one; a landscape image and a
        # logo at 150,000 bytes and one byte over.
        "responsive_video_6s": lambda: pool(pool_video.with_fields(duration_ms=6000)),
        "responsive_video_30s": lambda: pool(pool_video.with_fields(duration_ms=30000)),
        "responsive_video_5999ms": lambda: pool(pool_video.with_fields(duration_ms=5999)),
        "responsive_video_30001ms": lambda: pool(pool_video.with_fields(duration_ms=30001)),
        "responsive_logo_4_1": lambda: pool(pool_logo),
        "responsive_logo_2_1": lambda: pool(pool_logo.with_fields(width=200)),
        "responsive_logo_150kb": lambda: pool(pool_logo.with_fields(file_size_bytes=150_000)),
        "responsive_logo_150_001b": lambda: pool(pool_logo.with_fields(file_size_bytes=150_001)),
        "responsive_image_150kb": lambda: pool(pool_image),
        "responsive_image_150_001b": lambda: pool(pool_image.with_fields(file_size_bytes=150_001)),
        # A CALL TO ACTION on each kind that closes it: one value the declaration lists and one
        # it does not, each beside what the kind requires.
        "image_cta_shop_now": lambda: build_assets(mrec, text_spec("cta", content="SHOP_NOW")),
        "image_cta_explore_more": lambda: build_assets(mrec, text_spec("cta", content="EXPLORE_MORE")),
        "image_cta_lowercase": lambda: build_assets(mrec, text_spec("cta", content="shop_now")),
        "native_cta_sign_up": lambda: build_assets(*native, text_spec("cta", content="SIGN_UP")),
        "native_cta_download": lambda: build_assets(*native, text_spec("cta", content="DOWNLOAD")),
        # THE NATIVE IMAGE CAP, against ``native_image_cap``: the main image and the icon at
        # 100,000 bytes and one byte over, and the main image at 102,400 bytes -- 100 binary
        # kilobytes, over the cap only in the decimal kilobytes the pin states.
        "native_main_image_100kb": lambda: native_image("main_image", 100_000),
        "native_icon_100kb": lambda: native_image("icon", 100_000),
        "native_main_image_100_001b": lambda: native_image("main_image", 100_001),
        "native_icon_100_001b": lambda: native_image("icon", 100_001),
        "native_main_image_102_400b": lambda: native_image("main_image", 102_400),
        "video_cta_watch_more": lambda: build_assets(clip, text_spec("cta", content="WATCH_MORE")),
        "video_cta_learn_more": lambda: build_assets(clip, text_spec("cta", content="LEARN_MORE")),
        # AUDIO: the spot at another duration or codec, and over the 1 MB cap by 40,000 bytes.
        "audio_30s": lambda: build_assets(spot),
        "audio_15s": lambda: build_assets(spot.with_fields(duration_ms=15000)),
        "audio_30s_aac": lambda: build_assets(spot.with_fields(codec="aac")),
        "audio_30s_1_04mb": lambda: build_assets(spot.with_fields(file_size_bytes=1_040_000)),
        # The spot at the other accepted sample rate, at one outside the set, and in mono.
        "audio_30s_48khz": lambda: build_assets(spot.with_fields(sampling_rate_hz=48000)),
        "audio_30s_22khz": lambda: build_assets(spot.with_fields(sampling_rate_hz=22050)),
        "audio_30s_mono": lambda: build_assets(spot.with_fields(channels="mono")),
    }
    assert name in manifests, f"no manifest named {name!r}; have {sorted(manifests)}"
    return manifests[name]()


def _placement(placement_id: str, mode: str, *accepted: dict[str, Any]) -> dict[str, Any]:
    """One stored ``core/placement.json`` document: a seller-inline placement narrowing inline.

    With no *accepted* declaration the placement states no ``format_options`` at all:
    core/product.json, "An omitted optional layer is unconstrained", so it inherits the
    product's set. An EMPTY list would be a present layer admitting nothing.
    """
    placement: dict[str, Any] = StoredPlacementFactory(placement_id=placement_id, mode=mode)
    if accepted:
        placement["format_options"] = list(accepted)
    return placement


#: The publisher-referenced placement the unresolved-layer rows store: ``core/placement.json``
#: ``kind: publisher_ref``, identified by ``{publisher_domain, placement_id}`` in the
#: publisher's adagents.json, which this seller does not resolve on the buy path.
_PUBLISHER_PLACEMENT_ID = "satisfaction_publisher_slot"


def _publisher_placement(mode: str) -> dict[str, Any]:
    """One stored publisher-referenced placement, in *mode*, narrowing nothing inline."""
    placement: dict[str, Any] = StoredPlacementFactory(
        kind="publisher_ref", placement_id=_PUBLISHER_PLACEMENT_ID, publisher_domain=_PUBLISHER_DOMAIN, mode=mode
    )
    return placement


#: The two deprecated references a legacy-only product sells beside each other, sized by
#: their ids: the SDK projects ``display_300x250`` to an image fixed at 300x250 and
#: ``display_728x90`` to one fixed at 728x90 (``registries/v1-canonical-mapping.json``).
_LEGACY_MREC = {"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250"}
_LEGACY_LEADERBOARD = {"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_728x90"}


def _legacy_placement(placement_id: str, *accepted: dict[str, Any]) -> dict[str, Any]:
    """One stored INCLUDED placement narrowing through the deprecated ``format_ids`` alone.

    The shape a legacy-only product's placements have. ``core/placement.json`` at the pin:
    "When present on a product placement, this field narrows the product-level
    ``format_ids`` contract and MUST NOT introduce formats the product does not accept."
    """
    placement: dict[str, Any] = StoredPlacementFactory(
        placement_id=placement_id, mode="included", format_ids=list(accepted)
    )
    return placement


#: Two deprecated references the pinned agent SERVES and the SDK cannot PROJECT. Both are in
#: the committed capture, which declares ``canonical.kind = image`` for each, so a creative
#: claiming one clears ``resolvable_format_ref`` and the sync's write gate; and
#: ``project_legacy_format_id`` projects neither, so a gate that grades a legacy-only product
#: through the projection alone sees an empty set. Measured against the capture with that
#: function: 21 of its 76 ids do not project, these two among them. A reference naming a tenant's own creative
#: agent fails the projection the same way, but no transport here serves such an agent.
_UNPROJECTABLE_MREC = {"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250_image_2x"}
_UNPROJECTABLE_LEADERBOARD = {"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_728x90_image_2x"}


#: What the seller STORES in ``placements``, per name, for the coverage scenarios. Every
#: row but ``narrowed_inline`` is stored beside ``two_image_options``, and the rows differ
#: only in which option each placement accepts and whether it is included.
_PLACEMENTS: dict[str, list[dict[str, Any]]] = {
    # Two INCLUDED placements that each accept a different option: no single option covers
    # both, so a package selecting one leaves the other placement's inventory uncovered.
    "split": [
        _placement("satisfaction_mrec_slot", "included", _MREC),
        _placement("satisfaction_leaderboard_slot", "included", _LEADERBOARD),
    ],
    # ``split`` with the leaderboard placement TARGETABLE instead of included: a package
    # does not buy it unless it selects it, so the 300x250 option covers what is bought.
    "split_leaderboard_targetable": [
        _placement("satisfaction_mrec_slot", "included", _MREC),
        _placement("satisfaction_leaderboard_slot", "targetable", _LEADERBOARD),
    ],
    # Two included placements that BOTH accept the 300x250 option.
    "shared": [
        _placement("satisfaction_mrec_slot", "included", _MREC),
        _placement("satisfaction_leaderboard_slot", "included", _MREC, _LEADERBOARD),
    ],
    # The ID-LESS inline declarations: no ``format_option_id``, so a placement declaration
    # is matched to the product's by kind and params (``core/product.json``'s third
    # matching rule). ``shared_inline``'s second placement restates 300x250 and accepts the
    # mrec option; ``split_inline``'s states 728x90 and does not.
    "shared_inline": [
        _placement("satisfaction_mrec_slot", "included", _MREC),
        _placement(
            "satisfaction_inline_slot", "included", {"format_kind": "image", "params": {"width": 300, "height": 250}}
        ),
    ],
    "split_inline": [
        _placement("satisfaction_mrec_slot", "included", _MREC),
        _placement(
            "satisfaction_inline_slot", "included", {"format_kind": "image", "params": {"width": 728, "height": 90}}
        ),
    ],
    # Two included placements that narrow NOTHING, so each inherits the product's whole set
    # and any selection the product accepts covers both.
    "unnarrowed": [
        _placement("satisfaction_mrec_slot", "included"),
        _placement("satisfaction_leaderboard_slot", "included"),
    ],
    # Stored beside ``fixed_300x250`` (image 300x250, max_file_size_kb 200, jpg/png): one
    # included placement narrowing ONLY the file-size cap, to 150, with no
    # ``format_option_id``. It states no width or height, so it inherits the option's fixed
    # 300x250 (canonical-formats.mdx § "Narrows": "when v2.params omits a parameter that v1
    # specified, v2 inherits v1's value"); a seller grading the placement's silence about
    # the size as a conflict refuses it.
    "narrowed_file_size": [
        _placement(
            "satisfaction_small_mrec_slot", "included", {"format_kind": "image", "params": {"max_file_size_kb": 150}}
        ),
    ],
    # Stored beside ``plain_300x250`` (one image option, 300x250, no cap): one included
    # placement narrowing that ONE option into TWO image entries, capped at 100 KB and at
    # 150 KB, neither naming the option. core/placement.json: its declarations "are
    # intersected with the product-level format_options", so the placement's effective set
    # holds two same-kind entries although the product publishes the kind once.
    "narrowed_twice": [
        _placement(
            "satisfaction_capped_mrec_slot",
            "included",
            {"format_kind": "image", "params": {"max_file_size_kb": 100}},
            {"format_kind": "image", "params": {"max_file_size_kb": 150}},
        ),
    ],
    # Stored beside ``duration_range``: an included placement BROADER than the option, its
    # range [1000, 120000] containing the product's [5000, 60000]. The option is the narrower
    # side here, so the match is the reverse direction.
    "widened_inline": [
        _placement(
            "satisfaction_long_video_slot",
            "included",
            {"format_kind": "video_hosted", "params": {"duration_ms_range": [1000, 120000]}},
        ),
    ],
    # Stored beside ``exact_duration`` (``duration_ms_exact`` 30000): an included placement
    # stating only a range that EXCLUDES the option's exact duration. Neither side satisfies
    # the other, so the placement admits nothing the product sells.
    "short_range_inline": [
        _placement(
            "satisfaction_short_video_slot",
            "included",
            {"format_kind": "video_hosted", "params": {"duration_ms_range": [5000, 20000]}},
        ),
    ],
    # Stored beside ``duration_range`` (video_hosted, ``duration_ms_range`` [5000, 60000]):
    # one included placement NARROWING it to [5000, 30000] with no ``format_option_id``. The
    # placement is the narrower side, so it matches; graded the other way round it does not.
    "narrowed_inline": [
        _placement(
            "satisfaction_short_video_slot",
            "included",
            {"format_kind": "video_hosted", "params": {"duration_ms_range": [5000, 30000]}},
        ),
    ],
    # Stored beside ``two_image_options``: the 300x250 seller-inline placement of ``split``
    # plus a PUBLISHER-REFERENCED one. Included, its effective set is the intersection with
    # the publisher's adagents.json catalog, which this seller cannot resolve, so it fails
    # closed (core/product.json: "an unresolved publisher placement or format-option
    # reference MUST fail closed"). Targetable, it is not bought, so it does not constrain.
    "publisher_ref_included": [
        _placement("satisfaction_mrec_slot", "included", _MREC),
        _publisher_placement("included"),
    ],
    "publisher_ref_targetable": [
        _placement("satisfaction_mrec_slot", "included", _MREC),
        _publisher_placement("targetable"),
    ],
    # Stored beside the legacy-only product of ``_LEGACY_MREC`` and ``_LEGACY_LEADERBOARD``:
    # ``split`` spelled in the deprecated arm, each included placement narrowing to one of
    # the two references, so no single size covers both.
    "legacy_split": [
        _legacy_placement("satisfaction_mrec_slot", _LEGACY_MREC),
        _legacy_placement("satisfaction_leaderboard_slot", _LEGACY_LEADERBOARD),
    ],
    # Stored beside the legacy-only product of the two UNPROJECTABLE references: one included
    # placement narrowing to the 300x250 one, and ``legacy_split`` spelled in those references.
    "unprojectable_mrec": [_legacy_placement("satisfaction_mrec_slot", _UNPROJECTABLE_MREC)],
    "unprojectable_split": [
        _legacy_placement("satisfaction_mrec_slot", _UNPROJECTABLE_MREC),
        _legacy_placement("satisfaction_leaderboard_slot", _UNPROJECTABLE_LEADERBOARD),
    ],
}

#: The placements name for a product that stores none, the control for every coverage row:
#: with no included placement there is no layer to cover.
_NO_PLACEMENTS = "none"


def _stored_placements(name: str) -> list[dict[str, Any]] | None:
    """The :data:`_PLACEMENTS` row *name* names, or None for :data:`_NO_PLACEMENTS`."""
    return None if name == _NO_PLACEMENTS else _PLACEMENTS[name]


#: What the buyer SENDS on its package, per selector name. The members are
#: ``media-buy/package-request.json``'s three format selectors.
_SELECTORS: dict[str, dict[str, Any]] = {
    # --- selectors that satisfy ---
    # NO selector at all: "omitting both selects all formats supported by the product"
    # (media-buy/package-request.json).
    "no_selector": {},
    # States the fixed size and nothing else, so every other constraint is inherited.
    "exact_300x250": {"format_kind": "image", "params": {"width": 300, "height": 250}},
    # The pin's create_media_buy_with_direct_canonical_selector: every param the product
    # constrains, restated.
    "complete_300x250": {
        "format_kind": "image",
        "params": {"width": 300, "height": 250, "max_file_size_kb": 200, "image_formats": ["jpg", "png"]},
    },
    "product_option_ref": {"format_option_refs": [{"scope": "product", "format_option_id": _PRODUCT_OPTION_ID}]},
    # ``two_image_options``'s OTHER option, the 728x90, alone.
    "leaderboard_option_ref": {
        "format_option_refs": [{"scope": "product", "format_option_id": _LEADERBOARD["format_option_id"]}]
    },
    # BOTH of ``two_image_options``'s product-local options on one arm: a SET, which is how a
    # buyer covers two included placements that accept one option each.
    "both_option_refs": {
        "format_option_refs": [
            {"scope": "product", "format_option_id": _PRODUCT_OPTION_ID},
            {"scope": "product", "format_option_id": _LEADERBOARD["format_option_id"]},
        ]
    },
    # The legacy arm ALONE, which is what the storyboard's step 10
    # (create_media_buy_with_legacy_mrec_format) sends: format_ids and no format_kind, no
    # params. This row used to carry ``"format_kind": "image"`` as well, which the old
    # merged-claim design needed and which is NOT a shape any storyboard step sends -- and
    # once routes became independent the row correctly started failing, because a bare
    # ``image`` kind beside it is its own under-specified route (step 16). The fixture was
    # the defect, not the refusal: `bare_kind_beside_option_ref` below now grades that
    # two-route shape deliberately, and this one grades step 10 as written.
    "legacy_display_300x250": {"format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250"}]},
    # A legacy id that reaches the 300x250 declaration ONLY through normalization. Nothing
    # this module's products carry spells it -- their deprecated arm and their
    # ``v1_format_ref`` both say ``display_300x250`` -- while the SDK's registry upgrade
    # takes both ids to display_image 300x250. So it is the one selector on which a seller
    # matching raw (agent_url, id) pairs and one matching the canonical shape disagree, which
    # is the comparison core/product-format-declaration.json forbids ("do not exact-match raw
    # (agent_url, id) pairs once a format_id has been projected"). It is also the id the
    # storyboard's create_media_buy_with_legacy_mrec_format step sends.
    "legacy_display_300x250_image": {
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250_image"}]
    },
    # A legacy id NO registry glob names and the SDK's upgrade does not rewrite, which the
    # reference agent serves declaring ``canonical.kind = audio_hosted``. So it resolves only
    # through step 2 of the registry's order -- the served declaration -- and a seller that
    # skips that step on the package route refuses it LEGACY_UNPROJECTABLE while
    # ``sync_creatives`` accepts the same id.
    "legacy_audio_standard_30s": {
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "audio_standard_30s"}]
    },
    # The house id ``house_ref_300x250`` asserts. Only the declaration's own v1_format_ref
    # reaches it, so a seller that skips step 1 of the registry's order finds no projection
    # and no kind, and refuses it LEGACY_UNPROJECTABLE.
    "legacy_house_ref_only": {"format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": _HOUSE_LEGACY_ID}]},
    # The storyboard's create_media_buy_with_equivalent_dual_emission: the product-local
    # option ref and ``display_300x250_image`` "normalize to the same declaration", so the
    # two routes agree and the buy is accepted.
    "option_plus_equivalent_legacy_image": {
        "format_option_refs": [{"scope": "product", "format_option_id": _PRODUCT_OPTION_ID}],
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250_image"}],
    },
    # The SAME deprecated id the seller publishes, sized EXPLICITLY by the buyer and
    # differently from the seller's catalogue entry. ``core/format-id.json`` carries
    # width/height on the reference for exactly this parameterized case, and the two halves
    # of the dimension rule give different answers here -- which is what makes the pair of
    # scenarios below able to fail.
    "legacy_sized_728x90": {
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250", "width": 728, "height": 90}]
    },
    "contained_range": {"format_kind": "video_hosted", "params": {"duration_ms_range": [10000, 30000]}},
    # An EXACT duration inside the declared range: "An exact value, such as
    # duration_ms_exact, satisfies a range when that exact value falls inside the accepted
    # interval" (canonical-formats.mdx).
    "exact_duration_in_range": {"format_kind": "video_hosted", "params": {"duration_ms_exact": 30000}},
    "covering_slots": {
        "format_kind": "video_hosted",
        "params": {"slots": [{"asset_group_id": "video_main", "asset_type": "video"}]},
    },
    "matching_publisher_claim": {
        "format_option_refs": [
            {"scope": "publisher", "publisher_domain": _PUBLISHER_DOMAIN, "format_option_id": _PRODUCT_OPTION_ID}
        ]
    },
    # ``local_and_publisher_options``' publisher-scoped 728x90 alone, and beside the
    # product-local 300x250: a SET, which is how a buyer covers every publisher's slice.
    "publisher_leaderboard_ref": {
        "format_option_refs": [
            {
                "scope": "publisher",
                "publisher_domain": _PUBLISHER_DOMAIN,
                "format_option_id": _LEADERBOARD["format_option_id"],
            }
        ]
    },
    "local_and_publisher_refs": {
        "format_option_refs": [
            {"scope": "product", "format_option_id": _PRODUCT_OPTION_ID},
            {
                "scope": "publisher",
                "publisher_domain": _PUBLISHER_DOMAIN,
                "format_option_id": _LEADERBOARD["format_option_id"],
            },
        ]
    },
    # Step 11: two co-present routes that BOTH resolve, to the same declaration. The
    # declaration asserts this exact legacy id in its v1_format_ref, so the legacy route is
    # a lookup rather than a guess.
    "option_plus_projectable_legacy": {
        "format_option_refs": [{"scope": "product", "format_option_id": _PRODUCT_OPTION_ID}],
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250"}],
    },
    # Step 12: a complete DIRECT canonical selector beside the legacy one, same declaration.
    "direct_plus_legacy": {
        "format_kind": "image",
        "params": {"width": 300, "height": 250},
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250"}],
    },
    # --- selectors that do not ---
    # The SECOND of ``two_mrec_options``' declarations by option ref, beside a legacy id that
    # both declarations narrow: the option ref selects one entry, the legacy id both.
    "second_mrec_option_plus_legacy": {
        "format_option_refs": [{"scope": "product", "format_option_id": "satisfaction_image_mrec_gif"}],
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250"}],
    },
    # ``fixed_15s_video``'s option by ref, beside a legacy video id parameterized at 30
    # seconds. ``video_640x480`` projects to ``video_hosted`` on the pinned registry and the
    # upgrade carries ``duration_ms`` through, so the legacy route states a duration the
    # option contradicts.
    "option_plus_conflicting_duration_legacy": {
        "format_option_refs": [{"scope": "product", "format_option_id": "satisfaction_video_15s"}],
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "video_640x480", "duration_ms": 30000}],
    },
    # The storyboard's reject_bare_image_selector_for_fixed_mrec: the right kind, no params.
    "bare_image_kind": {"format_kind": "image"},
    # formats.mdx's fifth rule as an input: the same kind, different dimensions.
    "mismatched_728x90": {"format_kind": "image", "params": {"width": 728, "height": 90}},
    # A kind in the pinned vocabulary that THIS product does not publish -- the closed set.
    "unpublished_video_kind": {"format_kind": "video_hosted", "params": {"width": 300, "height": 250}},
    "unpublished_option_ref": {"format_option_refs": [{"scope": "product", "format_option_id": "no_such_option"}]},
    "overlapping_range": {"format_kind": "video_hosted", "params": {"duration_ms_range": [1000, 30000]}},
    # The UPPER-bound mirror of ``overlapping_range``: the low end is inside the accepted
    # window and the high end is past it, so a seller testing one endpoint passes one row.
    "range_over_upper_bound": {"format_kind": "video_hosted", "params": {"duration_ms_range": [10000, 90000]}},
    # An exact duration that is not the declared exact duration.
    "exact_duration_mismatched": {"format_kind": "video_hosted", "params": {"duration_ms_exact": 15000}},
    # The exact-duration row's refusal half: the same key, a value outside the range.
    "exact_duration_outside_range": {"format_kind": "video_hosted", "params": {"duration_ms_exact": 90000}},
    # A duration range is one of the four constraints a selector MUST state, so silence
    # about it is under-specification, not inheritance.
    "duration_omitted": {"format_kind": "video_hosted"},
    # Inherited constraints still bind a claim that STATES them: a cap above the
    # product's and a file format outside its set each fail.
    "oversized_file_300x250": {
        "format_kind": "image",
        "params": {"width": 300, "height": 250, "max_file_size_kb": 500},
    },
    "unaccepted_image_format_300x250": {
        "format_kind": "image",
        "params": {"width": 300, "height": 250, "image_formats": ["gif"]},
    },
    "headline_over_limit_300x250": {
        "format_kind": "image",
        "params": {"width": 300, "height": 250, "headline_max_chars": 90},
    },
    "slot_missing": {
        "format_kind": "video_hosted",
        "params": {"slots": [{"asset_group_id": "caption", "asset_type": "text"}]},
    },
    # The required group, offered as the WRONG asset type. ``slot_missing`` differs in both
    # halves, so only this row grades "with compatible asset types" on its own.
    "slot_wrong_asset_type": {
        "format_kind": "video_hosted",
        "params": {"slots": [{"asset_group_id": "video_main", "asset_type": "image"}]},
    },
    # The same option id under a DIFFERENT publisher domain: the second half of the
    # exact-publisher-claim rule.
    "wrong_publisher_domain": {
        "format_option_refs": [
            {"scope": "publisher", "publisher_domain": "other.example", "format_option_id": _PRODUCT_OPTION_ID}
        ]
    },
    # The other half: the RIGHT publisher domain with an option id it does not publish.
    "wrong_publisher_option_id": {
        "format_option_refs": [
            {"scope": "publisher", "publisher_domain": _PUBLISHER_DOMAIN, "format_option_id": "no_such_option"}
        ]
    },
    # Step 15, the ORDERING obligation: the option ref RESOLVES and the legacy id does not.
    # The refusal must be UNSUPPORTED_FEATURE naming format_ids -- not an acceptance on the
    # option route, and not CONFLICTING_SELECTORS.
    "option_plus_unprojectable_legacy": {
        "format_option_refs": [{"scope": "product", "format_option_id": _PRODUCT_OPTION_ID}],
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "unmapped_legacy_selector_6648"}],
    },
    # The same ordering obligation on the OTHER canonical arm: the option ref fails to
    # resolve and a complete direct selector beside it resolves. format_option_refs'
    # description: sellers "reject unresolved options with UNSUPPORTED_FEATURE and a field
    # path to the failing entry before comparing co-present routes".
    "unpublished_option_plus_exact_300x250": {
        "format_option_refs": [{"scope": "product", "format_option_id": "no_such_option"}],
        "format_kind": "image",
        "params": {"width": 300, "height": 250},
    },
    # Step 17: fixed-size image dimensions are ATOMIC, so width without height is
    # SCHEMA-invalid and belongs in the other error bucket -- INVALID_REQUEST, not
    # UNSUPPORTED_FEATURE. Measured: the pinned PackageRequest refuses it itself
    # ("violates a constraint its schema states outside its properties,
    # media-buy/package-request.json, root conjunct 3, over format_kind, height, params"),
    # so the refusal comes from the request boundary and never reaches the satisfaction
    # function at all. That is the right owner and the scenario below pins it, because the
    # alternative -- satisfaction answering UNSUPPORTED_FEATURE for a schema violation --
    # is exactly the bucket confusion the step exists to prevent.
    "width_without_height": {"format_kind": "image", "params": {"width": 300}},
    # A SINGLE legacy route that normalizes and then selects nothing. Measured on the pinned
    # wheel: ``display_728x90`` upgrades to ``display_image`` + 728x90 and
    # ``canonical_kind_for`` answers ``image``, so against a product fixing 300x250 the
    # route resolves and satisfies no declaration -- the pin's "a single resolved route that
    # does not satisfy the product". The SAME id beside a resolving option ref is the
    # conflict row below, and the two differ in nothing else.
    "legacy_display_728x90": {"format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_728x90"}]},
    # BOTH sizes of the legacy-only product on one arm: a set, which is how a buyer covers
    # ``legacy_split``'s two included placements through the deprecated selector.
    "legacy_both_sizes": {"format_ids": [_LEGACY_MREC, _LEGACY_LEADERBOARD]},
    # Two legacy references that state no size. ``display_image`` is the unparameterized
    # template the registry maps to ``image`` by glob, so against a product fixing 300x250 it
    # is "a broad request with no dimensions", which core/product-format-declaration.json
    # says "does not satisfy a fixed-size ... product". ``display_300x250`` at another agent
    # that serves no declaration for it projects to nothing at all: the registry maps a
    # legacy id (glob and size upgrade alike) only at the reference agent's origin. Both
    # were accepted by satisfaction and refused only by a raw-key gate that ran after it --
    # VALIDATION_ERROR, and for the wrong reason (salesagent-ol033.20.26).
    "legacy_unsized_template": {"format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_image"}]},
    "foreign_agent_display_300x250": {
        "format_ids": [{"agent_url": "https://another-creative-agent.example", "id": "display_300x250"}]
    },
    # The creative agent's host with TWO trailing dots. docs/reference/url-canonicalization,
    # step 2: "More than one trailing dot creates an empty DNS label rather than a root-label
    # spelling and MUST be rejected", and comparers MUST reject a malformed authority. The
    # wire accepts it (``format: uri``), so the refusal is the seller's: it names no agent and
    # so no format. It used to raise out of the comparison and answer INTERNAL_ERROR.
    "malformed_agent_display_300x250": {
        "format_ids": [{"agent_url": f"https://{_AGENT_HOST}..", "id": "display_300x250"}]
    },
    # M2's refusal half: a selector satisfying NEITHER image declaration ``two_image_options``
    # publishes, so the two M2 accept rows cannot be passed by accepting every image.
    "mismatched_160x600": {"format_kind": "image", "params": {"width": 160, "height": 600}},
    # --- co-present routes that select DIFFERENT declarations ---
    # The storyboard's reject_conflicting_dual_emission: the option ref selects the 300x250
    # declaration, and the registry-backed legacy id normalizes to a 728x90 image the
    # product does not publish. Both routes resolve, so the refusal is the EQUIVALENCE one.
    "option_plus_conflicting_legacy": {
        "format_option_refs": [{"scope": "product", "format_option_id": _PRODUCT_OPTION_ID}],
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_728x90"}],
    },
    # The storyboard's reject_conflicting_canonical_routes: two CANONICAL routes, and the
    # direct one describes a 728x90 image. "Canonical precedence must not hide a conflict
    # between two resolvable canonical routes."
    "option_plus_conflicting_direct": {
        "format_option_refs": [{"scope": "product", "format_option_id": _PRODUCT_OPTION_ID}],
        "format_kind": "image",
        "params": {"width": 728, "height": 90},
    },
    # The same shape with the params OMITTED rather than wrong: the kind route selects no
    # declaration (silence does not satisfy an exact dimension) while the option ref selects
    # one, so the selected option sets differ. Graded UNSUPPORTED_FEATURE until the
    # equivalence comparison existed; see salesagent-iql3e for why the pin says otherwise.
    "bare_kind_beside_option_ref": {
        "format_option_refs": [{"scope": "product", "format_option_id": _PRODUCT_OPTION_ID}],
        "format_kind": "image",
    },
    # The broad legacy template beside the option ref. display_image states no size, so --
    # like a bare ``format_kind: image`` -- it selects no fixed-size declaration (package-
    # request.json format_kind: broad image "does not satisfy a fixed-size product
    # declaration"), while the option ref selects the 300x250: the sets differ.
    "option_plus_broad_legacy": {
        "format_option_refs": [{"scope": "product", "format_option_id": _PRODUCT_OPTION_ID}],
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_image"}],
    },
    # ``both_option_refs`` beside display_300x250: the option arm selects BOTH of
    # ``two_image_options``' entries, the legacy route only the 300x250 one its projection
    # narrows to. One option SET against another, so the two refs are not a conflict between
    # themselves and the arm as a whole is.
    "both_option_refs_plus_legacy": {
        "format_option_refs": [
            {"scope": "product", "format_option_id": _PRODUCT_OPTION_ID},
            {"scope": "product", "format_option_id": _LEADERBOARD["format_option_id"]},
        ],
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250"}],
    },
    # --- co-present routes on a product publishing ONLY the deprecated arm ---
    # A direct route of another size beside the legacy id the product carries: against the
    # SDK's projection of that id (image 300x250) the direct route selects nothing.
    "kind_728x90_plus_legacy": {
        "format_kind": "image",
        "params": {"width": 728, "height": 90},
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250"}],
    },
    # A direct route of another SHAPE: video beside an image legacy id.
    "video_kind_plus_legacy": {
        "format_kind": "video_hosted",
        "params": {"duration_ms_exact": 15000},
        "format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250"}],
    },
}


# ── Given steps ─────────────────────────────────────────────────────


@given("a tenant is configured for media buy creation")
def given_tenant_for_create(ctx: dict) -> None:
    """Assert the route already seeded a tenant and buyer, rather than seeding a second one.

    Both routes this feature uses seed before any Given runs -- the media-buy one seeds the
    whole create chain, the sync one a tenant and principal -- so the Background's job is to
    say the precondition exists and check it, not to establish it. A Background that seeded
    would create a SECOND tenant and leave the request pointing at whichever ran last, and
    ``setup_default_data`` re-creating a missing principal is how a later Given comes to
    undo an earlier one.

    The tenant is what BOTH routes have in common and so is what this asserts; the product
    is deliberately not checked, because the sync scenarios declare their own.
    """
    assert ctx.get("tenant") is not None, (
        "a format-satisfaction route must seed a tenant and principal before the Background runs; "
        "check the ENV_ROUTES rows claiming @format_satisfaction / @format_satisfaction_sync"
    )


def _store_declaring_product(
    ctx: dict,
    declaration: str,
    *,
    with_legacy_arm: bool,
    placements: str | None = None,
    publishers: str | None = None,
) -> None:
    """Store one product carrying *declaration*, with or without the deprecated arm.

    *placements* names a :data:`_PLACEMENTS` row stored in the product's ``placements``
    column; ``None`` stores none, which is every scenario but the coverage ones.
    *publishers* names a :data:`_PUBLISHERS` row stored as the product's ``properties``;
    ``None`` keeps the tenant's own ``all_inventory`` tag, a single publisher.

    ``with_legacy_arm=False`` stores a CANONICAL-ONLY product: ``format_ids`` NULL, which
    migration 08cd415b3d91 made representable. The product id carries the shape so two
    scenarios naming the same declaration cannot collide on one row.
    """
    product_id = f"satisfaction_{declaration}" if with_legacy_arm else f"satisfaction_canonical_{declaration}"
    _store_product(
        ctx,
        "_".join(part for part in (product_id, placements, publishers) if part),
        format_ids=list(_LEGACY_ARM) if with_legacy_arm else None,
        format_options=_DECLARATIONS[declaration],
        placements=_PLACEMENTS[placements] if placements else None,
        properties=_PUBLISHERS[publishers] if publishers else None,
    )


def _store_product(
    ctx: dict,
    product_id: str,
    *,
    format_ids: list[dict[str, Any]] | None,
    format_options: list[dict[str, Any]] | None,
    placements: list[dict[str, Any]] | None = None,
    properties: list[dict[str, Any]] | None = None,
    inventory_profile_id: int | None = None,
) -> None:
    """Store one product with the given arms and point the buyer's package at it.

    The product becomes ``ctx["default_product"]``, which is what the shared create-request
    builder points the package at -- so the buyer's request names THIS product without this
    module duplicating the request literal. *properties* replaces the ``all_inventory`` tag:
    ``ck_product_properties_xor`` stores exactly one of the two.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    product = ProductFactory(
        tenant=ctx["tenant"],
        product_id=product_id,
        delivery_type="non_guaranteed",
        format_ids=format_ids,
        format_options=format_options,
        placements=placements,
        properties=properties,
        property_tags=None if properties else ["all_inventory"],
        inventory_profile_id=inventory_profile_id,
    )
    ctx["default_pricing_option"] = PricingOptionFactory(
        product=product, pricing_model="cpm", currency="USD", is_fixed=True
    )
    ctx["default_product"] = product
    # Built AFTER the product exists so the package names it; the builder reads
    # ctx["default_product"] and is guarded against re-running.
    ctx.pop("request_kwargs", None)
    _ensure_request_defaults(ctx)


@given(parsers.parse('the seller declares a canonical format option "{declaration}"'))
def given_declaration(ctx: dict, declaration: str) -> None:
    """Store a DUAL-ARM product -- the named canonical declarations plus the legacy arm.

    Dual-arm is what the satisfaction scenarios want: the rule each one names is about the
    SELECTOR, and a product carrying both arms leaves every selector route reachable, so a
    refusal is attributable to the rule rather than to an arm the product never declared.
    """
    _store_declaring_product(ctx, declaration, with_legacy_arm=True)


@given(parsers.parse('the seller declares canonical format options "{declaration}" across placements "{placements}"'))
def given_declaration_across_placements(ctx: dict, declaration: str, placements: str) -> None:
    """Store the dual-arm product of :func:`given_declaration` with stored placements.

    The placements are what the coverage rule reads: each is a stored ``core/placement.json``
    document, and an included one may narrow the product's declarations inline.
    """
    _store_declaring_product(ctx, declaration, with_legacy_arm=True, placements=placements)


@given(parsers.parse('the seller declares canonical format options "{declaration}" across publishers "{publishers}"'))
def given_declaration_across_publishers(ctx: dict, declaration: str, publishers: str) -> None:
    """Store the dual-arm product of :func:`given_declaration` sold across the named publishers.

    No placements: the product's ``publisher_properties`` selectors are then what the
    coverage rule slices it by (formats.mdx § Matching and satisfaction).
    """
    _store_declaring_product(ctx, declaration, with_legacy_arm=True, publishers=publishers)


@given(parsers.parse("the seller publishes only a deprecated format sized {width:d}x{height:d}"))
def given_legacy_only_sized_product(ctx: dict, width: int, height: int) -> None:
    """Store a LEGACY-ONLY product whose deprecated entry CARRIES its dimensions.

    The fixture the dimension-precedence rule needs, and it is deliberately neither of this
    module's other two shapes. Dual-arm will not do: a legacy selector normalizes through
    ``upgrade_legacy_format_id``, so a buyer sizing ``display_300x250`` at 728x90 against a
    product whose declaration FIXES 300x250 is refused by satisfaction before any dimension
    is resolved -- correctly, and it would grade the refusal rather than the precedence.
    With no ``format_options`` the product narrows no canonical set, so the selector is
    accepted and the create path's dimension resolution is what decides what the ad server
    is asked to traffic.

    Legacy-only is spec-legal for the 3.x window -- ``core/product.json``: "Products MUST
    carry ``format_ids``, ``format_options``, or both during the 3.x migration window" --
    and is the population this rule still serves.
    """
    _store_legacy_only_sized_product(ctx, width, height, agent_url=DEFAULT_CREATIVE_AGENT_URL)


@given(
    parsers.parse(
        "the seller publishes only a deprecated format sized {width:d}x{height:d} "
        "at the creative agent's url with {variant}"
    )
)
def given_legacy_only_sized_product_spelled(ctx: dict, width: int, height: int, variant: str) -> None:
    """The same legacy-only product, its entry naming the creative agent in another spelling.

    The create-path half of ``core/format-id.json``'s MUST: "Callers comparing two
    format-id values MUST canonicalize agent_url per the AdCP URL canonicalization rules
    before treating two formats as the same." Only the PRODUCT's spelling moves; the buyer's
    selector names the agent as the factory does, so the two references disagree in bytes
    and in nothing the algorithm keeps.
    """
    _store_legacy_only_sized_product(ctx, width, height, agent_url=_agent_url_spelled(variant))


def _store_legacy_only_sized_product(ctx: dict, width: int, height: int, *, agent_url: str) -> None:
    """Store a legacy-only product whose one deprecated entry is sized, at *agent_url*."""
    entry = dict(_LEGACY_ARM_SIZED[0]) | {"width": width, "height": height, "agent_url": agent_url}
    _store_product(ctx, f"satisfaction_legacy_{width}x{height}", format_ids=[entry], format_options=None)


@given("the seller publishes only the deprecated format_ids")
def given_legacy_only_product(ctx: dict) -> None:
    """Store a LEGACY-ONLY product: this module's deprecated arm, and no format_options.

    Still legal at the pin -- ``core/product.json``: "Products MUST carry ``format_ids``,
    ``format_options``, or both during the 3.x migration window" -- and the shape on which
    a canonical route has no ``format_options[]`` entry to resolve against.
    """
    _store_product(ctx, "satisfaction_legacy_only", format_ids=list(_LEGACY_ARM), format_options=None)


@given(
    parsers.parse(
        'the seller publishes only the deprecated 300x250 and 728x90 formats across placements "{placements}"'
    )
)
def given_legacy_only_product_across_placements(ctx: dict, placements: str) -> None:
    """Store a LEGACY-ONLY product selling two sizes, with the named stored placements.

    The product publishes no ``format_options``, so the coverage rules read both its arm and
    its placements' deprecated ``format_ids`` through the SDK's projection. The rows this
    stores beside it narrow through that arm alone, the shape a legacy-only product's
    placements have.
    """
    _store_product(
        ctx,
        f"satisfaction_legacy_two_sizes_{placements}",
        format_ids=[_LEGACY_MREC, _LEGACY_LEADERBOARD],
        format_options=None,
        placements=_PLACEMENTS[placements],
    )


@given(
    parsers.parse(
        'the seller publishes only deprecated formats the SDK cannot project across placements "{placements}"'
    )
)
def given_unprojectable_product_across_placements(ctx: dict, placements: str) -> None:
    """Store a LEGACY-ONLY product selling the two unprojectable references, with placements.

    The product accepts what its deprecated arm accepts whatever the SDK can project, so an
    included placement narrowing nothing must admit the same creatives (``core/product.json``:
    "An omitted optional layer is unconstrained"). ``none`` stores no placements.
    """
    _store_product(
        ctx,
        f"satisfaction_legacy_unprojectable_{placements}",
        format_ids=[_UNPROJECTABLE_MREC, _UNPROJECTABLE_LEADERBOARD],
        format_options=None,
        placements=_stored_placements(placements),
    )


@given(parsers.parse('the seller declares a canonical format option "{declaration}" and no format_ids'))
def given_canonical_only_declaration(ctx: dict, declaration: str) -> None:
    """Store a CANONICAL-ONLY product -- the declaration, and no deprecated arm at all.

    The shape AdCP 3.2 tells new products to author, and the one stage-1 guarantee nothing
    graded: that such a product is PURCHASABLE and not merely discoverable. Every other
    product fixture on the create path carries the deprecated arm, so
    discoverable-but-unpurchasable had no detector.
    """
    _store_declaring_product(ctx, declaration, with_legacy_arm=False)


@given(
    parsers.parse(
        'the seller links a product declaring "{declaration}" to an inventory profile declaring "{profile_declaration}"'
    )
)
def given_profile_linked_declaration(ctx: dict, declaration: str, profile_declaration: str) -> None:
    """Store a product linked to an inventory profile whose canonical declarations differ.

    The profile supplies a linked product's contract (``linked_format_contract``), so the
    product's OWN arms -- the named declaration and this module's legacy arm, both naming
    ``display_300x250`` -- are stored and never served. Every value a reader of the product's
    own columns could produce therefore differs from the profile's.

    ``inventory_profile_id`` is the profile's INTEGER primary key, which
    ``products.inventory_profile_id`` references.
    """
    from tests.factories.inventory_profile import InventoryProfileFactory

    profile = InventoryProfileFactory(
        tenant=ctx["tenant"], format_ids=None, format_options=_DECLARATIONS[profile_declaration]
    )
    _store_product(
        ctx,
        f"satisfaction_profile_linked_{declaration}",
        format_ids=list(_LEGACY_ARM),
        format_options=_DECLARATIONS[declaration],
        inventory_profile_id=profile.id,
    )


@given(
    parsers.parse(
        'the buyer assigns a creative identified by canonical kind "{kind}" with its media asset keyed "{asset_key}"'
    )
)
def given_canonical_creative(ctx: dict, kind: str, asset_key: str) -> None:
    """Seed a creative carrying a CANONICAL identity and assign it to the package.

    ``canonical_kind`` rather than ``format``, which is the whole point: a creative seeded
    this way asserts NO legacy reference, so the four gates' deprecated comparison has
    nothing to compare and the canonical closed-set step is the only thing that can refuse
    it. The factory derives the stored columns through the same production mapping the sync
    path uses, so this row and a synced row cannot differ in shape.

    THE ASSET KEY IS A SCENARIO PARAMETER and not a fixture decision, because it is the one
    axis two of the scenarios below vary. A canonical creative has no format spec to ask for
    an ``asset_id`` -- it asserts no legacy reference -- so the key under which its media
    asset is stored is the whole of what the create path's pre-adapter gate has to go on,
    and whether the gate can read it is the obligation. ``image_main`` is what
    ``formats/canonical/image.json`` declares (its ``slots`` default), and a key the
    declaration does not name must still be refused; the feature states both.

    This key used to be hardcoded ``banner_image``, a member of this seller's own
    ``MEDIA_ASSET_FALLBACK_IDS`` guess-list, because nothing read the declaration and the
    allowlist was the only path a canonical creative had -- so the scenarios graded the
    closed set and never the asset reader. That was salesagent-ol033.16; the workaround goes
    with it.
    """
    creative = _seed_canonical_creative(ctx, kind=kind, asset_key=asset_key)
    ctx["request_kwargs"]["packages"][0]["creative_ids"] = [creative.creative_id]


def _seed_canonical_creative(
    ctx: dict,
    *,
    kind: str,
    asset_key: str = "image_main",
    assets: dict[str, Any] | None = None,
    status: str = "approved",
    **row: Any,
) -> Any:
    """One creative carrying the named CANONICAL kind, committed. Returns the row.

    Approved unless the caller names another review ``status``; ``row`` carries any other
    column a caller pins (a ``creative_id`` a later sync must address). ``assets`` replaces
    the one 300x250 image keyed ``asset_key`` with a whole named manifest (:func:`_manifest`).

    The default asset key is ``image_main``, which ``formats/canonical/image.json`` declares
    as the ``image`` kind's required image slot -- so a caller that does not care about the
    key gets the one the pin names rather than a convention this seller invented. The image
    states its file size (``manifest_image_spec``), because a declaration capping it is owed
    the member.

    CANONICAL ONLY. This grew a ``format_id`` arm for a legacy caller and then lost that
    caller, and the dead parameter is removed rather than left: it built a creative carrying
    a legacy reference WITHOUT routing it through ``resolvable_format_ref``, the door that
    refuses a reference the pinned capture does not serve. A bypass sitting beside a new
    door is how the door gets routed around later by someone who finds the simpler
    parameter -- and this change exists precisely because an unserved id went unnoticed for
    months. A legacy-arm caller goes through ``given_sync_legacy_creative``, which uses the
    door.
    """
    from tests.factories.creative import CreativeFactory
    from tests.factories.creative_asset import build_assets, manifest_image_spec

    arm: dict[str, Any] = {"canonical_kind": kind}
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        status=status,
        data={"assets": assets if assets is not None else build_assets(manifest_image_spec(asset_key))},
        **arm,
        **row,
    )
    ctx["env"]._commit_factory_data()
    ctx["manifest_creative_id"] = creative.creative_id
    return creative


@given(parsers.parse('the buyer assigns a canonical "{kind}" creative carrying the "{manifest}" manifest'))
def given_canonical_creative_with_manifest(ctx: dict, kind: str, manifest: str) -> None:
    """A stored canonical creative with the named manifest, assigned to the package.

    The create path grades the STORED creative, so it is seeded as stored -- the same
    production identity mapping a sync writes -- and only its assets vary between rows.
    """
    creative = _seed_canonical_creative(ctx, kind=kind, assets=_manifest(manifest))
    ctx["request_kwargs"]["packages"][0]["creative_ids"] = [creative.creative_id]


@given(parsers.parse('the buyer assigns canonical "{kind}" creatives carrying the "{manifests}" manifests'))
def given_canonical_creatives_with_manifests(ctx: dict, kind: str, manifests: str) -> None:
    """Several stored canonical creatives, one per comma-separated manifest, all on the package.

    The package's creative SET is what the placement-coverage rule grades, so the set is the
    scenario's parameter: one creative per manifest, each eligible for every placement
    (a create package routes no creative to a placement).
    """
    _assign_canonical_creatives(ctx, kind, manifests)


@given(
    parsers.parse(
        'the buyer assigns canonical "{kind}" creatives carrying the "{manifests}" manifests '
        'naming product options "{options}"'
    )
)
def given_canonical_creatives_with_manifests_and_options(ctx: dict, kind: str, manifests: str, options: str) -> None:
    """The creative set above, each creative routed by ``format_option_ref`` to its option.

    canonical-formats.mdx: the ref "is **REQUIRED** on the manifest when the target product's
    ``format_options`` contains two or more declarations sharing the same ``format_kind``".
    *options* is comma-separated and aligned with *manifests*.
    """
    _assign_canonical_creatives(ctx, kind, manifests, options)


def _assign_canonical_creatives(ctx: dict, kind: str, manifests: str, options: str | None = None) -> None:
    """Seed :func:`_seed_canonical_creatives` and put every one on the package being built."""
    ctx["request_kwargs"]["packages"][0]["creative_ids"] = [
        creative.creative_id for creative in _seed_canonical_creatives(ctx, kind, manifests, options)
    ]


def _product_option_refs(ctx: dict, options: str | None, count: int) -> list[dict[str, str] | None]:
    """A ``format_option_ref`` per comma-separated option id, or none at all.

    ``core/format-option-ref.json`` has two arms, and the option the product declares says
    which one names it: ``{scope: "publisher", publisher_domain, format_option_id}`` for a
    declaration scoped to a publisher, ``{scope: "product", format_option_id}`` otherwise
    (formats.mdx: "An exact publisher claim must match both ``publisher_domain`` and
    ``format_option_id``"). An id the product does not declare takes the product arm.
    Aligned with the manifests the caller seeds: one per creative, in order.
    """
    if options is None:
        return [None] * count
    scoped = {
        option["format_option_id"]: option["publisher_domain"]
        for option in ctx["default_product"].format_options or []
        if option.get("format_option_id") and option.get("publisher_domain")
    }
    refs: list[dict[str, str] | None] = []
    for option in options.split(","):
        option_id = option.strip()
        domain = scoped.get(option_id)
        refs.append(
            {"scope": "publisher", "publisher_domain": domain, "format_option_id": option_id}
            if domain
            else {"scope": "product", "format_option_id": option_id}
        )
    assert len(refs) == count, f"{len(refs)} options for {count} creatives"
    return refs


def _seed_canonical_creatives(ctx: dict, kind: str, manifests: str, options: str | None = None) -> list[Any]:
    """One committed canonical creative per comma-separated :func:`_manifest` name, in order.

    *options*, when given, routes each to the aligned product option (:func:`_product_option_refs`).
    """
    names = [name.strip() for name in manifests.split(",")]
    return [
        _seed_canonical_creative(ctx, kind=kind, assets=_manifest(name), format_option_ref_document=ref)
        for name, ref in zip(names, _product_option_refs(ctx, options, len(names)), strict=True)
    ]


@given(parsers.parse('a canonical "{kind}" creative carrying the "{manifest}" manifest exists'))
def given_canonical_creative_with_manifest_exists(ctx: dict, kind: str, manifest: str) -> None:
    """The update path's creative: stored with the named manifest, assigned by the When."""
    ctx["creative_to_assign"] = _seed_canonical_creative(ctx, kind=kind, assets=_manifest(manifest))


@given(
    parsers.parse(
        'canonical "{kind}" creatives carrying the "{manifests}" manifests naming product options "{options}" exist'
    )
)
def given_canonical_creatives_with_manifests_exist(ctx: dict, kind: str, manifests: str, options: str) -> None:
    """The update path's creative set: one stored creative per manifest, each routed to the
    aligned product option (:func:`_product_option_refs`), assigned by the When."""
    ctx["creatives_to_assign"] = _seed_canonical_creatives(ctx, kind, manifests, options)


# ── UPDATE path ─────────────────────────────────────────────────────


@given("the buyer already holds a media buy for that product")
def given_existing_media_buy(ctx: dict) -> None:
    """Seed a committed media buy with one package naming the canonical product.

    The package's ``package_config.product_id`` is what the update gate resolves the product
    from, so the buy has to name THIS product rather than the env's default one -- the gate
    would otherwise grade the selector against a product the scenario never declared.
    """
    from datetime import UTC, datetime, timedelta

    from tests.factories import MediaBuyFactory, MediaPackageFactory

    media_buy = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        status="pending_approval",
        currency="USD",
        start_time=datetime.now(UTC),
        end_time=datetime.now(UTC) + timedelta(days=30),
    )
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": ctx["default_product"].product_id, "budget": 5000.0},
    )
    ctx["env"]._commit_factory_data()
    ctx["existing_media_buy"] = media_buy
    ctx["existing_package"] = package


@given(parsers.parse('a creative identified by canonical kind "{kind}" exists'))
def given_creative_exists(ctx: dict, kind: str) -> None:
    """Seed the creative the update will assign, without assigning it yet."""
    ctx["creative_to_assign"] = _seed_canonical_creative(ctx, kind=kind)


@when("the Buyer Agent assigns that creative through update_media_buy")
def when_update_assigns_creative(ctx: dict) -> None:
    """Dispatch a real update_media_buy carrying the creative on its package.

    A RAW flat body: ``media_buy_id`` is what ``MediaBuyDualEnv._is_update_request`` routes
    on, and dispatching the flat kwargs sends them through the real route and production
    Pydantic rather than constructing the request in the test process -- the same reason the
    create side dispatches raw.
    """
    _dispatch_package_update(ctx, creative_ids=[ctx["creative_to_assign"].creative_id])


@when("the Buyer Agent uploads the package's creatives through update_media_buy")
def when_update_uploads_creatives(ctx: dict) -> None:
    """Dispatch a real update_media_buy whose package carries the creatives inline.

    The creatives are the ones a Given put on the package the buyer is building
    (``ctx["request_kwargs"]["packages"][0]["creatives"]``), so the create and update
    scenarios upload the same bytes through one Given.
    """
    _dispatch_package_update(ctx, creatives=ctx["request_kwargs"]["packages"][0]["creatives"])


#: The routing token for an assignment that names no ``placement_ids``: "When omitted, the
#: creative is eligible for all placements" (``sync-creatives-request.json``).
_EVERY_PLACEMENT = "any"


@when(parsers.parse('the Buyer Agent assigns those creatives through update_media_buy routed to "{routing}"'))
def when_update_assigns_routed_creatives(ctx: dict, routing: str) -> None:
    """Dispatch update_media_buy with one ``creative_assignments`` entry per seeded creative.

    *routing* is comma-separated and aligned with the creatives: a placement id routes that
    creative there through ``placement_ids``, and ``any`` omits the member. The package's
    ``creative_assignments`` replace its creatives, so this is the whole set the seller grades.
    """
    targets = [target.strip() for target in routing.split(",")]
    creatives = ctx["creatives_to_assign"]
    assert len(targets) == len(creatives), f"{len(targets)} routes for {len(creatives)} creatives"
    _dispatch_package_update(
        ctx,
        creative_assignments=[
            {"creative_id": creative.creative_id} | ({} if target == _EVERY_PLACEMENT else {"placement_ids": [target]})
            for creative, target in zip(creatives, targets, strict=True)
        ],
    )


#: How a held package came by a creative: ``inline`` through a package's ``creatives`` array,
#: ``library`` through ``creative_ids`` / ``creative_assignments``. update_media_buy.mdx gives
#: the two separate replacement scopes, and ``CreativeAssignment.inline`` is what records it.
_ASSIGNMENT_ORIGINS = {"inline": True, "library": False}


@given(
    parsers.parse(
        'the package already carries {origin:w} canonical "{kind}" creatives carrying the "{manifests}" manifests'
    )
)
def given_package_carries_creatives(ctx: dict, origin: str, kind: str, manifests: str) -> None:
    """Assign one stored canonical creative per manifest to the held package, from *origin*.

    Each assignment names no ``placement_ids``, so each creative is eligible for every
    placement, and the set covers what its manifests fill. Each seeded ``(creative_id,
    inline)`` pair is kept in ``ctx["seeded_assignments"]``, the package's state before the
    When, which a Then compares the persisted rows against.
    """
    from tests.factories.creative import CreativeAssignmentFactory

    assert origin in _ASSIGNMENT_ORIGINS, f"no assignment origin {origin!r}; have {sorted(_ASSIGNMENT_ORIGINS)}"
    seeded = ctx.setdefault("seeded_assignments", set())
    for creative in _seed_canonical_creatives(ctx, kind, manifests):
        CreativeAssignmentFactory(
            creative=creative,
            media_buy=ctx["existing_media_buy"],
            package_id=ctx["existing_package"].package_id,
            inline=_ASSIGNMENT_ORIGINS[origin],
        )
        seeded.add((creative.creative_id, _ASSIGNMENT_ORIGINS[origin]))
    ctx["env"]._commit_factory_data()


@when(
    parsers.parse(
        "the Buyer Agent replaces the package's inline creatives through update_media_buy "
        'with canonical "{kind}" creatives carrying the "{manifests}" manifests naming product options "{options}"'
    )
)
def when_update_replaces_inline_creatives(ctx: dict, kind: str, manifests: str, options: str) -> None:
    """Dispatch update_media_buy whose package ``creatives`` carry one NEW body per manifest.

    package-update.json ``creatives``: "Replace this package's inline creative assets". Each
    body has a fresh ``creative_id``, so none of the held inline creatives is kept and the
    array replaces them all, and each names the aligned product option
    (:func:`_product_option_refs`). The ids sent are kept in ``ctx["uploaded_creative_ids"]``.
    """
    names = [name.strip() for name in manifests.split(",")]
    ctx["uploaded_creative_ids"] = [f"satisfaction_inline_{uuid.uuid4().hex[:12]}" for _ in names]
    _dispatch_package_update(
        ctx,
        creatives=[
            _canonical_claim(creative_id, kind, assets=_manifest(name), format_option_ref=ref)
            for creative_id, name, ref in zip(
                ctx["uploaded_creative_ids"], names, _product_option_refs(ctx, options, len(names)), strict=True
            )
        ],
    )


def _dispatch_package_update(ctx: dict, **package_members: Any) -> None:
    """One update_media_buy against the held buy's package, carrying *package_members*."""
    from tests.bdd.steps.generic._dispatch import dispatch_package_update

    dispatch_package_update(
        ctx,
        media_buy_id=ctx["existing_media_buy"].media_buy_id,
        package_id=ctx["existing_package"].package_id,
        **package_members,
    )


# ── ASSIGNMENT path (sync_creatives) ────────────────────────────────


@given("a packaged product declaring a canonical image option fixed at 300x250")
def given_sync_canonical_product(ctx: dict) -> None:
    """Seed tenant + principal + a DUAL-ARM product + a media buy and package to assign to.

    Its own seeding rather than the create chain's: ``CreativeSyncEnv`` is a different env
    with a different default seed, and the assignment gate resolves the product from
    ``package_config.product_id``, so the package has to name the product this scenario
    declared.
    """
    _seed_sync_chain(ctx, format_options=_DECLARATIONS["fixed_300x250"], format_ids=list(_LEGACY_ARM))


@given(parsers.parse('a packaged product declaring canonical format options "{declaration}"'))
def given_sync_declaring_product(ctx: dict, declaration: str) -> None:
    """The sync chain over a dual-arm product carrying the named :data:`_DECLARATIONS` row.

    The named sibling of the fixed-300x250 Given above, for the rows that need a product
    publishing SEVERAL declarations of one kind -- the case ``format_option_ref`` exists for.
    """
    _seed_sync_chain(ctx, format_options=_DECLARATIONS[declaration], format_ids=list(_LEGACY_ARM))


@given(
    parsers.parse('a packaged product whose placements "{placements}" narrow canonical format options "{declaration}"')
)
def given_sync_product_across_placements(ctx: dict, placements: str, declaration: str) -> None:
    """The sync chain over a dual-arm product that also stores the named placements.

    Worded to start differently from the bare declaration Given above, whose trailing
    ``"{declaration}"`` would otherwise match across the rest of a longer sentence.
    """
    _seed_sync_chain(
        ctx,
        format_options=_DECLARATIONS[declaration],
        format_ids=list(_LEGACY_ARM),
        placements=_PLACEMENTS[placements],
    )


@given(
    parsers.parse(
        'a packaged product selling only deprecated formats the SDK cannot project across placements "{placements}"'
    )
)
def given_sync_unprojectable_product_across_placements(ctx: dict, placements: str) -> None:
    """The sync chain over the legacy-only product of the two unprojectable references.

    The assignment path's twin of :func:`given_unprojectable_product_across_placements`: a
    creative CLAIMING a deprecated reference reaches the seller only through sync, so the
    rows grading how a placement's deprecated ``format_ids`` narrow such a creative live here.
    """
    _seed_sync_chain(
        ctx,
        format_options=None,
        format_ids=[_UNPROJECTABLE_MREC, _UNPROJECTABLE_LEADERBOARD],
        placements=_stored_placements(placements),
    )


@given("a packaged product declaring only the deprecated format_ids")
def given_sync_legacy_product(ctx: dict) -> None:
    """The LEGACY-arm control for the lenient asymmetry: no ``format_options`` at all.

    Which lane the satisfaction function takes is decided by what the PRODUCT declares, so
    this is the one knob that moves the sibling scenario from the canonical lane to the
    deprecated one -- same tool, same mode, same condition.
    """
    _seed_sync_chain(ctx, format_options=None, format_ids=list(_LEGACY_ARM))


@given(parsers.parse("a packaged product selling only a deprecated format sized {width:d}x{height:d}"))
def given_sync_legacy_sized_product(ctx: dict, width: int, height: int) -> None:
    """A legacy-only product whose deprecated entry names a TEMPLATE id and SIZES it.

    The fixture the acceptance rule needs, and the module had no shape that could express it:
    ``_LEGACY_ARM`` leaves the arm's dimensions unset, so a product restricting to 300x250 and
    a product restricting to nothing produce the same accepted set and no assertion can tell
    a conforming creative from a mis-sized one (salesagent-4764o).

    A TEMPLATE id carrying parameters, not a name-encoded one, and that is measured rather
    than stylistic: ``upgrade_legacy_format_id`` rewrites a reference's parameters from its
    NAME, so ``display_300x250_image`` sized 728x90 upgrades to ``display_image`` at 300x250
    and compares equal to the product -- a name-encoded id cannot express a mis-sized claim at
    all. ``core/format-id.json`` is explicit that the two spellings are different things: an
    id "used alone, references a template format. When combined with dimension/duration
    fields, creates a parameterized format ID for a specific variant."

    LEGACY-ONLY because the lane is chosen by what the PRODUCT declares. With a canonical arm
    present the claim would be graded by ``_canonical_lane``, which projects, and the row
    would grade the projection instead of the deprecated arm's own comparison.
    """
    _seed_legacy_sized_sync_chain(ctx, width, height, agent_url=DEFAULT_CREATIVE_AGENT_URL)


@given(
    parsers.parse(
        "a packaged product selling only a deprecated format sized {width:d}x{height:d} "
        "at the creative agent's url with {variant}"
    )
)
def given_sync_legacy_sized_product_spelled(ctx: dict, width: int, height: int, variant: str) -> None:
    """The same legacy-only product, its entry naming the creative agent in another spelling.

    The assignment path's half of ``core/format-id.json``'s MUST. It is the one comparison the
    deprecated lane makes that a respelling reaches: the creative's claim is resolved against
    the agent's catalog under its own spelling, and only the PRODUCT's entry is respelled, so
    nothing upstream of the satisfaction verdict sees the second spelling at all.
    """
    _seed_legacy_sized_sync_chain(ctx, width, height, agent_url=_agent_url_spelled(variant))


def _seed_legacy_sized_sync_chain(ctx: dict, width: int, height: int, *, agent_url: str) -> None:
    """The sync chain over a legacy-only product selling one sized template at *agent_url*."""
    _seed_sync_chain(
        ctx,
        format_options=None,
        format_ids=[{"agent_url": agent_url, "id": "display_image", "width": width, "height": height}],
    )


@given(
    parsers.parse(
        "a packaged product selling only a deprecated format sized {width:d}x{height:d} at {pixel_ratio:d}x density"
    )
)
def given_sync_legacy_density_product(ctx: dict, width: int, height: int, pixel_ratio: int) -> None:
    """The same legacy-only product, additionally fixing the asset DENSITY it requires.

    ``pixel_ratio`` is the fourth parameterizing field, and the one an earlier SDK's
    comparison did not read. This row is what keeps the density comparison falsifiable: with
    no product fixing a density, a comparison that ignored the field would pass every row.

    The pin requires the match and requires the dimensions alongside it:
    ``core/format-id.json`` calls the field "**Required** intrinsic-pixel density for a
    parameterized visual format" and says it "Requires ``width`` and ``height``", so a
    density is seeded WITH a size rather than instead of one.
    """
    _seed_sync_chain(
        ctx,
        format_options=None,
        format_ids=[
            {
                "agent_url": DEFAULT_CREATIVE_AGENT_URL,
                "id": "display_image",
                "width": width,
                "height": height,
                "pixel_ratio": pixel_ratio,
            }
        ],
    )


def _seed_sync_chain(
    ctx: dict,
    *,
    format_options: list[dict[str, Any]] | None,
    format_ids: list[dict[str, str]],
    placements: list[dict[str, Any]] | None = None,
) -> None:
    """The product, media buy and package a sync_creatives assignment needs.

    The tenant and principal are the ROUTE's (``_seed_default_tenant_and_principal``), not
    re-seeded here: ``setup_default_data`` re-creates a missing principal, and calling it
    twice is how a Given comes to undo what the route already established.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory

    tenant, principal = ctx["tenant"], ctx["principal"]
    product = ProductFactory(
        tenant=tenant,
        product_id="satisfaction_sync_product",
        delivery_type="non_guaranteed",
        format_ids=format_ids,
        format_options=format_options,
        placements=placements,
        property_tags=["all_inventory"],
    )
    PricingOptionFactory(product=product, pricing_model="cpm", currency="USD", is_fixed=True)
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
    package = MediaPackageFactory(media_buy=media_buy, package_config={"product_id": product.product_id})
    ctx["env"]._commit_factory_data()
    ctx["default_product"] = product
    ctx["existing_package"] = package


#: The creative the sync scenarios send. One id, because the assignment is about this one
#: creative and the result is read off ``creatives[0]``.
_SYNC_CREATIVE_ID = "satisfaction_sync_creative"


@given(parsers.parse('the buyer syncs a creative identified by canonical kind "{kind}"'))
def given_sync_canonical_creative(ctx: dict, kind: str) -> None:
    """The creative payload the sync will carry, identified by its CANONICAL kind.

    A PAYLOAD rather than a pre-seeded row, because ``SyncCreativesRequest.creatives`` is
    required with ``min_length=1`` -- a buyer cannot assign without syncing, so sync-and-
    assign in one call IS the real flow and seeding a row then sending an empty list is a
    shape the pin refuses (measured: ``List should have at least 1 item``).
    """
    _sync_canonical_claim(ctx, kind)


@given(
    parsers.parse(
        'the buyer syncs a creative whose format_option_ref names product option "{option}" of canonical kind "{kind}"'
    )
)
def given_sync_canonical_creative_with_option(ctx: dict, option: str, kind: str) -> None:
    """The canonical claim NARROWED to one product option, as creative-manifests.mdx
    § Exact option routing spells it: ``format_kind`` plus a product-scoped
    ``format_option_ref`` ``{scope: "product", format_option_id}``.

    Worded to not end on the kind's quoted value, so the bare-kind Given above (whose
    ``"{kind}"`` would otherwise match across the rest of this sentence) cannot bind it.
    """
    _sync_canonical_claim(ctx, kind, format_option_ref={"scope": "product", "format_option_id": option})


@given(parsers.parse('the buyer syncs a canonical "{kind}" creative carrying the "{manifest}" manifest'))
def given_sync_canonical_creative_with_manifest(ctx: dict, kind: str, manifest: str) -> None:
    """The canonical claim on the wire with the named manifest (:func:`_manifest`)."""
    _sync_canonical_claim(ctx, kind, assets=_manifest(manifest))


@given(
    parsers.parse(
        'the buyer syncs a canonical "{kind}" creative carrying the "{manifest}" manifest '
        'and naming product option "{option}"'
    )
)
def given_sync_canonical_creative_with_manifest_and_option(ctx: dict, kind: str, manifest: str, option: str) -> None:
    """The same claim routed to one product option by ``format_option_ref``.

    creative-manifests.mdx § Exact option routing: "When a product accepts multiple
    declarations of the same kind ... add format_option_ref."
    """
    _sync_canonical_claim(
        ctx,
        kind,
        assets=_manifest(manifest),
        format_option_ref={"scope": "product", "format_option_id": option},
    )


def _sync_canonical_claim(ctx: dict, kind: str, **members: Any) -> None:
    """The sync scenarios' one creative: a :func:`_canonical_claim` under :data:`_SYNC_CREATIVE_ID`."""
    ctx["creatives"] = [_canonical_claim(_SYNC_CREATIVE_ID, kind, **members)]
    ctx["manifest_creative_id"] = _SYNC_CREATIVE_ID


def _canonical_claim(creative_id: str, kind: str, **members: Any) -> dict[str, Any]:
    """ONE canonical claim on the wire: ``format_kind`` and no ``format_id`` key at all.

    ``format_id=OMIT`` is what makes it canonical: ``core/creative-asset.json`` is a oneOf
    on the identifier, so the legacy arm has to be ABSENT rather than null -- the pin
    reports absent and null identically, and the factory's ``OMIT`` is the only way a call
    site can say which it meant.

    The assets fill the kind's required slots (``canonical_assets``): the seller grades a
    canonical manifest against the declaration it selects, or its kind when it selects none
    (creative-manifests.mdx § Validation), so the factory's default ``image``-keyed asset
    would be a manifest missing ``image_main``. *members* may replace them, and must for a
    kind outside the pinned vocabulary, which declares no slots to fill.
    """
    from tests.factories.creative_asset import canonical_assets
    from tests.factories.request import OMIT, CreativeAssetRequestFactory

    if "assets" not in members:
        members["assets"] = canonical_assets(kind)
    return CreativeAssetRequestFactory.payload(creative_id=creative_id, format_id=OMIT, format_kind=kind, **members)


def _mixed_identity_creative(format_id: str, kind: str) -> dict[str, Any]:
    """A creative naming BOTH identifiers, declared as the malformation it is.

    creative-manifests.mdx § Deprecated compatibility branch: a 3.2 implementation "must
    not ... mix format_id and format_kind in one manifest", and core/creative-asset.json's
    root oneOf refuses it (each arm carries a ``not`` on the other's identifier). The
    ``format_id`` is one the agent serves (``resolvable_format_ref``) and the kind is in the
    vocabulary, so each half is valid alone and only the MIX is wrong.
    """
    from src.core.errors.codes import ErrorCode
    from tests.factories.format import resolvable_format_ref
    from tests.factories.malformed import malformed
    from tests.factories.request import CreativeAssetRequestFactory

    return malformed(
        "semantic",
        "both format_id and format_kind present, each valid on its own; core/creative-asset.json's "
        "root oneOf admits exactly one, so the pin refuses the item with [type=oneOf].",
        CreativeAssetRequestFactory.payload(
            creative_id=_SYNC_CREATIVE_ID, format_id=resolvable_format_ref(format_id), format_kind=kind
        ),
        obligation=ErrorCode.INVALID_REQUEST,
    )


@given(
    parsers.parse('the buyer syncs a creative identified by both format_id "{format_id}" and canonical kind "{kind}"')
)
def given_sync_mixed_identity_creative(ctx: dict, format_id: str, kind: str) -> None:
    """The sync item carries both identifiers (see ``_mixed_identity_creative``)."""
    ctx["creatives"] = [_mixed_identity_creative(format_id, kind)]


@given(parsers.parse('the buyer syncs a creative identified by canonical kind "{kind}" beside a null format_id'))
def given_sync_kind_beside_null_format_id(ctx: dict, kind: str) -> None:
    """The canonical claim with the legacy key PRESENT and null, as a dict on the wire.

    A dict and not a model, because a model dumped for the wire drops the null (an unset
    optional member is absent on a buyer's wire) and would send the legal canonical item.
    """
    from src.core.errors.codes import ErrorCode
    from tests.factories.malformed import malformed
    from tests.factories.request import CreativeAssetRequestFactory

    ctx["creatives"] = [
        malformed(
            "explicit_none",
            "format_kind beside format_id: null. core/creative-asset.json's root oneOf arms each pair a "
            "required with a not-required, and required counts a present key whatever its value, so the "
            "item names both identifiers and matches neither arm.",
            CreativeAssetRequestFactory.payload(creative_id=_SYNC_CREATIVE_ID, format_id=None, format_kind=kind),
            obligation=ErrorCode.INVALID_REQUEST,
        )
    ]


@given(
    parsers.parse(
        'the buyer\'s package uploads a creative identified by both format_id "{format_id}" and canonical kind "{kind}"'
    )
)
def given_package_mixed_identity_creative(ctx: dict, format_id: str, kind: str) -> None:
    """The package the buyer is building carries the mixed creative inline.

    ``packages[].creatives[]`` is core/creative-asset.json on both create_media_buy
    (media-buy/package-request.json) and update_media_buy (media-buy/package-update.json),
    so the create When sends this package as built and the update When sends its creatives.
    """
    ctx["request_kwargs"]["packages"][0]["creatives"] = [_mixed_identity_creative(format_id, kind)]


@given(parsers.parse('a creative identified by canonical kind "{kind}" is already in the library as "{status}"'))
def given_canonical_creative_in_library(ctx: dict, kind: str, status: str) -> None:
    """The library row a later sync of the SAME creative_id updates, in review *status*.

    Same id as the sync Givens' item, and the route's principal, so the sync takes the
    update path (``creative_repo.get_by_id(creative_id, principal_id)``).
    """
    _seed_canonical_creative(ctx, kind=kind, status=status, creative_id=_SYNC_CREATIVE_ID)


def _sync_legacy_claim(ctx: dict, format_id: str, *, assets: dict[str, Any] | None = None, **parameters: int) -> None:
    """ONE legacy claim on the wire, against the catalog that answers it.

    The three Givens below differ only in which of the reference's own parameterizing
    fields the scenario names, so the payload and the catalog are set up here once.

    Built through ``resolvable_format_ref`` rather than from the scenario's string
    directly, because a CREATIVE's claim is resolved over the wire: the id has to be one
    the creative agent actually serves, or the sync is refused REFERENCE_NOT_FOUND before
    any satisfaction verdict exists to grade. The scenario still says WHICH id, and it
    cannot name one that only the in-process registry mock would answer.

    AND THE CATALOG IS ALREADY THERE, which is why this step arranges none. The door
    ``resolvable_format_ref`` guards asks whether the AGENT serves the id; the projection
    then asks the agent what canonical kind it declares, and both now read the committed
    capture on every env this step routes to -- ``CreativeSyncEnv`` installs production's
    ``ReferenceFormatsRegistry`` serving the whole capture (1883dedc4), and
    ``LibraryUpsertCreativeSyncEnv`` leaves ``get_creative_agent_registry`` unpatched, so
    the registry it reaches serves the same capture for itself. Measured:
    ``video_1280x720`` is in the capture and declares ``canonical.kind = video_hosted``,
    and no registry glob matches it.

    THIS STEP USED TO CALL ``serve_reference_catalog()`` HERE (since deleted). That
    method wrote through ``mock["registry"]``, a key ``LibraryUpsertCreativeSyncEnv``
    DELETES after ``__enter__`` (the trap its own docstring names), so the two
    ``@creative_library_upsert`` scenarios routed there died in this Given with
    ``KeyError: 'registry'`` on all four transports -- measured red at
    ``tests/harness/creative_sync.py:1148`` in box run innet_171026_2156, and before
    1883dedc4 renamed the key, as ``KeyError: 'run_async'`` in innet_171026_2038. The call
    bought nothing on either env: it only UNDOES a prior narrowing, no Given here narrows,
    and the capture is the default. A catalogue a scenario needs narrowed says so with
    ``set_served_catalogue``, which seeds a real peer agent rather than the registry and
    so works on the real-registry env too.

    THE ASSETS ARE WHAT THE NAMED FORMAT REQUIRES (``named_format_assets``) unless *assets*
    says otherwise: the seller holds a legacy manifest to its named format's required assets
    (creative-manifests.mdx § Validation), so a claim on a video format carrying the factory's
    display assets is refused for its manifest before any lane is graded.
    """
    from tests.factories.creative_asset import named_format_assets
    from tests.factories.format import resolvable_format_ref
    from tests.factories.request import CreativeAssetRequestFactory

    ctx["creatives"] = [
        CreativeAssetRequestFactory.payload(
            creative_id=_SYNC_CREATIVE_ID,
            format_id=resolvable_format_ref(format_id, **parameters),
            assets=named_format_assets(format_id) if assets is None else assets,
        )
    ]
    # The creative a refusal names, as the canonical claim records it: a legacy reference the
    # product takes at other parameters is refused with the creative's own code.
    ctx["manifest_creative_id"] = _SYNC_CREATIVE_ID


@given(
    parsers.parse(
        'the buyer syncs a creative whose format_option_ref names product option "{option}" beside format_id "{format_id}"'
    )
)
def given_sync_legacy_creative_with_option(ctx: dict, option: str, format_id: str) -> None:
    """The DEPRECATED arm carrying a ``format_option_ref`` beside it.

    ``core/creative-asset.json`` declares ``format_option_ref`` at the item's top level and
    forbids it in neither oneOf arm, so a v1 item may route to an exact product option too.
    Worded like the canonical sibling, option first, so neither this sentence nor the plain
    ``identified by format_id "{format_id}"`` Given can bind the other's text.
    """
    _sync_legacy_claim(ctx, format_id)
    ctx["creatives"][0]["format_option_ref"] = {"scope": "product", "format_option_id": option}


@given(parsers.parse('the buyer syncs a creative identified by format_id "{format_id}"'))
def given_sync_legacy_creative(ctx: dict, format_id: str) -> None:
    """The same payload identified by its DEPRECATED reference, for the legacy lane's row.

    The ONE difference from its canonical sibling is which identifier arm the creative
    carries, which is what decides the lane -- so the lenient asymmetry's two rows differ in
    the creative's arm and the product's arm and nothing else. Its assets are the ones the
    named format requires (:func:`_sync_legacy_claim`).
    """
    _sync_legacy_claim(ctx, format_id)


@given(
    parsers.parse('the buyer syncs a creative identified by format_id "{format_id}" carrying the "{manifest}" manifest')
)
def given_sync_legacy_creative_with_manifest(ctx: dict, format_id: str, manifest: str) -> None:
    """The legacy claim with the named manifest (:func:`_legacy_manifest`)."""
    _sync_legacy_claim(ctx, format_id, assets=_legacy_manifest(format_id, manifest))


def _legacy_manifest(format_id: str, manifest: str) -> dict[str, Any]:
    """A manifest for named format *format_id*, by name, derived from what the format requires.

    ``complete`` is every required asset of the format as the capture serves it
    (``named_format_assets``). ``<asset_id>_missing`` drops one of them, and
    ``<asset_id>_as_text`` fills it with a text asset instead of the type the format declares.
    The asset id must be one the format requires, so a row cannot name an asset whose absence
    the format would not notice.
    """
    from tests.factories.creative_asset import build_assets, named_format_assets, text_spec

    assets = named_format_assets(format_id)
    if manifest == "complete":
        return assets
    for suffix in ("_missing", "_as_text"):
        if manifest.endswith(suffix):
            asset_id = manifest.removesuffix(suffix)
            assert asset_id in assets, f"{format_id!r} requires no asset {asset_id!r}; it requires {sorted(assets)}"
            del assets[asset_id]
            if suffix == "_as_text":
                assets.update(build_assets(text_spec(asset_id, content="not the declared type")))
            return assets
    raise AssertionError(f"unknown legacy manifest {manifest!r}: complete, <asset_id>_missing or <asset_id>_as_text")


@given(parsers.parse('the buyer syncs a creative identified by format_id "{format_id}" sized {width:d}x{height:d}'))
def given_sync_legacy_creative_sized(ctx: dict, format_id: str, width: int, height: int) -> None:
    """The same claim, PARAMETERIZED -- the buyer naming one variant of a template.

    The ONE difference from the unsized sibling is that the reference carries its own
    width and height, which ``core/format-id.json`` says is what names a specific variant
    rather than the template. That is the dimension every other fixture in this module holds
    constant, and holding it constant is why the acceptance comparison could be wrong with
    the whole module green: with no parameters on either side, "compare the template" and
    "compare the variant" return the same answer for every row.

    The parameters go through ``resolvable_format_ref`` with the rest of the reference, so the
    pin's own field names are the only ones a scenario can spell.
    """
    _sync_legacy_claim(ctx, format_id, width=width, height=height)


@given(
    parsers.parse(
        'the buyer syncs a creative identified by format_id "{format_id}" '
        "sized {width:d}x{height:d} at {pixel_ratio:d}x density"
    )
)
def given_sync_legacy_creative_density(ctx: dict, format_id: str, width: int, height: int, pixel_ratio: int) -> None:
    """The claim naming a DENSITY as well as a size -- the fourth parameterizing field.

    Its own step rather than an optional clause on the sized sibling, because a scenario
    that says nothing about density means the claim CARRIES no density, and that silence is
    one of the two cases the rule has to refuse against a product that fixes one. A step
    defaulting the field would make that case unreachable.
    """
    _sync_legacy_claim(ctx, format_id, width=width, height=height, pixel_ratio=pixel_ratio)


def _serve_publisher_format(
    ctx: dict, format_id: str, asset_types: str, kind: str | None, *, slot_fields: dict[str, Any] | None = None
) -> None:
    """The seller's catalogue is ONE publisher format, served by its own creative agent.

    For a scenario whose subject is what the registry's resolution order does with a format
    the reference agent does not carry: the format's declared asset types, and whether it
    declares a ``canonical`` projection at all, are the two inputs steps 2 and 4 read. It is
    served for real by the tenant's peer agent (``set_served_catalogue``), so every
    transport resolves the creative against the same served declaration. *slot_fields* are
    further members every slot carries (:func:`_publisher_format`).
    """
    ctx["served_format"] = _publisher_format(format_id, asset_types, kind, slot_fields=slot_fields)
    ctx["env"].set_served_catalogue([ctx["served_format"]])


def _publisher_format(
    format_id: str,
    asset_types: str,
    kind: str | None,
    *,
    slot_fields: dict[str, Any] | None = None,
    **format_id_fields: str,
) -> Any:
    """One publisher format: its id, its comma-separated asset types and its canonical kind, if any.

    *slot_fields* are further members every slot carries (its ``requirements``);
    *format_id_fields* override the reference's other members (its ``agent_url``).
    """
    from adcp.types import CanonicalProjectionReference

    from tests.factories.format import FormatFactory, FormatIdFactory, make_asset

    return FormatFactory(
        format_id=FormatIdFactory(id=format_id, **format_id_fields),
        assets=[
            make_asset(t, asset_id=f"{t}_{i}", **(slot_fields or {})) for i, t in enumerate(asset_types.split(", "))
        ],
        canonical=CanonicalProjectionReference(kind=kind) if kind is not None else None,
    )


@given(
    parsers.parse(
        'the seller\'s creative agent serves "{format_id}" with assets "{asset_types}" declaring canonical kind "{kind}"'
    )
)
def given_served_publisher_format(ctx: dict, format_id: str, asset_types: str, kind: str) -> None:
    """A publisher format whose own declaration names its canonical kind (registry step 2)."""
    _serve_publisher_format(ctx, format_id, asset_types, kind)


@given(
    parsers.parse(
        'the seller\'s creative agent serves "{format_id}" with assets "{asset_types}" declaring no canonical kind'
    )
)
def given_served_unannotated_publisher_format(ctx: dict, format_id: str, asset_types: str) -> None:
    """A publisher format that leaves its projection to the registry (steps 3 and 4)."""
    _serve_publisher_format(ctx, format_id, asset_types, None)


@given(
    parsers.parse(
        'the seller\'s creative agent serves "{format_id}" with one "{asset_type}" slot requiring version '
        '"{version}" and no canonical kind'
    )
)
def given_served_versioned_publisher_format(ctx: dict, format_id: str, asset_type: str, version: str) -> None:
    """A publisher format whose one VAST or DAAST slot states its version, with no canonical projection.

    The version is stated the way core/format.json lets a slot state it, as the slot's
    ``requirements.<asset_type>_version`` (``vast_version`` / ``daast_version``): the
    registry's versioned structural entries match on it.
    """
    _serve_publisher_format(
        ctx, format_id, asset_type, None, slot_fields={"requirements": {f"{asset_type}_version": version}}
    )


#: The agent a relisted format is declared under: not the seller's creative agent, and not
#: one any canonicalization makes equal to it.
_RELISTING_AGENT_URL = "https://relisting-creatives.example"


@given(
    parsers.parse(
        'the seller\'s creative agent lists "{format_id}" with assets "{asset_types}" under another agent\'s URL'
    )
)
def given_relisted_publisher_format(ctx: dict, format_id: str, asset_types: str) -> None:
    """The seller's creative agent serves a format whose ``format_id.agent_url`` names ANOTHER agent.

    Served VERBATIM (``rehome=False``), so the entry keeps the agent it declares; that is
    the whole fixture. It declares its canonical kind, so a seller that took it as the named
    format would have a projection to store -- the refusal can only come from reading the
    declared agent.
    """
    from src.core.schemas import canonical_agent_url

    fmt = _publisher_format(format_id, asset_types, "image", agent_url=_RELISTING_AGENT_URL)
    ctx["served_format"] = fmt
    peer_url = ctx["env"].seed_own_creative_agent([fmt], rehome=False)
    assert canonical_agent_url(peer_url) != canonical_agent_url(_RELISTING_AGENT_URL), (
        "the seller's creative agent IS the agent the entry declares, so the entry is its own format "
        "and this scenario grades nothing"
    )
    ctx["relisted_format_id"] = format_id


@given("the buyer syncs a creative naming that format at the seller's creative agent")
def given_sync_relisted_format_creative(ctx: dict) -> None:
    """The legacy claim names the relisted id at the URL the seller's creative agent is dialled at.

    Its assets are the ones the relisted entry requires (``format_manifest``), so the only
    thing that can refuse it is the agent the entry declares.
    """
    from tests.factories.creative_asset import format_manifest
    from tests.factories.request import CreativeAssetRequestFactory

    ctx["creatives"] = [
        CreativeAssetRequestFactory.payload(
            creative_id=_SYNC_CREATIVE_ID,
            format_id={"agent_url": ctx["env"].peer_agent_url(), "id": ctx["relisted_format_id"]},
            assets=format_manifest(ctx["served_format"]),
        )
    ]


@given(
    parsers.parse(
        'the buyer syncs a creative identified by format_id "{format_id}" '
        "at the standard creative agent spelled with {variant}"
    )
)
def given_sync_legacy_creative_respelled(ctx: dict, format_id: str, variant: str) -> None:
    """A legacy claim at the standard creative agent, its URL in the named spelling.

    Not through ``resolvable_format_ref``: that door admits only ids the reference capture
    lists, and this scenario's id is one the capture does NOT list, which is its subject. The
    guard here is the opposite one -- the id must stay absent, or the agent's catalogue
    answers and the registry is never asked.
    """
    from tests.factories.request import CreativeAssetRequestFactory

    _assert_unlisted_by_the_agent(format_id)
    ctx["creatives"] = [
        CreativeAssetRequestFactory.payload(
            creative_id=_SYNC_CREATIVE_ID,
            format_id={"agent_url": _agent_url_spelled(variant), "id": format_id},
        )
    ]


def _assert_unlisted_by_the_agent(format_id: str) -> None:
    """Refuse a row whose id the reference capture lists: the agent's catalogue would answer it.

    The scenarios that use this grade the canonical mapping registry answering for an id the
    standard creative agent's deprecated catalogue does NOT list, so an id the capture holds
    would pass for the catalogue's reason and grade nothing.
    """
    from src.core.format_cache import load_reference_formats

    assert format_id not in {fmt.format_id.id for fmt in load_reference_formats()}, (
        f"{format_id!r} is in the reference capture, so the agent's catalogue resolves it and the "
        "registry's projection is not what this scenario grades; pick an id the capture lacks"
    )


@given(parsers.parse('the seller publishes only the deprecated format "{format_id}" at the standard creative agent'))
def given_legacy_only_named_product(ctx: dict, format_id: str) -> None:
    """Store a LEGACY-ONLY product whose one deprecated entry names *format_id* at the standard agent."""
    _store_product(
        ctx,
        f"satisfaction_legacy_{format_id}",
        format_ids=[{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": format_id}],
        format_options=None,
    )


@given(
    parsers.parse(
        'the buyer assigns a stored creative identified by format_id "{format_id}" sized {width:d}x{height:d} '
        "at the standard creative agent"
    )
)
def given_stored_legacy_creative_assigned(ctx: dict, format_id: str, width: int, height: int) -> None:
    """A library creative on a legacy id the agent does not list, named in the package's creative_ids.

    Seeded through ``CreativeFactory``, which derives the stored identity with the production
    accessor the sync path writes through, so the row is what a sync of that id stores. Its
    one media asset carries the URL and the size the create path's asset gate reads.
    """
    from tests.factories.creative import CreativeFactory

    _assert_unlisted_by_the_agent(format_id)
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        format=format_id,
        agent_url=DEFAULT_CREATIVE_AGENT_URL,
        approved=True,
        data={"assets": {"banner_image": {"url": "https://example.com/banner.png", "width": width, "height": height}}},
    )
    ctx["env"]._commit_factory_data()
    ctx["request_kwargs"]["packages"][0]["creative_ids"] = [creative.creative_id]


@given("the buyer syncs a creative identified by that served format")
def given_sync_served_format_creative(ctx: dict) -> None:
    """The legacy claim naming the format the peer publishes, at the URL it publishes it under.

    Not through ``resolvable_format_ref``: that door checks the REFERENCE capture, and this
    format is the peer's own. The identity is the one the env hands over, so no step spells
    an agent URL for a seeded format.
    """
    from tests.factories.creative_asset import format_manifest
    from tests.factories.request import CreativeAssetRequestFactory

    (served,) = ctx["env"].seeded_format_ids()
    ctx["creatives"] = [
        CreativeAssetRequestFactory.payload(
            creative_id=_SYNC_CREATIVE_ID,
            format_id={"agent_url": str(served.agent_url), "id": served.id},
            # What the served format requires, so the manifest is not what these rows grade.
            assets=format_manifest(ctx["served_format"]),
        )
    ]


@given("the buyer asks for lenient validation")
def given_lenient(ctx: dict) -> None:
    """``validation_mode: "lenient"`` -- "skip what this product will not take"."""
    ctx["validation_mode"] = "lenient"


@when("the Buyer Agent syncs it into the library")
def when_sync_library_only(ctx: dict) -> None:
    """Dispatch a real sync_creatives that WRITES the creative and assigns it to nothing.

    The sibling below assigns as well, and that difference is the whole point: the
    assignment path has a satisfaction gate (``_assignments.py`` calls ``satisfies``) while
    the library UPSERT path has none, so a scenario that assigns cannot tell which gate
    refused. With no ``assignments`` member there is exactly one thing under test -- whether
    the seller will STORE a creative whose format it cannot resolve to a canonical kind.
    """
    from tests.bdd.steps.generic._dispatch import dispatch_request

    dispatch_request(
        ctx,
        account={"account_id": ctx["env"].setup_default_account().account_id},
        creatives=ctx["creatives"],
    )


@when("the Buyer Agent assigns it to that package")
def when_sync_assigns(ctx: dict) -> None:
    """Dispatch a real sync_creatives that syncs the creative AND assigns it to the package."""
    from tests.bdd.steps.generic._dispatch import dispatch_request

    kwargs: dict[str, Any] = {
        "account": {"account_id": ctx["env"].setup_default_account().account_id},
        "creatives": ctx["creatives"],
        "assignments": [{"creative_id": _SYNC_CREATIVE_ID, "package_id": ctx["existing_package"].package_id}],
    }
    if "validation_mode" in ctx:
        kwargs["validation_mode"] = ctx["validation_mode"]
    dispatch_request(ctx, **kwargs)


@given(parsers.parse('the buyer\'s package selects "{selector}"'))
def given_selector(ctx: dict, selector: str) -> None:
    """Put the named format selector on the request's single package.

    MERGED onto the package the shared builder already assembled rather than replacing it:
    the package carries a product_id, a pricing_option_id and a budget that every other
    create scenario also sends, and a selector scenario is not about any of those.
    """
    _put_selector(ctx, _SELECTORS[selector])
    # Which selector was sent, for the conflict Then: its refusal must summarize exactly the
    # arms the buyer supplied, and reading them back off this table is what keeps the
    # scenario's claim tied to its own input rather than to a restated list.
    ctx["selector"] = selector


def _put_selector(ctx: dict, members: dict[str, Any]) -> None:
    """Merge *members* onto the request's single package."""
    packages = ctx["request_kwargs"]["packages"]
    assert len(packages) == 1, f"expected exactly one package to put a selector on, got {len(packages)}"
    packages[0].update(members)


@given(
    parsers.parse("the buyer's package selects format_id \"{format_id}\" at the creative agent's url with {variant}")
)
def given_selector_at_respelled_agent(ctx: dict, format_id: str, variant: str) -> None:
    """A legacy selector naming *format_id* at the creative agent's url in the named spelling."""
    _put_selector(ctx, {"format_ids": [{"agent_url": _agent_url_spelled(variant), "id": format_id}]})


# ── Then steps ──────────────────────────────────────────────────────


@then("the buyer receives the media buy")
def then_media_buy_allocated(ctx: dict) -> None:
    """Assert the buy exists ON THE WIRE and the response carries no error.

    BOTH halves. ``media_buy_id`` alone is satisfied by a response that allocated the buy
    and then reported a problem alongside it, and an absent ``errors`` key alone is
    satisfied by any non-answer -- so a gate that refused the selector could pass either
    one of them on its own. Read off the serialized body rather than the typed result: the
    typed payload has already coerced every field to its declared type, and what the
    scenario claims is what the buyer received.
    """
    allocated = wire_entry(ctx, "packages", index=0)
    assert allocated.get("product_id") == ctx["default_product"].product_id, (
        f"the allocated package names {allocated.get('product_id')!r}, not the product the selector was graded against"
    )
    media_buy_id = wire_field(ctx, "media_buy_id")
    assert isinstance(media_buy_id, str) and media_buy_id, (
        f"expected a non-empty media_buy_id on the wire, got {media_buy_id!r}"
    )
    wire_absent(ctx, "errors")


@then(parsers.parse('the buyer is refused UNSUPPORTED_FEATURE naming the "{arm}" selector'))
def then_refused_unsupported_feature(ctx: dict, arm: str) -> None:
    """Assert the wire envelope: the code, its pinned recovery, and the field.

    ``assert_wire_error`` defaults ``recovery`` to the PINNED enum's classification for the
    code, so the recovery is graded without this scenario restating it -- UNSUPPORTED_FEATURE
    is ``correctable`` there ("check get_adcp_capabilities and remove unsupported fields"),
    which is the right lever for a buyer who can name a different format.

    The field is pinned because the two canonical selector arms are DIFFERENT fields and a
    refusal naming the wrong one sends the buyer to edit a selector they did not write. The
    buyer-facing message is deliberately not asserted: it is a function of the code through
    CODE_TABLE, so asserting both compares the table to itself.

    The feature's Examples name the ARM and the pointer is composed here, rather than the
    Examples carrying ``packages[0].format_kind`` whole. The square brackets are why: an
    Examples value lands in the pytest nodeid, and the BDD artifact readers take the FIRST
    bracketed token of a nodeid as the transport -- so a bracket inside a parametrize value
    silently misattributes every one of these scenarios to a transport named
    ``0].format_kind`` (salesagent-8qela.159 is that corruption measured on another suite).
    """
    ctx["result"].assert_wire_error("UNSUPPORTED_FEATURE", field=f"packages[0].{arm}")


@then(
    parsers.parse(
        'the buyer is refused UNSUPPORTED_FEATURE naming the "{arm}" selector '
        'for an uncovered placement of "{selected}"'
    )
)
@then(
    parsers.parse(
        'the buyer is refused UNSUPPORTED_FEATURE naming the "{arm}" selector '
        'for an uncovered publisher slice of "{selected}"'
    )
)
def then_refused_uncovered_placement(ctx: dict, arm: str, selected: str) -> None:
    """The selector refusal above, narrowed to the placement-coverage rule by its details.

    A publisher slice is refused by the same rule with the same details: the pin states the
    slice and the placement as one coverage obligation, and ``capability`` names that rule.

    ``details.capability`` names the rule that refused, which is what separates this
    refusal from an unpublished option ref or an unsatisfied param on the same arm and code.
    ``details.rejected_value`` is the declaration the package selected, compared to the
    option id the scenario names. ``core/error.json`` defines it as "the offending value the
    buyer supplied", and here that is the selection the placement does not admit.
    """
    _assert_placement_refusal(ctx, arm, "placement_uncovered", selected)


@then(
    parsers.parse(
        'the buyer is refused UNSUPPORTED_FEATURE naming the "{arm}" selector '
        'for the unresolved publisher placement "{placement_id}"'
    )
)
def then_refused_unresolved_placement(ctx: dict, arm: str, placement_id: str) -> None:
    """The selector refusal for an included placement whose effective set cannot be resolved.

    ``details.capability`` names the rule (``placement_unresolved``) and
    ``details.rejected_value`` the placement, which is what the buyer cannot be sold until
    the seller resolves the publisher's catalog for it.
    """
    _assert_placement_refusal(ctx, arm, "placement_unresolved", placement_id)


@then(
    parsers.parse(
        'the buyer is refused UNSUPPORTED_FEATURE naming the package\'s product for the uncovered publisher slice "{slice_id}"'
    )
)
def then_refused_uncovered_slice_without_selector(ctx: dict, slice_id: str) -> None:
    """The coverage refusal for a package that named NO selector, at ``packages[0].product_id``.

    Such a package "selects all formats supported by the product"
    (``media-buy/package-request.json``), so no arm is at fault and the pointer names the
    product the buyer chose. ``details.rejected_value`` is the slice left unserved -- the
    buyer supplied no value -- named by its ``publisher_domain``, as the creative-set
    refusal names it.
    """
    _assert_placement_refusal(ctx, "product_id", "placement_uncovered", slice_id)


@then(
    parsers.parse(
        "the buyer is refused UNSUPPORTED_FEATURE naming the package's product "
        'for the unresolved publisher placement "{placement_id}"'
    )
)
def then_refused_unresolved_placement_without_selector(ctx: dict, placement_id: str) -> None:
    """The unresolved-placement refusal for a package that named NO selector, at ``packages[0].product_id``.

    The same rule and rejected value as the selecting package's refusal above; only the
    pointer differs, because the buyer selected nothing and the product is what it chose.
    """
    _assert_placement_refusal(ctx, "product_id", "placement_unresolved", placement_id)


def _assert_placement_refusal(ctx: dict, arm: str, capability: str, rejected: str) -> None:
    """A placement-rule UNSUPPORTED_FEATURE at the arm, naming the rule and the rejected value."""
    ctx["result"].assert_wire_error(
        "UNSUPPORTED_FEATURE",
        field=f"packages[0].{arm}",
        details={"capability": capability, "rejected_value": rejected},
    )


@then(
    parsers.parse('the refusal names the "{capability}" rule, rejecting "{rejected}" and accepting only "{accepted}"')
)
def then_refusal_names_rule_and_sets(ctx: dict, capability: str, rejected: str, accepted: str) -> None:
    """The refusal's details: which rule refused, the value refused, and the set offered back.

    ``details.capability`` is what separates a closed-set refusal from an unsatisfied-params
    one: both are UNSUPPORTED_FEATURE at the same arm, so the code and field the preceding
    Then asserts cannot tell them apart. ``rejected_value`` and ``accepted_values`` are the
    pair ``core/error.json`` names as the canonical rejection-set keys; ``accepted_values``
    is compared whole, so a refusal offering the pinned sixteen-kind vocabulary instead of
    the product's own closed set fails.
    """
    ctx["result"].assert_wire_error(
        "UNSUPPORTED_FEATURE",
        details={"capability": capability, "rejected_value": rejected, "accepted_values": [accepted]},
    )


@then("the update is accepted")
def then_update_accepted(ctx: dict) -> None:
    """Assert the update answered on the wire and carries no error.

    Paired halves, same reasoning as the create accept: ``media_buy_id`` alone is satisfied
    by a response that answered and then reported a problem, and an absent ``errors`` key
    alone is satisfied by a non-answer.
    """
    media_buy_id = wire_field(ctx, "media_buy_id")
    assert media_buy_id == ctx["existing_media_buy"].media_buy_id, (
        f"the update answered about {media_buy_id!r}, not the buy the scenario holds"
    )
    wire_absent(ctx, "errors")


@then(parsers.parse('the buyer is refused UNSUPPORTED_FEATURE naming the "{array}" array'))
def then_refused_naming_array(ctx: dict, array: str) -> None:
    """The UPDATE path's refusal, whose pointer names which creative array the buyer sent.

    ``packages[0].<array>`` composed here rather than written in the Examples, for the
    bracket reason the sibling Then records: a bracket inside a parametrize value lands in
    the nodeid and the BDD artifact readers take the first bracketed token as the transport.
    """
    ctx["result"].assert_wire_error("UNSUPPORTED_FEATURE", field=f"packages[0].{array}")


@then("the assignment is recorded with no assignment error")
def then_assignment_recorded(ctx: dict) -> None:
    """Assert the sync answered AND the creative's assignment carries no error for the package.

    BOTH halves, and the second is the load-bearing one: lenient mode answers SUCCESS while
    recording per-package failures in ``assignment_errors``, so a scenario asserting only
    that the sync succeeded would pass on a refusal that was merely recorded instead of
    raised. Read off the WIRE entry for this creative (``creatives[]`` carries the
    per-creative ``assignment_errors`` map), not the typed payload, which has already been
    coerced to its declared type.
    """
    entry = wire_entry(ctx, "creatives", creative_id=_SYNC_CREATIVE_ID)
    assert entry.get("action") in ("created", "updated"), f"the creative was not accepted: {entry!r}"
    errors = entry.get("assignment_errors") or {}
    assert errors == {}, f"expected no assignment error, got {errors!r}"


@then("the sync succeeds and records an assignment error for that package")
def then_assignment_error_recorded(ctx: dict) -> None:
    """Assert lenient mode SOFTENED the deprecated-arm refusal: success plus a recorded error.

    The control for the canonical row above. Three things together, because any two of them
    are satisfied by the wrong behaviour: the call succeeded (not refused), the error map
    names THIS package, and the recorded reason names both format identities -- the refused
    one and what the product accepts. The reason is graded on those VALUES and not on its
    wording, the same correction made to the sibling integration test.

    ``video_1280x720`` is the refused id, and it is a CATALOG id on purpose: the scenario's
    creative claims a reference production RESOLVES, so what the record names is a
    satisfaction verdict rather than an unresolvable-reference refusal upstream of the gate.
    It also cannot satisfy the second assertion by accident, which a nearer id would --
    ``display_300x250`` is a substring of the catalog's ``display_300x250_image``.
    """
    result = ctx["result"]
    assert result.is_success, f"lenient mode should have answered success, got {result.error!r}"
    creative_results = result.payload.creatives
    assert creative_results, "sync_creatives answered with no creative results"
    package_id = ctx["existing_package"].package_id
    errors = creative_results[0].assignment_errors or {}
    assert package_id in errors, f"no assignment error recorded for {package_id!r}: {errors!r}"
    recorded = errors[package_id]
    assert "video_1280x720" in recorded, f"the record does not name the refused format: {recorded}"
    assert "display_300x250" in recorded, f"the record does not name what the product accepts: {recorded}"


@then("the buyer is refused INVALID_REQUEST")
def then_refused_invalid_request(ctx: dict) -> None:
    """Assert the SCHEMA bucket, which is the point: a malformed selector is not an
    unsupported one.

    No ``field`` asserted, and that is this refusal's own shape rather than an omission: the
    pin's conjunct is stated at the PackageRequest ROOT (over ``format_kind``, ``height``
    and ``params`` jointly), so there is no single member to point at -- pinning one would
    assert a pointer the pinned model has no basis to emit. The CODE is the obligation the
    storyboard step states, and the code is what this grades.
    """
    ctx["result"].assert_wire_error("INVALID_REQUEST")


@then(parsers.parse('the creative is stored carrying canonical kind "{kind}"'))
def then_stored_with_canonical_kind(ctx: dict, kind: str) -> None:
    """The POSITIVE half of the write gate: the projected kind reaches the column.

    Read off the stored row rather than off the response, because the column is the subject
    -- a NOT NULL constraint is satisfied by any non-null value, so what has to be shown is
    that the value is the kind the registry projects and not merely that something was
    written. The fire-probe for this change perturbs exactly that: make the projection
    return a WRONG kind and this assertion is the one that must redden.

    The sync is asserted to have SUCCEEDED first. Without that, an exception path that
    stored nothing and answered an error would make the kind assertion fail for the wrong
    reason and read as a projection bug.
    """
    stored_kind = _stored_synced_creative_column(ctx, "format_kind")
    assert stored_kind == kind, (
        f"stored format_kind={stored_kind!r}, expected {kind!r} -- the kind the buyer named, or the "
        f"one the registry projects its legacy id to; a different value is not the identity it sent"
    )


@then(parsers.parse('the creative is stored with its format_option_ref naming product option "{option}"'))
def then_stored_with_option_ref(ctx: dict, option: str) -> None:
    """The option ref the buyer sent reaches the column WHOLE, scope discriminator included.

    core/format-option-ref.json is a oneOf whose arms differ by ``scope`` (and by
    ``publisher_domain``, required on one arm and forbidden on the other), so the stored
    document is compared whole: an id kept without its scope would read back as neither arm.
    """
    stored = _stored_synced_creative_column(ctx, "format_option_ref")
    assert stored == {"scope": "product", "format_option_id": option}, (
        f"stored format_option_ref={stored!r}, expected the product-scoped ref to {option!r}"
    )


def _stored_synced_creative_column(ctx: dict, column: str) -> Any:
    """One column of the synced creative's stored row, after asserting the sync succeeded.

    The sync is asserted to have SUCCEEDED first: an exception path that stored nothing
    and answered an error would otherwise fail the column assertion for the wrong reason.
    Read off the row because the column is the subject -- a constraint is satisfied by any
    value, so what has to be shown is that the value is the one the buyer's identity names.
    """
    from tests.bdd.steps._outcome_helpers import stored_creative_column

    result = ctx["result"]
    assert result.is_success, f"the sync was expected to succeed but answered {result.error!r}"
    return stored_creative_column(ctx, _SYNC_CREATIVE_ID, column)


@then("the buyer is refused UNSUPPORTED_FEATURE and nothing is stored")
def then_refused_and_nothing_stored(ctx: dict) -> None:
    """The refusal AND the absence of the row, because the wire alone cannot show the write.

    The user's ruling is "gate at writing, not write if we can't resolve it", and a wire
    envelope cannot distinguish a seller that refused BEFORE writing from one that wrote the
    row and then answered an error. Those are different sellers: the second leaves a
    ``format_kind`` NULL behind for every later reader, which is the defect. So the state
    that must NOT have changed is asserted alongside the code -- read back through
    ``CreativeUoW``, the same repository production writes through, rather than by a raw
    query that could see a different transaction.

    BOTH halves, and each is needed. The code alone passes on a seller that stored the row
    and refused afterwards; the empty library alone passes on a seller that refused for some
    unrelated reason and never got near the write.

    THE REFUSAL IS PER-CREATIVE, NOT A WIRE ENVELOPE, and this step asserted the envelope
    first and was wrong to. ``sync_creatives`` is a BATCH tool: the pinned
    ``sync-creatives-response.json`` carries the outcome per entry in ``creatives[]``, with
    ``failed`` among the ``creative-action`` enum's members and an ``errors`` list on the
    entry, so one unservable creative in a batch of three must not fail the other two. The
    request itself therefore completes. Measured before the assertion was corrected:
    production answered ``status=completed``, "Synced 0 creatives, 1 failed",
    ``action=failed``, with ``UNSUPPORTED_FEATURE`` and ``recovery=correctable`` on the entry
    -- the right behaviour failing a demand for the wrong shape. The entry is located by
    ``creative_id`` rather than by position, so the obligation does not quietly become "and
    it was first".

    READ OFF THE TYPED PAYLOAD, not the wire dict. Indexing ``wire["creatives"][...]
    ["errors"]`` is a step re-deriving where the spec puts a field, which
    ``test_architecture_bdd_wire_discipline`` refuses by name -- and it refused this step
    when it was written that way. ``assert_wire_error`` is not the remedy here either: that
    asserts the ENVELOPE, and this is a batch tool whose request COMPLETES while one entry
    fails. ``payload_or_none`` is the sanctioned reader that keeps the value's provenance,
    and the typed model exposes ``action`` and ``errors`` as attributes, so no position is
    restated. Same primitive the UC-006 per-creative steps use.
    """
    _assert_entry_failed_and_nothing_stored(ctx, "UNSUPPORTED_FEATURE")


@then(parsers.parse("the creative is reported failed with {code:w} and nothing is stored"))
def then_reported_failed_and_nothing_stored(ctx: dict, code: str) -> None:
    """A library-only sync refused per item for its manifest, and the row not written.

    The same two halves as the unprojectable-format refusal above, for a different code:
    a canonical manifest assigned to nothing is held to the slots its kind declares
    (creative-manifests.mdx § Validation), and one that does not fill them is a per-entry
    failure of the batch, not a stored creative.
    """
    _assert_entry_failed_and_nothing_stored(ctx, code)


def _assert_entry_failed_and_nothing_stored(ctx: dict, code: str) -> None:
    """The synced creative's batch entry FAILED with exactly *code*, and no row was written.

    Read off the typed payload by ``creative_id`` and the library read back through
    ``CreativeUoW``; the reasons both halves are needed are on
    :func:`then_refused_and_nothing_stored`.
    """
    assert_sync_entry_refused_and_not_stored(ctx, _SYNC_CREATIVE_ID, code)


@then(parsers.parse("the creative is refused {code:w} naming the packages field"))
def then_creative_refused_naming_packages(ctx: dict, code: str) -> None:
    """The create path's manifest refusal: the code, the ``packages`` pointer and the creative.

    enums/error-code.json on CREATIVE_SIZE_MISMATCH: "Sellers SHOULD identify the offending
    creative" -- the pointer names the request field the buyer sent (the packages array, as
    every creative refusal on this path does) and ``details.creative_id`` names the creative.
    """
    _assert_creative_refused(ctx, code, field="packages")


@then(parsers.parse('the creative is refused {code:w} naming the "{array}" array'))
def then_creative_refused_naming_array(ctx: dict, code: str, array: str) -> None:
    """The update path's manifest refusal, pointing at the creative array the buyer sent."""
    _assert_creative_refused(ctx, code, field=f"packages[0].{array}")


@then(parsers.parse("the creative is refused {code:w}"))
def then_creative_refused(ctx: dict, code: str) -> None:
    """The sync assignment path's manifest refusal: the code and the creative, no pointer.

    No ``field`` for the reason :func:`then_refused_unsupported_feature_bare` gives: the
    assignment gate refuses a STORED creative, and which one is ``details.creative_id``.
    """
    _assert_creative_refused(ctx, code, field=None)


@then(parsers.parse('the creative is refused CREATIVE_VALUE_NOT_ALLOWED at "{path}" allowing "{allowed}"'))
def then_creative_value_not_allowed(ctx: dict, path: str, allowed: str) -> None:
    """A value outside a closed set: the code, the manifest path, and the set the buyer may pick from.

    enums/error-code.json on CREATIVE_VALUE_NOT_ALLOWED: "The seller MUST set ``error.field`` to
    the offending asset's path within the manifest ... and SHOULD include the format's
    ``allowed_values`` array in ``error.details.allowed_values``". So unlike the other creative
    codes on this path, the pointer is the manifest path rather than absent, and the set is
    graded where the entry puts it. *allowed* is comma-separated in the feature.
    """
    values = [value.strip() for value in allowed.split(",")]
    _assert_creative_refused(ctx, "CREATIVE_VALUE_NOT_ALLOWED", field=path, details={"allowed_values": values})


@then(
    parsers.parse(
        'the creatives are refused CREATIVE_VALIDATION_FAILED_GENERIC at "{field}" '
        'for leaving placement "{placement_id}" uncovered'
    )
)
@then(
    parsers.parse(
        'the creatives are refused CREATIVE_VALIDATION_FAILED_GENERIC at "{field}" '
        'for leaving the publisher slice "{placement_id}" uncovered'
    )
)
def then_creatives_leave_placement_uncovered(ctx: dict, field: str, placement_id: str) -> None:
    """The creative-set coverage refusal: the code, the request field, and which placement.

    For a multi-publisher product without placements the uncovered unit is a publisher's
    eligibility slice, and ``rejected_value`` is that slice's ``publisher_domain``.

    ``details.rejected_value`` is the included placement the set leaves unserved -- with
    several creatives there is no single offending creative, and the placement is what the
    buyer has to fill -- and ``details.product_id`` the product whose placement it is. The
    code is the pinned enum's generic creative code: no more specific one names an uncovered
    placement. *field* is the request member that carried the creatives, which differs by
    tool (``packages`` on create, the package's array on update, ``assignments`` on sync).
    """
    ctx["result"].assert_wire_error(
        "CREATIVE_VALIDATION_FAILED_GENERIC",
        field=field,
        details={"rejected_value": placement_id, "product_id": ctx["default_product"].product_id},
    )


@then("the package keeps the creatives it held and none of the uploaded creatives is stored")
def then_refused_update_changed_no_creative(ctx: dict) -> None:
    """The refused update left the package's assignments and the creative library as seeded.

    update_media_buy.mdx § Compatibility transaction boundary: "One update_media_buy request
    is one atomic mutation" and "No package, assignment, library creative, or MediaBuy field
    from the rejected update may change". The refusal reads the same on the wire whether or
    not the upload was written first, so the persisted state is read back: the package's
    assignments, each with its ``inline`` flag, equal the ones the Givens seeded, and no
    creative carries an id the update uploaded. Read through the env's own session, which is
    bound to the database the request wrote on every transport and sees only committed rows.
    """
    from src.core.database.models import Creative, CreativeAssignment

    env = ctx["env"]
    tenant_id = ctx["tenant"].tenant_id
    held = {
        (row.creative_id, row.inline)
        for row in env.query(
            CreativeAssignment,
            tenant_id=tenant_id,
            media_buy_id=ctx["existing_media_buy"].media_buy_id,
            package_id=ctx["existing_package"].package_id,
        )
    }
    assert held == ctx["seeded_assignments"], (
        f"the refused update changed the package's assignments: held {sorted(held)}, "
        f"seeded {sorted(ctx['seeded_assignments'])}"
    )
    stored = [cid for cid in ctx["uploaded_creative_ids"] if env.query(Creative, tenant_id=tenant_id, creative_id=cid)]
    assert stored == [], f"the refused update stored the creatives it uploaded: {stored}"


def _assert_creative_refused(ctx: dict, code: str, *, field: str | None, details: dict[str, Any] | None = None) -> None:
    """The wire envelope carries *code* (its pinned recovery) and names the scenario's creative.

    *details* is subset-checked against ``errors[0].details`` when a code's entry names keys of
    its own (CREATIVE_VALUE_NOT_ALLOWED's ``allowed_values``).
    """
    result = ctx["result"]
    result.assert_wire_error(code, field=field, details=details)
    details = result.wire_error_details(code)
    assert details.get("creative_id") == ctx["manifest_creative_id"], (
        f"the refusal names creative {details.get('creative_id')!r}, not {ctx['manifest_creative_id']!r}: {details}"
    )
    ctx["refused_code"] = code


def _refusal_problem(ctx: dict, path: str) -> dict[str, Any]:
    """The ``problems[]`` entry for manifest *path* in the refusal a prior Then asserted."""
    return _problem_at(ctx["result"].wire_error_details(ctx["refused_code"]), path, ctx["manifest_creative_id"])


def _entry_refusal_problem(ctx: dict, path: str) -> dict[str, Any]:
    """The ``problems[]`` entry for manifest *path* on the synced creative's FAILED batch entry.

    The library-only sync refuses per item: the request completes and the error rides the
    creative's own entry (``creatives[].errors``), not the envelope, so it is read there.
    """
    creative_id = ctx["manifest_creative_id"]
    errors = wire_entry_errors(ctx, "creatives", creative_id=creative_id)
    assert len(errors) == 1, f"expected exactly one error on {creative_id!r}'s entry, got {errors!r}"
    return _problem_at(errors[0].get("details") or {}, path, creative_id)


def _problem_at(details: dict[str, Any], path: str, subject_id: str) -> dict[str, Any]:
    """The ``problems[]`` entry at manifest *path* in *details*, about creative *subject_id*."""
    problems = {problem.get("field"): problem for problem in details.get("problems") or []}
    assert path in problems, f"the refusal names no problem at {path!r}; it names {sorted(problems)}"
    problem = problems[path]
    assert problem.get("subject_id") == subject_id, f"the problem is about another subject: {problem}"
    return dict(problem)


def _assert_states(problem: dict[str, Any], path: str, rejected: str, accepted: str) -> None:
    """*problem* states *rejected* where the comma-separated *accepted* is accepted."""
    assert problem.get("rejected_value") == rejected, f"{path}: stated {problem.get('rejected_value')!r}"
    assert problem.get("accepted_values") == [value.strip() for value in accepted.split(",")], (
        f"{path}: accepted {problem.get('accepted_values')!r}"
    )


def _assert_missing(problem: dict[str, Any], path: str, accepted: str) -> None:
    """*problem* states nothing where *accepted* is required."""
    assert "rejected_value" not in problem, f"{path} is reported as stating {problem.get('rejected_value')!r}"
    assert problem.get("accepted_values") == [accepted], f"{path}: accepted {problem.get('accepted_values')!r}"


@then(parsers.parse('the refusal names "{path}" stating "{rejected}" where "{accepted}" is accepted'))
def then_refusal_names_stated_value(ctx: dict, path: str, rejected: str, accepted: str) -> None:
    """One failed constraint, as values: what the asset states and what the declaration accepts.

    The SIZE_MISMATCH entry in enums/error-code.json: a seller "MAY include the submitted and
    accepted dimensions in buyer-safe error.details". ``accepted`` is the declaration's set
    or bound, comma-separated in the feature.
    """
    _assert_states(_refusal_problem(ctx, path), path, rejected, accepted)


@then(parsers.parse('the refusal names "{path}" missing where "{accepted}" is accepted'))
def then_refusal_names_missing_slot(ctx: dict, path: str, accepted: str) -> None:
    """Something the manifest does not supply: nothing stated, what the declaration requires accepted.

    A required slot left empty (its asset type accepted), an asset silent about a capped member
    (the cap accepted), or a rendition set lacking a required pixel ratio (that ratio accepted).
    """
    _assert_missing(_refusal_problem(ctx, path), path, accepted)


@then(parsers.parse('the entry names "{path}" stating "{rejected}" where "{accepted}" is accepted'))
def then_entry_names_stated_value(ctx: dict, path: str, rejected: str, accepted: str) -> None:
    """The per-item twin of the envelope step above, read off the creative's failed entry."""
    _assert_states(_entry_refusal_problem(ctx, path), path, rejected, accepted)


@then(parsers.parse('the entry names "{path}" missing where "{accepted}" is accepted'))
def then_entry_names_missing(ctx: dict, path: str, accepted: str) -> None:
    """The per-item twin of the envelope step above, read off the creative's failed entry."""
    _assert_missing(_entry_refusal_problem(ctx, path), path, accepted)


@then(parsers.parse('the refusal names the compatible options "{options}"'))
def then_refusal_names_compatible_options(ctx: dict, options: str) -> None:
    """An ambiguous selection names the declarations the manifest fits, the values a ref may take.

    core/creative-asset.json, ``format_option_ref``: "Required when the target product has
    multiple format_options entries sharing the same format_kind". ``accepted_values`` is the
    pin's canonical rejection-set key, and here it lists exactly the options the buyer can
    name to disambiguate.
    """
    details = ctx["result"].wire_error_details(ctx["refused_code"])
    assert details.get("accepted_values") == [option.strip() for option in options.split(",")], details


@then("the buyer is refused UNSUPPORTED_FEATURE")
def then_refused_unsupported_feature_bare(ctx: dict) -> None:
    """The ASSIGNMENT path's refusal: the code and its pinned recovery, no field.

    No ``field`` here, deliberately and not as an omission. The assignment gate refuses a
    STORED creative's identity, and ``core/error.json``'s ``field`` names a request field --
    which creative is in ``details.creative_id`` and which package in ``details.package_id``,
    both of which the raise site supplies. Asserting a pointer this path does not own would
    pin a value production has no reason to carry.
    """
    ctx["result"].assert_wire_error("UNSUPPORTED_FEATURE")


@then(parsers.parse("the buyer is refused VALIDATION_ERROR naming the {width:d}x{height:d} it accepts"))
def then_refused_legacy_not_accepted(ctx: dict, width: int, height: int) -> None:
    """The DEPRECATED lane's refusal, and the dimensions have to be in it.

    A DIFFERENT CODE from the canonical sibling, and that is production's documented
    decision rather than this scenario's preference: for a legacy format the product does
    not take at all (``FormatSatisfaction.creative_coded`` is false), the creative
    ASSIGNMENT sites raise ``AdCPValidationError`` themselves -- "violates business rules
    beyond schema validation". A format the product takes at another size gets the creative
    code instead, graded by ``the creative is refused {code}``. (A package's own
    format selector is a different site, coded UNSUPPORTED_FEATURE by
    ``media-buy/package-request.json``'s selector contract.) A row asserting UNSUPPORTED_FEATURE here would be asserting the canonical
    lane's code against the deprecated lane's refusal, which is the mistake this Then was
    written to replace. No ``field``: the site carries none, for the same reason the bare
    UNSUPPORTED_FEATURE sibling asserts none.

    THE DIMENSIONS ARE THE POINT, not the code. Before the fix the accepted set was built
    from TEMPLATE keys, so it could only ever say ``<agent>/display_image`` -- the same
    string for a product restricting to 300x250 and one restricting to nothing. Asserting
    the code alone would pass against that, since a refusal would still arrive once anything
    reddened. Asserting that the buyer is told WHICH size the product accepts is what
    distinguishes a represented restriction from an unrepresentable one.

    Graded as a substring of the accepted set rather than against a rendered literal: the
    exact spelling is ``_reference_display``'s and pinning it would grade this seller's
    formatting, where the obligation is that the dimensions reach the buyer at all.

    The array comes from ``wire_envelope_errors``, not from indexing the envelope here. Its
    own docstring names this mistake: "without this one, a step needing it wrote
    ``envelope.get('errors')`` itself and the guard flagged it, correctly." It did -- check F
    of ``test_architecture_bdd_wire_discipline`` caught the first version of this step.
    """
    ctx["result"].assert_wire_error("VALIDATION_ERROR")
    _assert_accepted_size_named(ctx, width, height)


@then(
    parsers.parse(
        'the buyer is refused UNSUPPORTED_FEATURE naming the "format_ids" selector and the {width:d}x{height:d} it accepts'
    )
)
def then_refused_legacy_selector_not_accepted(ctx: dict, width: int, height: int) -> None:
    """The deprecated lane's refusal of a PACKAGE selector: UNSUPPORTED_FEATURE, and the size.

    ``media-buy/package-request.json``'s "Format selector contract (normative)": "A single
    resolved route that does not satisfy the product is rejected with
    ``UNSUPPORTED_FEATURE``", and "a legacy ``format_id`` that cannot be normalized ...
    MUST be rejected with ``UNSUPPORTED_FEATURE``". A legacy-only product is still a product
    the route has to satisfy, so its selector refusal carries this code and the
    ``format_ids`` pointer, where the creative assignment sites above keep VALIDATION_ERROR.
    The accepted size is graded for the reason :func:`then_refused_legacy_not_accepted` gives.
    """
    ctx["result"].assert_wire_error("UNSUPPORTED_FEATURE", field="packages[0].format_ids")
    _assert_accepted_size_named(ctx, width, height)


def _assert_accepted_size_named(ctx: dict, width: int, height: int) -> None:
    """Assert the refusal's ``details.accepted_values`` tells the buyer the size accepted."""
    accepted = str(wire_envelope_errors(ctx)[0].get("details", {}).get("accepted_values"))
    for value in (width, height):
        assert str(value) in accepted, (
            f"the refusal does not tell the buyer the product accepts {width}x{height}; "
            f"accepted_values={accepted!r}. A template-key accepted set cannot carry a size."
        )


@then("the buyer is refused CONFLICTING_SELECTORS naming the package")
def then_refused_conflicting_selectors(ctx: dict) -> None:
    """Assert the equivalence refusal: the code, its pinned recovery, and the PACKAGE pointer.

    The pointer is ``packages[0]`` and not ``packages[0].<arm>``, which is this refusal's
    own shape rather than a looser assertion: no single arm is at fault, the set of them is,
    and ``enums/error-code.json``'s entry for the code says so -- "``error.field`` SHOULD
    point at the conflicting package". Asserting an arm here would pin the pointer of the
    refusal this one is deliberately NOT: ``option_plus_unprojectable_legacy`` is
    UNSUPPORTED_FEATURE naming ``format_ids``, and the two must not converge.

    ``assert_wire_error`` defaults ``recovery`` to the pinned enum's classification, which
    for this code is ``correctable`` -- the buyer's lever is to send one route, or to make
    the routes agree.

    The DETAILS are asserted too, and they are what stops this passing on a seller that
    answers the code without having compared anything: the pin asks for "the supplied
    selector routes and their normalized canonical summaries", and a route's summary is the
    option SET it selected, because sets are what step (2) compares ("require the selected
    option sets to match"). So, per route -- a route is an arm, and an array arm is one route
    selecting a set:

    - every arm the buyer sent is summarized ONCE, and no other;
    - every summary states its selected set as a list, empty when it selected nothing;
    - on a product that publishes ``format_options[]``, every option named is one it
      publishes (a legacy-only product's selection is the SDK's projection of its
      deprecated arm, whose option ids the product never published);
    - the ``format_option_refs`` arm selected exactly the options its refs name, which is
      the request's own statement and not this seller's derivation: "Each reference
      matches one target product ``format_options[]`` entry";
    - the arms' sets differ, because that disagreement IS the refusal. A summary that
      names one entry of a set two options narrow shows two routes agreeing beside a
      refusal that says they differ.

    WHICH set any other route selected is the scenario's to state, through
    :func:`then_conflict_route_selects`.
    """
    routes = _conflict_route_summaries(ctx)
    request = _SELECTORS[ctx["selector"]]
    sent = [arm for arm in ("format_option_refs", "format_kind", "format_ids") if arm in request]
    assert sorted(route.get("field") for route in routes) == sorted(sent), (
        f"the refusal summarizes routes {[r.get('field') for r in routes]}, but the buyer sent one route per arm {sent}"
    )
    assert all(isinstance(route.get("selected_option"), list) for route in routes), (
        f"every route summary must state the option set it selected (empty when none); got {routes!r}"
    )
    arms = {route["field"]: set(route["selected_option"]) for route in routes}
    published = {option["format_option_id"] for option in ctx["default_product"].format_options or ()}
    named = set().union(*arms.values())
    if published:
        assert named <= published, (
            f"the refusal names options {sorted(named - published)} the product does not publish ({sorted(published)})"
        )
    if "format_option_refs" in request:
        referenced = {ref["format_option_id"] for ref in request["format_option_refs"]}
        assert arms["format_option_refs"] == referenced, (
            f"the format_option_refs route names {sorted(referenced)} but its summary selected "
            f"{sorted(arms['format_option_refs'])}"
        )
    summarized = {arm: sorted(selected) for arm, selected in arms.items()}
    assert len({frozenset(selected) for selected in arms.values()}) > 1, (
        f"every route's summary selected the same set, yet the routes were refused as differing -- got {summarized!r}"
    )


def _conflict_route_summaries(ctx: dict) -> list[dict[str, Any]]:
    """The wire ``CONFLICTING_SELECTORS`` refusal at the package, and its per-route summaries.

    ``wire_error_object`` and not the envelope dict: the envelope WRAPS ``errors[]`` and
    ``details`` lives on the error object inside it, which this reader resolves through the
    one locator the assertion also uses.
    """
    result = ctx["result"]
    result.assert_wire_error("CONFLICTING_SELECTORS", field="packages[0]")
    error_object = result.wire_error_object() or {}
    return list(((error_object.get("details") or {}).get("selector_routes")) or [])


@then(parsers.parse('the conflict summarizes the "{arm}" route as selecting "{options}"'))
def then_conflict_route_selects(ctx: dict, arm: str, options: str) -> None:
    """Assert the summary of route *arm* reports exactly the option SET *options* (comma-separated).

    ``enums/error-code.json`` asks ``CONFLICTING_SELECTORS`` details to "identify the supplied
    selector routes and their normalized canonical summaries", and step (2) of
    ``media-buy/package-request.json`` compares "the selected option sets", so a summary
    carries the set its route selected and this compares it as a set.
    """
    _assert_conflict_route_selected(ctx, arm, set(options.split(",")))


@then(parsers.parse('the conflict summarizes the "{arm}" route as selecting nothing'))
def then_conflict_route_selects_nothing(ctx: dict, arm: str) -> None:
    """Assert the summary of route *arm* reports an EMPTY selected set: it resolved and selected no option."""
    _assert_conflict_route_selected(ctx, arm, set())


def _assert_conflict_route_selected(ctx: dict, arm: str, expected: set[str]) -> None:
    """The one summary for *arm* names *expected* as its selected option set."""
    summaries = [route for route in _conflict_route_summaries(ctx) if route.get("field") == arm]
    assert len(summaries) == 1, f"expected one summary for the {arm!r} route, got {summaries!r}"
    selected = summaries[0].get("selected_option")
    assert isinstance(selected, list) and set(selected) == expected, (
        f"the {arm!r} route is summarized as selecting {selected!r}, expected the set {sorted(expected)!r}"
    )


@then(parsers.parse('the package snapshots the "{option_id}" declaration fixed at {width:d}x{height:d}'))
def then_package_snapshots_declaration(ctx: dict, option_id: str, width: int, height: int) -> None:
    """Assert the accepted package carries the SELECTED declaration as a wire snapshot.

    Read off the serialized body: ``core/package.json`` declares ``formats_to_provide`` as
    an array of ``PackageFormatSnapshot``, whose product-format fields "remain flattened at
    the root", so the kind, the option id and the params are read where the pin puts them.

    The PARAMS are asserted and not only the option id, because the params are what stage
    7's adapters read off the snapshot -- an entry naming the right option with the wrong
    dimensions is the defect a snapshot exists to prevent. ``product_id`` is asserted
    because the pin pairs it with the snapshot ("Product whose effective selected format
    was snapshotted") and because it is the binding that makes the record readable without
    the live product.
    """
    package = wire_entry(ctx, "packages", index=0)
    snapshots = package.get("formats_to_provide")
    assert isinstance(snapshots, list) and len(snapshots) == 1, (
        f"expected exactly one package format snapshot on the wire, got {snapshots!r}"
    )
    snapshot = snapshots[0]
    assert snapshot.get("format_option_id") == option_id, (
        f"the snapshot names option {snapshot.get('format_option_id')!r}, not the selected {option_id!r}"
    )
    assert snapshot.get("format_kind") == "image", f"the snapshot names kind {snapshot.get('format_kind')!r}"
    assert snapshot.get("product_id") == ctx["default_product"].product_id, (
        f"the snapshot binds product {snapshot.get('product_id')!r}, not the one the selector was graded against"
    )
    params = snapshot.get("params") or {}
    assert (params.get("width"), params.get("height")) == (width, height), (
        f"the snapshot's dimensions are {params.get('width')}x{params.get('height')}, expected {width}x{height}"
    )
    ctx["wire_snapshot"] = snapshot


@then("the stored package snapshot names the same declaration")
def then_stored_snapshot_matches(ctx: dict) -> None:
    """Assert the PERSISTED row carries the snapshot the response reported.

    THE HALF A RESPONSE CANNOT PROVIDE. ``core/package.json`` requires a package to "retain
    and return this checklist", and core/package-format-snapshot.json adds "never mutate
    this snapshot in place" -- so a seller that assembled the snapshot for the reply and
    stored nothing would satisfy the sibling Then and then answer every later read from the
    live product, which is the substitution the pin names ("the live Product is never
    substituted for the package snapshot").

    Compared on the three fields that identify the selection rather than on the whole
    document: the stored shape is the persistence edge's serialization (nulls included,
    defaults materialized) and the wire's is ``to_wire``'s, so a whole-document equality
    would be asserting that two serializers agree about absent optionals.
    """
    wire_snapshot = ctx["wire_snapshot"]
    package_id = wire_entry(ctx, "packages", index=0).get("package_id")
    assert package_id, "the response package carries no package_id, so the stored row cannot be read back"
    stored = stored_package_config(ctx, wire_field(ctx, "media_buy_id"), str(package_id))
    snapshots = stored.get("formats_to_provide")
    assert isinstance(snapshots, list) and len(snapshots) == 1, (
        f"expected exactly one persisted package format snapshot, got {snapshots!r}"
    )
    snapshot = snapshots[0]
    stored_identity = (snapshot.get("format_kind"), snapshot.get("format_option_id"), snapshot.get("product_id"))
    wire_identity = (
        wire_snapshot.get("format_kind"),
        wire_snapshot.get("format_option_id"),
        wire_snapshot.get("product_id"),
    )
    assert stored_identity == wire_identity, (
        f"the stored snapshot names {stored_identity!r} and the response reported {wire_identity!r}"
    )
    stored_params = snapshot.get("params") or {}
    wire_params = wire_snapshot.get("params") or {}
    assert (stored_params.get("width"), stored_params.get("height")) == (
        wire_params.get("width"),
        wire_params.get("height"),
    ), (
        f"the stored snapshot's dimensions are {stored_params.get('width')}x{stored_params.get('height')} "
        f"and the response reported {wire_params.get('width')}x{wire_params.get('height')}"
    )


@then(parsers.parse('the package\'s deprecated format_ids_to_provide names "{legacy_id}"'))
def then_legacy_formats_to_provide(ctx: dict, legacy_id: str) -> None:
    """Assert the deprecated projection was DERIVED from the snapshot, not echoed back.

    The buyer's request carries no ``format_ids`` at all in this scenario, so *legacy_id*
    can only have reached the response through the selected declaration's own
    ``v1_format_ref`` -- which is the one legal v2 -> v1 direction
    (``registries/v1-canonical-mapping.json``) and the one source
    ``format_resolver.snapshot_legacy_arm`` reads.
    """
    package = wire_entry(ctx, "packages", index=0)
    assert package.get("format_ids") is None, (
        "this scenario only grades a DERIVATION if the buyer sent no legacy arm, and the "
        f"response echoes format_ids={package.get('format_ids')!r}"
    )
    provided = package.get("format_ids_to_provide")
    assert isinstance(provided, list), f"expected a deprecated format_ids_to_provide array, got {provided!r}"
    assert [entry.get("id") for entry in provided] == [legacy_id], (
        f"format_ids_to_provide names {[e.get('id') for e in provided]}, expected exactly [{legacy_id!r}]"
    )


@then("the package carries no deprecated format_ids_to_provide")
def then_no_legacy_formats_to_provide(ctx: dict) -> None:
    """Assert the projection is ABSENT when the declaration asserts no legacy reference.

    Absent and not empty: ``core/package.json`` types the field as an array of format
    references with no empty-array meaning assigned, and an invented reference is what
    ``registries/v1-canonical-mapping.json`` forbids ("SDKs MUST NOT synthesize a v1
    ``format_id`` from the registry by inverting structural matches ... SDKs that synthesize
    unilaterally produce inter-SDK divergence on structurally-equal values").

    Informative only beside this scenario's first Then, which asserts the snapshot IS
    present on the same response -- an absence assertion alone passes under any perturbation
    that drops the package entirely.

    Asserted on the ENTRY DICT and not through ``wire_absent``, which is deliberate: its
    dotted-path resolver walks objects only, so ``"packages.0.format_ids_to_provide"`` hits
    the list, reads as WIRE_MISSING and would pass whatever the package carried. The key's
    presence on the entry is the real claim, and ``wire_entry`` has already proved the
    package is there.
    """
    package = wire_entry(ctx, "packages", index=0)
    assert "format_ids_to_provide" not in package, (
        "the declaration asserts no v1_format_ref, so there is nothing to project -- yet the "
        f"package carries format_ids_to_provide={package['format_ids_to_provide']!r}"
    )


@then("the buyer is refused UNSUPPORTED_FEATURE naming the packages field")
def then_refused_naming_packages(ctx: dict) -> None:
    """The creative-side refusal, which names ``packages`` rather than one selector arm.

    A creative is not a selector: the buyer sent ``creative_ids``, and WHICH creative is in
    ``details.creative_id`` rather than in the pointer, because the pointer names a request
    FIELD and the request field here is the packages array. The same pointer the sibling
    creative refusals on this path already use (``PACKAGES_FIELD``).
    """
    ctx["result"].assert_wire_error("UNSUPPORTED_FEATURE", field="packages")


# ── The MANUAL-APPROVAL path ────────────────────────────────────────
#
# Everything above dispatches on the AUTO path. These four grade the same satisfaction
# question, and the same snapshot obligation, with the tenant's human_review_required
# column flipped -- which production answers differently, because the manual-approval
# branch returns 190 lines before the selector gate. See the feature's own section comment
# for the measurement and the three beads these are the instrument for.
#
# The Given "the tenant requires manual approval" is the SHARED generic step
# (steps/generic/given_media_buy.py): it flips the tenant column, configures the env's
# adapter mocks' manual_approval_operations (production needs both), and writes the
# AdapterConfig row so the Docker-hosted adapter reads the same state over e2e_rest. Not
# re-implemented here -- one spelling of "this tenant reviews by hand".


@given("the Buyer Agent sent the create_media_buy request")
def given_create_already_sent(ctx: dict) -> None:
    """Dispatch the create as a GIVEN, so the approval scenario's When is the approval.

    The same shared When every other scenario in this feature uses, reached from the Given
    side because this scenario's subject is what happens AFTER the buy is parked. A
    scenario whose When is the create cannot also have the approval as its When.
    """
    from tests.bdd.steps.domain.uc002_create_media_buy import when_send_create_media_buy

    when_send_create_media_buy(ctx)


@given(parsers.parse("the seller then republishes that option fixed at {width:d}x{height:d}"))
def given_product_republished(ctx: dict, width: int, height: int) -> None:
    """Move the LIVE product's declaration after the buy was parked, keeping the option id.

    THE ONE KNOB THIS SCENARIO TURNS, and the reason it can grade anything: with the
    product unchanged, "execute the recorded snapshot" and "execute the live product" give
    the same answer and the scenario is satisfied by either. Editing the params makes the
    two answers differ -- 300x250 against 728x90 -- so the assertion separates them.

    The ``format_option_id`` is deliberately NOT changed. This is the same published option
    the buyer selected, republished with different dimensions, which is the case
    core/package-format-snapshot.json's immutability rule names ("changing selected format
    ... requires a replacement package_id; never mutate this snapshot in place"). Changing
    the id instead would make it a different option and the buy's selector simply
    unresolvable, which is a different question.
    """
    product = ctx["default_product"]
    product.format_options = [{**dict(_DECLARATIONS["fixed_300x250"][0]), "params": {"width": width, "height": height}}]
    ctx["env"]._commit_factory_data()


@when("the publisher approves the parked buy")
def when_publisher_approves(ctx: dict) -> None:
    """Run the real approval execution, the way an admin route runs it.

    ``run_approval`` is the ONE spelling of this call (``tests/helpers/media_buy_approval``),
    shared with the three integration modules that drive it directly, so a BDD scenario and
    an integration test cannot drift on its keyword arguments. Nothing is patched out: the
    callee, both units of work and every commit run for real, and the ad-server boundary is
    the env's adapter mock -- which is what makes the ask observable.
    """
    from tests.helpers.media_buy_approval import run_approval

    buy = parked_media_buy(ctx)
    ctx["approval_result"] = run_approval(buy.media_buy_id, ctx["tenant"].tenant_id)


@then(parsers.parse('the parked package snapshots the "{option_id}" declaration fixed at {width:d}x{height:d}'))
def then_parked_package_snapshots(ctx: dict, option_id: str, width: int, height: int) -> None:
    """Assert the PARKED buy's persisted package records the declaration its selector chose.

    The stored row and not the reply, and that is forced: the submitted envelope carries no
    media_buy_id, so there is nothing on the wire to read a package off. ``parked_media_buy``
    locates the buy by tenant through the repository for the same reason.

    Asserted on the same three identity fields plus the params as the auto-path sibling, so
    the two paths are held to one obligation rather than two.
    """
    from src.core.database.repositories.uow import MediaBuyUoW

    buy = parked_media_buy(ctx)
    with MediaBuyUoW(ctx["tenant"].tenant_id) as uow:
        assert uow.media_buys is not None
        packages = uow.media_buys.get_packages(buy.media_buy_id)
        configs = [dict(p.package_config or {}) for p in packages]
    assert len(configs) == 1, f"expected one persisted package on the parked buy, got {len(configs)}"
    snapshots = configs[0].get("formats_to_provide")
    assert isinstance(snapshots, list) and len(snapshots) == 1, (
        f"the parked buy's package records formats_to_provide={snapshots!r}; "
        f"core/package.json requires the selected declaration to be retained, and the package "
        f"carries only {sorted(configs[0])}"
    )
    snapshot = snapshots[0]
    assert snapshot.get("format_option_id") == option_id, (
        f"the recorded snapshot names option {snapshot.get('format_option_id')!r}, not {option_id!r}"
    )
    assert snapshot.get("product_id") == ctx["default_product"].product_id, (
        f"the recorded snapshot binds product {snapshot.get('product_id')!r}"
    )
    params = snapshot.get("params") or {}
    assert (params.get("width"), params.get("height")) == (width, height), (
        f"the recorded snapshot's dimensions are {params.get('width')}x{params.get('height')}, "
        f"expected {width}x{height}"
    )


@then(parsers.parse("the ad server is asked to traffic a {width:d}x{height:d} package"))
def then_adapter_trafficks_dimensions(ctx: dict, width: int, height: int) -> None:
    """Assert what the CREATE handed the ad server, read off the adapter boundary.

    The create-path sibling of :func:`then_adapter_asked_for_dimensions`, separate rather
    than shared because the two read a different call: that one grades an APPROVAL replay
    and asserts an approval ran first, while this one grades the create that the When
    dispatched. A shared step would have to branch on which ran, which is the silent-env
    shape the BDD guards forbid.

    IN-PROCESS ONLY, and NOT for the reason the approval sibling appears to share. That one
    IS graded on e2e_rest and non-vacuously -- its row went PASSED, FAILED, PASSED across
    three in-network runs -- so "the adapter is unobservable on a live transport" would be
    false. The real difference is WHICH PROCESS RUNS THE BEHAVIOUR. ``when_publisher_approves``
    calls ``run_approval`` directly in the TEST process on every transport (that helper has no
    e2e branch at all), so on e2e_rest only the CREATE crosses the wire and the approval it
    grades is in-process, populating this same mock for real.

    This step grades the create itself, which on e2e_rest executes inside the container
    against the Docker mock ad server. There is no in-process call to read, and no
    server-written record of what the ad server was asked to traffic: ``package_config``
    holds the package's creative CONTRACT (``formats_to_provide`` /
    ``format_ids_to_provide``, which for a legacy-only product is the BUYER'S request arm),
    and the create path's audit records carry budget and counts and no dimensions. Reading
    either would grade the ask rather than the resolution. ``salesagent-6k1g0`` is the record
    that would lift these scenarios to four transports.
    """
    from tests.bdd.steps._outcome_helpers import is_e2e

    assert not is_e2e(ctx), (
        "this step reads the in-process adapter mock, and on a live transport the create runs "
        "in the container where that mock is never populated -- unlike the approval sibling, "
        "whose When runs in-process on every transport. A scenario using this step must not be "
        "routed to a live transport until salesagent-6k1g0 gives the server a record of what "
        "it asked the ad server to traffic."
    )
    adapter = ctx["env"].mock["adapter"].return_value
    calls = adapter.create_media_buy.call_args_list
    assert calls, "the create never reached the ad server, so there is nothing to grade"
    media_packages = calls[-1].kwargs.get("packages") or calls[-1].args[1]
    dimensions = [(f.width, f.height) for pkg in media_packages for f in (pkg.format_ids or [])]
    assert (width, height) in dimensions, (
        f"the ad server was asked to traffic {dimensions!r}, which does not include the "
        f"{width}x{height} this scenario requires"
    )


def assert_approval_executed(ctx: dict) -> None:
    """The approval the When ran executed the order, or the failure it reported is named."""
    from src.core.tools.media_buy_create import ApprovalOutcome

    approval = ctx.get("approval_result")
    assert approval is not None, "no approval was run — the When must dispatch it before this Then"
    assert approval.outcome is ApprovalOutcome.EXECUTED, (
        f"the approval did not execute the order: outcome={approval.outcome!r}, error={approval.error_msg!r}"
    )


def _approval_media_packages(ctx: dict) -> list[Any]:
    """The MediaPackage list the APPROVAL handed the ad server, off the adapter mock.

    ``_execute_adapter_media_buy_creation`` resolves ``get_adapter`` as a module global in
    ``media_buy_create``, which is the name this env patches, and calls
    ``adapter.create_media_buy(request, packages, ...)`` positionally.
    """
    approval = ctx.get("approval_result")
    assert approval is not None, "no approval was run — the When must dispatch it before this Then"
    calls = ctx["env"].mock["adapter"].return_value.create_media_buy.call_args_list
    assert calls, (
        f"the approval never reached the ad server, so there is nothing to grade "
        f"(approval outcome={getattr(approval, 'outcome', approval)!r})"
    )
    return list(calls[-1].kwargs.get("packages") or calls[-1].args[1])


@then("the approval executes the order")
def then_approval_executes(ctx: dict) -> None:
    """The publisher's approval placed the order rather than failing it."""
    assert_approval_executed(ctx)


@then(parsers.parse('the ad server is asked for exactly the format "{format_id}"'))
def then_adapter_asked_for_format(ctx: dict, format_id: str) -> None:
    """The legacy format ids the approval handed the ad server are exactly *format_id*.

    A package that recorded no snapshot is placed on the product's CONTRACT, whose legacy
    arm is derived from the declarations' ``v1_format_ref`` (canonical-formats.mdx producer
    rules: compatibility ``format_ids`` "MUST be derived from them"). The id is the axis
    that separates the contract from the product's own stored columns in these fixtures.
    """
    asked = sorted(f.id for pkg in _approval_media_packages(ctx) for f in (pkg.format_ids or []))
    assert asked == [format_id], f"the ad server was asked for formats {asked!r}, expected exactly [{format_id!r}]"


@then(parsers.parse("the ad server is asked for a {width:d}x{height:d} package"))
def then_adapter_asked_for_dimensions(ctx: dict, width: int, height: int) -> None:
    """Assert what the APPROVAL handed the ad server, read off the adapter boundary.

    An approval's product IS the ad-server call, so that is the observable rather than a
    response field. ``_execute_adapter_media_buy_creation`` resolves ``get_adapter`` as a
    module global in ``media_buy_create``, which is the name this env patches, so the
    MediaPackage list the approval reconstructed is recorded on the mock.

    Graded on the DIMENSIONS and not on the format id: the id is unchanged by the Given
    that republishes the option, so an id comparison would pass whichever declaration the
    approval read. The dimensions are the axis the two answers differ on.
    """
    dimensions = [(f.width, f.height) for pkg in _approval_media_packages(ctx) for f in (pkg.format_ids or [])]
    assert (width, height) in dimensions, (
        f"the ad server was asked for {dimensions!r}, which does not include the "
        f"{width}x{height} the parked buy recorded — the approval read the live product, "
        f"not the package snapshot"
    )


@then("the buy is parked for a human decision")
def then_buy_is_parked(ctx: dict) -> None:
    """Assert the SUBMITTED task envelope, and that a buy was actually parked.

    Its own step rather than the generic "the approval path should be manual", and the
    difference is the obligation, not the spelling: that step accepts ``completed`` and
    ``active`` as well, because the Docker-hosted mock adapter auto-approves over e2e. This
    scenario's whole claim is that the buy is PARKED -- it is the accept half of the pair
    whose refuse half asserts a refusal -- so a status that means "already executed" must
    not satisfy it.

    BOTH halves, for the reason the sibling accept steps in this module record: the
    envelope alone is satisfied by a seller that answered ``submitted`` and persisted
    nothing, and a persisted row alone says nothing about what the buyer was told.
    ``parked_media_buy`` asserts there is exactly one buy for the tenant, which is also
    what the later approval steps address.
    """
    payload = ctx["result"].payload
    status = str(getattr(payload, "status", None) or "")
    assert status.endswith("submitted"), f"expected the submitted task envelope for a parked buy, got status={status!r}"
    buy = parked_media_buy(ctx)
    assert str(buy.status).endswith("pending_approval"), (
        f"the buy was reported submitted but persisted status={buy.status!r}, "
        "so nothing is actually waiting for an approver"
    )


@then(parsers.parse('the parked package\'s deprecated format_ids_to_provide names "{legacy_id}"'))
def then_parked_legacy_formats_to_provide(ctx: dict, legacy_id: str) -> None:
    """Assert the PARKED buy's deprecated projection is derived, not echoed, like the auto path's.

    The buyer sent only ``format_option_refs``, so *legacy_id* can have reached the record
    only through the selected declaration's own ``v1_format_ref`` -- the one legal v2 -> v1
    direction (``registries/v1-canonical-mapping.json``). Before the manual branch recorded
    a snapshot there was nothing to derive from and the field was absent, which
    ``core/package.json`` says a buyer must not read as full coverage
    ("Absence means legacy readiness was not reported").

    The auto-path sibling asserts the same thing off the WIRE; this one reads the stored
    row, because a parked buy's envelope carries no package at all.
    """
    from src.core.database.repositories.uow import MediaBuyUoW

    buy = parked_media_buy(ctx)
    with MediaBuyUoW(ctx["tenant"].tenant_id) as uow:
        assert uow.media_buys is not None
        configs = [dict(p.package_config or {}) for p in uow.media_buys.get_packages(buy.media_buy_id)]
    assert len(configs) == 1, f"expected one persisted package on the parked buy, got {len(configs)}"
    provided = configs[0].get("format_ids_to_provide")
    assert isinstance(provided, list), (
        f"the parked buy records format_ids_to_provide={provided!r}; the selected declaration "
        f"asserts a v1_format_ref, so the projection has a source"
    )
    assert [entry.get("id") for entry in provided] == [legacy_id], (
        f"format_ids_to_provide names {[e.get('id') for e in provided]}, expected exactly [{legacy_id!r}]"
    )
