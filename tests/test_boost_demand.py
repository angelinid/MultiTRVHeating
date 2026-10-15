"""
Boost counts as a heat request: a boosted zone asks for heat whatever its temperature error,
and its demand is its (measured) opening. Run on the physical simulation (sim_house.py).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))
sys.path.insert(0, str(Path(__file__).parent))

from sim_house import SimHouse
from zone_wrapper import default_boost_entity_id
from master_controller import demand_to_flow_temp

MINUTE = 60


def house(high_boosted: bool = False) -> SimHouse:
    """Four rooms, all at or above their target (nothing wants heat); 'kitchen' is the one we boost."""
    s = SimHouse(discharge='leo', hold_writes=True)
    s.add('leo', temp=20.5, setpoint=19.0, high=False)
    s.add('living', temp=20.5, setpoint=19.0, high=False)
    s.add('kitchen', temp=22.0, setpoint=19.0, high=high_boosted)
    s.add('bath', temp=20.5, setpoint=19.0, high=False)
    return s


class BoostDemandTests:
    def __init__(self):
        import logging
        logging.getLogger('don_controller').setLevel(logging.CRITICAL)
        self.passed = 0
        self.failed = 0

    def verify(self, condition: bool, message: str) -> None:
        if condition:
            self.passed += 1
            print(f"    ✓ {message}")
        else:
            self.failed += 1
            print(f"    ✗ {message}")

    async def test_01_entity_id_derivation(self):
        print("\nTest 1: Boost switch entity derived from the climate entity")
        self.verify(default_boost_entity_id('climate.radiator_1_bedroom') == 'switch.radiator_1_bedroom_boost_heating', "Z2M naming")
        self.verify(default_boost_entity_id('sensor.foo') is None and default_boost_entity_id(None) is None, "no climate prefix -> none")

    async def test_02_boost_in_a_satisfied_low_priority_room_starts_the_boiler(self):
        print("\nTest 2: Boost in a room above target (low priority) - boiler on, full demand")
        s = house()
        c = await s.start()
        await s.run(10 * MINUTE)
        self.verify(c.current_flow_temp == 0 and s.boiler_on_seconds == 0, "idle house: boiler off")
        await s.set_boost('kitchen', True)
        await s.run(5 * MINUTE)
        self.verify(s.valves['kitchen'].position == 100, "boosted valve fully open")
        self.verify(c.zones['climate.kitchen'].boosted, "zone knows it is boosted")
        self.verify(abs(c.current_flow_temp - demand_to_flow_temp(1.0)) < 0.01, f"flow request at full demand ({c.current_flow_temp})")
        self.verify(s.boiler_on_seconds > 0, "boiler ran")
        s.close()

    async def test_03_boost_in_a_high_priority_room_with_negative_error(self):
        print("\nTest 3: Boost in a high-priority room above target - counts as demand")
        s = house(high_boosted=True)
        c = await s.start()
        await s.run(10 * MINUTE)
        self.verify(c.current_flow_temp == 0, "idle house: boiler off")
        await s.set_boost('kitchen', True)
        await s.run(5 * MINUTE)
        self.verify(abs(c.current_flow_temp - demand_to_flow_temp(1.0)) < 0.01, f"flow request at full demand ({c.current_flow_temp})")
        s.close()

    async def test_04_boost_adds_to_a_small_demand_elsewhere(self):
        print("\nTest 4: Boost plus a 25 % room - the flow follows the boost, not the small demand")
        s = house()
        s.valves['bath'].room.temp = 18.5            # bath wants a little heat
        s.valves['bath'].setpoint = 19.0
        c = await s.start()
        await s.run(10 * MINUTE)
        await s.set_boost('kitchen', True)
        await s.run(5 * MINUTE)
        self.verify(abs(c.current_flow_temp - demand_to_flow_temp(1.0)) < 0.01, f"60 degree request instead of the small one ({c.current_flow_temp})")
        s.close()

    async def test_05_boost_ends(self):
        print("\nTest 5: Boost ends - valve closes, boiler stops")
        s = house()
        c = await s.start()
        await s.run(10 * MINUTE)
        await s.set_boost('kitchen', True)
        await s.run(5 * MINUTE)
        await s.set_boost('kitchen', False)
        await s.run(15 * MINUTE)
        self.verify(s.valves['kitchen'].position == 0, "valve closed")
        self.verify(c.current_flow_temp == 0, "boiler request back to 0")
        self.verify(not c.zones['climate.kitchen'].boosted, "zone no longer boosted")
        s.close()

    async def test_06_unavailable_or_missing_switch_is_not_a_boost(self):
        print("\nTest 6: Boost switch unavailable/unknown - not boosted")
        s = house()
        c = await s.start()
        await s.run(5 * MINUTE)
        await s.set_boost('kitchen', True)
        await s.run(60)
        self.verify(c.zones['climate.kitchen'].boosted, "boosted")
        await s._emit(c._async_boost_change, s.valves['kitchen'].boost_id, 'unavailable')
        self.verify(not c.zones['climate.kitchen'].boosted, "unavailable switch counts as off")
        s.close()

    async def test_07_boost_already_on_at_startup(self):
        print("\nTest 7: Boost already on when HA starts - seeded from the switch state")
        s = house()
        c = await s.start(prep=lambda: s._set(s.valves['kitchen'].boost_id, 'on'))
        self.verify(c.zones['climate.kitchen'].boosted, "zone seeded as boosted")
        s.close()

    async def run_all_tests(self):
        for name in sorted(n for n in dir(self) if n.startswith("test_")):
            await getattr(self, name)()
        print(f"\n  Boost demand: {self.passed} passed, {self.failed} failed")


if __name__ == "__main__":
    suite = BoostDemandTests()
    asyncio.run(suite.run_all_tests())
    sys.exit(1 if suite.failed else 0)
