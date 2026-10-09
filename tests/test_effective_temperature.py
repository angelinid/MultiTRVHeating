"""
Tests for the zone effective temperature: external sensor preference, staleness
fallback and removal of our own calibration offset from the TRV reading.
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))
sys.path.insert(0, str(Path(__file__).parent))

from zone_wrapper import ZoneWrapper, EXT_TEMP_MAX_AGE, HEATING_TEMP_OFFSET


def climate_state(current: float, target: float):
    return SimpleNamespace(
        state="heat",
        attributes={"current_temperature": current, "temperature": target},
    )


class TestEffectiveTemperature:
    """Effective temperature behaviour of ZoneWrapper."""

    def __init__(self):
        self.passed = 0
        self.failed = 0

    def verify(self, condition: bool, message: str) -> None:
        if condition:
            self.passed += 1
            print(f"    ✓ {message}")
        else:
            self.failed += 1
            print(f"    ✗ {message}")

    def make_zone(self, with_ext: bool) -> ZoneWrapper:
        return ZoneWrapper(
            entity_id="climate.test", name="Test",
            ext_temp_entity_id="sensor.ext" if with_ext else None,
        )

    def test_1_trv_only(self):
        print("\nTest 1: No external sensor - TRV reading is used")
        zone = self.make_zone(with_ext=False)
        zone.update_from_state(climate_state(18.0, 20.0))
        self.verify(zone.current_temp == 18.0, "effective temp equals TRV reading")
        self.verify(abs(zone.current_error - 2.0) < 1e-9, "error is target - TRV reading")

    def test_2_offset_removed(self):
        print("\nTest 2: Our offset is removed from the TRV reading")
        zone = self.make_zone(with_ext=False)
        zone.temperature_offset = HEATING_TEMP_OFFSET  # -2: TRV reads 2°C colder than real
        zone.update_from_state(climate_state(18.0, 20.0))
        self.verify(zone.current_temp == 20.0, "real temp = reported - offset = 20.0")
        self.verify(zone.current_error == 0.0, "no demand from our own offset")
        self.verify(zone.trv_temp == 18.0, "raw TRV reading kept")

    def test_3_external_preferred(self):
        print("\nTest 3: Fresh external sensor wins over the TRV")
        zone = self.make_zone(with_ext=True)
        zone.update_from_state(climate_state(17.0, 20.0))
        zone.update_external_temperature(19.5)
        self.verify(zone.current_temp == 19.5, "effective temp is the external reading")
        self.verify(abs(zone.current_error - 0.5) < 1e-9, "error uses the external reading")
        self.verify(zone.export_zone_state()["temperature_source"] == "external", "source reported as external")

    def test_4_no_reading_yet(self):
        print("\nTest 4: Configured sensor with no reading falls back to the TRV")
        zone = self.make_zone(with_ext=True)
        zone.update_from_state(climate_state(18.0, 20.0))
        self.verify(zone.ext_current_temp is None, "no fake default reading")
        self.verify(zone.current_temp == 18.0, "TRV reading used")
        self.verify(zone.export_zone_state()["external_sensor_temperature"] is None, "state shows no external reading")

    def test_5_stale_fallback(self):
        print("\nTest 5: Stale external sensor falls back to the TRV")
        zone = self.make_zone(with_ext=True)
        zone.update_external_temperature(19.5)
        zone.ext_last_update = time.time() - EXT_TEMP_MAX_AGE - 1
        zone.update_from_state(climate_state(18.0, 20.0))
        self.verify(zone.current_temp == 18.0, "stale reading ignored")
        self.verify(zone.export_zone_state()["temperature_source"] == "trv", "source reported as trv")

    def test_6_external_updates_error(self):
        print("\nTest 6: A new external reading refreshes the error immediately")
        zone = self.make_zone(with_ext=True)
        zone.update_from_state(climate_state(18.0, 20.0))
        zone.update_external_temperature(21.0)
        self.verify(zone.current_error < 0, "above target: no demand")
        self.verify(zone.get_demand_metric() == 0.0, "demand metric is zero")

    def run_all_tests(self):
        for name in sorted(n for n in dir(self) if n.startswith("test_")):
            getattr(self, name)()
        print(f"\n  Effective temperature: {self.passed} passed, {self.failed} failed")


if __name__ == "__main__":
    suite = TestEffectiveTemperature()
    suite.run_all_tests()
    sys.exit(1 if suite.failed else 0)
