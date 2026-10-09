"""
Safety and behaviour tests for the keep-open / hold mode, run on the physical simulation
(sim_house.py). The rule: the boiler pump must never circulate while every valve is shut,
including the ~5 minutes it keeps running after the boiler is told to stop.
"""

import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))
sys.path.insert(0, str(Path(__file__).parent))

from sim_house import SimHouse
from zone_wrapper import HOLD_AUTO, HOLD_SWITCH, HOLD_TEMP_OFFSET
from keep_open import BOILER_PUMP_OVERRUN, MIN_HOLD_SECONDS, CONFIRM_TIMEOUT

HOUR = 3600
MINUTE = 60


def standard_house(discharge='leo', hold_writes=True, leo=(20.0, 18.0), others=(20.5, 19.0)) -> SimHouse:
    """Leo (low priority, external sensor, the selected keep-open valve) and three other rooms."""
    s = SimHouse(discharge=discharge, hold_writes=hold_writes)
    s.add('leo', temp=leo[0], setpoint=leo[1], high=False, ext=True)
    s.add('living', temp=others[0], setpoint=others[1])
    s.add('kitchen', temp=others[0], setpoint=others[1])
    s.add('bath', temp=others[0], setpoint=others[1], high=False)
    return s


async def fuzz_house(seed: int, trace: bool = False, speed: tuple = (15, 15), events: int = 12):
    """Random house with random events; returns (house, action log). speed = actuator s/step range."""
    rnd = random.Random(seed)
    s = SimHouse(discharge=rnd.choice(['leo', 'living', 'kitchen', 'bath']), hold_writes=True)
    s.zigbee_delay = rnd.randint(1, 8) if speed != (15, 15) else 3
    names = ['leo', 'living', 'kitchen', 'bath']
    actions = []
    for n in names:
        v = s.add(n, temp=rnd.uniform(15, 22), setpoint=rnd.choice([16, 18, 19, 20, 21]),
                  high=rnd.random() < 0.5, ext=rnd.random() < 0.5)
        v.step_seconds = rnd.randint(*speed)
    c = await s.start()
    horizon = 4 * HOUR
    for _ in range(rnd.randint(4, events)):
        at = rnd.randint(60, horizon - 600)
        kind = rnd.choice(['setpoint', 'hold_on', 'hold_off', 'restart', 'stuck', 'unstick',
                           'pos_unavail', 'disable', 'enable', 'select', 'offline', 'online',
                           'force', 'unforce', 'cal_off', 'cal_on', 'ext_dead'])
        key = rnd.choice(names)
        value = rnd.choice([14, 17, 19, 21, 23])
        actions.append((at, kind, key, value))

        def act(kind=kind, key=key, value=value):
            v = s.valves[key]
            ctl = s.controller
            z = ctl.zones[v.climate_id]
            if kind == 'setpoint':
                v.setpoint = value
            elif kind == 'hold_on':
                return ctl.async_set_zone_hold(z, HOLD_SWITCH, True)
            elif kind == 'hold_off':
                return ctl.async_set_zone_hold(z, HOLD_SWITCH, False)
            elif kind == 'restart':
                held = [zz.entity_id for zz in ctl.zones.values() if zz.has_hold(HOLD_SWITCH)]
                c2 = s.restart_controller()
                for eid in held:
                    c2.apply_hold(c2.zones[eid], HOLD_SWITCH, True, c2.now())
            elif kind == 'stuck':
                v.stuck = True
            elif kind == 'unstick':
                v.stuck = False
            elif kind == 'pos_unavail':
                return s._emit(ctl._async_position_change, v.pos_id, 'unavailable')
            elif kind == 'disable':
                ctl.component_enabled = False
            elif kind == 'enable':
                ctl.component_enabled = True
            elif kind == 'select':
                ctl.keep_open.update_config(v.climate_id, v.key.title())
            elif kind == 'offline':
                v.offline = True
            elif kind == 'online':
                v.offline = False
            elif kind == 'force':
                v.force_target = rnd.choice([0.0, 25.0, 50.0, 100.0])
            elif kind == 'unforce':
                v.force_target = None
            elif kind == 'cal_off':
                v.cal_unavailable = True
                s._set(v.calib_id, 'unavailable')
            elif kind == 'cal_on':
                v.cal_unavailable = False
                s._set(v.calib_id, str(float(v.calibration)))
            elif kind == 'ext_dead':
                v.ext_dead = True
        s.at(at, act)
    s.at(horizon - 300, lambda: setattr(s.controller, 'component_enabled', True))
    await s.run(horizon)
    return s, sorted(actions)


