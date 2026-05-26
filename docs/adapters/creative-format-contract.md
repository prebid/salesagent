# Creative Format Contract

Adapters expose and consume canonical AdCP creative formats. Adapter-specific
creative names, ad-server template IDs, and inventory slot behaviors must map
back to those canonical formats before they reach buyers.

## Rules

- Products and inventory profiles should store canonical format IDs such as
  `display_image`, `display_html`, `display_js`, `video_vast`, `audio_vast`,
  `product_carousel_display`, and `image_slideshow_5s_each`.
- Adapter-local names are implementation details. For example, Broadstreet
  "cube" maps to a canonical carousel/slideshow display format; it should not
  be advertised as a buyer-facing format ID.
- Hosted media and tag-based media are different canonical formats. Hosted audio
  remains `audio_15s` / `audio_30s` / `audio_60s`; buyer-supplied VAST or DAAST
  audio tags are `audio_vast`.
- Slot and tag capabilities are not creative formats unless the creative payload
  changes. GAM SafeFrame, fluid slots, and responsive slot behavior describe the
  publisher tag/inventory surface. They should be represented in inventory or
  property metadata, not as duplicate creative format variants.
- If an adapter cannot traffic a selected canonical format end to end, fail
  before the ad-server create call with a clear validation error.

## Current Adapter Mapping

| Adapter | Canonical creative formats | Notes |
|---|---|---|
| GAM | Display image/HTML/JS, video VAST, native, rich display templates | SafeFrame and fluid/responsive behavior belong to slot/tag capability metadata. |
| SpringServe | `audio_vast`, video VAST, hosted video | `demand_class=tag` requires exactly one assigned VAST creative; the adapter injects its URL as `vast_endpoint_url` before creating the Demand Tag. Hosted audio is intentionally not exposed until SpringServe hosted audio upload is proven live. |
| FreeWheel | Video VAST | VAST assets create FreeWheel creative resources with renditions and can be associated through creative instances. Audio is not advertised until a live FreeWheel audio trafficking path is proven. |
| Broadstreet | Canonical display/rich-display formats | Broadstreet-specific names such as "cube" map to canonical carousel/slideshow display formats. |

## Test Checklist

- Format discovery returns canonical IDs only.
- Product and inventory-profile UI can select the canonical IDs.
- `create_media_buy` rejects unsupported creative/package combinations before
  calling the adapter.
- Live smoke tests cover the ad-server payload shape when credentials and scope
  are available.
