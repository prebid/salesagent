"""GAM mappings for canonical AdCP creative formats."""

import pytest

from src.adapters.gam.formats import (
    GAM_CANONICAL_FORMAT_IDS,
    build_gam_creative_placeholder,
    gam_format_support,
    gam_format_type,
    gam_supported_format_models,
)
from src.admin.blueprints.inventory_profiles import (
    _native_formats_for_capabilities,
    _video_formats_for_capabilities,
)
from src.services.gam_product_config_service import GAMProductConfigService


def test_gam_support_matrix_uses_canonical_format_ids():
    assert "display_image" in GAM_CANONICAL_FORMAT_IDS
    assert "display_html" in GAM_CANONICAL_FORMAT_IDS
    assert "display_js" in GAM_CANONICAL_FORMAT_IDS
    assert "native_standard" in GAM_CANONICAL_FORMAT_IDS
    assert "product_carousel_display" in GAM_CANONICAL_FORMAT_IDS
    assert "image_slideshow_5s_each" in GAM_CANONICAL_FORMAT_IDS
    assert "audio_30s" not in GAM_CANONICAL_FORMAT_IDS


def test_gam_supported_format_models_are_reference_agent_formats():
    formats = gam_supported_format_models()
    by_id = {fmt.format_id.id: fmt for fmt in formats}

    assert set(by_id) == set(GAM_CANONICAL_FORMAT_IDS)
    assert "supports_safe_frame" not in by_id["display_html"].platform_config["gam"]
    assert by_id["native_standard"].platform_config["gam"]["supports_fluid"] is True


def test_display_format_builds_pixel_placeholder_from_canonical_dimensions():
    placeholder = build_gam_creative_placeholder(
        {
            "agent_url": "https://creative.adcontextprotocol.org",
            "id": "display_image",
            "width": 300,
            "height": 250,
        }
    )

    assert placeholder == {
        "expectedCreativeCount": 1,
        "size": {"width": 300, "height": 250},
        "creativeSizeType": "PIXEL",
    }


def test_native_format_builds_native_fluid_placeholder_with_template_id():
    placeholder = build_gam_creative_placeholder(
        {"agent_url": "https://creative.adcontextprotocol.org", "id": "native_standard"},
        impl_config={"native_template_id": "123456"},
    )

    assert placeholder == {
        "expectedCreativeCount": 1,
        "creativeTemplateId": 123456,
        "creativeSizeType": "NATIVE",
        "size": {"width": 1, "height": 1, "isAspectRatio": False},
    }


def test_template_backed_carousel_uses_canonical_id_and_gam_template():
    placeholder = build_gam_creative_placeholder(
        {"agent_url": "https://creative.adcontextprotocol.org", "id": "product_carousel_display"},
        impl_config={"creative_template_ids": {"product_carousel_display": "789"}},
    )

    assert placeholder["creativeTemplateId"] == 789
    assert placeholder["size"] == {"width": 1, "height": 1}
    assert placeholder["creativeSizeType"] == "PIXEL"


def test_audio_format_is_not_supported_as_standalone_gam_placeholder():
    with pytest.raises(ValueError, match="Audio format 'audio_30s' is not supported"):
        build_gam_creative_placeholder({"agent_url": "https://creative.adcontextprotocol.org", "id": "audio_30s"})


def test_gam_product_config_service_derives_placeholders_from_canonical_dicts():
    placeholders = GAMProductConfigService._generate_creative_placeholders(
        [
            {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_html",
                "width": 728,
                "height": 90,
            },
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "native_standard"},
        ]
    )

    assert placeholders == [
        {"width": 728, "height": 90, "expected_creative_count": 1, "is_native": False},
        {"width": 1, "height": 1, "expected_creative_count": 1, "is_native": True},
    ]


def test_inventory_profile_capabilities_map_native_and_olv_to_canonical_formats():
    native = _native_formats_for_capabilities({"adcp_capabilities": {"slot_kind": "native"}})
    video = _video_formats_for_capabilities({"adcp_capabilities": {"slot_kind": "olv"}}, (640, 480))

    assert native == [{"agent_url": "https://creative.adcontextprotocol.org", "id": "native_standard"}]
    assert {fmt["id"] for fmt in video} == {"video_standard", "video_vast"}
    assert all(fmt["width"] == 640 and fmt["height"] == 480 for fmt in video)


def test_gam_format_type_uses_support_matrix_before_heuristics():
    assert gam_format_type("product_carousel_display") == "display"
    assert gam_format_type("native_standard") == "native"
    assert gam_format_type("audio_30s") == "audio"
    assert gam_format_support("display_js").creative_types == ("ThirdPartyCreative",)
