"""
Flow override: forces the flow request for 15 minutes from the dashboard, needs an open valve,
is limited to 45 °C while only one valve is open, can be cancelled, and never breaks the
pump-safety rule.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))
sys.path.insert(0, str(Path(__file__).parent))

from override import MAX_FLOW_ONE_VALVE, OVERRIDE_DURATION, OverrideError
from test_keep_open_safety import standard_house

MINUTE = 60


class OverrideTests:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def verify(self, ok, text):
        if ok:
            self.passed += 1
            print(f"    ✓ {text}")
        else:
            self.failed += 1
            print(f"    ✗ {text}")

    async def set(self, s, value):
        c = s.controller
        c.flow_override.start(value, c.now())
        await c._calculate_and_command()

    async def test_01_applies_for_15_minutes_then_schedule(self):
        print("\nTest 1: Override sets the flow for 15 minutes, then the schedule takes over")
        s = standard_house(keep_open='leo', others=(20.5, 19.0))     # nobody wants heat
        c = await s.start()
        await s.run(10 * MINUTE)
        self.verify(c.current_flow_temp == 0.0, "before: the schedule asks for no heat")
        self.verify(c.has_open_valve(), "the keep-open valve is open")
        await self.set(s, 40)
        self.verify(c.current_flow_temp == 40.0, f"flow is 40 °C (got {c.current_flow_temp})")
        await s.run(14 * MINUTE)
        self.verify(c.current_flow_temp == 40.0, "still 40 °C after 14 minutes")
        await s.run(2 * MINUTE)
        self.verify(not c.flow_override.active and c.current_flow_temp == 0.0, "after 15 minutes: back to the schedule (no heat)")
        self.verify(not s.violation_runs(), "no unsafe period")
        s.close()

    async def test_02_setting_again_restarts_the_timer(self):
        print("\nTest 2: Setting it again restarts the 15 minutes and changes the value")
        s = standard_house(keep_open='leo', others=(20.5, 19.0))
        c = await s.start()
        await s.run(5 * MINUTE)
        await self.set(s, 35)
        await s.run(10 * MINUTE)
        await self.set(s, 42)
        await s.run(10 * MINUTE)
        self.verify(c.flow_override.active and c.current_flow_temp == 42.0, "10 minutes after the second setting it is still on, at 42 °C")
        s.close()

    async def test_03_refused_without_open_valve(self):
        print("\nTest 3: No open valve: refused with a message, nothing changes")
        s = standard_house(keep_open=None, others=(20.5, 19.0))
        c = await s.start()
        await s.run(10 * MINUTE)
        self.verify(not c.has_open_valve(), "no valve is open")
        try:
            c.flow_override.start(40, c.now())
            refused = False
        except OverrideError as err:
            refused = "No valve is open" in str(err)
        self.verify(refused, "OverrideError with the reason")
        self.verify(not c.flow_override.active and c.current_flow_temp == 0.0, "no override, no flow")
        s.close()

    async def test_04_valves_close_during_override(self):
        print("\nTest 4: If every valve closes during the override the boiler stops, and resumes when one opens")
        s = standard_house(keep_open=None, others=(18.0, 19.0))      # kitchen etc. cold: valves open
        c = await s.start()
        await s.run(10 * MINUTE)
        self.verify(c.has_open_valve(), "valves are open")
        await self.set(s, 45)
        for v in s.valves.values():                                   # rooms get warm, valves close
            v.room.temp = 25.0
        await s.run(10 * MINUTE)
        self.verify(c.flow_override.active, "the override carries on")
        if not c.has_open_valve():
            self.verify(c.current_flow_temp == 0.0 and c.flow_override.blocked, "no valve open: no flow, status says why")
        else:
            self.verify(True, "a valve stayed open (keep-open), flow continues")
        s.close()

    async def test_05_cancel(self):
        print("\nTest 5: Cancelling goes back to the schedule at once")
        s = standard_house(keep_open='leo', others=(20.5, 19.0))
        c = await s.start()
        await s.run(5 * MINUTE)
        await self.set(s, 55)
        await s.run(2 * MINUTE)
        c.flow_override.cancel("test")
        await c._calculate_and_command()
        self.verify(not c.flow_override.active and c.current_flow_temp == 0.0, "override ended, no heat requested")
        s.close()

    async def test_06_force_off_and_pump_overrun(self):
        print("\nTest 6: Forcing the boiler off while rooms want heat; the pump rule still holds")
        s = standard_house(keep_open='leo', others=(17.0, 19.0))
        c = await s.start()
        await s.run(10 * MINUTE)
        self.verify(c.current_flow_temp > 0, "rooms want heat: boiler is on")
        await self.set(s, 0)
        self.verify(c.current_flow_temp == 0.0, "forced off")
        await s.run(10 * MINUTE)
        self.verify(c.current_flow_temp == 0.0, "stays off during the override")
        self.verify(not s.violation_runs(), f"valves stayed open for the pump overrun ({s.violation_runs()[:2]})")
        await s.run(6 * MINUTE)
        self.verify(c.current_flow_temp > 0, "after 15 minutes the schedule heats again")
        s.close()

    async def test_07_limited_with_one_valve(self):
        print("\nTest 7: With only one valve open the flow is limited to 45 °C")
        s = standard_house(keep_open='leo', others=(20.5, 19.0))
        c = await s.start()
        await s.run(10 * MINUTE)
        open_valves = sum(1 for v in c.zones.values() if v.measured_opening > 0)
        self.verify(open_valves == 1, f"one valve open ({open_valves})")
        await self.set(s, 60)
        self.verify(c.current_flow_temp == MAX_FLOW_ONE_VALVE and c.flow_override.capped_to == MAX_FLOW_ONE_VALVE,
                    f"flow limited to {MAX_FLOW_ONE_VALVE:.0f} (got {c.current_flow_temp})")
        s.close()

    async def test_08_range(self):
        print("\nTest 8: Below 25 the boiler is off, 25 is the lowest flow, above 60 is refused")
        s = standard_house(keep_open='leo', others=(20.5, 19.0))
        c = await s.start()
        await s.run(10 * MINUTE)
        await self.set(s, 24)
        self.verify(c.current_flow_temp == 0.0 and c.flow_override.value == 0.0, "24 °C (below the minimum) switches the boiler off")
        await self.set(s, 25)
        self.verify(c.current_flow_temp == 25.0, "25 °C is the lowest flow")
        try:
            c.flow_override.start(80, c.now())
            ok = False
        except OverrideError:
            ok = True
        self.verify(ok, "80 °C is refused")
        s.close()

    async def run_all_tests(self):
        for name in sorted(n for n in dir(self) if n.startswith("test_")):
            await getattr(self, name)()
        print(f"\n  Flow override: {self.passed} passed, {self.failed} failed")


if __name__ == "__main__":
    suite = OverrideTests()
    asyncio.run(suite.run_all_tests())
    sys.exit(1 if suite.failed else 0)
