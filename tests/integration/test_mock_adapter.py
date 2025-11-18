#!/usr/bin/env python3
"""Mock response showing what the GAM line item viewer API returns."""

import json

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# This is what the API endpoint /api/tenant/{tenant_id}/gam/line-item/7047822666 would return
mock_response = {
    "line_item": {
        "id": 7047822666,
        "name": "Q1_2025_Sports_Premium_Video",
        "orderId": 2871234567,
        "status": "DELIVERING",
        "lineItemType": "STANDARD",
        "priority": 8,
        "startDateTime": {
            "date": {"year": 2025, "month": 1, "day": 1},
            "hour": 0,
            "minute": 0,
            "second": 0,
            "timeZoneId": "America/New_York",
        },
        "endDateTime": {
            "date": {"year": 2025, "month": 3, "day": 31},
            "hour": 23,
            "minute": 59,
            "second": 59,
            "timeZoneId": "America/New_York",
        },
        "costType": "CPM",
        "costPerUnit": {"currencyCode": "USD", "microAmount": 15000000},  # $15 CPM
        "unitsBought": 1000000,  # 1M impressions
        "deliveryRateType": "EVENLY",
        "targeting": {
            "geoTargeting": {
                "targetedLocations": [
                    {"id": 2840, "type": "Country", "displayName": "United States"},
                    {"id": 2124, "type": "Country", "displayName": "Canada"},
                ],
                "excludedLocations": [],
            },
            "technologyTargeting": {
                "deviceCategoryTargeting": {
                    "targetedDeviceCategories": [{"id": 30000, "name": "Desktop"}, {"id": 30001, "name": "Tablet"}]
                }
            },
            "customTargeting": {
                "logicalOperator": "AND",
                "children": [
                    {
                        "keyId": 123456,
                        "keyName": "content_category",
                        "operator": "IS",
                        "valueIds": [789012, 789013],
                        "valueNames": ["sports", "news"],
                    },
                    {
                        "keyId": 234567,
                        "keyName": "audience_segment",
                        "operator": "IS",
                        "valueIds": [890123],
                        "valueNames": ["sports_enthusiasts"],
                    },
                ],
            },
            "dayPartTargeting": {
                "timeZone": "America/New_York",
                "dayParts": [
                    {
                        "dayOfWeek": "MONDAY",
                        "startTime": {"hour": 6, "minute": 0},
                        "endTime": {"hour": 23, "minute": 59},
                    },
                    {
                        "dayOfWeek": "TUESDAY",
                        "startTime": {"hour": 6, "minute": 0},
                        "endTime": {"hour": 23, "minute": 59},
                    },
                ],
            },
        },
        "frequencyCaps": [{"maxImpressions": 3, "numTimeUnits": 1, "timeUnit": "DAY"}],
        "creativePlaceholders": [
            {"size": {"width": 1920, "height": 1080}, "expectedCreativeCount": 3, "creativeSizeType": "PIXEL"}
        ],
    },
    "order": {
        "id": 2871234567,
        "name": "Q1 2025 Sports Campaign",
        "advertiserId": 4561234567,
        "advertiserName": "SportsBrand Inc",
        "traffickerId": 987654321,
        "status": "APPROVED",
        "totalBudget": {"currencyCode": "USD", "microAmount": 50000000000},  # $50,000
    },
    "creatives": [
        {
            "id": 138123456789,
            "name": "Sports_Hero_Video_30s",
            "size": {"width": 1920, "height": 1080},
            "Creative.Type": "VideoCreative",
            "previewUrl": "https://pubads.g.doubleclick.net/preview/123456",
        },
        {
            "id": 138123456790,
            "name": "Sports_Promo_Video_15s",
            "size": {"width": 1920, "height": 1080},
            "Creative.Type": "VideoCreative",
            "previewUrl": "https://pubads.g.doubleclick.net/preview/123457",
        },
    ],
    "creative_associations": [
        {"lineItemId": 7047822666, "creativeId": 138123456789},
        {"lineItemId": 7047822666, "creativeId": 138123456790},
    ],
    "media_product_json": {
        "product_id": "gam_line_item_7047822666",
        "name": "Q1_2025_Sports_Premium_Video",
        "description": "GAM Line Item: Q1_2025_Sports_Premium_Video",
        "formats": [
            {
                "format_id": "video_1920x1080",
                "name": "Video 1920x1080",
                "type": "video",
                "dimensions": {"width": 1920, "height": 1080},
            }
        ],
        "delivery_type": "guaranteed",
        "targeting_overlay": {
            "geo_country_any_of": ["United States", "Canada"],
            "device_type_any_of": ["desktop", "tablet"],
            "key_value_pairs": {"content_category": ["sports", "news"], "audience_segment": ["sports_enthusiasts"]},
            "dayparting": {
                "timezone": "America/New_York",
                "schedules": [
                    {"days": ["MONDAY"], "start_hour": 6, "end_hour": 23},
                    {"days": ["TUESDAY"], "start_hour": 6, "end_hour": 23},
                ],
            },
            "frequency_cap": {"suppress_minutes": 1440, "scope": "media_buy"},  # 24 hours
        },
        "implementation_config": {
            "gam": {
                "line_item_id": 7047822666,
                "order_id": 2871234567,
                "line_item_type": "STANDARD",
                "priority": 8,
                "delivery_rate_type": "EVENLY",
                "creative_placeholders": [
                    {"size": {"width": 1920, "height": 1080}, "expectedCreativeCount": 3, "creativeSizeType": "PIXEL"}
                ],
                "status": "DELIVERING",
                "start_datetime": "2025-01-01T00:00:00",
                "end_datetime": "2025-03-31T23:59:59",
                "units_bought": 1000000,
                "cost_type": "CPM",
                "discount_type": None,
                "allow_overbook": False,
            }
        },
    },
}

