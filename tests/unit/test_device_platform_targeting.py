"""Tests for device_platform → device_form_factors targeting conversion.

Ensures device_platform (OS-level) values from AdCP TargetingOverlay are converted to the
form factors adapters consume, read off ``Targeting.device_form_factors``.

``device_platform`` is the PINNED spec field (core/targeting.json); ``device_type_any_of``
is a seller extension (``src/services/targeting_capabilities.py``). The conversion is a
DERIVED property, not a validator that fills in a field — the model never rewrites its own
input, and an internal field never gets mutated to look buyer-supplied. GAM reads
``targeting_overlay.device_form_factors`` (``src/adapters/gam/managers/targeting.py:777``).

Mapping (``_PLATFORM_TO_FORM_FACTORS`` in ``src/core/schemas/_base.py``):
  ios, android                          → mobile, tablet
  windows, macos, linux, chromeos       → desktop
  tvos, tizen, webos, fire_os, roku_os  → ctv
  unknown                               → (ignored, no form factors)

WHAT THESE TESTS DO NOT ENDORSE. They pin the mapping as it behaves today; whether it
should exist is open on GH #1076, and two of its premises are questionable against the
pin. ``core/targeting.json`` separates the dimensions on purpose -- ``device_platform``
is "technical compatibility (app only works on iOS)", ``device_type`` is "hardware
categories rather than operating systems" -- so deriving form factors from the OS field
conflates two independent buyer intents. And the pinned ``device_type`` /
``device_type_exclude`` fields never reach ``device_form_factors`` at all, so GAM's
refusal (guarded on that property) does not fire for them and it silently accepts
targeting it will not honor. Read a failure here as "the mapping changed", never as
"the mapping is correct".
"""

from src.core.schemas import Targeting


class TestDevicePlatformToDeviceType:
    """device_platform values must map to device_form_factors."""

    def test_mobile_platforms(self):
        t = Targeting(device_platform=["ios", "android"])
        assert t.device_form_factors == ["mobile", "tablet"]

    def test_desktop_platforms(self):
        t = Targeting(device_platform=["windows", "macos", "linux", "chromeos"])
        assert t.device_form_factors == ["desktop"]

    def test_ctv_platforms(self):
        t = Targeting(device_platform=["tvos", "tizen", "webos", "fire_os", "roku_os"])
        assert t.device_form_factors == ["ctv"]

    def test_mixed_platforms(self):
        t = Targeting(device_platform=["ios", "windows", "tvos"])
        assert t.device_form_factors == ["ctv", "desktop", "mobile", "tablet"]

    def test_single_ios(self):
        t = Targeting(device_platform=["ios"])
        assert t.device_form_factors == ["mobile", "tablet"]

    def test_single_windows(self):
        t = Targeting(device_platform=["windows"])
        assert t.device_form_factors == ["desktop"]

    def test_single_tvos(self):
        t = Targeting(device_platform=["tvos"])
        assert t.device_form_factors == ["ctv"]

    def test_unknown_platform_ignored(self):
        """'unknown' maps to no form factors — and does not become an empty list."""
        t = Targeting(device_platform=["unknown"])
        assert t.device_form_factors is None

    def test_unknown_platform_does_not_invent_a_seller_extension(self):
        """The derived read never writes the seller extension it falls back from."""
        t = Targeting(device_platform=["ios", "android"])
        assert t.device_type_any_of is None

    def test_unknown_mixed_with_known(self):
        """unknown is ignored, known platforms still map."""
        t = Targeting(device_platform=["unknown", "ios"])
        assert t.device_form_factors == ["mobile", "tablet"]

    def test_no_device_platform(self):
        """Neither source set → no form factors to target."""
        t = Targeting()
        assert t.device_form_factors is None

    def test_empty_device_platform(self):
        """An explicit null device_platform is the same as unset."""
        t = Targeting(device_platform=None)
        assert t.device_form_factors is None


class TestDevicePlatformDoesNotOverrideExplicit:
    """If device_type_any_of is already set, device_platform does not contribute."""

    def test_explicit_device_type_preserved(self):
        """The seller extension WINS over the spec field; the two are not unioned.

        ``device_form_factors`` returns ``device_type_any_of`` whole when it is set, so a
        buyer who sends ``device_platform=["ios"]`` to a seller carrying an explicit
        ``device_type_any_of=["desktop"]`` gets desktop only — the ios request narrows
        nothing and adds nothing.
        """
        t = Targeting(device_type_any_of=["desktop"], device_platform=["ios"])
        assert t.device_form_factors == ["desktop"]
        # And the buyer's own field is still readable, unrewritten.
        assert [p.value for p in t.device_platform] == ["ios"]

    def test_explicit_device_type_alone(self):
        """With no device_platform, the seller extension is returned as-is."""
        t = Targeting(device_type_any_of=["desktop"])
        assert t.device_form_factors == ["desktop"]


class TestDevicePlatformDeduplication:
    """Mapped form factors are deduplicated and sorted."""

    def test_duplicate_mobile_platforms(self):
        """ios and android both map to mobile+tablet, so no duplicates."""
        t = Targeting(device_platform=["ios", "android"])
        assert t.device_form_factors == ["mobile", "tablet"]

    def test_all_desktop_platforms(self):
        """Multiple desktop platforms produce a single 'desktop'."""
        t = Targeting(device_platform=["windows", "macos", "linux", "chromeos"])
        assert t.device_form_factors == ["desktop"]

    def test_all_ctv_platforms(self):
        """Multiple CTV platforms produce a single 'ctv'."""
        t = Targeting(device_platform=["tvos", "tizen", "webos", "fire_os", "roku_os"])
        assert t.device_form_factors == ["ctv"]


class TestDevicePlatformRoundtrip:
    """device_platform survives model_dump → reconstruct; the derived value is not dumped."""

    def test_roundtrip_dumps_the_spec_field_only(self):
        t1 = Targeting(device_platform=["ios", "android"])
        d = t1.model_dump(exclude_none=True, mode="json")
        # The pinned spec field the buyer sent is what goes on the wire.
        assert d["device_platform"] == ["ios", "android"]
        # device_form_factors is DERIVED on read, so it is not a wire field at all; and the
        # seller extension it falls back from was never supplied, so it stays absent
        # rather than being back-filled from device_platform.
        assert "device_form_factors" not in d
        assert "device_type_any_of" not in d

    def test_roundtrip_reconstruct(self):
        t1 = Targeting(device_platform=["ios", "windows"])
        d = t1.model_dump(exclude_none=True, mode="json")
        t2 = Targeting(**d)
        assert t2.device_form_factors == t1.device_form_factors
        assert t2.device_form_factors == ["desktop", "mobile", "tablet"]
