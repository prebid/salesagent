"""Test that GetSignalsResponse properly excludes internal fields from nested Signal objects.

Signal has 4 internal fields that should NOT appear in responses:
- tenant_id: Internal multi-tenancy tracking
- created_at: Internal audit timestamp
- updated_at: Internal audit timestamp
- metadata: Internal additional data

Signal.model_dump() excludes these, but GetSignalsResponse must explicitly
call it for nested signals.

Related:
- Original bug: SyncCreativesResponse (f5bd7b8a)
- Systematic fix: All response models with nested Pydantic models
- Pattern: Parent models must explicitly call nested model.model_dump()
"""

from datetime import UTC, datetime

from src.core.schemas import GetSignalsResponse, Signal, SignalDeployment, SignalPricing


def test_get_signals_response_excludes_internal_fields():
    """Test that GetSignalsResponse excludes Signal internal fields."""
    # Create Signal with internal fields
    signal = Signal(
        signal_agent_segment_id="signal_123",
        name="Test Signal",
        description="Test signal description",
        signal_type="marketplace",
        data_provider="TestProvider",
        coverage_percentage=85.5,
        deployments=[
            SignalDeployment(
                platform="gam",
                account=None,
                is_live=True,
                type="platform",
                scope="platform-wide",
                decisioning_platform_segment_id="seg_123",
                estimated_activation_duration_minutes=None,
            )
        ],
        pricing=SignalPricing(cpm=2.50, currency="USD"),
        # Internal fields - should be excluded
        tenant_id="tenant_456",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata={"internal_key": "internal_value"},
    )

    # Create response
    response = GetSignalsResponse(signals=[signal])

    # Dump to dict
    result = response.model_dump()

    # Verify internal fields excluded from nested signal
    signal_data = result["signals"][0]
    assert "tenant_id" not in signal_data, "Internal field 'tenant_id' should be excluded"
    assert "created_at" not in signal_data, "Internal field 'created_at' should be excluded"
    assert "updated_at" not in signal_data, "Internal field 'updated_at' should be excluded"
    assert "metadata" not in signal_data, "Internal field 'metadata' should be excluded"

    # Verify required AdCP fields present
    assert signal_data["signal_agent_segment_id"] == "signal_123"
    assert signal_data["name"] == "Test Signal"
    assert signal_data["description"] == "Test signal description"
    assert signal_data["signal_type"] == "marketplace"
    assert signal_data["data_provider"] == "TestProvider"
    assert signal_data["coverage_percentage"] == 85.5
    assert "deployments" in signal_data
    assert "pricing" in signal_data


def test_get_signals_response_with_multiple_signals():
    """Test that internal fields are excluded from all signals in the list."""
    # Create multiple signals with internal fields
    signals = [
        Signal(
            signal_agent_segment_id=f"signal_{i}",
            name=f"Test Signal {i}",
            description=f"Description {i}",
            signal_type="marketplace" if i % 2 == 0 else "custom",
            data_provider=f"Provider{i}",
            coverage_percentage=float(80 + i),
            deployments=[
                SignalDeployment(
                    platform="gam",
                    account=None,
                    is_live=True,
                    type="platform",
                    scope="platform-wide",
                    decisioning_platform_segment_id=f"seg_{i}",
                    estimated_activation_duration_minutes=None,
                )
            ],
            pricing=SignalPricing(cpm=2.50 + i, currency="USD"),
            # Internal fields
            tenant_id=f"tenant_{i}",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            metadata={"key": f"value_{i}"},
        )
        for i in range(3)
    ]

    response = GetSignalsResponse(signals=signals)
    result = response.model_dump()

    # Verify internal fields excluded from all signals
    for i, signal_data in enumerate(result["signals"]):
        assert "tenant_id" not in signal_data, f"Signal {i}: tenant_id should be excluded"
        assert "created_at" not in signal_data, f"Signal {i}: created_at should be excluded"
        assert "updated_at" not in signal_data, f"Signal {i}: updated_at should be excluded"
        assert "metadata" not in signal_data, f"Signal {i}: metadata should be excluded"

        # Verify required fields present
        assert signal_data["signal_agent_segment_id"] == f"signal_{i}"
        assert signal_data["name"] == f"Test Signal {i}"
