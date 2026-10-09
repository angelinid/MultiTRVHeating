"""
Unit tests for hold mode at zone level: the opening curve, the single offset policy,
virtual opening and demand for a held zone.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))
sys.path.insert(0, str(Path(__file__).parent))

from trv_model import opening_for_error, OPENING_CURVE
from zone_wrapper import (
    ZoneWrapper, HOLD_AUTO, HOLD_SWITCH, HOLD_TEMP_OFFSET, HEATING_TEMP_OFFSET, DEFAULT_TEMP_OFFSET,
)


def climate(current, target):
    return SimpleNamespace(state="heat", attributes={"current_temperature": current, "temperature": target})


class TestHoldMode:
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

    def zone(self, high=True, ext=False) -> ZoneWrapper:
        return ZoneWrapper("climate.t", "T", is_high_priority=high,
                           ext_temp_entity_id="sensor.t" if ext else None)

    def test_1_opening_curve(self):
        print("\nTest 1: Error to opening - 0.5/1/1.5/2 °C map to 25/50/75/100 %")
        for error, expected in ((0.5, 25), (1.0, 50), (1.5, 75), (2.0, 100), (3.0, 100)):
            self.verify(opening_for_error(error) == expected, f"{error} °C -> {expected} %")
        self.verify(opening_for_error(0.0) == 0 and opening_for_error(-1.0) == 0, "no opening at or above target")
        self.verify(opening_for_error(0.2) == OPENING_CURVE[0][1], "a small shortfall still asks for the first step")
        self.verify(opening_for_error(1.4) == 50 and opening_for_error(1.9) == 75, "values between steps use the step below")

    def test_2_hold_offset(self):
        print("\nTest 2: Hold sets the hold offset and release returns to the policy offset")
        z = self.zone()
        self.verify(z.set_hold(HOLD_SWITCH, True, 0.0), "engaging reports a changed offset")
        self.verify(z.temperature_offset == HOLD_TEMP_OFFSET, "hold offset wanted")
        self.verify(not z.set_hold(HOLD_AUTO, True, 1.0), "a second reason changes nothing")
        z.set_hold(HOLD_SWITCH, False, 2.0)
        self.verify(z.held and z.temperature_offset == HOLD_TEMP_OFFSET, "still held by the other reason")
        self.verify(z.set_hold(HOLD_AUTO, False, 3.0), "releasing the last reason reports a change")
        self.verify(z.temperature_offset == DEFAULT_TEMP_OFFSET, "neutral offset again")

    def test_3_opening_logic_cannot_override_hold(self):
        print("\nTest 3: Valve movements and boiler-off resets never touch a held offset")
        z = self.zone()
        z.set_hold(HOLD_SWITCH, True, 0.0)
        for opening in (0.0, 100.0, 80.0, 0.0, 50.0):
            z.update_trv_opening(opening)
            self.verify(z.temperature_offset == HOLD_TEMP_OFFSET, f"opening {opening:.0f} % keeps the hold offset")
        z.reset_policy_offset()
        self.verify(z.temperature_offset == HOLD_TEMP_OFFSET, "boiler-off reset keeps the hold offset")
        z.set_hold(HOLD_SWITCH, False, 1.0)
        self.verify(z.temperature_offset == DEFAULT_TEMP_OFFSET, "policy applies again after release")

    def test_4_policy_offset_resumes_after_release(self):
        print("\nTest 4: Heating offset logic still works and resumes after a hold")
        z = self.zone()
        z.update_trv_opening(80.0)
        self.verify(z.temperature_offset == HEATING_TEMP_OFFSET, "-2 °C at 80 %")
        z.set_hold(HOLD_AUTO, True, 0.0)
        self.verify(z.temperature_offset == HOLD_TEMP_OFFSET, "hold wins")
        z.set_hold(HOLD_AUTO, False, 1.0)
        self.verify(z.temperature_offset == HEATING_TEMP_OFFSET, "back to the heating offset")

    def test_5_virtual_opening_and_demand(self):
        print("\nTest 5: A held zone's opening is virtual and drives demand")
        z = self.zone(high=False, ext=True)
        z.update_from_state(climate(11.0, 19.0))      # TRV reads 11 because of the -9 offset
        z.update_external_temperature(18.0)           # the room is really 18
        z.set_hold(HOLD_SWITCH, True, 0.0)
        z.update_trv_opening(100.0)                   # valve forced fully open
        self.verify(abs(z.current_error - 1.0) < 1e-9, "error from the external sensor")
        self.verify(z.effective_opening == 50.0, "1 °C below target -> 50 % virtual opening")
        self.verify(abs(z.get_demand_metric() - 0.5) < 1e-9, "demand follows the virtual opening, not the forced 100 %")
        self.verify(z.is_demanding_heat and z.counts_as_high_priority, "switch hold asks for heat as high priority")
        z.update_external_temperature(19.5)
        self.verify(z.effective_opening == 0.0 and not z.is_demanding_heat, "no demand once the room is at target")

    def test_6_auto_hold_keeps_priority(self):
        print("\nTest 6: Automatic hold does not change a zone's priority")
        z = self.zone(high=False)
        z.set_hold(HOLD_AUTO, True, 0.0)
        self.verify(not z.counts_as_high_priority, "still low priority")

    def test_7_forced_opening_is_not_cooling(self):
        print("\nTest 7: A forced-open valve is not read as the 'cooling' demand signal")
        z = self.zone()
        z.update_from_state(climate(20.0, 19.0))
        z.set_hold(HOLD_SWITCH, True, 0.0)
        z.update_trv_opening(100.0)
        self.verify(z.heating_status != 'cooling' and not z.is_demanding_heat, "no demand from the forced opening")

    def test_8_unavailable_position_counts_closed(self):
        print("\nTest 8: Unavailable position sensor counts as a closed valve")
        z = self.zone()
        z.update_trv_opening(100.0)
        z.mark_position_unavailable()
        self.verify(z.measured_opening == 0.0 and z.effective_opening == 0.0, "treated as closed")
        z.update_trv_opening(50.0)
        self.verify(z.measured_opening == 50.0, "reports resume")

    def test_9_applied_offset_used_for_temperature(self):
        print("\nTest 9: The offset on the device (not the wanted one) is removed from the TRV reading")
        z = self.zone()
        z.update_device_offset(-2.0)
        z.update_from_state(climate(17.0, 20.0))
        self.verify(z.current_temp == 19.0, "device offset removed")
        z.set_hold(HOLD_SWITCH, True, 0.0)           # wanted -9, device still -2
        self.verify(z.current_temp == 19.0, "pending hold does not distort the estimate")
        z.update_device_offset(-9.0)
        self.verify(z.current_temp == 26.0, "estimate follows the device once it reports the new offset")

    def test_10_write_in_flight_counts_as_applied(self):
        print("\nTest 10: A calibration write takes effect on the reading at once")
        z = self.zone()
        z.update_device_offset(0.0)
        z.update_from_state(climate(20.0, 20.0))
        z.pushed_offset = -1.0
        z.offset_in_flight = True
        z.update_from_state(climate(19.0, 20.0))      # the TRV already reads 1 °C lower
        self.verify(z.current_temp == 20.0, "reading corrected by the offset just written, before the echo")
        z.update_device_offset(-1.0)
        self.verify(not z.offset_in_flight and z.current_temp == 20.0, "echo confirms it, estimate unchanged")

    def run_all_tests(self):
        for name in sorted(n for n in dir(self) if n.startswith("test_")):
            getattr(self, name)()
        print(f"\n  Hold mode: {self.passed} passed, {self.failed} failed")


if __name__ == "__main__":
    suite = TestHoldMode()
    suite.run_all_tests()
    sys.exit(1 if suite.failed else 0)
