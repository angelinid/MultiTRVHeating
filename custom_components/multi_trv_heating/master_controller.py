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
            priority = config.get('priority', 1.0)  # Default: normal priority
            ext_temp_entity_id = config.get('ext_temp_entity_id', None)  # Optional external temperature tracking
            
            # Create zone wrapper
            self.zones[entity_id] = ZoneWrapper(
                entity_id=entity_id, 
                name=zone_name,
                floor_area_m2=area,
                priority=priority,
                ext_temp_entity_id=ext_temp_entity_id
            )
            _LOGGER.info(
                "MasterController: Registered zone '%s' (%s, area=%.1f m², priority=%.2f%s)", 
                zone_name, entity_id, area, priority,
                f", External Temp tracking" if ext_temp_entity_id else ""
            )
        
        # List of all entities to monitor for Home Assistant state change events
        # This includes both climate entities and optional external temperature entities
        self.monitored_entity_ids = list(self.zones.keys())
        
        # Add external temperature entities to monitoring list
        for zone in self.zones.values():
            if zone.ext_temp_entity_id:
                self.monitored_entity_ids.append(zone.ext_temp_entity_id)

    async def async_start_listening(self):
        """
        Start listening for state change events on all monitored zones.
        
        Sets up Home Assistant state change event listener that will call
        _async_hvac_demand_change whenever a zone's climate entity changes state.
        """
        _LOGGER.info("MasterController starting to listen to %s zones.", len(self.zones))
        
        # Listen for state changes on all zone climate entities
        # Each state change triggers _async_hvac_demand_change event handler
        self.hass.helpers.event.async_track_state_change_event(
            self.monitored_entity_ids,
            self._async_hvac_demand_change
        )
        
    async def _async_hvac_demand_change(self, event) -> None:
        """
        Event handler: Called when any monitored entity's state changes.
        
        Handles:
        - Climate entity changes: Updates temperature, target, HVAC action
        - External temperature sensor changes: Updates external temperature reading
        
        After any state change, triggers boiler control logic recalculation.
        
        Args:
            event: Home Assistant state change event containing entity_id and new_state
        """
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        
        _LOGGER.debug("State change event received for entity_id=%s", entity_id)
        
        # Check if this is a climate entity update (main zone entity)
        zone = self.zones.get(entity_id)
        if zone and new_state:
            # Climate entity update (temperature, target, etc.)
            zone.update_from_state(new_state)
            _LOGGER.debug("Zone '%s' updated from climate entity state", zone.name)
        else:
            # Check if this is an external temperature sensor update
            # Requirement #9: Support external temperature sensors for zones
            temperature_updated = False
            for zone in self.zones.values():
                if zone.ext_temp_entity_id == entity_id and new_state:
                    try:
                        # Extract temperature from state
                        ext_temp = float(new_state.state)
                        zone.update_external_temperature(ext_temp)
                        temperature_updated = True
                        _LOGGER.debug(
                            "Zone '%s': External temp sensor updated to %.1f°C",
                            zone.name, ext_temp
                        )
                    except (ValueError, TypeError) as e:
                        _LOGGER.warning(
                            "Error reading external temperature from %s: %s",
                            entity_id, e
                        )
                    break
            
            if not zone and not temperature_updated:
                _LOGGER.warning("Unknown entity_id received: %s", entity_id)
        
        # Recalculate boiler command based on all zones' current states
        await self._calculate_and_command()

    async def _calculate_and_command(self) -> None:
        """
        Main heating control logic. Called whenever any zone state changes.
        
        Implements all control requirements:
        1. Listen to TRV opening changes (requirement #1) ✓ via event handler
        2. Aggregate heating requests (requirement #2) ✓ via demand metrics
        3. Control boiler accordingly (requirement #3) ✓ via flow temp command
        4. Two priority levels (requirement #4) ✓ via zone priority settings
        5. High priority trigger at 25% (requirement #5) ✓ via ZoneWrapper logic
        6. Low priority trigger at 100% or aggregate (requirement #6) ✓ calculated here
        7. Boiler intensity = highest demand (requirement #7) ✓ calculated here
        
        Algorithm:
        1. Check if any high-priority zone is demanding heat -> turn on boiler
        2. Calculate aggregate demand from low-priority zones
        3. If aggregate >= 100%, turn on boiler
        4. Calculate boiler intensity from highest zone demand
        5. Convert demand metric to flow temperature and command boiler
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
                # High priority: Can trigger boiler at 25% opening
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
        
        # ========== Optional: Adjust TRV temperature offsets (Requirement #8) ==========
        # When a zone is demanding heat, reduce its temperature offset to make valve open more
        # This is a future enhancement that can be enabled when needed
        for zone in self.zones.values():
            if zone.is_demanding_heat and zone.current_error > 0:
                # Zone is cold and valve is open: reduce offset to encourage opening
                # (This tricks the valve into thinking it's even colder)
                if zone.temperature_offset > MIN_TEMP_OFFSET:
                    new_offset = max(MIN_TEMP_OFFSET, zone.temperature_offset - 1.0)
                    zone.set_temperature_offset(new_offset)
                    _LOGGER.debug(
                        "Zone '%s': Reduced temp offset to %.1f°C (current error=%.1f°C)",
                        zone.name, new_offset, zone.current_error
                    )
            elif zone.current_error <= 0:
                # Zone has reached target: reset offset back to 0
                if zone.temperature_offset != 0.0:
                    zone.reset_temperature_offset()
                    _LOGGER.info("Zone '%s': Reached target, offset reset", zone.name)

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