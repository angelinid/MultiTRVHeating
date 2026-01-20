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

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional

try:
    from homeassistant.helpers.event import async_track_state_change_event
except ImportError:
    # For testing without Home Assistant installed
    async_track_state_change_event = None

try:
    from .zone_wrapper import ZoneWrapper
    from .zone_wrapper import DEFAULT_TEMP_OFFSET
    from .preheating import PreheatingController
    from .pump_discharge import PumpDischargeController
except ImportError:
    from zone_wrapper import ZoneWrapper
    from zone_wrapper import DEFAULT_TEMP_OFFSET
    from preheating import PreheatingController
    from pump_discharge import PumpDischargeController

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger("don_controller")

# =========================================================
# OpenTherm Control Constants
# =========================================================

# Flow temperature = 25°C is minimum when boiler is ON
MIN_FLOW_TEMP = 25.0

# Flow temperature = 60°C is maximum boiler output (safety limit)
MAX_FLOW_TEMP = 60.0

# Temperature offset configuration constants
MIN_TEMP_OFFSET = -5.0  # Minimum offset in degrees Celsius
MAX_TEMP_OFFSET = 5.0   # Maximum offset in degrees Celsius


class MasterController:

    def __init__(self, hass: "HomeAssistant", zone_configs: list[dict]) -> None:
        """
        Initialize the master controller with zone configuration.
        Args:
            hass: Home Assistant core instance
            zone_configs: List of zone configuration dicts from config entry
        """
        self.hass = hass
        self.zones: dict[str, ZoneWrapper] = {}
        
        # ========== Component Enable/Disable ==========
        # Master switch to enable/disable the entire component
        self.component_enabled = False
        
        # ========== Pre-heating Configuration ==========
        self.preheating = PreheatingController(self)
        
        # ========== Pump Discharge Configuration ==========
        # Get discharge TRV from config if available
        discharge_trv_entity_id = zone_configs[0].get('discharge_trv_entity_id') if zone_configs else None
        discharge_trv_name = zone_configs[0].get('discharge_trv_name') if zone_configs else None
        self.pump_discharge = PumpDischargeController(hass, discharge_trv_entity_id, discharge_trv_name)
        
        # ========== OpenTherm Configuration ==========
        # Current OpenTherm flow temperature request (for sensor reporting)
        self.current_flow_temp: float = MIN_FLOW_TEMP
        
        _LOGGER.info("MasterController initializing with %d zones", len(zone_configs))
        
        # Instantiate ZoneWrapper for each configured zone
        for config in zone_configs:
            entity_id = config['entity_id']
            zone_name = config.get('name', entity_id)
            area = config.get('area', 0.0)
            is_high_priority = config.get('is_high_priority', True)  # Boolean priority
            trv_position_entity_id = config.get('trv_position_entity_id')  # TRV position sensor
            temp_calib_entity_id = config.get('temp_calib_entity_id')  # Temperature calibration entity
            ext_temp_entity_id = config.get('ext_temp_entity_id', None)  # Optional external temperature
            
            # Create zone wrapper
            self.zones[entity_id] = ZoneWrapper(
                my_master_controller=self,
                entity_id=entity_id, 
                name=zone_name,
                floor_area_m2=area,
                is_high_priority=is_high_priority,
                trv_position_entity_id=trv_position_entity_id,
                temp_calib_entity_id=temp_calib_entity_id,
                ext_temp_entity_id=ext_temp_entity_id
            )
            _LOGGER.info(
                "MasterController: Registered zone '%s' (%s, area=%.1f m², priority=%s, "
                "position_sensor=%s, calib_entity=%s%s)", 
                zone_name, entity_id, area, "HIGH" if is_high_priority else "LOW",
                trv_position_entity_id or "none", temp_calib_entity_id or "none",
                f", external_temp={ext_temp_entity_id}" if ext_temp_entity_id else ""
            )
        
        # Build list of all entities to monitor
        self.monitored_climate_entities = list(self.zones.keys())
        self.monitored_position_sensors = []  # TRV position sensors
        self.monitored_external_sensors = []  # External temperature sensors
        
        # Add TRV position sensors and external sensors to monitoring lists
        for zone in self.zones.values():
            if zone.trv_position_entity_id:
                self.monitored_position_sensors.append(zone.trv_position_entity_id)
            if zone.ext_temp_entity_id:
                self.monitored_external_sensors.append(zone.ext_temp_entity_id)

    async def async_start_listening(self):
        """
        Start listening for state change events on all monitored zones.
        
        Sets up multiple Home Assistant state change event listeners:
        1. Climate entities - for temperature changes
        2. TRV position sensors - for valve opening changes
        3. External temperature sensors - for better temperature readings
        4. Pre-heating end time entity - for pre-heating activation
        """
        if async_track_state_change_event is None:
            _LOGGER.warning("async_track_state_change_event not available - running in test mode")
            return
        
        _LOGGER.info(
            "MasterController starting listeners: %d climate entities, %d position sensors, %d external sensors",
            len(self.monitored_climate_entities), len(self.monitored_position_sensors),
            len(self.monitored_external_sensors)
        )
        
        # Listen for climate entity state changes (temperature, target, etc.)
        if self.monitored_climate_entities:
            _LOGGER.debug("Setting up listener for climate entities: %s", self.monitored_climate_entities)
            async_track_state_change_event(
                self.hass,
                self.monitored_climate_entities,
                self._async_climate_state_change
            )
        
        # Listen for TRV position sensor changes (valve opening %)
        if self.monitored_position_sensors:
            _LOGGER.debug("Setting up listener for position sensors: %s", self.monitored_position_sensors)
            async_track_state_change_event(
                self.hass,
                self.monitored_position_sensors,
                self._async_position_change
            )
        
        # Listen for external temperature sensor changes
        if self.monitored_external_sensors:
            _LOGGER.debug("Setting up listener for external sensors: %s", self.monitored_external_sensors)
            async_track_state_change_event(
                self.hass,
                self.monitored_external_sensors,
                self._async_external_temp_change
            )
        
        _LOGGER.info("All listeners set up successfully")
        
    async def _async_climate_state_change(self, event) -> None:
        """
        Event handler: Called when a climate entity's state changes.
        
        Updates zone temperature and target temperature information.
        Does NOT trigger boiler recalculation - only position changes do.
        
        Args:
            event: Home Assistant state change event
        """
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        
        _LOGGER.debug("Climate state change for entity_id=%s", entity_id)
        
        zone = self.zones.get(entity_id)
        if zone and new_state:
            zone.update_from_state(new_state)
            _LOGGER.debug("Zone '%s' updated from climate entity", zone.name)
    
    async def _async_position_change(self, event) -> None:
        """
        Event handler: Called when a TRV position sensor's state changes.
        
        Updates zone TRV valve opening percentage and recalculates demand.
        TRV position entity IDs follow pattern: sensor.<entity_name_lower>_position
        
        Args:
            event: Home Assistant state change event
        """
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        
        _LOGGER.debug("TRV position change for entity_id=%s, state=%s", entity_id, new_state.state if new_state else None)
        
        # Find which zone this position sensor belongs to
        for zone in self.zones.values():
            if zone.trv_position_entity_id == entity_id and new_state:
                # Skip unknown/unavailable states - sensor hasn't been initialized yet
                if new_state.state in ['unknown', 'unavailable', None, '']:
                    _LOGGER.debug(
                        "TRV position for zone '%s' is %s, skipping update",
                        zone.name, new_state.state or 'None'
                    )
                    break
                
                try:
                    # Extract opening percentage from sensor state
                    opening_percent = float(new_state.state)
                    if zone.update_trv_opening(opening_percent):
                        # Need to update temperature offset in zone
                        await self.hass.services.async_call(
                            "number",
                            "set_value",
                            {
                                "entity_id": zone.temp_calib_entity_id,
                                "value": zone.temperature_offset
                            },
                            blocking=False,
                        )
                        _LOGGER.debug(
                            "Zone '%s': Applied offset %.1f°C to %s",
                            zone.name, zone.temperature_offset, zone.temp_calib_entity_id
                        )
                    _LOGGER.debug(
                        "Zone '%s': TRV position updated to %.0f%% (from %s)",
                        zone.name, opening_percent, entity_id
                    )
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "Error reading TRV position from %s (state='%s'): %s",
                        entity_id, new_state.state, e
                    )
                break
        
        # Recalculate boiler command
        await self._calculate_and_command()
    
    async def _async_external_temp_change(self, event) -> None:
        """
        Event handler: Called when an external temperature sensor's state changes.
        
        Updates the external temperature reading for the zone.
        Does NOT trigger boiler recalculation - only position changes do.
        
        Args:
            event: Home Assistant state change event
        """
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        
        _LOGGER.debug("External temp sensor change for entity_id=%s, state=%s", entity_id, new_state.state if new_state else None)
        
        # Find which zone this external sensor belongs to
        for zone in self.zones.values():
            if zone.ext_temp_entity_id == entity_id and new_state:
                # Skip unknown/unavailable states - sensor hasn't been initialized yet
                if new_state.state in ['unknown', 'unavailable', None, '']:
                    _LOGGER.debug(
                        "External temp for zone '%s' is %s, skipping update",
                        zone.name, new_state.state or 'None'
                    )
                    break
                
                try:
                    # Extract temperature from sensor state
                    ext_temp = float(new_state.state)
                    zone.update_external_temperature(ext_temp)
                    _LOGGER.info(
                        "Zone '%s': External temp updated to %.1f°C (from %s)",
                        zone.name, ext_temp, entity_id
                    )
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "Error reading external temp from %s (state='%s'): %s",
                        entity_id, new_state.state, e
                    )
                break

    def _is_preheating(self) -> bool:
        """Check if pre-heating mode is currently active. Delegates to PreheatingController."""
        return self.preheating.is_active()
    
    async def _calculate_and_command(self) -> None:
        """
        Main heating control logic. Called whenever any zone state changes.
        
        Implements all control requirements:
        1. Listen to TRV opening changes (requirement #1) ✓ via position sensor
        2. Aggregate heating requests (requirement #2) ✓ via demand metrics
        3. Control boiler accordingly (requirement #3) ✓ via flow temp command
        4. Two priority levels (requirement #4) ✓ via boolean is_high_priority
        5. High priority trigger at any opening (requirement #5) ✓ via > 0% check
        6. Low priority trigger at 100% or aggregate (requirement #6) ✓ calculated here
        7. Boiler intensity = highest demand (requirement #7) ✓ calculated here
        8. Temperature offset control (requirement #8) ✓ via calibration entity
        
        Algorithm:
        1. Check if any high-priority zone is demanding heat -> turn on boiler
        2. Calculate aggregate demand from low-priority zones
        3. If aggregate >= 100%, turn on boiler
        4. Check if all zones are at 0% or closing (descending trajectory) -> can turn off
        5. Calculate boiler intensity from highest zone demand
        6. Convert demand metric to flow temperature and command boiler
        7. Export temperature offset to Home Assistant calibration entities
        """
        if self.component_enabled is False:
            _LOGGER.info("Component disabled - skipping boiler calculation")
            return
        
        _LOGGER.debug("Calculating boiler command from %d zones", len(self.zones))
        
        # ========== Track high-priority zone demand ==========
        high_priority_demanding = False  # Is any high-priority zone demanding?
        high_priority_demand = 0.0       # Highest demand from high-priority zones
        
        # ========== Track low-priority zone aggregation ==========
        low_priority_aggregate = 0.0  # Sum of low-priority zone openings
        low_priority_count = 0         # Number of low-priority zones
        low_priority_max_demand = 0.0  # Highest demand from low-priority zones
        
        # ========== Analyze all zones ==========
        for zone in self.zones.values():
            # Skip discharge TRV - it shouldn't influence boiler control
            if self.pump_discharge.is_discharge_valve(zone.entity_id):
                _LOGGER.debug(
                    "Zone '%s' is discharge valve - excluding from boiler calculations",
                    zone.name
                )
                continue
            
            demand = zone.get_demand_metric()
            
            if zone.is_high_priority:
                # High priority: Can trigger boiler at any opening > 0%
                if zone.is_demanding_heat:
                    high_priority_demanding = True
                    _LOGGER.debug(
                        "High-priority zone '%s' is demanding heat (opening=%.0f%%)",
                        zone.name, zone.trv_opening_percent
                    )
                high_priority_demand = max(high_priority_demand, demand)
            else:
                # Low priority: Track for aggregation
                # Requirement #6: Aggregate multiple low-priority zones
                low_priority_aggregate += zone.trv_opening_percent
                low_priority_count += 1
                low_priority_max_demand = max(low_priority_max_demand, demand)
                
                _LOGGER.debug(
                    "Low-priority zone '%s': opening=%.0f%% (running aggregate=%.0f%%)",
                    zone.name, zone.trv_opening_percent, low_priority_aggregate
                )
        
        # ========== Determine if boiler should be ON or OFF ==========
        boiler_should_be_on = False
        boiler_demand = 0.0
        
        # Condition 1: Any high-priority zone demanding heat?
        if high_priority_demanding:
            boiler_should_be_on = True
            boiler_demand = high_priority_demand
            _LOGGER.info("Boiler command: ON (high-priority demand)")
        
        # Condition 2: Aggregate low-priority zones >= 100%?
        # Requirement #6: Multiple low-priority zones can aggregate
        elif low_priority_count > 0 and low_priority_aggregate >= 100.0:
            boiler_should_be_on = True
            boiler_demand = low_priority_max_demand
            _LOGGER.info(
                "Boiler command: ON (low-priority aggregate: %.0f%%)",
                low_priority_aggregate
            )
        
        # Boiler OFF: Neither condition met
        # Reset all zone temperature offsets when boiler shuts down
        else:
            boiler_should_be_on = False
            boiler_demand = 0.0
            _LOGGER.info("Boiler command: OFF (no demand)")
            
            # Reset temperature offsets for all zones when boiler shuts off
            await self._reset_all_zone_offsets()
        
        # ========== Calculate boiler intensity (Requirement #7) ==========
        # Boiler intensity is proportional to the highest heat demand from any zone
        # Demand metric: 0.0 = off, 1.0 = max
        # Flow temperature: MIN_FLOW_TEMP (5°C) = off, MAX_FLOW_TEMP (80°C) = max
        
        # Check if pre-heating is active - if yes, use pre-heating override instead
        if self._is_preheating():
            flow_temp = self.preheating.calculate_flow_temp_override()
            _LOGGER.info(
                "Pre-heating active: Using override flow_temp=%.1f°C (instead of normal demand-based %.1f°C)",
                flow_temp,
                MIN_FLOW_TEMP + (boiler_demand * (MAX_FLOW_TEMP - MIN_FLOW_TEMP)) if boiler_should_be_on and boiler_demand > 0 else MIN_FLOW_TEMP
            )
        elif boiler_should_be_on and boiler_demand > 0:
            # Convert demand metric to flow temperature
            # Linear interpolation: demand=0.0 -> MIN_FLOW_TEMP, demand=1.0 -> MAX_FLOW_TEMP
            flow_temp = MIN_FLOW_TEMP + (boiler_demand * (MAX_FLOW_TEMP - MIN_FLOW_TEMP))
            _LOGGER.info(
                "Boiler intensity: demand=%.2f -> flow_temp=%.1f°C",
                boiler_demand, flow_temp
            )
        else:
            # Boiler OFF: Set to 0°C
            flow_temp = 0.0
            _LOGGER.info("Boiler OFF: Setting flow temp to 0°C")
        
        # ========== Send command to boiler ==========
        self.set_opentherm_flow_temp(flow_temp)
        
        # ========== Update pump discharge controller ==========
        # Pump discharge keeps one TRV open after boiler shuts off to circulate water
        await self.pump_discharge.evaluate_and_update(boiler_should_be_on)
    
    def set_opentherm_flow_temp(self, flow_temp: float) -> None:
        """
        Command the boiler's flow temperature via OpenTherm integration.
        
        Calls Home Assistant service to set the number entity that controls
        the ESPHome OpenTherm device. Includes safety clamping to min/max.
        
        Args:
            flow_temp: Target flow temperature in °C (clamped to MIN_FLOW_TEMP .. MAX_FLOW_TEMP)
        """
        
        # Clamp to safe physical limits
        final_temp = max(MIN_FLOW_TEMP, min(MAX_FLOW_TEMP, flow_temp)) if flow_temp > 0 else 0.0
        
        # Track the current flow temperature for sensor reporting
        self.current_flow_temp = final_temp
        
        _LOGGER.debug(
            "Setting OpenTherm flow temperature: requested=%.1f°C, final=%.1f°C",
            flow_temp, final_temp
        )
    
    async def _reset_all_zone_offsets(self) -> None:
        """
        Reset all zone temperature offsets to 0°C.
        
        Called when boiler shuts down to reset all TRV calibration offsets
        to neutral position. This prevents TRVs from continuing to open
        after the boiler stops.
        """
        for zone in self.zones.values():
            zone.temperature_offset = DEFAULT_TEMP_OFFSET
            await self.hass.services.async_call(
                "number",
                "set_value",
                {
                    "entity_id": zone.temp_calib_entity_id,
                    "value": zone.temperature_offset
                },
                blocking=False,
            )
            _LOGGER.debug("Zone '%s' temperature offset set to %d°C", zone.name, zone.temperature_offset)
        
        _LOGGER.debug("All zone temperature offsets reset to 0°C")

    def get_controller_state(self) -> dict:
        zones_state = []
        for zone in self.zones.values():
            zones_state.append({
                "name": zone.name,
                "entity_id": zone.entity_id,
                "state": zone.export_zone_state()
            })
        
        return {
            "zones": zones_state,
            "zone_count": len(self.zones),
            "current_flow_temp": self.current_flow_temp,
            "pump_discharge": self.pump_discharge.get_discharge_state(),
        }
    
    def get_zone_state(self, entity_id: str) -> Optional[dict]:
        zone = self.zones.get(entity_id)
        if zone:
            return zone.export_zone_state()
        return None