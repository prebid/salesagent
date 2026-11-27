"""Validated signals and their agent deployments, shared by catalogue tests."""

import factory
from adcp.types import Signal


class SignalFactory(factory.Factory):
    class Meta:
        model = Signal

    class Params:
        agent_url = "https://sales.example.com"
        key_type = "key_value"
        key = "audience"
        value = "premium"
        is_live = True

    signal_agent_segment_id = factory.Sequence(lambda n: f"segment-{n}")
    name = "Premium audience"
    description = factory.LazyAttribute(lambda signal: f"Description for {signal.name}")
    signal_ref = {"scope": "signal_source", "signal_source_url": "https://signals.example.com", "signal_id": "audience"}
    signal_type = "marketplace"
    data_provider = "TestProvider"
    coverage_percentage = 85.0
    deployments = factory.LazyAttribute(
        lambda signal: [
            {
                "type": "agent",
                "agent_url": signal.agent_url,
                "is_live": signal.is_live,
                "activation_key": (
                    {"type": "key_value", "key": signal.key, "value": signal.value}
                    if signal.key_type == "key_value"
                    else {"type": signal.key_type, "segment_id": signal.value}
                ),
            }
        ]
    )

    @classmethod
    def payload(cls, **fields) -> dict:
        """Apply scenario fields before validating and serializing the signal."""
        return cls.build(**fields).model_dump(mode="json", exclude_none=True)
