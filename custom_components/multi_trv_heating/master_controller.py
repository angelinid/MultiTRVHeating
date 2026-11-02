"""
MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Callable, Optional

try:
    from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
except ImportError:
    # For testing without Home Assistant installed
    async_track_state_change_event = None
    async_track_time_interval = None

try:
    from .const import LOGGER_NAME, MAX_FLOW_TEMP, MIN_FLOW_TEMP
    from .keep_open import BOILER_PUMP_OVERRUN, KeepOpenController
    from .preheating import PreheatingController
    from .zone import Zone
    from .zone_wrapper import HOLD_TEMP_OFFSET, ZoneWrapper
    from .zones_config import legacy_keep_open, normalize_zone_configs
except ImportError:
    from const import LOGGER_NAME, MAX_FLOW_TEMP, MIN_FLOW_TEMP
    from keep_open import BOILER_PUMP_OVERRUN, KeepOpenController
    from preheating import PreheatingController
    from zone import Zone
    from zone_wrapper import HOLD_TEMP_OFFSET, ZoneWrapper
    from zones_config import legacy_keep_open, normalize_zone_configs

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(LOGGER_NAME)

# Sensor states that carry no usable reading.
_INVALID_STATES = ("unknown", "unavailable", None, "")

# Seconds to wait for valves to open before turning the boiler on from OFF.
VALVE_OPEN_DELAY = 20

# The control loop also runs on a timer: timeouts (pump overrun, hold minimums, confirmation)
# must work when no entity changes.
TICK_SECONDS = 30

# Calibration writes: the actuators are slow and battery powered.
MIN_OFFSET_WRITE_INTERVAL = 120.0   # between non-urgent writes to one TRV
ECHO_GRACE = 300.0                  # wait this long for the device to report a new calibration (it takes 2-3 min)
TARGET_PENDING_TIMEOUT = 600.0      # a written setpoint is ignored-against for this long (valves report slowly)
STARTUP_GRACE = 120.0               # after a restart, wait this long at most for every valve to report
MAX_DRIFT_RETRIES = 3               # rewrite a calibration that does not stick this many times


def demand_to_flow_temp(demand: float) -> float:
    """Map a demand metric (0.0-1.0) linearly onto MIN_FLOW_TEMP..MAX_FLOW_TEMP."""
    return MIN_FLOW_TEMP + demand * (MAX_FLOW_TEMP - MIN_FLOW_TEMP)


class MasterController:
    """
    Aggregates zone demand and drives the boiler flow temperature and the TRV offsets.

    One control cycle (run on every relevant state change and on a timer):
    1. keep-open: decide which valve (if any) is held open from the measured openings
    2. boiler decision from the zones' effective openings:
       - ON if any high-priority zone is demanding heat (intensity = highest high-priority demand)
       - ON if the low-priority openings sum to >= 100% (intensity = highest low-priority demand)
       - OFF otherwise, and the opening-based offsets are reset
       Pre-heating, when active, overrides the flow temperature.
    3. interlock: never request flow unless a valve is measured open
    4. write the TRV offsets (the only place offsets are written)
    5. command the boiler
    """

    def __init__(self, hass: "HomeAssistant", zone_configs: list[dict],
                 keep_open_valve: Optional[str] = None) -> None:
        self.hass = hass
        self.zones: dict[str, ZoneWrapper] = {}   # the valves, by climate entity id
        self.rooms: dict[str, Zone] = {}          # the zones (rooms), by zone id

        # Master enable switch for the whole component (set by ComponentEnableSwitch).
        self.component_enabled = False

        # Log-only mode for held-open offsets: decisions are made and logged but the hold
        # offset is not written to the TRV (set by HoldWritesSwitch).
        self.hold_writes_enabled = True

        # Clock for all timers (monotonic seconds); tests replace it.
        self._clock: Callable[[], float] = time.monotonic
        self.valve_open_delay: float = VALVE_OPEN_DELAY

        self.preheating = PreheatingController(self)

        # The keep-open valve: passed in, or (config version 1) stored on the first entry.
        legacy_id, legacy_name = legacy_keep_open(zone_configs)
        self.keep_open = KeepOpenController(self, keep_open_valve or legacy_id, legacy_name)

        # Last commanded OpenTherm flow temperature (for sensor reporting). Starts "on" because
        # the boiler state at startup is unknown: the first OFF decision then counts as a stop.
        self.current_flow_temp: float = MIN_FLOW_TEMP
        self.boiler_off_at: Optional[float] = None

        self._interlock_active = False
        self._last_decision: Optional[tuple] = None
        self._settle_started: Optional[float] = None
        self._settled = False
        self._calc_lock = asyncio.Lock()
        self._calc_waiting = False
        self._unsubscribers: list[Callable[[], None]] = []
        self._ext_unsubscribe: Optional[Callable[[], None]] = None

        for zone_config in normalize_zone_configs(zone_configs):
            valves = []
            for config in zone_config['valves']:
                valve = ZoneWrapper(
                    my_master_controller=self,
                    entity_id=config['entity_id'],
                    name=config.get('name', config['entity_id']),
                    trv_position_entity_id=config.get('trv_position_entity_id'),
                    temp_calib_entity_id=config.get('temp_calib_entity_id'),
                )
                self.zones[valve.entity_id] = valve
                valve.boost_entity_id = config.get('boost_entity_id') or self._find_boost_switch(valve.entity_id)
                if valve.boost_entity_id:
                    valve.boost_reader = lambda eid=valve.boost_entity_id: self._switch_is_on(eid)
                else:
                    _LOGGER.warning("Valve '%s': no boost switch found on its device - boost is not detected", valve.name)
                valves.append(valve)
            zone = Zone(
                zone_config['zone_id'], zone_config['name'], valves,
                floor_area_m2=zone_config.get('area', 0.0),
                is_high_priority=zone_config.get('is_high_priority', True),
                ext_temp_entity_id=zone_config.get('ext_temp_entity_id'),
            )
            self.rooms[zone.zone_id] = zone
            _LOGGER.info(
                "Registered zone '%s' (area=%.1f m², priority=%s, external_temp=%s) with %d valve(s): %s",
                zone.name, zone.floor_area_m2, "HIGH" if zone.is_high_priority else "LOW",
                zone.ext_temp_entity_id or "none", len(valves),
                ", ".join(f"{v.name} ({v.entity_id})" for v in valves),
            )

        # Name of the keep-open valve, for display
        keep = self.zones.get(self.keep_open.keep_valve_id or "")
        if keep:
            self.keep_open.keep_valve_name = keep.name

        self.monitored_climate_entities = list(self.zones)
        self.monitored_position_sensors = [
            z.trv_position_entity_id for z in self.zones.values() if z.trv_position_entity_id
        ]
        self.monitored_external_sensors = self._external_sensors()
        self.monitored_calibration_entities = [
            z.temp_calib_entity_id for z in self.zones.values() if z.temp_calib_entity_id
        ]
        self.monitored_boost_switches = [
            z.boost_entity_id for z in self.zones.values() if z.boost_entity_id
        ]

    def _external_sensors(self) -> list[str]:
        """The external temperature sensors of all zones (each once)."""
        return list(dict.fromkeys(z.ext_temp_entity_id for z in self.rooms.values() if z.ext_temp_entity_id))

    def _find_boost_switch(self, climate_entity_id: str) -> Optional[str]:
        """The 'Boost heating' switch on the same device as the climate entity (entity registry)."""
        try:
            from homeassistant.helpers import entity_registry as er
            registry = er.async_get(self.hass)
            entry = registry.async_get(climate_entity_id)
            if entry is None or not entry.device_id:
                return None
            for other in er.async_entries_for_device(registry, entry.device_id):
                if other.domain == "switch" and "boost" in (other.original_name or other.entity_id).lower():
                    return other.entity_id
        except Exception:  # registry not available (tests) or lookup failed
            pass
        return None

    def _switch_is_on(self, entity_id: str) -> bool:
        states = getattr(self.hass, "states", None)
        state = states.get(entity_id) if states is not None else None
        return state is not None and state.state == "on"

    def now(self) -> float:
        """Controller clock (monotonic seconds)."""
        return self._clock()

    # ------------------------------------------------------------------
    # Event listeners
    # ------------------------------------------------------------------

    async def async_start_listening(self) -> None:
        """Seed from current states, then subscribe to state changes and the control timer."""
        self._seed_from_states()

        if async_track_state_change_event is None:
            _LOGGER.warning("async_track_state_change_event not available - running in test mode")
            return

        listeners: list[tuple[list[str], Callable]] = [
            (self.monitored_climate_entities, self._async_climate_state_change),
            (self.monitored_position_sensors, self._async_position_change),
            (self.monitored_calibration_entities, self._async_calibration_change),
            (self.monitored_boost_switches, self._async_boost_change),
        ]
        for entity_ids, handler in listeners:
            if entity_ids:
                self._unsubscribers.append(async_track_state_change_event(self.hass, entity_ids, handler))

        self._subscribe_external_sensors()

        self._unsubscribers.append(
            async_track_time_interval(self.hass, self._async_tick, timedelta(seconds=TICK_SECONDS))
        )

        _LOGGER.info(
            "Listening to %d climate entities, %d position sensors, %d external sensors, "
            "%d calibration entities",
            len(self.monitored_climate_entities), len(self.monitored_position_sensors),
            len(self.monitored_external_sensors), len(self.monitored_calibration_entities),
        )

    def _subscribe_external_sensors(self) -> None:
        """(Re)subscribe to the zones' external temperature sensors."""
        if self._ext_unsubscribe:
            self._ext_unsubscribe()
            self._ext_unsubscribe = None
        if self.monitored_external_sensors and async_track_state_change_event is not None:
            self._ext_unsubscribe = async_track_state_change_event(
                self.hass, self.monitored_external_sensors, self._async_external_temp_change
            )

    def set_external_sensor(self, zone: Zone, entity_id: Optional[str]) -> None:
        """Use `entity_id` (or none) as the zone's external temperature sensor, from now on."""
        entity_id = entity_id or None
        if zone.ext_temp_entity_id == entity_id:
            return
        _LOGGER.info("Zone '%s': external temperature sensor %s -> %s",
                     zone.name, zone.ext_temp_entity_id or "none", entity_id or "none")
        zone.set_external_sensor(entity_id)
        self.monitored_external_sensors = self._external_sensors()
        if entity_id:
            states = getattr(self.hass, "states", None)
            value = self._parse_state(states.get(entity_id)) if states is not None else None
            if value is not None:
                for valve in zone.valves:
                    valve.update_external_temperature(value)
        self._subscribe_external_sensors()

    async def async_stop_listening(self) -> None:
        """Unsubscribe everything (config entry unload)."""
        if self._ext_unsubscribe:
            self._ext_unsubscribe()
            self._ext_unsubscribe = None
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def _seed_from_states(self) -> None:
        """Read the current entity states so the controller does not start blind after a restart."""
        states = getattr(self.hass, "states", None)
        if states is None:
            return
        for zone in self.zones.values():
            climate = states.get(zone.entity_id)
            if climate is not None:
                zone.update_from_state(climate)
            if zone.trv_position_entity_id:
                value = self._parse_state(states.get(zone.trv_position_entity_id))
                if value is not None:
                    zone.update_trv_opening(value)
                else:
                    zone.mark_position_unavailable()
            if zone.ext_temp_entity_id:
                value = self._parse_state(states.get(zone.ext_temp_entity_id))
                if value is not None:
                    zone.update_external_temperature(value)
            if zone.temp_calib_entity_id:
                zone.update_device_offset(self._parse_state(states.get(zone.temp_calib_entity_id)))
            zone.refresh_demand()

    @staticmethod
    def _parse_state(state) -> Optional[float]:
        if state is None or state.state in _INVALID_STATES:
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _zone_for_sensor(self, attr: str, entity_id: str) -> Optional[ZoneWrapper]:
        """Return the zone whose `attr` (e.g. 'trv_position_entity_id') equals entity_id."""
        for zone in self.zones.values():
            if getattr(zone, attr) == entity_id:
                return zone
        return None

    @staticmethod
    def _read_float_state(zone: ZoneWrapper, new_state, entity_id: str, what: str) -> Optional[float]:
        """Parse a numeric sensor state; None if unavailable or unparseable."""
        if new_state.state in _INVALID_STATES:
            _LOGGER.debug("Zone '%s': %s is %s, skipping update", zone.name, what, new_state.state or 'None')
            return None
        try:
            return float(new_state.state)
        except (ValueError, TypeError) as e:
            _LOGGER.warning(
                "Error reading %s from %s (state='%s'): %s", what, entity_id, new_state.state, e
            )
            return None

    async def _async_climate_state_change(self, event) -> None:
        """Climate entity changed: refresh zone temperatures and recalculate."""
        new_state = event.data.get('new_state')
        zone = self.zones.get(event.data.get('entity_id'))
        if zone and new_state:
            zone.update_from_state(new_state)
            if zone.zone is not None:
                await self._adopt_target(zone.zone, zone)

        await self._calculate_and_command()

    async def _adopt_target(self, zone: Zone, valve: ZoneWrapper) -> None:
        """
        A valve reported its setpoint. Our own writes are ignored until the valve reports them (or
        TARGET_PENDING_TIMEOUT); a valve that was offline is brought to the zone target; any other
        change made outside (dial, Z2M, a scene) is adopted by the zone, last writer wins, and the
        other valves are brought to it.
        """
        if not valve.has_reading:
            return
        reported = valve.target_temp
        now = self.now()
        pending = zone.pending_targets.get(valve.entity_id)
        if pending:
            value, written_at = pending
            if abs(reported - value) <= 0.01 or now - written_at > TARGET_PENDING_TIMEOUT:
                zone.pending_targets.pop(valve.entity_id, None)
                valve._refresh_temperature(cascade=False)
            else:
                return
        if zone.desired_target is None:
            zone.desired_target = reported
            return
        if valve.just_returned:
            valve.just_returned = False
            if abs(reported - zone.desired_target) > 0.01:
                await self._write_valve_target(zone, valve, zone.desired_target)
            return
        if abs(reported - zone.desired_target) > 0.01:
            _LOGGER.info("Zone '%s': target %.1f -> %.1f (set on '%s')",
                         zone.name, zone.desired_target, reported, valve.name)
            zone.desired_target = reported
            await self._sync_targets(zone, except_valve=valve)

    async def _write_valve_target(self, zone: Zone, valve: ZoneWrapper, value: float) -> None:
        zone.pending_targets[valve.entity_id] = (value, self.now())
        valve._refresh_temperature(cascade=False)
        await self.hass.services.async_call(
            "climate", "set_temperature", {"entity_id": valve.entity_id, "temperature": value}, blocking=False
        )

    async def _sync_targets(self, zone: Zone, except_valve: Optional[ZoneWrapper] = None) -> None:
        """Write the zone target to every available valve that does not have it."""
        for valve in zone.valves:
            if valve is except_valve or not valve.available:
                continue
            if abs(valve.target_temp - zone.desired_target) > 0.01 or valve.entity_id in zone.pending_targets:
                await self._write_valve_target(zone, valve, zone.desired_target)

    async def async_set_zone_target(self, zone: Zone, value: float) -> None:
        """Set the zone's target on all of its valves."""
        zone.desired_target = float(value)
        for valve in zone.valves:
            if valve.available:
                await self._write_valve_target(zone, valve, float(value))
        await self._calculate_and_command()

    async def async_set_zone_hvac_mode(self, zone: Zone, mode: str) -> None:
        """Set the hvac mode on all available valves of the zone."""
        for valve in zone.valves:
            if valve.available:
                await self.hass.services.async_call(
                    "climate", "set_hvac_mode", {"entity_id": valve.entity_id, "hvac_mode": mode}, blocking=False
                )

    async def async_boost_zone(self, zone: Zone) -> None:
        """Start the TRV boost on all valves of the zone (the only way a zone starts one)."""
        for valve in zone.valves:
            if valve.boost_entity_id:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": valve.boost_entity_id}, blocking=False
                )
        _LOGGER.info("Zone '%s': boost started on %d valve(s)", zone.name, len(zone.valves))

    async def _async_position_change(self, event) -> None:
        """TRV position sensor changed: update the measured valve opening and recalculate."""
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        zone = self._zone_for_sensor('trv_position_entity_id', entity_id) if new_state else None

        if zone:
            opening_percent = self._read_float_state(zone, new_state, entity_id, "TRV position")
            if opening_percent is not None:
                zone.update_trv_opening(opening_percent)
            elif new_state.state in _INVALID_STATES:
                zone.mark_position_unavailable()

        await self._calculate_and_command()

    async def _async_external_temp_change(self, event) -> None:
        """External temperature sensor changed: store the reading and recalculate."""
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        valves = [v for v in self.zones.values() if v.ext_temp_entity_id == entity_id] if new_state else []

        if valves:
            ext_temp = self._read_float_state(valves[0], new_state, entity_id, "external temperature")
            if ext_temp is not None:
                for valve in valves:
                    valve.update_external_temperature(ext_temp)
                await self._calculate_and_command()

    async def _async_calibration_change(self, event) -> None:
        """TRV calibration entity changed: remember what the device really has."""
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        zone = self._zone_for_sensor('temp_calib_entity_id', entity_id) if new_state else None

        if zone:
            zone.update_device_offset(self._parse_state(new_state))
            await self._calculate_and_command()

    async def _async_boost_change(self, event) -> None:
        """TRV boost switch changed: a boosted zone is a heat request."""
        entity_id = event.data.get('entity_id')
        zone = self._zone_for_sensor('boost_entity_id', entity_id)

        if zone:
            _LOGGER.info("Zone '%s': boost %s", zone.name, "on" if zone.boosted else "off")
            zone.refresh_demand()
            await self._calculate_and_command()

    async def _async_tick(self, _now=None) -> None:
        """Timer: run the control loop so timeouts work when nothing else changes."""
        await self._calculate_and_command()

    # ------------------------------------------------------------------
    # Holds
    # ------------------------------------------------------------------

    def apply_hold(self, zone: "Zone | ZoneWrapper", reason: str, active: bool, now: float) -> bool:
        """Add/remove a hold reason on a zone (offsets are written by the next sync)."""
        return any([v.set_hold(reason, active, now) for v in getattr(zone, "valves", [zone])])

    async def async_set_zone_hold(self, zone: "Zone | ZoneWrapper", reason: str, active: bool) -> None:
        """Hold a zone's valves open (or release them) on request, e.g. from the hold switch."""
        for valve in getattr(zone, "valves", [zone]):
            self.apply_hold(valve, reason, active, self.now())
        _LOGGER.info("Zone '%s': %s hold %s", zone.name, reason, "on" if active else "off")
        await self._calculate_and_command()

    async def async_set_keep_open_valve(self, entity_id: Optional[str], name: Optional[str]) -> None:
        """Select the keep-open valve; the hold moves to it on the spot."""
        self.keep_open.update_config(entity_id, name)
        await self._calculate_and_command()

    def startup_settling(self, now: float) -> bool:
        """
        True after a restart until every valve has reported (position and calibration), or for at
        most STARTUP_GRACE. The holds are not persisted: the valves still carry the calibration we
        wrote before the restart, so until we know them all we neither write calibrations nor move
        the keep-open hold. Otherwise the first valve to report gets its hold overwritten and a
        different valve is held instead.
        """
        if self._settled:
            return False
        if self._settle_started is None:
            self._settle_started = now
        known = all(
            z.position_available and not self.calibration_unavailable(z)
            for z in self.zones.values() if z.temp_calib_entity_id
        )
        if known or now - self._settle_started >= STARTUP_GRACE:
            self._settled = True
            _LOGGER.info("Start-up settled after %.0f s (%s)", now - self._settle_started,
                         "all valves reported" if known else "grace time over")
        return not self._settled

    def calibration_unavailable(self, zone: ZoneWrapper) -> bool:
        """True if the zone's calibration entity is missing or offline, so it cannot be written."""
        states = getattr(self.hass, "states", None)
        if states is None or not zone.temp_calib_entity_id:
            return False
        state = states.get(zone.temp_calib_entity_id)
        return state is None or state.state in ("unavailable", "unknown")

    def pump_overrun_active(self, now: float) -> bool:
        """True while the boiler pump may still be running after the boiler was stopped."""
        return self.boiler_off_at is not None and now - self.boiler_off_at < BOILER_PUMP_OVERRUN

    def has_open_valve(self) -> bool:
        """True if at least one valve is measured open."""
        return any(z.measured_opening > 0 for z in self.zones.values())

    # ------------------------------------------------------------------
    # Control loop
    # ------------------------------------------------------------------

    def _evaluate_boiler_demand(self) -> tuple[bool, float, str]:
        """
        Aggregate zone demand.

        Returns (boiler_should_be_on, boiler_demand 0.0-1.0, human readable reason).
        """
        high_priority_demanding = False
        high_priority_demand = 0.0
        low_priority_aggregate = 0.0   # Sum of low-priority valve openings (%)
        low_priority_count = 0
        low_priority_max_demand = 0.0

        for room in self.rooms.values():
            valves = room.active_valves
            high = [v for v in valves if v.counts_as_high_priority]
            low = [v for v in valves if not v.counts_as_high_priority]
            if high:
                high_priority_demanding = high_priority_demanding or any(v.is_demanding_heat for v in high)
                high_priority_demand = max(high_priority_demand, max(v.get_demand_metric() for v in high))
            if low:
                # A zone counts once, whatever its number of valves
                low_priority_aggregate += max(v.effective_opening for v in low)
                low_priority_count += 1
                low_priority_max_demand = max(low_priority_max_demand, max(v.get_demand_metric() for v in low))

        if high_priority_demanding:
            return True, high_priority_demand, "high-priority demand"
        if low_priority_count > 0 and low_priority_aggregate >= 100.0:
            return True, low_priority_max_demand, f"low-priority aggregate {low_priority_aggregate:.0f}%"
        return False, 0.0, "no demand"

    async def _calculate_and_command(self) -> None:
        """Run one control cycle. Calls that arrive while one runs are merged into one more cycle."""
        if self._calc_waiting:
            return
        self._calc_waiting = True
        async with self._calc_lock:
            self._calc_waiting = False
            await self._run_cycle()

    def _decide(self, now: float) -> tuple[float, str]:
        """Keep-open update, boiler decision and interlock. Returns (flow temperature, reason)."""
        if self.startup_settling(now):
            self.keep_open.last_reason = "starting up"
        else:
            self.keep_open.update(now)

        boiler_should_be_on, boiler_demand, reason = self._evaluate_boiler_demand()
        _LOGGER.debug(
            "Boiler decision: %s (%s, demand=%.2f)",
            "ON" if boiler_should_be_on else "OFF", reason, boiler_demand,
        )

        if not boiler_should_be_on:
            for zone in self.zones.values():
                zone.reset_policy_offset()

        flow_temp = demand_to_flow_temp(boiler_demand) if boiler_should_be_on and boiler_demand > 0 else 0.0
        if self.preheating.is_active():
            # Pre-heating only adds to the normal demand, it never lowers it
            preheating_flow = self.preheating.calculate_flow_temp_override()
            if preheating_flow > flow_temp:
                flow_temp = preheating_flow
                reason = "pre-heating"

        if flow_temp > 0 and not self.has_open_valve():
            # Logged once per episode: it can persist for hours (e.g. waiting for a valve to open)
            (_LOGGER.debug if self._interlock_active else _LOGGER.warning)(
                "Interlock: no valve is open, not starting the boiler (%s)", reason
            )
            self._interlock_active = True
            flow_temp = 0.0
            reason = "interlock: no open valve"
        else:
            self._interlock_active = False
        self._log_decision(flow_temp, reason)
        return flow_temp, reason

    def _log_decision(self, flow_temp: float, reason: str) -> None:
        """INFO line with the figures behind a changed decision (flow request or reason)."""
        key = (round(flow_temp, 1), reason)
        if key == self._last_decision:
            return
        self._last_decision = key
        parts = []
        for z in self.zones.values():
            if z.is_demanding_heat or z.held or z.measured_opening > 0 or z.boosted:
                parts.append(
                    f"{z.name}: room={z.current_temp:.1f} (trv={z.trv_temp:.1f}, offset={z.applied_offset():.0f}"
                    f"{'*' if z.offset_in_flight else ''}) target={z.target_temp:.1f} err={z.current_error:+.1f} "
                    f"open={z.measured_opening:.0f}% demand={z.get_demand_metric():.2f}"
                    f"{' HELD' if z.held else ''}{' BOOST' if z.boosted else ''}"
                )
        _LOGGER.info("Decision: flow=%.1f°C (%s) | %s", flow_temp, reason, "; ".join(parts) or "no active zones")

    async def _run_cycle(self) -> None:
        if self.component_enabled is False:
            _LOGGER.debug("Component disabled - skipping boiler calculation")
            return

        now = self.now()
        flow_temp, reason = self._decide(now)
        await self._sync_offsets(now)

        if flow_temp > 0 and self.current_flow_temp == 0 and self.hass and self.valve_open_delay > 0:
            # Starting the boiler from OFF: give the valves time to open, then decide again on what
            # the house looks like by then instead of committing a stale decision
            await asyncio.sleep(self.valve_open_delay)
            now = self.now()
            flow_temp, reason = self._decide(now)
            await self._sync_offsets(now)

        await self.set_opentherm_flow_temp(flow_temp, reason)

    async def set_opentherm_flow_temp(self, flow_temp: float, reason: str = "") -> None:
        """Record the boiler flow temperature request (clamped to MIN..MAX, 0 = OFF)."""
        final_temp = max(MIN_FLOW_TEMP, min(MAX_FLOW_TEMP, flow_temp)) if flow_temp > 0 else 0.0
        previous = self.current_flow_temp

        self.current_flow_temp = final_temp

        if previous > 0 and final_temp == 0:
            self.boiler_off_at = self.now()
        elif final_temp > 0:
            self.boiler_off_at = None

        if (previous > 0) != (final_temp > 0):
            _LOGGER.info(
                "Boiler %s (%s, flow_temp=%.1f°C)",
                "ON" if final_temp > 0 else "OFF", reason or "manual", final_temp,
            )
        else:
            _LOGGER.debug("Flow temperature: requested=%.1f°C, final=%.1f°C", flow_temp, final_temp)

    # ------------------------------------------------------------------
    # TRV calibration offsets (the only writer)
    # ------------------------------------------------------------------

    async def _sync_offsets(self, now: float) -> None:
        """
        Make every TRV's calibration match what its zone wants.

        Writes only on a difference, and no more often than MIN_OFFSET_WRITE_INTERVAL per TRV,
        except for starting a hold (a valve must open now). A calibration the device does not
        keep is rewritten up to MAX_DRIFT_RETRIES times.
        """
        if self.startup_settling(now):
            return
        for zone in self.zones.values():
            if not zone.temp_calib_entity_id or self.calibration_unavailable(zone):
                continue

            want = zone.offset_to_write(self.hold_writes_enabled)
            device_ok = zone.device_offset is not None and abs(zone.device_offset - want) <= 0.01
            pushed_ok = zone.pushed_offset is not None and abs(zone.pushed_offset - want) <= 0.01

            since_write = now - zone.last_offset_write
            if zone.offset_in_flight and since_write > ECHO_GRACE:
                zone.offset_in_flight = False

            if device_ok:
                zone.pushed_offset = want
                zone.offset_in_flight = False
                zone.drift_retries = 0
                continue

            drift = pushed_ok and zone.device_offset is not None and since_write > ECHO_GRACE
            if pushed_ok and not drift:
                continue
            if drift and zone.drift_retries >= MAX_DRIFT_RETRIES:
                continue

            urgent = want == HOLD_TEMP_OFFSET
            if not urgent and since_write < MIN_OFFSET_WRITE_INTERVAL:
                continue

            if drift:
                zone.drift_retries += 1
                _LOGGER.warning(
                    "Zone '%s': calibration is %.1f°C on the device, wanted %.1f°C - rewriting (%d/%d)",
                    zone.name, zone.device_offset, want, zone.drift_retries, MAX_DRIFT_RETRIES,
                )
            await self._async_push_zone_offset(zone, want)
            zone.pushed_offset = want
            zone.offset_in_flight = True
            zone.last_offset_write = now
            zone._refresh_temperature()

    async def _async_push_zone_offset(self, zone: ZoneWrapper, value: float) -> None:
        """Write a temperature offset to the zone's TRV calibration entity."""
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": zone.temp_calib_entity_id, "value": value},
            blocking=False,
        )
        _LOGGER.debug("Zone '%s': offset %.1f°C sent to %s", zone.name, value, zone.temp_calib_entity_id)

    # ------------------------------------------------------------------
    # State export (sensors)
    # ------------------------------------------------------------------

    def get_controller_state(self) -> dict:
        """Snapshot of controller-level state for sensors."""
        return {
            "zones": [
                {"name": z.name, "entity_id": z.entity_id, "state": z.export_zone_state()}
                for z in self.rooms.values()
            ],
            "zone_count": len(self.rooms),
            "current_flow_temp": self.current_flow_temp,
            "keep_open": self.keep_open.get_state(),
        }

    def get_zone_state(self, entity_id: str) -> Optional[dict]:
        """Snapshot of one zone's state, or None if unknown."""
        zone = self.rooms.get(entity_id)
        return zone.export_zone_state() if zone else None
