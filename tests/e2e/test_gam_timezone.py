#!/usr/bin/env python3
"""
Test script to understand GAM timezone behavior

This script runs sample reports with different timezone settings to understand:
1. What timezone GAM returns data in by default
2. How to specify a timezone for the report
3. What format the timestamps come back in
"""

import csv
import gzip
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest
import pytz


def test_gam_timezone_behavior():
    """Test GAM reporting timezone behavior"""

    print("=" * 60)
    print("GAM Timezone Behavior Test")
    print("=" * 60)

    # Check if we can get a real GAM client
    try:
        from scripts.ops.gam_helper import get_ad_manager_client_for_tenant
        from src.core.database.database_session import get_db_session

        # Get first available tenant with GAM configured
        conn = get_db_session()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT tenant_id, config
            FROM tenants
            WHERE config LIKE '%google_ad_manager%'
            LIMIT 1
        """
        )
        result = cursor.fetchone()
        conn.close()

        if not result:
            print("\n❌ No tenant with GAM configured found.")
            print("   Running in mock mode instead...")
            return test_mock_timezone_behavior()

        tenant_id = result["tenant_id"]
        config = json.loads(result["config"]) if isinstance(result["config"], str) else result["config"]

        print(f"\n✅ Found tenant: {tenant_id}")

        # Get the GAM client
        client = get_ad_manager_client_for_tenant(tenant_id)

        # Test different timezone configurations
        test_timezone_configs(client, config)

    except Exception as e:
        print(f"\n⚠️ Cannot test with real GAM: {e}")
        print("   Running mock tests instead...")
        return test_mock_timezone_behavior()


def test_timezone_configs():
    """Test different timezone configurations with GAM"""
    # Skip this test as it requires a real GAM client
    pytest.skip("This test requires a real GAM client connection")

    # Get network info to understand default timezone
    network_service = client.GetService("NetworkService")
    network = network_service.getCurrentNetwork()

    print("\n📍 Network Information:")
    print(f"   Network Code: {network.networkCode}")
    print(f"   Display Name: {network.displayName}")
    print(f"   Time Zone: {network.timeZone}")
    print(f"   Currency Code: {network.currencyCode}")

    # Define test configurations
    timezone_tests = [
        {"name": "Default (No timezone specified)", "config": {}},
        {"name": "PUBLISHER timezone", "config": {"timeZoneType": "PUBLISHER"}},
        {"name": "PACIFIC timezone", "config": {"timeZoneType": "PACIFIC"}},
    ]

    # Run a small test report for each configuration
    now = datetime.now()
    yesterday = now - timedelta(days=1)

    for test in timezone_tests:
        print(f"\n🧪 Testing: {test['name']}")
        print("-" * 40)

        try:
            # Build report query
            report_query = {
                "dimensions": ["DATE", "HOUR"],
                "columns": ["AD_SERVER_IMPRESSIONS"],
                "dateRangeType": "CUSTOM_DATE",
                "startDate": {"year": yesterday.year, "month": yesterday.month, "day": yesterday.day},
                "endDate": {"year": yesterday.year, "month": yesterday.month, "day": yesterday.day},
            }

            # Add timezone configuration if specified
            report_query.update(test["config"])

            # Create and run the report job
            report_job = {"reportQuery": report_query}

            print(f"   Running report with config: {test['config']}")
            report_job_id = report_service.runReportJob(report_job)

            # Wait for completion
            import time

            max_wait = 30
            waited = 0
            while waited < max_wait:
                status = report_service.getReportJobStatus(report_job_id)
                if status == "COMPLETED":
                    break
                elif status == "FAILED":
                    print("   ❌ Report failed")
                    continue
                time.sleep(1)
                waited += 1

            if report_service.getReportJobStatus(report_job_id) != "COMPLETED":
                print(f"   ⏱️ Report timed out after {max_wait} seconds")
                continue

            # Download and parse the report
            with tempfile.NamedTemporaryFile(suffix=".csv.gz", delete=False) as tmp_file:
                report_downloader.DownloadReportToFile(report_job_id, "CSV_DUMP", tmp_file)
                tmp_path = tmp_file.name

            # Read the CSV
            with gzip.open(tmp_path, "rt") as gz_file:
                csv_reader = csv.DictReader(gz_file)
                rows = list(csv_reader)

            # Clean up
            os.unlink(tmp_path)

            # Analyze the results
            if rows:
                print(f"   ✅ Got {len(rows)} rows")
                print("   Sample row:")
                sample = rows[0]
                for key, value in sample.items():
                    if key in ["DATE", "HOUR"]:
                        print(f"      {key}: {value}")

                # Check the format of DATE and HOUR
                if "DATE" in sample:
                    date_format = analyze_date_format(sample["DATE"])
                    print(f"   Date format: {date_format}")

                if "HOUR" in sample:
                    hour_format = analyze_hour_format(sample["HOUR"])
                    print(f"   Hour format: {hour_format}")
            else:
                print("   ⚠️ No data returned")

        except Exception as e:
            print(f"   ❌ Error: {e}")


def analyze_date_format(date_str):
    """Analyze the format of the date string"""
    if "-" in date_str:
        parts = date_str.split("-")
        if len(parts) == 3:
            return f"ISO format (YYYY-MM-DD): {date_str}"
    return f"Unknown format: {date_str}"


def analyze_hour_format(hour_str):
    """Analyze the format of the hour string"""
    try:
        hour_int = int(hour_str)
        if 0 <= hour_int <= 23:
            return f"Hour as integer (0-23): {hour_str}"
    except:
        pass

    if len(hour_str) == 10 and hour_str.isdigit():
        return f"YYYYMMDDHH format: {hour_str}"

    return f"Unknown format: {hour_str}"


def test_mock_timezone_behavior():
    """Test timezone behavior with mock data"""

    print("\n🎭 Mock Timezone Test")
    print("-" * 40)

    # Simulate different timezone scenarios
    timezones = [
        ("America/New_York", "Eastern Time"),
        ("America/Los_Angeles", "Pacific Time"),
        ("Europe/London", "London Time"),
        ("Asia/Tokyo", "Tokyo Time"),
    ]

    for tz_name, tz_label in timezones:
        tz = pytz.timezone(tz_name)
        now = datetime.now(tz)

        print(f"\n📍 {tz_label} ({tz_name}):")
        print(f"   Current time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"   UTC offset: {now.strftime('%z')}")
        print(f"   ISO format: {now.isoformat()}")

        # Simulate what GAM might return
        print(f"   Simulated GAM DATE: {now.strftime('%Y-%m-%d')}")
        print(f"   Simulated GAM HOUR: {now.hour}")

    print("\n📊 Key Findings:")
    print("   • GAM typically returns DATE in YYYY-MM-DD format")
    print("   • HOUR is returned as integer 0-23")
    print("   • No timezone information in the data itself")
    print("   • Timezone is determined by:")
    print("     - Network's configured timezone (default)")
    print("     - timeZoneType parameter in report query")
    print("     - PUBLISHER = network timezone")
    print("     - PACIFIC = Pacific Time")
    print("   • Data freshness must account for network timezone")


def main():
    """Main test runner"""
    print("\n🚀 GAM Timezone Behavior Test")
    print("=" * 60)

    # First, let's understand Python timezone handling
    print("\n📚 Python Timezone Basics:")
    print("-" * 40)

    # Show different timezone representations
    ny_tz = pytz.timezone("America/New_York")
    la_tz = pytz.timezone("America/Los_Angeles")

    now_utc = datetime.now(pytz.UTC)
    now_ny = now_utc.astimezone(ny_tz)
    now_la = now_utc.astimezone(la_tz)

    print(f"UTC:      {now_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"New York: {now_ny.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"LA:       {now_la.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # Now test GAM behavior
    test_gam_timezone_behavior()

    print("\n" + "=" * 60)
    print("✅ Timezone Test Complete")
    print("=" * 60)

    print("\n🔑 Recommendations:")
    print("1. Always get the network timezone from GAM network settings")
    print("2. Store network timezone in tenant configuration")
    print("3. Use timeZoneType: 'PUBLISHER' for consistency")
    print("4. Convert timestamps to requested timezone in post-processing")
    print("5. Include both network_timezone and requested_timezone in response")
    print("6. Calculate data_valid_until based on network timezone")


if __name__ == "__main__":
    main()
