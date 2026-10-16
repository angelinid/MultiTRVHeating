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

    async def test_01_boost_switch_found_through_the_entity_registry(self):
        print("\nTest 1: Boost switch is the 'Boost heating' switch on the climate entity's device")
        import types
        from collections import namedtuple
        Entry = namedtuple('Entry', 'entity_id domain device_id original_name')
        entries = [
            Entry('climate.radiator_x', 'climate', 'dev1', None),
            Entry('sensor.some_other_name_countdown', 'sensor', 'dev1', 'Duration'),
            Entry('switch.some_other_name', 'switch', 'dev1', 'Boost heating'),
            Entry('climate.radiator_y', 'climate', 'dev2', None),
        ]

        class Registry:
            def async_get(self, eid):
                return next((e for e in entries if e.entity_id == eid), None)
        er = types.ModuleType('homeassistant.helpers.entity_registry')
        er.async_get = lambda hass: Registry()
        er.async_entries_for_device = lambda reg, dev: [e for e in entries if e.device_id == dev]
        saved = {k: sys.modules.get(k) for k in ('homeassistant', 'homeassistant.helpers', 'homeassistant.helpers.entity_registry')}
        ha, helpers = types.ModuleType('homeassistant'), types.ModuleType('homeassistant.helpers')
        helpers.entity_registry = er
        sys.modules.update({'homeassistant': ha, 'homeassistant.helpers': helpers, 'homeassistant.helpers.entity_registry': er})
        try:
            s = house()
            c = await s.start()
            self.verify(c._find_boost_switch('climate.radiator_x') == 'switch.some_other_name', "found by device, whatever the names")
            self.verify(c._find_boost_switch('climate.radiator_y') is None, "device without a boost switch -> none")
            self.verify(c._find_boost_switch('climate.unknown') is None, "unknown entity -> none")
            s.close()
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

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
        print("\nTest 5: Boost ends - valve closes, boiler stops, never unsafe")
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
        runs = s.violation_runs()
        self.verify(not runs, f"pump never circulates with every valve shut (runs {runs[:3]})")
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

    async def test_08_hold_unaffected_by_boost(self):
        print("\nTest 8: Keep-open hold stays while another zone is boosted")
        s = house()
        c = await s.start()
        await s.run(10 * MINUTE)
        await s.set_boost('kitchen', True)
        await s.run(5 * MINUTE)
        self.verify(s.valves['leo'].position > 0, "keep-open valve still open")
        s.close()

    async def test_09_boost_ending_never_leaves_every_valve_shut_while_pumping(self):
        print("\nTest 9: Boost ends right after the boiler ran - the held valve covers the pump overrun")
        s = house()
        c = await s.start()
        await s.run(10 * MINUTE)
        await s.set_boost('kitchen', True)
        await s.run(5 * MINUTE)
        await s.set_boost('kitchen', False)
        await s.run(10 * MINUTE)
        self.verify(s.valves['leo'].position == 100, "keep-open valve fully open after the boost")
        runs = s.violation_runs()
        self.verify(not runs, f"no unsafe period (runs {runs[:3]})")
        s.close()

    async def run_all_tests(self):
        for name in sorted(n for n in dir(self) if n.startswith("test_")):
            await getattr(self, name)()
        print(f"\n  Boost demand: {self.passed} passed, {self.failed} failed")


if __name__ == "__main__":
    suite = BoostDemandTests()
    asyncio.run(suite.run_all_tests())
    sys.exit(1 if suite.failed else 0)
