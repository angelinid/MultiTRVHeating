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
from typing import TYPE_CHECKING, Optional

try:
    from .zone_wrapper import ZoneWrapper
except ImportError:
    from zone_wrapper import ZoneWrapper

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger("don_controller")

# =========================================================
# OpenTherm Control Constants
# =========================================================

# Entity ID where OpenTherm flow temperature is set (Home Assistant number entity)
OPEN_THERM_FLOW_TEMP_ENTITY = "number.opentherm_flow_temp"

# Flow temperature = 5°C is boiler OFF signal (minimum safe temperature)
MIN_FLOW_TEMP = 5.0

# Flow temperature = 80°C is maximum boiler output (safety limit)
MAX_FLOW_TEMP = 80.0

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
        """
        _LOGGER.info(
            "MasterController starting listeners: %d climate entities, %d position sensors, %d external sensors",
            len(self.monitored_climate_entities), len(self.monitored_position_sensors),
            len(self.monitored_external_sensors)
        )
        
        # Listen for climate entity state changes (temperature, target, etc.)
        if self.monitored_climate_entities:
            self.hass.helpers.event.async_track_state_change_event(
                self.monitored_climate_entities,
                self._async_climate_state_change
            )
        
        # Listen for TRV position sensor changes (valve opening %)
        if self.monitored_position_sensors:
            self.hass.helpers.event.async_track_state_change_event(
                self.monitored_position_sensors,
                self._async_position_change
            )
        
        # Listen for external temperature sensor changes
        if self.monitored_external_sensors:
            self.hass.helpers.event.async_track_state_change_event(
                self.monitored_external_sensors,
                self._async_external_temp_change
            )
        
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
        
        _LOGGER.debug("TRV position change for entity_id=%s", entity_id)
        
        # Find which zone this position sensor belongs to
        for zone in self.zones.values():
            if zone.trv_position_entity_id == entity_id and new_state:
                try:
                    # Extract opening percentage from sensor state
                    opening_percent = float(new_state.state)
                    zone.update_trv_opening(opening_percent)
                    _LOGGER.debug(
                        "Zone '%s': TRV position updated to %.0f%%",
                        zone.name, opening_percent
                    )
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "Error reading TRV position from %s: %s",
                        entity_id, e
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
        
        _LOGGER.debug("External temp sensor change for entity_id=%s", entity_id)
        
        # Find which zone this external sensor belongs to
        for zone in self.zones.values():
            if zone.ext_temp_entity_id == entity_id and new_state:
                try:
                    # Extract temperature from sensor state
                    ext_temp = float(new_state.state)
                    zone.update_external_temperature(ext_temp)
                    _LOGGER.debug(
                        "Zone '%s': External temp updated to %.1f°C",
                        zone.name, ext_temp
                    )
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "Error reading external temp from %s: %s",
                        entity_id, e
                    )
                break

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
        4. Calculate boiler intensity from highest zone demand
        5. Convert demand metric to flow temperature and command boiler
        6. Export temperature offset to Home Assistant calibration entities
        """
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
        else:
            boiler_should_be_on = False
            boiler_demand = 0.0
            _LOGGER.info("Boiler command: OFF (no demand)")
        
        # ========== Calculate boiler intensity (Requirement #7) ==========
        # Boiler intensity is proportional to the highest heat demand from any zone
        # Demand metric: 0.0 = off, 1.0 = max
        # Flow temperature: MIN_FLOW_TEMP (5°C) = off, MAX_FLOW_TEMP (80°C) = max
        
        if boiler_should_be_on and boiler_demand > 0:
            # Convert demand metric to flow temperature
            # Linear interpolation: demand=0.0 -> MIN_FLOW_TEMP, demand=1.0 -> MAX_FLOW_TEMP
            flow_temp = MIN_FLOW_TEMP + (boiler_demand * (MAX_FLOW_TEMP - MIN_FLOW_TEMP))
            _LOGGER.info(
                "Boiler intensity: demand=%.2f -> flow_temp=%.1f°C",
                boiler_demand, flow_temp
            )
        else:
            # Boiler OFF: Set to minimum safe temperature
            flow_temp = MIN_FLOW_TEMP
            _LOGGER.info("Boiler OFF: Setting flow temp to MIN (%.1f°C)", MIN_FLOW_TEMP)
        
        # ========== Send command to boiler ==========
        await self.async_set_opentherm_flow_temp(flow_temp)
        
        # ========== Export TRV temperature offsets (Requirement #8) ==========
        # Temperature offsets are now set in zone_wrapper when valve opens/closes
        # Just export the current offset value to Home Assistant for visibility
        await self._export_temperature_offsets()
    
    async def _export_temperature_offsets(self, zone) -> None:
        """
        Export current TRV temperature offset values to Home Assistant.
        
        The offset is calculated in zone_wrapper based on valve state:
        - Valve open: offset = -2.0°C
        - Valve closed: offset = 0°C
        
        This method exports those values to the calibration entities
        so they're visible and can be monitored in Home Assistant.
        """
        if not zone.temp_calib_entity_id:
            return  # Skip zones without calibration entity
        
        _LOGGER.debug(
            "Zone '%s': Exporting offset %.1f°C to %s",
            zone.name, zone.temperature_offset, zone.temp_calib_entity_id
        )
        
        # Export current offset to Home Assistant
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": zone.temp_calib_entity_id, "value": zone.temperature_offset},
            blocking=False,
        )

    async def async_set_opentherm_flow_temp(self, flow_temp: float) -> None:
        """
        Command the boiler's flow temperature via OpenTherm integration.
        
        Calls Home Assistant service to set the number entity that controls
        the ESPHome OpenTherm device. Includes safety clamping to min/max.
        
        Args:
            flow_temp: Target flow temperature in °C (clamped to MIN_FLOW_TEMP .. MAX_FLOW_TEMP)
        """
        
        # Clamp to safe physical limits
        final_temp = max(MIN_FLOW_TEMP, min(MAX_FLOW_TEMP, flow_temp))
        
        _LOGGER.debug(
            "Setting OpenTherm flow temperature: requested=%.1f°C, final=%.1f°C",
            flow_temp, final_temp
        )
        
        # Call Home Assistant number service to update the entity
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": OPEN_THERM_FLOW_TEMP_ENTITY, "value": final_temp},
            blocking=False,
        )

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
            "zone_count": len(self.zones)
        }
    
    def get_zone_state(self, entity_id: str) -> Optional[dict]:
        zone = self.zones.get(entity_id)
        if zone:
            return zone.export_zone_state()
        return None