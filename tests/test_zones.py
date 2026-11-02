"""
Multi-valve zones: grouping, the zone temperature (average of the valves), valve availability,
demand counted once per zone, the zone target (set on all valves, adopted from a valve, no
feedback loop), boost on all valves and the keep-open valve being an individual valve.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))
sys.path.insert(0, str(Path(__file__).parent))

from keep_open import KeepOpenController
from master_controller import MasterController
from mock_ha import MockEvent, MockHass, MockState
from zone_wrapper import HOLD_AUTO, HOLD_SWITCH, HOLD_TEMP_OFFSET
from zones_config import apply_selected_sensors, describe_groups, group_zone_configs, normalize_zone_configs

FLAT = [
    {'entity_id': 'climate.a1', 'name': 'Bed 1', 'area': 35.0, 'is_high_priority': False,
     'trv_position_entity_id': 'sensor.a1_pos', 'temp_calib_entity_id': 'number.a1_cal',
     'ext_temp_entity_id': 'sensor.bed'},
    {'entity_id': 'climate.k', 'name': 'Kitchen', 'area': 15.0, 'is_high_priority': True,
     'trv_position_entity_id': 'sensor.k_pos', 'temp_calib_entity_id': 'number.k_cal'},
    {'entity_id': 'climate.a2', 'name': 'Bed 2', 'area': 30.0, 'is_high_priority': True,
     'trv_position_entity_id': 'sensor.a2_pos', 'temp_calib_entity_id': 'number.a2_cal',
     'ext_temp_entity_id': 'sensor.bed'},
    {'entity_id': 'climate.l', 'name': 'Leo', 'area': 20.0, 'is_high_priority': True,
     'trv_position_entity_id': 'sensor.l_pos', 'temp_calib_entity_id': 'number.l_cal',
     'ext_temp_entity_id': 'sensor.leo'},
]


def room_config(ext=None, priority=True, n=2):
    valves = [
        {'entity_id': f'climate.v{i}', 'name': f'Valve {i}', 'trv_position_entity_id': f'sensor.v{i}_pos',
         'temp_calib_entity_id': f'number.v{i}_cal', 'boost_entity_id': f'switch.v{i}_boost'}
        for i in range(1, n + 1)
    ]
    return [{'zone_id': 'climate.v1', 'name': 'Room', 'area': 20.0, 'is_high_priority': priority,
             'ext_temp_entity_id': ext, 'valves': valves}]


class Services:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data=None, blocking=False):
        self.calls.append((domain, service, dict(data or {})))

    def of(self, domain, service):
        return [d for dom, svc, d in self.calls if dom == domain and svc == service]


class ZoneTests:
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

    def make(self, zones, n_states=None):
        hass = MockHass()
        hass.services = Services()
        ctl = MasterController(hass, zones)
        self.t = 0.0
        ctl._clock = lambda: self.t
        ctl.valve_open_delay = 0
        ctl.component_enabled = True
        return hass, ctl

    async def climate(self, hass, ctl, vid, temp, target, state='heat'):
        attrs = {'current_temperature': temp, 'temperature': target} if state not in ('unavailable', 'unknown') else {}
        new = MockState(entity_id=vid, state=state, attributes=attrs)
        hass.states[vid] = new
        await ctl._async_climate_state_change(MockEvent(data={'entity_id': vid, 'new_state': new}))

    async def pos(self, hass, ctl, vid, value):
        new = MockState(entity_id=vid, state=str(value), attributes={})
        hass.states[vid] = new
        await ctl._async_position_change(MockEvent(data={'entity_id': vid, 'new_state': new}))

    # --- grouping ---------------------------------------------------------

    def test_01_grouping(self):
        print("\nTest 1: Valves that share an external sensor become one zone")
        zones = group_zone_configs(FLAT)
        self.verify([z['name'] for z in zones] == ['Bed 1', 'Kitchen', 'Leo'], "three zones, in order of first appearance")
        bed = zones[0]
        self.verify([v['entity_id'] for v in bed['valves']] == ['climate.a1', 'climate.a2'], "the two bedrooms share a zone")
        self.verify(bed['area'] == 35.0 and bed['is_high_priority'] is True, "largest area, high priority if any valve is")
        self.verify(bed['zone_id'] == 'climate.a1', "zone id is the first valve's climate entity")
        self.verify(len(describe_groups(zones)) == 1, "one merge is reported")
        self.verify(normalize_zone_configs(zones) == zones, "an already grouped config is left alone")
        self.verify(len(group_zone_configs(group_zone_configs(FLAT)[0:0])) == 0, "empty config is fine")

    def test_01b_sensors_chosen_in_the_ui_are_used_for_grouping(self):
        print("\nTest 1b: The sensor chosen with the select entity (storage) decides the grouping")
        stored = {
            'zone_ext_temp_select_E_kitchen_ext_temp_select': 'sensor.leo',   # kitchen now uses Leo's sensor
            'zone_ext_temp_select_E_bed_2_ext_temp_select': '',               # bedroom 2's sensor removed
        }
        zones = group_zone_configs(apply_selected_sensors(FLAT, stored, 'E'))
        by_name = {z['name']: [v['entity_id'] for v in z['valves']] for z in zones}
        self.verify(by_name.get('Kitchen') == ['climate.k', 'climate.l'], "kitchen joins the zone of the sensor it was given")
        self.verify(by_name.get('Bed 1') == ['climate.a1'] and 'Bed 2' in by_name, "a removed sensor leaves the zone")
        self.verify(FLAT[2]['ext_temp_entity_id'] == 'sensor.bed', "the input config is not modified")

    # --- temperature ------------------------------------------------------

    async def test_02_average_without_external_sensor(self):
        print("\nTest 2: Without an external sensor the zone temperature is the average of the valves")
        hass, ctl = self.make(room_config())
        await self.climate(hass, ctl, 'climate.v1', 18.0, 20.0)
        self.verify(ctl.rooms['climate.v1'].current_temp == 18.0, "one valve reported: its reading")
        await self.climate(hass, ctl, 'climate.v2', 20.0, 20.0)
        zone = ctl.rooms['climate.v1']
        self.verify(abs(zone.current_temp - 19.0) < 1e-9, f"average of 18 and 20 = 19 (got {zone.current_temp})")
        self.verify(all(abs(v.current_error - 1.0) < 1e-9 for v in zone.valves), "both valves use the zone temperature for their error")

    async def test_03_offset_removed_per_valve(self):
        print("\nTest 3: Each valve's own calibration is removed before averaging")
        hass, ctl = self.make(room_config())
        ctl.zones['climate.v1'].device_offset = -2.0
        await self.climate(hass, ctl, 'climate.v1', 18.0, 20.0)   # real 20
        await self.climate(hass, ctl, 'climate.v2', 22.0, 20.0)   # real 22
        self.verify(abs(ctl.rooms['climate.v1'].current_temp - 21.0) < 1e-9, "average of the real temperatures (20, 22)")

    async def test_04_external_sensor_wins_for_all_valves(self):
        print("\nTest 4: The external sensor is shared by all valves of the zone")
        hass, ctl = self.make(room_config(ext='sensor.room'))
        await self.climate(hass, ctl, 'climate.v1', 18.0, 20.0)
        await self.climate(hass, ctl, 'climate.v2', 18.0, 20.0)
        new = MockState(entity_id='sensor.room', state='19.5', attributes={})
        hass.states['sensor.room'] = new
        await ctl._async_external_temp_change(MockEvent(data={'entity_id': 'sensor.room', 'new_state': new}))
        self.verify(all(v.current_temp == 19.5 for v in ctl.zones.values()), "every valve uses the sensor reading")
        self.verify(ctl.monitored_external_sensors == ['sensor.room'], "the sensor is listened to once")

    async def test_05_unavailable_valve_ignored(self):
        print("\nTest 5: An unavailable valve is ignored while another one is available")
        hass, ctl = self.make(room_config())
        await self.climate(hass, ctl, 'climate.v1', 16.0, 20.0)
        await self.climate(hass, ctl, 'climate.v2', 20.0, 20.0)
        await self.climate(hass, ctl, 'climate.v1', 0, 0, state='unavailable')
        zone = ctl.rooms['climate.v1']
        self.verify(zone.active_valves == [ctl.zones['climate.v2']], "only the available valve counts")
        self.verify(abs(zone.current_temp - 20.0) < 1e-9, "zone temperature from the available valve alone")
        await self.climate(hass, ctl, 'climate.v2', 0, 0, state='unavailable')
        self.verify(len(zone.active_valves) == 2, "with none available the zone keeps its last values")

    # --- demand -----------------------------------------------------------

    async def test_06_low_priority_counted_once(self):
        print("\nTest 6: A low-priority zone with two 60 % valves is 60 %, not 120 %")
        hass, ctl = self.make(room_config(priority=False))
        for v in ('v1', 'v2'):
            await self.climate(hass, ctl, f'climate.{v}', 18.0, 20.0)
            await self.pos(hass, ctl, f'sensor.{v}_pos', 60)
        on, _, reason = ctl._evaluate_boiler_demand()
        self.verify(not on, f"no boiler request from one zone at 60 % ({reason})")
        await self.pos(hass, ctl, 'sensor.v2_pos', 100)
        on, _, reason = ctl._evaluate_boiler_demand()
        self.verify(on, f"the zone at its fullest valve (100 %) asks for heat ({reason})")

    async def test_07_opening_is_max_and_area_once(self):
        print("\nTest 7: Opening is the maximum; the area is counted once")
        hass, ctl = self.make(room_config())
        await self.climate(hass, ctl, 'climate.v1', 18.0, 20.0)
        await self.climate(hass, ctl, 'climate.v2', 18.0, 20.0)
        await self.pos(hass, ctl, 'sensor.v1_pos', 30)
        await self.pos(hass, ctl, 'sensor.v2_pos', 70)
        state = ctl.rooms['climate.v1'].export_zone_state()
        self.verify(state['trv_opening_percent'] == 70.0, "zone opening = max")
        self.verify(abs(ctl.preheating._get_max_high_priority_thermal_load() - 2.0 * 20.0) < 1e-6,
                    "pre-heating load = error x area, the area counted once")
        self.verify(len(ctl.rooms) == 1 and len(ctl.zones) == 2, "one zone, two valves")

    # --- target -----------------------------------------------------------

    async def test_08_set_target_goes_to_all_valves(self):
        print("\nTest 8: Setting the zone target writes it to every valve")
        hass, ctl = self.make(room_config())
        zone = ctl.rooms['climate.v1']
        await self.climate(hass, ctl, 'climate.v1', 18.0, 16.0)
        await self.climate(hass, ctl, 'climate.v2', 18.0, 16.0)
        hass.services.calls.clear()
        await ctl.async_set_zone_target(zone, 19.0)
        sent = hass.services.of('climate', 'set_temperature')
        self.verify({d['entity_id'] for d in sent} == {'climate.v1', 'climate.v2'} and all(d['temperature'] == 19.0 for d in sent),
                    "both valves get 19")
        self.verify(all(v.current_error == 19.0 - v.current_temp for v in zone.valves), "decisions use the new target at once")

    async def test_09_no_feedback_loop(self):
        print("\nTest 9: A valve reporting its old setpoint after our write does not undo it")
        hass, ctl = self.make(room_config())
        zone = ctl.rooms['climate.v1']
        await self.climate(hass, ctl, 'climate.v1', 18.0, 16.0)
        await self.climate(hass, ctl, 'climate.v2', 18.0, 16.0)
        await ctl.async_set_zone_target(zone, 19.0)
        hass.services.calls.clear()
        self.t += 60
        await self.climate(hass, ctl, 'climate.v1', 18.0, 16.0)       # stale report of the old value
        self.verify(zone.desired_target == 19.0 and not hass.services.of('climate', 'set_temperature'),
                    "stale report ignored, nothing rewritten")
        await self.climate(hass, ctl, 'climate.v1', 18.0, 19.0)
        await self.climate(hass, ctl, 'climate.v2', 18.0, 19.0)
        self.verify(not zone.pending_targets, "the confirmations clear the pending writes")

    async def test_10_outside_change_adopted(self):
        print("\nTest 10: A valve changed from outside sets the zone target; the other valve follows")
        hass, ctl = self.make(room_config())
        zone = ctl.rooms['climate.v1']
        await self.climate(hass, ctl, 'climate.v1', 18.0, 16.0)
        await self.climate(hass, ctl, 'climate.v2', 18.0, 16.0)
        hass.services.calls.clear()
        await self.climate(hass, ctl, 'climate.v2', 18.0, 21.0)       # dial turned
        self.verify(zone.desired_target == 21.0, "the zone adopted 21")
        sent = hass.services.of('climate', 'set_temperature')
        self.verify([d['entity_id'] for d in sent] == ['climate.v1'] and sent[0]['temperature'] == 21.0,
                    "only the other valve was written")

    async def test_11_returning_valve_resynced(self):
        print("\nTest 11: A valve that was offline is brought to the zone target, not adopted")
        hass, ctl = self.make(room_config())
        zone = ctl.rooms['climate.v1']
        await self.climate(hass, ctl, 'climate.v1', 18.0, 16.0)
        await self.climate(hass, ctl, 'climate.v2', 18.0, 16.0)
        await self.climate(hass, ctl, 'climate.v2', 0, 0, state='unavailable')
        await ctl.async_set_zone_target(zone, 19.0)
        sent = hass.services.of('climate', 'set_temperature')
        self.verify('climate.v2' not in {d['entity_id'] for d in sent[-1:]} or len(sent) == 1, "the unavailable valve is skipped")
        hass.services.calls.clear()
        await self.climate(hass, ctl, 'climate.v2', 18.0, 16.0)       # back, old setpoint
        self.verify(zone.desired_target == 19.0, "the zone target was not overwritten by the old setpoint")
        sent = hass.services.of('climate', 'set_temperature')
        self.verify([d['entity_id'] for d in sent] == ['climate.v2'] and sent[0]['temperature'] == 19.0,
                    "the returning valve is written the zone target")

    # --- boost, hold, keep-open ---------------------------------------------

    async def test_12_boost_all_valves_only_when_actioned(self):
        print("\nTest 12: Boosting the zone boosts all its valves; a boost from outside still counts")
        hass, ctl = self.make(room_config())
        zone = ctl.rooms['climate.v1']
        await ctl.async_boost_zone(zone)
        on = {d['entity_id'] for d in hass.services.of('switch', 'turn_on')}
        self.verify(on == {'switch.v1_boost', 'switch.v2_boost'}, "both boost switches turned on")
        hass.services.calls.clear()
        await self.climate(hass, ctl, 'climate.v1', 21.0, 20.0)
        await self.climate(hass, ctl, 'climate.v2', 21.0, 20.0)
        self.verify(not hass.services.of('switch', 'turn_on'), "nothing boosts by itself")
        hass.states['switch.v2_boost'] = MockState(entity_id='switch.v2_boost', state='on', attributes={})
        self.verify(zone.boosted, "one valve boosting (e.g. from the TRV button) makes the zone boosted")

    async def test_13_hold_switch_holds_one_valve(self):
        print("\nTest 13: A Hold Open switch holds its own valve only")
        hass, ctl = self.make(room_config())
        v1, v2 = ctl.zones['climate.v1'], ctl.zones['climate.v2']
        await ctl.async_set_zone_hold(v2, HOLD_SWITCH, True)
        self.verify(v2.has_hold(HOLD_SWITCH) and not v1.held, "only the second valve is held")
        await ctl.async_set_zone_hold(v2, HOLD_SWITCH, False)
        self.verify(not (v1.held or v2.held), "released")

    async def test_14_keep_open_targets_one_valve(self):
        print("\nTest 14: Keep-open holds the selected valve only, not its sibling")
        cfg = room_config()
        hass, ctl = self.make(cfg)
        ctl.keep_open.update_config('climate.v2', 'Valve 2')
        for v in ('v1', 'v2'):
            hass.states[f'number.{v}_cal'] = MockState(entity_id=f'number.{v}_cal', state='0.0', attributes={})
            await self.climate(hass, ctl, f'climate.{v}', 20.0, 20.0)
            await self.pos(hass, ctl, f'sensor.{v}_pos', 0)
        ctl._settled = True
        ctl.keep_open.update(self.t)
        held = [v.name for v in ctl.zones.values() if v.has_hold(HOLD_AUTO)]
        self.verify(held == ['Valve 2'], f"only the selected valve is held (held: {held})")

    async def run_all_tests(self):
        for name in sorted(n for n in dir(self) if n.startswith("test_")):
            res = getattr(self, name)()
            if hasattr(res, '__await__'):
                await res
        print(f"\n  Multi-valve zones: {self.passed} passed, {self.failed} failed")


if __name__ == "__main__":
    suite = ZoneTests()
    asyncio.run(suite.run_all_tests())
    sys.exit(1 if suite.failed else 0)