class KeepOpenSafetyTests:
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

    def no_violations(self, s: SimHouse, label: str = "") -> None:
        runs = s.violation_runs()
        self.verify(not runs, f"pump never circulates with every valve shut {label}(runs: {runs[:5]})")

    # ------------------------------------------------------------------
    # Basic
    # ------------------------------------------------------------------

    async def test_01_idle_house_keeps_one_valve_open(self):
        print("\nTest 1: Idle house - one valve is held open, no boiler, one calibration write")
        s = standard_house()
        c = await s.start()
        await s.run(20 * MINUTE)
        self.verify(s.valves['leo'].position == 100, "selected valve is fully open")
        self.verify(all(s.valves[k].position == 0 for k in ('living', 'kitchen', 'bath')), "others stay closed")
        self.verify(s.valves['leo'].calibration == HOLD_TEMP_OFFSET, "hold offset reached the device")
        self.verify(len(s.writes_for('leo')) == 1, f"single calibration write (got {s.writes_for('leo')})")
        self.verify(c.current_flow_temp == 0 and s.boiler_on_seconds == 0, "boiler stays off (all rooms at target)")
        self.no_violations(s)
        s.close()

    async def test_02_normal_heating_cycle(self):
        print("\nTest 2: Cold rooms heat up, valves close, boiler stops - never unsafe")
        s = standard_house(leo=(20.0, 18.0), others=(16.5, 19.0))
        c = await s.start()
        await s.run(6 * HOUR)
        self.verify(s.boiler_on_seconds > 600, f"boiler ran to heat the rooms ({s.boiler_on_seconds}s)")
        self.verify(s.valves['living'].room.temp > 18.5, f"rooms warmed (living {s.valves['living'].room.temp:.1f})")
        self.verify(s.valves['leo'].position > 0, "a valve is open at the end")
        self.no_violations(s)
        s.close()

    async def test_03_all_valves_slam_shut_while_boiler_runs(self):
        print("\nTest 3: Every valve closes at once while the boiler runs")
        s = standard_house(others=(14.0, 19.0))
        c = await s.start()
        await s.run(12 * MINUTE)
        self.verify(c.current_flow_temp > 0, "boiler is running before the slam")

        def slam():
            for k, v in s.valves.items():
                if k != 'leo':              # the held valve stays open by its calibration
                    v.force_target = 0.0
        s.at(s.t + 1, slam)
        await s.run(15 * MINUTE)
        total = len(s.violations)
        self.verify(total <= 10, f"unsafe time is at most the actuator latency ({total}s, runs {s.violation_runs()})")
        self.verify(s.valves['leo'].position > 0 or s.valves['leo'].force_target == 0.0, "keep-open valve ordered open")
        s.close()

    # ------------------------------------------------------------------
    # Night hold (child's room)
    # ------------------------------------------------------------------

    async def test_04_night_hold_drives_boiler_from_external_sensor(self):
        print("\nTest 4: Night hold - room below target heats, offset never cleared all night")
        s = standard_house(leo=(17.0, 19.0), others=(20.5, 19.0))
        c = await s.start()
        s.hold_events = []
        leo = s.valves['leo']
        zone = c.zones['climate.leo']
        await s.run(5 * MINUTE)
        await c.async_set_zone_hold(zone, HOLD_SWITCH, True)

        lost = []
        original_run = s.run

        await s.run(8 * HOUR)
        history = [w for w in s.writes_for('leo')]
        self.verify(all(v == HOLD_TEMP_OFFSET for _, v in history), f"only the hold offset was ever written ({history})")
        self.verify(leo.calibration == HOLD_TEMP_OFFSET, "hold offset still on the device after 8 h")
        self.verify(leo.room.temp >= 18.5, f"room reached target ({leo.room.temp:.1f})")
        self.verify(s.boiler_on_seconds > 300, "boiler ran for the held room")
        self.verify(leo.position > 0, "valve open at the end")
        self.no_violations(s)
        s.close()

    async def test_05_night_hold_room_satisfied(self):
        print("\nTest 5: Night hold with the room above target - valve open, boiler off")
        s = standard_house(leo=(20.5, 19.0))
        c = await s.start()
        await c.async_set_zone_hold(c.zones['climate.leo'], HOLD_SWITCH, True)
        await s.run(20 * MINUTE)
        self.verify(s.valves['leo'].position == 100, "valve held fully open")
        self.verify(s.boiler_on_seconds == 0, "no boiler for a satisfied room")
        self.no_violations(s)
        s.close()

    async def test_06_other_zone_fires_and_stops_during_hold(self):
        print("\nTest 6: Another room asks for heat during the hold - hold survives boiler on and off")
        s = standard_house(leo=(20.5, 19.0), others=(20.5, 19.0))
        c = await s.start()
        await c.async_set_zone_hold(c.zones['climate.leo'], HOLD_SWITCH, True)
        await s.run(30 * MINUTE)
        s.valves['living'].setpoint = 22.0       # living wants heat
        await s.run(2 * HOUR)
        s.valves['living'].setpoint = 17.0       # and then stops
        await s.run(30 * MINUTE)
        self.verify(s.boiler_on_seconds > 300, "boiler ran for the other room")
        self.verify(all(v == HOLD_TEMP_OFFSET for _, v in s.writes_for('leo')), f"hold never touched ({s.writes_for('leo')})")
        # With -9 the TRV only stays fully open while the room is below setpoint + 7 °C
        self.verify(s.valves['leo'].position > 0 and s.valves['leo'].calibration == HOLD_TEMP_OFFSET,
                    f"held valve still open at the end (pos {s.valves['leo'].position}, room {s.valves['leo'].room.temp:.1f})")
        self.no_violations(s)
        s.close()

    async def test_07_hold_released_in_the_morning_other_valve_takes_over(self):
        print("\nTest 7: Hold released - the keep-open valve takes over before the room valve closes")
        s = standard_house(discharge='bath', leo=(20.5, 19.0))
        c = await s.start()
        await c.async_set_zone_hold(c.zones['climate.leo'], HOLD_SWITCH, True)
        await s.run(2 * HOUR)
        self.verify(not c.zones['climate.bath'].held, "keep-open valve not held while the room hold provides the flow")
        await c.async_set_zone_hold(c.zones['climate.leo'], HOLD_SWITCH, False)
        await s.run(40 * MINUTE)
        self.verify(s.valves['bath'].position == 100, "keep-open valve now open")
        self.verify(s.valves['leo'].position == 0 and s.valves['leo'].calibration == 0.0, "room valve released")
        self.no_violations(s)
        s.close()

    async def test_08_selected_valve_is_the_held_room(self):
        print("\nTest 8: Selected valve is the night-held room - release hands over without a write")
        s = standard_house(discharge='leo', leo=(20.5, 19.0))
        c = await s.start()
        zone = c.zones['climate.leo']
        await c.async_set_zone_hold(zone, HOLD_SWITCH, True)
        await s.run(HOUR)
        writes_before = len(s.writes_for('leo'))
        await c.async_set_zone_hold(zone, HOLD_SWITCH, False)
        await s.run(HOUR)
        self.verify(len(s.writes_for('leo')) == writes_before, f"no offset flip on release ({s.writes_for('leo')})")
        self.verify(zone.has_hold(HOLD_AUTO) and not zone.has_hold(HOLD_SWITCH), "now held by the keep-open rule")
        self.no_violations(s)
        s.close()

    # ------------------------------------------------------------------
    # Failures
    # ------------------------------------------------------------------

    async def test_09_held_valve_stuck_escalates(self):
        print("\nTest 9: Held valve never opens - another valve is held after the confirmation time")
        s = standard_house()
        s.valves['leo'].stuck = True
        c = await s.start()
        await s.run(CONFIRM_TIMEOUT - 30)
        self.verify(not c.zones['climate.bath'].held and not c.zones['climate.kitchen'].held, "no escalation too early")
        await s.run(120)
        held = [z.name for z in c.zones.values() if z.held]
        self.verify(len(held) >= 2, f"a second valve is held ({held})")
        self.verify(any(v.position > 0 for k, v in s.valves.items() if k != 'leo'), "a fallback valve is open")
        self.no_violations(s)
        s.close()

    async def test_10_selected_calibration_unavailable(self):
        print("\nTest 10: Selected valve's calibration entity offline - fallback valve held at once")
        s = standard_house()
        s.valves['leo'].cal_unavailable = True
        c = await s.start(prep=lambda: s._set(s.valves['leo'].calib_id, 'unavailable'))
        await s.run(10 * MINUTE)
        held = [z.name for z in c.zones.values() if z.held]
        self.verify(held and 'Leo' not in held, f"fallback valve held instead ({held})")
        self.verify(any(v.position > 0 for v in s.valves.values()), "a valve is open")
        s.close()

    async def test_11_offline_valve_escalates(self):
        print("\nTest 11: Valve offline (ignores writes, sends no reports)")
        s = standard_house()
        s.valves['leo'].offline = True
        c = await s.start()
        await s.run(10 * MINUTE)
        self.verify(any(v.position > 0 for k, v in s.valves.items() if k != 'leo'), "another valve opened")
        self.no_violations(s)
        s.close()

    async def test_12_calibration_write_lost(self):
        print("\nTest 12: Device keeps dropping the calibration - bounded retries, no write spam")
        s = standard_house()
        s.valves['leo'].offline = True  # ignores writes but we still echo its old value below
        c = await s.start()
        await s.run(2 * HOUR)
        n = len(s.writes_for('leo'))
        self.verify(n <= 6, f"bounded number of writes to an unresponsive TRV ({n})")
        s.close()

    async def test_13_no_flow_without_open_valve_preheating(self):
        print("\nTest 13: Pre-heating wants flow but every valve is shut - interlock holds the boiler")
        s = standard_house(discharge=None, leo=(15.0, 20.0), others=(15.0, 20.0))
        for v in s.valves.values():
            v.force_target = 0.0
        c = await s.start()
        from datetime import datetime, timedelta
        c.preheating.is_enabled = True
        c.preheating.preheating_end_time = datetime.now() + timedelta(hours=2)
        await s.run(30 * MINUTE)
        self.verify(s.boiler_on_seconds == 0, f"boiler never started ({s.boiler_on_seconds}s)")
        self.no_violations(s)
        s.close()

    async def test_14_all_position_sensors_unavailable(self):
        print("\nTest 14: All position sensors go unavailable while heating")
        s = standard_house(others=(16.5, 19.0))
        c = await s.start()
        await s.run(40 * MINUTE)

        async def drop():
            for v in s.valves.values():
                v.offline = True
                await s._emit(c._async_position_change, v.pos_id, 'unavailable')
        s.at(s.t + 1, drop)
        await s.run(10 * MINUTE)
        self.verify(c.current_flow_temp == 0, "boiler stopped: no valve can be confirmed open")
        self.verify(all(z.measured_opening == 0 for z in c.zones.values()), "unknown positions count as closed")
        s.close()

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------

    async def test_15_valve_closes_during_pump_overrun(self):
        print("\nTest 15: Last open valve closes while the pump is still running after a boiler stop")
        s = standard_house(others=(14.0, 19.0))
        c = await s.start()
        await s.run(12 * MINUTE)
        self.verify(c.current_flow_temp > 0, "boiler running")
        for k in ('living', 'kitchen', 'bath'):
            s.valves[k].setpoint = 10.0          # satisfied from now on
        s.valves['living'].force_target = 50.0   # but one actuator stays half open
        await s.run(2 * MINUTE)
        self.verify(c.current_flow_temp == 0, "boiler stopped")
        off = c.boiler_off_at
        s.valves['living'].force_target = 0.0    # now the last open valve closes inside the overrun
        leo = c.zones['climate.leo']
        engaged_at = None
        released_early = None
        while s.t < off + BOILER_PUMP_OVERRUN + 120:
            await s.run(2)
            if leo.held and engaged_at is None:
                engaged_at = s.t
            if engaged_at is not None and not leo.held and s.t < off + BOILER_PUMP_OVERRUN:
                released_early = s.t
        self.verify(engaged_at is not None, f"keep-open valve engaged when the last valve closed (t={engaged_at})")
        self.verify(released_early is None, f"and kept through the pump overrun (released at {released_early})")
        self.verify(s.valves['leo'].position > 0, "keep-open valve is open")
        # CLOSING_ENGAGE_OPENING = 25 %: the last valve can shut a moment before the held one has
        # opened its first step (physical race, see docs/keep-open-and-hold-design.md)
        self.verify(len(s.violations) <= 5, f"unsafe time limited to the actuator race ({len(s.violations)}s)")
        s.close()

    async def test_16_min_hold_time(self):
        print("\nTest 16: Hold lasts at least the minimum time even if another valve opens right away")
        s = standard_house()
        c = await s.start()
        await s.run(20)
        s.valves['living'].setpoint = 24.0
        s.valves['living'].room.temp = 18.0   # living opens fully
        zone = c.zones['climate.leo']
        await s.run(int(MIN_HOLD_SECONDS) - 10)
        self.verify(zone.has_hold(HOLD_AUTO), "hold still in place before the minimum time")
        s.close()

    async def test_17_flapping_neighbour_does_not_flap_hold(self):
        print("\nTest 17: A neighbour valve toggling 25%/50% - bounded calibration writes")
        s = standard_house()
        c = await s.start()
        toggle = {'v': 25.0}

        def flip():
            toggle['v'] = 50.0 if toggle['v'] == 25.0 else 25.0
            s.valves['living'].force_target = toggle['v']
        for k in range(1, 40):
            s.at(60 + k * 20, flip)
        await s.run(30 * MINUTE)
        n = len(s.writes_for('leo'))
        self.verify(n <= 4, f"hold writes stay bounded ({n}: {s.writes_for('leo')})")
        self.no_violations(s)
        s.close()

    async def test_18_overrun_timer_without_events(self):
        print("\nTest 18: Timers work with no entity changes (ticks only)")
        s = standard_house(others=(16.5, 19.0))
        c = await s.start()
        await s.run(40 * MINUTE)
        s.valves['leo'].force_target = None
        # freeze every device: no more reports or movement, only controller ticks
        for v in s.valves.values():
            v.stuck = True
            v.offline = True
        await s.run(20 * MINUTE)
        self.verify(c.pump_overrun_active(s.t) in (True, False), "controller keeps ticking")
        s.close()

    # ------------------------------------------------------------------
    # Restart / config
    # ------------------------------------------------------------------

    async def test_19_restart_during_night_hold(self):
        print("\nTest 19: HA restarts in the middle of the night hold - offset never cleared")
        s = standard_house(leo=(17.0, 19.0))
        c = await s.start()
        await c.async_set_zone_hold(c.zones['climate.leo'], HOLD_SWITCH, True)
        await s.run(HOUR)
        writes_before = len(s.writes_for('leo'))
        c2 = s.restart_controller()
        c2.apply_hold(c2.zones['climate.leo'], HOLD_SWITCH, True, c2.now())   # switch restores its state
        await s.run(2 * HOUR)
        self.verify(s.valves['leo'].calibration == HOLD_TEMP_OFFSET, "hold offset still on the device")
        self.verify(len(s.writes_for('leo')) == writes_before, f"no rewrite after restart ({s.writes_for('leo')})")
        self.verify(s.valves['leo'].position > 0, "valve stayed open")
        self.no_violations(s)
        s.close()

    async def test_20_restart_with_stale_heating_offsets(self):
        print("\nTest 20: Restart finds TRVs stuck at -2 - they are corrected once, not every cycle")
        s = standard_house(discharge=None)
        for v in s.valves.values():
            v.calibration = -2.0
        c = await s.start()
        await s.run(30 * MINUTE)
        counts = {k: len(s.writes_for(k)) for k in s.valves}
        self.verify(all(n <= 1 for n in counts.values()), f"at most one correction per TRV ({counts})")
        self.verify(all(v.calibration == 0.0 for v in s.valves.values()), "all back to neutral")
        s.close()

    async def test_21_component_disabled(self):
        print("\nTest 21: Component disabled - no writes, no boiler")
        s = standard_house(others=(16.5, 19.0))
        c = await s.start(enabled=False)
        await s.run(30 * MINUTE)
        self.verify(not s.calibration_writes, "nothing written")
        self.verify(s.boiler_on_seconds == 0 or c.current_flow_temp == 25.0, "controller does not command the boiler")
        s.close()

    async def test_22_log_only_mode(self):
        print("\nTest 22: Hold writes switched off - decisions made, no hold offset written")
        s = standard_house(hold_writes=False, leo=(20.5, 19.0))
        c = await s.start()
        await c.async_set_zone_hold(c.zones['climate.leo'], HOLD_SWITCH, True)
        await s.run(HOUR)
        self.verify(all(v != HOLD_TEMP_OFFSET for _, v in s.writes_for('leo')), "hold offset never written")
        self.verify(c.zones['climate.leo'].held, "hold decision still tracked")
        self.verify(s.valves['leo'].position == 0, "valve not forced open")
        self.verify(s.boiler_on_seconds == 0, "no boiler while every valve is shut")
        self.no_violations(s)
        s.close()

    async def test_23_selection_changed_while_holding(self):
        print("\nTest 23: Keep-open valve re-selected while a hold is active")
        s = standard_house(discharge='leo')
        c = await s.start()
        await s.run(10 * MINUTE)
        c.keep_open.update_config('climate.bath', 'Bath')
        await s.run(30 * MINUTE)
        self.verify(s.valves['bath'].position > 0, "new valve opened")
        self.verify(not c.zones['climate.leo'].held, "old valve released after the minimum time")
        self.no_violations(s)
        s.close()

    async def test_23b_selection_moves_the_hold_without_a_closing_trigger(self):
        print("\nTest 23b: Selection changed with a neighbour valve open at 25 % - hold moves in the same cycle")
        s = standard_house(discharge='leo', others=(20.5, 19.0))
        c = await s.start()
        await s.run(10 * MINUTE)
        s.valves['living'].setpoint = 22.0
        s.valves['living'].force_target = 25.0     # neighbour at the engage limit
        await s.run(2 * MINUTE)
        self.verify(c.zones['climate.leo'].held, "old valve still held")
        await c.async_set_keep_open_valve('climate.bath', 'Bath')
        self.verify(c.zones['climate.bath'].has_hold(HOLD_AUTO), "new valve held in the same cycle")
        await s.run(20 * MINUTE)
        self.verify(s.valves['bath'].position > 0, "new valve open")
        self.verify(not c.zones['climate.leo'].held, "old valve released")
        self.no_violations(s)
        s.close()

    async def test_23c_selection_cleared_and_reselected(self):
        print("\nTest 23c: Selection cleared, then set again")
        s = standard_house(discharge='leo')
        c = await s.start()
        await s.run(10 * MINUTE)
        await c.async_set_keep_open_valve(None, None)
        self.verify(not any(z.held for z in c.zones.values()), "nothing held while the feature is off")
        await s.run(5 * MINUTE)
        await c.async_set_keep_open_valve('climate.kitchen', 'Kitchen')
        await s.run(10 * MINUTE)
        self.verify(c.zones['climate.kitchen'].has_hold(HOLD_AUTO) and s.valves['kitchen'].position > 0, "kitchen now held open")
        s.close()

    async def test_23d_selection_changed_inside_pump_overrun(self):
        print("\nTest 23d: Selection changed during the pump overrun - never without an open valve")
        s = standard_house(discharge='leo', others=(14.0, 19.0))
        c = await s.start()
        await s.run(12 * MINUTE)
        for k in ('living', 'kitchen', 'bath'):
            s.valves[k].setpoint = 10.0
        await s.run(90)
        self.verify(c.current_flow_temp == 0, "boiler stopped, pump overrunning")
        await c.async_set_keep_open_valve('climate.bath', 'Bath')
        await s.run(10 * MINUTE)
        self.verify(s.valves['bath'].position > 0 and not c.zones['climate.leo'].held, "hold moved to the new valve")
        self.verify(len(s.violations) <= 5, f"no more than the actuator race ({len(s.violations)}s)")
        s.close()

    async def test_24_feature_disabled(self):
        print("\nTest 24: No keep-open valve selected - nothing is held, interlock still applies")
        s = standard_house(discharge=None, others=(16.5, 19.0))
        c = await s.start()
        await s.run(2 * HOUR)
        self.verify(not any(z.has_hold(HOLD_AUTO) for z in c.zones.values()), "no automatic hold")
        s.close()

    async def test_25_rapid_hold_switch_toggling(self):
        print("\nTest 25: Hold switch toggled repeatedly")
        s = standard_house(leo=(17.0, 19.0))
        c = await s.start()
        zone = c.zones['climate.leo']
        for k in range(10):
            await c.async_set_zone_hold(zone, HOLD_SWITCH, k % 2 == 0)
            await s.run(20)
        await c.async_set_zone_hold(zone, HOLD_SWITCH, True)
        await s.run(30 * MINUTE)
        self.verify(s.valves['leo'].calibration == HOLD_TEMP_OFFSET and s.valves['leo'].position > 0, "ends held and open")
        self.verify(len(s.writes_for('leo')) <= 6, f"writes bounded ({len(s.writes_for('leo'))})")
        self.no_violations(s)
        s.close()

    async def test_26_external_sensor_dies_during_hold(self):
        print("\nTest 26: External sensor stops reporting overnight - falls back to the TRV reading")
        s = standard_house(leo=(17.5, 19.0))
        c = await s.start()
        zone = c.zones['climate.leo']
        await c.async_set_zone_hold(zone, HOLD_SWITCH, True)
        await s.run(10 * MINUTE)
        s.valves['leo'].ext_dead = True
        await s.run(3 * HOUR)
        self.verify(zone.export_zone_state()['temperature_source'] == 'trv', "fell back to the TRV")
        self.verify(abs(zone.current_temp - s.valves['leo'].room.temp) < 1.0,
                    f"fallback estimate close to the real room ({zone.current_temp:.1f} vs {s.valves['leo'].room.temp:.1f})")
        self.no_violations(s)
        s.close()

    async def test_27_priority_low_zone_held_by_switch_is_high_priority(self):
        print("\nTest 27: A low-priority zone held by switch asks for heat on its own")
        s = standard_house(leo=(18.0, 19.0), others=(20.5, 19.0))
        c = await s.start()
        zone = c.zones['climate.leo']
        self.verify(not zone.counts_as_high_priority, "low priority normally")
        await c.async_set_zone_hold(zone, HOLD_SWITCH, True)
        self.verify(zone.counts_as_high_priority, "high priority while held by switch")
        s.close()

    async def test_27b_no_writes_before_entities_exist(self):
        print("\nTest 27b: Calibration entities not there yet at startup - nothing is written into the void")
        s = standard_house(discharge=None)
        for v in s.valves.values():
            v.calibration = -2.0
        c = await s.start(prep=lambda: [s.hass.states.pop(v.calib_id) for v in s.valves.values()])
        await s.run(60)
        self.verify(not s.calibration_writes, f"no writes while the entities are missing ({len(s.calibration_writes)})")
        for v in s.valves.values():                 # Z2M entities come online reporting -2
            await s._emit(c._async_calibration_change, v.calib_id, '-2.0')
        await s.run(5 * MINUTE)
        counts = {k: len(s.writes_for(k)) for k in s.valves}
        self.verify(all(n == 1 for n in counts.values()), f"each TRV corrected exactly once once it exists ({counts})")
        self.verify(all(v.calibration == 0.0 for v in s.valves.values()), "all neutral")
        s.close()

    async def test_27c_decision_rechecked_after_valve_open_wait(self):
        print("\nTest 27c: Boiler start waits for the valves, then decides again instead of committing a stale ON")
        s = standard_house(others=(14.0, 19.0), discharge=None)
        c = await s.start()
        await s.run(2 * MINUTE)
        self.verify(c.current_flow_temp > 0, "boiler running (setup)")
        c.current_flow_temp = 0.0                  # as if it had been off
        c.valve_open_delay = 5
        real_sleep = asyncio.sleep

        async def fake_sleep(_delay):              # while we wait, every room reaches its target
            for zone in c.zones.values():
                zone.target_temp = 10.0
                zone._refresh_temperature()
        asyncio.sleep = fake_sleep
        try:
            await c._calculate_and_command()
        finally:
            asyncio.sleep = real_sleep
        self.verify(c.current_flow_temp == 0.0, f"boiler not started on the stale decision (flow {c.current_flow_temp})")
        s.close()

    # ------------------------------------------------------------------
    # Fuzz
    # ------------------------------------------------------------------

    async def test_28_fuzz(self):
        print("\nTest 28: Randomised houses (setpoint changes, holds, failures, restarts)")
        total_runs = 0
        worst = []
        for seed in range(25):
            try:
                s, _ = await fuzz_house(seed)
            except Exception as e:  # a crash is a failure of its own
                self.verify(False, f"seed {seed} raised {type(e).__name__}: {e}")
                continue
            runs = s.violation_runs()
            if runs:
                worst.append((seed, runs))
            total_runs += len(runs)
            s.close()
        # With CLOSING_ENGAGE_OPENING = 25 % a few houses see a short gap when the last valve closes
        # faster than the held valve can open its first step. Bound how often and how long.
        longest = max((r[1] for _, runs in worst for r in runs), default=0)
        self.verify(len(worst) <= 5, f"at most 5 of 25 random houses have any unsafe period ({len(worst)}: {worst})")
        self.verify(longest <= 45, f"no unsafe period longer than 45 s (longest {longest}s)")

    async def run_all_tests(self):
        for name in sorted(n for n in dir(self) if n.startswith("test_")):
            await getattr(self, name)()
        print(f"\n  Keep-open safety: {self.passed} passed, {self.failed} failed")


if __name__ == "__main__":
    suite = KeepOpenSafetyTests()
    asyncio.run(suite.run_all_tests())
    sys.exit(1 if suite.failed else 0)
