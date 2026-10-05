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
from typing import TYPE_CHECKING, Callable, Optional

try:
    from homeassistant.helpers.event import async_track_state_change_event
except ImportError:
    # For testing without Home Assistant installed
    async_track_state_change_event = None

try:
    from .const import LOGGER_NAME, MAX_FLOW_TEMP, MIN_FLOW_TEMP
    from .preheating import PreheatingController
    from .pump_discharge import PumpDischargeController
    from .zone_wrapper import DEFAULT_TEMP_OFFSET, ZoneWrapper
except ImportError:
    from const import LOGGER_NAME, MAX_FLOW_TEMP, MIN_FLOW_TEMP
    from preheating import PreheatingController
    from pump_discharge import PumpDischargeController
    from zone_wrapper import DEFAULT_TEMP_OFFSET, ZoneWrapper

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(LOGGER_NAME)

# Sensor states that carry no usable reading.
_INVALID_STATES = ("unknown", "unavailable", None, "")

# Seconds to wait for valves to open before turning the boiler on from OFF.
VALVE_OPEN_DELAY = 20


def demand_to_flow_temp(demand: float) -> float:
    """Map a demand metric (0.0-1.0) linearly onto MIN_FLOW_TEMP..MAX_FLOW_TEMP."""
    return MIN_FLOW_TEMP + demand * (MAX_FLOW_TEMP - MIN_FLOW_TEMP)


