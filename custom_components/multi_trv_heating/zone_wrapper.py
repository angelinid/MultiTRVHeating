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

import time
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from master_controller import MasterController

_LOGGER = logging.getLogger("don_controller")

# Temperature offset configuration constants
# Zigbee TRV valves typically support temperature offset to compensate for readings
MIN_TEMP_OFFSET = -5.0  # Minimum offset in degrees Celsius
MAX_TEMP_OFFSET = 5.0   # Maximum offset in degrees Celsius
DEFAULT_TEMP_OFFSET = 0.0  # Default (no offset)
HEATING_TEMP_OFFSET = -2.0  # Offset applied when heating

# TRV opening thresholds for heat demand triggers
HIGH_PRIORITY_MIN_OPENING = 25.0  # High priority zones trigger boiler at 25% opening
LOW_PRIORITY_MIN_OPENING = 100.0  # Low priority zones only trigger at 100% opening


class ZoneWrapper:
    """
    Wrapper class for a single heating zone (room/area with a TRV valve).
    
    Each zone monitors:
    - Current and target temperature from the climate entity
    - TRV valve opening percentage (0-100%)
    - Temperature offset for the TRV (optional feature)
    - Priority level (high vs low priority)
    - Optional external temperature sensor
    
    The wrapper calculates:
    - Temperature error (target - current)
    - Demand metric for boiler control
    - Whether the zone is demanding heat based on opening % and priority
    
    Temperature offset feature (requirement #8):
    - When a zone needs heat, reduce the TRV offset (makes it read colder)
    - This tricks the valve into opening more while the boiler heats
    - Reset offset to 0 when target temperature is reached
    """

    def __init__(self, entity_id: str, name: str, floor_area_m2: float = 0.0,
                 is_high_priority: bool = True, trv_position_entity_id: Optional[str] = None,
                 temp_calib_entity_id: Optional[str] = None, ext_temp_entity_id: Optional[str] = None,
                 my_master_controller: Optional['MasterController'] = None) -> None:
        """
        Initialize a zone wrapper.
        
        Args:
            entity_id: Home Assistant climate entity ID (e.g., "climate.living_room")
            name: User-friendly zone name
            floor_area_m2: Floor area in square meters (for demand calculation)
            is_high_priority: Boolean - True=high priority (any opening), False=low priority (needs 100%)
            trv_position_entity_id: TRV position sensor entity (e.g., "sensor.radiator_1_position")
            temp_calib_entity_id: Temperature calibration number entity (e.g., "number.radiator_1_local_temperature_calibration")
            ext_temp_entity_id: Optional external temperature sensor entity ID
            my_master_controller: Optional reference to MasterController for offset export
        """
        # ========== Zone Identification ==========
        self.entity_id = entity_id
        self.name = name
        self.floor_area_m2 = floor_area_m2
        
        # ========== Priority Configuration ==========
        # Priority determines when this zone can trigger the boiler:
        # - High priority (True): Can trigger boiler at any TRV opening (including 25%)
        # - Low priority (False): Needs 100% opening or aggregates with other low zones
        self.is_high_priority = is_high_priority
        
        # ========== Temperature Tracking ==========
        self.current_temp = 20.0  # Current room temperature (°C)
        self.target_temp = 20.0   # Target temperature set on the TRV (°C)
        self.current_error = 0.0  # target_temp - current_temp (error signal)
        
        # ========== External Temperature Sensor (Requirement #9) ==========
        # Optional: If zone has an external sensor, import its reading
        # Useful for compensating TRV readings when it's near a radiator
        self.ext_temp_entity_id = ext_temp_entity_id
        self.ext_current_temp = 20.0  # External sensor reading (°C)
        
        # ========== TRV Valve State & Control ==========
        # TRV position sensor: Tracks valve opening percentage (0-100%)
        self.trv_position_entity_id = trv_position_entity_id
        self.trv_opening_percent = 0.0  # TRV valve opening: 0-100%
        self.is_demanding_heat = False  # True if zone needs heat from boiler
        
        # ========== Zone Heating/Cooling Status ==========
        # Internal status tracking: 'heating', 'cooling', or 'idle'
        # Updated only when TRV opening percentage changes
        # Determined by comparing current_temp vs target_temp
        self.heating_status = 'idle'  # 'heating' = current_temp < target_temp, 'cooling' = current_temp > target_temp, 'idle' = equal
        
        # Temperature calibration (offset) control: Adjusts TRV's temperature reading
        # Entity ID for the number entity that controls TRV offset (-5 to +5°C typically)
        self.temp_calib_entity_id = temp_calib_entity_id
        self.temperature_offset = DEFAULT_TEMP_OFFSET  # Current offset (-5 to +5)
        
        # ========== Heating Demand Logic ==========
        # Timestamp of last temperature change (used for stability analysis)
        self.last_update_time = time.time()

        self.master_controller = my_master_controller  # Reference to MasterController
        
        _LOGGER.debug(
            "Zone initialized: %s (entity=%s, area=%.1f m², priority=%s, position_sensor=%s, calib_entity=%s)",
            name, entity_id, floor_area_m2, "HIGH" if is_high_priority else "LOW",
            trv_position_entity_id or "none", temp_calib_entity_id or "none"
        )

    def update_from_state(self, new_state) -> None:
        """
        Update zone state from Home Assistant climate entity state change.
        
        Extracts:
        - current_temperature: Current room temperature
        - temperature: Target temperature setting
        - hvac_action: Current HVAC action (affects demand calculation)
        
        Args:
            new_state: Home Assistant State object from climate entity
        """
        if not new_state or not new_state.attributes:
            _LOGGER.warning("Received invalid state for zone %s", self.name)
            return
        
        try:
            # Extract temperature readings from climate entity attributes
            current = new_state.attributes.get("current_temperature")
            
            # Try multiple attribute names for target temperature
            target = new_state.attributes.get("temperature")
            if target is None:
                target = new_state.attributes.get("target_temp")
            if target is None:
                # Fallback: try to use the state value itself
                target = new_state.state
            
            if current is not None:
                self.current_temp = float(current)
            if target is not None:
                self.target_temp = float(target)
            
            # Calculate the temperature error (how much we need to heat)
            # Positive error = zone is too cold, needs heat
            self.current_error = self.target_temp - self.current_temp
            
            # Update timestamp for tracking stability
            self.last_update_time = time.time()
            
            # Recalculate demand based on new temperature state
            self._update_demand_metric()
            
            _LOGGER.debug(
                "Zone '%s' updated: current=%.1f°C, target=%.1f°C, error=%.1f°C, "
                "opening=%.0f%%, demanding=%s",
                self.name, self.current_temp, self.target_temp, self.current_error,
                self.trv_opening_percent, self.is_demanding_heat
            )
            
        except (ValueError, TypeError, AttributeError) as e:
            _LOGGER.error("Error parsing state for zone %s: %s", self.name, e)

    def update_trv_opening(self, opening_percent: float) -> None:
        """
        Update the TRV valve opening percentage from sensor.
        
        The opening percentage is a key indicator of heating demand:
        - High priority zones: Opening > 0% triggers boiler
        - Low priority zones: Opening >= 100% triggers boiler
        - Low priority zones can aggregate: Multiple zones at 50% = 100% demand
        
        When opening percentage changes, update the zone's heating/cooling status
        depending on the opening percentage history.
        
        Also manages temperature offset (Requirement #8):
        - If valve opens: Set offset to HEATING_TEMP_OFFSET (hardcoded) to encourage more opening
        - If valve closes: Reset offset to 0°C
        
        Args:
            opening_percent: TRV valve opening (0-100%)
        """
        # Clamp to valid range
        opening_percent = max(0.0, min(100.0, opening_percent))

        update_offset = False

        # Update heating/cooling status based on current temperature differential
        # Only update status when opening changes (detected by checking if we're in this method)
        if self.trv_opening_percent > opening_percent:
            self.heating_status = 'heating'
        elif self.trv_opening_percent < opening_percent:
            self.heating_status = 'cooling'
            # Valve is open: Set negative offset to encourage more opening
            if self.temperature_offset != HEATING_TEMP_OFFSET:
                update_offset = True
                self.temperature_offset = HEATING_TEMP_OFFSET
                _LOGGER.debug("Zone '%s': Valve opened, offset set to %.1f°C", self.name, HEATING_TEMP_OFFSET)

        self.trv_opening_percent = opening_percent
        
        _LOGGER.debug(
            "Zone '%s': Opening updated to %.0f%%, status=%s (current=%.1f°C, target=%.1f°C)",
            self.name, self.trv_opening_percent, self.heating_status, self.current_temp, self.target_temp
        )
        
        # Manage temperature offset based on valve state
        if self.trv_opening_percent == 0.0:
            # Valve is closed: Reset offset to 0
            if self.temperature_offset != DEFAULT_TEMP_OFFSET:
                self.temperature_offset = DEFAULT_TEMP_OFFSET
                update_offset = True
                _LOGGER.debug("Zone '%s': Valve closed, offset reset to 0°C", self.name)
        
        # Recalculate demand based on new opening percentage
        self._update_demand_metric()
        
        _LOGGER.debug(
            "Zone '%s': TRV opening updated to %.0f%%, offset=%.1f°C",
            self.name, self.trv_opening_percent, self.temperature_offset
        )
        return update_offset

    def update_external_temperature(self, external_temp: float) -> None:
        """
        Update external temperature sensor reading (Requirement #9).
        
        This is used when a zone has an external temperature sensor to supplement
        or replace the TRV's built-in temperature reading, which can be unreliable
        if the TRV is mounted near a radiator.
        
        Note: Does NOT trigger _calculate_and_command - only position changes
        and climate entity changes trigger boiler recalculation.
        
        Args:
            external_temp: Temperature reading from external sensor (°C)
        """
        self.ext_current_temp = float(external_temp)
        _LOGGER.debug(
            "Zone '%s': External temp sensor updated to %.1f°C",
            self.name, self.ext_current_temp
        )

    def _update_demand_metric(self) -> None:
        """
        Recalculate whether this zone is demanding heat from the boiler.
        
        Logic (Requirements #5, #6):
        - High priority zones: Demand heat if opening > 0% (any opening triggers demand)
        - Low priority zones: Demand heat if opening >= 100%
        
        This is then used by MasterController to:
        1. Decide whether to turn boiler on
        2. Aggregate low-priority zones (they need 100% collective opening)
        3. Set boiler intensity based on highest demand
        """
        # Determine heating demand based on TRV opening and priority level
        if self.is_high_priority:
            # High priority: Can trigger boiler with any opening > HIGH_PRIORITY_MIN_OPENING or if cooling
            self.is_demanding_heat = self.trv_opening_percent > HIGH_PRIORITY_MIN_OPENING or self.heating_status == 'cooling'
        else:
            # Low priority: Needs 100% opening to trigger on its own
            self.is_demanding_heat = self.trv_opening_percent >= LOW_PRIORITY_MIN_OPENING
        
        _LOGGER.debug(
            "Zone '%s' demand updated: priority=%s, opening=%.0f%%, demanding=%s",
            self.name, "HIGH" if self.is_high_priority else "LOW",
            self.trv_opening_percent, self.is_demanding_heat
        )

    def get_demand_metric(self) -> float:
        """
        Return a normalized demand metric (0.0 to 1.0) for boiler control.
        
        This metric represents how much heat this zone is requesting:
        - 0.0 = No demand (valve closed OR target already reached)
        - 0.5 = 50% demand (valve at 50% opening and below target)
        - 1.0 = Maximum demand (valve fully open and below target)
        
        The boiler intensity (flow temperature) is set to match the HIGHEST
        demand among all zones (Requirement #7).
        
        Note: Demand is based on valve opening, but returns 0 if already at target.
        
        Returns:
            float: Demand metric (0.0 to 1.0) based on valve opening percentage
        """
        # No demand if target is already reached
        if self.current_error <= 0.0:
            return 0.0
        
        # Demand is the normalized valve opening (0-100% → 0.0-1.0)
        normalized_opening = self.trv_opening_percent / 100.0
        
        return max(0.0, min(1.0, normalized_opening))

    def export_zone_state(self) -> dict:
        """
        Export the complete zone state for Home Assistant to display/log.
        
        This satisfies Requirement #9: export parameters for Home Assistant
        display. Includes current temperature, target, demand metrics, and
        optional external sensor reading if available. Also exports the current
        temperature offset value being sent to the TRV.
        
        Returns:
            dict: Complete zone state snapshot
        """
        state = {
            # Temperature information
            "current_temperature": round(self.current_temp, 2),
            "target_temperature": round(self.target_temp, 2),
            "temperature_error": round(self.current_error, 2),
            
            # Zone properties
            "name": self.name,
            "floor_area_m2": round(self.floor_area_m2, 2),
            "is_high_priority": self.is_high_priority,
            
            # Heating demand
            "is_demanding_heat": self.is_demanding_heat,
            "trv_opening_percent": round(self.trv_opening_percent, 1),
            
            # Temperature offset feature - exported back to Home Assistant
            "temperature_offset": round(self.temperature_offset, 1),
            "temp_calib_entity_id": self.temp_calib_entity_id,
            
            # External sensor (if available)
            "has_external_sensor": self.ext_temp_entity_id is not None,
            "external_sensor_temperature": round(self.ext_current_temp, 2) if self.ext_temp_entity_id else None,
        }
        
        return state