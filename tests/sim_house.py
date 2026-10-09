"""
Physical simulation of a house for safety tests.

Models what the controller cannot see: slow actuators that commit to a position and finish the
travel before taking the next command, Zigbee delay on calibration writes, TRVs that add their
calibration to the temperature they report, room heat gain/loss, and a boiler pump that keeps
circulating for BOILER_PUMP_OVERRUN seconds after the boiler is told to stop.

The safety rule checked every simulated second: the pump must never be circulating while every
valve is physically shut.
"""

import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))
sys.path.insert(0, str(Path(__file__).parent))

import zone_wrapper
from keep_open import BOILER_PUMP_OVERRUN
from master_controller import MasterController
from mock_ha import MockEvent, MockHass, MockState
from trv_model import opening_for_error
from zone_wrapper import HOLD_SWITCH

TICK = 30
STEP_SECONDS = 15          # an actuator needs this long per 25 % step
ZIGBEE_DELAY = 3           # calibration write reaches the TRV
ECHO_DELAY = 2             # and the device reports it back
CLIMATE_REPORT = 60        # TRV temperature report interval
EXT_REPORT = 300           # external sensor report interval
BASE_EPOCH = 1_700_000_000.0
AVAILABILITY_TIMEOUT = 1500   # Z2M marks a silent battery device unavailable after this long (passive timeout)


class _TimeShim:
    """Stands in for the `time` module inside zone_wrapper so sensor staleness follows sim time."""

    def __init__(self, sim: "SimHouse") -> None:
        self.sim = sim

    def time(self) -> float:
        return BASE_EPOCH + self.sim.t

    def monotonic(self) -> float:
        return self.sim.t


class SimRoom:
    def __init__(self, name: str, temp: float, ambient: float = 10.0, loss: float = 0.00003,
                 gain: float = 0.0015) -> None:
        self.name = name
        self.temp = temp
        self.ambient = ambient
        self.loss = loss
        self.gain = gain
        self.valves: list["SimValve"] = []

    def step(self, circulating: bool) -> None:
        heat = sum(v.position / 100.0 for v in self.valves) * self.gain if circulating else 0.0
        self.temp += heat - self.loss * (self.temp - self.ambient)


class SimValve:
    """One TRV: regulates itself from its reported temperature, slow actuator, calibration offset."""

    def __init__(self, sim: "SimHouse", key: str, room: SimRoom, setpoint: float = 20.0,
                 priority_high: bool = True, ext_sensor: bool = False, calibration: float = 0.0) -> None:
        self.sim = sim
        self.key = key
        self.room = room
        room.valves.append(self)
        self.setpoint = setpoint
        self.priority_high = priority_high
        self.ext_sensor = ext_sensor
        self.calibration = calibration       # value inside the device
        self.position = 0.0                  # actual, multiples of 25
        self.committed: Optional[float] = None
        self.step_timer = 0
        self.step_seconds = STEP_SECONDS
        self.stuck = False                   # actuator never moves
        self.offline = False                 # no reports, ignores calibration writes
        self.pending_cal: list[tuple[int, float]] = []   # (apply time, value)
        self.force_target: Optional[float] = None        # override the regulation (scenario)
        self.cal_unavailable = False                     # calibration entity offline: no echo
        self.offline_for = 0                             # seconds silent; > AVAILABILITY_TIMEOUT = unavailable
        self.marked_unavailable = False

        self.climate_id = f"climate.{key}"
        self.pos_id = f"sensor.{key}_position"
        self.calib_id = f"number.{key}_calibration"
        self.ext_id = f"sensor.{key}_room_temp" if ext_sensor else None
        self.last_reported_pos: Optional[float] = None

    @property
    def reported_temp(self) -> float:
        return round(self.room.temp + self.calibration, 1)

    def desired_target(self) -> float:
        if self.force_target is not None:
            return self.force_target
        return opening_for_error(self.setpoint - self.reported_temp)

    def step(self) -> None:
        # calibration writes arriving
        for item in list(self.pending_cal):
            if item[0] <= self.sim.t:
                self.pending_cal.remove(item)
                if not self.offline and not self.cal_unavailable:
                    self.calibration = item[1]
                    self.sim.echo_calibration(self, self.sim.t + ECHO_DELAY)
        if self.stuck:
            return
        if self.committed is None and self.desired_target() != self.position:
            self.committed = self.desired_target()
            self.step_timer = 0
        if self.committed is not None:
            self.step_timer += 1
            if self.step_timer >= self.step_seconds:
                self.step_timer = 0
                self.position += 25.0 if self.committed > self.position else -25.0
                if self.position == self.committed:
                    self.committed = None