class MasterController:
    """
    Aggregates zone demand and drives the boiler flow temperature.

    Boiler decision (evaluated on every climate / TRV position change):
    - ON if any high-priority zone is demanding heat (intensity = highest high-priority demand)
    - ON if the low-priority valve openings sum to >= 100% (intensity = highest low-priority demand)
    - OFF otherwise, and all zone temperature offsets are reset
    Pre-heating, when active, overrides the flow temperature. The pump discharge
    valve (while discharging) is excluded from the calculation.
    """

    def __init__(self, hass: "HomeAssistant", zone_configs: list[dict]) -> None:
        self.hass = hass
        self.zones: dict[str, ZoneWrapper] = {}

        # Master enable switch for the whole component (set by ComponentEnableSwitch).
        self.component_enabled = False

        self.preheating = PreheatingController(self)

        # Pump discharge settings live on the first zone's config.
        first = zone_configs[0] if zone_configs else {}
        self.pump_discharge = PumpDischargeController(
            hass, first.get('discharge_trv_entity_id'), first.get('discharge_trv_name')
        )

        # Last commanded OpenTherm flow temperature (for sensor reporting).
        self.current_flow_temp: float = MIN_FLOW_TEMP

        for config in zone_configs:
            zone = ZoneWrapper(
                my_master_controller=self,
                entity_id=config['entity_id'],
                name=config.get('name', config['entity_id']),
                floor_area_m2=config.get('area', 0.0),
                is_high_priority=config.get('is_high_priority', True),
                trv_position_entity_id=config.get('trv_position_entity_id'),
                temp_calib_entity_id=config.get('temp_calib_entity_id'),
                ext_temp_entity_id=config.get('ext_temp_entity_id'),
            )
            self.zones[zone.entity_id] = zone
            _LOGGER.info(
                "Registered zone '%s' (%s, area=%.1f m², priority=%s, position_sensor=%s, "
                "calib_entity=%s, external_temp=%s)",
                zone.name, zone.entity_id, zone.floor_area_m2,
                "HIGH" if zone.is_high_priority else "LOW",
                zone.trv_position_entity_id or "none", zone.temp_calib_entity_id or "none",
                zone.ext_temp_entity_id or "none",
            )

        self.monitored_climate_entities = list(self.zones)
        self.monitored_position_sensors = [
            z.trv_position_entity_id for z in self.zones.values() if z.trv_position_entity_id
        ]
        self.monitored_external_sensors = [
            z.ext_temp_entity_id for z in self.zones.values() if z.ext_temp_entity_id
        ]

    # ------------------------------------------------------------------
    # Event listeners
    # ------------------------------------------------------------------

    async def async_start_listening(self) -> None:
        """Subscribe to climate, TRV position and external temperature state changes."""
        if async_track_state_change_event is None:
            _LOGGER.warning("async_track_state_change_event not available - running in test mode")
            return

        listeners: list[tuple[list[str], Callable]] = [
            (self.monitored_climate_entities, self._async_climate_state_change),
            (self.monitored_position_sensors, self._async_position_change),
            (self.monitored_external_sensors, self._async_external_temp_change),
        ]
        for entity_ids, handler in listeners:
            if entity_ids:
                async_track_state_change_event(self.hass, entity_ids, handler)

        _LOGGER.info(
            "Listening to %d climate entities, %d position sensors, %d external sensors",
            len(self.monitored_climate_entities), len(self.monitored_position_sensors),
            len(self.monitored_external_sensors),
        )

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

        await self._calculate_and_command()

    async def _async_position_change(self, event) -> None:
        """TRV position sensor changed: update valve opening/offset and recalculate."""
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        zone = self._zone_for_sensor('trv_position_entity_id', entity_id) if new_state else None

        if zone:
            opening_percent = self._read_float_state(zone, new_state, entity_id, "TRV position")
            if opening_percent is not None and zone.update_trv_opening(opening_percent):
                await self._async_push_zone_offset(zone)

        await self._calculate_and_command()

    async def _async_external_temp_change(self, event) -> None:
        """External temperature sensor changed: store the reading (no recalculation)."""
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        zone = self._zone_for_sensor('ext_temp_entity_id', entity_id) if new_state else None

        if zone:
            ext_temp = self._read_float_state(zone, new_state, entity_id, "external temperature")
            if ext_temp is not None:
                zone.update_external_temperature(ext_temp)

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

        for zone in self.zones.values():
            if self.pump_discharge.is_discharge_valve(zone.entity_id):
                _LOGGER.debug("Zone '%s' is discharging - excluded from boiler calculation", zone.name)
                continue

            demand = zone.get_demand_metric()
            if zone.is_high_priority:
                high_priority_demanding = high_priority_demanding or zone.is_demanding_heat
                high_priority_demand = max(high_priority_demand, demand)
            else:
                low_priority_aggregate += zone.trv_opening_percent
                low_priority_count += 1
                low_priority_max_demand = max(low_priority_max_demand, demand)

        if high_priority_demanding:
            return True, high_priority_demand, "high-priority demand"
        if low_priority_count > 0 and low_priority_aggregate >= 100.0:
            return True, low_priority_max_demand, f"low-priority aggregate {low_priority_aggregate:.0f}%"
        return False, 0.0, "no demand"

    async def _calculate_and_command(self) -> None:
        """Decide boiler ON/OFF and flow temperature, command it, then update pump discharge."""
        if self.component_enabled is False:
            _LOGGER.debug("Component disabled - skipping boiler calculation")
            return

        boiler_should_be_on, boiler_demand, reason = self._evaluate_boiler_demand()
        _LOGGER.debug(
            "Boiler decision: %s (%s, demand=%.2f)",
            "ON" if boiler_should_be_on else "OFF", reason, boiler_demand,
        )

        if not boiler_should_be_on:
            await self._reset_all_zone_offsets()

        if self.preheating.is_active():
            flow_temp = self.preheating.calculate_flow_temp_override()
            reason = "pre-heating"
        elif boiler_should_be_on and boiler_demand > 0:
            flow_temp = demand_to_flow_temp(boiler_demand)
        else:
            flow_temp = 0.0

        await self.set_opentherm_flow_temp(flow_temp, reason)
        await self.pump_discharge.evaluate_and_update(boiler_should_be_on)

    async def set_opentherm_flow_temp(self, flow_temp: float, reason: str = "") -> None:
        """
        Record the boiler flow temperature request (clamped to MIN..MAX, 0 = OFF).

        When switching the boiler on from OFF, waits VALVE_OPEN_DELAY seconds first
        so the valves can open (only with a real HA instance).
        """
        final_temp = max(MIN_FLOW_TEMP, min(MAX_FLOW_TEMP, flow_temp)) if flow_temp > 0 else 0.0
        previous = self.current_flow_temp

        if previous == 0 and final_temp > 0 and self.hass:
            await asyncio.sleep(VALVE_OPEN_DELAY)

        self.current_flow_temp = final_temp

        if (previous > 0) != (final_temp > 0):
            _LOGGER.info(
                "Boiler %s (%s, flow_temp=%.1f°C)",
                "ON" if final_temp > 0 else "OFF", reason or "manual", final_temp,
            )
        else:
            _LOGGER.debug("Flow temperature: requested=%.1f°C, final=%.1f°C", flow_temp, final_temp)

    async def _async_push_zone_offset(self, zone: ZoneWrapper) -> None:
        """Write the zone's current temperature offset to its TRV calibration entity."""
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": zone.temp_calib_entity_id, "value": zone.temperature_offset},
            blocking=False,
        )
        _LOGGER.debug(
            "Zone '%s': offset %.1f°C sent to %s",
            zone.name, zone.temperature_offset, zone.temp_calib_entity_id,
        )

    async def _reset_all_zone_offsets(self) -> None:
        """Reset every zone's TRV temperature offset to neutral (called when the boiler is OFF)."""
        for zone in self.zones.values():
            zone.temperature_offset = DEFAULT_TEMP_OFFSET
            await self._async_push_zone_offset(zone)

    # ------------------------------------------------------------------
    # State export (sensors)
    # ------------------------------------------------------------------

    def get_controller_state(self) -> dict:
        """Snapshot of controller-level state for sensors."""
        return {
            "zones": [
                {"name": z.name, "entity_id": z.entity_id, "state": z.export_zone_state()}
                for z in self.zones.values()
            ],
            "zone_count": len(self.zones),
            "current_flow_temp": self.current_flow_temp,
            "pump_discharge": self.pump_discharge.get_discharge_state(),
        }

    def get_zone_state(self, entity_id: str) -> Optional[dict]:
        """Snapshot of one zone's state, or None if unknown."""
        zone = self.zones.get(entity_id)
        return zone.export_zone_state() if zone else None