print("=" * 80)
print("GAM LINE ITEM VIEWER - API RESPONSE")
print("=" * 80)
print("\nEndpoint: /api/tenant/{tenant_id}/gam/line-item/7047822666")
print("Returns: Complete line item data with order, creatives, and media product JSON")
print("\n" + "=" * 80)
print("FULL RESPONSE:")
print("=" * 80)
print(json.dumps(mock_response, indent=2))

print("\n" + "=" * 80)
print("KEY FEATURES OF THE LINE ITEM VIEWER:")
print("=" * 80)
print(
    """
1. BASIC INFORMATION:
   - Line Item ID: 7047822666
   - Name: Q1_2025_Sports_Premium_Video
   - Status: DELIVERING
   - Type: STANDARD
   - Priority: 8

2. PRICING & BUDGET:
   - CPM: $15.00
   - Total Impressions: 1,000,000
   - Total Budget: $15,000

3. TARGETING:
   - Geographic: US and Canada
   - Devices: Desktop and Tablet
   - Custom Key-Values:
     * content_category: sports, news
     * audience_segment: sports_enthusiasts
   - Dayparting: Monday-Tuesday, 6 AM - 11:59 PM EST
   - Frequency Cap: 3 impressions per day

4. CREATIVES:
   - 2 Video creatives (1920x1080)
   - 30-second and 15-second spots

5. MEDIA PRODUCT JSON:
   - Converts all GAM data to internal format
   - Ready for round-trip validation
   - Shows exact mapping from GAM to internal structure
"""
)

print("\n" + "=" * 80)
print("UI FEATURES:")
print("=" * 80)
print(
    """
The Line Item Viewer UI provides:

1. **Overview Tab**: Basic info, order details, pricing, schedule
2. **Targeting Tab**: All targeting criteria in organized sections
3. **Creatives Tab**: List of associated creatives with preview links
4. **Delivery Settings Tab**: Pacing, frequency caps, creative placeholders
5. **Raw GAM Data Tab**: Complete JSON response from GAM API
6. **Product JSON Tab**: Internal media product format with copy button

Access via:
- Orders Browser: Click "Details" button on any line item
- Tenant Detail Page: Click "View Line Item" and enter ID
- Direct URL: /tenant/{tenant_id}/gam/line-item/{line_item_id}
"""
)

# Save the response to a file for reference
with open("line_item_7047822666_response.json", "w") as f:
    json.dump(mock_response, f, indent=2)

print("\n✓ Full response saved to: line_item_7047822666_response.json")