class SimHouse:
    def __init__(self, discharge: Optional[str] = None, hold_writes: bool = True) -> None:
        self.t = 0
        self.hass = MockHass()
        self.rooms: list[SimRoom] = []
        self.valves: dict[str, SimValve] = {}
        self.discharge = discharge
        self.zigbee_delay = ZIGBEE_DELAY
        self.hold_writes = hold_writes
        self.controller: Optional[MasterController] = None
        self.calibration_writes: list[tuple[int, str, float]] = []
        self.violations: list[int] = []
        self.boiler_commanded_on = False
        self.boiler_off_time: Optional[int] = None
        self.boiler_on_seconds = 0
        self.callbacks: dict[int, list[Callable]] = {}
        self.pending_echo: list[tuple[int, SimValve]] = []
        self.log: list[str] = []
        self._orig_time = zone_wrapper.time
        zone_wrapper.time = _TimeShim(self)

        sim = self

        class SimServices:
            async def async_call(self, domain, service, data=None, blocking=False):
                if domain == "number" and service == "set_value":
                    sim.on_calibration_write(data["entity_id"], float(data["value"]))

        self.hass.services = SimServices()

    # --- construction -------------------------------------------------

    def add(self, key: str, temp: float = 20.0, setpoint: float = 20.0, high: bool = True,
            ext: bool = False, calibration: float = 0.0, room: Optional[SimRoom] = None) -> SimValve:
        room = room or SimRoom(key, temp)
        if room not in self.rooms:
            self.rooms.append(room)
        valve = SimValve(self, key, room, setpoint, high, ext, calibration)
        self.valves[key] = valve
        return valve

    def zone_configs(self) -> list[dict]:
        configs = []
        for i, v in enumerate(self.valves.values()):
            cfg = {
                'entity_id': v.climate_id, 'name': v.key.title(), 'area': 20.0,
                'is_high_priority': v.priority_high,
                'trv_position_entity_id': v.pos_id, 'temp_calib_entity_id': v.calib_id,
            }
            if v.ext_id:
                cfg['ext_temp_entity_id'] = v.ext_id
            if i == 0 and self.discharge:
                cfg['discharge_trv_entity_id'] = self.valves[self.discharge].climate_id
                cfg['discharge_trv_name'] = self.valves[self.discharge].key.title()
            configs.append(cfg)
        return configs

    async def start(self, enabled: bool = True, prep: Optional[Callable] = None) -> MasterController:
        """Publish initial states, build the controller, seed it and run the first cycle."""
        for v in self.valves.values():
            self._publish_all(v)
        if prep:
            prep()
        ctl = MasterController(self.hass, self.zone_configs())
        ctl._clock = lambda: float(self.t)
        ctl.valve_open_delay = 0
        ctl.component_enabled = enabled
        ctl.hold_writes_enabled = self.hold_writes
        self.controller = ctl
        ctl._seed_from_states()
        await ctl._calculate_and_command()   # first cycle: the boiler state is no longer "unknown"
        self.boiler_off_time = None
        return ctl

    def restart_controller(self) -> MasterController:
        """HA restart: a new controller instance that seeds from the entity states."""
        old = self.controller
        ctl = MasterController(self.hass, self.zone_configs())
        ctl._clock = lambda: float(self.t)
        ctl.valve_open_delay = 0
        ctl.component_enabled = True
        ctl.hold_writes_enabled = self.hold_writes
        self.controller = ctl
        ctl._seed_from_states()
        return ctl

    def close(self) -> None:
        zone_wrapper.time = self._orig_time

    # --- entity states --------------------------------------------------

    def _set(self, entity_id: str, state: str, attributes: Optional[dict] = None):
        self.hass.states[entity_id] = MockState(entity_id=entity_id, state=state, attributes=attributes or {})
        return self.hass.states[entity_id]

    def _climate_attrs(self, v: SimValve) -> dict:
        return {'current_temperature': v.reported_temp, 'temperature': v.setpoint}

    def _publish_all(self, v: SimValve) -> None:
        self._set(v.climate_id, 'heat', self._climate_attrs(v))
        self._set(v.pos_id, str(int(v.position)))
        self._set(v.calib_id, str(float(v.calibration)))
        if v.ext_id:
            self._set(v.ext_id, str(round(v.room.temp, 2)))

    async def _emit(self, handler, entity_id: str, state: str, attributes: Optional[dict] = None) -> None:
        new = self._set(entity_id, state, attributes)
        await handler(MockEvent(data={'entity_id': entity_id, 'new_state': new}))

    # --- device side ----------------------------------------------------

    def on_calibration_write(self, entity_id: str, value: float) -> None:
        v = next(x for x in self.valves.values() if x.calib_id == entity_id)
        self.calibration_writes.append((self.t, v.key, value))
        v.pending_cal.append((self.t + self.zigbee_delay, value))

    def echo_calibration(self, v: SimValve, when: int) -> None:
        self.pending_echo.append((when, v))

    @property
    def circulating(self) -> bool:
        ctl = self.controller
        return ctl.current_flow_temp > 0 or (
            self.boiler_off_time is not None and self.t < self.boiler_off_time + BOILER_PUMP_OVERRUN
        )

    # --- main loop ------------------------------------------------------

    def at(self, t: int, fn: Callable) -> None:
        self.callbacks.setdefault(t, []).append(fn)

    async def run(self, seconds: int) -> None:
        ctl = self.controller
        end = self.t + seconds
        while self.t < end:
            self.t += 1
            for fn in self.callbacks.pop(self.t, []):
                res = fn()
                if hasattr(res, '__await__'):
                    await res

            heating = ctl.current_flow_temp > 0
            circulating = self.circulating
            for room in self.rooms:
                room.step(heating)
            for v in self.valves.values():
                before = v.position
                v.step()
                if v.position != v.last_reported_pos and not v.offline:
                    v.last_reported_pos = v.position
                    await self._emit(ctl._async_position_change, v.pos_id, str(int(v.position)))
            # Z2M availability: a TRV silent for AVAILABILITY_TIMEOUT turns unavailable in HA
            for v in self.valves.values():
                if v.offline:
                    v.offline_for += 1
                    if v.offline_for == AVAILABILITY_TIMEOUT and not v.marked_unavailable:
                        v.marked_unavailable = True
                        await self._emit(ctl._async_position_change, v.pos_id, 'unavailable')
                        await self._emit(ctl._async_climate_state_change, v.climate_id, 'unavailable', {})
                        await self._emit(ctl._async_calibration_change, v.calib_id, 'unavailable')
                else:
                    v.offline_for = 0
                    if v.marked_unavailable:
                        v.marked_unavailable = False
                        v.last_reported_pos = None
                        await self._emit(ctl._async_calibration_change, v.calib_id, str(float(v.calibration)))
            # calibration echoes
            for item in list(self.pending_echo):
                if item[0] <= self.t:
                    self.pending_echo.remove(item)
                    v = item[1]
                    await self._emit(ctl._async_calibration_change, v.calib_id, str(float(v.calibration)))
            # periodic reports
            for i, v in enumerate(self.valves.values()):
                if not v.offline and (self.t + i * 7) % CLIMATE_REPORT == 0:
                    await self._emit(ctl._async_climate_state_change, v.climate_id, 'heat', self._climate_attrs(v))
                if v.ext_id and (self.t + i * 11) % EXT_REPORT == 0 and not getattr(v, 'ext_dead', False):
                    await self._emit(ctl._async_external_temp_change, v.ext_id, str(round(v.room.temp, 2)))
            if self.t % TICK == 0:
                await ctl._async_tick()

            # boiler physics + safety invariant
            on = ctl.current_flow_temp > 0
            if self.boiler_commanded_on and not on:
                self.boiler_off_time = self.t
            if on:
                self.boiler_off_time = None
                self.boiler_on_seconds += 1
            self.boiler_commanded_on = on
            # Not counted: a disabled controller has handed control away, and a TRV that went silent
            # looks open to the controller until Z2M marks it unavailable (a window nobody can see into)
            blind = any(v.offline and not v.marked_unavailable for v in self.valves.values())
            if ctl.component_enabled and not blind and self.circulating and all(v.position == 0 for v in self.valves.values()):
                self.violations.append(self.t)

    # --- helpers for assertions ----------------------------------------

    def violation_runs(self) -> list[tuple[int, int]]:
        """Contiguous (start, length) runs of unsafe seconds."""
        runs = []
        for t in self.violations:
            if runs and t == runs[-1][0] + runs[-1][1]:
                runs[-1] = (runs[-1][0], runs[-1][1] + 1)
            else:
                runs.append((t, 1))
        return runs

    def writes_for(self, key: str) -> list[tuple[int, float]]:
        return [(t, val) for t, k, val in self.calibration_writes if k == key]
