"""Tests for device_platform → device_type_any_of targeting conversion.

Regression tests for salesagent-z0ud: ensures device_platform (OS-level)
values from AdCP TargetingOverlay are correctly converted to internal
device_type_any_of (form factor) values that adapters consume.

Mapping:
  ios, android          → mobile, tablet
  windows, macos, linux, chromeos → desktop
  tvos, tizen, webos, fire_os, roku_os → ctv
  unknown               → (ignored, no form factors)
"""

from src.core.schemas import Targeting


class TestDevicePlatformToDeviceType:
    """device_platform values must map to device_type_any_of form factors."""

    def test_mobile_platforms(self):
        t = Targeting(device_platform=["ios", "android"])
        assert sorted(t.device_type_any_of) == ["mobile", "tablet"]

    def test_desktop_platforms(self):
        t = Targeting(device_platform=["windows", "macos", "linux", "chromeos"])
        assert t.device_type_any_of == ["desktop"]

    def test_ctv_platforms(self):
        t = Targeting(device_platform=["tvos", "tizen", "webos", "fire_os", "roku_os"])
        assert t.device_type_any_of == ["ctv"]

    def test_mixed_platforms(self):
        t = Targeting(device_platform=["ios", "windows", "tvos"])
        assert sorted(t.device_type_any_of) == ["ctv", "desktop", "mobile", "tablet"]

    def test_single_ios(self):
        t = Targeting(device_platform=["ios"])
        assert sorted(t.device_type_any_of) == ["mobile", "tablet"]

    def test_single_windows(self):
        t = Targeting(device_platform=["windows"])
        assert t.device_type_any_of == ["desktop"]

    def test_single_tvos(self):
        t = Targeting(device_platform=["tvos"])
        assert t.device_type_any_of == ["ctv"]

    def test_unknown_platform_ignored(self):
        """unknown platform maps to no form factors."""
        t = Targeting(device_platform=["unknown"])
        assert t.device_type_any_of is None

    def test_unknown_mixed_with_known(self):
        """unknown is ignored, known platforms still map."""
        t = Targeting(device_platform=["unknown", "ios"])
        assert sorted(t.device_type_any_of) == ["mobile", "tablet"]

    def test_no_device_platform(self):
        """No device_platform should leave device_type_any_of untouched."""
        t = Targeting()
        assert t.device_type_any_of is None

    def test_empty_device_platform(self):
        """Empty list should not set device_type_any_of (pydantic min_length may reject)."""
        t = Targeting(device_platform=None)
        assert t.device_type_any_of is None


class TestDevicePlatformDoesNotOverrideExplicit:
    """If device_type_any_of is already set, device_platform should not overwrite it."""

    def test_explicit_device_type_preserved(self):
        t = Targeting(device_type_any_of=["desktop"], device_platform=["ios"])
        assert t.device_type_any_of == ["desktop"]


class TestDevicePlatformDeduplication:
    """Mapped form factors should be deduplicated."""

    def test_duplicate_mobile_platforms(self):
        """ios and android both map to mobile+tablet, should not duplicate."""
        t = Targeting(device_platform=["ios", "android"])
        assert sorted(t.device_type_any_of) == ["mobile", "tablet"]

    def test_all_desktop_platforms(self):
        """Multiple desktop platforms should produce single 'desktop'."""
        t = Targeting(device_platform=["windows", "macos", "linux", "chromeos"])
        assert t.device_type_any_of == ["desktop"]

    def test_all_ctv_platforms(self):
        """Multiple CTV platforms should produce single 'ctv'."""
        t = Targeting(device_platform=["tvos", "tizen", "webos", "fire_os", "roku_os"])
        assert t.device_type_any_of == ["ctv"]


class TestDevicePlatformRoundtrip:
    """device_platform should survive model_dump → reconstruct cycle."""

    def test_roundtrip_preserves_device_type(self):
        t1 = Targeting(device_platform=["ios", "android"])
        d = t1.model_dump(exclude_none=True)
        # device_type_any_of should be in the serialized output
        assert "device_type_any_of" in d
        assert sorted(d["device_type_any_of"]) == ["mobile", "tablet"]
        # device_platform is inherited from TargetingOverlay and should be present
        assert "device_platform" in d

    def test_roundtrip_reconstruct(self):
        t1 = Targeting(device_platform=["ios", "windows"])
        d = t1.model_dump(exclude_none=True)
        t2 = Targeting(**d)
        # device_type_any_of should be the same after reconstruction
        assert sorted(t2.device_type_any_of) == sorted(t1.device_type_any_of)
